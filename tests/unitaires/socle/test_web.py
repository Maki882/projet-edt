"""Application web : fonctions appelées directement (module de routes sans routeur refusé, macros des gabarits)."""

from __future__ import annotations

import sys
import types

import pytest
from tests.outils.socle.extension_web import NOM_MODULE_ESSAI

from edt.erreurs import ErreurConfiguration
from edt.web import app as module_app


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
