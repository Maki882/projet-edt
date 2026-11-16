"""Faux client du SDK : rejoue des réponses écrites d'avance, enregistre les arguments reçus.

Aucun test n'appelle le service : `FauxClient` imite la seule méthode que
l'assistant utilise (`with_options(...).beta.messages.create(**arguments)`),
et rend de vrais objets du SDK (`BetaMessage`) ou lève de vraies exceptions
du SDK. Aides :

- `message(contenu, arret, **usage)` : une réponse du modèle ;
- `texte(...)`, `outil(...)`, `reflexion(...)`, `repli(...)` : des blocs ;
- `reponse_texte("…")`, `reponse_outil("nom", {...})` : réponses courantes ;
- `erreur_api(classe, statut)`, `delai_depasse()`, `reseau_coupe()` : des pannes.

`FauxClient.bloquer()` retient la réponse suivante jusqu'à `liberer()` :
de quoi observer un échange « en cours ».
"""

from __future__ import annotations

import copy
import itertools
import threading
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
from anthropic.types.beta import BetaMessage

_identifiants = itertools.count(1)
REQUETE = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def message(contenu: list[dict[str, Any]], arret: str | None, **usage: Any) -> BetaMessage:
    """Réponse du modèle (`BetaMessage` validé par le SDK)."""
    return BetaMessage.model_validate(
        {
            "id": f"msg_test_{next(_identifiants)}",
            "type": "message",
            "role": "assistant",
            "model": usage.pop("modele", "claude-opus-5-5"),
            "content": contenu,
            "stop_reason": arret,
            "stop_sequence": None,
            "stop_details": usage.pop("stop_details", None),
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                **usage,
            },
        }
    )


def texte(contenu: str) -> dict[str, Any]:
    return {"type": "text", "text": contenu, "citations": None}


def reflexion(signature: str = "signature-test") -> dict[str, Any]:
    return {"type": "thinking", "thinking": "", "signature": signature}


def outil(nom: str, entree: dict[str, Any], identifiant: str | None = None) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": identifiant or f"toolu_test_{next(_identifiants)}",
        "name": nom,
        "input": entree,
    }


def repli(de: str = "claude-opus-5-5", vers: str = "claude-opus-5") -> dict[str, Any]:
    return {
        "type": "fallback",
        "from": {"model": de},
        "to": {"model": vers},
        "trigger": {"type": "refusal", "category": None},
    }


def reponse_texte(contenu: str, **usage: Any) -> BetaMessage:
    return message([reflexion(), texte(contenu)], "end_turn", **usage)


def reponse_outil(nom: str, entree: dict[str, Any], **usage: Any) -> BetaMessage:
    return message([reflexion(), outil(nom, entree)], "tool_use", **usage)


def erreur_api(
    classe: type[anthropic.APIStatusError], statut: int, message_api: str = "message de test"
):
    """Exception d'état HTTP du SDK, avec un identifiant de requête."""
    reponse = httpx2.Response(statut, headers={"request-id": "req_test"}, request=REQUETE)
    return classe(message_api, response=reponse, body=None)


def delai_depasse() -> anthropic.APITimeoutError:
    return anthropic.APITimeoutError(request=REQUETE)


def reseau_coupe() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(request=REQUETE)


class FauxClient:
    """Client factice : `reponses` est consommée dans l'ordre ; `appels` garde les arguments."""

    def __init__(self, reponses: list[Any]) -> None:
        self.reponses = list(reponses)
        self.appels: list[dict[str, Any]] = []
        self.options: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._creer))
        self._libre = threading.Event()
        self._libre.set()
        self.en_attente = threading.Event()

    def with_options(self, **options: Any) -> FauxClient:
        self.options.append(options)
        return self

    def bloquer(self) -> None:
        """La réponse suivante attend `liberer()` (échange observable « en cours »)."""
        self._libre.clear()

    def liberer(self) -> None:
        self._libre.set()

    def _creer(self, **arguments: Any) -> Any:
        self.appels.append(copy.deepcopy(arguments))
        self.en_attente.set()
        if not self._libre.wait(timeout=10):
            raise AssertionError("FauxClient : réponse retenue plus de 10 s (liberer() oublié ?)")
        if not self.reponses:
            raise AssertionError("FauxClient : plus aucune réponse prévue")
        suivante = self.reponses.pop(0)
        if isinstance(suivante, BaseException):
            raise suivante
        return suivante
