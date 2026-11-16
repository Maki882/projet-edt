"""Boucle d'un échange avec le faux client : arrêts, outils, historique, bornes, arguments."""

from __future__ import annotations

import datetime as dt
import itertools
import json
import logging
import time

import anthropic
import pytest
from anthropic.types.beta import BetaMessage
from tests.outils.assistant.faux_client import (
    FauxClient,
    client_sdk_simule,
    corps_erreur,
    delai_depasse,
    erreur_api,
    message,
    outil,
    reflexion,
    repli,
    reponse_http,
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
# Schémas envoyés : limites documentées du mode strict
# --------------------------------------------------------------------------- #

# « Structured outputs », § Schema complexity limits et § JSON Schema limitations
# (platform.claude.com/docs/en/build-with-claude/structured-outputs). Les trois
# limites chiffrées portent sur le total de tous les schémas stricts d'une requête ;
# un outil sans `strict` n'y compte pas.
OUTILS_STRICTS_MAX = 20
PARAMETRES_FACULTATIFS_MAX = 24
PARAMETRES_A_UNION_MAX = 16
MOTS_REFUSES_EN_STRICT = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "maxItems",
        "uniqueItems",
        "contains",
        "minProperties",
        "maxProperties",
        "patternProperties",
        "oneOf",
        "not",
        "if",
        "then",
        "else",
    }
)
FORMATS_PRIS_EN_CHARGE = frozenset(
    {"date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid"}
)
MOTIFS_REFUSES = ("(?=", "(?!", "(?<=", "(?<!", "\\b", "\\B", *(f"\\{n}" for n in range(1, 10)))


def _sous_schemas(schema: dict):
    """Le schéma et tous ses sous-schémas : propriétés, éléments, branches, définitions."""
    yield schema
    for sous in schema.get("properties", {}).values():
        yield from _sous_schemas(sous)
    if isinstance(schema.get("items"), dict):
        yield from _sous_schemas(schema["items"])
    for cle in ("anyOf", "allOf"):
        for sous in schema.get(cle, []):
            yield from _sous_schemas(sous)
    for cle in ("$defs", "definitions"):
        for sous in schema.get(cle, {}).values():
            yield from _sous_schemas(sous)


def depassements_du_mode_strict(outils_envoyes: list[dict]) -> list[str]:
    """Limites documentées du mode strict que ces outils dépassent (liste vide : aucune)."""
    stricts = [outil for outil in outils_envoyes if outil.get("strict")]
    schemas = [(o["name"], s) for o in stricts for s in _sous_schemas(o["input_schema"])]
    unions = sum(1 for _, s in schemas if "anyOf" in s or isinstance(s.get("type"), list))
    facultatifs = sum(
        len(set(s.get("properties", {})) - set(s.get("required", [])))
        for _, s in schemas
        if s.get("type") == "object"
    )
    erreurs = []
    if len(stricts) > OUTILS_STRICTS_MAX:
        erreurs.append(f"{len(stricts)} outils stricts (limite {OUTILS_STRICTS_MAX})")
    if unions > PARAMETRES_A_UNION_MAX:
        erreurs.append(f"{unions} paramètres à union (limite {PARAMETRES_A_UNION_MAX})")
    if facultatifs > PARAMETRES_FACULTATIFS_MAX:
        erreurs.append(
            f"{facultatifs} paramètres facultatifs (limite {PARAMETRES_FACULTATIFS_MAX})"
        )
    for nom, s in schemas:
        if s.get("type") == "object" and s.get("additionalProperties") is not False:
            erreurs.append(f"{nom} : objet sans additionalProperties: false")
        if refuses := sorted(MOTS_REFUSES_EN_STRICT & set(s)):
            erreurs.append(f"{nom} : mots non pris en charge {refuses}")
        if s.get("minItems", 0) not in (0, 1):
            erreurs.append(f"{nom} : minItems {s['minItems']} (0 ou 1 seulement)")
        if any(isinstance(v, dict | list) for v in s.get("enum", [])):
            erreurs.append(f"{nom} : énumération de valeurs non simples")
        if "format" in s and s["format"] not in FORMATS_PRIS_EN_CHARGE:
            erreurs.append(f"{nom} : format {s['format']} non pris en charge")
        if any(motif in s.get("pattern", "") for motif in MOTIFS_REFUSES):
            erreurs.append(f"{nom} : motif {s['pattern']} non pris en charge")
        if "$ref" in s:
            erreurs.append(f"{nom} : $ref (récursif ou externe : refusé)")
    return erreurs


@pytest.mark.cas_limite
def test_schemas_envoyes_dans_les_limites_du_mode_strict():
    """Cas : la requête réelle part avec les douze outils de l'assistant.

    Risque : le service refuse toute requête en 400 (« Schemas contains too many parameters
    with union types (74 …) », limite 16) : l'assistant ne répond jamais, alors que tous les
    tests passent par le faux client, qui n'applique aucune de ces limites.
    """
    outils_envoyes = boucle.parametres_requete([], _config())["tools"]
    assert len(outils_envoyes) == 12
    assert depassements_du_mode_strict(outils_envoyes) == []


