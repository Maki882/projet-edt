"""Panneau « Assistant » : conversation, réponses et propositions à confirmer.

Adresses (aucune entrée de menu : le panneau est sur toutes les pages) :

- `GET /assistant/panneau` : la conversation du cookie (ou une nouvelle),
  les exemples et le formulaire (fragment chargé à la première ouverture) ;
- `POST /assistant/messages` : contrôle les limites et lance l'échange ;
  rend la bulle de l'utilisateur et la bulle d'attente ;
- `GET /assistant/echanges/{jeton}?vu=<n>` : où en est l'échange : 204 si
  l'étape n'a pas changé, sinon la bulle à jour, puis la réponse ;
- `POST /assistant/propositions/{jeton}/confirmer` et `.../refuser` ;
- `POST /assistant/conversation/nouvelle` ;
- `GET /assistant` : la même conversation dans une page entière, pour un
  navigateur sans JavaScript (rafraîchie toutes les 2 s pendant un échange).

Les refus et les erreurs propres au panneau sont rendus en 200, dans le fil
(un 4xx serait affiché par `app.js` dans la zone de messages de la page,
hors du panneau). Les routes n'ouvrent pas de session de base : les services
de l'assistant ouvrent leurs propres transactions courtes.

Garde contre l'envoi depuis un autre site (chaque message coûte) : un POST
est refusé (403, le service n'est pas appelé) si l'en-tête `Sec-Fetch-Site`
est présent et ne vaut ni `same-origin` ni `none`, ou si l'en-tête `Origin`
désigne un autre hôte que `Host`.

La fonction globale Jinja `etat_assistant()` (posée à l'import de ce module,
que `creer_app` charge avec les autres routes) donne au gabarit du panneau
(`templates/assistant/_panneau.html`, inclus par `base.html`) l'état de
l'assistant. Conception : `docs/assistant.md`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from edt.services import assistant
from edt.services.assistant import temps
from edt.services.assistant.conversations import Echange, RoleTour, Tour
from edt.web.erreurs import reponse_erreur
from edt.web.gabarits import (
    adresse_courante,
    est_htmx,
    gabarits,
    rediriger,
    rendre,
)

journal = logging.getLogger(__name__)

routeur = APIRouter(prefix="/assistant")

COOKIE = "edt_assistant"
MARGE_ATTENTE_S = 30
"""Le navigateur cesse d'interroger le serveur au bout de l'échéance d'un échange plus cette marge."""
PERDUE = (
    "Cette réponse a été perdue (redémarrage du serveur ou inactivité) : renvoyez votre demande."
)
REINITIALISEE = (
    "Conversation réinitialisée (redémarrage du serveur ou inactivité) : la précédente est perdue."
)


# --------------------------------------------------------------------------- #
# Mise en forme du texte du modèle
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BlocTexte:
    """Un paragraphe, ou une liste à tirets."""

    texte: str = ""
    elements: tuple[str, ...] = ()

    @property
    def liste(self) -> bool:
        return bool(self.elements)


PUCES = ("- ", "• ", "* ", "– ")


def blocs_de_texte(texte: str) -> list[BlocTexte]:
    """Paragraphes et listes à tirets d'un texte du modèle (le gabarit l'échappe ensuite).

    Seuls les paragraphes (lignes séparées par une ligne vide) et les tirets
    sont mis en forme ; le gras écrit « **...** » est retiré.
    """
    blocs: list[BlocTexte] = []
    paragraphe: list[str] = []
    elements: list[str] = []

    def fermer() -> None:
        if paragraphe:
            blocs.append(BlocTexte(texte=" ".join(paragraphe)))
            paragraphe.clear()
        if elements:
            blocs.append(BlocTexte(elements=tuple(elements)))
            elements.clear()

    for ligne in texte.replace("**", "").splitlines():
        brute = ligne.strip()
        if not brute:
            fermer()
        elif brute.startswith(PUCES):
            if paragraphe:
                fermer()
            elements.append(brute[2:].strip())
        else:
            if elements:
                fermer()
            paragraphe.append(brute)
    fermer()
    return blocs


def _compteur(restants: int) -> str:
    if restants <= 0:
        return "Aucun message possible dans cette conversation : commencez-en une nouvelle."
    if restants == 1:
        return "1 message possible dans cette conversation"
    return f"{restants} messages possibles dans cette conversation"


