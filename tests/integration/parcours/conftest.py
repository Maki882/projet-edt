"""Fixture commune aux parcours de bout en bout.

Les fixtures de `tests/conftest.py` s'appliquent déjà : chaque test a sa propre
base SQLite dans un dossier temporaire (`environnement`), et peut demander
`jeu_minimal` (base chargée du petit jeu de démonstration) ou `client`
(navigateur simulé qui appelle la vraie application web).

Ici, on ajoute seulement `sans_prechauffage` (automatique) : elle coupe le
préchauffage des écrans, un fil d'arrière-plan lancé après un calcul qui
survivrait à la base temporaire du test. Ce n'est PAS une simulation :
solveur, vérificateur et base restent les vrais.

Les aides `DELAI`, `calculer` et `compter` sont dans
`tests/outils/parcours/aides.py`.
"""

from __future__ import annotations

import pytest

from edt.services.emploi_du_temps import prechauffage


@pytest.fixture(autouse=True)
def sans_prechauffage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pas de fil de préchauffage des écrans : il survivrait à la base temporaire du test."""
    monkeypatch.setattr(prechauffage, "ACTIF", False)
