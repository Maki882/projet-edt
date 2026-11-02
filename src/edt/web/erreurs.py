"""Traduction des erreurs en réponses lisibles.

Aucun écran n'affiche d'erreur brute :

- une erreur métier (`ErreurEDT`) devient une page avec son message et ses
  détails, au statut HTTP correspondant à sa classe ;
- une erreur HTTP (page inconnue, méthode refusée) et une saisie invalide
  deviennent une page explicite ;
- une violation d'intégrité de la base (doublon, élément encore utilisé)
  devient un refus expliqué ;
- une panne du pilote de base (base occupée, non initialisée, fichier qui
  n'est pas une base SQLite, en lecture seule, disque plein, inaccessible)
  devient un 503 qui dit quoi faire ;
- toute autre exception est interceptée par l'intergiciel
  (`edt.web.intergiciels`), journalisée avec sa trace, et rendue par la même
  page d'erreur avec l'identifiant de la requête.

Selon le demandeur, la même erreur est rendue en page complète, en fragment
de message (requête HTMX, affiché dans la zone de messages par
`static/js/app.js`) ou en JSON (client qui demande du JSON).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import DatabaseError, IntegrityError
from starlette.exceptions import HTTPException as ErreurHTTP

from edt.erreurs import (
    ErreurConfiguration,
    ErreurDonneesInvalides,
    ErreurEDT,
    ErreurInfaisable,
    ErreurIntrouvable,
    ErreurNonLivre,
    ErreurRegleMetier,
    ErreurTempsDepasse,
)
from edt.journal import contexte_requete
from edt.services.jeux import operation_en_cours
from edt.web.gabarits import est_htmx, gabarits

journal = logging.getLogger(__name__)

STATUTS: tuple[tuple[type[ErreurEDT], int, str], ...] = (
    (ErreurIntrouvable, 404, "Élément introuvable"),
    (ErreurDonneesInvalides, 422, "Données invalides"),
    (ErreurRegleMetier, 409, "Opération refusée"),
    (ErreurInfaisable, 422, "Emploi du temps impossible"),
    (ErreurTempsDepasse, 422, "Temps de calcul dépassé"),
    (ErreurNonLivre, 501, "Fonction pas encore disponible"),
    (ErreurConfiguration, 500, "Configuration invalide"),
)
"""Statut HTTP et titre de page par classe d'erreur métier (la première qui correspond)."""

TITRES_HTTP = {
    404: ("Page introuvable", "Cette page n'existe pas ou n'existe plus."),
    405: ("Action non permise", "Cette action n'est pas permise sur cette page."),
}


def veut_du_json(request: Request) -> bool:
    """Vrai si le client attend du JSON plutôt qu'une page."""
    accepte = request.headers.get("accept", "")
    return "application/json" in accepte and "text/html" not in accepte


def reponse_erreur(
    request: Request,
    *,
    statut: int,
    titre: str,
    message: str,
    details: Iterable[str] = (),
) -> Response:
    """Construit la réponse d'erreur adaptée au demandeur (page, fragment HTMX ou JSON)."""
    id_requete = contexte_requete.get() or getattr(request.state, "id_requete", None)
    details = tuple(details)
    if veut_du_json(request):
        return JSONResponse(
            {"erreur": titre, "message": message, "details": details, "id_requete": id_requete},
            status_code=statut,
        )
    contexte = {
        "titre": titre,
        "message": message,
        "details": details,
        "statut": statut,
        "id_requete": id_requete,
        "menu": getattr(request.app.state, "menu", ()),
        "chemin": request.url.path,
        "messages": [],
    }
    gabarit = "erreur_fragment.html" if est_htmx(request) else "erreur.html"
    return gabarits.TemplateResponse(request, gabarit, contexte, status_code=statut)


def _statut_et_titre(erreur: ErreurEDT) -> tuple[int, str]:
    for classe, statut, titre in STATUTS:
        if isinstance(erreur, classe):
            return statut, titre
    return 400, "Opération impossible"


async def _erreur_metier(request: Request, erreur: ErreurEDT) -> Response:
    statut, titre = _statut_et_titre(erreur)
    journal.warning("Erreur métier (%s) : %s", type(erreur).__name__, erreur)
    return reponse_erreur(
        request, statut=statut, titre=titre, message=erreur.message, details=erreur.details
    )


async def _erreur_http(request: Request, erreur: ErreurHTTP) -> Response:
    titre, message = TITRES_HTTP.get(
        erreur.status_code, ("Requête refusée", "Cette demande n'a pas pu être traitée.")
    )
    journal.info("Erreur HTTP %d sur %s", erreur.status_code, request.url.path)
    return reponse_erreur(request, statut=erreur.status_code, titre=titre, message=message)


