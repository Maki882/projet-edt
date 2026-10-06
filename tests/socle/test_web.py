"""Application web du socle : accueil, santé, erreurs lisibles, mécanisme d'extension."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from fastapi import APIRouter, Request
from fastapi.testclient import TestClient
from sqlalchemy import select

from edt.db.modeles import Campus
from edt.db.moteur import session_base
from edt.erreurs import ErreurConfiguration, ErreurRegleMetier
from edt.web import app as module_app
from edt.web.dependances import SessionRequete
from edt.web.gabarits import rediriger
from edt.web.menu import SECTION_REFERENTIELS, EntreeMenu

NOM_MODULE_ESSAI = "edt.web.routes.essai_socle"


def _journal(dossier: Path) -> str:
    return (dossier / "logs" / "edt.log").read_text(encoding="utf-8")


@pytest.fixture
def client_etendu(base, monkeypatch):
    """Application à laquelle un module de routes d'essai est ajouté par la découverte."""
    routeur = APIRouter(prefix="/essai")

    @routeur.get("/panne")
    def panne():
        raise RuntimeError("détail technique à ne pas montrer")

    @routeur.get("/refus")
    def refus():
        raise ErreurRegleMetier("Cette salle est encore utilisée.", ["par le besoin n° 12"])

    @routeur.post("/campus")
    def creer_campus(request: Request, session: SessionRequete, code: str):
        session.add(Campus(code=code, nom=f"Campus {code}"))
        return rediriger(request, "/", f"Campus {code} créé.")

    module = types.ModuleType(NOM_MODULE_ESSAI)
    module.routeur = routeur
    module.MENU = [EntreeMenu("Écran d'essai", "/essai", section=SECTION_REFERENTIELS, ordre=150)]
    monkeypatch.setitem(sys.modules, NOM_MODULE_ESSAI, module)
    reels = module_app.modules_de_routes()
    monkeypatch.setattr(module_app, "modules_de_routes", lambda: [*reels, NOM_MODULE_ESSAI])
    return TestClient(module_app.creer_app())


def test_accueil_affiche_l_etat_de_la_base(jeu_minimal, client):
    reponse = client.get("/")

    assert reponse.status_code == 200
    assert reponse.headers["X-Request-ID"]
    page = reponse.text
    assert "Données fictives" in page
    assert "La base est initialisée" in page
    assert "Contenu</h2>" not in page  # plus de compteurs d'entités sur l'accueil
    assert 'href="/" class="actif"' in page  # l'entrée de menu « Accueil » est active


def test_accueil_reste_lisible_sans_base_initialisee(environnement):
    client = TestClient(module_app.creer_app())

    reponse = client.get("/")
    assert reponse.status_code == 200
    assert "pas encore initialisée" in reponse.text

    sante = client.get("/sante")
    assert sante.status_code == 503
    assert sante.json()["statut"] == "degrade"


def test_sante(client):
    reponse = client.get("/sante")

    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["statut"] == "ok"
    assert corps["base"] == {
        "accessible": True,
        "initialisee": True,
        "mode_journal": "wal",
        "ecarts_schema": [],
    }


def test_fichiers_statiques_embarques(client):
    assert client.get("/static/css/app.css").status_code == 200
    htmx = client.get("/static/js/htmx.min.js")
    assert htmx.status_code == 200
    assert "htmx" in htmx.text and len(htmx.text) > 10_000


def test_erreur_inattendue_page_lisible_et_trace_journalisee(client_etendu, environnement):
    reponse = client_etendu.get("/essai/panne")

    assert reponse.status_code == 500
    id_requete = reponse.headers["X-Request-ID"]
    assert "Une erreur inattendue" in reponse.text
    assert id_requete in reponse.text  # la référence est affichée à l'utilisateur
    assert "Traceback" not in reponse.text
    assert "détail technique" not in reponse.text

    journal = _journal(environnement)
    assert f"req={id_requete}" in journal
    assert "Traceback" in journal
    assert "RuntimeError: détail technique à ne pas montrer" in journal


