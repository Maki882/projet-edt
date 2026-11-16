"""Séances datées d'une version d'emploi du temps : instanciation et replacement, en base.

Fonctions de service appelées par l'orchestration des calculs
(`edt.services.generation`), par la ligne de commande (`python -m edt
instancier`) et par l'interface :

- `instancier_version(session, version_id)` : produit les séances datées de la
  semaine type de la version, replace celles qui tombent un jour fermé ou
  pendant une indisponibilité, et enregistre le tout dans la table
  `occurrence` ;
- `replacer_occurrences(session, version_id, occurrence_ids, ...)` : replace
  des séances datées désignées, dans une fenêtre de dates.

Les deux délèguent aux fonctions pures `edt.solveur.instanciation.instancier`
et `edt.solveur.replacement.replacer`. Elles ne valident pas la transaction :
c'est l'appelant qui possède la session. Elles lisent la version puis
écrivent ses séances datées : la transaction de l'appelant doit s'ouvrir par
`edt.db.moteur.transaction_immediate` avant sa première requête (c'est déjà
le cas d'une requête web d'écriture), sinon une écriture validée entre la
lecture et l'écriture par une autre connexion fait échouer SQLite aussitôt
(« database is locked »).

Ce qui est écrit
----------------
| Sort de l'occurrence | `statut` | `origine` | `date_initiale` | `commentaire` |
|---|---|---|---|---|
| a lieu à sa date | planifiée | semaine type | vide | vide |
| replacée | déplacée | replacement | date prévue par la semaine type | pourquoi elle a bougé |
| sans place | à programmer | replacement | date prévue (qui reste sa date) | pourquoi rien ne convient |

`instancier_version` est **idempotente** : relancée sur la même version, elle
remplace les occurrences issues de la semaine type ou d'un replacement, et ne
touche pas aux occurrences saisies à la main ou importées (qui restent en
place et que les séances replacées évitent).

Une version dont les séances datées ont été **importées** (origine « import »,
sans lien avec une séance hebdomadaire) n'est pas recalculée : ces séances ne
viennent pas de la semaine type, et l'instancier à côté doublerait le
semestre. `instancier_version` le refuse (`ErreurRegleMetier`).

Une séance de la semaine type dont la place est **prise par une retouche**
(même salle, même groupe ou même enseignant au même moment) est replacée
comme si elle tombait un jour fermé, avec la raison « place prise par une
séance saisie à la main ».

Une séance hebdomadaire a **au plus une séance datée par semaine**. Une
séance datée saisie à la main ou importée, rattachée à une séance
hebdomadaire, **couvre** sa semaine : celle de sa date d'origine
(`date_initiale`), sinon de sa date. La séance de la semaine type n'est pas
recréée cette semaine-là, que la retouche soit un déplacement ou une
annulation. C'est la règle qu'applique l'interface après une retouche.

Les contraintes désactivées lors du calcul de la version le restent ici.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from edt.db import modeles as m
from edt.db.moteur import identifiant_valide
from edt.domaine.calendrier import lundi_de
from edt.domaine.contraintes import Niveau, Niveaux, niveaux_depuis_desactivees
from edt.domaine.enumerations import OrigineOccurrence, StatutOccurrence
from edt.domaine.heures import plage_vers_texte
from edt.domaine.instantane import Instantane, Occurrence, decomposer_id_seance
from edt.erreurs import ErreurIntrouvable, ErreurRegleMetier
from edt.services.instantane import charger_instantane, charger_occurrences, charger_placements
from edt.services.niveaux import niveaux_regles_de_version
from edt.solveur.contrat import OptionsSolveur
from edt.solveur.instanciation import instancier
from edt.solveur.replacement import OptionsReplacement, Replacement, replacer

journal = logging.getLogger(__name__)

ORIGINES_RECALCULEES = (OrigineOccurrence.INSTANCIATION, OrigineOccurrence.REPLACEMENT)
"""Origines des occurrences que `instancier_version` remplace à chaque exécution."""


@dataclass(frozen=True)
class OccurrenceAProgrammer:
    """Une séance datée qui n'a trouvé aucune place : à programmer à la main.

    - `occurrence_id` : identifiant de la ligne `occurrence` (statut « à programmer ») ;
    - `besoin` : libellé du besoin ; `date_origine`, `horaire` : ce que
      prévoyait la semaine type ;
    - `raison` : pourquoi aucune place ne convient, en clair.
    """

    occurrence_id: int | None
    besoin_id: int
    besoin: str
    seance_id: str | None
    date_origine: dt.date
    horaire: str
    raison: str


@dataclass(frozen=True)
class RapportInstanciation:
    """Bilan d'une instanciation ou d'un replacement.

    - `nb_occurrences` : séances datées traitées (toutes celles de la semaine
      type pour `instancier_version`, celles désignées pour `replacer_occurrences`) ;
    - `nb_planifiees` : celles qui ont lieu à la date prévue ;
    - `nb_replacees` : celles qui ont été déplacées ;
    - `nb_a_programmer` et `a_programmer` : celles qui n'ont trouvé aucune place ;
    - `nb_conservees` : occurrences saisies à la main ou importées, laissées
      telles quelles ;
    - `nb_couvertes` : séances de la semaine type non recréées parce qu'une
      séance saisie à la main ou importée couvre déjà leur semaine ;
    - `nb_seances_ignorees` : séances hebdomadaires de la version qui ne
      correspondent plus à aucun besoin du semestre (non instanciées).
    """

    version_id: int
    nb_occurrences: int
    nb_planifiees: int
    nb_replacees: int
    nb_a_programmer: int
    a_programmer: tuple[OccurrenceAProgrammer, ...]
    nb_conservees: int = 0
    nb_seances_ignorees: int = 0
    duree_s: float = 0.0
    nb_couvertes: int = 0

    def resume(self) -> str:
        """Phrase française pour l'utilisateur."""
        phrase = (
            f"{self.nb_occurrences} séance(s) datée(s) : {self.nb_planifiees} à la date prévue, "
            f"{self.nb_replacees} replacée(s), {self.nb_a_programmer} à programmer."
        )
        if self.nb_couvertes:
            phrase += (
                f" {self.nb_couvertes} séance(s) de la semaine type n'ont pas été recréées : "
                "une séance saisie à la main ou importée occupe déjà leur semaine."
            )
        if self.nb_seances_ignorees:
            phrase += (
                f" {self.nb_seances_ignorees} séance(s) de la semaine type ne correspondent "
                "plus à aucun besoin et n'ont pas été instanciées."
            )
        return phrase

    def vers_dict(self) -> dict[str, object]:
        """Représentation JSON du rapport."""
        donnees = asdict(self)
        donnees["a_programmer"] = [
            {**asdict(entree), "date_origine": entree.date_origine.isoformat()}
            for entree in self.a_programmer
        ]
        return donnees


