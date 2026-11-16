"""Déroulement d'un échange : appels au modèle, outils, erreurs du service.

Boucle écrite à la main (et non le « tool runner » du SDK) pour garder la
main sur ce qui borne la dépense et la durée : le budget est réservé avant
chaque appel, l'échéance de l'échange est vérifiée entre deux appels (une
reprise du SDK n'est accordée que si elle tient avant), l'étape en cours est
affichée, et l'échange n'est validé que s'il se termine normalement
(`end_turn`).

Règles de l'API suivies :

- `stop_reason` est lu avant le contenu ; un `tool_use` d'une réponse
  tronquée (`max_tokens`) ou refusée (`refusal`) n'est jamais exécuté ;
- le contenu de chaque réponse est renvoyé tel quel dans l'historique
  (réflexion comprise : le modèle lie ses blocs de réflexion à la
  conversation), sauf après un repli : les blocs de réflexion et d'appel
  d'outil situés avant le dernier bloc `fallback` sont retirés, le bloc
  `fallback` reste à sa place (`blocs_a_renvoyer`) ;
- tous les résultats d'outils d'une réponse partent dans un seul message,
  chacun avec l'identifiant de son appel ;
- l'historique est en ajout seul : un échange qui n'aboutit pas ne laisse
  aucune trace dans la conversation ;
- ni `thinking` (réflexion toujours active), ni `tool_choice` (forcer un
  outil est refusé par ce modèle), ni `temperature` ; outils stricts, effort
  réglable, cache explicite sur les consignes et automatique en fin de
  conversation, repli serveur par défaut en cas de refus.

Les erreurs du service sont traduites en `Issue` (matrice de
`docs/assistant.md`), du plus précis au plus général. Une clé refusée, un
défaut de facturation ou un modèle introuvable mettent l'assistant en pause
quelques minutes (en mémoire) : inutile de les redemander à chaque message.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from edt.services.assistant import temps
from edt.services.assistant.configuration import ConfigurationAssistant
from edt.services.assistant.consignes import CONSIGNES
from edt.services.assistant.limites import (
    Budget,
    Compteurs,
    LimiteAtteinte,
    cout_maximal_appel,
    cout_reel,
)
from edt.services.assistant.outils import OUTILS, OUTILS_JSON, ContexteOutil, etape, executer_outil
from edt.services.emploi_du_temps.presentation import date_longue

journal = logging.getLogger(__name__)

BETA_REPLI = "server-side-fallback-2026-07-01"
MARGE_ECHEANCE_S = 5
CARACTERES_PAR_JETON = 3
"""Estimation prudente (le français compte plutôt 3,5 à 4 caractères par jeton) : elle sert
à réserver le budget et à refuser un contexte trop long avant l'appel, jamais à facturer."""
TAILLE_PREFIXE_ESTIMEE = math.ceil((len(CONSIGNES) + len(OUTILS_JSON)) / CARACTERES_PAR_JETON)


class Issue(StrEnum):
    """Comment un échange s'est terminé."""

    SUCCES = "succes"
    IDENTIFIANT = "identifiant"
    CLE_REFUSEE = "cle_refusee"
    FACTURATION = "facturation"
    MODELE_INTROUVABLE = "modele_introuvable"
    REQUETE_INVALIDE = "requete_invalide"
    TROP_LONGUE = "trop_longue"
    DEBIT = "debit"
    SURCHARGE = "surcharge"
    DELAI = "delai"
    RESEAU = "reseau"
    REFUS = "refus"
    TRONQUEE = "tronquee"
    ARRET_INCONNU = "arret_inconnu"
    TROP_D_APPELS = "trop_d_appels"
    CONTEXTE = "contexte"
    LIMITE = "limite"
    INTERNE = "interne"


INDISPONIBLE = "L'assistant est momentanément indisponible ; le reste de l'application fonctionne."

