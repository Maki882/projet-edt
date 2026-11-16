"""Mécanisme générique, côté service : modification partielle, refus détaillé par champ, identifiants."""

from __future__ import annotations

import pytest

from edt.db.moteur import session_base
from edt.services.referentiels import operations
from edt.services.referentiels.operations import ErreurSaisie


def test_modification_partielle_par_le_service(ids):
    with session_base() as session:
        valeurs = operations.modifier(
            session, "salle", ids.salle_n016, {"capacite": 99}, partiel=True
        )
    assert valeurs["capacite"] == 99 and len(valeurs["equipements"]) == 2


def test_service_refuse_une_saisie_avec_le_detail_par_champ(ids):
    with session_base() as session, pytest.raises(ErreurSaisie) as erreur:
        operations.creer(session, "salle", {"code": "", "campus_id": 999, "capacite": -1})
    assert set(erreur.value.champs) == {"code", "campus_id", "type_salle_id", "capacite"}


def test_identifiants_de_toutes_les_pages(ids):
    with session_base() as session:
        tous = operations.identifiants(session, "salle")
        issy = operations.identifiants(session, "salle", filtres={"campus": str(ids.issy)})
        cherches = operations.identifiants(session, "salle", recherche="n2")
    assert len(tous) == 6 and len(issy) == 2 and len(cherches) == 1