def instancier_version(
    session: Session, version_id: int, contraintes_desactivees: Iterable[str] | None = None
) -> RapportInstanciation:
    """Calcule et enregistre les séances datées de la version `version_id`.

    `contraintes_desactivees` : par défaut, celles du calcul qui a produit la
    version. Transaction de `session` ouverte en écriture immédiate (voir
    l'en-tête du module). Lève `ErreurIntrouvable` si la version n'existe pas, et
    `ErreurRegleMetier`, sans rien modifier, si ses séances datées ont été
    importées (elles ne viennent pas de la semaine type : les recalculer les
    doublerait).
    """
    debut = time.perf_counter()
    version, instantane, niveaux, niveaux_regles = _contexte(
        session, version_id, contraintes_desactivees
    )

    placements = charger_placements(session, version_id)
    connues = {seance.id for seance in instantane.seances}
    ignores = [p.seance_id for p in placements if p.seance_id not in connues]
    if ignores:
        journal.warning(
            "Version %d : %d séance(s) hebdomadaire(s) sans besoin correspondant, non "
            "instanciée(s) (%s)",
            version_id,
            len(ignores),
            ", ".join(ignores[:10]),
        )
    placements = [p for p in placements if p.seance_id in connues]

    enregistrees = _occurrences_enregistrees(session, version_id)
    nb_importees = sum(
        1
        for occurrence, origine, _ in enregistrees
        if origine is OrigineOccurrence.IMPORT and occurrence.seance_id is None
    )
    if nb_importees:
        raise ErreurRegleMetier(
            f"Les séances datées de la version « {version.libelle} » ont été importées "
            f"({nb_importees} séance(s)) : les recalculer depuis la semaine type les "
            "doublerait. Rien n'a été modifié."
        )
    retouches = [
        (occurrence, date_initiale)
        for occurrence, origine, date_initiale in enregistrees
        if origine not in ORIGINES_RECALCULEES
    ]
    conservees = [occurrence for occurrence, _ in retouches]
    # Semaines déjà couvertes : (séance hebdomadaire, lundi de la semaine d'origine).
    couvertes = {
        (occurrence.seance_id, lundi_de(date_initiale or occurrence.date))
        for occurrence, date_initiale in retouches
        if occurrence.seance_id is not None
    }
    supprimees = session.execute(
        delete(m.Occurrence).where(
            m.Occurrence.version_id == version_id, m.Occurrence.origine.in_(ORIGINES_RECALCULEES)
        )
    ).rowcount

    instancie = instancier(
        instantane, placements, en_place=conservees, niveaux=niveaux, niveaux_regles=niveaux_regles
    )

    def a_creer(occurrence: Occurrence) -> bool:
        return (occurrence.seance_id, lundi_de(occurrence.date)) not in couvertes

    planifiees = [occurrence for occurrence in instancie.occurrences if a_creer(occurrence)]
    a_replacer = [revoir for revoir in instancie.a_replacer if a_creer(revoir.occurrence)]
    nb_couvertes = instancie.nb_occurrences - len(planifiees) - len(a_replacer)
    replace = replacer(
        instantane,
        [*conservees, *planifiees],
        [revoir.occurrence for revoir in a_replacer],
        OptionsReplacement(niveaux=niveaux, niveaux_regles=niveaux_regles),
    )
    motifs = {id(revoir.occurrence): revoir.raison for revoir in a_replacer}

    seances_hebdo = _seances_hebdo(session, version_id)
    lignes = [
        _ligne(version_id, seances_hebdo, occurrence, OrigineOccurrence.INSTANCIATION)
        for occurrence in planifiees
    ]
    for replacement in replace.replacements:
        motif = motifs[id(replacement.origine)]
        commentaire = (
            f"Déplacée : {motif}."
            if replacement.replacee
            else _a_programmer_texte(motif, replacement)
        )
        lignes.append(
            _ligne(
                version_id,
                seances_hebdo,
                replacement.occurrence,
                OrigineOccurrence.REPLACEMENT,
                date_initiale=replacement.origine.date,
                commentaire=commentaire,
            )
        )
    if lignes:
        session.execute(insert(m.Occurrence), lignes)
    session.flush()

    rapport = RapportInstanciation(
        version_id=version_id,
        nb_occurrences=len(lignes),
        nb_planifiees=len(planifiees)
        + sum(1 for r in replace.replacees if r.occurrence.statut is StatutOccurrence.PLANIFIEE),
        nb_replacees=sum(
            1 for r in replace.replacees if r.occurrence.statut is StatutOccurrence.DEPLACEE
        ),
        nb_a_programmer=len(replace.a_programmer),
        a_programmer=_a_programmer(session, instantane, version_id, replace.a_programmer, motifs),
        nb_conservees=len(conservees),
        nb_seances_ignorees=len(ignores),
        duree_s=round(time.perf_counter() - debut, 3),
        nb_couvertes=nb_couvertes,
    )
    journal.info(
        "Instanciation de la version %d (%s) : %s %d ancienne(s) occurrence(s) remplacée(s), "
        "%d conservée(s) (%.3f s)",
        version_id,
        version.libelle,
        rapport.resume(),
        supprimees,
        len(conservees),
        rapport.duree_s,
    )
    return rapport


