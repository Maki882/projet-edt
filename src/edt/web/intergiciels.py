"""Intergiciel de journalisation des requêtes.

Pour chaque requête HTTP :

- attribue un identifiant court, posé dans le contexte de journalisation
  (toutes les lignes de journal de la requête le portent : `req=...`), dans
  `request.state.id_requete` et dans l'en-tête de réponse `X-Request-ID` ;
- journalise la méthode, le chemin, le statut et la durée ;
- intercepte toute exception non prévue : la trace complète part dans le
  journal avec l'identifiant, et l'utilisateur reçoit une page d'erreur
  lisible portant ce même identifiant (jamais de trace brute à l'écran).
"""

from __future__ import annotations

import logging
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
                        "L'opération n'a pas pu aboutir. L'incident a été enregistré : la "
                        "référence ci-dessous permet de le retrouver dans le journal."
                    ),
                )
                await reponse(scope, receive, envoyer)
        finally:
            duree_ms = (time.perf_counter() - debut) * 1000
            niveau = (
                logging.DEBUG
                if scope["path"].startswith(PREFIXES_SILENCIEUX) and statut < 400
                else logging.INFO
            )
            journal.log(
                niveau, "%s %s -> %d en %.0f ms", scope["method"], scope["path"], statut, duree_ms
            )
            contexte_requete.reset(jeton)
