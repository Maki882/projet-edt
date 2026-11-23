"""Accès au site par mot de passe : connexion, droits de la consultation, site ouvert par défaut."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from edt.web import app as module_app
from edt.web.acces import COOKIE_ACCES

GESTION = "essai-gestion"
CONSULTATION = "essai-consultation"


@pytest.fixture
def site_protege(version_generee, monkeypatch):
    """Client de l'application avec les deux mots de passe d'essai et un emploi du temps."""
    monkeypatch.setenv("EDT_MDP_GESTION", GESTION)
    monkeypatch.setenv("EDT_MDP_CONSULTATION", CONSULTATION)
    return TestClient(module_app.creer_app(), follow_redirects=False)


def _connecter(client: TestClient, mot_de_passe: str, suite: str = "/"):
    return client.post("/connexion", data={"mot_de_passe": mot_de_passe, "suite": suite})


def test_sans_mot_de_passe_configure_le_site_reste_ouvert(client):
    client.follow_redirects = False

    assert client.get("/").status_code == 200
    assert client.get("/calculs").status_code == 200
    assert client.get("/connexion").headers["location"] == "/"
    assert "Se déconnecter" not in client.get("/").text


def test_toute_page_demande_la_connexion_et_y_revient_ensuite(site_protege):
    reponse = site_protege.get("/emploi-du-temps?semaine=2")
    assert reponse.status_code == 303
    assert reponse.headers["location"] == "/connexion?suite=%2Femploi-du-temps%3Fsemaine%3D2"
    assert site_protege.get("/sante").status_code == 200
    assert site_protege.get("/static/css/app.css").status_code == 200
    page = site_protege.get("/connexion")
    assert page.status_code == 200 and 'type="password"' in page.text
    assert 'href="/calculs"' not in page.text

    reponse = _connecter(site_protege, GESTION, "/emploi-du-temps?semaine=2")
    assert reponse.status_code == 303
    assert reponse.headers["location"] == "/emploi-du-temps?semaine=2"
    assert "httponly" in reponse.headers["set-cookie"].lower()
    assert site_protege.get("/calculs").status_code == 200


@pytest.mark.cas_limite
def test_mauvais_mot_de_passe_refuse_sans_cookie(site_protege, monkeypatch):
    """Cas : mot de passe faux, vide, ou cookie fabriqué à la main.
    Risque : accès ouvert sans le bon mot de passe, ou essais en rafale sans aucun frein.
    """
    monkeypatch.setattr("edt.web.routes.connexion.PAUSE_APRES_ECHEC_S", 0)
    for faux in ("mauvais-mot", ""):
        reponse = _connecter(site_protege, faux)
        assert reponse.status_code == 401
        assert "Mot de passe incorrect" in reponse.text
        assert COOKIE_ACCES not in reponse.cookies
    site_protege.cookies.set(COOKIE_ACCES, "gestion.9999999999.0000")
    assert site_protege.get("/calculs").status_code == 303


@pytest.mark.cas_limite
def test_une_ecriture_sans_connexion_est_refusee_avant_la_route(site_protege):
    """Cas : un envoi de formulaire ou un fragment HTMX arrive sans connexion.
    Risque : l'écriture passe (aucune route ne vérifie elle-même l'accès), ou HTMX insère la
    page de connexion entière dans un fragment.
    """
    assert (
        site_protege.post("/emploi-du-temps/seances/1/verrou", data={"verrou": "true"}).status_code
        == 303
    )
    fragment = site_protege.get("/a-traiter/compteur", headers={"HX-Request": "true"})
    assert fragment.status_code == 401
    assert fragment.headers["HX-Redirect"] == "/connexion"


def test_la_consultation_lit_l_emploi_du_temps_sans_pouvoir_le_modifier(site_protege):
    _connecter(site_protege, CONSULTATION)

    assert site_protege.get("/").headers["location"] == "/emploi-du-temps"
    page = site_protege.get("/emploi-du-temps")
    assert page.status_code == 200
    assert "Consultation" in page.text and "Se déconnecter" in page.text
    assert 'href="/calculs"' not in page.text and 'href="/salles"' not in page.text
    assert "bouton-assistant" not in page.text

    panneau = re.search(r'hx-get="(/emploi-du-temps/seances/(\d+)/panneau)"', page.text)
    assert panneau, "la grille doit montrer au moins une séance"
    detail = site_protege.get(panneau.group(1), headers={"HX-Request": "true"})
    assert detail.status_code == 200
    assert "Verrouiller" not in detail.text and "edt-formulaire" not in detail.text

    seance = panneau.group(2)
    assert site_protege.get(f"/emploi-du-temps/seances/{seance}/formulaire").status_code == 403
    refus = site_protege.post(f"/emploi-du-temps/seances/{seance}/verrou", data={"verrou": "true"})
    assert refus.status_code == 403
    assert "réservée au gestionnaire" in refus.text
    for page_reservee in ("/calculs", "/salles", "/export", "/export/donnees.xlsx"):
        assert site_protege.get(page_reservee).status_code == 403, page_reservee


def test_la_deconnexion_redemande_le_mot_de_passe(site_protege):
    _connecter(site_protege, GESTION)
    assert site_protege.get("/calculs").status_code == 200

    reponse = site_protege.post("/deconnexion")
    assert reponse.headers["location"] == "/connexion"
    assert site_protege.get("/calculs").status_code == 303
