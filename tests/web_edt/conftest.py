"""Fixtures des tests des écrans « Générer », « Emploi du temps », « À traiter » et de l'accueil.

Les fonctions des lots pas encore livrés (vérificateur, pré-vérifications,
instanciation) sont **simulées** en installant de faux modules aux noms
convenus : le raccord (`edt.services.emploi_du_temps.raccords`) les trouve
exactement comme il trouvera les vrais.

- `version_generee` : jeu minimal + un calcul réussi du vrai moteur (ses
  séances datées sont produites par l'orchestration) ;
- `faux_verificateur` : faux `edt.services.verification`, réglable ;
- `sans_verificateur` : comme si le vérificateur n'était pas livré (sans
  ces deux fixtures, c'est le vrai vérificateur qui tourne) ;
- `aujourdhui_fige` : le « semestre par défaut » calculé au 5 octobre 2026.

Aucun test ne suppose l'emploi du temps particulier d'un moteur : les jours
et les dates utilisés sont lus dans la version produite (`jour_avec_seance`,
`date_avec_seances`).
"""

from __future__ import annotations

import datetime as dt
import sys
import types
from dataclasses import dataclass, field

import pytest
from sqlalchemy import func, select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import StatutOccurrence
from edt.services import generation
from edt.services.emploi_du_temps import memoire
from edt.services.emploi_du_temps import versions as service_versions
from edt.verification.contrat import Anomalie

DELAI = 20  # secondes : garde-fou pour qu'un test bloqué échoue au lieu d'attendre sans fin


@dataclass(frozen=True)
class VersionGeneree:
    """Repères d'une version produite par un calcul réussi sur le jeu minimal."""

    semestre_id: int
    annee_scolaire_id: int
    calcul_id: int
    version_id: int


@pytest.fixture
def version_generee(jeu_minimal) -> VersionGeneree:
    """Jeu minimal et une version active produite par le moteur (séances datées comprises)."""
    calcul_id = generation.lancer_calcul(jeu_minimal.semestre_id)
    suivi = generation.attendre_calcul(calcul_id, DELAI)
    assert suivi.reussi, suivi.message
    return VersionGeneree(
        semestre_id=jeu_minimal.semestre_id,
        annee_scolaire_id=jeu_minimal.annee_scolaire_id,
        calcul_id=calcul_id,
        version_id=suivi.version_id,
    )


@dataclass
class FauxRapport:
    """Même forme que le `RapportVerification` attendu du vérificateur."""

    anomalies: tuple[Anomalie, ...]
    indicateurs: object = None


INDICATEURS = {
    "pourcentage_seances_placees": 100.0,
    "pourcentage_utilisation_salles": 37.5,
    "changements_campus_groupes": 6,
    "changements_campus_enseignants": 2,
    "trous_creneaux": 4,
    "nb_semaines": 21,
}


@dataclass
class FauxVerificateur:
    """Faux vérificateur : chaque liste est renvoyée telle quelle par la fonction correspondante."""

    hebdo: list[Anomalie] = field(default_factory=list)
    datees: list[Anomalie] = field(default_factory=list)
    modification: list[Anomalie] = field(default_factory=list)
    pre_verifications: list[Anomalie] = field(default_factory=list)
    indicateurs: dict | None = field(default_factory=lambda: dict(INDICATEURS))
    appels: list[tuple] = field(default_factory=list)
    contraintes_modification: object = None

    def __setattr__(self, nom, valeur) -> None:
        # Changer ce que renvoie le faux vérificateur ne touche pas la base : on vide la
        # mémoire des écrans pour que le prochain affichage le voie.
        super().__setattr__(nom, valeur)
        memoire.oublier()

    def module(self) -> types.ModuleType:
        module = types.ModuleType("edt.services.verification")

        def verifier_version(session, version_id, contraintes_actives=None):
            self.appels.append(("version", version_id))
            return FauxRapport(tuple(self.hebdo), self.indicateurs)

        def verifier_occurrences_version(
            session, version_id, date_debut=None, date_fin=None, contraintes_actives=None
        ):
            self.appels.append(("occurrences", version_id, date_debut, date_fin))
            return FauxRapport(tuple(self.datees))

        def verifier_modification(
            session, version_id, seance_id, jour, debut, fin, salle_id, *, contraintes_actives=None
        ):
            self.appels.append(("modification", seance_id, jour, debut, fin, salle_id))
            self.contraintes_modification = contraintes_actives
            # Comme le vrai : seules les contraintes actives sont vérifiées.
            actives = None if contraintes_actives is None else set(contraintes_actives)
            return FauxRapport(
                tuple(a for a in self.modification if actives is None or str(a.code) in actives)
            )

        module.verifier_version = verifier_version
        module.verifier_occurrences_version = verifier_occurrences_version
        module.verifier_modification = verifier_modification
        return module

    def pre_verifier_semestre(self, session, semestre_id, contraintes_desactivees=()):
        """Renvoie les anomalies dont la contrainte n'est pas décochée (comme le vrai)."""
        self.appels.append(("pre", semestre_id, tuple(contraintes_desactivees)))
        return [a for a in self.pre_verifications if str(a.code) not in contraintes_desactivees]


