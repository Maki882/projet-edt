"""Ce qui reste à traiter dans les emplois du temps actifs (page « Points à traiter »).

Pour la version active de chaque semestre :

- les anomalies de la **semaine type** (`raccords.verifier_version`) et des
  **séances datées** (`raccords.verifier_occurrences_version`), regroupées
  par gravité puis par contrainte (libellé du catalogue) ; chaque ligne
  porte les paramètres d'un lien vers l'emploi du temps concerné, la
  séance mise en évidence ;
- les séances **à programmer** : occurrences qu'aucun placement valide n'a
  pu replacer (jour fermé, indisponibilité datée), avec leur raison.

Une vérification qui n'a pas pu tourner est rendue comme telle
(`raccords.Controle.motif`), jamais comme une liste vide.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from edt.db import modeles as m
from edt.domaine.contraintes import CATALOGUE, CodeContrainte
from edt.domaine.enumerations import Gravite, StatutOccurrence
from edt.domaine.instantane import decomposer_id_seance
from edt.services.emploi_du_temps import presentation, raccords
from edt.services.emploi_du_temps.versions import (
    ResumeSemestre,
    ResumeVersion,
    annee_du_semestre,
    lire_version,
    lister_semestres,
)
from edt.verification.contrat import Anomalie

journal = logging.getLogger(__name__)

LIMITE_LIGNES = 100
"""Nombre maximal de lignes affichées par contrainte (le total reste indiqué)."""

TITRES_GRAVITE = {
    Gravite.ERREUR: "Règles non respectées",
    Gravite.AVERTISSEMENT: "Situations à vérifier",
    Gravite.INFORMATION: "Pour information",
}

RAISON_PAR_DEFAUT = (
    "Aucun créneau respectant toutes les contraintes n'a été trouvé pour la replacer."
)

_TAILLE_LOT = 500


@dataclass(frozen=True)
class LigneAnomalie:
    """Une anomalie, avec les paramètres du lien vers l'emploi du temps concerné.

    `niveau` : « règle importante » ou « règle facultative » quand le
    vérificateur dit le niveau de la règle (`Anomalie.niveau`), sinon `None` ;
    `regle` : la règle personnalisée citée par une anomalie `REGLE` (code,
    phrase, lien), sinon `None`.
    """

    message: str
    code: str
    gravite: Gravite
    lien: dict[str, str] | None
    niveau: str | None = None
    regle: presentation.RegleCitee | None = None


@dataclass(frozen=True)
class GroupeContrainte:
    """Les anomalies d'une même contrainte."""

    code: str
    libelle: str
    description: str
    lignes: tuple[LigneAnomalie, ...]
    total: int


@dataclass(frozen=True)
class GroupeGravite:
    """Les anomalies d'une gravité, par contrainte."""

    gravite: Gravite
    titre: str
    contraintes: tuple[GroupeContrainte, ...]
    total: int


@dataclass(frozen=True)
class SeanceAProgrammer:
    """Une occurrence restée « à programmer »."""

    occurrence_id: int
    date: dt.date
    debut: int
    fin: int
    libelle: str
    groupes: tuple[str, ...]
    enseignants: tuple[str, ...]
    raison: str
    lien: dict[str, str]


