"""Panneau Assistant par HTTP, avec le faux client : question, proposition, limites, gardes.

Chaque test passe par le client de test de l'application ; l'échange tourne
dans son fil, et `_attendre` interroge `GET /assistant/echanges/<jeton>`
comme le ferait le navigateur.
"""

from __future__ import annotations

import re
import time
from html import unescape

import pytest
from sqlalchemy import func, select
from tests.outils.assistant.faux_client import message, reponse_outil, reponse_texte
from tests.outils.assistant.fixtures import CLE_FACTICE

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.services import assistant

HX = {"HX-Request": "true", "HX-Current-URL": "http://testserver/salles?page=2"}
REGLE_CM = {
    "type": "CRENEAUX_INTERDITS",
    "portee": {"nature": "type_seance", "cible": "CM"},
    "parametres": {"jours": ["mercredi"], "debut": "13:45", "fin": "18:00"},
    "niveau": None,
    "periode": {},
    "commentaire": None,
}


def _envoyer(site, texte="Bonjour", **entetes):
    return site.post(
        "/assistant/messages", data={"message": texte, "page": "/salles"}, headers={**HX, **entetes}
    )


def _jeton(reponse) -> str:
    return re.search(r'hx-get="/assistant/echanges/([^?"]+)\?vu=', reponse.text).group(1)


def _attendre(site, jeton: str, delai_s: float = 5.0) -> str:
    """Interroge l'échange comme le navigateur, jusqu'à la bulle finale ; renvoie son HTML."""
    vu, fin = 1, time.monotonic() + delai_s
    while time.monotonic() < fin:
        reponse = site.get(f"/assistant/echanges/{jeton}?vu={vu}", headers=HX)
        if reponse.status_code == 200 and "assistant-bulle-attente" not in reponse.text:
            return reponse.text
        if reponse.status_code == 200:
            vu = int(re.search(r"\?vu=(\d+)", reponse.text).group(1))
        time.sleep(0.02)
    raise AssertionError("échange non terminé")


def _compter(modele) -> int:
    with session_base() as session:
        return session.scalar(select(func.count()).select_from(modele))


@pytest.fixture
def site(client, jour_fige):
    return client


# --------------------------------------------------------------------------- #
# Question, réponse
# --------------------------------------------------------------------------- #


def test_question_puis_reponse(site, faux_client):
    faux_client.reponses = [
        reponse_outil("lire_contexte", {}),
        reponse_texte("Voici :\n- premier point\n- second <script>alert(1)</script>\n\nFin."),
    ]
    envoi = _envoyer(site, "Quel est le semestre courant ?")
    assert envoi.status_code == 200 and envoi.headers["X-Assistant-Accepte"] == "oui"
    assert "edt_assistant" in envoi.cookies
    assert "Quel est le semestre courant ?" in unescape(envoi.text)
    assert 'hx-trigger="every 1s"' in envoi.text and 'hx-swap-oob="true"' in envoi.text
    final = _attendre(site, _jeton(envoi))
    assert "<li>premier point</li>" in final and "<p>Fin.</p>" in final
    assert "<script>" not in final and "&lt;script&gt;" in final
    assert "9 messages possibles dans cette conversation" in final
    # Le message système du tour porte la page affichée (chemin seul).
    assert (
        "Page affichée par l'utilisateur : /salles"
        in faux_client.appels[0]["messages"][1]["content"]
    )
    panneau = site.get("/assistant/panneau", headers=HX).text
    assert "premier point" in panneau and 'id="assistant-question"' in panneau


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("page", "attendue"),
    [
        ("/" + "数" * 85_000, None),
        ("/salles?page=2&q=" + "x" * 300, "/salles"),
        ("/regles/<script>", None),
        ("https://autre.example.org/salles", None),
        ("/emploi-du-temps", "/emploi-du-temps"),
    ],
    ids=["85000-caracteres", "parametres", "balise", "autre-site", "page-valide"],
)
def test_page_affichee_bornee_au_chemin_d_une_page(site, faux_client, caplog, page, attendue):
    """Cas : le champ caché « page » est fabriqué : 85 000 caractères, une adresse avec
    paramètres, un chemin avec balise, une adresse d'un autre site.

    Risque : un texte arbitraire qui contourne la limite de 800 caractères, part en entier au
    modèle (coût sous-estimé par la réservation) et dans le journal.
    """
    faux_client.reponses = [reponse_texte("ok")]
    envoi = site.post("/assistant/messages", data={"message": "Bonjour", "page": page}, headers=HX)
    assert envoi.headers.get("X-Assistant-Accepte") == "oui"
    _attendre(site, _jeton(envoi))
    systeme = faux_client.appels[0]["messages"][1]["content"]
    assert len(systeme) < 300
    if attendue is None:
        assert "Page affichée" not in systeme
    else:
        assert f"Page affichée par l'utilisateur : {attendue}\n" in systeme + "\n"
    debut = [r.getMessage() for r in caplog.records if "commencé" in r.getMessage()]
    assert debut and all(len(ligne) < 300 for ligne in debut)
    assert "数" not in caplog.text and "<script>" not in caplog.text


