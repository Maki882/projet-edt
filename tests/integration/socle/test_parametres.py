"""Paramètres globaux vus par l'application web : l'écran reste ouvrable avec une valeur corrompue."""

from __future__ import annotations

import pytest

from edt.db.modeles import Parametre
from edt.db.moteur import session_base


@pytest.mark.cas_limite
def test_l_ecran_des_parametres_reste_ouvrable_avec_une_valeur_corrompue(base, client):
    """Cas : l'utilisateur ouvre /parametres pour corriger une valeur corrompue en base.

    Risque : l'écran répond 422 et ne peut pas servir à réparer ; il doit s'ouvrir (200) et
    signaler la valeur enregistrée invalide à réenregistrer.
    """
    with session_base() as session:
        session.get(Parametre, "pause_min").valeur = "quinze"
    reponse = client.get("/parametres")
    assert reponse.status_code == 200
    assert "pause_min" in reponse.text and "invalide" in reponse.text
