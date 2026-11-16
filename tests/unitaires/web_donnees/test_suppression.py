"""Suppression, côté service : les conséquences d'une suppression sont expliquées."""

from __future__ import annotations

from edt.db.moteur import session_base
from edt.services.referentiels import operations


def test_consequences_expliquent_la_suite(ids):
    with session_base() as session:
        analyse = operations.analyser_suppression(session, "type_salle", ids.type_cours)
    assert any("accepteront alors toutes les salles" in texte for texte in analyse.consequences)
    assert all(raison.lien == "" for raison in analyse.effets)