def test_interrogation_sans_changement_repond_204(site, faux_client):
    faux_client.reponses = [reponse_texte("ok")]
    faux_client.bloquer()
    envoi = _envoyer(site)
    assert faux_client.en_attente.wait(5)
    jeton = _jeton(envoi)
    assert site.get(f"/assistant/echanges/{jeton}?vu=1", headers=HX).status_code == 204
    pendant = site.get("/assistant/panneau", headers=HX).text
    assert "assistant-bulle-attente" in pendant
    faux_client.liberer()
    assert "ok" in _attendre(site, jeton)


def test_echange_perdu_apres_redemarrage(site, faux_client):
    faux_client.reponses = [reponse_texte("ok")]
    envoi = _envoyer(site)
    jeton = _jeton(envoi)
    _attendre(site, jeton)
    assistant.reinitialiser()  # redémarrage du serveur : la mémoire est vide
    perdue = site.get(f"/assistant/echanges/{jeton}?vu=1", headers=HX)
    assert "Cette réponse a été perdue" in unescape(perdue.text)
    panneau = site.get("/assistant/panneau", headers=HX)
    assert "Conversation réinitialisée" in unescape(panneau.text)
    assert 'edt_assistant=""' in panneau.headers.get("set-cookie", "")


def test_exemples_tires_des_donnees(jeu_minimal, client, jour_fige, cle_factice):
    panneau = unescape(client.get("/assistant/panneau", headers=HX).text)
    assert "« M. Bernard n'est pas disponible le mercredi matin. »" in panneau
    assert "« Ajouter 2 h de TD de mathématiques par semaine à la classe A1-G1. »" in panneau
    assert "Quelles salles de plus de 40 places" in panneau
    assert "10 messages possibles dans cette conversation" in panneau


# --------------------------------------------------------------------------- #
# Propositions
# --------------------------------------------------------------------------- #


def _proposer(site, faux_client) -> tuple[str, str]:
    faux_client.reponses = [
        reponse_outil("proposer_regle", REGLE_CM),
        reponse_texte("Je vous propose."),
    ]
    final = _attendre(site, _jeton(_envoyer(site, "Pas de CM le mercredi après-midi.")))
    jeton = re.search(r'/assistant/propositions/([^/]+)/confirmer"', final).group(1)
    return final, jeton


def test_proposition_confirmee_par_le_bouton(jeu_minimal, client, jour_fige, faux_client):
    avant = _compter(m.Regle)
    final, jeton = _proposer(client, faux_client)
    assert "P1 · Nouvelle règle" in unescape(final) and "À confirmer" in unescape(final)
    assert _compter(m.Regle) == avant
    reponse = client.post(f"/assistant/propositions/{jeton}/confirmer", headers=HX)
    texte = unescape(reponse.text)
    assert reponse.status_code == 200 and "Confirmée" in texte
    assert "Enregistrée : règle R005, active." in texte and "data-focus" in texte
    assert f'href="/regles?annee={jeu_minimal.annee_scolaire_id}&amp;q=R005"' in reponse.text
    deuxieme = unescape(client.post(f"/assistant/propositions/{jeton}/confirmer", headers=HX).text)
    assert "Déjà confirmée" in deuxieme
    assert _compter(m.Regle) == avant + 1


def test_proposition_refusee_par_le_bouton(jeu_minimal, client, jour_fige, faux_client):
    avant = _compter(m.Regle)
    _, jeton = _proposer(client, faux_client)
    texte = unescape(client.post(f"/assistant/propositions/{jeton}/refuser", headers=HX).text)
    assert "Refusée : rien n'a été enregistré." in texte and "Confirmer</button>" not in texte
    assert _compter(m.Regle) == avant
    faux_client.reponses = [reponse_texte("Entendu.")]
    _attendre(client, _jeton(_envoyer(client, "Merci")))
    assert "P1 refusée" in faux_client.appels[-1]["messages"][-1]["content"]