MESSAGES: dict[Issue, str] = {
    Issue.IDENTIFIANT: "L'assistant est indisponible : sa configuration est à corriger par "
    "l'administrateur.",
    Issue.CLE_REFUSEE: INDISPONIBLE,
    Issue.FACTURATION: INDISPONIBLE,
    Issue.MODELE_INTROUVABLE: INDISPONIBLE,
    Issue.REQUETE_INVALIDE: "Erreur interne de l'assistant.",
    Issue.TROP_LONGUE: "Conversation trop longue : commencez-en une nouvelle.",
    Issue.DEBIT: "L'assistant est très sollicité : réessayez dans une minute.",
    Issue.SURCHARGE: "Le service de l'assistant est surchargé : réessayez dans un instant.",
    Issue.DELAI: "La réponse prend trop de temps : réessayez avec une demande plus simple.",
    Issue.RESEAU: "Service injoignable : réessayez dans un instant.",
    Issue.REFUS: "Je ne peux pas traiter cette demande. Reformulez-la en restant sur l'emploi "
    "du temps.",
    Issue.TRONQUEE: "Ma réponse a été coupée : posez une question plus précise.",
    Issue.ARRET_INCONNU: "Erreur interne de l'assistant.",
    Issue.TROP_D_APPELS: "Je n'ai pas pu terminer en {appels} étapes : reformulez plus "
    "précisément.",
    Issue.CONTEXTE: "Cette conversation est trop longue : commencez-en une nouvelle.",
    Issue.INTERNE: "Erreur interne de l'assistant.",
}
"""Message affiché pour chaque issue en échec (une limite atteinte porte son propre message)."""

AVEC_REFERENCE = frozenset({Issue.REQUETE_INVALIDE, Issue.ARRET_INCONNU, Issue.INTERNE})
"""Issues dont le message cite la référence de l'échange (à communiquer à l'administrateur)."""

PAUSES_S = {Issue.CLE_REFUSEE: 600, Issue.FACTURATION: 3600, Issue.MODELE_INTROUVABLE: 600}
"""Pause de l'assistant après ces issues, en secondes."""

CLOT_LA_CONVERSATION = frozenset({Issue.TROP_LONGUE, Issue.CONTEXTE})
"""Issues après lesquelles la conversation ne peut plus continuer."""


# --------------------------------------------------------------------------- #
# Pause après une panne de configuration côté service
# --------------------------------------------------------------------------- #

_pause: dict[str, Any] = {"jusqua": 0.0, "issue": None}
_verrou_pause = threading.Lock()


def en_pause() -> Issue | None:
    """L'issue qui a mis l'assistant en pause, tant que la pause dure ; sinon `None`."""
    with _verrou_pause:
        if _pause["issue"] is not None and temps.maintenant() < _pause["jusqua"]:
            return _pause["issue"]
        return None


def _mettre_en_pause(issue: Issue) -> None:
    with _verrou_pause:
        _pause["issue"], _pause["jusqua"] = issue, temps.maintenant() + PAUSES_S[issue]


def lever_la_pause() -> None:
    """Oublie une pause en cours (redémarrage, tests)."""
    with _verrou_pause:
        _pause["issue"], _pause["jusqua"] = None, 0.0


# --------------------------------------------------------------------------- #
# Requête
# --------------------------------------------------------------------------- #


def parametres_requete(
    messages: Sequence[Any], config: ConfigurationAssistant, *, jetons_sortie: int | None = None
) -> dict[str, Any]:
    """Arguments de `beta.messages.create` : tous fixes pour une configuration, sauf `messages`."""
    return {
        "model": config.modele,
        "max_tokens": jetons_sortie or config.jetons_sortie_max,
        "system": [{"type": "text", "text": CONSIGNES, "cache_control": {"type": "ephemeral"}}],
        "tools": OUTILS,
        "messages": list(messages),
        "output_config": {"effort": config.effort},
        "cache_control": {"type": "ephemeral"},
        "betas": [BETA_REPLI],
        "fallbacks": "default",
    }


