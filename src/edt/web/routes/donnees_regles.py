"""Écrans « Données > Règles et calendrier » : calendrier, grille horaire, paramètres.

- `/calendrier` : années scolaires et semestres, ajout d'une année avec ses
  dates par défaut, préremplissage des jours fériés ; sous-écrans génériques
  des années, semestres et jours fermés ;
- `/grille` : créneaux de la grille horaire ;
- `/parametres` : réglages globaux, construits depuis `edt.domaine.parametres`.
"""

from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, Request
from fastapi.responses import Response

from edt.domaine.calendrier import annee_scolaire_de
from edt.erreurs import ErreurDonneesInvalides, ErreurRegleMetier
from edt.services.parametres import reinitialiser_parametres
from edt.services.referentiels.calendrier import (
    ANNEE_SCOLAIRE,
    CRENEAU,
    JOUR_FERME,
    SEMESTRE,
    apercu_calendrier,
    creer_annee_par_defaut,
    preremplir_jours_feries,
)
from edt.services.referentiels.operations import ErreurSaisie
from edt.services.referentiels.reglages import (
    ParametreAffiche,
    enregistrer_parametres_saisis,
    parametres_affichables,
)
from edt.web.dependances import SessionRequete
from edt.web.donnees.ecrans import Ecran, Formulaire, routeur_ecran
from edt.web.gabarits import rediriger, rendre
from edt.web.menu import SECTION_CONFIGURATION, EntreeMenu

journal = logging.getLogger(__name__)

PARENT_CALENDRIER = ("Retour au calendrier", "/calendrier")

ECRAN_ANNEES = Ecran(
    ANNEE_SCOLAIRE,
    "/calendrier/annees",
    introduction="Les années scolaires. Supprimer une année supprime tout ce qui lui appartient.",
    parent=PARENT_CALENDRIER,
)
ECRAN_SEMESTRES = Ecran(
    SEMESTRE,
    "/calendrier/semestres",
    introduction="Les semestres et leurs dates de cours : un emploi du temps se génère par semestre.",
    parent=PARENT_CALENDRIER,
)
ECRAN_JOURS_FERMES = Ecran(
    JOUR_FERME,
    "/calendrier/jours-fermes",
    introduction=(
        "Jours fériés, vacances, examens et semaines spéciales : pour tout l'établissement ou "
        "seulement pour un groupe (et ses sous-groupes)."
    ),
    parent=PARENT_CALENDRIER,
)
ECRAN_GRILLE = Ecran(
    CRENEAU,
    "/grille",
    introduction=(
        "Les créneaux de la grille horaire : une séance commence au début d'un créneau "
        "(sauf créneau imposé). Le samedi n'accueille que les types de séance qui l'autorisent."
    ),
    edition_en_ligne=True,
    titre_page="Grille horaire",
)

GROUPES_PARAMETRES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Calcul", ("temps_limite_s", "temps_limite_long_s", "nb_workers")),
    (
        "Journée",
        ("ouverture_min", "fermeture_min", "pause_min", "dejeuner_debut_min", "dejeuner_fin_min"),
    ),
    ("Lieux", ("trajet_defaut_min", "accessibilite_inconnue_acceptee")),
    (
        "Qualité de l'emploi du temps",
        (
            "objectif_actif",
            "poids_trous",
            "poids_changement_campus",
            "poids_samedi",
            "poids_creneaux_extremes",
        ),
    ),
)
"""Rubriques de l'écran des paramètres ; un paramètre non cité va dans « Autres réglages »."""

UNITES = {"s": "secondes", "min": "minutes"}

routeur = APIRouter()
MENU = [
    EntreeMenu("Calendrier", "/calendrier", section=SECTION_CONFIGURATION, ordre=500),
    EntreeMenu("Grille horaire", "/grille", section=SECTION_CONFIGURATION, ordre=510),
    EntreeMenu("Paramètres", "/parametres", section=SECTION_CONFIGURATION, ordre=520),
]


# --------------------------------------------------------------------------- #
# Calendrier
# --------------------------------------------------------------------------- #


@routeur.get("/calendrier", name="calendrier")
def calendrier(request: Request, session: SessionRequete) -> Response:
    """Vue d'ensemble : années, semestres, nombre de fermetures."""
    annees = apercu_calendrier(session)
    proposee = annee_scolaire_de(dt.date.today())
    existantes = {a.libelle for a in annees}
    while f"{proposee}-{proposee + 1}" in existantes:
        proposee += 1
    return rendre(
        request, "donnees/calendrier.html", {"annees": annees, "annee_proposee": proposee}
    )


