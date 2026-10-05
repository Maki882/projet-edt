"""Onglet « À traiter » : anomalies et séances à programmer des emplois du temps actifs.

Adresses :

- `GET /a-traiter` (`semestre_id` facultatif) : pour la version active de
  chaque semestre, anomalies de la semaine type et des séances datées
  (par gravité puis par contrainte, chaque ligne menant à la séance dans
  l'emploi du temps) et séances à programmer ;
- `GET /a-traiter/compteur` : petit fragment avec le nombre d'éléments à
  traiter, prévu pour être affiché dans l'onglet du menu.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from edt.services.emploi_du_temps import anomalies, presentation, versions
from edt.web.dependances import SessionRequete
from edt.web.gabarits import rendre
from edt.web.menu import EntreeMenu

SECTION_A_TRAITER = "À traiter"

routeur = APIRouter(prefix="/a-traiter")
MENU = [
    EntreeMenu(
        "Anomalies et séances à programmer", "/a-traiter", section=SECTION_A_TRAITER, ordre=350
    )
]


@routeur.get("", name="a_traiter")
def page(request: Request, session: SessionRequete, semestre_id: int | None = None):
    """Ce qui reste à traiter dans les emplois du temps actifs."""
    if semestre_id is not None:
        versions.exiger_semestre(session, semestre_id)
    return rendre(
        request,
        "edt/a_traiter.html",
        {
            "fmt": presentation,
            "semestres": versions.lister_semestres(session),
            "semestre_id": semestre_id,
            "liste": anomalies.a_traiter(session, semestre_id),
        },
    )


@routeur.get("/compteur", name="a_traiter_compteur")
def compteur(request: Request, session: SessionRequete):
    """Nombre d'éléments à traiter (fragment pour l'onglet du menu ; vide s'il n'y a rien)."""
    liste = anomalies.a_traiter(session)
    return rendre(
        request,
        "edt/_compteur.html",
        {
            "total": sum(element.total for element in liste),
            "complet": all(element.complet for element in liste),
        },
    )
