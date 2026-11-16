"""Intergiciels : journalisation des requêtes, HEAD servi comme GET.

`IntergicielHead` : FastAPI ne déclare que GET pour `@routeur.get`
(Starlette, lui, y ajoute HEAD) ; sans cet intergiciel, une sonde de
supervision ou `curl -I` recevrait 405 partout. HEAD suit le chemin de GET,
corps retiré.

`IntergicielJournal`, pour chaque requête HTTP :

- attribue un identifiant court, posé dans le contexte de journalisation
  (toutes les lignes de journal de la requête le portent : `req=...`), dans
  `request.state.id_requete` et dans l'en-tête de réponse `X-Request-ID` ;
- journalise la méthode, le chemin, le statut et la durée (au niveau DEBUG
  pour les fichiers statiques et l'interrogation périodique du suivi d'un
  calcul, qui écrirait sinon une ligne par seconde : `est_silencieux`) ;
- intercepte toute exception non prévue : la trace complète part dans le
  journal avec l'identifiant, et l'utilisateur reçoit une page d'erreur
  lisible portant ce même identifiant (jamais de trace brute à l'écran).
"""

from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from edt.journal import contexte_requete
from edt.web.erreurs import reponse_erreur

journal = logging.getLogger("edt.web.requetes")

EN_TETE_ID = "X-Request-ID"
PREFIXES_SILENCIEUX = ("/static/",)
"""Chemins journalisés au niveau DEBUG seulement (fichiers statiques)."""
MOTIFS_SILENCIEUX = (re.compile(r"/calculs/[0-9]+/etat"),)
"""Chemins entiers journalisés au niveau DEBUG : suivi d'un calcul, interrogé chaque seconde."""


def est_silencieux(chemin: str, statut: int) -> bool:
    """Vrai si la requête réussie est journalisée au niveau DEBUG seulement (voir l'en-tête)."""
    return statut < 400 and (
        chemin.startswith(PREFIXES_SILENCIEUX)
        or any(motif.fullmatch(chemin) for motif in MOTIFS_SILENCIEUX)
    )


class IntergicielHead:
    """Intergiciel ASGI : HEAD répond comme GET (statut, en-têtes dont Content-Length), sans corps.

    Placé sous `IntergicielJournal`, qui journalise donc bien « HEAD ». Une
    route qui n'accepte pas GET répond à HEAD comme à GET : 405.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "HEAD":
            await self.app(scope, receive, send)
            return

        async def sans_corps(message: Message) -> None:
            if message["type"] == "http.response.body":
                message = {**message, "body": b""}
            await send(message)

        await self.app({**scope, "method": "GET"}, receive, sans_corps)


class IntergicielJournal:
    """Intergiciel ASGI : identifiant de requête, journal, filet de sécurité des erreurs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        id_requete = uuid.uuid4().hex[:12]
        scope.setdefault("state", {})["id_requete"] = id_requete
        jeton = contexte_requete.set(id_requete)
        debut = time.perf_counter()
        statut = 500
        reponse_commencee = False

        async def envoyer(message: Message) -> None:
            nonlocal statut, reponse_commencee
            if message["type"] == "http.response.start":
                statut = message["status"]
                reponse_commencee = True
                MutableHeaders(scope=message)[EN_TETE_ID] = id_requete
            await send(message)

        try:
            try:
                await self.app(scope, receive, envoyer)
            except Exception:
                journal.exception("Erreur inattendue pendant %s %s", scope["method"], scope["path"])
                if reponse_commencee:
                    # La réponse est déjà partie : impossible d'afficher une page d'erreur.
                    raise
                reponse = reponse_erreur(
                    Request(scope, receive),
                    statut=500,
                    titre="Une erreur inattendue s'est produite",
                    message=(
                        "L'opération n'a pas abouti et l'incident a été enregistré. Réessayer ; "
                        "si le problème revient, communiquer la référence ci-dessous à "
                        "l'administrateur du serveur."
                    ),
                )
                await reponse(scope, receive, envoyer)
        finally:
            duree_ms = (time.perf_counter() - debut) * 1000
            niveau = logging.DEBUG if est_silencieux(scope["path"], statut) else logging.INFO
            journal.log(
                niveau, "%s %s -> %d en %.0f ms", scope["method"], scope["path"], statut, duree_ms
            )
            contexte_requete.reset(jeton)