@routeur.post("/calendrier/annees/par-defaut", name="annee_par_defaut")
def ajouter_annee(request: Request, session: SessionRequete, formulaire: Formulaire) -> Response:
    """Crée une année scolaire avec ses deux semestres et ses jours fériés."""
    brute = str(formulaire.get("annee_debut", "")).strip()
    if not brute.isdigit():
        raise ErreurDonneesInvalides(f"Année de début invalide : « {brute} » (par exemple 2027).")
    try:
        annee = creer_annee_par_defaut(session, int(brute))
    except ErreurRegleMetier as refus:
        return rediriger(request, "/calendrier", refus.message, "erreur")
    return rediriger(
        request,
        "/calendrier",
        f"Année scolaire {annee['libelle']} créée avec ses deux semestres et ses jours fériés.",
    )


@routeur.post("/calendrier/annees/{ident:int}/feries", name="annee_feries")
def feries(request: Request, session: SessionRequete, ident: int) -> Response:
    """Ajoute les jours fériés manquants de l'année (fériés mobiles compris)."""
    ajoutes = preremplir_jours_feries(session, ident)
    message = (
        f"{ajoutes} jour{'s' if ajoutes > 1 else ''} férié{'s' if ajoutes > 1 else ''} "
        f"ajouté{'s' if ajoutes > 1 else ''}."
        if ajoutes
        else "Tous les jours fériés de l'année étaient déjà présents."
    )
    return rediriger(request, "/calendrier", message, "succes" if ajoutes else "info")


# --------------------------------------------------------------------------- #
# Paramètres
# --------------------------------------------------------------------------- #


def _rubriques_parametres(
    parametres: tuple[ParametreAffiche, ...],
) -> list[tuple[str, list[ParametreAffiche]]]:
    par_cle = {p.cle: p for p in parametres}
    rubriques = [
        (titre, [par_cle[cle] for cle in cles if cle in par_cle])
        for titre, cles in GROUPES_PARAMETRES
    ]
    cites = {cle for _, cles in GROUPES_PARAMETRES for cle in cles}
    autres = [p for p in parametres if p.cle not in cites]
    if autres:
        rubriques.append(("Autres réglages", autres))
    return [(titre, liste) for titre, liste in rubriques if liste]


def _rendre_parametres(
    request: Request,
    session: SessionRequete,
    saisies: dict[str, str] | None = None,
    erreur: ErreurDonneesInvalides | None = None,
) -> Response:
    parametres = parametres_affichables(session)
    return rendre(
        request,
        "donnees/parametres.html",
        {
            "rubriques": _rubriques_parametres(parametres),
            "saisies": saisies or {},
            "erreurs": erreur.champs if isinstance(erreur, ErreurSaisie) else {},
            "erreur": erreur,
            "unites": UNITES,
        },
        statut=422 if erreur is not None else 200,
    )


@routeur.get("/parametres", name="parametres")
def parametres(request: Request, session: SessionRequete) -> Response:
    """Formulaire des paramètres globaux."""
    return _rendre_parametres(request, session)


@routeur.post("/parametres", name="parametres_enregistrer")
def enregistrer_parametres(
    request: Request, session: SessionRequete, formulaire: Formulaire
) -> Response:
    """Enregistre les paramètres ; une valeur refusée est signalée sous son champ."""
    saisies: dict[str, str] = {}
    for parametre in parametres_affichables(session):
        if parametre.type == "bool":
            saisies[parametre.cle] = "oui" if parametre.cle in formulaire else "non"
        elif parametre.cle in formulaire:
            saisies[parametre.cle] = str(formulaire[parametre.cle])
    try:
        enregistrer_parametres_saisis(session, saisies)
    except ErreurDonneesInvalides as erreur:
        session.rollback()
        return _rendre_parametres(request, session, saisies, erreur)
    return rediriger(request, "/parametres", "Paramètres enregistrés.")


@routeur.post("/parametres/defaut", name="parametres_defaut")
def parametres_par_defaut(request: Request, session: SessionRequete) -> Response:
    """Remet tous les paramètres à leur valeur par défaut."""
    reinitialiser_parametres(session)
    return rediriger(
        request, "/parametres", "Tous les paramètres ont repris leur valeur par défaut."
    )


for _ecran in (ECRAN_ANNEES, ECRAN_SEMESTRES, ECRAN_JOURS_FERMES, ECRAN_GRILLE):
    routeur.include_router(routeur_ecran(_ecran))
