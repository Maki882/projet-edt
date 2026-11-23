"""Accès au site : connexion par mot de passe, gestionnaire ou consultation.

Désactivé par défaut : sans `EDT_MDP_GESTION`, le site reste ouvert à tous,
comme avant (`edt.config`). Renseigné, toute page demande une connexion :

- **gestion** (`EDT_MDP_GESTION`) : tous les droits, comme sans connexion ;
- **consultation** (`EDT_MDP_CONSULTATION`, facultatif) : lecture seule de
  l'emploi du temps et de son export (tableur, agenda), pour des étudiants
  ou des enseignants. Le reste (données, calculs, assistant, tests) répond
  « page réservée au gestionnaire » (403), et aucune écriture n'est permise.

Le rôle est gardé dans un cookie signé (HMAC-SHA256), valable
`DUREE_SESSION_S`. La clé de signature dérive des deux mots de passe :
changer un mot de passe déconnecte tout le monde, sans autre réglage.

Les règles d'accès sont ici, en un seul endroit, et l'intergiciel
`IntergicielAcces` les applique avant toute route : un écran ajouté plus tard
est réservé au gestionnaire tant qu'il n'est pas ajouté à
`autorise_en_consultation`.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from collections.abc import Callable, Iterable
from typing import Literal
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from edt.config import Configuration
from edt.web.menu import Onglet, SectionMenu

journal = logging.getLogger(__name__)

Role = Literal["gestion", "consultation"]
ROLE_GESTION: Role = "gestion"
ROLE_CONSULTATION: Role = "consultation"
LIBELLES_ROLES: dict[str, str] = {ROLE_GESTION: "Gestion", ROLE_CONSULTATION: "Consultation"}

COOKIE_ACCES = "edt_acces"
DUREE_SESSION_S = 12 * 3600
"""Durée d'une connexion : une journée de travail, puis il faut se reconnecter."""

ADRESSE_CONNEXION = "/connexion"
CHEMINS_PUBLICS = frozenset({ADRESSE_CONNEXION, "/deconnexion", "/sante"})
"""Chemins servis sans connexion : la page de connexion, la déconnexion et la sonde de santé."""
PREFIXES_PUBLICS = ("/static/",)

PREFIXE_CONSULTATION = "/emploi-du-temps"
EXPORTS_CONSULTATION = frozenset({"/export/emploi-du-temps.csv", "/export/emploi-du-temps.ics"})
METHODES_LECTURE = frozenset({"GET", "HEAD"})

PREPARATION_INTERNE = "preparation_interne"
"""Marque posée dans l'état de la requête par la préparation des pages au démarrage
(`edt.web.app._preparer`) : servie comme le gestionnaire, sans cookie."""


def _cle(config: Configuration) -> bytes:
    """Clé de signature des cookies, dérivée des deux mots de passe (voir l'en-tête)."""
    matiere = f"edt-acces\x00{config.mdp_gestion}\x00{config.mdp_consultation}"
    return hashlib.sha256(matiere.encode("utf-8")).digest()


def _signature(cle: bytes, texte: str) -> str:
    return hmac.new(cle, texte.encode("ascii"), hashlib.sha256).hexdigest()


def creer_jeton(config: Configuration, role: Role, maintenant: float | None = None) -> str:
    """Valeur du cookie d'accès : `role.expiration.signature`."""
    expiration = int((time.time() if maintenant is None else maintenant) + DUREE_SESSION_S)
    texte = f"{role}.{expiration}"
    return f"{texte}.{_signature(_cle(config), texte)}"


def lire_jeton(
    config: Configuration, jeton: str | None, maintenant: float | None = None
) -> Role | None:
    """Rôle porté par un cookie valide, ou `None` (absent, falsifié, expiré, rôle inconnu)."""
    if not jeton:
        return None
    morceaux = jeton.split(".")
    if len(morceaux) != 3:
        return None
    role, expiration, signature = morceaux
    attendue = _signature(_cle(config), f"{role}.{expiration}")
    if not hmac.compare_digest(signature, attendue):
        return None
    if not expiration.isascii() or not expiration.isdigit():
        return None
    if int(expiration) < (time.time() if maintenant is None else maintenant):
        return None
    if role == ROLE_GESTION:
        return ROLE_GESTION
    if role == ROLE_CONSULTATION and config.mdp_consultation:
        return ROLE_CONSULTATION
    return None


def role_du_mot_de_passe(config: Configuration, mot_de_passe: str) -> Role | None:
    """Rôle ouvert par ce mot de passe, ou `None`. Comparaison à temps constant."""
    saisi = mot_de_passe.encode("utf-8")
    gestion = hmac.compare_digest(saisi, config.mdp_gestion.encode("utf-8"))
    consultation = bool(config.mdp_consultation) and hmac.compare_digest(
        saisi, config.mdp_consultation.encode("utf-8")
    )
    if gestion:
        return ROLE_GESTION
    if consultation:
        return ROLE_CONSULTATION
    return None


