"""Fixtures partagées par les tests unitaires et d'intégration de l'import et de l'export.

Elles sont définies ici une seule fois, puis importées par leur nom dans le
`conftest.py` de chaque côté qui s'en sert. Les fixtures de `tests/conftest.py`
(base temporaire, jeu minimal, client web) s'appliquent ; s'y ajoutent :

- `fichier_exemple(nom)` : un fichier fictif de `tests/outils/echange/fichiers/` ;
- `importer(fichiers, ...)` : applique (ou simule) un import dans sa propre
  session validée, et renvoie le rapport ;
- `version_reference` : une version importée (mode référence) depuis
  `seances_existantes.csv`, sans calcul : plus rapide qu'une version générée.

Banque de cas limites : un test de cas limite porte le marqueur
`cas_limite` et commence par une docstring « Cas : ... Risque : ... » ; ses
fichiers se construisent avec `tests/outils/echange/fabrique.py` (CSV, classeur,
archive, iCal, en mémoire et fictifs).
"""

from __future__ import annotations

import pytest
from tests.outils.echange.fabrique import DOSSIER_FICHIERS

from edt.db.moteur import session_base
from edt.services import echange as service
from edt.services.echange import FichierEnvoye, ModeImport, OptionsImport


@pytest.fixture
def fichier_exemple():
    """Fabrique : nom d'un fichier de `fichiers/` -> FichierEnvoye."""

    def lire(nom: str) -> FichierEnvoye:
        return FichierEnvoye(nom, (DOSSIER_FICHIERS / nom).read_bytes())

    return lire


@pytest.fixture
def importer():
    """Fabrique : importe des fichiers (simulés ou non) dans une session validée."""

    def importer_(
        fichiers,
        *,
        mode: ModeImport = ModeImport.DONNEES,
        simuler: bool = False,
        url: str | None = None,
        **options,
    ):
        if isinstance(fichiers, FichierEnvoye):
            fichiers = [fichiers]
        reglages = OptionsImport(mode=mode, **options)
        with session_base(url) as session:
            if simuler:
                return service.simuler_import(session, fichiers, reglages)
            return service.appliquer_import(session, fichiers, reglages)

    return importer_


@pytest.fixture
def version_reference(jeu_minimal, importer, fichier_exemple) -> int:
    """Identifiant d'une version importée en mode référence (sans calcul) sur le jeu minimal."""
    rapport = importer(
        fichier_exemple("seances_existantes.csv"),
        mode=ModeImport.REFERENCE,
        semestre_id=jeu_minimal.semestre_id,
    )
    return rapport.version_id
