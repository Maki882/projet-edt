"""Application web : fonctions appelées directement (module de routes sans routeur refusé, macros des gabarits)."""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import types

import pytest
from starlette.requests import Request
from tests.outils.socle.extension_web import NOM_MODULE_ESSAI

from edt.erreurs import ErreurConfiguration
from edt.web import app as module_app
from edt.web.gabarits import DOSSIER_STATIQUE, rediriger
from edt.web.intergiciels import IntergicielJournal, est_silencieux


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


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("adresse", "attendue"),
    [
        ("/salles?q=日", "/salles?q=%E6%97%A5"),
        ("/regles?q=été&annee=1", "/regles?q=%C3%A9t%C3%A9&annee=1"),
        ("/salles?q=N%C3%A9", "/salles?q=N%C3%A9"),  # déjà codée : laissée telle quelle
    ],
)
def test_redirection_htmx_vers_une_adresse_non_ascii(adresse, attendue):
    """Cas : une action envoyée par HTMX revient à une adresse dont la recherche contient
    « é » ou « 日 » (champ caché `retour`).

    Risque : l'en-tête `HX-Redirect` n'accepte que le latin-1 : page 500 et écriture valide
    annulée au lieu de la redirection.
    """
    requete = Request({"type": "http", "method": "POST", "headers": [(b"hx-request", b"true")]})
    reponse = rediriger(requete, adresse, "Enregistré.")
    assert reponse.status_code == 204
    assert reponse.headers["HX-Redirect"] == attendue


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("chemin", "statut", "silencieux"),
    [
        ("/calculs/12/etat", 200, True),
        ("/static/css/app.css", 200, True),
        ("/calculs/12/etat", 404, False),  # une erreur reste au niveau INFO
        ("/calculs/12", 200, False),
        ("/calculs/12/etat/autre", 200, False),
        ("/assistant/echanges/abc123", 204, True),  # suivi d'un échange avec l'assistant
        ("/assistant/echanges/abc123", 404, False),
        ("/assistant/panneau", 200, False),
        ("/tests/unitaires/suivi", 200, True),  # suivi d'une exécution des tests
        ("/tests/integration/suivi", 200, True),
        ("/tests/integration/suivi", 500, False),
        ("/tests/integration", 200, False),
        ("/tests/autre/suivi", 200, False),
    ],
)
def test_suivi_d_un_calcul_journalise_en_debug(chemin, statut, silencieux):
    """Cas : la page de suivi interroge `/calculs/N/etat` chaque seconde pendant un calcul, le
    panneau de l'assistant `/assistant/echanges/<jeton>` pendant un échange, et l'onglet des
    tests `/tests/<famille>/suivi` pendant une exécution.

    Risque : une ligne INFO par seconde, un tiers du journal, qui noie les vrais événements.
    """
    assert est_silencieux(chemin, statut) is silencieux


@pytest.mark.cas_limite
def test_requete_annulee_par_l_arret_du_serveur_n_est_pas_journalisee_en_500(caplog):
    """Cas : le serveur s'arrête pendant qu'une page se prépare (préparation des premières
    pages au démarrage, ou visiteur en cours) : la requête est annulée (`CancelledError`).

    Risque : le journal note « GET / -> 500 », une erreur qui n'a pas eu lieu, sans trace ni
    cause ; l'exploitant cherche une panne qui n'existe pas.
    """

    async def page_annulee(scope, receive, send):
        raise asyncio.CancelledError

    async def recevoir():
        return {"type": "http.request"}

    async def envoyer(message):
        raise AssertionError("aucune réponse ne doit partir")

    intergiciel = IntergicielJournal(page_annulee)
    portee = {"type": "http", "method": "GET", "path": "/", "headers": []}
    caplog.set_level(logging.DEBUG, logger="edt.web.requetes")
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(intergiciel(portee, recevoir, envoyer))
    lignes = [r.getMessage() for r in caplog.records if r.name == "edt.web.requetes"]
    assert lignes and not any("500" in ligne for ligne in lignes), lignes
    assert any("GET / -> interrompue" in ligne for ligne in lignes), lignes


@pytest.mark.cas_limite
def test_script_commun_contre_le_double_envoi_et_le_bandeau_reseau_qui_reste():
    """Cas : double clic sur un bouton d'envoi ; serveur redémarré pendant le suivi d'un calcul.

    Risque : second envoi qui affiche « Élément introuvable » ou « Opération refusée » ;
    bandeau « Le serveur ne répond pas » laissé à l'écran une fois le serveur revenu. Le
    comportement se vérifie dans un navigateur (aucun moteur JavaScript dans les tests) : ce
    test garde les points d'accroche du script et les appels qui leur donnent leur effet.
    """
    script = (DOSSIER_STATIQUE / "js" / "app.js").read_text(encoding="utf-8")
    # Double envoi : tout formulaire POST, réactivé au retour sur la page.
    assert '!== "post"' in script and 'setAttribute("data-envoi-en-cours"' in script
    # Pas de bouton mort : la réactivation rend le bouton cliquable, et elle est appelée au
    # retour sur la page (pageshow) comme à l'expiration de la minuterie.
    reactiver = re.search(r"function reactiver\(boutons\) \{(.*?)\n  \}\n", script, re.S)
    assert reactiver is not None, "fonction reactiver introuvable"
    assert "bouton.disabled = false;" in reactiver.group(1)
    assert 'removeAttribute("data-envoi-en-cours")' in reactiver.group(1)
    assert re.search(
        r'addEventListener\("pageshow", function \(\) \{\s*'
        r'reactiver\(document\.querySelectorAll\("\[data-envoi-en-cours\]"\)\);',
        script,
    ), "pageshow sans réactivation"
    assert re.search(
        r"setTimeout\(function \(\) \{\s*reactiver\(boutons\);\s*\}, DELAI_REACTIVATION_MS\)",
        script,
    ), "minuterie sans réactivation"
    # Panne de réseau : bandeau marqué, retiré à la première requête réussie.
    assert 'setAttribute("data-erreur-reseau"' in script
    assert 'addEventListener("htmx:afterRequest"' in script
    assert 'querySelector("[data-erreur-reseau]")' in script
