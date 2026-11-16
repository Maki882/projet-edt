"""Fixtures des tests unitaires de l'assistant (définies dans `tests/outils/assistant/`).

`sans_cle` est automatique : aucun test ne voit une clé ou un profil du poste.
"""

import pytest
from tests.outils.assistant.fixtures import (  # noqa: F401
    cle_factice,
    sans_cle,
    version_minimale,
)


@pytest.fixture(autouse=True)
def _isoler(sans_cle):  # noqa: F811
    """Chaque test part d'un environnement sans identifiant et d'un assistant neuf."""
