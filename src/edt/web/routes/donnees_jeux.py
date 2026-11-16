"""Écran « Données > Établissement fictif » : charger un établissement fictif, remettre à zéro.

Les deux actions passent par `edt.services.jeux`, qui ouvre ses propres
sessions : une remise à zéro supprime les tables et ne doit jamais se faire
pendant qu'une session de requête est ouverte. Ces routes n'utilisent donc
pas `SessionRequete`. Pendant un calcul, les deux sont refusées
(`ErreurCalculEnCours`) et la page l'explique.

Un chargement réussi redirige vers la page de son compte rendu
(`/jeux/resultat/<numéro>`, POST puis GET) : un rechargement de cette page
la réaffiche sans renvoyer le formulaire, donc sans second chargement. Le
compte rendu du dernier chargement est gardé en mémoire du processus
jusqu'au chargement suivant ; une adresse périmée (autre chargement depuis,
serveur redémarré) ramène à la page des établissements fictifs.
"""

from __future__ import annotations

import itertools
import logging
import threading

from fastapi import APIRouter, Request
from fastapi.responses import Response

from edt.config import charger_configuration
from edt.erreurs import ErreurCalculEnCours, ErreurDonneesInvalides, ErreurRegleMetier
from edt.services.emploi_du_temps import prechauffage
from edt.services.etat import EtatBase, diagnostiquer_base
from edt.services.jeux import (
    ErreurChargementEnCours,
    operation_en_cours,
    recharger_jeu_fictif,
    remettre_a_zero,
    tailles_disponibles,
)
from edt.services.referentiels.conversion import entier_naturel
from edt.synthese.configuration import GRAINE_MAX
from edt.synthese.rapport import RapportJeu
from edt.web.donnees.ecrans import Formulaire
from edt.web.gabarits import Message, rediriger, rendre
from edt.web.menu import SECTION_DONNEES, EntreeMenu

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

_numeros = itertools.count(1)
_dernier_chargement: tuple[str, RapportJeu] | None = None
"""Numéro et rapport du dernier chargement réussi (affiché par `resultat`)."""
_verrou_dernier = threading.Lock()

routeur = APIRouter(prefix="/jeux")
MENU = [
    EntreeMenu("Établissement fictif", "/jeux", section=SECTION_DONNEES, ordre=430),
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
    messages = [message] if message else []
    if message is None and operation_en_cours():
        # Page rechargée pendant un chargement : le dire, plutôt qu'une base qui paraît vide.
        messages.append(
            Message(
                "Un chargement d'établissement fictif ou une remise à zéro est en cours : "
                "attendre quelques secondes, puis recharger la page.",
                "attention",
            )
        )
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
        messages=messages,
    )


def _nombre(n: int, singulier: str, pluriel: str) -> str:
    return f"{n} {singulier if n <= 1 else pluriel}"


def synthese(rapport: RapportJeu) -> list[str]:
    """Ce qui a été chargé, en quatre lignes lisibles (le résumé complet reste replié)."""
    c = rapport.compteurs.get
    proposees = sum(1 for regle in rapport.regles if regle.etat == "proposee")
    regles = _nombre(len(rapport.regles), "règle personnalisée", "règles personnalisées")
    return [
        f"{_nombre(c('campus', 0), 'campus', 'campus')}, {_nombre(c('salle', 0), 'salle', 'salles')}",
        f"{_nombre(c('groupe', 0), 'groupe', 'groupes')}, "
        f"{_nombre(c('etudiant', 0), 'étudiant', 'étudiants')}",
        f"{_nombre(c('enseignant', 0), 'enseignant', 'enseignants')}, "
        f"{_nombre(c('matiere', 0), 'matière', 'matières')}, "
        f"{_nombre(rapport.nb_besoins, 'besoin', 'besoins')} d'enseignement",
        f"{regles} d'exemple"
        + (f", dont {_nombre(proposees, 'proposée', 'proposées')} à activer" if proposees else ""),
    ]


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
    # Les écrans suivants (accueil, « Points à traiter », Générer) sont préparés pendant la lecture.
    prechauffage.lancer(charger_configuration().db_url)
    global _dernier_chargement
    with _verrou_dernier:
        numero = str(next(_numeros))
        _dernier_chargement = (numero, rapport)
    return rediriger(
        request,
        f"/jeux/resultat/{numero}",
        f"Établissement fictif « {saisies['taille']} » chargé : "
        f"{rapport.compteurs.get('salle', 0)} salles, "
        f"{rapport.compteurs.get('enseignant', 0)} enseignants, "
        f"{rapport.nb_besoins} besoins.",
    )


@routeur.get("/resultat/{numero}", name="jeux_resultat")
def resultat(request: Request, numero: str) -> Response:
    """Compte rendu du dernier chargement ; une adresse périmée ramène à la page des jeux."""
    with _verrou_dernier:
        dernier = _dernier_chargement
    if dernier is None or dernier[0] != numero:
        journal.info("Compte rendu de chargement n° %s demandé : plus disponible", numero[:30])
        return rediriger(
            request,
            "/jeux",
            "Ce compte rendu de chargement n'est plus disponible (un autre chargement a eu "
            "lieu depuis, ou le serveur a redémarré) : l'état actuel de la base est affiché "
            "ci-dessous.",
            "info",
        )
    rapport = dernier[1]
    return rendre(
        request,
        "donnees/jeux_resultat.html",
        {"rapport": rapport, "resume": rapport.resume(), "synthese": synthese(rapport)},
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
