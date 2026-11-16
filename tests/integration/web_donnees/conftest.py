"""Fixtures des tests d'intégration de l'espace « Données » et de la mise en page commune.

Fixtures propres à ce côté :

- `site` : client web sur une base chargée du jeu minimal ;
- `version_generee` : jeu minimal et une version produite par un vrai calcul
  (son identifiant) ;
- `calcul_retenu` : un calcul lancé sur le semestre du jeu minimal, retenu par
  un moteur qui attend d'être libéré (`liberer()`), puis qui calcule pour de
  vrai : de quoi agir « pendant le calcul » puis vérifier qu'il aboutit.

Fixtures partagées (définies dans `tests.outils.web_donnees.fixtures`) : `ids`,
`sans_prechauffage` (automatique), `generation_lente`. Les aides (`HX`,
`depuis`, `compter`, `saisie_salle`...) sont dans `tests.outils.web_donnees.aides`.

Les tests de la banque des cas limites portent le marqueur `cas_limite` et
commencent par « Cas : ... Risque : ... ».
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from tests.outils.web_donnees.fixtures import generation_lente, ids, sans_prechauffage  # noqa: F401

from edt import solveur
from edt.db.jeux import JeuMinimal
from edt.services import generation

DELAI_CALCUL_S = 20
"""Garde-fou : un calcul de test qui ne finit pas fait échouer le test au lieu de le bloquer."""


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def site(jeu_minimal: JeuMinimal, client: TestClient) -> TestClient:
    """Client web sur une base chargée du jeu minimal."""
    return client


@pytest.fixture
def version_generee(jeu_minimal: JeuMinimal) -> int:
    """Jeu minimal et une version active produite par le vrai moteur ; renvoie son identifiant."""
    calcul_id = generation.lancer_calcul(jeu_minimal.semestre_id)
    suivi = generation.attendre_calcul(calcul_id, DELAI_CALCUL_S)
    assert suivi.reussi, suivi.message
    return suivi.version_id


@dataclass(frozen=True)
class CalculRetenu:
    """Calcul en cours, retenu avant le moteur : `liberer()` le laisse calculer et finir."""

    id: int
    semestre_id: int
    _liberation: threading.Event

    def liberer(self) -> generation.SuiviCalcul:
        """Laisse le vrai moteur calculer, attend la fin du calcul et renvoie son suivi."""
        self._liberation.set()
        return generation.attendre_calcul(self.id, DELAI_CALCUL_S)


@pytest.fixture
def calcul_retenu(jeu_minimal: JeuMinimal, monkeypatch):
    """Calcul lancé sur le semestre du jeu minimal, retenu tant que le test ne le libère pas.

    Le moteur attend d'être libéré, puis appelle le vrai moteur sur l'instantané
    chargé au lancement : ce que le test a changé entre-temps ne s'y voit pas,
    mais l'enregistrement de la solution, lui, se fait dans la base telle
    qu'elle est devenue (c'est là que se voient les suppressions acceptées à tort).
    """
    demarre, liberation = threading.Event(), threading.Event()
    vrai_moteur = solveur.resoudre

    def moteur_retenu(instantane, options, arret=None):
        demarre.set()
        assert liberation.wait(DELAI_CALCUL_S), "le calcul retenu n'a pas été libéré"
        return vrai_moteur(instantane, options, arret)

    monkeypatch.setattr(solveur, "resoudre", moteur_retenu)
    calcul_id = generation.lancer_calcul(jeu_minimal.semestre_id)
    assert demarre.wait(DELAI_CALCUL_S), "le calcul n'a pas atteint le moteur"
    calcul = CalculRetenu(calcul_id, jeu_minimal.semestre_id, liberation)
    yield calcul
    # Le calcul doit être fini avant que la base temporaire ne disparaisse.
    liberation.set()
    generation.attendre_calcul(calcul_id, DELAI_CALCUL_S)