gabarits.env.globals["etat_assistant"] = assistant.etat
gabarits.env.filters["blocs_assistant"] = blocs_de_texte
gabarits.env.filters["compteur_assistant"] = _compteur


# --------------------------------------------------------------------------- #
# Aides
# --------------------------------------------------------------------------- #


def _origine_refusee(request: Request) -> bool:
    """Vrai si la requête vient manifestement d'un autre site (voir l'en-tête)."""
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        return True
    origine = request.headers.get("origin")
    if not origine:
        return False
    # « null » : page sans origine (cadre isolé, fichier local) : refusée comme un autre site.
    return origine == "null" or urlsplit(origine).netloc != request.headers.get("host")


def _refus_origine(request: Request) -> Response:
    journal.warning("Assistant : envoi refusé, venu d'un autre site (%s)", request.url.path)
    return reponse_erreur(
        request,
        statut=403,
        titre="Requête refusée",
        message="Cette action doit être faite depuis l'application elle-même.",
    )


def _poser_cookie(request: Request, reponse: Response, identifiant: str | None) -> None:
    """Pose (ou efface, sans identifiant) le cookie de la conversation.

    Il est reposé à chaque activité de la conversation (message, ouverture du
    panneau, réponse, clic sur une proposition) : sa durée suit ainsi celle de
    la conversation en mémoire, oubliée après la même durée sans activité.
    """
    if identifiant is None:
        reponse.delete_cookie(COOKIE)
        return
    reponse.set_cookie(
        COOKIE,
        identifiant,
        max_age=assistant.configuration().expiration_min * 60,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )


def _page_courante(request: Request) -> str | None:
    """Chemin de la page affichée (`HX-Current-URL`), s'il reste dans l'application."""
    adresse = adresse_courante(request)
    return None if adresse is None else assistant.page_affichee(adresse.path)


def _fragment(request: Request, gabarit: str, contexte: dict, *, statut: int = 200) -> HTMLResponse:
    return gabarits.TemplateResponse(request, gabarit, contexte, status_code=statut)


def _contexte_conversation(vue: assistant.VueConversation, page: str | None, annonce: str | None):
    config = assistant.configuration()
    return {
        "vue": vue,
        "exemples": assistant.exemples() if not vue.tours else (),
        "page": page,
        "annonce": annonce,
        "longueur_max": config.longueur_max,
        "limite_ms": _limite_ms(vue.echange),
    }


def _limite_ms(echange: Echange | None) -> int:
    """Millisecondes pendant lesquelles le navigateur peut encore attendre cet échange."""
    if echange is None:
        return 0
    config = assistant.configuration()
    ecoule = temps.maintenant() - echange.debut
    return max(1000, int((config.delai_s + MARGE_ATTENTE_S - ecoule) * 1000))


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@routeur.get("/panneau", name="assistant_panneau")
def panneau(request: Request):
    """La conversation du cookie (ou l'accueil d'une nouvelle), pour le panneau."""
    identifiant = request.cookies.get(COOKIE)
    vue = assistant.vue(identifiant)
    annonce = REINITIALISEE if vue.perdue else None
    reponse = _fragment(
        request,
        "assistant/_conversation.html",
        _contexte_conversation(vue, _page_courante(request), annonce),
    )
    if vue.perdue or vue.conversation_id is not None:
        _poser_cookie(request, reponse, vue.conversation_id)
    return reponse


@routeur.post("/messages", name="assistant_envoyer")
def envoyer(
    request: Request,
    message: Annotated[str, Form()] = "",
    page: Annotated[str, Form()] = "",
):
    """Lance un échange ; rend la bulle de l'utilisateur et la bulle d'attente, ou un refus."""
    if _origine_refusee(request):
        return _refus_origine(request)
    resultat = assistant.envoyer(
        request.cookies.get(COOKIE),
        message,
        page,
        request.client.host if request.client else None,
    )
    if not est_htmx(request):
        reponse = rediriger(
            request,
            "/assistant",
            resultat.refus.texte if resultat.refus else None,
            niveau="attention",
        )
    elif resultat.accepte:
        vue = assistant.vue(resultat.conversation_id)
        reponse = _fragment(
            request,
            "assistant/_envoi.html",
            {
                "tour": resultat.tour_utilisateur,
                "echange": resultat.echange,
                "version": 1,
                "etape": resultat.echange.etape,
                "limite_ms": _limite_ms(resultat.echange),
                "vue": vue,
                "annonce": REINITIALISEE if resultat.perdue else None,
            },
        )
        reponse.headers["X-Assistant-Accepte"] = "oui"
    else:
        reponse = _fragment(request, "assistant/_tour.html", {"tour": resultat.refus})
    if resultat.conversation_id is not None:
        _poser_cookie(request, reponse, resultat.conversation_id)
    return reponse