def test_erreur_metier_traduite_en_message(client_etendu):
    reponse = client_etendu.get("/essai/refus")

    assert reponse.status_code == 409
    assert "Cette salle est encore utilisée." in reponse.text
    assert "par le besoin n° 12" in reponse.text


def test_erreur_rendue_en_fragment_pour_htmx_et_en_json(client_etendu):
    fragment = client_etendu.get("/essai/refus", headers={"HX-Request": "true"})
    assert fragment.status_code == 409
    assert "<html" not in fragment.text and "message-erreur" in fragment.text

    donnees = client_etendu.get("/essai/refus", headers={"Accept": "application/json"})
    assert donnees.json()["message"] == "Cette salle est encore utilisée."


def test_page_inconnue(client):
    reponse = client.get("/n-existe-pas")
    assert reponse.status_code == 404
    assert "Page introuvable" in reponse.text


def test_module_de_routes_decouvert_avec_son_menu(client_etendu):
    page = client_etendu.get("/").text
    assert "Référentiels" in page
    assert '<a href="/essai">Écran d&#39;essai</a>' in page


def test_session_validee_avant_la_redirection_et_message_affiche(client_etendu):
    reponse = client_etendu.post("/essai/campus", params={"code": "ZZ"}, follow_redirects=False)
    assert reponse.status_code == 303
    with session_base() as session:
        assert session.scalar(select(Campus.code)) == "ZZ"  # déjà validé à ce stade

    assert "Campus ZZ créé." in client_etendu.get("/").text
    assert "Campus ZZ créé." not in client_etendu.get("/").text  # affiché une seule fois

    # Un doublon viole l'unicité : refus expliqué, rien d'enregistré.
    doublon = client_etendu.post("/essai/campus", params={"code": "ZZ"}, follow_redirects=False)
    assert doublon.status_code == 409
    assert "Opération refusée" in doublon.text


def test_module_de_routes_sans_routeur_refuse(base, monkeypatch):
    monkeypatch.setitem(sys.modules, NOM_MODULE_ESSAI, types.ModuleType(NOM_MODULE_ESSAI))
    monkeypatch.setattr(module_app, "modules_de_routes", lambda: [NOM_MODULE_ESSAI])
    with pytest.raises(ErreurConfiguration, match="routeur"):
        module_app.creer_app()


def test_macros_tableau_formulaire_et_message():
    from edt.web.gabarits import Message, gabarits

    source = """{% import "macros.html" as ui %}
{% call(salle) ui.tableau([("code", "Code"), ("pmr", "Accessible"), ("note", "Note")], salles) %}
  {{ ui.bouton_suppression("/salles/" ~ salle.code ~ "/supprimer") }}
{% endcall %}
{{ ui.tableau([("code", "Code")], [], vide="Aucune salle.") }}
{% call ui.formulaire("/salles", libelle_bouton="Créer") %}
  {{ ui.champ("capacite", "Places", valeur=30, type="number", requis=true, erreur="Trop grand") }}
  {{ ui.champ("type", "Type", valeur=2, type="select", options=[(1, "Amphi"), (2, "Cours")]) }}
{% endcall %}
{{ ui.messages(messages) }}
{{ 510|heure }} / {{ 120|duree }} / {{ 2|jour }}"""
    page = gabarits.env.from_string(source).render(
        salles=[{"code": "N204", "pmr": False, "note": "<i>RDC</i>"}],
        messages=[Message("Salle enregistrée.", "succes")],
    )

    assert "<td>N204</td>" in page and "<td>Non</td>" in page
    assert "&lt;i&gt;RDC&lt;/i&gt;" in page  # le contenu est échappé
    assert 'action="/salles/N204/supprimer" data-confirmation=' in page
    assert '<p class="vide">Aucune salle.</p>' in page
    assert 'name="capacite" value="30" required' in page and "Trop grand" in page
    assert '<option value="2" selected>Cours</option>' in page
    assert 'class="message message-succes"' in page
    assert "8 h 30 / 2 h / mercredi" in page