@dataclass(frozen=True)
class ATraiter:
    """Ce qui reste à traiter dans la version active d'un semestre.

    `decochees` : écarts aux contraintes décochées lors du calcul de la
    version (semaine type et séances datées), listés à part et non comptés :
    l'utilisateur les a acceptés en lançant le calcul.
    """

    semestre: ResumeSemestre
    version: ResumeVersion
    hebdo: raccords.Controle
    datees: raccords.Controle | None
    gravites_hebdo: tuple[GroupeGravite, ...]
    gravites_datees: tuple[GroupeGravite, ...]
    a_programmer: tuple[SeanceAProgrammer, ...]
    decochees: tuple[GroupeContrainte, ...] = ()
    contraintes_decochees: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        """Nombre de points à traiter : LA définition, reprise par l'accueil et l'onglet.

        Erreurs et avertissements des contraintes actives, sur la semaine type
        et sur les séances datées, chaque séance « à programmer » comptant
        une seule fois : l'avertissement « reste à programmer » que le
        vérificateur émet pour elle n'est pas ajouté à la séance elle-même.
        """
        a_programmer = {s.occurrence_id for s in self.a_programmer}
        datees = 0
        if self.datees is not None and self.datees.verification is not None:
            datees = sum(
                1
                for a in self.datees.verification.anomalies
                if a.gravite in (Gravite.ERREUR, Gravite.AVERTISSEMENT)
                and not (
                    str(a.code) == CodeContrainte.VOLUME
                    and a.occurrences
                    and a_programmer.issuperset(a.occurrences)
                )
            )
        return _nb_a_traiter(self.hebdo) + datees + len(a_programmer)

    @property
    def datees_comme_hebdo(self) -> bool:
        """Vrai si les séances datées n'ont que des anomalies déjà vues dans la semaine type.

        Chaque couple (gravité, règle) des séances datées figure dans la semaine
        type : les séances datées en découlent, corriger la semaine type (ou
        générer à nouveau) les corrige aussi.
        """
        if not self.gravites_datees:
            return False
        hebdo = {(g.gravite, c.code) for g in self.gravites_hebdo for c in g.contraintes}
        return all(
            (g.gravite, c.code) in hebdo for g in self.gravites_datees for c in g.contraintes
        )

    @property
    def nb_ecarts_decoches(self) -> int:
        """Écarts (toutes gravités) aux contraintes décochées lors du calcul."""
        return sum(groupe.total for groupe in self.decochees)

    @property
    def complet(self) -> bool:
        """Vrai si toutes les vérifications ont tourné (le total est alors exact)."""
        return self.hebdo.disponible and (self.datees is None or self.datees.disponible)


def a_traiter(session: Session, semestre_id: int | None = None) -> tuple[ATraiter, ...]:
    """La liste « À traiter » des versions actives (d'un seul semestre si `semestre_id`)."""
    resultat = []
    for semestre in lister_semestres(session):
        if semestre_id is not None and semestre.id != semestre_id:
            continue
        if semestre.version_active_id is None:
            continue
        resultat.append(a_traiter_version(session, semestre, semestre.version_active_id))
    return tuple(resultat)


def a_traiter_version(session: Session, semestre: ResumeSemestre, version_id: int) -> ATraiter:
    """Anomalies et séances à programmer d'une version."""
    version = lire_version(session, version_id)
    liens = _Liens.charger(session, version_id)
    hebdo = raccords.controler_version(session, version_id)
    datees = None
    if version.nb_occurrences:
        datees = raccords.controler_occurrences(session, version_id)
    # Année du semestre : le lien d'une règle citée ouvre la liste de cette année.
    annee_id = annee_du_semestre(session, semestre.id)
    gravites_hebdo = (
        _grouper(hebdo.verification.anomalies, liens, annee_id) if hebdo.verification else ()
    )
    gravites_datees: tuple[GroupeGravite, ...] = ()
    decochees = list(hebdo.decochees)
    if datees is not None and datees.verification is not None:
        liens.charger_occurrences(
            session,
            (o for a in (*datees.verification.anomalies, *datees.decochees) for o in a.occurrences),
        )
        gravites_datees = _grouper(datees.verification.anomalies, liens, annee_id)
        decochees.extend(datees.decochees)
    return ATraiter(
        semestre=semestre,
        version=version,
        hebdo=hebdo,
        datees=datees,
        gravites_hebdo=gravites_hebdo,
        gravites_datees=gravites_datees,
        a_programmer=_a_programmer(session, version_id, liens),
        decochees=tuple(
            contrainte
            for groupe in _grouper(decochees, liens, annee_id)
            for contrainte in groupe.contraintes
        ),
        contraintes_decochees=presentation.libelles_contraintes(hebdo.contraintes_decochees),
    )


def _nb_a_traiter(controle: raccords.Controle) -> int:
    if controle.verification is None:
        return 0
    return controle.verification.nb_erreurs + controle.verification.nb_avertissements


