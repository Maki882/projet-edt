"""Rendu des pages : environnement Jinja2 partagé, filtres, messages et redirections.

Tous les écrans passent par ces fonctions :

    return rendre(request, "salles/liste.html", {"salles": salles})
    return rediriger(request, "/salles", "Salle enregistrée.")

Les gabarits vivent dans `edt/web/templates/` (un sous-dossier par famille
d'écrans) et étendent `base.html`. Les fragments destinés à HTMX sont de
simples gabarits sans `{% extends %}`.

Filtres disponibles dans tous les gabarits : `heure` (510 donne « 8 h 30 »),
`duree` (120 donne « 2 h »), `jour` (0 donne « lundi »), `date_fr`
(« 05/10/2026 »), `dateheure_fr`.

Ce qui vient du navigateur n'est jamais cru sur parole :

- `adresse_locale` dit si une adresse de retour reste dans l'application
  (pas de redirection ouverte, aucun caractère de contrôle qui finirait dans
  un en-tête) ; `rediriger` écarte en dernier recours une adresse qui en
  contient (un retour à la ligne dans `HX-Redirect` ajouterait un en-tête) ;
- `adresse_courante` lit l'en-tête `HX-Current-URL` sans planter s'il est
  illisible ;
- un identifiant d'adresse (`{n:int}`) de plus de `CHIFFRES_ADRESSE_MAX`
  chiffres ne correspond à aucune page (404) : Python refuse de convertir plus
  de 4 300 chiffres, ce qui donnait une erreur 500 dans le routage.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, quote, unquote, urlsplit

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.convertors import IntegerConvertor, register_url_convertor

from edt import __version__
from edt.domaine.enumerations import JOURS
from edt.domaine.heures import duree_vers_texte, minutes_vers_hhmm, minutes_vers_texte
from edt.journal import contexte_requete

journal = logging.getLogger(__name__)

DOSSIER_WEB = Path(__file__).parent
DOSSIER_GABARITS = DOSSIER_WEB / "templates"
DOSSIER_STATIQUE = DOSSIER_WEB / "static"

COOKIE_MESSAGES = "edt_messages"
CHIFFRES_ADRESSE_MAX = 30
"""Au-delà, un identifiant d'adresse ne désigne rien : la route ne correspond pas (404)."""
NIVEAUX = ("succes", "info", "attention", "erreur")


@dataclass(frozen=True)
class Message:
    """Message affiché dans la zone de messages : niveau (`NIVEAUX`), texte, détails."""

    texte: str
    niveau: str = "info"
    details: tuple[str, ...] = ()


def _jour(numero: int | None) -> str:
    return "" if numero is None else JOURS[numero]


def _heure(minutes: int | None) -> str:
    return "" if minutes is None else minutes_vers_texte(minutes)


def _duree(minutes: int | None) -> str:
    return "" if minutes is None else duree_vers_texte(minutes)


def _hhmm(minutes: int | None) -> str:
    return "" if minutes is None else minutes_vers_hhmm(minutes)


def _date_fr(valeur: dt.date | None) -> str:
    return "" if valeur is None else valeur.strftime("%d/%m/%Y")


def _dateheure_fr(valeur: dt.datetime | None) -> str:
    return "" if valeur is None else valeur.strftime("%d/%m/%Y à %H h %M")


gabarits = Jinja2Templates(directory=DOSSIER_GABARITS)
gabarits.env.filters.update(
    heure=_heure,
    hhmm=_hhmm,
    duree=_duree,
    jour=_jour,
    date_fr=_date_fr,
    dateheure_fr=_dateheure_fr,
)
gabarits.env.globals.update(version_application=__version__, JOURS=JOURS)


class _IdentifiantBorne(IntegerConvertor):
    """`{n:int}` des adresses, borné à `CHIFFRES_ADRESSE_MAX` chiffres.

    Le convertisseur d'origine accepte « [0-9]+ » puis appelle `int()`, qui lève
    `ValueError` au-delà de 4 300 chiffres : erreur 500 avant même la route.
    Enregistré à l'import de ce module, que tous les modules de routes
    importent avant de déclarer leurs adresses.
    """

    regex = f"[0-9]{{1,{CHIFFRES_ADRESSE_MAX}}}"


register_url_convertor("int", _IdentifiantBorne())