@routeur.get("/echanges/{jeton}", name="assistant_echange")
def echange(request: Request, jeton: str, vu: int = 0):
    """État de l'échange : 204 si l'étape n'a pas changé, sinon la bulle à jour ou finale."""
    identifiant = request.cookies.get(COOKIE)
    trouve = assistant.lire_echange(identifiant, jeton)
    if trouve is None:
        journal.info("Assistant : échange %s inconnu à l'interrogation", jeton[:12])
        return _fragment(request, "assistant/_tour.html", {"tour": Tour(RoleTour.ERREUR, PERDUE)})
    version, etape, tour = trouve.instantane()
    if tour is None and version == vu:
        return Response(status_code=204)
    if tour is None:
        return _fragment(
            request,
            "assistant/_attente.html",
            {
                "echange": trouve,
                "version": version,
                "etape": etape,
                "limite_ms": _limite_ms(trouve),
            },
        )
    reponse = _fragment(
        request,
        "assistant/_fin.html",
        {"tour": tour, "vue": assistant.vue(identifiant)},
    )
    _poser_cookie(request, reponse, identifiant)
    return reponse


def _suite_proposition(request: Request, jeton: str, action) -> Response:
    if _origine_refusee(request):
        return _refus_origine(request)
    resultat = action(request.cookies.get(COOKIE), jeton)
    if not est_htmx(request):
        return rediriger(request, "/assistant", resultat.message, niveau="info")
    if resultat.proposition is None:
        return _fragment(
            request,
            "assistant/_proposition_perdue.html",
            {"jeton": jeton, "message": resultat.message},
        )
    reponse = _fragment(
        request,
        "assistant/_proposition.html",
        {"proposition": resultat.proposition, "message": resultat.message, "apres_clic": True},
    )
    _poser_cookie(request, reponse, request.cookies.get(COOKIE))
    return reponse


@routeur.post("/propositions/{jeton}/confirmer", name="assistant_confirmer")
def confirmer(request: Request, jeton: str):
    """Écrit la proposition (revalidée par le service) et rend la carte à jour."""
    return _suite_proposition(request, jeton, assistant.confirmer)


@routeur.post("/propositions/{jeton}/refuser", name="assistant_refuser")
def refuser(request: Request, jeton: str):
    """Refuse la proposition (rien n'est écrit) et rend la carte à jour."""
    return _suite_proposition(request, jeton, assistant.refuser)


@routeur.post("/conversation/nouvelle", name="assistant_nouvelle_conversation")
def nouvelle_conversation(request: Request):
    """Oublie la conversation du cookie (refusé pendant un échange)."""
    if _origine_refusee(request):
        return _refus_origine(request)
    identifiant = request.cookies.get(COOKIE)
    refus = assistant.nouvelle_conversation(identifiant)
    if not est_htmx(request):
        reponse: Response = rediriger(request, "/assistant", refus, niveau="attention")
    else:
        vue = assistant.vue(identifiant if refus else None)
        reponse = _fragment(
            request,
            "assistant/_conversation.html",
            _contexte_conversation(vue, _page_courante(request), refus or "Nouvelle conversation."),
        )
    if refus is None:
        _poser_cookie(request, reponse, None)
    return reponse


@routeur.get("", name="assistant_page")
def page(request: Request):
    """La conversation dans une page entière (navigateur sans JavaScript)."""
    etat = assistant.etat()
    identifiant = request.cookies.get(COOKIE)
    vue = assistant.vue(identifiant)
    contexte = {
        "etat": etat,
        "page_assistant": True,
        **_contexte_conversation(vue, "/assistant", REINITIALISEE if vue.perdue else None),
    }
    reponse = rendre(request, "assistant/page.html", contexte)
    if vue.perdue:
        _poser_cookie(request, reponse, None)
    return reponse
