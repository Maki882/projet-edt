"""Boucle d'un échange avec le faux client : arrêts, outils, historique, bornes, arguments."""

from __future__ import annotations

import datetime as dt
import itertools
import logging

import pytest
from anthropic.types.beta import BetaMessage
from tests.outils.assistant.faux_client import (
    FauxClient,
    message,
    outil,
    reflexion,
    repli,
    reponse_outil,
    reponse_texte,
    texte,
)

from edt.services.assistant import boucle
from edt.services.assistant.boucle import Issue, blocs_a_renvoyer, derouler, message_du_tour
from edt.services.assistant.configuration import charger_configuration_assistant
from edt.services.assistant.consignes import CONSIGNES
from edt.services.assistant.limites import Budget
from edt.services.assistant.outils import OUTILS, ContexteOutil

AUJOURDHUI = dt.date(2026, 10, 5)


def _config(**variables):
    return charger_configuration_assistant(
        {"ANTHROPIC_API_KEY": "factice", **variables}, version=lambda: "2.8.0"
    )


def _contexte() -> ContexteOutil:
    compteur = itertools.count(1)
    return ContexteOutil("as-test", AUJOURDHUI, lambda: next(compteur))


def _derouler(faux, *, historique=(), config=None, horloge=None, contexte=None, **options):
    etapes: list[str] = []
    resultat = derouler(
        historique=list(historique),
        texte="Bonjour",
        message_systeme=message_du_tour(AUJOURDHUI, "/salles", ()),
        client=faux,
        config=config or _config(),
        contexte_outils=contexte or _contexte(),
        budget=Budget(),
        signaler=etapes.append,
        horloge=horloge or (lambda: 0.0),
        **options,
    )
    return resultat, etapes


@pytest.fixture(autouse=True)
def _pause_levee():
    boucle.lever_la_pause()
    yield
    boucle.lever_la_pause()


# --------------------------------------------------------------------------- #
# Arrêts du modèle
# --------------------------------------------------------------------------- #


def test_reponse_directe_et_arguments_envoyes(base):
    faux = FauxClient([reponse_texte("Bonjour, que puis-je faire ?")])
    resultat, _ = _derouler(faux)
    assert resultat.reussi and resultat.texte == "Bonjour, que puis-je faire ?"
    assert resultat.appels == 1 and resultat.modeles == ["claude-opus-5-5"]
    (arguments,) = faux.appels
    assert arguments["model"] == "claude-opus-5-5"
    assert arguments["output_config"] == {"effort": "low"}
    assert arguments["betas"] == ["server-side-fallback-2026-07-01"]
    assert arguments["fallbacks"] == "default"
    assert arguments["cache_control"] == {"type": "ephemeral"}
    assert arguments["system"] == [
        {"type": "text", "text": CONSIGNES, "cache_control": {"type": "ephemeral"}}
    ]
    assert arguments["tools"] == OUTILS and all(o["strict"] for o in arguments["tools"])
    assert arguments["max_tokens"] == 4096
    for interdit in ("thinking", "tool_choice", "temperature", "top_p", "top_k"):
        assert interdit not in arguments
    utilisateur, systeme = arguments["messages"]
    assert utilisateur == {"role": "user", "content": [{"type": "text", "text": "Bonjour"}]}
    assert (
        systeme["role"] == "system"
        and "Page affichée par l'utilisateur : /salles" in systeme["content"]
    )
    assert resultat.messages[-1]["role"] == "assistant"
    assert faux.options == [{"timeout": 40.0, "max_retries": 1}]


def test_outil_puis_reponse_et_historique_en_ajout_seul(base):
    premiere = reponse_outil("lire_contexte", {})
    faux = FauxClient([premiere, reponse_texte("Nous sommes en 2026-2027.")])
    resultat, etapes = _derouler(faux)
    assert resultat.reussi and resultat.outils == ["lire_contexte"] and resultat.appels == 2
    premier, second = (a["messages"] for a in faux.appels)
    assert second[: len(premier)] == premier  # ajout seul : aucun message réécrit
    assistant, resultats = second[len(premier) :]
    assert assistant == {"role": "assistant", "content": list(premiere.content)}
    assert assistant["content"][0].type == "thinking"  # réflexion renvoyée telle quelle
    (bloc,) = resultats["content"]
    assert bloc["type"] == "tool_result" and bloc["tool_use_id"] == premiere.content[1].id
    assert "Année scolaire en cours" in bloc["content"] and "is_error" not in bloc
    assert "Je consulte le calendrier et les réglages…" in etapes
    assert etapes[-1] == "Je rédige la réponse…"
    assert resultat.messages[: len(second)] == second


