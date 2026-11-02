"""Commandes d'échange de données : gabarit, import, exports.

    python -m edt gabarit --format xlsx
    python -m edt importer donnees.xlsx --simuler
    python -m edt importer salles.csv enseignants.csv
    python -m edt importer donnees.xlsx --tout-remplacer
    python -m edt importer edt.ics --mode reference --semestre 1
    python -m edt exporter-donnees --format zip
    python -m edt exporter-edt --version 3 --format ics --couche date --groupe A1-G1

Les commandes appellent `edt.services.echange`, comme l'interface web.
`importer` affiche le bilan et le rapport ligne par ligne, et sort avec le
code 1 s'il y a des rejets (les lignes valides sont tout de même entrées,
sauf avec `--simuler`).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer

from edt.db.moteur import session_base
from edt.echange.donnees import ENTIER_SQLITE_MAX
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable
from edt.services import echange as service
from edt.services.echange import FichierEnvoye, FichierProduit, ModeImport

commandes = typer.Typer()

AIDE_MODE = (
    "donnees (données de l'établissement), reference, verrous ou a_replacer "
    "(emploi du temps existant)."
)


@commandes.command("gabarit")
def gabarit(
    format: Annotated[
        str, typer.Option(help="xlsx (classeur à onglets) ou zip (un CSV par feuille).")
    ] = "xlsx",
    sortie: Annotated[Path | None, typer.Option(help="Fichier ou dossier de destination.")] = None,
) -> None:
    """Écrit le gabarit vide du format d'échange, avec ses explications."""
    with session_base() as session:
        fichier = service.produire_gabarit(session, format)
    _ecrire(fichier, sortie)


@commandes.command("exporter-donnees")
def exporter_donnees(
    format: Annotated[
        str, typer.Option(help="xlsx (classeur à onglets) ou zip (un CSV par feuille).")
    ] = "xlsx",
    sortie: Annotated[Path | None, typer.Option(help="Fichier ou dossier de destination.")] = None,
) -> None:
    """Exporte les données de l'établissement dans le format d'échange (réimportable)."""
    with session_base() as session:
        fichier = service.exporter_donnees(session, format)
    _ecrire(fichier, sortie)


@commandes.command("exporter-edt")
def exporter_edt(
    version: Annotated[
        int, typer.Option("--version", help="Identifiant de la version d'emploi du temps.")
    ],
    format: Annotated[str, typer.Option(help="csv ou ics.")] = "csv",
    couche: Annotated[
        str, typer.Option(help="hebdo (semaine type) ou date (séances datées).")
    ] = "hebdo",
    groupe: Annotated[str | None, typer.Option(help="Code d'un groupe.")] = None,
    enseignant: Annotated[str | None, typer.Option(help="Identifiant d'un enseignant.")] = None,
    salle: Annotated[
        str | None, typer.Option(help="Code d'une salle (« code@campus » si ambigu).")
    ] = None,
    sortie: Annotated[Path | None, typer.Option(help="Fichier ou dossier de destination.")] = None,
) -> None:
    """Exporte l'emploi du temps d'une version en CSV ou en iCal (tout, ou une ressource)."""
    if not 0 < version <= ENTIER_SQLITE_MAX:  # au-delà, la base lèverait OverflowError
        raise ErreurIntrouvable(f"La version d'emploi du temps {version} n'existe pas.")
    with session_base() as session:
        filtre = service.resoudre_filtre(
            session, version, groupe=groupe, enseignant=enseignant, salle=salle
        )
        fichier = service.exporter_emploi_du_temps(session, version, format, couche, **filtre)
    _ecrire(fichier, sortie)


