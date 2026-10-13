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
from edt.domaine.contraintes import CATALOGUE
from edt.domaine.enumerations import StatutOccurrence
from edt.services import generation
from edt.services.emploi_du_temps import memoire, modifications, prechauffage, raccords
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


@pytest.fixture(autouse=True)
def sans_prechauffage(monkeypatch) -> None:
    """Pas de fil de préchauffage dans les tests : il survivrait à la base temporaire."""
    monkeypatch.setattr(prechauffage, "ACTIF", False)


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
    niveaux_pre_verifies: dict | None = None

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
            # Comme le vrai : codes donnés vérifiés en dur ; sans eux, les niveaux du calcul
            # de la version, qui excluent les règles ignorées lors de ce calcul.
            if contraintes_actives is None:
                ignorees = raccords.contraintes_desactivees_de(session, version_id)
                gardees = [a for a in self.modification if str(a.code) not in ignorees]
            else:
                actives = set(contraintes_actives)
                gardees = [a for a in self.modification if str(a.code) in actives]
            return FauxRapport(tuple(gardees))

        module.verifier_version = verifier_version
        module.verifier_occurrences_version = verifier_occurrences_version
        module.verifier_modification = verifier_modification
        return module

    def pre_verifier_semestre(
        self, session, semestre_id, contraintes_desactivees=(), *, niveaux=None
    ):
        """Renvoie les anomalies des règles examinées : comme le vrai, celles qui ont un niveau.

        `niveaux` (clé absente : règle ignorée) prime sur `contraintes_desactivees`.
        """
        if niveaux is not None:
            contraintes_desactivees = {str(c) for c in CATALOGUE} - {str(c) for c in niveaux}
        # Un relevé, pas un réglage : ne pas vider la mémoire des écrans (voir __setattr__).
        object.__setattr__(self, "niveaux_pre_verifies", None if niveaux is None else dict(niveaux))
        self.appels.append(("pre", semestre_id, tuple(sorted(contraintes_desactivees))))
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


def autre_place(session, seance) -> tuple[int, int]:
    """Une place `(jour, début)` proposée pour la séance, différente de sa place dans la version.

    Lue au moment du test dans le formulaire de la séance, jamais écrite en
    dur : la solution de CP-SAT, donc la place actuelle, change d'un système
    à l'autre. Un autre jour d'abord, sinon un autre début du même jour.
    """
    actuelle = (seance.jour, seance.debut_min)
    jours = [j for j, _ in modifications.formulaire_seance(session, seance.id).jours]
    for jour in sorted(jours, key=lambda j: j == seance.jour):  # le jour actuel en dernier
        proposes = modifications.formulaire_seance(session, seance.id, jour=jour).debuts
        for debut, _ in proposes:
            if (jour, debut) != actuelle:
                return jour, debut
    raise AssertionError(f"Aucune autre place proposée pour la séance {seance.id} ({actuelle}).")


def identifiant_groupe(session, code: str) -> int:
    """Identifiant d'un groupe du jeu minimal par son code."""
    return session.scalar(select(m.Groupe.id).where(m.Groupe.code == code))
