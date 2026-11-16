"""Fixtures partagées par les tests unitaires et d'intégration des écrans de l'emploi du temps.

Importées par leur nom dans le `conftest.py` de `tests/unitaires/web_edt` et
de `tests/integration/web_edt` :

- `sans_prechauffage` (appliquée d'office) : pas de fil de préchauffage ;
- `faux_verificateur` : faux `edt.services.verification`, réglable ;
- `sans_verificateur` : comme si le vérificateur n'était pas livré (sans
  ces deux fixtures, c'est le vrai vérificateur qui tourne) ;
- `aujourdhui_fige` : le « semestre par défaut » calculé au 5 octobre 2026.
"""

from __future__ import annotations

import sys

import pytest
from tests.outils.web_edt.aides import FauxVerificateur

from edt.services import generation
from edt.services.emploi_du_temps import memoire, prechauffage
from edt.services.emploi_du_temps import versions as service_versions


@pytest.fixture(autouse=True)
def sans_prechauffage(monkeypatch) -> None:
    """Pas de fil de préchauffage dans les tests : il survivrait à la base temporaire."""
    monkeypatch.setattr(prechauffage, "ACTIF", False)


@pytest.fixture
def faux_verificateur(monkeypatch) -> FauxVerificateur:
    """Installe un faux vérificateur et de fausses pré-vérifications, vides par défaut."""
    memoire.oublier()
    faux = FauxVerificateur()
    monkeypatch.setitem(sys.modules, "edt.services.verification", faux.module())
    monkeypatch.setattr(
        generation, "pre_verifier_semestre", faux.pre_verifier_semestre, raising=False
    )
    return faux


@pytest.fixture
def sans_verificateur(monkeypatch) -> None:
    """Fait comme si le vérificateur n'était pas livré (le raccord doit le dire, pas planter)."""
    memoire.oublier()
    monkeypatch.setitem(sys.modules, "edt.services.verification", None)


@pytest.fixture
def aujourdhui_fige(monkeypatch, aujourdhui):
    """Le semestre par défaut est choisi comme si l'on était le 5 octobre 2026."""
    original = service_versions.semestre_par_defaut
    monkeypatch.setattr(
        service_versions,
        "semestre_par_defaut",
        lambda session, jour=None: original(session, jour or aujourdhui),
    )
    return aujourdhui
