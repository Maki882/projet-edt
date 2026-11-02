"""Outils communs aux tests du lot « import et export ».

Les fixtures de `tests/conftest.py` (base temporaire, jeu minimal, client
web) s'appliquent ; s'y ajoutent :

- `fichier_exemple(nom)` : un fichier fictif de `tests/echange/fichiers/` ;
- `importer(fichiers, ...)` : applique (ou simule) un import dans sa propre
  session validée, et renvoie le rapport ;
- `exporter()` : l'export des données de la base, en valeurs typées ;
- `version_generee` : une version produite par un calcul sur le jeu minimal ;
- `version_reference` : une version importée (mode référence) depuis
  `seances_existantes.csv`, sans calcul : plus rapide.

Banque de cas limites : un test de cas limite porte le marqueur
`cas_limite` et commence par une docstring « Cas : ... Risque : ... » ; ses
fichiers se construisent avec `tests/echange/fabrique.py` (CSV, classeur,
archive, iCal, en mémoire et fictifs).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from edt.db.moteur import session_base
from edt.echange import donnees
from edt.services import echange as service
from edt.services import generation
from edt.services.echange import FichierEnvoye, ModeImport, OptionsImport

DOSSIER_FICHIERS = Path(__file__).parent / "fichiers"
DELAI = 30


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
def exporter():
    """Fabrique : export des données d'une base, en valeurs typées par feuille."""

    def exporter_(url: str | None = None) -> dict[str, list[dict]]:
        with session_base(url) as session:
            return {feuille.nom: lignes for feuille, lignes in donnees.exporter_donnees(session)}

    return exporter_


@pytest.fixture
def version_generee(jeu_minimal) -> int:
    """Identifiant d'une version générée (moteur provisoire) sur le jeu minimal."""
    suivi = generation.attendre_calcul(generation.lancer_calcul(jeu_minimal.semestre_id), DELAI)
    return generation.exiger_reussite(suivi).version_id


@pytest.fixture
def version_reference(jeu_minimal, importer, fichier_exemple) -> int:
    """Identifiant d'une version importée en mode référence (sans calcul) sur le jeu minimal."""
    rapport = importer(
        fichier_exemple("seances_existantes.csv"),
        mode=ModeImport.REFERENCE,
        semestre_id=jeu_minimal.semestre_id,
    )
    return rapport.version_id