def message_du_tour(aujourdhui: dt.date, page: str | None, suites: Sequence[str]) -> dict[str, Any]:
    """Message système du tour : date du jour, page affichée, suites données aux propositions."""
    lignes = [f"Date du jour : {date_longue(aujourdhui)}."]
    if page:
        lignes.append(f"Page affichée par l'utilisateur : {page}")
    if suites:
        lignes.append(
            "Suites données aux propositions depuis le dernier échange : "
            + " ; ".join(suites)
            + "."
        )
    return {"role": "system", "content": "\n".join(lignes)}


def blocs_a_renvoyer(contenu: Sequence[Any]) -> list[Any]:
    """Contenu d'une réponse à remettre dans l'historique (voir l'en-tête du module)."""
    blocs = list(contenu)
    reperes = [i for i, bloc in enumerate(blocs) if getattr(bloc, "type", None) == "fallback"]
    if not reperes:
        return blocs
    dernier = reperes[-1]
    retires = {"thinking", "redacted_thinking", "tool_use", "server_tool_use"}
    return [
        bloc
        for i, bloc in enumerate(blocs)
        if i >= dernier or getattr(bloc, "type", None) not in retires
    ]


def _taille(objet: Any) -> int:
    """Caractères d'un message une fois sérialisé (estimation de la taille du contexte)."""

    def simple(valeur: Any) -> Any:
        if hasattr(valeur, "model_dump"):
            return valeur.model_dump()
        return str(valeur)

    return len(json.dumps(objet, ensure_ascii=False, default=simple))


def estimer_jetons(messages: Sequence[Any], connus: int, depuis: int) -> int:
    """Jetons d'entrée estimés : ceux du dernier appel connu, plus les messages ajoutés depuis."""
    base = connus or TAILLE_PREFIXE_ESTIMEE
    ajoutes = sum(_taille(message) for message in messages[depuis:])
    return base + math.ceil(ajoutes / CARACTERES_PAR_JETON)


# --------------------------------------------------------------------------- #
# Déroulement
# --------------------------------------------------------------------------- #


@dataclass
class ResultatBoucle:
    """Ce que l'échange a produit : issue, historique à valider, texte, consommation."""

    issue: Issue
    messages: list[Any] = field(default_factory=list)
    texte: str = ""
    message: str = ""
    appels: int = 0
    outils: list[str] = field(default_factory=list)
    consommation: Compteurs = field(default_factory=Compteurs)
    modeles: list[str] = field(default_factory=list)
    taille_contexte: int = 0
    repli: bool = False

    @property
    def reussi(self) -> bool:
        """Vrai si l'échange s'est terminé normalement."""
        return self.issue is Issue.SUCCES


