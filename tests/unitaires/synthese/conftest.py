"""Fixtures des tests unitaires du générateur de données fictives.

Chaque jeu nommé est généré **une fois par session de tests**, dans sa
propre base SQLite d'un dossier temporaire du système (jamais dans le
dépôt) : `jeu_petit`, `jeu_moyen`, `jeu_grand`. Les tests les lisent sans
les modifier ; un test qui écrit crée sa propre base (`base_vierge`).

Aides réutilisables (banque des cas limites comprise), dans `tests/outils/synthese/` :

- `aides.creer_base`, `aides.generer`, `aides.generer_dans`, `aides.total_prevu` ;
- `controle_temoin.problemes_du_temoin(url, rapport)` : tout ce que le
  vérificateur et les contrôles directs reprochent au témoin.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.outils.synthese.aides import Jeu, creer_base, generer

from edt.db.moteur import liberer_moteurs


@pytest.fixture(scope="session")
def jeu_petit(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Jeu]:
    """Jeu « petit », graine 0, avec la version de référence."""
    yield generer(tmp_path_factory.mktemp("petit"), "petit", reference=True)
    liberer_moteurs()


@pytest.fixture(scope="session")
def jeu_moyen(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Jeu]:
    """Jeu « moyen », graine 0, avec la version de référence."""
    yield generer(tmp_path_factory.mktemp("moyen"), "moyen", reference=True)
    liberer_moteurs()


@pytest.fixture(scope="session")
def jeu_grand(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Jeu]:
    """Jeu « grand », graine 0 (quelques secondes : le seul jeu qui couvre tous les cas)."""
    yield generer(tmp_path_factory.mktemp("grand"), "grand")
    liberer_moteurs()


@pytest.fixture
def base_vierge(tmp_path: Path) -> str:
    """URL d'une base initialisée et vide de référentiels, propre au test."""
    return creer_base(tmp_path / "vierge")
