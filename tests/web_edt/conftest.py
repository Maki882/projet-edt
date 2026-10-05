"""Fixtures des tests des écrans « Générer », « Emploi du temps », « À traiter » et de l'accueil.

Les fonctions des lots pas encore livrés (vérificateur, pré-vérifications,
instanciation) sont **simulées** en installant de faux modules aux noms
convenus : le raccord (`edt.services.emploi_du_temps.raccords`) les trouve
exactement comme il trouvera les vrais.

- `version_generee` : jeu minimal + un calcul réussi du moteur provisoire ;
- `faux_verificateur` : faux `edt.services.verification`, réglable ;
- `fausse_instanciation` : faux `edt.services.instanciation` ;
- `seances_datees` : séances datées de la version, produites à la main ;
- `aujourdhui_fige` : le « semestre par défaut » calculé au 5 octobre 2026.
"""

from __future__ import annotations

import datetime as dt
import sys
import types
from dataclasses import dataclass, field

import pytest
from sqlalchemy import delete, select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.calendrier import semaines_entre
from edt.domaine.enumerations import OrigineOccurrence, StatutOccurrence
from edt.services import generation
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
    """Jeu minimal et une version active produite par le moteur provisoire."""
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

        def verifier_modification(session, version_id, seance_id, jour, debut, fin, salle_id):
            self.appels.append(("modification", seance_id, jour, debut, fin, salle_id))
            return FauxRapport(tuple(self.modification))

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
    faux = FauxVerificateur()
    monkeypatch.setitem(sys.modules, "edt.services.verification", faux.module())
    monkeypatch.setattr(
        generation, "pre_verifier_semestre", faux.pre_verifier_semestre, raising=False
    )
    return faux


def instancier(session, version_id: int) -> int:
    """Applique la semaine type au calendrier, sans replacement (suffisant pour les tests)."""
    version = session.get(m.VersionEdt, version_id)
    semestre = session.get(m.Semestre, version.semestre_id)
    semaines = {s.numero: s for s in semaines_entre(semestre.date_debut, semestre.date_fin)}
    session.execute(delete(m.Occurrence).where(m.Occurrence.version_id == version_id))
    nombre = 0
    for seance in session.scalars(
        select(m.SeanceHebdo).where(m.SeanceHebdo.version_id == version_id)
    ):
        for numero in seance.semaines:
            session.add(
                m.Occurrence(
                    version_id=version_id,
                    seance_hebdo_id=seance.id,
                    besoin_id=seance.besoin_id,
                    date=semaines[numero].date_du(seance.jour),
                    debut_min=seance.debut_min,
                    fin_min=seance.fin_min,
                    salle_id=seance.salle_id,
                    statut=StatutOccurrence.PLANIFIEE,
                    origine=OrigineOccurrence.INSTANCIATION,
                )
            )
            nombre += 1
    session.flush()
    return nombre


@pytest.fixture
def fausse_instanciation(monkeypatch) -> list[int]:
    """Installe un faux `edt.services.instanciation` ; renvoie la liste des versions instanciées."""
    appels: list[int] = []
    module = types.ModuleType("edt.services.instanciation")

    def instancier_version(session, version_id):
        appels.append(version_id)
        return instancier(session, version_id)

    module.instancier_version = instancier_version
    monkeypatch.setitem(sys.modules, "edt.services.instanciation", module)
    return appels


@pytest.fixture
def seances_datees(version_generee) -> VersionGeneree:
    """La version générée, avec ses séances datées."""
    with session_base() as session:
        instancier(session, version_generee.version_id)
    return version_generee


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


JOUR_TEST = dt.date(2026, 10, 12)
"""Un lundi de cours du semestre 1 du jeu minimal (semaine 6)."""