def derouler(
    *,
    historique: Sequence[Any],
    texte: str,
    message_systeme: dict[str, Any],
    client: Any,
    config: ConfigurationAssistant,
    contexte_outils: ContexteOutil,
    budget: Budget,
    taille_connue: int = 0,
    signaler: Callable[[str], None] = lambda etape_: None,
    horloge: Callable[[], float] = time.monotonic,
    jetons_sortie: int | None = None,
) -> ResultatBoucle:
    """Déroule un échange (voir l'en-tête) ; ne lève que sur une erreur de programmation.

    `historique` n'est jamais modifié : en cas de succès, `ResultatBoucle.messages`
    est le nouvel historique complet, dont `historique` est un préfixe exact.
    """
    messages = list(historique)
    depuis = len(messages)
    messages.append({"role": "user", "content": [{"type": "text", "text": texte}]})
    messages.append(message_systeme)
    resultat = ResultatBoucle(Issue.INTERNE)
    echeance = horloge() + config.delai_s
    connus = taille_connue
    for numero in range(1, config.appels_max + 1):
        restant = echeance - horloge()
        if restant < MARGE_ECHEANCE_S:
            journal.warning("Échéance de %d s atteinte avant l'appel %d", config.delai_s, numero)
            return _echec(resultat, Issue.DELAI)
        estimes = estimer_jetons(messages, connus, depuis)
        if estimes > config.contexte_max:
            journal.info(
                "Contexte estimé à %d jetons (limite %d) : conversation close",
                estimes,
                config.contexte_max,
            )
            return _echec(resultat, Issue.CONTEXTE)
        try:
            reservation = budget.reserver(cout_maximal_appel(estimes, config), config)
        except LimiteAtteinte as limite:
            resultat.message = limite.message
            return _echec(resultat, Issue.LIMITE, message=limite.message)
        delai = min(float(config.delai_appel_s), restant)
        reprises = 1 if restant >= 2 * delai + MARGE_ECHEANCE_S else 0
        signaler("Je lis votre demande…" if numero == 1 else "Je rédige la réponse…")
        debut = time.monotonic()
        try:
            reponse = client.with_options(timeout=delai, max_retries=reprises).beta.messages.create(
                **parametres_requete(messages, config, jetons_sortie=jetons_sortie)
            )
        except Exception as erreur:  # traduite par issue_de, qui relance ce qui n'est pas du SDK
            budget.annuler(reservation)
            issue = issue_de(erreur, delai)
            if issue in PAUSES_S:
                _mettre_en_pause(issue)
            return _echec(resultat, issue)
        resultat.appels = numero
        consommation = cout_reel(reponse.usage, reponse.model)
        budget.solder(reservation, consommation)
        resultat.consommation.ajouter(consommation)
        if reponse.model not in resultat.modeles:
            resultat.modeles.append(reponse.model)
        iterations = getattr(reponse.usage, "iterations", None) or []
        if any(getattr(i, "type", None) == "fallback_message" for i in iterations):
            resultat.repli = True
        usage = reponse.usage
        connus = (
            usage.input_tokens
            + (usage.cache_read_input_tokens or 0)
            + (usage.cache_creation_input_tokens or 0)
            + usage.output_tokens
        )
        resultat.taille_contexte = connus
        journal.debug(
            "Appel %d : arrêt %s en %.2f s, requête %s, modèle %s, jetons entrée %d, cache lu %d, "
            "cache écrit %d, sortie %d%s",
            numero,
            reponse.stop_reason,
            time.monotonic() - debut,
            getattr(reponse, "_request_id", None),
            reponse.model,
            usage.input_tokens,
            usage.cache_read_input_tokens or 0,
            usage.cache_creation_input_tokens or 0,
            usage.output_tokens,
            " (repli)" if resultat.repli else "",
        )
        arret = reponse.stop_reason
        if arret == "end_turn":
            blocs = blocs_a_renvoyer(reponse.content)
            messages.append({"role": "assistant", "content": blocs})
            depuis = len(messages)
            resultat.issue, resultat.messages = Issue.SUCCES, messages
            resultat.texte = "\n\n".join(
                b.text for b in blocs if getattr(b, "type", None) == "text" and b.text.strip()
            )
            if not resultat.texte:
                journal.warning("Réponse terminée sans texte (appel %d)", numero)
            return resultat
        if arret == "tool_use":
            blocs = blocs_a_renvoyer(reponse.content)
            messages.append({"role": "assistant", "content": blocs})
            appels_outils = [b for b in blocs if getattr(b, "type", None) == "tool_use"]
            if not appels_outils:
                journal.error("Arrêt tool_use sans appel d'outil (appel %d)", numero)
                return _echec(resultat, Issue.ARRET_INCONNU)
            resultats = []
            for bloc in appels_outils:
                signaler(etape(bloc.name, bloc.input if isinstance(bloc.input, dict) else {}))
                resultat.outils.append(bloc.name)
                resultats.append(executer_outil(bloc.name, bloc.id, bloc.input, contexte_outils))
            messages.append({"role": "user", "content": resultats})
            depuis = len(messages) - 1  # la réponse est comptée dans les jetons de sortie
            continue
        if arret == "pause_turn":
            journal.debug("Appel %d : tour mis en pause par le service, reprise", numero)
            messages.append({"role": "assistant", "content": list(reponse.content)})
            depuis = len(messages)
            continue
        if arret == "refusal":
            details = getattr(reponse, "stop_details", None)
            journal.warning(
                "Demande refusée par le modèle (catégorie %s)", getattr(details, "category", None)
            )
            return _echec(resultat, Issue.REFUS)
        if arret == "max_tokens":
            journal.warning(
                "Réponse coupée à %d jetons de sortie (appel %d)", config.jetons_sortie_max, numero
            )
            return _echec(resultat, Issue.TRONQUEE)
        journal.error("Arrêt inattendu du modèle : %r (appel %d)", arret, numero)
        return _echec(resultat, Issue.ARRET_INCONNU)
    journal.warning("Échange arrêté après %d appels sans réponse finale", config.appels_max)
    return _echec(
        resultat,
        Issue.TROP_D_APPELS,
        message=MESSAGES[Issue.TROP_D_APPELS].format(appels=config.appels_max),
    )