def test_plusieurs_outils_dans_une_reponse_un_seul_message_de_resultats(base):
    appels = message(
        [
            reflexion(),
            outil("lire_contexte", {}, "toolu_a"),
            outil("lister_regles_inconnu", {}, "toolu_b"),
        ],
        "tool_use",
    )
    faux = FauxClient([appels, reponse_texte("Voilà.")])
    resultat, _ = _derouler(faux)
    assert resultat.reussi
    resultats = faux.appels[1]["messages"][-1]
    assert resultats["role"] == "user"
    assert [b["tool_use_id"] for b in resultats["content"]] == ["toolu_a", "toolu_b"]
    assert (
        resultats["content"][1]["is_error"]
        and "Outil inconnu" in resultats["content"][1]["content"]
    )


def test_tour_mis_en_pause_par_le_service_puis_repris(base):
    pause = message([texte("Je continue…")], "pause_turn")
    faux = FauxClient([pause, reponse_texte("Terminé.")])
    resultat, _ = _derouler(faux)
    assert resultat.reussi and resultat.appels == 2
    assert faux.appels[1]["messages"][-1] == {"role": "assistant", "content": list(pause.content)}


def test_refus_du_modele_rien_n_est_valide(base, caplog):
    refus = message(
        [], "refusal", stop_details={"type": "refusal", "category": "cyber", "explanation": None}
    )
    with caplog.at_level(logging.WARNING):
        resultat, _ = _derouler(FauxClient([refus]))
    assert resultat.issue is Issue.REFUS and resultat.messages == []
    assert resultat.message.startswith("Je ne peux pas traiter cette demande.")
    assert "catégorie cyber" in caplog.text


@pytest.mark.cas_limite
def test_reponse_tronquee_avec_appel_d_outil_jamais_execute(base, monkeypatch):
    """Cas : la réponse s'arrête à la limite de jetons au milieu d'un appel d'outil.

    Risque : exécuter un outil aux arguments coupés (une proposition incomplète présentée).
    """
    executes = []
    monkeypatch.setattr(boucle, "executer_outil", lambda *a: executes.append(a) or {})
    tronquee = message([reflexion(), outil("proposer_regle", {"type": "CRENEAUX"})], "max_tokens")
    resultat, _ = _derouler(FauxClient([tronquee]))
    assert resultat.issue is Issue.TRONQUEE and executes == [] and resultat.messages == []
    assert resultat.message == "Ma réponse a été coupée : posez une question plus précise."


def test_arret_inattendu_journalise_en_erreur(base, caplog):
    with caplog.at_level(logging.ERROR):
        resultat, _ = _derouler(
            FauxClient([message([texte("…")], "model_context_window_exceeded")])
        )
    assert resultat.issue is Issue.ARRET_INCONNU and "Arrêt inattendu" in caplog.text


@pytest.mark.cas_limite
def test_trop_d_appels_arrete_l_echange(base):
    """Cas : le modèle appelle des outils sans jamais conclure.

    Risque : une boucle sans fin qui consomme le budget du jour.
    """
    faux = FauxClient([reponse_outil("lire_contexte", {}) for _ in range(3)])
    resultat, _ = _derouler(faux, config=_config(EDT_ASSISTANT_APPELS_MAX="3"))
    assert resultat.issue is Issue.TROP_D_APPELS and len(faux.appels) == 3
    assert resultat.message == "Je n'ai pas pu terminer en 3 étapes : reformulez plus précisément."


@pytest.mark.cas_limite
def test_echeance_de_l_echange_jamais_depassee(base):
    """Cas : chaque appel prend presque tout le délai.

    Risque : un échange qui dure plusieurs minutes, au-delà du délai du serveur web ; ici
    l'échange s'arrête avant l'appel qui dépasserait l'échéance.
    """
    instants = iter([0.0, 0.0, 88.0])
    faux = FauxClient([reponse_outil("lire_contexte", {}), reponse_texte("…")])
    resultat, _ = _derouler(faux, horloge=lambda: next(instants))
    assert resultat.issue is Issue.DELAI and len(faux.appels) == 1
    instants = iter([0.0, 0.0, 70.0])
    faux = FauxClient([reponse_outil("lire_contexte", {}), reponse_texte("Fin.")])
    resultat, _ = _derouler(faux, horloge=lambda: next(instants))
    assert resultat.reussi
    # Il restait 20 s : délai d'appel ramené à 20 s, sans reprise (elle ne tiendrait pas).
    assert faux.options == [
        {"timeout": 40.0, "max_retries": 1},
        {"timeout": 20.0, "max_retries": 0},
    ]