_CARACTERES_DE_CONTROLE = frozenset(chr(code) for code in (*range(32), 127))


def adresse_locale(adresse: object, prefixe: str = "/") -> bool:
    """Vrai si `adresse` est une adresse de l'application commençant par `prefixe`.

    Refusées : adresse vers un autre site (« //exemple.org », barre oblique
    inverse), et tout caractère de contrôle (un retour à la ligne finirait dans
    un en-tête de réponse).
    """
    texte = adresse if isinstance(adresse, str) else ""
    return (
        texte.startswith(prefixe)
        and texte.startswith("/")
        and "//" not in texte
        and "\\" not in texte
        and not _CARACTERES_DE_CONTROLE.intersection(texte)
    )


def adresse_courante(request: Request) -> SplitResult | None:
    """Adresse de la page d'où part une requête HTMX (`HX-Current-URL`), ou None.

    L'en-tête vient du navigateur : illisible (« http://[::1 »), il est ignoré
    et noté dans le journal au lieu de faire échouer la requête.
    """
    brute = request.headers.get("HX-Current-URL")
    if not brute:
        return None
    try:
        return urlsplit(brute)
    except ValueError as erreur:
        journal.info("En-tête HX-Current-URL illisible, ignoré (%s) : %r", erreur, brute[:200])
        return None


def est_htmx(request: Request) -> bool:
    """Vrai si la requête vient de HTMX (elle attend un fragment, pas une page entière)."""
    return request.headers.get("HX-Request") == "true"


def rendre(
    request: Request,
    gabarit: str,
    contexte: Mapping[str, Any] | None = None,
    *,
    statut: int = 200,
    messages: Iterable[Message] = (),
) -> HTMLResponse:
    """Rend un gabarit avec le contexte commun (menu, messages, identifiant de requête).

    `messages` s'ajoutent à ceux laissés par la redirection précédente
    (`rediriger`). Variables toujours disponibles dans le gabarit : `request`,
    `menu`, `chemin`, `messages`, `id_requete`, `version_application`.
    """
    en_attente = () if est_htmx(request) else _lire_messages(request)
    donnees: dict[str, Any] = {
        "menu": getattr(request.app.state, "menu", ()),
        "chemin": request.url.path,
        "messages": [*en_attente, *messages],
        "id_requete": contexte_requete.get() or getattr(request.state, "id_requete", None),
    }
    donnees.update(contexte or {})
    reponse = gabarits.TemplateResponse(request, gabarit, donnees, status_code=statut)
    if en_attente:
        reponse.delete_cookie(COOKIE_MESSAGES)
    return reponse


def rediriger(
    request: Request, url: str, message: str | None = None, niveau: str = "succes"
) -> Response:
    """Redirige après une action (POST puis GET) en laissant un message pour la page suivante.

    Pour une requête HTMX, la redirection passe par l'en-tête `HX-Redirect`.
    Une adresse qui contient un caractère de contrôle (fabriquée : les écrans
    vérifient leurs adresses de retour avec `adresse_locale`) est remplacée par
    l'accueil, jamais recopiée dans un en-tête.
    """
    if _CARACTERES_DE_CONTROLE.intersection(url):
        journal.warning("Redirection vers une adresse invalide écartée : %r", url[:200])
        url = "/"
    if est_htmx(request):
        reponse: Response = Response(status_code=204, headers={"HX-Redirect": url})
    else:
        reponse = RedirectResponse(url, status_code=303)
    if message:
        contenu = json.dumps([asdict(Message(texte=message, niveau=niveau))], ensure_ascii=False)
        reponse.set_cookie(
            COOKIE_MESSAGES, quote(contenu), max_age=120, httponly=True, samesite="lax"
        )
    return reponse


def _lire_messages(request: Request) -> tuple[Message, ...]:
    """Messages déposés par `rediriger` ; un contenu illisible est journalisé puis écarté."""
    brut = request.cookies.get(COOKIE_MESSAGES)
    if not brut:
        return ()
    try:
        elements = json.loads(unquote(brut))
        return tuple(
            Message(
                texte=str(element["texte"]),
                niveau=element["niveau"] if element.get("niveau") in NIVEAUX else "info",
                details=tuple(str(d) for d in element.get("details", ())),
            )
            for element in elements
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        journal.warning("Cookie de messages illisible, ignoré : %r", brut[:200])
        return ()
