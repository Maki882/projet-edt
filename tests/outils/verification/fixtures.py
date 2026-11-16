"""Fixtures des tests du vérificateur, utilisées par les tests unitaires et d'intégration.

Elles s'appuient sur celles de `tests/conftest.py` (`jeu_minimal`) et sont importées par nom
dans le `conftest.py` de chaque côté qui s'en sert :

- `version_sans_occurrences` : l'emploi du temps saisi à la main (`versions.EMPLOI_DU_TEMPS`),
  enregistré comme version active par sa propre session, sans séance datée.
"""

from __future__ import annotations

import pytest
from tests.outils.verification.versions import enregistrer_version

from edt.db.moteur import session_base


@pytest.fixture
def version_sans_occurrences(jeu_minimal) -> int:
    with session_base() as session:
        return enregistrer_version(session, jeu_minimal.semestre_id)