@pytest.mark.cas_limite
def test_contexte_trop_long_arrete_avant_l_appel(base):
    """Cas : la conversation dépasse la taille de contexte permise.

    Risque : un appel très coûteux ; ici aucun appel, et la conversation est close.
    """
    faux = FauxClient([])
    resultat, _ = _derouler(faux, config=_config(EDT_ASSISTANT_CONTEXTE_MAX="5000"))
    assert resultat.issue is Issue.CONTEXTE and faux.appels == []
    assert Issue.CONTEXTE in boucle.CLOT_LA_CONVERSATION


@pytest.mark.cas_limite
def test_budget_epuise_aucun_appel(base):
    """Cas : budget du jour à 0 $.

    Risque : dépense au-delà du plafond fixé par l'administrateur.
    """
    faux = FauxClient([])
    resultat, _ = _derouler(faux, config=_config(EDT_ASSISTANT_BUDGET_JOUR="0"))
    assert resultat.issue is Issue.LIMITE and faux.appels == []
    assert "limite du jour" in resultat.message


@pytest.mark.cas_limite
def test_estimation_prudente_pour_un_texte_non_ascii():
    """Cas : un texte fait de caractères non latins (« 数 »), qui comptent environ un jeton
    chacun, bien plus que les 3 caractères par jeton du français.

    Risque : réservation du budget et contrôle de la taille du contexte sous-estimés : un
    appel bien plus cher que prévu, et un budget du jour qui n'est plus borné.
    """
    texte = "数" * 3000
    estimes = boucle.estimer_jetons([{"role": "user", "content": texte}], 1000, 0)
    assert estimes >= 1000 + 3000
    francais = boucle.estimer_jetons([{"role": "user", "content": "a" * 3000}], 1000, 0)
    assert francais <= 1000 + 1100


def test_historique_d_une_conversation_prefixe_exact(base):
    faux = FauxClient([reponse_texte("Première réponse."), reponse_texte("Seconde réponse.")])
    premier, _ = _derouler(faux)
    second, _ = _derouler(faux, historique=premier.messages, taille_connue=premier.taille_contexte)
    assert second.reussi
    envoyes = faux.appels[1]["messages"]
    assert envoyes[: len(premier.messages)] == premier.messages
    assert premier.taille_contexte == 150  # 100 jetons d'entrée et 50 de sortie


def test_reponse_sans_texte_signalee(base, caplog):
    with caplog.at_level(logging.WARNING):
        resultat, _ = _derouler(FauxClient([message([reflexion()], "end_turn")]))
    assert resultat.reussi and resultat.texte == "" and "sans texte" in caplog.text


# --------------------------------------------------------------------------- #
# Repli
# --------------------------------------------------------------------------- #


def test_blocs_renvoyes_apres_un_repli():
    reponse = message(
        [
            reflexion("avant"),
            outil("lire_contexte", {}),
            repli(),
            reflexion("apres"),
            texte("Fin."),
        ],
        "end_turn",
    )
    blocs = blocs_a_renvoyer(reponse.content)
    assert [b.type for b in blocs] == ["fallback", "thinking", "text"]
    assert blocs[1].signature == "apres"
    sans_repli = message([reflexion(), texte("ok")], "end_turn")
    assert blocs_a_renvoyer(sans_repli.content) == list(sans_repli.content)


def test_repli_serveur_signale(base):
    reponse = message(
        [texte("Réponse du modèle de repli.")],
        "end_turn",
        modele="claude-opus-5",
        iterations=[
            {
                "type": "message",
                "input_tokens": 10,
                "output_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "model": "claude-opus-5-5",
            },
            {
                "type": "fallback_message",
                "input_tokens": 10,
                "output_tokens": 20,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "model": "claude-opus-5",
            },
        ],
    )
    assert isinstance(reponse, BetaMessage)
    resultat, _ = _derouler(FauxClient([reponse]))
    assert resultat.reussi and resultat.repli and resultat.modeles == ["claude-opus-5"]
    # 10 × 4 $ + 10 × 5 $ + 20 × 25 $ par million de jetons, en micro-dollars.
    assert resultat.consommation.cout == 40 + 50 + 500
