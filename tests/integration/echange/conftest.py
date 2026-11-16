"""Fixtures des tests d'intégration de l'import et de l'export.

Les fixtures partagées (`fichier_exemple`, `importer`, `version_reference`)
viennent de `tests/outils/echange/fixtures.py` ; s'y ajoute, propre à ce côté :

- `version_generee` : une version produite par un calcul complet sur le jeu minimal.
"""

from __future__ import annotations

import pytest
from tests.outils.echange.fixtures import fichier_exemple, importer, version_reference  # noqa: F401

from edt.services import generation

DELAI = 30


@pytest.fixture
def version_generee(jeu_minimal) -> int:
    """Identifiant de la version produite par un calcul complet sur le jeu minimal."""
    suivi = generation.attendre_calcul(generation.lancer_calcul(jeu_minimal.semestre_id), DELAI)
    return generation.exiger_reussite(suivi).version_id
