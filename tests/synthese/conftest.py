"""Fixtures des tests du générateur de données fictives.

Chaque jeu nommé est généré **une fois par session de tests**, dans sa
propre base SQLite d'un dossier temporaire du système (jamais dans le
dépôt) : `jeu_petit`, `jeu_moyen`, `jeu_grand`. Les tests les lisent sans
les modifier ; un test qui écrit crée sa propre base (`base_vierge`).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from edt.db.initialisation import initialiser_base
from edt.db.moteur import fabrique_sessions, liberer_moteurs, session_base
from edt.synthese.generateur import generer_jeu
from edt.synthese.rapport import RapportJeu

AUJOURDHUI = dt.date(2026, 10, 5)
"""Date de référence : l'année scolaire initialisée est 2026-2027."""


@dataclass(frozen=True)
class Jeu:
    """Un jeu généré : l'URL de sa base et le rapport de génération."""

    url: str
    rapport: RapportJeu

    def session(self) -> Session:
        """Nouvelle session sur la base du jeu (à fermer par l'appelant)."""
        return fabrique_sessions(self.url)()


def creer_base(dossier: Path) -> str:
    """Initialise une base vierge dans `dossier` et renvoie son URL."""
    url = f"sqlite:///{(dossier / 'jeu.db').as_posix()}"
    initialiser_base(url=url, aujourdhui=AUJOURDHUI)
    return url


def generer(dossier: Path, taille, graine: int = 0, reference: bool = False) -> Jeu:
    """Crée une base vierge, y génère le jeu et renvoie l'URL et le rapport."""
    url = creer_base(dossier)
    with session_base(url) as session:
        rapport = generer_jeu(session, taille, graine, enregistrer_reference=reference)
    return Jeu(url, rapport)


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