def test_le_controle_des_limites_detecte_un_depassement():
    """Le contrôle lui-même : un outil strict à dix-sept paramètres nullables est refusé."""
    trop = {
        "name": "trop",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                f"p{i}": {"anyOf": [{"type": "string"}, {"type": "null"}]} for i in range(17)
            },
            "required": [f"p{i}" for i in range(17)],
            "additionalProperties": False,
        },
    }
    assert depassements_du_mode_strict([trop]) == ["17 paramètres à union (limite 16)"]
    assert depassements_du_mode_strict([{**trop, "strict": False}]) == []


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
    assert arguments["tools"] == OUTILS and not any(o.get("strict") for o in OUTILS)
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
    assert faux.options == [{"timeout": 40.0, "max_retries": 0}]


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
        {"timeout": 40.0, "max_retries": 0},
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


@pytest.fixture
def sommeil_enregistre(monkeypatch):
    """Les attentes demandées par le SDK (`time.sleep`) sont enregistrées au lieu d'attendre."""
    attentes: list[float] = []
    monkeypatch.setattr(time, "sleep", attentes.append)
    return attentes


@pytest.mark.cas_limite
def test_une_attente_demandee_trop_longue_n_est_jamais_suivie(base, sommeil_enregistre):
    """Cas : le service répond 429 avec « retry-after: 3600 » (vrai SDK, service simulé).

    Risque : le SDK dort une heure avant de reprendre : l'échange reste « en cours » bien
    au-delà de son échéance, la conversation est bloquée, et deux échanges ainsi bloqués
    ferment l'assistant à tout le site.
    """
    client, requetes = client_sdk_simule(
        [reponse_http(429, corps_erreur("rate_limit_error"), {"retry-after": "3600"})] * 3
    )
    attentes: list[float] = []
    resultat = derouler(
        historique=[],
        texte="Bonjour",
        message_systeme=message_du_tour(AUJOURDHUI, None, ()),
        client=client,
        config=_config(),
        contexte_outils=_contexte(),
        budget=Budget(),
        horloge=lambda: 0.0,
        attendre=attentes.append,
    )
    assert resultat.issue is Issue.DEBIT
    assert len(requetes) == 1
    assert sommeil_enregistre == [] and attentes == []


@pytest.mark.cas_limite
def test_une_surcharge_est_reprise_une_fois_dans_l_echeance(base, sommeil_enregistre):
    """Cas : le service répond 529 (surcharge) puis répond normalement (vrai SDK simulé).

    Risque : sans reprise, une surcharge passagère devient une erreur ; avec la reprise du
    SDK, une attente non bornée par l'échéance.
    """
    succes = json.loads(reponse_texte("Bonjour.").model_dump_json())
    client, requetes = client_sdk_simule(
        [reponse_http(529, corps_erreur("overloaded_error")), reponse_http(200, succes)]
    )
    attentes: list[float] = []
    resultat = derouler(
        historique=[],
        texte="Bonjour",
        message_systeme=message_du_tour(AUJOURDHUI, None, ()),
        client=client,
        config=_config(),
        contexte_outils=_contexte(),
        budget=Budget(),
        horloge=lambda: 0.0,
        attendre=attentes.append,
    )
    assert resultat.reussi and resultat.texte == "Bonjour."
    assert len(requetes) == 2 and attentes == [1.0] and sommeil_enregistre == []


def test_attente_avant_reprise():
    assert (
        boucle.attente_avant_reprise(
            erreur_api(anthropic.RateLimitError, 429, entetes={"retry-after": "5"})
        )
        == 5.0
    )
    assert (
        boucle.attente_avant_reprise(
            erreur_api(anthropic.RateLimitError, 429, entetes={"retry-after-ms": "1500"})
        )
        == 1.5
    )
    assert (
        boucle.attente_avant_reprise(
            erreur_api(anthropic.OverloadedError, 529, entetes={"retry-after": "60"})
        )
        is None
    )
    assert boucle.attente_avant_reprise(erreur_api(anthropic.BadRequestError, 400)) is None
    assert boucle.attente_avant_reprise(delai_depasse()) == 1.0


@pytest.mark.cas_limite
def test_un_appel_hors_delai_est_compte_au_cout_maximal(base):
    """Cas : un appel dépasse son délai (la génération était lancée), sa reprise aussi.

    Risque : appels comptés comme gratuits alors qu'ils ont pu être facturés : la dépense
    réelle du jour dépasse le budget sans qu'aucune limite ne joue.
    """
    budget = Budget()
    resultat = derouler(
        historique=[],
        texte="Bonjour",
        message_systeme=message_du_tour(AUJOURDHUI, None, ()),
        client=FauxClient([delai_depasse(), delai_depasse()]),
        config=_config(),
        contexte_outils=_contexte(),
        budget=budget,
        horloge=lambda: 0.0,
        attendre=lambda secondes: None,
    )
    assert resultat.issue is Issue.DELAI
    consommation = budget.consommation()
    assert consommation.appels == 2 and consommation.cout > 0


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
