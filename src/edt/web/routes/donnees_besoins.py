"""Écrans « Données > Besoins et disponibilités » : besoins (la demande) et indisponibilités.

L'écran des besoins est l'entrée du générateur : liste filtrable (semestre,
promotion, matière, type, enseignant), ajout rapide d'un besoin hebdomadaire,
duplication d'une ligne, et charge hebdomadaire de chaque groupe.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from edt.domaine.enumerations import ModeVolume
from edt.services.referentiels.enseignements import (
    BESOIN,
    INDISPONIBILITE,
    charges_hebdomadaires,
    dupliquer_besoin,
)
from edt.services.referentiels.operations import PageListe, valeurs_par_defaut
from edt.web.dependances import SessionRequete
from edt.web.donnees.ecrans import ActionLigne, Ecran, routeur_ecran
from edt.web.donnees.formulaires import champs_affiches
from edt.web.gabarits import rediriger
from edt.web.menu import SECTION_PLANIFICATION, EntreeMenu

journal = logging.getLogger(__name__)

CHAMPS_AJOUT_RAPIDE = (
    "semestre_id",
    "matiere_id",
    "type_seance_id",
    "groupes",
    "enseignants",
    "duree_min",
    "seances_par_semaine",
)
"""Champs du formulaire d'ajout rapide (un besoin « chaque semaine »)."""


def _contexte_besoins(session: Session, page: PageListe) -> dict[str, Any]:
    semestre = page.filtres.get("semestre", "")
    valeurs = valeurs_par_defaut(BESOIN)
    valeurs.update(
        semestre_id=semestre,
        matiere_id=page.filtres.get("matiere", ""),
        type_seance_id=page.filtres.get("type", ""),
        enseignants=[page.filtres["enseignant"]] if page.filtres.get("enseignant") else [],
    )
    return {
        "ajout_rapide": champs_affiches(session, BESOIN, valeurs, noms=CHAMPS_AJOUT_RAPIDE),
        "mode_par_semaine": ModeVolume.PAR_SEMAINE.value,
        "charges": charges_hebdomadaires(session, int(semestre)) if semestre else (),
        "semestre_choisi": semestre,
    }


ECRAN_BESOINS = Ecran(
    BESOIN,
    "/besoins",
    introduction=(
        "Ce qu'il faut enseigner : pour chaque matière, à quels groupes, par quels "
        "enseignants, combien de séances et avec quelles exigences de salle. C'est ce que "
        "le générateur place dans l'emploi du temps."
    ),
    gabarit_liste="donnees/besoins.html",
    contexte_liste=_contexte_besoins,
    actions_ligne=(ActionLigne("Dupliquer", "/dupliquer"),),
)
ECRAN_INDISPONIBILITES = Ecran(
    INDISPONIBILITE,
    "/indisponibilites",
    introduction=(
        "Les moments où un enseignant, une salle ou un groupe ne peut pas avoir cours : "
        "chaque semaine (le lundi, le vendredi après-midi...) ou pendant une période datée."
    ),
)

routeur = APIRouter()
MENU = [
    EntreeMenu("Besoins", "/besoins", section=SECTION_PLANIFICATION, ordre=200),
    EntreeMenu("Indisponibilités", "/indisponibilites", section=SECTION_PLANIFICATION, ordre=210),
]


@routeur.post("/besoins/{ident:int}/dupliquer", name="besoin_dupliquer")
def dupliquer(request: Request, session: SessionRequete, ident: int) -> Response:
    """Crée une copie du besoin et ouvre son formulaire pour l'ajuster."""
    copie = dupliquer_besoin(session, ident)
    return rediriger(
        request,
        ECRAN_BESOINS.url_element(copie["id"]),
        f"Copie créée (besoin « {copie['nom_affiche']} ») : ajustez-la puis enregistrez.",
    )


routeur.include_router(routeur_ecran(ECRAN_BESOINS))
routeur.include_router(routeur_ecran(ECRAN_INDISPONIBILITES))
