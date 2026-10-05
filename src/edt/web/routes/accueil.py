"""Routes du socle : page d'accueil et contrôle de santé."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from edt import __version__
from edt.config import charger_configuration
from edt.services.etat import diagnostiquer_base
from edt.services.generation import dernier_calcul
from edt.web.gabarits import rendre
from edt.web.menu import EntreeMenu

routeur = APIRouter()
MENU = [EntreeMenu("Accueil", "/", ordre=0)]


@routeur.get("/", response_class=HTMLResponse, name="accueil")
def accueil(request: Request) -> HTMLResponse:
    """Page d'accueil : état de la base, compteurs des entités, dernier calcul."""
    etat = diagnostiquer_base()
    return rendre(
        request,
        "accueil.html",
        {
            "etat": etat,
            "dernier_calcul": dernier_calcul() if etat.initialisee else None,
            "environnement": charger_configuration().env,
        },
    )


@routeur.get("/sante", name="sante")
def sante() -> JSONResponse:
    """Contrôle de santé pour la supervision et le déploiement.

    Répond 200 avec `{"statut": "ok"}` quand la base est accessible et
    initialisée, 503 avec `{"statut": "degrade"}` sinon.
    """
    etat = diagnostiquer_base()
    pret = etat.accessible and etat.initialisee
    return JSONResponse(
        {
            "statut": "ok" if pret else "degrade",
            "version": __version__,
            "environnement": charger_configuration().env,
            "base": {
                "accessible": etat.accessible,
                "initialisee": etat.initialisee,
                "mode_journal": etat.mode_journal,
            },
        },
        status_code=200 if pret else 503,
    )
