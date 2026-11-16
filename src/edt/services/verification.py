"""Service de vérification : vérifie une version enregistrée ou une modification envisagée.

Fonctions typées appelées par la ligne de commande (`python -m edt verifier`)
et par l'interface (panneau des anomalies, modification manuelle). Elles
lisent la base, construisent l'instantané (`charger_instantane`) et
délèguent au vérificateur pur (`edt.verification`) ; elles n'écrivent rien.

- `verifier_version` : semaine type d'une version, avec ses indicateurs ;
- `verifier_occurrences_version` : séances datées d'une version,
  éventuellement bornées à une période ;
- `verifier_modification` : les anomalies qu'introduirait le déplacement
  d'une séance dans la semaine type, sans rien enregistrer ;
- `version_a_verifier` : la version désignée par l'utilisateur (une version,
  ou la version active d'un semestre, ou celle du semestre par défaut).

L'instantané d'une version est chargé avec cette version comme version de
base : les verrous sont ceux de la version vérifiée. Le résultat est un
`RapportVerification` figé, sérialisable en JSON par `vers_dict()`.

Niveaux des contraintes : sans `niveaux` ni `contraintes_actives`, chaque
fonction applique les niveaux du calcul qui a produit la version
(`edt.services.niveaux.niveaux_de_version` ; niveaux par défaut pour une
version de référence). Avec `niveaux`, ce sont eux. Avec seulement
`contraintes_actives`, les niveaux de la version sont **restreints** à ces
codes (les autres contraintes ne sont pas vérifiées) : restreindre la
vérification ne change pas la gravité d'une entorse ; un code demandé que le
calcul avait ignoré est vérifié à son niveau par défaut du catalogue. Une
entorse à une règle importante est un avertissement, à une règle facultative
une information (voir `edt.verification.niveaux`).

Règles personnalisées : sans `niveaux_regles`, chaque fonction vérifie les
règles **actuelles** (actives de l'année) aux niveaux choisis lors du calcul
de la version pour celles qui existaient alors
(`edt.services.niveaux.niveaux_regles_de_version`) ; une règle absente de
`niveaux_regles` est ignorée. Elles ne sont vérifiées que si `REGLE` l'est
(avec `contraintes_actives`, il faut le demander : `-c REGLE`).

Cas limites : aucune contrainte à vérifier (liste vide, niveaux vides) est
refusé (`ErreurDonneesInvalides`), car un rapport « valide » qui n'a rien
vérifié tromperait ; une version sans aucune séance datée donne, pour ses
séances datées, un avertissement `VOLUME` explicite plutôt qu'un rapport
vide ; des niveaux enregistrés illisibles sont signalés avec la version et le
calcul en cause ; une modification vers une salle inconnue est refusée
(`ErreurIntrouvable`) au lieu de passer pour « salle à définir ».
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from edt.db.modeles import Semestre, VersionEdt
from edt.db.moteur import identifiant_valide
from edt.domaine.calendrier import lundi_de
from edt.domaine.contraintes import (
    CodeContrainte,
    Niveau,
    Niveaux,
    codes_par_defaut,
    niveaux_par_defaut,
    normaliser_codes,
)
from edt.domaine.enumerations import Gravite
from edt.domaine.instantane import Instantane, Placement
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable, ErreurRegleMetier
from edt.services.calendrier import semestre_par_defaut
from edt.services.instantane import charger_instantane, charger_occurrences, charger_placements
from edt.services.niveaux import niveaux_de_version, niveaux_regles_de_version
from edt.verification import (
    Anomalie,
    Indicateurs,
    calculer_indicateurs,
    verifier,
    verifier_occurrences,
)
from edt.verification.niveaux import valider_niveaux, valider_niveaux_regles

journal = logging.getLogger(__name__)

SEMAINE_TYPE = "semaine_type"
SEANCES_DATEES = "seances_datees"
MODIFICATION = "modification"

_RANG_GRAVITE = {Gravite.ERREUR: 0, Gravite.AVERTISSEMENT: 1, Gravite.INFORMATION: 2}
"""Rang de gravité (0 = la plus grave), pour reconnaître une anomalie aggravée."""


@dataclass(frozen=True)
class RapportVerification:
    """Résultat d'une vérification, prêt à afficher ou à sérialiser.

    - `anomalies` : liste triée renvoyée par le vérificateur ;
    - `indicateurs` : indicateurs de la semaine type (`None` pour les séances
      datées et pour une modification) ;
    - `nb_erreurs`, `nb_avertissements`, `nb_informations` : compteurs par gravité ;
    - `par_code` : nombre d'anomalies (toutes gravités) par code, dans l'ordre
      du catalogue, codes absents omis ;
    - contexte : `version_id`, `version` (libellé), `semestre_id`, `semestre`
      (libellé), `objet` (`"semaine_type"`, `"seances_datees"` ou
      `"modification"`), `contraintes` (codes vérifiés), `nb_examines`
      (placements ou séances datées examinés), `periode` (bornes des séances
      datées vérifiées, si elles ont été bornées) ;
    - `niveaux` : niveaux appliqués (code → niveau ; une contrainte absente
      n'a pas été vérifiée) ; toujours renseignés par les services de ce
      module (`None` reste possible pour un rapport construit ailleurs, et
      signifie une vérification en dur). Un avertissement est une entorse à une
      règle importante, une information une entorse à une règle facultative
      (ou un écart admis) ;
    - `niveaux_regles` : niveau appliqué à chaque règle personnalisée vérifiée
      (identifiant → niveau ; une règle absente n'a pas été vérifiée).
    """

    anomalies: tuple[Anomalie, ...]
    indicateurs: Indicateurs | None
    nb_erreurs: int
    nb_avertissements: int
    nb_informations: int
    par_code: dict[str, int] = field(default_factory=dict)
    version_id: int | None = None
    version: str = ""
    semestre_id: int | None = None
    semestre: str = ""
    objet: str = SEMAINE_TYPE
    contraintes: tuple[str, ...] = ()
    nb_examines: int = 0
    periode: tuple[dt.date | None, dt.date | None] = (None, None)
    niveaux: Niveaux | None = None
    niveaux_regles: dict[int, Niveau] | None = None

    @property
    def valide(self) -> bool:
        """Vrai si aucune anomalie n'est de gravité « erreur »."""
        return self.nb_erreurs == 0

    def vers_dict(self) -> dict[str, object]:
        """Représentation JSON (dates ISO, codes et gravités en texte)."""
        debut, fin = self.periode
        return {
            "valide": self.valide,
            "objet": self.objet,
            "version_id": self.version_id,
            "version": self.version,
            "semestre_id": self.semestre_id,
            "semestre": self.semestre,
            "contraintes": list(self.contraintes),
            "nb_examines": self.nb_examines,
            "periode": [
                None if debut is None else debut.isoformat(),
                None if fin is None else fin.isoformat(),
            ],
            "nb_erreurs": self.nb_erreurs,
            "nb_avertissements": self.nb_avertissements,
            "nb_informations": self.nb_informations,
            "par_code": dict(self.par_code),
            "niveaux": None
            if self.niveaux is None
            else {str(code): str(niveau) for code, niveau in self.niveaux.items()},
            "niveaux_regles": None
            if self.niveaux_regles is None
            else {str(regle): str(niveau) for regle, niveau in self.niveaux_regles.items()},
            "anomalies": [anomalie_vers_dict(anomalie) for anomalie in self.anomalies],
            "indicateurs": None if self.indicateurs is None else self.indicateurs.vers_dict(),
        }


def anomalie_vers_dict(anomalie: Anomalie) -> dict[str, object]:
    """Représentation JSON d'une anomalie."""
    return {
        "code": str(anomalie.code),
        "gravite": str(anomalie.gravite),
        "bloquante": anomalie.bloquante,
        "message": anomalie.message,
        "seances": list(anomalie.seances),
        "occurrences": list(anomalie.occurrences),
        "besoins": list(anomalie.besoins),
        "salles": list(anomalie.salles),
        "enseignants": list(anomalie.enseignants),
        "groupes": list(anomalie.groupes),
        "jour": anomalie.jour,
        "date": None if anomalie.date is None else anomalie.date.isoformat(),
        "niveau": None if anomalie.niveau is None else str(anomalie.niveau),
        "regle_id": anomalie.regle_id,
        "regle_code": anomalie.regle_code,
        "regle_phrase": anomalie.regle_phrase,
    }


# --------------------------------------------------------------------------- #
# Vérifications
# --------------------------------------------------------------------------- #


def verifier_version(
    session: Session,
    version_id: int,
    contraintes_actives: Iterable[str] | None = None,
    *,
    niveaux: Niveaux | None = None,
    niveaux_regles: Mapping[int, Niveau] | None = None,
) -> RapportVerification:
    """Vérifie la semaine type de la version `version_id` et calcule ses indicateurs.

    Niveaux appliqués : voir l'en-tête du module (par défaut, ceux du calcul
    de la version). Lève `ErreurIntrouvable` si la version n'existe pas,
    `ErreurDonneesInvalides` si un code ou un niveau est inconnu ou si un
    placement enregistré est mal formé.
    """
    version = _version(session, version_id)
    retenus, codes = _choisir(session, version, contraintes_actives, niveaux)
    regles = _choisir_regles(session, version, niveaux_regles)
    instantane = charger_instantane(session, version.semestre_id, version_base_id=version.id)
    placements = charger_placements(session, version.id)
    anomalies = verifier(instantane, placements, codes, niveaux=retenus, niveaux_regles=regles)
    indicateurs = calculer_indicateurs(instantane, placements, anomalies)
    return _rapport(
        anomalies,
        indicateurs,
        version,
        instantane,
        objet=SEMAINE_TYPE,
        codes=codes,
        nb_examines=len(placements),
        niveaux=retenus,
        niveaux_regles=regles,
    )


def verifier_occurrences_version(
    session: Session,
    version_id: int,
    date_debut: dt.date | None = None,
    date_fin: dt.date | None = None,
    contraintes_actives: Iterable[str] | None = None,
    *,
    niveaux: Niveaux | None = None,
    niveaux_regles: Mapping[int, Niveau] | None = None,
) -> RapportVerification:
    """Vérifie les séances datées de la version `version_id`, éventuellement sur une période.

    Niveaux appliqués : voir l'en-tête du module (par défaut, ceux du calcul
    de la version).

    Avec une période `[date_debut, date_fin]` (bornes incluses, chacune
    facultative), la vérification porte sur les semaines entières qui la
    recoupent, pour que les maxima hebdomadaires soient complets ; seules les
    anomalies qui concernent une séance de la période sont gardées.
    `nb_examines` compte les séances datées de la période.

    Une version qui n'a **aucune** séance datée (instanciation jamais faite ou
    échouée) donne un avertissement `VOLUME` explicite si `VOLUME` est
    vérifiée (sinon un avertissement du journal) : un rapport vide la dirait
    valide sans rien avoir vérifié.
    """
    if date_debut is not None and date_fin is not None and date_fin < date_debut:
        raise ErreurDonneesInvalides(
            f"Période invalide : le {date_fin:%d/%m/%Y} précède le {date_debut:%d/%m/%Y}."
        )
    version = _version(session, version_id)
    retenus, codes = _choisir(session, version, contraintes_actives, niveaux)
    regles = _choisir_regles(session, version, niveaux_regles)
    instantane = charger_instantane(session, version.semestre_id, version_base_id=version.id)
    toutes = charger_occurrences(session, version.id)

    def dans(date: dt.date, debut: dt.date | None, fin: dt.date | None) -> bool:
        return (debut is None or debut <= date) and (fin is None or date <= fin)

    elargi_debut = None if date_debut is None else lundi_de(date_debut)
    elargi_fin = None if date_fin is None else _dimanche_de(date_fin)
    examinees = [o for o in toutes if dans(o.date, elargi_debut, elargi_fin)]
    periode = {o.id for o in examinees if dans(o.date, date_debut, date_fin)}
    anomalies = [] if toutes else _sans_seance_datee(version, codes, retenus)
    anomalies += [
        anomalie
        for anomalie in verifier_occurrences(
            instantane, examinees, codes, niveaux=retenus, niveaux_regles=regles
        )
        if not anomalie.occurrences or not periode.isdisjoint(anomalie.occurrences)
    ]
    return _rapport(
        anomalies,
        None,
        version,
        instantane,
        objet=SEANCES_DATEES,
        codes=codes,
        nb_examines=len(periode),
        periode=(date_debut, date_fin),
        niveaux=retenus,
        niveaux_regles=regles,
    )


def verifier_modification(
    session: Session,
    version_id: int,
    seance_id: str,
    jour: int,
    debut: int,
    fin: int,
    salle_id: int | None,
    *,
    contraintes_actives: Iterable[str] | None = None,
    niveaux: Niveaux | None = None,
    niveaux_regles: Mapping[int, Niveau] | None = None,
) -> RapportVerification:
    """Anomalies que le déplacement de la séance `seance_id` **introduit** dans la version.

    Rien n'est enregistré. La semaine type est vérifiée deux fois : telle
    qu'elle est enregistrée (avant), puis avec la séance placée ainsi (après :
    son placement est remplacé, ou ajouté si elle n'était pas placée). Seules
    les anomalies d'après qui citent cette séance et qui sont **nouvelles ou
    aggravées** sont renvoyées (voir `anomalies_introduites`) : une violation
    déjà présente avant le déplacement (maximum hebdomadaire d'un enseignant,
    contrainte décochée au calcul...) ne fige pas la séance, elle reste
    signalée par `verifier_version`. Un rapport valide signifie donc que la
    modification n'introduit aucune erreur : une entorse nouvelle à une règle
    importante ou facultative est renvoyée comme avertissement ou information,
    sans rendre le rapport invalide. Une séance verrouillée dans la version
    donne une anomalie `VERROU` si on la déplace.

    Niveaux appliqués : voir l'en-tête du module (par défaut, ceux du calcul
    de la version ; `contraintes_actives` seul les restreint à ces codes).

    Lève `ErreurIntrouvable` si la version, la séance ou la salle proposée
    n'existe pas (une salle inconnue ne vaut pas « salle à définir » ici),
    `ErreurDonneesInvalides` si le placement proposé est mal formé, si un code
    de contrainte est inconnu ou si aucune contrainte n'est à vérifier.
    """
    version = _version(session, version_id)
    retenus, codes = _choisir(session, version, contraintes_actives, niveaux)
    regles = _choisir_regles(session, version, niveaux_regles)
    instantane = charger_instantane(session, version.semestre_id, version_base_id=version.id)
    if seance_id not in {seance.id for seance in instantane.seances}:
        raise ErreurIntrouvable(
            f"La séance « {seance_id} » n'existe pas dans le semestre « {instantane.libelle} »."
        )
    if type(salle_id) is int and salle_id not in {salle.id for salle in instantane.salles}:
        raise ErreurIntrouvable(
            f"La salle n° {salle_id} n'existe pas (ou plus) : choisir une salle de la liste."
        )
    propose = Placement(seance_id, jour, debut, fin, salle_id)
    enregistres = charger_placements(session, version.id)
    apres = [p for p in enregistres if p.seance_id != seance_id]
    apres.append(propose)
    anomalies = anomalies_introduites(
        verifier(instantane, enregistres, codes, niveaux=retenus, niveaux_regles=regles),
        verifier(instantane, apres, codes, niveaux=retenus, niveaux_regles=regles),
        seance_id,
    )
    journal.debug("Modification envisagée de la séance %s : %s", seance_id, propose)
    return _rapport(
        anomalies,
        None,
        version,
        instantane,
        objet=MODIFICATION,
        codes=codes,
        nb_examines=1,
        niveaux=retenus,
        niveaux_regles=regles,
    )


def anomalies_introduites(
    avant: Sequence[Anomalie], apres: Sequence[Anomalie], seance_id: str
) -> list[Anomalie]:
    """Anomalies d'après qui citent `seance_id` et que le déplacement a créées ou aggravées.

    Deux anomalies se correspondent quand elles ont le même code (et la même
    règle personnalisée) et citent exactement les mêmes séances. Pour chaque correspondance, les anomalies
    d'après sont toutes gardées s'il y en a plus qu'avant, ou si la plus grave
    d'entre elles est plus grave que la plus grave d'avant ; sinon elles sont
    écartées comme déjà présentes. L'ordre d'après est conservé.
    """

    def regrouper(anomalies: Sequence[Anomalie]) -> dict[tuple, list[Anomalie]]:
        groupes: dict[tuple, list[Anomalie]] = {}
        for anomalie in anomalies:
            if seance_id in anomalie.seances:
                cle = (anomalie.code, anomalie.regle_id, anomalie.seances)
                groupes.setdefault(cle, []).append(anomalie)
        return groupes

    avant_par_cle, apres_par_cle = regrouper(avant), regrouper(apres)
    introduites: set[tuple] = set()
    for cle, liste in apres_par_cle.items():
        anciennes = avant_par_cle.get(cle, [])
        pire = min(_RANG_GRAVITE[a.gravite] for a in liste)
        pire_avant = min((_RANG_GRAVITE[a.gravite] for a in anciennes), default=len(_RANG_GRAVITE))
        if len(liste) > len(anciennes) or pire < pire_avant:
            introduites.add(cle)
    ecartees = sum(len(liste) for cle, liste in apres_par_cle.items() if cle not in introduites)
    if ecartees:
        journal.info(
            "Modification de la séance %s : %d anomalie(s) déjà présente(s) avant le déplacement "
            "écartée(s)",
            seance_id,
            ecartees,
        )
    return [a for a in apres if (a.code, a.regle_id, a.seances) in introduites]


def version_a_verifier(
    session: Session, version_id: int | None = None, semestre_id: int | None = None
) -> int:
    """Identifiant de la version à vérifier.

    La version `version_id` si elle est donnée ; sinon la version active du
    semestre `semestre_id`, ou à défaut du semestre proposé par défaut
    (`semestre_par_defaut`). Lève `ErreurIntrouvable` si la version ou le
    semestre n'existe pas ou si le semestre n'a pas de version active,
    `ErreurRegleMetier` si la version n'appartient pas au semestre donné ou si
    plusieurs versions du semestre sont actives (base créée sans l'index qui
    l'interdit) : vérifier l'une d'elles au hasard tromperait.
    """
    if version_id is not None:
        version = _version(session, version_id)
        if semestre_id is not None and version.semestre_id != semestre_id:
            raise ErreurRegleMetier(
                f"La version {version_id} n'appartient pas au semestre {semestre_id}."
            )
        return version.id
    if semestre_id is None:
        semestre = semestre_par_defaut(session)
    else:
        semestre = session.get(Semestre, semestre_id) if identifiant_valide(semestre_id) else None
        if semestre is None:
            raise ErreurIntrouvable(f"Le semestre {semestre_id} n'existe pas.")
    actives = session.scalars(
        select(VersionEdt)
        .where(VersionEdt.semestre_id == semestre.id, VersionEdt.active.is_(True))
        .order_by(VersionEdt.id)
    ).all()
    if not actives:
        raise ErreurIntrouvable(
            f"Le semestre « {semestre.libelle} » n'a pas de version active : générer ou importer "
            "un emploi du temps, ou désigner une version précise."
        )
    if len(actives) > 1:
        # L'index unique partiel l'interdit ; une base créée sans lui peut en avoir deux.
        raise ErreurRegleMetier(
            f"Plusieurs versions d'emploi du temps sont actives pour « {semestre.libelle} » "
            f"(versions {', '.join(str(v.id) for v in actives)}) : en garder une seule, ou "
            "désigner la version à vérifier (--version)."
        )
    return actives[0].id


# --------------------------------------------------------------------------- #
# Outils
# --------------------------------------------------------------------------- #


def _version(session: Session, version_id: int) -> VersionEdt:
    """La version `version_id`, ou `ErreurIntrouvable`.

    Un identifiant hors des bornes de la base (nul, négatif, ou au-delà de
    2**63 - 1) n'est pas demandé à SQLite, qui lèverait `OverflowError` : il
    n'existe pas (`edt.db.moteur.identifiant_valide`).
    """
    version = session.get(VersionEdt, version_id) if identifiant_valide(version_id) else None
    if version is None:
        raise ErreurIntrouvable(f"La version d'emploi du temps {version_id} n'existe pas.")
    return version


def _choisir(
    session: Session,
    version: VersionEdt,
    contraintes_actives: Iterable[str] | None,
    niveaux: Niveaux | None,
) -> tuple[Niveaux, tuple[str, ...]]:
    """Niveaux à appliquer et codes vérifiés, triés.

    - `niveaux` s'il est donné (`contraintes_actives` est alors ignoré) ;
    - sinon les niveaux du calcul de la version (`niveaux_de_version`),
      restreints aux codes de `contraintes_actives` s'il est donné (clé absente
      pour les autres : contraintes non vérifiées). Un code demandé que le
      calcul avait ignoré est vérifié à son niveau par défaut du catalogue,
      ce qui est journalisé en INFO.

    Lève `ErreurDonneesInvalides` si aucune contrainte n'est à vérifier (un
    rapport « valide » qui n'a rien vérifié tromperait) ou si les niveaux
    enregistrés avec le calcul de la version sont illisibles (l'erreur nomme
    la version et le calcul).
    """
    if niveaux is not None:
        retenus = valider_niveaux(niveaux)
    else:
        try:
            de_version = niveaux_de_version(session, version.id)
        except ErreurDonneesInvalides as erreur:
            raise _illisibles(version, "Les niveaux des contraintes", erreur) from erreur
        if contraintes_actives is None:
            retenus = valider_niveaux(de_version)
        else:
            defauts = niveaux_par_defaut()
            demandes = [CodeContrainte(code) for code in _codes(contraintes_actives)]
            ignores = [code for code in demandes if code not in de_version]
            if ignores:
                journal.info(
                    "Version %d : contrainte(s) demandée(s) mais ignorée(s) par son calcul, "
                    "vérifiée(s) à leur niveau par défaut : %s",
                    version.id,
                    ", ".join(f"{code} ({defauts[code]})" for code in ignores),
                )
            retenus = valider_niveaux(
                {code: de_version.get(code, defauts[code]) for code in demandes}
            )
    if not retenus:
        raise ErreurDonneesInvalides(
            f"Aucune contrainte à vérifier pour la version {version.id} « {version.libelle} » : "
            "en choisir au moins une (un rapport « valide » qui n'aurait rien vérifié "
            "tromperait)."
        )
    return retenus, tuple(sorted(retenus))


def _choisir_regles(
    session: Session, version: VersionEdt, niveaux_regles: Mapping[int, Niveau] | None
) -> dict[int, Niveau]:
    """Niveaux des règles personnalisées : ceux donnés, sinon ceux de la version (validés).

    Lève `ErreurDonneesInvalides` (qui nomme la version et le calcul) si la
    copie des règles enregistrée avec le calcul est illisible.
    """
    if niveaux_regles is None:
        try:
            return niveaux_regles_de_version(session, version.id)
        except ErreurDonneesInvalides as erreur:
            raise _illisibles(version, "Les niveaux des règles personnalisées", erreur) from erreur
    return valider_niveaux_regles(niveaux_regles)


def _illisibles(
    version: VersionEdt, quoi: str, erreur: ErreurDonneesInvalides
) -> ErreurDonneesInvalides:
    """L'erreur de lecture de niveaux enregistrés, qui nomme la version et son calcul.

    Sans ce contexte, « Contrainte inconnue : 'PAUSES'. » ne dirait ni quelle
    version ne peut plus être vérifiée, ni d'où vient le code fautif. Le
    message d'origine passe dans les détails.
    """
    origine = f"la version {version.id} « {version.libelle} »"
    if version.calcul_id is not None:
        origine = f"le calcul {version.calcul_id} de {origine}"
    return ErreurDonneesInvalides(
        f"{quoi} enregistrés avec {origine} sont illisibles : la vérification est "
        "impossible avec eux (relancer le calcul, ou vérifier avec des niveaux choisis).",
        details=(erreur.message, *erreur.details),
    )


def _dimanche_de(date: dt.date) -> dt.date:
    """Dimanche de la semaine de `date`, borné au dernier jour représentable (`date.max`)."""
    lundi = lundi_de(date)
    return lundi + dt.timedelta(days=min(6, (dt.date.max - lundi).days))


def _sans_seance_datee(
    version: VersionEdt, codes: Sequence[str], niveaux: Niveaux
) -> list[Anomalie]:
    """Signal d'une version sans aucune séance datée : avertissement `VOLUME`, ou journal."""
    if CodeContrainte.VOLUME not in codes:
        journal.warning(
            "Version %d « %s » : aucune séance datée à vérifier ; VOLUME n'est pas vérifiée, "
            "le rapport n'en dit donc rien (il ne prouve rien sur le calendrier réel).",
            version.id,
            version.libelle,
        )
        return []
    journal.info(
        "Version %d « %s » : aucune séance datée, signalé par un avertissement VOLUME.",
        version.id,
        version.libelle,
    )
    return [
        Anomalie(
            code=CodeContrainte.VOLUME,
            gravite=Gravite.AVERTISSEMENT,
            message=(
                f"La version « {version.libelle} » n'a aucune séance datée : produire ses "
                "séances datées (instanciation sur le calendrier) pour vérifier le calendrier "
                "réel."
            ),
            niveau=niveaux.get(CodeContrainte.VOLUME),
        )
    ]


def _codes(contraintes_actives: Iterable[str] | None) -> tuple[str, ...]:
    """Codes à vérifier, validés et triés (ceux actifs par défaut si `None`)."""
    if contraintes_actives is None:
        return tuple(sorted(codes_par_defaut()))
    if isinstance(contraintes_actives, str):
        raise ErreurDonneesInvalides(
            "Les contraintes à vérifier se donnent sous forme de liste de codes, pas d'un "
            f"texte seul (lu : {contraintes_actives!r})."
        )
    return tuple(sorted(normaliser_codes(contraintes_actives)))


def _rapport(
    anomalies: Sequence[Anomalie],
    indicateurs: Indicateurs | None,
    version: VersionEdt,
    instantane: Instantane,
    *,
    objet: str,
    codes: tuple[str, ...],
    nb_examines: int,
    periode: tuple[dt.date | None, dt.date | None] = (None, None),
    niveaux: Niveaux | None = None,
    niveaux_regles: Mapping[int, Niveau] | None = None,
) -> RapportVerification:
    gravites = Counter(anomalie.gravite for anomalie in anomalies)
    par_code = Counter(anomalie.code for anomalie in anomalies)
    rapport = RapportVerification(
        anomalies=tuple(anomalies),
        indicateurs=indicateurs,
        nb_erreurs=gravites[Gravite.ERREUR],
        nb_avertissements=gravites[Gravite.AVERTISSEMENT],
        nb_informations=gravites[Gravite.INFORMATION],
        par_code={str(code): par_code[code] for code in CodeContrainte if par_code[code]},
        version_id=version.id,
        version=version.libelle,
        semestre_id=instantane.semestre_id,
        semestre=instantane.libelle,
        objet=objet,
        contraintes=codes,
        nb_examines=nb_examines,
        periode=periode,
        niveaux=niveaux,
        niveaux_regles=None if niveaux_regles is None else dict(niveaux_regles),
    )
    journal.info(
        "Vérification (%s, %s) de la version %d « %s » : %d élément(s) examiné(s), "
        "%d erreur(s), %d avertissement(s), %d information(s).",
        objet,
        "contraintes en dur" if niveaux is None else f"{len(niveaux)} contrainte(s) à niveaux",
        version.id,
        version.libelle,
        nb_examines,
        rapport.nb_erreurs,
        rapport.nb_avertissements,
        rapport.nb_informations,
    )
    return rapport


__all__ = [
    "MODIFICATION",
    "SEANCES_DATEES",
    "SEMAINE_TYPE",
    "RapportVerification",
    "anomalie_vers_dict",
    "anomalies_introduites",
    "verifier_modification",
    "verifier_occurrences_version",
    "verifier_version",
    "version_a_verifier",
]
