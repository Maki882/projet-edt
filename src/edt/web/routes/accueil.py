"""Routes du socle : page d'accueil (état de l'établissement en un coup d'œil) et contrôle de santé.

L'accueil montre, quand la base est prête, la prochaine étape conseillée,
l'emploi du temps actif du semestre en cours avec les quatre indicateurs de
l'équipe et ce qui reste à traiter, le dernier calcul, puis l'état de la base
(`edt.services.emploi_du_temps.tableau_de_bord`). Les nombres de salles,
d'enseignants, etc. ne sont pas affichés : chaque liste de l'onglet Données les
donne, et ils n'aident pas à décider quoi faire.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from edt import __version__
from edt.config import charger_configuration
from edt.db.moteur import session_base
from edt.erreurs import ErreurEDT
from edt.services.emploi_du_temps import presentation
from edt.services.emploi_du_temps.tableau_de_bord import tableau_de_bord
from edt.services.etat import diagnostiquer_base
from edt.services.generation import dernier_calcul
from edt.web.gabarits import Message, rendre
from edt.web.menu import EntreeMenu

journal = logging.getLogger(__name__)

routeur = APIRouter()
MENU = [EntreeMenu("Accueil", "/", ordre=0)]


@routeur.get("/", response_class=HTMLResponse, name="accueil")
def accueil(request: Request) -> HTMLResponse:
    """Page d'accueil : prochaine étape, emploi du temps actif, indicateurs et état de la base."""
    etat = diagnostiquer_base()
    tableau = None
    messages = []
    if etat.initialisee:
        try:
            with session_base() as session:
                tableau = tableau_de_bord(session)
        except ErreurEDT as erreur:
            journal.warning("Tableau de bord indisponible : %s", erreur)
            messages.append(
                Message(
                    f"L'état de l'emploi du temps n'a pas pu être lu : {erreur.message}",
                    "attention",
                )
            )
    return rendre(
        request,
        "accueil.html",
        {
            "etat": etat,
            "dernier_calcul": dernier_calcul() if etat.initialisee else None,
            "environnement": charger_configuration().env,
            "tableau": tableau,
            "etape": tableau.prochaine_etape if tableau else None,
            "fmt": presentation,
        },
        messages=messages,
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
                "ecarts_schema": list(etat.ecarts_schema),
            },
        },
        status_code=200 if pret else 503,
    )