def est_public(chemin: str) -> bool:
    """Vrai si le chemin est servi sans connexion."""
    return chemin in CHEMINS_PUBLICS or chemin.startswith(PREFIXES_PUBLICS)


def autorise_en_consultation(methode: str, chemin: str) -> bool:
    """Vrai si l'accès en consultation peut servir cette requête.

    Lecture seule : l'emploi du temps (vues et détail d'une séance, sans le
    formulaire de déplacement) et son export en tableur ou en agenda.
    """
    if methode not in METHODES_LECTURE:
        return False
    if chemin in EXPORTS_CONSULTATION:
        return True
    if chemin == PREFIXE_CONSULTATION:
        return True
    return chemin.startswith(PREFIXE_CONSULTATION + "/") and not chemin.endswith("/formulaire")


def menu_du_role(menu: Iterable[Onglet], role: str | None) -> tuple[Onglet, ...]:
    """Menu affiché : complet, sauf en consultation où ne restent que les pages permises."""
    menu = tuple(menu)
    if role != ROLE_CONSULTATION:
        return menu
    onglets = []
    for onglet in menu:
        groupes = []
        for groupe in onglet.groupes:
            entrees = tuple(e for e in groupe.entrees if autorise_en_consultation("GET", e.url))
            if entrees:
                groupes.append(SectionMenu(groupe.titre, entrees, groupe.sections))
        if groupes:
            onglets.append(Onglet(onglet.titre, tuple(groupes)))
    return tuple(onglets)


def contexte_acces(request: Request, menu: Iterable[Onglet]) -> dict[str, object]:
    """Variables des gabarits liées à l'accès : menu, comptes actifs, rôle, libellé, lecture seule.

    `request.state.role` n'existe que si les comptes sont actifs (l'intergiciel
    le pose) : sans comptes, le menu est complet et rien n'est en lecture seule.
    Connexion requise et pas encore faite : aucun menu (la page de connexion).
    """
    etat = request.scope.get("state", {})
    if "role" not in etat:
        return {
            "menu": tuple(menu),
            "acces_actif": False,
            "role": None,
            "libelle_role": None,
            "lecture_seule": False,
        }
    role = etat["role"]
    return {
        "menu": menu_du_role(menu, role) if role else (),
        "acces_actif": True,
        "role": role,
        "libelle_role": LIBELLES_ROLES.get(role) if role else None,
        "lecture_seule": role == ROLE_CONSULTATION,
    }


def adresse_de_retour(suite: str | None) -> str:
    """Page où revenir après la connexion : un chemin local du site, sinon l'accueil."""
    if not suite or not suite.startswith("/") or suite.startswith("//") or "\\" in suite:
        return "/"
    if any(ord(caractere) < 32 for caractere in suite):
        return "/"
    return suite


class IntergicielAcces:
    """Intergiciel ASGI : exige une connexion et applique les droits du rôle.

    Sans comptes (`Configuration.comptes_actifs` faux), il laisse tout passer.
    Le rôle reconnu est posé dans `request.state.role` pour les gabarits.
    `reponse_refus` construit la page « réservé au gestionnaire » (injectée
    pour garder ce module indépendant des pages d'erreur).
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        config: Configuration,
        reponse_refus: Callable[[Request], Response],
    ) -> None:
        self.app = app
        self.config = config
        self.reponse_refus = reponse_refus

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.config.comptes_actifs:
            await self.app(scope, receive, send)
            return
        requete = Request(scope, receive)
        chemin = scope["path"]
        etat = scope.setdefault("state", {})
        if etat.get(PREPARATION_INTERNE):
            role: Role | None = ROLE_GESTION
        else:
            role = lire_jeton(self.config, requete.cookies.get(COOKIE_ACCES))
        etat["role"] = role
        if est_public(chemin):
            await self.app(scope, receive, send)
            return
        if role is None:
            reponse = self._vers_connexion(requete)
        elif role == ROLE_CONSULTATION and not autorise_en_consultation(scope["method"], chemin):
            if chemin == "/" and scope["method"] in METHODES_LECTURE:
                reponse = RedirectResponse(PREFIXE_CONSULTATION, status_code=303)
            else:
                journal.info("Accès en consultation refusé : %s %s", scope["method"], chemin)
                reponse = self.reponse_refus(requete)
        else:
            await self.app(scope, receive, send)
            return
        await reponse(scope, receive, send)

    def _vers_connexion(self, requete: Request) -> Response:
        """Renvoie vers la page de connexion, en retenant la page demandée (GET seulement)."""
        cible = ADRESSE_CONNEXION
        if requete.method in METHODES_LECTURE and requete.headers.get("HX-Request") != "true":
            demande = requete.url.path + (f"?{requete.url.query}" if requete.url.query else "")
            if demande != "/":
                cible += f"?suite={quote(demande, safe='')}"
        if requete.headers.get("HX-Request") == "true":
            # Un fragment HTMX ne peut pas afficher une page entière : la page entière change.
            return Response(status_code=401, headers={"HX-Redirect": cible})
        return RedirectResponse(cible, status_code=303)
