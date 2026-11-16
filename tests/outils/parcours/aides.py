"""Aides des parcours de bout en bout.

- `DELAI` : durée maximale d'attente d'un calcul ; un calcul bloqué fait
  échouer le test au lieu de le laisser attendre sans fin ;
- `calculer(semestre_id)` : lance un vrai calcul et attend sa fin ;
- `compter(modele, *conditions)` : nombre de lignes d'une table.

Les modules de tests les importent d'ici (`from tests.outils.parcours.aides
import calculer`) : un module de tests n'importe jamais un `conftest.py`.
"""

from __future__ import annotations

from sqlalchemy import func, select

from edt.db.moteur import session_base
from edt.services import generation

__all__ = ["DELAI", "calculer", "compter"]

DELAI = 20
"""Secondes : garde-fou pour qu'un calcul bloqué fasse échouer le test au lieu d'attendre sans fin."""


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
