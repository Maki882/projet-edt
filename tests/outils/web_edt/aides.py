"""Aides des tests des écrans « Générer », « Emploi du temps », « À traiter » et de l'accueil.

Partagées par les tests unitaires (`tests/unitaires/web_edt`), les tests
d'intégration (`tests/integration/web_edt`) et leurs fixtures
(`tests/outils/web_edt/fixtures.py`, `tests/integration/web_edt/conftest.py`).

Les fonctions des lots pas encore livrés (vérificateur, pré-vérifications,
instanciation) sont **simulées** en installant de faux modules aux noms
convenus : le raccord (`edt.services.emploi_du_temps.raccords`) les trouve
exactement comme il trouvera les vrais (`FauxVerificateur`).

Banque des cas limites (`@pytest.mark.cas_limite`, `test_cas_limites.py`) :

- `ENORME` : un identifiant au-delà de 2^63, qu'aucune ligne SQLite ne peut
  porter ; `CINQ_MILLE_CHIFFRES` : un nombre que `int()` refuse de lire ;
  `HX` : l'en-tête d'une requête HTMX ;
- `creer_version_vide`, `premiere_occurrence`, `calcul_qui_tourne` : une
  version sans séance, une séance datée de la version, un calcul en cours
  (moteur lent, arrêté à la sortie) ;
- `autre_ecrivain` : une autre connexion (import, chargement de jeu) qui tient
  le verrou d'écriture de la base.

Aucun test ne suppose l'emploi du temps particulier d'un moteur : les jours
et les dates utilisés sont lus dans la version produite (`jour_avec_seance`,
`date_avec_seances`).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import sqlite3
import threading
import types
from collections.abc import Iterator
from dataclasses import dataclass, field

from sqlalchemy import func, select

from edt import solveur
from edt.config import charger_configuration
from edt.db import modeles as m
from edt.db.moteur import chemin_base, session_base
from edt.domaine.contraintes import CATALOGUE
from edt.domaine.enumerations import OrigineVersion, StatutOccurrence
from edt.services import generation
from edt.services.emploi_du_temps import memoire, modifications, raccords
from edt.solveur.contrat import ResultatSolveur, StatutSolveur
from edt.verification.contrat import Anomalie

DELAI = 20  # secondes : garde-fou pour qu'un test bloqué échoue au lieu d'attendre sans fin

HX = {"HX-Request": "true"}
"""En-tête d'une requête HTMX (fragment attendu au lieu de la page complète)."""

ENORME = "9" * 20
"""Identifiant au-delà de 2^63 : aucune ligne SQLite ne peut le porter (lien mal copié)."""

DELAI_VERROU_COURT_MS = 200
"""Attente sur verrou des tests qui dépassent le délai (au lieu de `DELAI_VERROU_MS`, 10 s)."""

CINQ_MILLE_CHIFFRES = "1" * 5000
"""Au-delà de 4 300 chiffres, `int()` lève ValueError (limite de Python) : lien abîmé, envoi
fabriqué."""


@dataclass(frozen=True)
class VersionGeneree:
    """Repères d'une version produite par un calcul réussi sur le jeu minimal."""

    semestre_id: int
    annee_scolaire_id: int
    calcul_id: int
    version_id: int


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
    niveaux_regles_pre_verifies: dict | None = None

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
        self, session, semestre_id, contraintes_desactivees=(), *, niveaux=None, niveaux_regles=None
    ):
        """Renvoie les anomalies des règles examinées : comme le vrai, celles qui ont un niveau.

        `niveaux` (clé absente : règle ignorée) prime sur `contraintes_desactivees`.
        """
        if niveaux is not None:
            contraintes_desactivees = {str(c) for c in CATALOGUE} - {str(c) for c in niveaux}
        # Un relevé, pas un réglage : ne pas vider la mémoire des écrans (voir __setattr__).
        object.__setattr__(self, "niveaux_pre_verifies", None if niveaux is None else dict(niveaux))
        object.__setattr__(
            self,
            "niveaux_regles_pre_verifies",
            None if niveaux_regles is None else dict(niveaux_regles),
        )
        self.appels.append(("pre", semestre_id, tuple(sorted(contraintes_desactivees))))
        return [a for a in self.pre_verifications if str(a.code) not in contraintes_desactivees]


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


