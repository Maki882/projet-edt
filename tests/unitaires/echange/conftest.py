"""Fixtures des tests unitaires de l'import et de l'export.

Les fixtures partagées (`fichier_exemple`, `importer`, `version_reference`)
viennent de `tests/outils/echange/fixtures.py` ; s'y ajoute, propre à ce côté :

- `exporter()` : l'export des données de la base, en valeurs typées.
"""

from __future__ import annotations

import pytest
from tests.outils.echange.fixtures import fichier_exemple, importer, version_reference  # noqa: F401

from edt.db.moteur import session_base
from edt.echange import donnees


@pytest.fixture
def exporter():
    """Fabrique : export des données d'une base, en valeurs typées par feuille."""

    def exporter_(url: str | None = None) -> dict[str, list[dict]]:
        with session_base(url) as session:
            return {feuille.nom: lignes for feuille, lignes in donnees.exporter_donnees(session)}

    return exporter_
