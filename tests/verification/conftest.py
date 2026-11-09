"""Fixtures du lot vérificateur sur la base du jeu minimal (base SQLite temporaire).

Elles complètent celles de `tests/conftest.py` (`session`, `jeu_minimal`) :

- `version_id` : l'emploi du temps saisi à la main (`versions.EMPLOI_DU_TEMPS`),
  instancié naïvement sur le calendrier (une séance datée par semaine active) ;
- `version_sans_seances_datees` : le même, sans aucune séance datée (calcul
  réussi mais instanciation absente, ou import de la seule semaine type) ;
- `salle_n204_supprimee` : la salle N204 (TD de mathématiques de A1-G2)
  supprimée par l'application après l'enregistrement de `version_id` ; la
  base met à vide la salle de ses séances (« ON DELETE SET NULL ») ;
- `salle_n204_orpheline` : la même salle effacée en SQL brut, clés étrangères
  non appliquées (base importée, retouche à la main) : ses séances gardent
  l'identifiant d'une salle qui n'existe plus.

Les deux dernières renvoient l'ancien identifiant de N204.
"""

from __future__ import annotations

import sqlite3

import pytest

from edt.db.moteur import obtenir_moteur

from .versions import enregistrer_version, salle_par_code


@pytest.fixture
def version_id(jeu_minimal, session) -> int:
    """L'emploi du temps saisi à la main, avec ses séances datées."""
    return enregistrer_version(session, jeu_minimal.semestre_id, occurrences=True)


@pytest.fixture
def version_sans_seances_datees(jeu_minimal, session) -> int:
    """L'emploi du temps saisi à la main, sans aucune séance datée."""
    return enregistrer_version(session, jeu_minimal.semestre_id)


@pytest.fixture
def salle_n204_supprimee(version_id, session) -> int:
    """N204 supprimée par l'application après l'enregistrement de la version."""
    salle = salle_par_code(session, "N204")
    identifiant = salle.id
    session.delete(salle)
    session.commit()
    # La mise à vide est faite par la base : relire les séances comme le ferait une nouvelle
    # requête (la session garde sinon les objets déjà chargés, `expire_on_commit=False`).
    session.expire_all()
    return identifiant


@pytest.fixture
def salle_n204_orpheline(version_id, session) -> int:
    """N204 effacée en SQL brut, sans mise à vide : ses séances gardent son identifiant."""
    identifiant = salle_par_code(session, "N204").id
    session.commit()
    connexion = sqlite3.connect(obtenir_moteur().url.database)
    try:  # connexion sans « PRAGMA foreign_keys=ON » : rien n'est mis à vide
        connexion.execute("DELETE FROM salle_equipement WHERE salle_id = ?", (identifiant,))
        connexion.execute("DELETE FROM salle WHERE id = ?", (identifiant,))
        connexion.commit()
    finally:
        connexion.close()
    session.expire_all()
    return identifiant
