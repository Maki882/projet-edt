"""Panneau Assistant sur toutes les pages : non configuré, désactivé, prêt à charger."""

from __future__ import annotations

from html import unescape

import pytest

PAGES = (
    "/",
    "/salles",
    "/besoins",
    "/regles",
    "/emploi-du-temps",
    "/a-traiter",
    "/calculs",
    "/versions",
    "/parametres",
    "/jeux",
)


def test_sans_cle_toutes_les_pages_s_affichent_et_le_panneau_dit_non_configure(client):
    for adresse in PAGES:
        reponse = client.get(adresse)
        assert reponse.status_code == 200, adresse
        page = unescape(reponse.text)
        assert 'id="bouton-assistant"' in page and '<aside class="assistant" id="assistant"' in page
        assert "L'assistant n'est pas configuré sur ce serveur." in page, adresse
        assert 'id="assistant-question"' not in page and "/assistant/panneau" not in page
        assert "/static/js/assistant.js" in page and "/static/css/assistant.css" in page


def test_page_d_erreur_avec_le_panneau(client):
    reponse = client.get("/n-existe-pas")
    assert reponse.status_code == 404
    assert '<aside class="assistant" id="assistant"' in reponse.text
    assert "n'est pas configuré" in unescape(reponse.text)


def test_desactive_par_l_administrateur(client, monkeypatch, cle_factice):
    monkeypatch.setenv("EDT_ASSISTANT_ACTIF", "non")
    page = unescape(client.get("/").text)
    assert "L'assistant est désactivé par l'administrateur." in page
    assert cle_factice not in page


@pytest.mark.parametrize("variable", ["EDT_ASSISTANT_EFFORT", "EDT_ASSISTANT_BUDGET_JOUR"])
def test_configuration_invalide_annoncee_sans_bloquer_les_pages(
    client, monkeypatch, cle_factice, variable
):
    monkeypatch.setenv(variable, "invalide")
    reponse = client.get("/salles")
    assert reponse.status_code == 200
    assert "sa configuration est à corriger par l'administrateur" in unescape(reponse.text)
    assert variable not in reponse.text  # le détail est dans le journal, pas dans la page


def test_configure_le_panneau_charge_la_conversation_a_l_ouverture(client, cle_factice):
    page = client.get("/").text
    assert (
        'hx-get="/assistant/panneau"' in page and 'hx-trigger="edt:assistant-ouvert once"' in page
    )
    assert 'data-disponible="oui"' in page
    assert cle_factice not in page
