"""Fixtures du lot solveur, en plus de celles de `tests/conftest.py`.

- `lanceur` : lanceur de la ligne de commande `python -m edt` ;
- `processus_vivant` : un autre processus qui vit pendant tout le test (un
  calcul « en cours » attribué à son numéro n'est pas tenu pour abandonné),
  tué à la fin du test ;
- `guetter_journal` : événements levés par un message du journal, pour
  synchroniser un test sur une étape du moteur (première solution, début de
  l'amélioration, modèle construit) plutôt que sur un délai fixe. Un test qui
  dort un temps donné dépend de la charge de la machine ; un test qui attend
  l'événement ne dépend que du moteur.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator

import pytest
from typer.testing import CliRunner


@pytest.fixture
def lanceur() -> CliRunner:
    """Lanceur de la ligne de commande."""
    return CliRunner()


@pytest.fixture
def processus_vivant() -> Iterator[subprocess.Popen]:
    """Un processus qui dort deux minutes : vivant pendant le test, tué à la fin."""
    processus = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        yield processus
    finally:
        processus.kill()
        processus.wait()


class _Guetteur(logging.Handler):
    """Lève un événement dès qu'un enregistrement du journal contient le texte attendu."""

    def __init__(self, texte: str) -> None:
        super().__init__(logging.INFO)
        self.texte = texte
        self.vu = threading.Event()

    def emit(self, record: logging.LogRecord) -> None:
        if self.texte in record.getMessage():
            self.vu.set()


@pytest.fixture
def guetter_journal(caplog: pytest.LogCaptureFixture) -> Iterator[Callable[..., threading.Event]]:
    """Fabrique d'événements levés par le journal : `vu = guetter_journal("Emploi du temps valide")`.

    `vu.wait(delai)` attend que le moteur (journal `edt.solveur.moteur` par
    défaut) ait émis un message contenant le texte, depuis n'importe quel fil.
    """
    poses: list[tuple[str, _Guetteur]] = []

    def guetter(texte: str, logger: str = "edt.solveur.moteur") -> threading.Event:
        caplog.set_level(logging.INFO, logger=logger)
        gestionnaire = _Guetteur(texte)
        logging.getLogger(logger).addHandler(gestionnaire)
        poses.append((logger, gestionnaire))
        return gestionnaire.vu

    yield guetter
    for logger, gestionnaire in poses:
        logging.getLogger(logger).removeHandler(gestionnaire)