async def _saisie_invalide(request: Request, erreur: RequestValidationError) -> Response:
    details = []
    for anomalie in erreur.errors():
        champ = " / ".join(str(e) for e in anomalie.get("loc", ()) if e not in ("body", "query"))
        details.append(f"{champ or 'requête'} : valeur manquante ou invalide")
    journal.warning("Saisie invalide sur %s : %s", request.url.path, erreur.errors())
    return reponse_erreur(
        request,
        statut=422,
        titre="Saisie invalide",
        message="Certaines valeurs envoyées sont manquantes ou invalides.",
        details=details,
    )


async def _integrite(request: Request, erreur: IntegrityError) -> Response:
    journal.warning("Violation d'intégrité sur %s : %s", request.url.path, erreur.orig)
    return reponse_erreur(
        request,
        statut=409,
        titre="Opération refusée",
        message=(
            "Cette opération laisserait les données incohérentes : soit un élément identique "
            "existe déjà, soit l'élément est encore utilisé ailleurs (par un besoin, par "
            "exemple). Rien n'a été modifié."
        ),
    )


async def _base_indisponible(request: Request, erreur: DatabaseError) -> Response:
    """Base inutilisable : occupée (opération en cours, verrou), non initialisée, corrompue,
    en lecture seule, sur un disque plein, ou inaccessible.

    Pendant un chargement de jeu fictif ou une remise à zéro, les tables sont
    supprimées puis recréées : une requête qui tombe à ce moment-là reçoit
    « occupée, réessayer », pas le conseil d'initialiser la base. Un fichier
    qui n'est pas une base SQLite (`sqlite3.DatabaseError`, qui n'est pas une
    `OperationalError`) reçoit le même traitement, avec son remède.
    """
    cause = str(erreur.orig or erreur).lower()
    if operation_en_cours() or "locked" in cause:
        journal.warning("Base de données occupée sur %s : %s", request.url.path, erreur.orig)
        return reponse_erreur(
            request,
            statut=503,
            titre="Base de données occupée",
            message=(
                "La base de données est momentanément occupée par une autre opération "
                "(chargement d'un jeu de données, remise à zéro ou calcul). Rien n'a été "
                "modifié : réessayer dans quelques secondes."
            ),
        )
    journal.exception("Base de données indisponible sur %s", request.url.path)
    titre = "Base de données indisponible"
    if "no such table" in cause:
        message = (
            "La base de données n'est pas prête : elle n'est pas initialisée. Lancer "
            "« python -m edt init-db », ou la remettre à zéro dans Données > Jeux de "
            "données, puis réessayer."
        )
    elif "not a database" in cause or "malformed" in cause:
        message = (
            "Le fichier de la base de données est illisible : ce n'est pas une base SQLite, "
            "ou il est corrompu. Restaurer une sauvegarde, ou recréer la base avec "
            "« python -m edt init-db --reset » (les données sont perdues), puis réessayer."
        )
    elif "readonly" in cause or "read-only" in cause:
        titre = "Base de données en lecture seule"
        message = (
            "La base de données est en lecture seule : rien n'a été enregistré. À faire par "
            "l'administrateur du serveur : rendre le fichier de la base et son dossier "
            "inscriptibles par l'utilisateur du service (SQLite y crée ses fichiers -wal et "
            "-shm), puis réessayer ; « python -m edt doctor » vérifie l'écriture."
        )
    elif "is full" in cause:
        titre = "Espace disque épuisé"
        message = (
            "Le disque de la base de données est plein : rien n'a été enregistré. À faire par "
            "l'administrateur du serveur : libérer de l'espace disque (anciens journaux, "
            "sauvegardes), puis réessayer ; « python -m edt doctor » vérifie l'écriture."
        )
    else:
        message = (
            "La base de données n'est pas accessible. Réessayer dans quelques instants ; si "
            "le problème persiste, communiquer la référence ci-dessous à l'administrateur."
        )
    return reponse_erreur(request, statut=503, titre=titre, message=message)


def installer_gestionnaires(app: FastAPI) -> None:
    """Enregistre les gestionnaires d'erreurs sur l'application."""
    app.add_exception_handler(ErreurEDT, _erreur_metier)
    app.add_exception_handler(ErreurHTTP, _erreur_http)
    app.add_exception_handler(RequestValidationError, _saisie_invalide)
    app.add_exception_handler(IntegrityError, _integrite)
    app.add_exception_handler(DatabaseError, _base_indisponible)
