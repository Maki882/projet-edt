"""Écran « Données > Jeux de données » : charger un établissement fictif, remettre à zéro.

Les deux actions passent par `edt.services.jeux`, qui ouvre ses propres
sessions : une remise à zéro supprime les tables et ne doit jamais se faire
pendant qu'une session de requête est ouverte. Ces routes n'utilisent donc
pas `SessionRequete`. Pendant un calcul, les deux sont refusées
(`ErreurCalculEnCours`) et la page l'explique.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import Response

from edt.config import charger_configuration
from edt.erreurs import ErreurCalculEnCours, ErreurDonneesInvalides, ErreurRegleMetier
from edt.services.emploi_du_temps import prechauffage
from edt.services.etat import EtatBase, diagnostiquer_base
from edt.services.jeux import (
    ErreurChargementEnCours,
    recharger_jeu_fictif,
    remettre_a_zero,
    tailles_disponibles,
)
from edt.services.referentiels.conversion import entier_naturel
from edt.synthese.configuration import GRAINE_MAX
from edt.web.donnees.ecrans import Formulaire
from edt.web.gabarits import Message, rediriger, rendre
from edt.web.menu import GROUPE_JEUX, SECTION_DONNEES, EntreeMenu

journal = logging.getLogger(__name__)

TAILLE_PAR_DEFAUT = "moyen"
ENTITES_SAISIES = (
    "Campus",
    "Salles",
    "Enseignants",
    "Groupes",
    "Étudiants",
    "Matières",
    "Besoins",
    "Indisponibilités",
    "Versions d'emploi du temps",
    "Calculs",
)
"""Compteurs qui révèlent des données saisies ou chargées (au-delà des données par défaut)."""

routeur = APIRouter(prefix="/jeux")
MENU = [
    EntreeMenu("Jeux de données", "/jeux", section=SECTION_DONNEES, ordre=480, groupe=GROUPE_JEUX),
]


def donnees_presentes(etat: EtatBase) -> bool:
    """Vrai si la base contient des données au-delà des données par défaut."""
    return etat.initialisee and any(etat.compteurs.get(nom) for nom in ENTITES_SAISIES)


def _rendre(
    request: Request,
    *,
    saisies: dict[str, str] | None = None,
    erreurs: dict[str, str] | None = None,
    message: Message | None = None,
    statut: int = 200,
) -> Response:
    etat = diagnostiquer_base()
    return rendre(
        request,
        "donnees/jeux.html",
        {
            "etat": etat,
            "contenu": [(nom, n) for nom, n in etat.compteurs.items() if n],
            "donnees_presentes": donnees_presentes(etat),
            "tailles": tailles_disponibles(),
            "saisies": {"taille": TAILLE_PAR_DEFAUT, "graine": "0", **(saisies or {})},
            "erreurs": erreurs or {},
        },
        statut=statut,
        messages=[message] if message else (),
    )


@routeur.get("", name="jeux")
def jeux(request: Request) -> Response:
    """Page des jeux de données."""
    return _rendre(request)


@routeur.post("/charger", name="jeux_charger")
def charger(request: Request, formulaire: Formulaire) -> Response:
    """Charge un établissement fictif (après remise à zéro si la base contient des données)."""
    saisies = {
        "taille": str(formulaire.get("taille", "")),
        "graine": str(formulaire.get("graine", "")).strip() or "0",
        "reference": "1" if "reference" in formulaire else "",
    }
    erreurs: dict[str, str] = {}
    if saisies["taille"] not in {t.nom for t in tailles_disponibles()}:
        erreurs["taille"] = "Choisir une taille."
    graine = entier_naturel(saisies["graine"])
    if graine is None or graine > GRAINE_MAX:
        erreurs["graine"] = f"Nombre entier positif attendu : de 0 à {GRAINE_MAX} (0 par défaut)."
    etat = diagnostiquer_base()
    effacer = donnees_presentes(etat)
    if effacer and "confirmation" not in formulaire:
        erreurs["confirmation"] = (
            "Cochez la case pour confirmer l'effacement des données actuelles."
        )
    if erreurs:
        journal.info("Chargement d'un jeu fictif refusé : %s", erreurs)
        return _rendre(request, saisies=saisies, erreurs=erreurs, statut=422)

    try:
        rapport = recharger_jeu_fictif(
            saisies["taille"],
            graine,
            reinitialiser=effacer or not etat.initialisee,
            enregistrer_reference=bool(saisies["reference"]),
        )
    except (ErreurCalculEnCours, ErreurChargementEnCours) as refus:
        journal.warning("Chargement d'un jeu fictif refusé : %s", refus.message)
        return _rendre(
            request, saisies=saisies, message=Message(refus.message, "attention"), statut=409
        )
    except (ErreurRegleMetier, ErreurDonneesInvalides) as refus:
        journal.warning("Chargement d'un jeu fictif refusé : %s", refus.message)
        return _rendre(
            request, saisies=saisies, message=Message(refus.message, "erreur"), statut=409
        )
    journal.info(
        "Jeu fictif « %s » (graine %d) chargé depuis l'interface en %.1f s",
        saisies["taille"],
        rapport.graine,
        rapport.duree_s,
    )
    # Les écrans suivants (accueil, « À traiter », Générer) sont préparés pendant la lecture.
    prechauffage.lancer(charger_configuration().db_url)
    return rendre(
        request,
        "donnees/jeux_resultat.html",
        {"rapport": rapport, "resume": rapport.resume()},
        messages=[
            Message(
                f"Établissement fictif « {saisies['taille']} » chargé : "
                f"{rapport.compteurs.get('salle', 0)} salles, "
                f"{rapport.compteurs.get('enseignant', 0)} enseignants, "
                f"{rapport.nb_besoins} besoins.",
                "succes",
            )
        ],
    )


@routeur.post("/remise-a-zero", name="jeux_remise_a_zero")
def remise_a_zero(request: Request, formulaire: Formulaire) -> Response:
    """Supprime toutes les données et recrée les données par défaut, après confirmation."""
    if "confirmation_zero" not in formulaire:
        return _rendre(
            request,
            erreurs={"confirmation_zero": "Cochez la case pour confirmer l'effacement."},
            statut=422,
        )
    try:
        remettre_a_zero()
    except ErreurRegleMetier as refus:
        # Calcul en cours, ou chargement / remise à zéro déjà en cours.
        journal.warning("Remise à zéro refusée : %s", refus.message)
        return _rendre(request, message=Message(refus.message, "attention"), statut=409)
    return rediriger(
        request,
        "/jeux",
        "La base a été remise à zéro : il ne reste que les données par défaut "
        "(grille, listes, paramètres, année scolaire en cours).",
    )
