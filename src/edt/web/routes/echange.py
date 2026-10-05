"""Écrans d'échange de données : import (simulation puis application) et exports.

- `GET /import` : dépôt d'un ou plusieurs fichiers, choix du mode ;
- `POST /import/simuler` : simulation (rien n'est écrit) : aperçu des 20
  premières lignes de chaque tableau, bilan, rapport, bouton d'application ;
- `POST /import/appliquer` : application ; mêmes fichiers, renvoyés par le
  navigateur (aucun fichier n'est conservé sur le serveur) ;
- `GET /export` : gabarit vide, données, emploi du temps ;
- `GET /export/gabarit.{xlsx|zip}`, `GET /export/donnees.{xlsx|zip}` ;
- `GET /export/emploi-du-temps.csv` et `GET /export/emploi-du-temps.ics` :
  paramètres `version_id` (obligatoire), `couche` (`hebdo` ou `date`), et au
  plus un de `groupe_id`, `enseignant_id`, `salle_id`.

Les fichiers produits sont construits en mémoire avant la réponse (la
session de la requête est fermée au retour de la route). Envoi limité à 20 Mo.
"""

from __future__ import annotations

import base64
import logging
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable
from edt.services import echange as service
from edt.services.calendrier import semestre_par_defaut
from edt.services.echange import FichierEnvoye, FichierProduit, ModeImport, RapportImport
from edt.web.dependances import SessionRequete
from edt.web.gabarits import Message, est_htmx, rendre
from edt.web.menu import SECTION_DONNEES, EntreeMenu

journal = logging.getLogger(__name__)

routeur = APIRouter()
MENU = [
    EntreeMenu("Import", "/import", section=SECTION_DONNEES, ordre=410),
    EntreeMenu("Export", "/export", section=SECTION_DONNEES, ordre=420),
]

MARGE_ENTETES_OCTETS = 512 * 1024
"""Marge tolérée au-delà de 20 Mo pour l'enveloppe du formulaire (en-têtes des parties)."""
MAX_FICHIERS = 50
EXTENSIONS = ".csv,.txt,.tsv,.xlsx,.zip,.ics"


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #


@routeur.get("/import", response_class=HTMLResponse, name="import")
def page_import(request: Request, session: SessionRequete) -> HTMLResponse:
    """Formulaire d'import."""
    return rendre(request, "echange/import.html", _contexte_import(session))


@routeur.post("/import/simuler", response_class=HTMLResponse, name="import_simuler")
async def simuler(request: Request, session: SessionRequete) -> HTMLResponse:
    """Simule l'import : aperçu, bilan et rapport, sans rien écrire."""
    envois, options = await _lire_envoi(request)
    rapport = await run_in_threadpool(service.simuler_import, session, envois, options)
    remplacement = (
        await run_in_threadpool(service.compter_remplacement, session)
        if options.tout_remplacer
        else {}
    )
    resultat = _contexte_resultat(rapport, remplacement)
    if est_htmx(request):
        return rendre(request, "echange/_resultat.html", resultat)
    contexte = await run_in_threadpool(_contexte_import, session)
    return rendre(request, "echange/import.html", contexte | resultat)


@routeur.post("/import/appliquer", response_class=HTMLResponse, name="import_appliquer")
async def appliquer(request: Request, session: SessionRequete) -> HTMLResponse:
    """Applique l'import : les lignes valides entrent, les autres sont listées."""
    envois, options = await _lire_envoi(request)
    rapport = await run_in_threadpool(service.appliquer_import, session, envois, options)
    contexte = await run_in_threadpool(_contexte_import, session)
    if rapport.a_des_rejets:
        message = Message(
            f"Import appliqué : {rapport.nb_rejets} ligne(s) ou séance(s) non importée(s), "
            "voir le rapport.",
            "attention",
        )
    else:
        message = Message("Import appliqué : toutes les lignes sont entrées.", "succes")
    return rendre(
        request,
        "echange/import.html",
        contexte | _contexte_resultat(rapport, {}),
        messages=[message],
    )


async def _lire_envoi(request: Request) -> tuple[list[FichierEnvoye], service.OptionsImport]:
    """Fichiers et options du formulaire ; refuse un envoi de plus de 20 Mo avant de le lire."""
    longueur = request.headers.get("content-length", "")
    if longueur.isdigit() and int(longueur) > service.TAILLE_MAX_OCTETS + MARGE_ENTETES_OCTETS:
        service.verifier_taille([int(longueur)])
    formulaire = await request.form(max_files=MAX_FICHIERS, max_fields=50)
    try:
        envois = []
        total = 0
        for element in formulaire.getlist("fichiers"):
            if not isinstance(element, UploadFile) or not element.filename:
                continue
            contenu = await element.read()
            total += len(contenu)
            service.verifier_taille([total])
            envois.append(FichierEnvoye(element.filename, contenu))
        options = service.lire_options(
            _texte(formulaire.get("mode")),
            _texte(formulaire.get("semestre_id")),
            _texte(formulaire.get("semaine_type")),
            tout_remplacer=bool(formulaire.get("tout_remplacer")),
            confirmer_remplacement=bool(formulaire.get("confirmer_remplacement")),
        )
    finally:
        await formulaire.close()  # les fichiers temporaires du téléversement sont supprimés
    if not envois:
        raise ErreurDonneesInvalides(
            "Aucun fichier envoyé : choisir au moins un fichier à importer."
        )
    return envois, options


def _texte(valeur: object) -> str | None:
    return valeur if isinstance(valeur, str) and valeur.strip() else None


