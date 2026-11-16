"""Fixtures des tests unitaires du socle (cas limites compris).

- `verrou_ecriture_bref` : un autre fil tient le verrou d'écriture pendant
  quelques dixièmes de seconde (« un autre processus écrit brièvement ») ;
- `compter`, `delai_verrou_court`, `verrou_ecriture` : partagées avec les tests
  d'intégration, définies dans `tests/outils/socle/fixtures.py`.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest
from tests.outils.socle.fixtures import compter, delai_verrou_court, verrou_ecriture  # noqa: F401


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
