"""Fixtures du lot socle partagées par ses tests unitaires et d'intégration.

Chaque côté les importe par leur nom dans son `conftest.py` :

- `compter` : nombre de lignes d'une table (`compter(m.Salle)`), dans une
  session à part ;
- `delai_verrou_court` : délai d'attente sur verrou SQLite réduit à 400 ms
  pour les connexions ouvertes ensuite (les moteurs sont libérés) ;
- `verrou_ecriture` : contexte qui tient le verrou d'écriture de la base
  depuis une connexion brute, comme le ferait un autre processus.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import func, select

from edt.db import moteur as module_moteur
from edt.db.moteur import liberer_moteurs, session_base

DELAI_VERROU_COURT_MS = 400
"""Délai d'attente sur verrou des tests qui provoquent un verrou : court, mais mesurable."""


@pytest.fixture
def compter() -> Callable[[type], int]:
    """`compter(modele)` : nombre de lignes de la table, lu dans une session à part."""

    def _compter(modele: type) -> int:
        with session_base() as session:
            return session.scalar(select(func.count()).select_from(modele))

    return _compter


@pytest.fixture
def delai_verrou_court(monkeypatch: pytest.MonkeyPatch) -> int:
    """Attente sur verrou réduite à 400 ms pour les connexions ouvertes après l'appel."""
    monkeypatch.setattr(module_moteur, "DELAI_VERROU_MS", DELAI_VERROU_COURT_MS)
    liberer_moteurs()
    return DELAI_VERROU_COURT_MS


@pytest.fixture
def verrou_ecriture(environnement: Path):
    """Contexte : un « autre processus » tient le verrou d'écriture de la base.

        with verrou_ecriture():
            ...  # toute écriture attend le délai, puis échoue « database is locked »

    La lecture reste possible (mode WAL). Le verrou est rendu à la sortie du bloc.
    """

    @contextmanager
    def _verrou(chemin: Path | None = None) -> Iterator[sqlite3.Connection]:
        brute = sqlite3.connect(chemin or environnement / "edt.db")
        brute.execute("BEGIN IMMEDIATE")
        try:
            yield brute
        finally:
            brute.rollback()
            brute.close()

    return _verrou
