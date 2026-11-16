"""Service `edt.services.jeux` exercé directement (sans passer par la commande).

Ces tests utilisent la base de l'environnement de test (fixtures `environnement`
et `base` de `tests/conftest.py`), comme les commandes réelles. La commande
`python -m edt jeu` est testée dans `tests/integration/synthese/test_commande.py`.
"""

from __future__ import annotations

import types

import pytest

from edt.db.moteur import session_base
from edt.erreurs import ErreurCalculEnCours, ErreurRegleMetier
from edt.services import jeux


def test_tailles_disponibles():
    tailles = {t.nom: t for t in jeux.tailles_disponibles()}
    assert list(tailles) == ["petit", "moyen", "grand"]
    assert tailles["grand"].nb_promotions == 5 and tailles["grand"].semestres == ("S1", "S2")
    assert all(t.description for t in tailles.values())


@pytest.mark.cas_limite
def test_base_non_initialisee_refusee(environnement):
    """Cas : chargement d'un jeu sans remise à zéro dans une base jamais initialisée.
    Risque : erreur SQL « no such table » au lieu de l'indication « init-db »."""
    with pytest.raises(ErreurRegleMetier, match="pas initialisée"):
        jeux.recharger_jeu_fictif("petit")


@pytest.mark.cas_limite
def test_refus_pendant_un_calcul(base, monkeypatch):
    """Cas : chargement ou remise à zéro demandés pendant un calcul d'emploi du temps.
    Risque : le calcul lit une base qui change ou disparaît sous lui."""
    monkeypatch.setattr(jeux, "calcul_en_cours", lambda: types.SimpleNamespace(id=42))
    with pytest.raises(ErreurCalculEnCours, match="n°42"):
        jeux.recharger_jeu_fictif("petit", reinitialiser=True)
    with session_base() as session, pytest.raises(ErreurCalculEnCours):
        jeux.charger_jeu_fictif(session, "petit")
