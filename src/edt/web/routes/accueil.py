"""Routes du socle : page d'accueil (ce qu'il faut faire maintenant) et contrôle de santé.

L'accueil ne montre que ce dont l'équipe pédagogique a besoin pour avancer
(`edt.services.emploi_du_temps.tableau_de_bord`) : la prochaine étape
conseillée ; l'emploi du temps du semestre en cours (calcul en cours, version
active à consulter, points à traiter) ; le dernier calcul **seulement** s'il
s'est terminé sans emploi du temps ; l'état de la base **seulement** s'il y a
un problème ou si la base est vide (`base_a_signaler`). Aucune statistique :
les indicateurs restent sur le résultat d'un calcul, les nombres dans les
listes de l'onglet Données, l'emplacement et l'environnement de la base dans
`/sante` et `python -m edt doctor`.
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
from edt.services.etat import EtatBase, diagnostiquer_base
from edt.web.gabarits import Message, rendre
from edt.web.menu import EntreeMenu

journal = logging.getLogger(__name__)

routeur = APIRouter()
MENU = [EntreeMenu("Accueil", "/", ordre=0)]


@routeur.get("/", response_class=HTMLResponse, name="accueil")
def accueil(request: Request) -> HTMLResponse:
    """Page d'accueil : prochaine étape, emploi du temps du semestre, calcul sans solution, base."""
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
            "base_a_signaler": base_a_signaler(etat),
            "tableau": tableau,
            "etape": tableau.prochaine_etape if tableau else None,
            "fmt": presentation,
        },
        messages=messages,
    )


def base_a_signaler(etat: EtatBase) -> bool:
    """Vrai si l'accueil doit parler de la base : panne, schéma périmé, base non initialisée ou vide.

    Quand tout va bien, le bloc « État de la base de données » n'est pas affiché.
    """
    return not etat.accessible or etat.schema_perime or not etat.initialisee or etat.vide


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
