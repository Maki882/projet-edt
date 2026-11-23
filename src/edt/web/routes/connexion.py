"""Connexion et déconnexion (accès gestionnaire ou consultation, voir `edt.web.acces`).

Ces pages n'existent vraiment que si des mots de passe sont configurés
(`EDT_MDP_GESTION`) : sinon, `/connexion` renvoie à l'accueil, le site étant
ouvert à tous. Pas d'entrée de menu : la connexion est demandée par
l'intergiciel d'accès, la déconnexion est un bouton de la barre du haut.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from edt.config import charger_configuration
from edt.web.acces import (
    ADRESSE_CONNEXION,
    COOKIE_ACCES,
    DUREE_SESSION_S,
    LIBELLES_ROLES,
    adresse_de_retour,
    creer_jeton,
    role_du_mot_de_passe,
)
from edt.web.gabarits import Message, rendre

journal = logging.getLogger(__name__)

routeur = APIRouter()
MENU: list = []

PAUSE_APRES_ECHEC_S = 1.0
"""Attente après un mot de passe refusé : ralentit les essais en série."""


@routeur.get(ADRESSE_CONNEXION, response_class=HTMLResponse, name="connexion")
def page_connexion(request: Request, suite: str = "") -> Response:
    """Formulaire de connexion (ou l'accueil si le site est ouvert à tous)."""
    if not charger_configuration().comptes_actifs:
        return RedirectResponse("/", status_code=303)
    return rendre(request, "connexion/connexion.html", {"suite": adresse_de_retour(suite)})


@routeur.post(ADRESSE_CONNEXION, name="se_connecter")
async def se_connecter(request: Request) -> Response:
    """Vérifie le mot de passe, pose le cookie d'accès et renvoie à la page demandée."""
    config = charger_configuration()
    if not config.comptes_actifs:
        return RedirectResponse("/", status_code=303)
    champs = await request.form()
    mot_de_passe = str(champs.get("mot_de_passe", ""))
    suite = adresse_de_retour(str(champs.get("suite", "")))
    role = role_du_mot_de_passe(config, mot_de_passe) if mot_de_passe else None
    if role is None:
        journal.info("Connexion refusée : mot de passe inconnu")
        await asyncio.sleep(PAUSE_APRES_ECHEC_S)
        return rendre(
            request,
            "connexion/connexion.html",
            {"suite": suite},
            statut=401,
            messages=[Message("Mot de passe incorrect.", "erreur")],
        )
    journal.info("Connexion réussie : accès %s", LIBELLES_ROLES[role].lower())
    reponse = RedirectResponse(suite, status_code=303)
    reponse.set_cookie(
        COOKIE_ACCES,
        creer_jeton(config, role),
        max_age=DUREE_SESSION_S,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return reponse


@routeur.post("/deconnexion", name="se_deconnecter")
def se_deconnecter() -> Response:
    """Efface le cookie d'accès et revient à la page de connexion."""
    reponse = RedirectResponse(ADRESSE_CONNEXION, status_code=303)
    reponse.delete_cookie(COOKIE_ACCES, httponly=True, samesite="lax")
    return reponse
