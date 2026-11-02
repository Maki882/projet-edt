"""Fixtures et aides du lot socle, partagées par ses tests (cas limites compris).

Les tests de cas limites portent le marqueur `cas_limite` et une docstring
« Cas : … Risque : … » ; ils réutilisent ces aides au lieu de les redéfinir :

- `lanceur` : lanceur de la ligne de commande (`CliRunner`) ;
- `compter` : nombre de lignes d'une table (`compter(m.Salle)`), dans une
  session à part ;
- `delai_verrou_court` : délai d'attente sur verrou SQLite réduit à 400 ms
  pour les connexions ouvertes ensuite (les moteurs sont libérés) ;
- `verrou_ecriture` : contexte qui tient le verrou d'écriture de la base
  depuis une connexion brute, comme le ferait un autre processus ;
- `lecture_seule` : contexte qui rend un fichier en lecture seule le temps
  du bloc, puis rétablit ses droits ;
- `corrompre_base` : remplace le fichier de la base par du texte (« file is
  not a database »), moteurs libérés ;
- `verrou_ecriture_bref` : un autre fil tient le verrou d'écriture pendant
  quelques dixièmes de seconde (« un autre processus écrit brièvement »).
"""

from __future__ import annotations

import os
import sqlite3
import stat
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import func, select
from typer.testing import CliRunner

from edt.db import moteur as module_moteur
from edt.db.moteur import liberer_moteurs, session_base

DELAI_VERROU_COURT_MS = 400
"""Délai d'attente sur verrou des tests qui provoquent un verrou : court, mais mesurable."""


@pytest.fixture
def lanceur() -> CliRunner:
    """Lanceur de la ligne de commande (`python -m edt ...`)."""
    return CliRunner()


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


@pytest.fixture
def lecture_seule():
    """Contexte : le fichier est en lecture seule le temps du bloc (droits rétablis ensuite)."""

    @contextmanager
    def _lecture_seule(chemin: Path) -> Iterator[Path]:
        os.chmod(chemin, stat.S_IREAD)
        try:
            yield chemin
        finally:
            os.chmod(chemin, stat.S_IWRITE | stat.S_IREAD)

    return _lecture_seule


@pytest.fixture
def corrompre_base(environnement: Path) -> Callable[[], Path]:
    """Remplace le fichier de la base par du texte : ce n'est plus une base SQLite."""

    def _corrompre() -> Path:
        liberer_moteurs()
        chemin = environnement / "edt.db"
        for suffixe in ("-wal", "-shm"):
            (environnement / f"edt.db{suffixe}").unlink(missing_ok=True)
        chemin.write_text("x" * 1200, encoding="utf-8")
        return chemin

    return _corrompre


@pytest.fixture
def verrou_ecriture_bref(environnement: Path):
    """`verrou_ecriture_bref(duree_s)` : un autre fil prend le verrou d'écriture de la base et le
    rend après `duree_s` secondes ; rend la main une fois le verrou pris."""
    fils: list[threading.Thread] = []

    def _tenir(duree_s: float = 0.4) -> None:
        pris = threading.Event()

        def tenir() -> None:
            brute = sqlite3.connect(environnement / "edt.db")
            brute.execute("BEGIN IMMEDIATE")
            pris.set()
            time.sleep(duree_s)
            brute.rollback()
            brute.close()

        fil = threading.Thread(target=tenir, name="verrou-bref")
        fil.start()
        fils.append(fil)
        assert pris.wait(5)

    yield _tenir
    for fil in fils:
        fil.join(5)