# --------------------------------------------------------------------------- #
# Regroupement
# --------------------------------------------------------------------------- #


def _grouper(
    anomalies: Sequence[Anomalie], liens: _Liens, annee_id: int | None = None
) -> tuple[GroupeGravite, ...]:
    par_gravite: dict[Gravite, dict[str, list[Anomalie]]] = defaultdict(lambda: defaultdict(list))
    for anomalie in anomalies:
        par_gravite[anomalie.gravite][str(anomalie.code)].append(anomalie)
    ordre_codes = {str(code): rang for rang, code in enumerate(CATALOGUE)}
    groupes = []
    for gravite in (Gravite.ERREUR, Gravite.AVERTISSEMENT, Gravite.INFORMATION):
        par_code = par_gravite.get(gravite)
        if not par_code:
            continue
        contraintes = []
        for code in sorted(par_code, key=lambda c: (ordre_codes.get(c, len(ordre_codes)), c)):
            liste = sorted(
                par_code[code], key=lambda a: (a.date or dt.date.min, a.jour or 0, a.message)
            )
            entree = CATALOGUE.get(CodeContrainte(code)) if code in ordre_codes else None
            contraintes.append(
                GroupeContrainte(
                    code=code,
                    libelle=entree.libelle if entree else code,
                    description=entree.description if entree else "",
                    lignes=tuple(
                        LigneAnomalie(
                            a.message,
                            code,
                            gravite,
                            liens.lien(a),
                            presentation.etiquette_niveau(a.niveau),
                            presentation.regle_citee(a, annee_id),
                        )
                        for a in liste[:LIMITE_LIGNES]
                    ),
                    total=len(liste),
                )
            )
        groupes.append(
            GroupeGravite(
                gravite=gravite,
                titre=TITRES_GRAVITE[gravite],
                contraintes=tuple(contraintes),
                total=sum(c.total for c in contraintes),
            )
        )
    return tuple(groupes)


class _Liens:
    """Construit les paramètres de lien vers l'emploi du temps d'une anomalie.

    Cible : le premier groupe, enseignant ou salle nommé par l'anomalie,
    sinon le premier groupe du besoin concerné (celui de la séance, ou le
    premier de `Anomalie.besoins` pour un problème sans séance).
    """

    def __init__(
        self,
        version_id: int,
        seances: dict[tuple[int, int], int],
        premier_groupe: dict[int, int],
    ) -> None:
        self.version_id = version_id
        self.seances = seances
        self.premier_groupe = premier_groupe
        self.occurrences: dict[int, tuple[dt.date, int]] = {}

    @classmethod
    def charger(cls, session: Session, version_id: int) -> _Liens:
        seances = {
            (besoin_id, rang): identifiant
            for identifiant, besoin_id, rang in session.execute(
                select(m.SeanceHebdo.id, m.SeanceHebdo.besoin_id, m.SeanceHebdo.rang).where(
                    m.SeanceHebdo.version_id == version_id
                )
            )
        }
        premier_groupe: dict[int, int] = {}
        for besoin_id, groupe_id in session.execute(
            select(m.besoin_groupe.c.besoin_id, m.Groupe.id)
            .join(m.Groupe, m.Groupe.id == m.besoin_groupe.c.groupe_id)
            .join(m.Besoin, m.Besoin.id == m.besoin_groupe.c.besoin_id)
            .where(
                m.Besoin.semestre_id
                == select(m.VersionEdt.semestre_id)
                .where(m.VersionEdt.id == version_id)
                .scalar_subquery()
            )
            .order_by(m.Groupe.code)
        ):
            premier_groupe.setdefault(besoin_id, groupe_id)
        return cls(version_id, seances, premier_groupe)

    def charger_occurrences(self, session: Session, identifiants: Iterable[int]) -> None:
        """Date et besoin des occurrences nommées par les anomalies datées (par lots)."""
        manquants = sorted({i for i in identifiants if i not in self.occurrences})
        for debut in range(0, len(manquants), _TAILLE_LOT):
            lot = manquants[debut : debut + _TAILLE_LOT]
            for identifiant, date, besoin_id in session.execute(
                select(m.Occurrence.id, m.Occurrence.date, m.Occurrence.besoin_id).where(
                    m.Occurrence.id.in_(lot)
                )
            ):
                self.occurrences[identifiant] = (date, besoin_id)

    def lien(self, anomalie: Anomalie) -> dict[str, str] | None:
        """Paramètres de `/emploi-du-temps` pour cette anomalie (ou `None` si rien à montrer)."""
        besoin_id = None
        parametres: dict[str, str] = {"version_id": str(self.version_id)}
        occurrence = next((o for o in anomalie.occurrences if o in self.occurrences), None)
        if occurrence is not None:
            date, besoin_id = self.occurrences[occurrence]
            parametres.update(couche="date", date=date.isoformat(), seance=f"o{occurrence}")
        elif anomalie.seances:
            besoin_id, rang = decomposer_id_seance(anomalie.seances[0])
            identifiant = self.seances.get((besoin_id, rang))
            if anomalie.date is not None:
                parametres.update(couche="date", date=anomalie.date.isoformat())
            else:
                parametres["couche"] = "hebdo"
                if identifiant is not None:
                    parametres["seance"] = f"h{identifiant}"
        elif anomalie.date is not None:
            parametres.update(couche="date", date=anomalie.date.isoformat())
        else:
            parametres["couche"] = "hebdo"
        if besoin_id is None and anomalie.besoins:
            # Problème de dépliage ou séance sans placement : le besoin est le seul repère.
            besoin_id = anomalie.besoins[0]

        if anomalie.groupes:
            parametres["groupe_id"] = str(anomalie.groupes[0])
        elif anomalie.enseignants:
            parametres["enseignant_id"] = str(anomalie.enseignants[0])
        elif anomalie.salles:
            parametres["salle_id"] = str(anomalie.salles[0])
        elif besoin_id is not None and besoin_id in self.premier_groupe:
            parametres["groupe_id"] = str(self.premier_groupe[besoin_id])
        else:
            return None
        return parametres


