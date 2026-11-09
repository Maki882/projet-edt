"""Outils communs aux tests d'intégration.

Les fixtures de `tests/conftest.py` s'appliquent déjà : chaque test a sa propre
base SQLite dans un dossier temporaire (`environnement`), et peut demander
`jeu_minimal` (base chargée du petit jeu de démonstration) ou `client`
(navigateur simulé qui appelle la vraie application web).

Ici, on ajoute seulement :

- `DELAI` : durée maximale d'attente d'un calcul (repris des tests web) ; un
  calcul bloqué fait échouer le test au lieu de le laisser attendre sans fin ;
- `sans_prechauffage` (automatique) : coupe le préchauffage des écrans, un
  fil d'arrière-plan lancé après un calcul qui survivrait à la base
  temporaire du test. Ce n'est PAS une simulation : solveur, vérificateur et
  base restent les vrais ;
- `calculer(semestre_id)` : lance un vrai calcul et attend sa fin ;
- `compter(modele, *conditions)` : nombre de lignes d'une table.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from tests.web_edt.conftest import DELAI

from edt.db.moteur import session_base
from edt.services import generation
from edt.services.emploi_du_temps import prechauffage

__all__ = ["DELAI", "calculer", "compter"]


@pytest.fixture(autouse=True)
def sans_prechauffage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pas de fil de préchauffage des écrans : il survivrait à la base temporaire du test."""
    monkeypatch.setattr(prechauffage, "ACTIF", False)


def calculer(semestre_id: int) -> generation.SuiviCalcul:
    """Lance un vrai calcul (solveur CP-SAT) sur le semestre et attend sa fin (au plus DELAI s)."""
    suivi = generation.attendre_calcul(generation.lancer_calcul(semestre_id), DELAI)
    assert suivi.reussi, suivi.message
    return suivi


def compter(modele, *conditions, url: str | None = None) -> int:
    """Nombre de lignes de `modele` vérifiant les conditions (base du test, ou `url`)."""
    requete = select(func.count()).select_from(modele)
    if conditions:
        requete = requete.where(*conditions)
    with session_base(url) as session:
        return session.scalar(requete)
