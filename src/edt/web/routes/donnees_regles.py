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
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from edt.domaine.calendrier import annee_scolaire_de
from edt.domaine.regles import EtatRegle
from edt.erreurs import ErreurCalculEnCours, ErreurDonneesInvalides, ErreurRegleMetier
from edt.services import generation
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
from edt.services.referentiels.conversion import entier_naturel
from edt.services.referentiels.operations import ErreurSaisie
from edt.services.referentiels.reglages import (
    ParametreAffiche,
    enregistrer_parametres_saisis,
    parametres_affichables,
)
from edt.services.regles import lister as lister_regles
from edt.web.dependances import SessionRequete
from edt.web.donnees.ecrans import Ecran, Formulaire, SuiteCreation, routeur_ecran
from edt.web.gabarits import rediriger, rendre
from edt.web.menu import SECTION_CONFIGURATION, EntreeMenu

journal = logging.getLogger(__name__)

PARENT_CALENDRIER = ("Retour au calendrier", "/calendrier")


def suite_creation_annee(session: Session, annee: Mapping[str, Any]) -> SuiteCreation | None:
    """Une année créée par le formulaire a reçu ses règles proposées : le dire, y mener.

    La création les propose (crochet `apres_creation` du service) ; on compte ici
    les règles proposées de l'année, qui vient de naître : ce sont exactement
    celles-là. On arrive sur leur liste, où les confirmer ou les écarter.
    """
    nombre = len(lister_regles(session, annee["id"], etat=EtatRegle.PROPOSEE))
    if nombre == 0:
        return None
    adresse = "/regles?" + urlencode({"annee": annee["id"], "etat": EtatRegle.PROPOSEE.value})
    texte = (
        "1 règle personnalisée proposée pour cette année, ci-dessous : sans effet tant "
        "qu'elle n'est pas confirmée."
        if nombre == 1
        else f"{nombre} règles personnalisées proposées pour cette année, ci-dessous : sans "
        "effet tant qu'elles ne sont pas confirmées."
    )
    return SuiteCreation(texte, adresse)


ECRAN_ANNEES = Ecran(
    ANNEE_SCOLAIRE,
    "/calendrier/annees",
    introduction="Les années scolaires. Supprimer une année supprime tout ce qui lui appartient.",
    parent=PARENT_CALENDRIER,
    suite_creation=suite_creation_annee,
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
        (
            "jours_travailles",
            "ouverture_min",
            "fermeture_min",
            "pause_min",
            "dejeuner_mode",
            "dejeuner_debut_min",
            "dejeuner_fin_min",
            "dejeuner_duree_min",
        ),
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
    annee_debut = entier_naturel(brute)
    if annee_debut is None:
        journal.info("Ajout d'une année refusé : année de début « %s » invalide", brute[:30])
        return rediriger(
            request,
            "/calendrier",
            f"Année de début invalide : « {brute[:30]} ». Saisir une année, par exemple 2027.",
            "erreur",
        )
    try:
        annee = creer_annee_par_defaut(session, annee_debut)
    except ErreurRegleMetier as refus:
        return rediriger(request, "/calendrier", refus.message, "erreur")
    return rediriger(
        request,
        "/calendrier",
        f"Année scolaire {annee['libelle']} créée avec ses deux semestres et ses jours fériés. "
        + texte_regles_proposees(annee["regles_proposees"]),
    )


def texte_regles_proposees(nombre: int) -> str:
    """« 2 règles proposées, à revoir dans Règles personnalisées. » (ou rien à revoir)."""
    if nombre == 0:
        return "Aucune règle personnalisée à proposer."
    if nombre == 1:
        return "1 règle proposée, à revoir dans Règles personnalisées."
    return f"{nombre} règles proposées, à revoir dans Règles personnalisées."


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
    erreur: ErreurDonneesInvalides | ErreurCalculEnCours | None = None,
) -> Response:
    parametres = parametres_affichables(session)
    if isinstance(erreur, ErreurCalculEnCours):
        statut = 409
    else:
        statut = 422 if erreur is not None else 200
    return rendre(
        request,
        "donnees/parametres.html",
        {
            "rubriques": _rubriques_parametres(parametres),
            "saisies": saisies or {},
            "erreurs": erreur.champs if isinstance(erreur, ErreurSaisie) else {},
            "erreur": erreur,
            "unites": UNITES,
            # Prévenir avant la saisie (le refus, lui, s'affiche à la place).
            "calcul_en_cours": generation.calcul_en_cours() if erreur is None else None,
        },
        statut=statut,
    )


def _refus_pendant_un_calcul() -> ErreurCalculEnCours | None:
    """Refus de changer les paramètres pendant un calcul, ou None s'il n'y en a pas.

    Un calcul a lu les paramètres à son lancement et les relit pour contrôler
    puis dater sa solution : les changer entre-temps donnerait une version
    vérifiée avec d'autres réglages que ceux du calcul (comme la pause
    déjeuner réglée depuis « Générer », refusée elle aussi).
    """
    suivi = generation.calcul_en_cours()
    if suivi is None:
        return None
    journal.info("Paramètres non modifiés : calcul n° %d en cours", suivi.id)
    return ErreurCalculEnCours(
        f"Un calcul d'emploi du temps est en cours (calcul n° {suivi.id}) : les paramètres ne "
        "changent pas pendant un calcul. Attendre qu'il se termine, ou l'arrêter, puis "
        "enregistrer de nouveau. Rien n'a été enregistré."
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
        elif parametre.est_jours:
            # Cases décochées : absentes de l'envoi ; aucune case = ensemble vide, refusé.
            saisies[parametre.cle] = ",".join(formulaire.getlist(parametre.cle))
        elif parametre.cle in formulaire:
            saisies[parametre.cle] = str(formulaire[parametre.cle])
    refus = _refus_pendant_un_calcul()
    if refus is not None:
        return _rendre_parametres(request, session, saisies, refus)
    try:
        enregistrer_parametres_saisis(session, saisies)
    except ErreurDonneesInvalides as erreur:
        session.rollback()
        return _rendre_parametres(request, session, saisies, erreur)
    return rediriger(request, "/parametres", "Paramètres enregistrés.")


@routeur.post("/parametres/defaut", name="parametres_defaut")
def parametres_par_defaut(request: Request, session: SessionRequete) -> Response:
    """Remet tous les paramètres à leur valeur par défaut."""
    refus = _refus_pendant_un_calcul()
    if refus is not None:
        return _rendre_parametres(request, session, erreur=refus)
    reinitialiser_parametres(session)
    return rediriger(
        request, "/parametres", "Tous les paramètres ont repris leur valeur par défaut."
    )


for _ecran in (ECRAN_ANNEES, ECRAN_SEMESTRES, ECRAN_JOURS_FERMES, ECRAN_GRILLE):
    routeur.include_router(routeur_ecran(_ecran))
