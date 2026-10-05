"""Routes du socle : page d'accueil (état de l'établissement en un coup d'œil) et contrôle de santé.

L'accueil montre, quand la base est prête, la prochaine étape conseillée,
l'emploi du temps actif du semestre en cours avec les quatre indicateurs de
l'équipe et ce qui reste à traiter, le dernier calcul, puis les compteurs et
l'état de la base (`edt.services.emploi_du_temps.tableau_de_bord`).
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
from edt.services.emploi_du_temps.tableau_de_bord import Etape, ProchaineEtape, tableau_de_bord
from edt.services.etat import diagnostiquer_base
from edt.services.generation import dernier_calcul
from edt.web.gabarits import Message, rendre
from edt.web.menu import EntreeMenu

journal = logging.getLogger(__name__)

routeur = APIRouter()
MENU = [EntreeMenu("Accueil", "/", ordre=0)]

ECRANS_DE_SAISIE = ("/besoins", "/salles", "/groupes", "/enseignants", "/donnees", "/import")
"""Écrans de saisie proposés pour l'étape « saisir », dans l'ordre de préférence (s'ils existent)."""


def _lien_de_saisie(request: Request, etape: ProchaineEtape) -> ProchaineEtape:
    """Complète l'étape « saisir » avec le premier écran de saisie présent dans le menu."""
    if etape.code != Etape.SAISIR or etape.lien:
        return etape
    adresses = {
        entree.url
        for section in getattr(request.app.state, "menu", ())
        for entree in section.entrees
    }
    preferes = ["/besoins"] if "besoins" in (etape.libelle_lien or "").lower() else []
    for adresse in (*preferes, *ECRANS_DE_SAISIE):
        if adresse in adresses:
            return ProchaineEtape(etape.code, etape.phrase, adresse, etape.libelle_lien)
    return etape


@routeur.get("/", response_class=HTMLResponse, name="accueil")
def accueil(request: Request) -> HTMLResponse:
    """Page d'accueil : prochaine étape, emploi du temps actif, indicateurs, base et compteurs."""
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
            "etape": _lien_de_saisie(request, tableau.prochaine_etape) if tableau else None,
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