def replacer_occurrences(
    session: Session,
    version_id: int,
    occurrence_ids: Sequence[int],
    date_debut: dt.date | None = None,
    date_fin: dt.date | None = None,
    contraintes_desactivees: Iterable[str] | None = None,
) -> RapportInstanciation:
    """Replace les séances datées `occurrence_ids` de la version, dans une fenêtre de dates.

    Toutes les autres séances datées de la version restent en place et sont
    évitées. Une occurrence replacée passe « déplacée » (sa date d'origine
    est gardée) ; une occurrence sans place passe « à programmer ». La
    fenêtre va de `date_debut` à `date_fin`, bornes incluses (par défaut tout
    le semestre). Lève `ErreurIntrouvable` si une occurrence n'appartient pas
    à la version.
    """
    debut = time.perf_counter()
    _, instantane, niveaux, niveaux_regles = _contexte(session, version_id, contraintes_desactivees)
    demandees = list(dict.fromkeys(occurrence_ids))
    toutes = {occurrence.id: occurrence for occurrence in charger_occurrences(session, version_id)}
    inconnues = [identifiant for identifiant in demandees if identifiant not in toutes]
    if inconnues:
        raise ErreurIntrouvable(
            f"{len(inconnues)} séance(s) datée(s) à replacer n'appartiennent pas à la version "
            f"{version_id}.",
            [", ".join(str(identifiant) for identifiant in inconnues[:20])],
        )
    a_replacer = [toutes[identifiant] for identifiant in demandees]
    choisies = set(demandees)
    fixes = [
        occurrence for identifiant, occurrence in toutes.items() if identifiant not in choisies
    ]
    replace = replacer(
        instantane,
        fixes,
        a_replacer,
        OptionsReplacement(
            niveaux=niveaux,
            niveaux_regles=niveaux_regles,
            date_debut=date_debut,
            date_fin=date_fin,
        ),
    )

    motif = "replacement demandé"
    for replacement in replace.replacements:
        ligne = session.get(m.Occurrence, replacement.origine.id)
        nouvelle = replacement.occurrence
        ligne.statut = nouvelle.statut
        ligne.origine = OrigineOccurrence.REPLACEMENT
        if replacement.replacee:
            if nouvelle.date != ligne.date and ligne.date_initiale is None:
                ligne.date_initiale = ligne.date
            ligne.date = nouvelle.date
            ligne.debut_min, ligne.fin_min = nouvelle.debut, nouvelle.fin
            ligne.salle_id = nouvelle.salle_id
            ligne.commentaire = f"Déplacée : {motif}."
        else:
            ligne.commentaire = _a_programmer_texte(motif, replacement)
    session.flush()

    rapport = RapportInstanciation(
        version_id=version_id,
        nb_occurrences=len(replace.replacements),
        nb_planifiees=sum(
            1 for r in replace.replacees if r.occurrence.statut is StatutOccurrence.PLANIFIEE
        ),
        nb_replacees=sum(
            1 for r in replace.replacees if r.occurrence.statut is StatutOccurrence.DEPLACEE
        ),
        nb_a_programmer=len(replace.a_programmer),
        a_programmer=tuple(
            _entree(instantane, replacement.origine.id, replacement, replacement.raison)
            for replacement in replace.a_programmer
        ),
        duree_s=round(time.perf_counter() - debut, 3),
    )
    journal.info("Replacement dans la version %d : %s", version_id, rapport.resume())
    return rapport


