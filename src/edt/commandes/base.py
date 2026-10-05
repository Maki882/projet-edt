"""Commandes du socle : `init-db`, `verifier-schema`, `jeu-minimal`, `serve`, `version`, `doctor`."""

from __future__ import annotations

import logging
import socket
import sys
import uuid
from typing import Annotated

import typer

from edt import __version__
from edt.config import Configuration, charger_configuration
from edt.db.initialisation import (
    MARCHE_A_SUIVRE_SCHEMA_PERIME,
    exiger_schema_a_jour,
    initialiser_base,
    tables_manquantes,
)
from edt.db.jeux import creer_jeu_minimal
from edt.db.moteur import obtenir_moteur, session_base
from edt.erreurs import ErreurEDT, ErreurRegleMetier
from edt.services.etat import EtatBase, diagnostiquer_base

commandes = typer.Typer()
journal = logging.getLogger(__name__)

PYTHON_MINIMUM = (3, 11)
ORTOOLS_MINIMUM = (9, 15)

OK, ATTENTION, ECHEC = "OK", "ATTENTION", "ECHEC"

CODE_SCHEMA_PERIME = 1
CODE_BASE_INACCESSIBLE = 2
"""Codes de sortie de `verifier-schema` (0 : schéma à jour)."""


@commandes.command("version")
def version() -> None:
    """Affiche la version de l'application."""
    typer.echo(f"edt {__version__}")


@commandes.command("init-db")
def init_db(
    reset: Annotated[
        bool,
        typer.Option(
            "--reset", help="Supprime d'abord toutes les tables : les données sont perdues."
        ),
    ] = False,
) -> None:
    """Crée la base, son schéma et les données par défaut (sans risque à relancer).

    Il n'y a pas de migrations : si une table existante n'a plus les colonnes du
    modèle, la commande échoue sans rien modifier, liste les écarts et indique
    de relancer avec --reset.
    """
    rapport = initialiser_base(reset=reset)
    action = "remise à zéro puis initialisée" if rapport.reinitialisee else "initialisée"
    typer.echo(f"Base {action} : {rapport.url}")
    typer.echo(f"  {rapport.nb_tables} tables, {rapport.nb_ajouts} ligne(s) par défaut ajoutée(s)")
    for table, nombre in rapport.ajouts.items():
        if nombre:
            typer.echo(f"  - {table} : {nombre}")


@commandes.command("verifier-schema")
def verifier_schema() -> None:
    """Vérifie que les tables de la base ont les colonnes du modèle (rien n'est modifié).

    Code de sortie 0 si le schéma est à jour, 1 s'il est périmé (les écarts sont
    listés), 2 si la base est inaccessible. Une table pas encore créée n'est pas
    un écart : « init-db » la crée.
    """
    try:
        etat = diagnostiquer_base()
    except ErreurEDT as erreur:
        journal.error("Vérification du schéma impossible : %s", erreur)
        typer.echo(f"Base inaccessible : {erreur}", err=True)
        raise typer.Exit(CODE_BASE_INACCESSIBLE) from erreur
    if not etat.accessible:
        typer.echo(f"Base inaccessible ({etat.url}) : {etat.erreur}", err=True)
        raise typer.Exit(CODE_BASE_INACCESSIBLE)
    if etat.schema_perime:
        typer.echo(
            f"schéma périmé : {len(etat.ecarts_schema)} écart(s) entre la base et le modèle "
            f"({etat.url})"
        )
        for ecart in etat.ecarts_schema:
            typer.echo(f"  - {ecart}")
        typer.echo(MARCHE_A_SUIVRE_SCHEMA_PERIME)
        raise typer.Exit(CODE_SCHEMA_PERIME)
    if etat.tables_manquantes:
        typer.echo(
            f"schéma à jour pour les tables existantes ; {len(etat.tables_manquantes)} table(s) "
            "pas encore créée(s) : lancer « python -m edt init-db »"
        )
    else:
        typer.echo("schéma à jour")


@commandes.command("jeu-minimal")
def jeu_minimal() -> None:
    """Charge un tout petit jeu de données fictif et cohérent dans une base vide."""
    moteur = obtenir_moteur()
    exiger_schema_a_jour(moteur)
    if tables_manquantes(moteur):
        raise ErreurRegleMetier(
            "La base n'est pas initialisée : lancer d'abord « python -m edt init-db »."
        )
    with session_base() as session:
        jeu = creer_jeu_minimal(session)
    typer.echo(
        "Jeu minimal chargé : "
        f"{jeu.nb_campus} campus, {jeu.nb_salles} salles, {jeu.nb_enseignants} enseignants, "
        f"{jeu.nb_groupes} groupes, {jeu.nb_matieres} matières, {jeu.nb_besoins} besoins "
        f"(semestre n°{jeu.semestre_id})."
    )


@commandes.command("serve")
def serve(
    host: Annotated[str | None, typer.Option(help="Adresse d'écoute (défaut : EDT_HOST).")] = None,
    port: Annotated[int | None, typer.Option(help="Port d'écoute (défaut : EDT_PORT).")] = None,
    reload: Annotated[
        bool,
        typer.Option("--reload", help="Recharge le serveur quand le code change (développement)."),
    ] = False,
) -> None:
    """Lance le serveur web (uvicorn sur `edt.web.asgi:app`)."""
    import uvicorn

    config = charger_configuration()
    hote, port_ecoute = host or config.host, port or config.port
    typer.echo(f"Serveur EDT sur http://{hote}:{port_ecoute} (Ctrl+C pour arrêter)")
    # log_config=None : uvicorn n'installe pas sa propre journalisation, ses messages
    # passent par la nôtre ; chaque requête est déjà journalisée par notre intergiciel.
    uvicorn.run(
        "edt.web.asgi:app",
        host=hote,
        port=port_ecoute,
        reload=reload,
        log_config=None,
        access_log=False,
    )