def test_proposition_inconnue(site):
    texte = unescape(site.post("/assistant/propositions/inconnue/confirmer", headers=HX).text)
    assert "Cette proposition n'existe plus" in texte


@pytest.mark.cas_limite
def test_base_occupee_au_clic_sur_confirmer(
    jeu_minimal, client, jour_fige, faux_client, delai_verrou_court, verrou_ecriture
):
    """Cas : un autre processus tient la base quand l'utilisateur clique sur Confirmer.

    Risque : une page « base occupée » dans la zone de messages, invisible au téléphone ; ici
    la carte le dit et reste à confirmer.
    """
    _, jeton = _proposer(client, faux_client)
    with verrou_ecriture():
        reponse = client.post(f"/assistant/propositions/{jeton}/confirmer", headers=HX)
    assert reponse.status_code == 200
    assert "Base occupée : rien n'a été enregistré, réessayez." in unescape(reponse.text)
    assert "Confirmer</button>" in reponse.text


def test_formulaire_prerempli_du_besoin(version_minimale, client, jour_fige, faux_client):
    faux_client.reponses = [
        reponse_outil(
            "preparer_formulaire_besoin",
            {
                "semestre": None,
                "matiere": "IM.1101",
                "type_seance": "TD",
                "groupes": ["A1-G1"],
                "enseignants": ["ENS003"],
                "duree_minutes": 120,
                "seances_par_semaine": 1,
            },
        ),
        reponse_texte("Voici le formulaire."),
    ]
    final = _attendre(client, _jeton(_envoyer(client, "Ajouter 2 h de TD")))
    adresse = unescape(re.search(r'href="(/besoins/nouveau\?[^"]+)"', final).group(1))
    formulaire = client.get(adresse)
    assert formulaire.status_code == 200
    texte = unescape(formulaire.text)
    assert re.search(r'name="duree_min"[^>]*value="2 h"', texte)
    with session_base() as session:
        matiere = session.scalar(select(m.Matiere.id).where(m.Matiere.code == "IM.1101"))
        groupe = session.scalar(select(m.Groupe.id).where(m.Groupe.code == "A1-G1"))
        enseignant = session.scalar(
            select(m.Enseignant.id).where(m.Enseignant.identifiant == "ENS003")
        )
    assert f'<option value="{matiere}" selected>' in texte
    assert re.search(rf'name="groupes" value="{groupe}" checked', texte)
    assert re.search(rf'name="enseignants" value="{enseignant}" checked', texte)
    assert re.search(r'name="seances_par_semaine"[^>]*value="1"', texte)


# --------------------------------------------------------------------------- #
# Limites et gardes
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
def test_limite_de_la_conversation_sans_appel(site, faux_client, monkeypatch):
    """Cas : second message alors que la conversation n'en permet qu'un.

    Risque : appel facturé malgré la limite ; refus rendu en 4xx, invisible dans le panneau.
    """
    monkeypatch.setenv("EDT_ASSISTANT_MESSAGES_PAR_CONVERSATION", "1")
    faux_client.reponses = [reponse_texte("ok")]
    _attendre(site, _jeton(_envoyer(site)))
    refus = _envoyer(site, "Encore")
    assert refus.status_code == 200 and "X-Assistant-Accepte" not in refus.headers
    assert "assistant-bulle-attention" in refus.text
    assert "Cette conversation est terminée (1 messages)" in unescape(refus.text)
    assert len(faux_client.appels) == 1


@pytest.mark.cas_limite
def test_budget_du_jour_a_zero(site, faux_client, monkeypatch):
    """Cas : budget du jour fixé à 0 $ par l'administrateur.

    Risque : un appel facturé au-delà du plafond.
    """
    monkeypatch.setenv("EDT_ASSISTANT_BUDGET_JOUR", "0")
    refus = _envoyer(site)
    assert "limite du jour" in unescape(refus.text) and faux_client.appels == []


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    "entetes",
    [{"Sec-Fetch-Site": "cross-site"}, {"Origin": "https://autre.example.org"}, {"Origin": "null"}],
)
def test_envoi_depuis_un_autre_site_refuse(site, faux_client, entetes):
    """Cas : une page d'un autre site fait envoyer des messages par le navigateur du visiteur.

    Risque : le budget de l'assistant dépensé à l'insu du visiteur.
    """
    faux_client.reponses = [reponse_texte("ok")]
    assert _envoyer(site, **entetes).status_code == 403
    assert (
        site.post("/assistant/conversation/nouvelle", headers={**HX, **entetes}).status_code == 403
    )
    assert (
        site.post("/assistant/propositions/x/confirmer", headers={**HX, **entetes}).status_code
        == 403
    )
    assert faux_client.appels == []
    meme_site = _envoyer(site, **{"Sec-Fetch-Site": "same-origin", "Origin": "http://testserver"})
    assert meme_site.headers.get("X-Assistant-Accepte") == "oui"


