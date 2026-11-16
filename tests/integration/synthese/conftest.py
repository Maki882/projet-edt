"""Fixtures des tests d'intégration du générateur de données fictives."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner


@pytest.fixture
def lanceur() -> CliRunner:
    """Exécuteur de la ligne de commande `python -m edt` (sur la base de l'environnement)."""
    return CliRunner()