# --------------------------------------------------------------------------- #
# Banque des cas limites
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Reperes:
    """Identifiants réels d'une version générée, pour viser des adresses existantes."""

    seance: int
    occurrence: int
    calcul: int
    version: int
    semestre: int


def premiere_occurrence(session, version_id: int) -> m.Occurrence:
    """La séance datée de plus petit identifiant de la version (échoue clairement sans)."""
    occurrence = session.scalar(
        select(m.Occurrence)
        .where(m.Occurrence.version_id == version_id)
        .order_by(m.Occurrence.id)
        .limit(1)
    )
    assert occurrence is not None, f"la version {version_id} n'a aucune séance datée"
    return occurrence


def creer_version_vide(
    semestre_id: int, *, active: bool = False, libelle: str = "Version vide"
) -> int:
    """Une version sans aucune séance (import resté vide, référence non remplie) ; son identifiant.

    `active` : elle devient la version active du semestre (l'ancienne ne l'est plus).
    """
    with session_base() as session:
        if active:
            for version in session.scalars(
                select(m.VersionEdt).where(
                    m.VersionEdt.semestre_id == semestre_id, m.VersionEdt.active.is_(True)
                )
            ):
                version.active = False
            session.flush()
        numero = (
            session.scalar(
                select(func.max(m.VersionEdt.numero)).where(m.VersionEdt.semestre_id == semestre_id)
            )
            or 0
        ) + 1
        version = m.VersionEdt(
            semestre_id=semestre_id,
            numero=numero,
            libelle=libelle,
            origine=OrigineVersion.REFERENCE,
            active=active,
        )
        session.add(version)
        session.flush()
        return version.id


@contextlib.contextmanager
def calcul_qui_tourne(semestre_id: int, monkeypatch) -> Iterator[int]:
    """Un calcul en cours (moteur lent qui attend l'arrêt) ; arrêté et attendu à la sortie."""
    demarre = threading.Event()

    def moteur_lent(instantane, options, arret=None):
        demarre.set()
        arret.wait(DELAI)
        return ResultatSolveur(StatutSolveur.ARRETE)

    monkeypatch.setattr(solveur, "resoudre", moteur_lent)
    calcul_id = generation.lancer_calcul(semestre_id)
    try:
        assert demarre.wait(DELAI), "le calcul n'a pas démarré"
        yield calcul_id
    finally:
        generation.demander_arret(calcul_id)
        generation.attendre_calcul(calcul_id, DELAI)


@contextlib.contextmanager
def autre_ecrivain(
    *, ecriture: str | None = None, rendre_apres_s: float | None = None
) -> Iterator[threading.Event]:
    """Une autre connexion (import, chargement de jeu dans un autre onglet) tient le verrou
    d'écriture de la base, pris par `BEGIN IMMEDIATE` à l'entrée du bloc.

    - `ecriture` : instruction SQL faite par cet écrivain puis validée en rendant le
      verrou (la base a changé : une transaction qui avait déjà lu ne pourrait plus
      écrire) ; sans elle, il annule ;
    - `rendre_apres_s` : un fil rend le verrou après ce délai ; sinon, à la sortie du bloc.

    Renvoie l'événement posé quand le verrou est rendu.
    """
    brute = sqlite3.connect(
        chemin_base(charger_configuration().db_url), isolation_level=None, check_same_thread=False
    )
    rendu = threading.Event()

    def rendre() -> None:
        brute.execute("COMMIT" if ecriture else "ROLLBACK")
        rendu.set()

    brute.execute("BEGIN IMMEDIATE")
    if ecriture:
        brute.execute(ecriture)
    minuterie = threading.Timer(rendre_apres_s, rendre) if rendre_apres_s is not None else None
    if minuterie is not None:
        minuterie.start()
    try:
        yield rendu
    finally:
        if minuterie is not None:
            minuterie.join()
        if not rendu.is_set():
            rendre()
        brute.close()