def _echec(resultat: ResultatBoucle, issue: Issue, *, message: str | None = None) -> ResultatBoucle:
    resultat.issue = issue
    resultat.messages = []
    resultat.message = message or MESSAGES.get(issue, MESSAGES[Issue.INTERNE])
    return resultat


def issue_de(erreur: Exception, delai: float) -> Issue:
    """Issue d'une exception du SDK, de la plus précise à la plus générale ; relance les autres."""
    import anthropic

    identifiant = getattr(erreur, "request_id", None)
    if isinstance(erreur, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        journal.error(
            "Clé refusée par le service (statut %s, requête %s) : assistant en pause %d min",
            erreur.status_code,
            identifiant,
            PAUSES_S[Issue.CLE_REFUSEE] // 60,
        )
        return Issue.CLE_REFUSEE
    if isinstance(erreur, anthropic.NotFoundError):
        journal.error(
            "Modèle inconnu ou non autorisé pour ce compte (EDT_ASSISTANT_MODELE, requête %s) : %s",
            identifiant,
            erreur.message,
        )
        return Issue.MODELE_INTROUVABLE
    if isinstance(erreur, anthropic.BadRequestError):
        journal.error(
            "Requête refusée par le service (400, requête %s) : %s", identifiant, erreur.message
        )
        if "bound to a different conversation" in str(erreur.message):
            return Issue.TROP_LONGUE
        return Issue.REQUETE_INVALIDE
    if isinstance(erreur, anthropic.RateLimitError):
        journal.warning(
            "Débit limité par le service (429, requête %s, retry-after %s)",
            identifiant,
            erreur.response.headers.get("retry-after"),
        )
        return Issue.DEBIT
    if isinstance(erreur, anthropic.InternalServerError):
        journal.warning(
            "Service surchargé ou en panne (statut %s, requête %s)", erreur.status_code, identifiant
        )
        return Issue.SURCHARGE
    if isinstance(erreur, anthropic.APIStatusError):
        if erreur.status_code == 402:
            journal.error(
                "Facturation refusée par le service (402, requête %s) : assistant en pause %d min",
                identifiant,
                PAUSES_S[Issue.FACTURATION] // 60,
            )
            return Issue.FACTURATION
        if erreur.status_code == 413:
            journal.warning("Requête trop volumineuse (413, requête %s)", identifiant)
            return Issue.TROP_LONGUE
        journal.error(
            "Erreur du service (statut %s, requête %s) : %s",
            erreur.status_code,
            identifiant,
            erreur.message,
        )
        return Issue.INTERNE
    if isinstance(erreur, anthropic.APITimeoutError):
        journal.warning("Délai de %.0f s dépassé pour un appel au service", delai)
        return Issue.DELAI
    if isinstance(erreur, anthropic.APIConnectionError):
        journal.warning("Service injoignable : %s", type(erreur).__name__)
        return Issue.RESEAU
    if isinstance(erreur, anthropic.CredentialsError):
        journal.error("Identifiant du service introuvable par le SDK (%s)", type(erreur).__name__)
        return Issue.IDENTIFIANT
    if isinstance(erreur, anthropic.AnthropicError):
        journal.error("Erreur du SDK : %s", type(erreur).__name__)
        return Issue.INTERNE
    raise erreur