# --------------------------------------------------------------------------- #
# Outils
# --------------------------------------------------------------------------- #


def _contexte(
    session: Session, version_id: int, contraintes_desactivees: Iterable[str] | None
) -> tuple[m.VersionEdt, Instantane, Niveaux, dict[int, Niveau] | None]:
    """La version, l'instantané de son semestre (avec ses verrous), les niveaux des contraintes
    et ceux des règles personnalisées.

    Les niveaux sont ceux du calcul qui a produit la version (ses options
    enregistrées) ; `contraintes_desactivees`, s'il est donné, les remplace
    par les niveaux par défaut sans ces codes. Les niveaux des règles sont
    ceux de la version (`edt.services.niveaux.niveaux_regles_de_version`,
    même lecture que le vérificateur).
    """
    version = session.get(m.VersionEdt, version_id) if identifiant_valide(version_id) else None
    if version is None:
        raise ErreurIntrouvable(f"La version d'emploi du temps {version_id} n'existe pas.")
    if contraintes_desactivees is not None:
        niveaux = niveaux_depuis_desactivees(contraintes_desactivees)
    elif version.calcul is not None:
        calcul = version.calcul
        niveaux = OptionsSolveur.lire_niveaux(
            calcul.parametres or {}, calcul.contraintes_desactivees or ()
        )
    else:
        niveaux = niveaux_depuis_desactivees()
    instantane = charger_instantane(session, version.semestre_id, version_id)
    return version, instantane, niveaux, niveaux_regles_de_version(session, version_id)