@commandes.command("importer")
def importer(
    fichiers: Annotated[
        list[Path],
        typer.Argument(help="Fichiers CSV, XLSX, ZIP ou ICS (un dossier : tous ses fichiers)."),
    ],
    mode: Annotated[str, typer.Option(help=AIDE_MODE)] = ModeImport.DONNEES.value,
    simuler: Annotated[
        bool, typer.Option("--simuler", help="Montre le bilan et le rapport sans rien écrire.")
    ] = False,
    tout_remplacer: Annotated[
        bool,
        typer.Option(
            "--tout-remplacer",
            help="Supprime d'abord toutes les données de l'établissement (demande confirmation).",
        ),
    ] = False,
    oui: Annotated[
        bool, typer.Option("--oui", help="Confirme « tout remplacer » sans poser la question.")
    ] = False,
    semestre: Annotated[
        int | None,
        typer.Option(help="Semestre de l'emploi du temps importé (défaut : le semestre en cours)."),
    ] = None,
    semaine_type: Annotated[
        str | None,
        typer.Option(help="Une date (JJ/MM/AAAA) de la semaine à retenir comme semaine type."),
    ] = None,
    rapport: Annotated[Path | None, typer.Option(help="Écrit aussi le rapport en CSV.")] = None,
) -> None:
    """Importe des fichiers : données de l'établissement ou emploi du temps existant."""
    options = service.lire_options(mode, semestre, semaine_type, tout_remplacer)
    envois = _lire_fichiers(fichiers)
    confirme = False
    if tout_remplacer and not simuler:
        with session_base() as session:
            comptes = service.compter_remplacement(session)
        detail = (
            ", ".join(f"{n} {libelle}" for libelle, n in comptes.items() if n) or "aucune donnée"
        )
        typer.echo(f"« Tout remplacer » supprimera d'abord : {detail}.")
        confirme = oui or typer.confirm("Confirmer la suppression puis l'import ?", default=False)
        if not confirme:
            typer.echo("Import annulé : rien n'a été modifié.", err=True)
            raise typer.Exit(1)
    options = replace(options, confirmer_remplacement=confirme)
    with session_base() as session:
        if simuler:
            resultat = service.simuler_import(session, envois, options)
        else:
            resultat = service.appliquer_import(session, envois, options)
    _afficher(resultat)
    if rapport is not None:
        _ecrire(service.rapport_telechargeable(resultat), rapport)
    if resultat.a_des_rejets:
        typer.echo(
            f"{resultat.nb_rejets} ligne(s) ou séance(s) non importée(s), "
            f"{resultat.nb_erreurs} erreur(s) dans le rapport.",
            err=True,
        )
        raise typer.Exit(1)


def _lire_fichiers(chemins: list[Path]) -> list[FichierEnvoye]:
    envois = []
    for chemin in chemins:
        if chemin.is_dir():
            membres = sorted(p for p in chemin.iterdir() if p.is_file())
            if not membres:
                raise ErreurDonneesInvalides(f"Le dossier « {chemin} » ne contient aucun fichier.")
        elif chemin.is_file():
            membres = [chemin]
        else:
            raise ErreurDonneesInvalides(f"Fichier introuvable : « {chemin} ».")
        for membre in membres:
            try:
                envois.append(FichierEnvoye(membre.name, membre.read_bytes()))
            except OSError as erreur:
                raise ErreurDonneesInvalides(
                    f"Lecture impossible de « {membre} » : {erreur}"
                ) from erreur
    service.verifier_taille([len(e.contenu) for e in envois])
    return envois


def _afficher(resultat: service.RapportImport) -> None:
    mode = ModeImport(resultat.mode)
    if resultat.simulation:
        typer.echo(f"Simulation de l'import ({mode.libelle}) : rien n'a été écrit.")
    else:
        typer.echo(f"Import appliqué ({mode.libelle}).")
    typer.echo(f"Fichiers : {', '.join(resultat.fichiers)}")
    typer.echo("Bilan :")
    for bilan in resultat.bilans:
        typer.echo(f"  {bilan.objet} : {bilan.texte}")
    if resultat.version_id is not None:
        typer.echo(f"Version d'emploi du temps créée : n° {resultat.version_id}")
    for phrase in resultat.semaines_types:
        typer.echo(phrase)
    if resultat.besoins:
        typer.echo("Besoins reconstitués :")
        for besoin in resultat.besoins:
            typer.echo(
                f"  [{besoin['etat']}] {besoin['type']} {besoin['matiere']} {besoin['libelle']} "
                f"- {besoin['groupes']} - {besoin['enseignants']} - {besoin['duree']}, "
                f"{besoin['volume']} - {besoin['creneaux']}"
            )
    if resultat.signalements:
        typer.echo(f"Rapport ({len(resultat.signalements)} ligne(s)) :")
        for s in resultat.signalements:
            position = f"{s.position} : " if s.position else ""
            typer.echo(f"  [{s.gravite.value}] {position}{s.message}")


def _ecrire(fichier: FichierProduit, sortie: Path | None) -> None:
    chemin = Path(fichier.nom) if sortie is None else sortie
    if chemin.is_dir():
        chemin = chemin / fichier.nom
    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_bytes(fichier.contenu)
    except OSError as erreur:
        raise ErreurDonneesInvalides(f"Écriture impossible de « {chemin} » : {erreur}") from erreur
    typer.echo(f"Fichier écrit : {chemin} ({len(fichier.contenu)} octets)")
