"""Onglet « À traiter » : anomalies et séances à programmer des emplois du temps actifs.

Adresses :

- `GET /a-traiter` (`semestre_id` facultatif) : pour la version active de
  chaque semestre, anomalies de la semaine type et des séances datées
  (par gravité puis par contrainte, chaque ligne menant à la séance dans
  l'emploi du temps) et séances à programmer ;
- `GET /a-traiter/compteur` : petit fragment avec le nombre d'éléments à
  traiter, affiché dans l'onglet du menu (`edt.web.menu.COMPTEURS_ONGLETS`) ;
  ouvert sans HTMX (adresse saisie, lien), il mène à la page.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from edt.services.emploi_du_temps import anomalies, compteur, presentation, versions
from edt.services.emploi_du_temps.identifiants import EntierFacultatif
from edt.web.dependances import SessionRequete
from edt.web.gabarits import est_htmx, rediriger, rendre
from edt.web.menu import EntreeMenu

SECTION_A_TRAITER = "À traiter"

routeur = APIRouter(prefix="/a-traiter")
MENU = [
    EntreeMenu(
        "Anomalies et séances à programmer", "/a-traiter", section=SECTION_A_TRAITER, ordre=350
    )
]


@routeur.get("", name="a_traiter")
def page(request: Request, session: SessionRequete, semestre_id: EntierFacultatif = None):
    """Ce qui reste à traiter dans les emplois du temps actifs (`semestre_id` vide : tous)."""
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
def fragment_compteur(request: Request, session: SessionRequete):
    """Nombre d'éléments à traiter (fragment pour l'onglet du menu ; vide s'il n'y a rien).

    Mémorisé tant que la base ne change pas (`compteur.compteur_a_traiter`) :
    l'appeler depuis chaque page ne coûte presque rien. Ouvert sans HTMX, il
    mène à la page « À traiter » au lieu d'un nombre nu.
    """
    if not est_htmx(request):
        return rediriger(request, "/a-traiter")
    resultat = compteur.compteur_a_traiter(session)
    return rendre(
        request,
        "edt/_compteur.html",
        {"total": resultat.total, "complet": resultat.complet},
    )