def _contexte_import(session) -> dict:
    try:
        defaut = semestre_par_defaut(session).id
    except ErreurIntrouvable:
        journal.info("Aucun semestre : le choix du semestre d'import reste vide")
        defaut = None
    return {
        "modes": [(mode.value, mode.libelle) for mode in ModeImport],
        "semestres": service.semestres_importables(session),
        "semestre_defaut": defaut,
        "extensions": EXTENSIONS,
        "taille_max_mo": service.TAILLE_MAX_OCTETS // (1024 * 1024),
    }


def _contexte_resultat(rapport: RapportImport, remplacement: dict[str, int]) -> dict:
    """Ce que le gabarit du résultat affiche (aperçu, bilan, rapport, téléchargement)."""
    fichier_rapport = service.rapport_telechargeable(rapport)
    apercus = [
        {
            "titre": f"{a.fichier} - {a.feuille}",
            "nb_lignes": a.nb_lignes,
            "colonnes": [(str(i), entete) for i, entete in enumerate(a.entetes)],
            "lignes": [{str(i): valeur for i, valeur in enumerate(ligne)} for ligne in a.lignes],
        }
        for a in rapport.apercus
    ]
    return {
        "rapport": rapport,
        "mode_libelle": ModeImport(rapport.mode).libelle,
        "apercus": apercus,
        "signalements": [
            {
                "gravite": s.gravite,
                "fichier": s.fichier,
                "feuille": s.feuille,
                "ligne": s.ligne,
                "colonne": s.colonne,
                "message": s.message,
            }
            for s in rapport.signalements
        ],
        "remplacement_texte": ", ".join(
            f"{n} {libelle}" for libelle, n in remplacement.items() if n
        )
        or "aucune donnée",
        "tout_remplacer": bool(remplacement)
        or any(b.objet == "tout remplacer" for b in rapport.bilans),
        "rapport_nom": fichier_rapport.nom,
        "rapport_url": "data:text/csv;charset=utf-8;base64,"
        + base64.b64encode(fichier_rapport.contenu).decode("ascii"),
    }


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #


@routeur.get("/export", response_class=HTMLResponse, name="export")
def page_export(request: Request, session: SessionRequete) -> HTMLResponse:
    """Gabarit vide, données de l'établissement, emploi du temps d'une version."""
    choix = service.choix_export(session)
    return rendre(
        request,
        "echange/export.html",
        {
            "choix": choix,
            "couches": [(couche.value, couche.libelle) for couche in service.Couche],
        },
    )


@routeur.get("/export/gabarit.{format}", name="export_gabarit")
def telecharger_gabarit(format: str, session: SessionRequete) -> Response:
    """Gabarit vide (xlsx ou zip)."""
    return _telecharger(service.produire_gabarit(session, format))


@routeur.get("/export/donnees.{format}", name="export_donnees")
def telecharger_donnees(format: str, session: SessionRequete) -> Response:
    """Données de l'établissement (xlsx ou zip)."""
    return _telecharger(service.exporter_donnees(session, format))


@routeur.get("/export/emploi-du-temps.csv", name="export_edt_csv")
def telecharger_edt_csv(
    session: SessionRequete,
    version_id: Annotated[str, Query()],
    couche: Annotated[str, Query()] = "hebdo",
    groupe_id: Annotated[str | None, Query()] = None,
    enseignant_id: Annotated[str | None, Query()] = None,
    salle_id: Annotated[str | None, Query()] = None,
) -> Response:
    """Emploi du temps d'une version en CSV."""
    return _telecharger_edt(session, "csv", version_id, couche, groupe_id, enseignant_id, salle_id)


@routeur.get("/export/emploi-du-temps.ics", name="export_edt_ics")
def telecharger_edt_ics(
    session: SessionRequete,
    version_id: Annotated[str, Query()],
    couche: Annotated[str, Query()] = "hebdo",
    groupe_id: Annotated[str | None, Query()] = None,
    enseignant_id: Annotated[str | None, Query()] = None,
    salle_id: Annotated[str | None, Query()] = None,
) -> Response:
    """Emploi du temps d'une version en iCal."""
    return _telecharger_edt(session, "ics", version_id, couche, groupe_id, enseignant_id, salle_id)


def _telecharger_edt(
    session,
    format: str,
    version_id: str,
    couche: str,
    groupe_id: str | None,
    enseignant_id: str | None,
    salle_id: str | None,
) -> Response:
    fichier = service.exporter_emploi_du_temps(
        session,
        _identifiant(version_id, "version_id"),
        format,
        couche or "hebdo",
        groupe_id=_identifiant(groupe_id, "groupe_id"),
        enseignant_id=_identifiant(enseignant_id, "enseignant_id"),
        salle_id=_identifiant(salle_id, "salle_id"),
    )
    return _telecharger(fichier)


def _identifiant(texte: str | None, nom: str) -> int | None:
    """Identifiant numérique d'un paramètre (vide = non renseigné)."""
    if texte is None or not texte.strip():
        if nom == "version_id":
            raise ErreurDonneesInvalides("Choisir la version d'emploi du temps à exporter.")
        return None
    if not texte.strip().isdigit():
        raise ErreurDonneesInvalides(f"Paramètre {nom} invalide : {texte!r} (nombre attendu).")
    return int(texte)


def _telecharger(fichier: FichierProduit) -> Response:
    """Réponse de téléchargement, construite entièrement en mémoire."""
    return Response(
        content=fichier.contenu,
        media_type=fichier.type_mime,
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{fichier.nom}\"; filename*=UTF-8''{quote(fichier.nom)}"
            )
        },
    )