def _raison(commentaire: str | None) -> str:
    """Raison lisible d'une séance à programmer (commentaire écrit par l'instanciation)."""
    texte = (commentaire or "").strip().removeprefix("À programmer :").strip()
    if not texte:
        return RAISON_PAR_DEFAUT
    return texte[:1].upper() + texte[1:]


def _a_programmer(
    session: Session, version_id: int, liens: _Liens
) -> tuple[SeanceAProgrammer, ...]:
    occurrences = session.scalars(
        select(m.Occurrence)
        .where(
            m.Occurrence.version_id == version_id,
            m.Occurrence.statut == StatutOccurrence.A_PROGRAMMER,
        )
        .options(
            selectinload(m.Occurrence.besoin).options(
                selectinload(m.Besoin.groupes),
                selectinload(m.Besoin.enseignants),
                joinedload(m.Besoin.matiere),
                joinedload(m.Besoin.type_seance),
            )
        )
        .order_by(m.Occurrence.date, m.Occurrence.debut_min, m.Occurrence.id)
    )
    resultat = []
    for occurrence in occurrences:
        besoin = occurrence.besoin
        groupes = sorted(besoin.groupes, key=lambda g: g.code)
        lien = {
            "version_id": str(version_id),
            "couche": "date",
            "date": occurrence.date.isoformat(),
            "seance": f"o{occurrence.id}",
        }
        if groupes:
            lien["groupe_id"] = str(groupes[0].id)
        elif besoin.enseignants:
            lien["enseignant_id"] = str(besoin.enseignants[0].id)
        resultat.append(
            SeanceAProgrammer(
                occurrence_id=occurrence.id,
                date=occurrence.date,
                debut=occurrence.debut_min,
                fin=occurrence.fin_min,
                libelle=f"{besoin.matiere.libelle} ({besoin.type_seance.code})"
                + (f" - {besoin.libelle}" if besoin.libelle else ""),
                groupes=tuple(g.code for g in groupes),
                enseignants=tuple(e.nom_affiche for e in besoin.enseignants),
                raison=_raison(occurrence.commentaire),
                lien=lien,
            )
        )
    return tuple(resultat)
