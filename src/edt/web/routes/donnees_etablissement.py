"""Écrans « Données > Établissement » : campus et trajets, salles, enseignants, groupes...

Chaque entité a ses écrans génériques (`edt.web.donnees.ecrans`) ; s'y
ajoutent la matrice des trajets (page des campus) et l'arbre des groupes.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from edt.erreurs import ErreurIntrouvable
from edt.services.referentiels.enseignements import MATIERE, TYPE_SEANCE
from edt.services.referentiels.lieux import (
    CAMPUS,
    EQUIPEMENT,
    SALLE,
    TYPE_SALLE,
    cle_trajet,
    enregistrer_trajets,
    matrice_trajets,
)
from edt.services.referentiels.operations import ErreurSaisie, PageListe, options
from edt.services.referentiels.personnes import (
    ENSEIGNANT,
    ETUDIANT,
    GROUPE,
    annee_par_defaut,
    arbre_groupes,
)
from edt.web.dependances import SessionRequete
from edt.web.donnees.ecrans import Ecran, Formulaire, contexte_liste, routeur_ecran
from edt.web.gabarits import rediriger, rendre
from edt.web.menu import GROUPE_LISTES, SECTION_REFERENTIELS, EntreeMenu

journal = logging.getLogger(__name__)


def _contexte_campus(session: Session, page: PageListe) -> dict[str, Any]:
    return {"matrice": matrice_trajets(session), "cle_trajet": cle_trajet, "erreurs_trajets": {}}


ECRAN_CAMPUS = Ecran(
    CAMPUS,
    "/campus",
    introduction=(
        "Les sites où ont lieu les cours, et le temps de trajet entre eux : deux séances "
        "du même jour sur deux sites différents en sont séparées."
    ),
    gabarit_liste="donnees/campus.html",
    contexte_liste=_contexte_campus,
    edition_en_ligne=True,
    titre_page="Campus et trajets",
)
ECRAN_SALLES = Ecran(
    SALLE,
    "/salles",
    introduction=(
        "Les salles de cours : leur capacité, leur type et leurs équipements décident des "
        "séances qu'elles peuvent accueillir."
    ),
)
ECRAN_TYPES_SALLE = Ecran(
    TYPE_SALLE,
    "/types-salle",
    introduction="Liste des types de salle (amphithéâtre, salle informatique...).",
    edition_en_ligne=True,
)
ECRAN_EQUIPEMENTS = Ecran(
    EQUIPEMENT,
    "/equipements",
    introduction="Liste des équipements qu'une salle peut posséder et qu'un besoin peut exiger.",
    edition_en_ligne=True,
)
ECRAN_ENSEIGNANTS = Ecran(
    ENSEIGNANT,
    "/enseignants",
    introduction="Les enseignants, leur statut et leurs éventuels maxima d'enseignement.",
)
ECRAN_GROUPES = Ecran(
    GROUPE,
    "/groupes",
    introduction=(
        "Les groupes d'étudiants : promotions, classes et demi-groupes forment un arbre ; "
        "les groupes transverses (langues, options) et tiers-temps sont à part."
    ),
    liens=(("Voir l'arbre", "/groupes/arbre"),),
)
ECRAN_ETUDIANTS = Ecran(
    ETUDIANT,
    "/etudiants",
    introduction=(
        "Facultatif : les étudiants (fictifs) et leurs inscriptions. Deux groupes qui "
        "partagent un étudiant n'ont jamais cours en même temps."
    ),
)
ECRAN_MATIERES = Ecran(
    MATIERE,
    "/matieres",
    introduction="Les matières, identifiées par leur code module et leur promotion.",
)
ECRAN_TYPES_SEANCE = Ecran(
    TYPE_SEANCE,
    "/types-seance",
    introduction="Les types de séance (CM, TD, TP...), leur couleur et leur salle par défaut.",
    edition_en_ligne=True,
)

ECRANS = (
    ECRAN_CAMPUS,
    ECRAN_SALLES,
    ECRAN_TYPES_SALLE,
    ECRAN_EQUIPEMENTS,
    ECRAN_ENSEIGNANTS,
    ECRAN_GROUPES,
    ECRAN_ETUDIANTS,
    ECRAN_MATIERES,
    ECRAN_TYPES_SEANCE,
)

routeur = APIRouter()
MENU = [
    EntreeMenu("Campus et trajets", "/campus", section=SECTION_REFERENTIELS, ordre=100),
    EntreeMenu("Salles", "/salles", section=SECTION_REFERENTIELS, ordre=110),
    EntreeMenu(
        "Types de salle",
        "/types-salle",
        section=SECTION_REFERENTIELS,
        ordre=120,
        groupe=GROUPE_LISTES,
    ),
    EntreeMenu(
        "Équipements", "/equipements", section=SECTION_REFERENTIELS, ordre=130, groupe=GROUPE_LISTES
    ),
    EntreeMenu("Enseignants", "/enseignants", section=SECTION_REFERENTIELS, ordre=140),
    EntreeMenu("Groupes", "/groupes", section=SECTION_REFERENTIELS, ordre=150),
    EntreeMenu("Étudiants", "/etudiants", section=SECTION_REFERENTIELS, ordre=160),
    EntreeMenu("Matières", "/matieres", section=SECTION_REFERENTIELS, ordre=170),
    EntreeMenu(
        "Types de séance",
        "/types-seance",
        section=SECTION_REFERENTIELS,
        ordre=180,
        groupe=GROUPE_LISTES,
    ),
]


@routeur.post("/campus/trajets", name="trajets_enregistrer")
def enregistrer_matrice(
    request: Request, session: SessionRequete, formulaire: Formulaire
) -> Response:
    """Enregistre la matrice des trajets ; une case vide rétablit le trajet par défaut."""
    matrice = matrice_trajets(session)
    saisies = {}
    for position, (campus_a, _, _) in enumerate(matrice.campus):
        for campus_b, _, _ in matrice.campus[position + 1 :]:
            cle = cle_trajet(campus_a, campus_b)
            if cle in formulaire:
                saisies[(campus_a, campus_b)] = str(formulaire[cle])
    try:
        modifies = enregistrer_trajets(session, saisies)
    except ErreurSaisie as erreur:
        session.rollback()
        contexte = contexte_liste(request, session, ECRAN_CAMPUS)
        contexte.update(erreurs_trajets=erreur.champs, saisies_trajets=dict(formulaire))
        return rendre(request, ECRAN_CAMPUS.gabarit_liste, contexte, statut=422)
    return rediriger(
        request,
        "/campus",
        f"Temps de trajet enregistrés ({modifies} modification{'s' if modifies > 1 else ''}).",
    )


@routeur.get("/groupes/arbre", name="groupes_arbre")
def arbre(request: Request, session: SessionRequete) -> Response:
    """Les groupes d'une année sous forme d'arbre."""
    annees = options(session, GROUPE, "annee_scolaire_id")
    demandee = request.query_params.get("annee", "").strip()
    par_defaut = annee_par_defaut(session)
    annee = demandee or (str(par_defaut) if par_defaut is not None else "")
    if annee and annee not in {valeur for valeur, _ in annees}:
        raise ErreurIntrouvable(f"Année scolaire introuvable : « {annee} ».")
    return rendre(
        request,
        "donnees/groupes_arbre.html",
        {
            "ecran": ECRAN_GROUPES,
            "annees": annees,
            "annee": annee,
            "noeuds": arbre_groupes(session, int(annee)) if annee else (),
        },
    )


for _ecran in ECRANS:
    routeur.include_router(routeur_ecran(_ecran))
