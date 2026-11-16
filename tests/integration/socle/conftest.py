"""Fixtures des tests d'intégration du socle (cas limites compris).

Les tests de cas limites portent le marqueur `cas_limite` et une docstring
« Cas : … Risque : … » ; ils réutilisent ces aides au lieu de les redéfinir :

- `lanceur` : lanceur de la ligne de commande (`CliRunner`) ;
- `lecture_seule` : contexte qui rend un fichier en lecture seule le temps
  du bloc, puis rétablit ses droits ;
- `corrompre_base` : remplace le fichier de la base par du texte (« file is
  not a database »), moteurs libérés ;
- `compter`, `delai_verrou_court`, `verrou_ecriture` : partagées avec les tests
  unitaires, définies dans `tests/outils/socle/fixtures.py`.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from tests.outils.socle.fixtures import compter, delai_verrou_court, verrou_ecriture  # noqa: F401
from typer.testing import CliRunner

from edt.db.moteur import liberer_moteurs


@pytest.fixture
def lanceur() -> CliRunner:
    """Lanceur de la ligne de commande (`python -m edt ...`)."""
    return CliRunner()


@pytest.fixture
def lecture_seule():
    """Contexte : le fichier est en lecture seule le temps du bloc (droits rétablis ensuite)."""

    @contextmanager
    def _lecture_seule(chemin: Path) -> Iterator[Path]:
        os.chmod(chemin, stat.S_IREAD)
        try:
            # Un compte administrateur (root dans un conteneur) ignore ces droits : le test
            # croirait éprouver la lecture seule sans qu'elle s'applique.
            assert not os.access(chemin, os.W_OK), (
                f"{chemin} reste inscriptible malgré la lecture seule (compte administrateur ?) : "
                "lancer les tests avec un compte ordinaire"
            )
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