def _occurrences_enregistrees(
    session: Session, version_id: int
) -> list[tuple[Occurrence, OrigineOccurrence, dt.date | None]]:
    """Occurrences enregistrées de la version : `(occurrence, origine, date d'origine)`."""
    complements = {
        identifiant: (origine, date_initiale)
        for identifiant, origine, date_initiale in session.execute(
            select(m.Occurrence.id, m.Occurrence.origine, m.Occurrence.date_initiale).where(
                m.Occurrence.version_id == version_id
            )
        )
    }
    return [
        (occurrence, *complements[occurrence.id])
        for occurrence in charger_occurrences(session, version_id)
    ]


def _seances_hebdo(session: Session, version_id: int) -> dict[tuple[int, int], int]:
    """Identifiant de chaque séance hebdomadaire de la version, par (besoin, rang)."""
    return {
        (besoin_id, rang): identifiant
        for identifiant, besoin_id, rang in session.execute(
            select(m.SeanceHebdo.id, m.SeanceHebdo.besoin_id, m.SeanceHebdo.rang).where(
                m.SeanceHebdo.version_id == version_id
            )
        )
    }


def _ligne(
    version_id: int,
    seances_hebdo: dict[tuple[int, int], int],
    occurrence: Occurrence,
    origine: OrigineOccurrence,
    date_initiale: dt.date | None = None,
    commentaire: str | None = None,
) -> dict[str, object]:
    """Ligne de la table `occurrence` pour une séance datée."""
    seance_hebdo_id = None
    if occurrence.seance_id is not None:
        seance_hebdo_id = seances_hebdo.get(decomposer_id_seance(occurrence.seance_id))
    return {
        "version_id": version_id,
        "seance_hebdo_id": seance_hebdo_id,
        "besoin_id": occurrence.besoin_id,
        "date": occurrence.date,
        "debut_min": occurrence.debut,
        "fin_min": occurrence.fin,
        "salle_id": occurrence.salle_id,
        "statut": occurrence.statut,
        "origine": origine,
        "date_initiale": date_initiale,
        "commentaire": commentaire,
    }


def _a_programmer_texte(motif: str, replacement: Replacement) -> str:
    return f"À programmer : {motif} ; {replacement.raison}."


def _entree(
    instantane: Instantane, occurrence_id: int | None, replacement: Replacement, raison: str
) -> OccurrenceAProgrammer:
    origine = replacement.origine
    return OccurrenceAProgrammer(
        occurrence_id=occurrence_id,
        besoin_id=origine.besoin_id,
        besoin=instantane.besoin(origine.besoin_id).libelle,
        seance_id=origine.seance_id,
        date_origine=origine.date,
        horaire=plage_vers_texte(origine.debut, origine.fin),
        raison=raison,
    )


def _a_programmer(
    session: Session,
    instantane: Instantane,
    version_id: int,
    replacements: Sequence[Replacement],
    motifs: dict[int, str],
) -> tuple[OccurrenceAProgrammer, ...]:
    """Entrées « à programmer » du rapport, avec l'identifiant de la ligne enregistrée."""
    if not replacements:
        return ()
    identifiants = {
        (besoin_id, date, debut): identifiant
        for identifiant, besoin_id, date, debut in session.execute(
            select(
                m.Occurrence.id, m.Occurrence.besoin_id, m.Occurrence.date, m.Occurrence.debut_min
            ).where(
                m.Occurrence.version_id == version_id,
                m.Occurrence.statut == StatutOccurrence.A_PROGRAMMER,
            )
        )
    }
    return tuple(
        _entree(
            instantane,
            identifiants.get((r.origine.besoin_id, r.origine.date, r.origine.debut)),
            r,
            f"{motifs[id(r.origine)]} ; {r.raison}",
        )
        for r in replacements
    )