def test_nouvelle_conversation(site, faux_client):
    faux_client.reponses = [reponse_texte("ok"), reponse_texte("ok")]
    faux_client.bloquer()
    envoi = _envoyer(site)
    assert faux_client.en_attente.wait(5)
    refus = site.post("/assistant/conversation/nouvelle", headers=HX)
    assert "Une réponse est en préparation" in unescape(refus.text)
    faux_client.liberer()
    _attendre(site, _jeton(envoi))
    nouvelle = site.post("/assistant/conversation/nouvelle", headers=HX)
    texte = unescape(nouvelle.text)
    assert "Nouvelle conversation." in texte and "Exemples de demandes" in texte
    assert 'edt_assistant=""' in nouvelle.headers.get("set-cookie", "")


def test_sans_cle_l_envoi_est_refuse_proprement(client):
    refus = client.post("/assistant/messages", data={"message": "Bonjour"}, headers=HX)
    assert refus.status_code == 200 and "n'est pas configuré" in unescape(refus.text)


@pytest.mark.cas_limite
def test_aucune_reponse_ne_contient_la_cle(site, faux_client):
    """Cas : la clé est dans l'environnement du serveur.

    Risque : la clé recopiée dans une page, un fragment ou un en-tête.
    """
    faux_client.reponses = [reponse_texte("ok"), message([], "refusal")]
    reponses = [site.get("/"), site.get("/assistant/panneau", headers=HX), site.get("/assistant")]
    envoi = _envoyer(site)
    reponses.append(envoi)
    _attendre(site, _jeton(envoi))
    reponses.append(_envoyer(site, "Encore"))
    for reponse in reponses:
        assert CLE_FACTICE not in reponse.text and CLE_FACTICE not in str(reponse.headers)


# --------------------------------------------------------------------------- #
# Page sans JavaScript
# --------------------------------------------------------------------------- #


def test_page_sans_javascript(site, faux_client):
    faux_client.reponses = [reponse_texte("Réponse dans la page.")]
    faux_client.bloquer()
    envoi = site.post("/assistant/messages", data={"message": "Bonjour"}, follow_redirects=False)
    assert envoi.status_code == 303 and envoi.headers["location"] == "/assistant"
    assert faux_client.en_attente.wait(5)
    pendant = site.get("/assistant").text
    assert '<meta http-equiv="refresh" content="2">' in pendant
    assert "La conversation est affichée dans cette page." in unescape(pendant)
    assert pendant.count('id="assistant-fil"') == 1
    faux_client.liberer()
    fin = time.monotonic() + 5
    while "Réponse dans la page." not in site.get("/assistant").text:
        assert time.monotonic() < fin
        time.sleep(0.02)
    assert 'http-equiv="refresh"' not in site.get("/assistant").text


@pytest.mark.cas_limite
def test_le_cookie_de_conversation_est_renouvele_a_chaque_activite(site, faux_client):
    """Cas : une démonstration de plus de 30 minutes sur une même conversation.

    Risque : le cookie posé au premier message expire alors que la conversation vit encore
    sur le serveur : la réponse en cours est « perdue », Confirmer répond « Cette
    proposition n'existe plus », et le message suivant ouvre une autre conversation.
    """
    faux_client.reponses = [reponse_texte("un"), reponse_texte("deux")]
    premier = _envoyer(site)
    _attendre(site, _jeton(premier))
    for reponse in (_envoyer(site, "Encore"), site.get("/assistant/panneau", headers=HX)):
        cookie = reponse.headers.get("set-cookie", "")
        assert "edt_assistant=" in cookie and "Max-Age=1800" in cookie, cookie
        assert premier.cookies["edt_assistant"] in cookie