@pytest.fixture
def faux_verificateur(monkeypatch) -> FauxVerificateur:
    """Installe un faux vérificateur et de fausses pré-vérifications, vides par défaut."""
    memoire.oublier()
    faux = FauxVerificateur()
    monkeypatch.setitem(sys.modules, "edt.services.verification", faux.module())
    monkeypatch.setattr(
        generation, "pre_verifier_semestre", faux.pre_verifier_semestre, raising=False
    )
    return faux


@pytest.fixture
def sans_verificateur(monkeypatch) -> None:
    """Fait comme si le vérificateur n'était pas livré (le raccord doit le dire, pas planter)."""
    memoire.oublier()
    monkeypatch.setitem(sys.modules, "edt.services.verification", None)


@pytest.fixture
def seances_datees(version_generee) -> VersionGeneree:
    """La version générée ; ses séances datées existent dès la fin du calcul."""
    with session_base() as session:
        nombre = session.scalar(
            select(func.count())
            .select_from(m.Occurrence)
            .where(m.Occurrence.version_id == version_generee.version_id)
        )
    assert nombre, "le calcul aurait dû produire les séances datées"
    return version_generee


def date_avec_seances(session, version_id: int, minimum: int = 1) -> dt.date:
    """Première date où la version a au moins `minimum` séances datées qui ont lieu."""
    date = session.scalar(
        select(m.Occurrence.date)
        .where(
            m.Occurrence.version_id == version_id,
            m.Occurrence.statut.in_([StatutOccurrence.PLANIFIEE, StatutOccurrence.DEPLACEE]),
        )
        .group_by(m.Occurrence.date)
        .having(func.count() >= minimum)
        .order_by(m.Occurrence.date)
        .limit(1)
    )
    assert date is not None, f"aucune date avec {minimum} séance(s) datée(s)"
    return date


def jour_avec_seance(session, version_id: int, filtre) -> int:
    """Un jour de la semaine type où une séance satisfaisant `filtre` (clause SQL) a lieu."""
    jour = session.scalar(
        select(m.SeanceHebdo.jour)
        .join(m.Besoin, m.Besoin.id == m.SeanceHebdo.besoin_id)
        .where(m.SeanceHebdo.version_id == version_id, filtre)
        .order_by(m.SeanceHebdo.jour)
        .limit(1)
    )
    assert jour is not None, "aucune séance pour cette cible"
    return jour


@pytest.fixture
def aujourdhui_fige(monkeypatch, aujourdhui):
    """Le semestre par défaut est choisi comme si l'on était le 5 octobre 2026."""
    original = service_versions.semestre_par_defaut
    monkeypatch.setattr(
        service_versions,
        "semestre_par_defaut",
        lambda session, jour=None: original(session, jour or aujourdhui),
    )
    return aujourdhui


def seance_du_besoin(session, version_id: int, matiere: str, type_code: str, groupe: str):
    """La séance hebdomadaire d'un besoin du jeu minimal (matière, type, premier groupe)."""
    for seance in session.scalars(
        select(m.SeanceHebdo).where(m.SeanceHebdo.version_id == version_id)
    ):
        besoin = seance.besoin
        if (
            besoin.matiere.libelle == matiere
            and besoin.type_seance.code == type_code
            and groupe in {g.code for g in besoin.groupes}
        ):
            return seance
    raise AssertionError(f"Aucune séance {type_code} {matiere} pour {groupe}")


def identifiant_groupe(session, code: str) -> int:
    """Identifiant d'un groupe du jeu minimal par son code."""
    return session.scalar(select(m.Groupe.id).where(m.Groupe.code == code))