@commandes.command("doctor")
def doctor() -> None:
    """Vérifie l'installation : Python, OR-Tools, base, schéma, dossier des journaux, port."""
    config = charger_configuration()
    etat = _diagnostiquer()
    controles = [
        _controler_python(),
        _controler_ortools(),
        _controler_base(etat),
        _controler_schema(etat),
        _controler_journaux(config),
        _controler_port(config),
    ]
    typer.echo(f"Diagnostic EDT {__version__} (environnement « {config.env} »)")
    for statut, message in controles:
        typer.echo(f"[{statut:^9}] {message}")
    echecs = sum(1 for statut, _ in controles if statut == ECHEC)
    if echecs:
        typer.echo(f"{echecs} contrôle(s) en échec.", err=True)
        raise typer.Exit(1)
    typer.echo("Tout est prêt.")


# --------------------------------------------------------------------------- #
# Contrôles de `doctor` : chacun renvoie (statut, message) et ne lève jamais.
# --------------------------------------------------------------------------- #


def _controler_python() -> tuple[str, str]:
    actuelle = sys.version_info[:3]
    texte = ".".join(map(str, actuelle))
    requis = ".".join(map(str, PYTHON_MINIMUM))
    if actuelle[:2] >= PYTHON_MINIMUM:
        return OK, f"Python {texte} ({requis} ou plus requis)"
    return ECHEC, f"Python {texte} : la version {requis} ou plus est requise"


def _controler_ortools() -> tuple[str, str]:
    try:
        import ortools
        from ortools.sat.python import cp_model
    except ImportError as erreur:
        return ECHEC, f"OR-Tools introuvable ({erreur}) : installer les dépendances du projet"
    version_lue = ortools.__version__
    try:
        numerique = tuple(int(partie) for partie in version_lue.split(".")[:2])
    except ValueError:
        return ECHEC, f"OR-Tools : version illisible ({version_lue!r})"
    requis = ".".join(map(str, ORTOOLS_MINIMUM))
    if numerique < ORTOOLS_MINIMUM:
        return ECHEC, f"OR-Tools {version_lue} : la version {requis} ou plus est requise"

    modele = cp_model.CpModel()
    x = modele.new_int_var(0, 10, "x")
    modele.add(x >= 3)
    modele.minimize(x)
    solveur = cp_model.CpSolver()
    statut = solveur.solve(modele)
    if statut != cp_model.OPTIMAL or solveur.value(x) != 3:
        return ECHEC, f"OR-Tools {version_lue} : le solveur CP-SAT ne répond pas comme attendu"
    return OK, f"OR-Tools {version_lue} ({requis} ou plus requis), solveur CP-SAT opérationnel"


def _diagnostiquer() -> EtatBase | str:
    """État de la base, ou le message d'erreur si elle ne peut même pas être ouverte."""
    try:
        return diagnostiquer_base()
    except ErreurEDT as erreur:
        journal.error("Diagnostic de la base impossible : %s", erreur)
        return str(erreur)


def _controler_base(etat: EtatBase | str) -> tuple[str, str]:
    if isinstance(etat, str):
        return ECHEC, f"Base : {etat}"
    if not etat.accessible:
        return ECHEC, f"Base {etat.url} inaccessible : {etat.erreur}"
    mode = f", mode {etat.mode_journal}" if etat.mode_journal else ""
    return OK, f"Base {etat.url} accessible{mode}"


def _controler_schema(etat: EtatBase | str) -> tuple[str, str]:
    if isinstance(etat, str) or not etat.accessible:
        return ATTENTION, "Schéma non vérifié : la base est inaccessible"
    if etat.schema_perime:
        return (
            ECHEC,
            f"Schéma périmé : {len(etat.ecarts_schema)} écart(s) entre la base et le modèle "
            "(détail : « python -m edt verifier-schema » ; remède : « python -m edt init-db "
            "--reset », qui efface les données)",
        )
    if etat.tables_manquantes:
        return (
            ATTENTION,
            f"Schéma : base non initialisée ({len(etat.tables_manquantes)} table(s) "
            "manquante(s)) : lancer « python -m edt init-db »",
        )
    return OK, "Schéma à jour : les tables de la base correspondent au modèle"


def _controler_journaux(config: Configuration) -> tuple[str, str]:
    dossier = config.log_dir
    temoin = dossier / f".doctor-{uuid.uuid4().hex}"
    try:
        dossier.mkdir(parents=True, exist_ok=True)
        temoin.write_text("ok", encoding="utf-8")
        temoin.unlink()
    except OSError as erreur:
        return ECHEC, f"Journaux : impossible d'écrire dans « {dossier} » ({erreur})"
    return OK, f"Journaux : dossier « {dossier} » accessible en écriture"


def _controler_port(config: Configuration) -> tuple[str, str]:
    adresse = f"{config.host}:{config.port}"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as prise:
            prise.bind((config.host, config.port))
    except OSError as erreur:
        return (
            ATTENTION,
            f"Port {adresse} indisponible ({erreur}) : le serveur tourne peut-être déjà, "
            "sinon choisir un autre EDT_PORT",
        )
    return OK, f"Port {adresse} libre"
