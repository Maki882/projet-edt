"""Fixtures des tests d'intégration du solveur, en plus de celles de `tests/conftest.py`.

- `lanceur` : lanceur de la ligne de commande `python -m edt` ;
- `processus_vivant` : un autre processus qui vit pendant tout le test (un
  calcul « en cours » attribué à son numéro n'est pas tenu pour abandonné),
  tué à la fin du test.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator

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
