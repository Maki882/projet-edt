"""Commandes générales : `init-db`, `verifier-schema`, `jeu-minimal`, `serve`, `version`,
`doctor`, et l'exploitation de la base : `sauvegarder`, `verifier-sauvegarde`."""

from __future__ import annotations

import datetime as dt
import logging
import re
import shutil
import socket
import sqlite3
import sys
import tempfile
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import create_engine, select, text

from edt import __version__
from edt.config import Configuration, charger_configuration
from edt.db.base import Base
from edt.db.initialisation import (
    MARCHE_A_SUIVRE_SCHEMA_PERIME,
    copier_base,
    ecarts_schema,
    exiger_schema_a_jour,
    initialiser_base,
    sauvegarder_base,
    tables_manquantes,
)
from edt.db.jeux import creer_jeu_minimal
from edt.db.modeles import Campus
from edt.db.moteur import (
    chemin_base,
    expliquer_erreur_base,
    obtenir_moteur,
    session_base,
    transaction_immediate,
)
from edt.erreurs import (
    ErreurConfiguration,
    ErreurDonneesInvalides,
    ErreurEDT,
    ErreurIntrouvable,
    ErreurRegleMetier,
)
from edt.journal import DOSSIER_CALCULS, NOM_FICHIER, panne_journalisation
from edt.services.etat import EtatBase, diagnostiquer_base
from edt.services.jeux import remettre_a_zero

commandes = typer.Typer()
journal = logging.getLogger(__name__)

PYTHON_MINIMUM = (3, 11)
ORTOOLS_MINIMUM = (9, 15)

OK, ATTENTION, ECHEC = "OK", "ATTENTION", "ECHEC"

CODE_SCHEMA_PERIME = 1
CODE_BASE_INACCESSIBLE = 2
"""Codes de sortie de `verifier-schema` (0 : schéma à jour)."""

PREFIXE_SAUVEGARDE = "quotidienne"
"""Préfixe par défaut des sauvegardes de `sauvegarder` (minuteur quotidien du serveur)."""
PREFIXE_RESERVE = "edt"
"""Préfixe des sauvegardes du déploiement (deploy/deployer-distant.sh), qui a sa rotation."""
SAUVEGARDES_A_GARDER = 14
"""Sauvegardes d'un même préfixe gardées par défaut (deux semaines de sauvegardes quotidiennes)."""
MARGE_DISQUE_OCTETS = 10 * 1024 * 1024
"""Place libre exigée en plus de la taille de la base avant d'écrire une sauvegarde."""
AGE_PARTIEL_ABANDONNE_S = 3600.0
"""Âge au-delà duquel un fichier « .partiel » est tenu pour abandonné (échec ancien)."""


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
    oui: Annotated[
        bool,
        typer.Option("--oui", "-y", help="Avec --reset : ne demande pas de confirmation."),
    ] = False,
    sans_sauvegarde: Annotated[
        bool,
        typer.Option(
            "--sans-sauvegarde",
            help=(
                "Avec --reset : ne copie pas la base avant de l'effacer (sauvegarde déjà faite "
                "par l'appelant, comme le déploiement)."
            ),
        ),
    ] = False,
) -> None:
    """Crée la base, son schéma et les données par défaut (sans risque à relancer).

    Il n'y a pas de migrations : si une table existante n'a plus les colonnes du
    modèle, la commande échoue sans rien modifier, liste les écarts et indique
    de relancer avec --reset.

    --reset demande confirmation quand la commande est tapée dans un terminal
    (--oui l'évite ; un script non interactif n'est pas interrogé), refuse
    d'agir pendant un calcul ou pendant un chargement de jeu, et, en
    production, sauvegarde d'abord la base dans le dossier « sauvegardes »
    à côté d'elle (« edt-<date>-avant-reset.db »), sauf avec --sans-sauvegarde.
    """
    sauvegarde = None
    if reset:
        _confirmer_remise_a_zero(oui)

        def sauvegarder() -> None:
            nonlocal sauvegarde
            sauvegarde = sauvegarder_base()

        # Même chemin que l'écran de configuration : verrou, fichier marqueur vu des autres
        # processus, refus pendant un calcul. La sauvegarde suit les refus : une demande
        # refusée n'ajoute pas de copie à la rotation des sauvegardes.
        prod = charger_configuration().est_prod and not sans_sauvegarde
        rapport = remettre_a_zero(avant=sauvegarder if prod else None)
    else:
        rapport = initialiser_base()
    action = "remise à zéro puis initialisée" if rapport.reinitialisee else "initialisée"
    if sauvegarde is not None:
        typer.echo(f"Sauvegarde de l'ancienne base : {sauvegarde}")
    typer.echo(f"Base {action} : {rapport.url}")
    typer.echo(f"  {rapport.nb_tables} tables, {rapport.nb_ajouts} ligne(s) par défaut ajoutée(s)")
    for table, nombre in rapport.ajouts.items():
        if nombre:
            typer.echo(f"  - {table} : {nombre}")
    with session_base() as session:
        vide = session.scalar(select(Campus.id).limit(1)) is None
    if vide:
        typer.echo(
            "Étape suivante : charger des données fictives (« python -m edt jeu --taille moyen », "
            "ou « python -m edt jeu-minimal » pour un tout petit jeu), puis lancer « python -m edt "
            "serve »."
        )
    else:
        typer.echo("Étape suivante : lancer « python -m edt serve ».")


def _entree_interactive() -> bool:
    """Vrai si la commande est tapée dans un terminal (une question peut y être posée)."""
    return sys.stdin is not None and sys.stdin.isatty()


def _confirmer_remise_a_zero(oui: bool) -> None:
    """Demande confirmation d'une remise à zéro tapée dans un terminal (sauf --oui)."""
    if oui:
        return
    if not _entree_interactive():
        journal.warning(
            "Remise à zéro de la base demandée sans confirmation (entrée non interactive)"
        )
        return
    typer.confirm(
        "La remise à zéro supprime TOUTES les données de la base. Continuer ?", abort=True
    )


@commandes.command("verifier-schema")
def verifier_schema() -> None:
    """Vérifie que les tables de la base correspondent au modèle : colonnes, unicités, index.

    Rien n'est modifié.

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
    exiger_schema_a_jour(moteur, rien_modifie=True)
    if tables_manquantes(moteur):
        raise ErreurRegleMetier(
            "La base n'est pas initialisée : lancer d'abord « python -m edt init-db »."
        )
    with session_base() as session:
        transaction_immediate(session)  # écrivain long : lit puis écrit, verrou pris d'emblée
        jeu = creer_jeu_minimal(session)
    typer.echo(
        "Jeu minimal chargé : "
        f"{jeu.nb_campus} campus, {jeu.nb_salles} salles, {jeu.nb_enseignants} enseignants, "
        f"{jeu.nb_groupes} groupes, {jeu.nb_matieres} matières, {jeu.nb_besoins} besoins "
        f"(semestre n°{jeu.semestre_id})."
    )
    typer.echo(
        f"Étape suivante : « python -m edt generer --semestre {jeu.semestre_id} » calcule "
        "l'emploi du temps ; « python -m edt serve » ouvre l'application dans le navigateur."
    )


@commandes.command("serve")
def serve(
    host: Annotated[str | None, typer.Option(help="Adresse d'écoute (défaut : EDT_HOST).")] = None,
    port: Annotated[
        int | None,
        typer.Option(min=1, max=65535, help="Port d'écoute, de 1 à 65535 (défaut : EDT_PORT)."),
    ] = None,
    reload: Annotated[
        bool,
        typer.Option("--reload", help="Recharge le serveur quand le code change (développement)."),
    ] = False,
) -> None:
    """Lance le serveur web (uvicorn sur `edt.web.asgi:app`).

    En production, le service systemd lance uvicorn lui-même (deploy/edt.service) ;
    cette commande sert au poste de développement.
    """
    import uvicorn

    config = charger_configuration()
    hote = config.host if host is None else host.strip()
    if not hote:
        raise ErreurConfiguration("--host ne peut pas être vide (sans l'option : EDT_HOST).")
    port_ecoute = config.port if port is None else port
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
        _controler_ecriture(etat),
        _controler_schema(etat),
        _controler_journaux(config),
        _controler_journaux_calcul(config),
        _controler_port(config),
    ]
    typer.echo(f"Diagnostic EDT {__version__} (environnement « {config.env} »)")
    for statut, message in controles:
        typer.echo(f"[{statut:^9}] {message}")
    echecs = sum(1 for statut, _ in controles if statut == ECHEC)
    avertissements = sum(1 for statut, _ in controles if statut == ATTENTION)
    if echecs:
        typer.echo(
            f"{echecs} contrôle(s) en échec : corriger ce que disent les lignes ECHEC, puis "
            "relancer « python -m edt doctor ».",
            err=True,
        )
        raise typer.Exit(1)
    if avertissements:
        typer.echo(
            f"Aucun échec, {avertissements} avertissement(s) : suivre les indications des lignes "
            "ATTENTION."
        )
        return
    typer.echo("Tout est prêt.")


@commandes.command("sauvegarder")
def sauvegarder(
    dossier: Annotated[
        Path | None,
        typer.Option(
            "--dossier",
            help="Dossier des sauvegardes (défaut : « sauvegardes » à côté de la base).",
        ),
    ] = None,
    garder: Annotated[
        int,
        typer.Option("--garder", min=0, help="Sauvegardes de ce préfixe à conserver (0 : toutes)."),
    ] = SAUVEGARDES_A_GARDER,
    prefixe: Annotated[
        str,
        typer.Option(
            "--prefixe",
            help="Début du nom des fichiers, en lettres minuscules (« edt » est réservé au "
            "déploiement).",
        ),
    ] = PREFIXE_SAUVEGARDE,
) -> None:
    """Sauvegarde la base : copie cohérente, même service en marche, vérifiée avant d'être gardée.

    Le fichier `<préfixe>-<date et heure UTC>.db` est d'abord écrit en « .partiel »,
    puis renommé seulement si son contrôle d'intégrité passe ; en cas d'échec
    (base illisible, disque plein), rien ne reste. Seules les sauvegardes du même
    préfixe tournent : les plus anciennes au-delà de --garder sont supprimées.
    """
    if not re.fullmatch(r"[a-z]+", prefixe) or prefixe == PREFIXE_RESERVE:
        raise ErreurDonneesInvalides(
            f"Préfixe « {prefixe} » refusé : lettres minuscules seulement, et « {PREFIXE_RESERVE} » "
            "est réservé aux sauvegardes du déploiement."
        )
    base = _fichier_de_la_base()
    if not base.is_file():
        raise ErreurIntrouvable(f"Base introuvable : {base} (rien à sauvegarder).")
    dossier = base.parent / "sauvegardes" if dossier is None else dossier
    try:
        dossier.mkdir(parents=True, exist_ok=True)
        libre = shutil.disk_usage(dossier).free
    except OSError as erreur:
        raise ErreurConfiguration(
            f"Dossier des sauvegardes inutilisable « {dossier} » : {erreur}"
        ) from erreur
    besoin = _taille_base(base) + MARGE_DISQUE_OCTETS
    if libre < besoin:
        raise ErreurConfiguration(
            f"Espace disque insuffisant dans « {dossier} » : {_lisible(libre)} libres, "
            f"{_lisible(besoin)} nécessaires. Faire de la place puis relancer."
        )
    cible = dossier / f"{prefixe}-{_maintenant_utc():%Y%m%dT%H%M%SZ}.db"
    if cible.exists():
        raise ErreurRegleMetier(
            f"La sauvegarde {cible.name} existe déjà (deux sauvegardes dans la même seconde) : "
            "relancer dans un instant."
        )
    _supprimer_partiels_abandonnes(dossier, prefixe)
    partiel = cible.with_name(cible.name + ".partiel")
    try:
        nb_tables = copier_base(base, partiel)
        partiel.replace(cible)
    except BaseException:
        partiel.unlink(missing_ok=True)
        raise
    journal.info("Sauvegarde vérifiée : %s (%d tables)", cible, nb_tables)
    typer.echo(
        f"Sauvegarde vérifiée : {cible} ({_lisible(cible.stat().st_size)}, {nb_tables} tables, "
        "intégrité ok)."
    )
    for nom in _faire_tourner(dossier, prefixe, garder):
        typer.echo(f"  ancienne sauvegarde supprimée : {nom}")


@commandes.command("verifier-sauvegarde")
def verifier_sauvegarde(
    fichier: Annotated[Path, typer.Argument(help="Fichier de sauvegarde à contrôler.")],
) -> None:
    """Contrôle une sauvegarde sans la modifier : intégrité, schéma, nombre de lignes par table.

    Le contrôle porte sur une copie temporaire. Code de sortie 0 si la sauvegarde
    peut être restaurée telle quelle avec cette version de l'application, 1 sinon
    (le motif est affiché). Une table du modèle absente de la sauvegarde n'est pas
    un écart : « init-db » la crée après la restauration.
    """
    if not fichier.is_file():
        raise ErreurIntrouvable(f"Sauvegarde introuvable : {fichier}")
    with tempfile.TemporaryDirectory(prefix="edt-verification-") as dossier:
        copie = Path(dossier) / "copie.db"
        shutil.copyfile(fichier, copie)
        nb_tables = _verifier_integrite(copie, fichier)
        manquantes, ecarts, lignes = _comparer_au_modele(copie)
    typer.echo(f"Sauvegarde {fichier} : intégrité ok, {nb_tables} tables.")
    for table, nombre in lignes.items():
        typer.echo(f"  {table} : {nombre} ligne(s)")
    if len(manquantes) == len(Base.metadata.tables):
        typer.echo("Aucune table de l'application : ce n'est pas une sauvegarde de EDT.", err=True)
        raise typer.Exit(1)
    if manquantes:
        typer.echo(
            f"{len(manquantes)} table(s) du modèle absente(s) ({', '.join(manquantes)}) : "
            "« init-db » les créera après la restauration."
        )
    if ecarts:
        typer.echo(
            f"Schéma différent du modèle de cette version ({len(ecarts)} écart(s)) : la sauvegarde "
            "se restaure avec la version qui l'a produite, pas avec celle-ci.",
            err=True,
        )
        for ecart in ecarts:
            typer.echo(f"  - {ecart}", err=True)
        raise typer.Exit(1)
    typer.echo("Schéma conforme au modèle : sauvegarde restaurable avec cette version.")


# --------------------------------------------------------------------------- #
# Contrôles de `doctor` : chacun renvoie (statut, message) et ne lève jamais.
# --------------------------------------------------------------------------- #


def _controler_python() -> tuple[str, str]:
    actuelle = sys.version_info[:3]
    texte = ".".join(map(str, actuelle))
    requis = ".".join(map(str, PYTHON_MINIMUM))
    if actuelle[:2] >= PYTHON_MINIMUM:
        return OK, f"Python {texte} ({requis} ou plus requis)"
    return (
        ECHEC,
        f"Python {texte} : la version {requis} ou plus est requise ; l'installer, puis recréer "
        "l'environnement du projet (README, « Installation »)",
    )


def _controler_ortools() -> tuple[str, str]:
    try:
        import ortools
        from ortools.sat.python import cp_model
    except ImportError as erreur:
        return (
            ECHEC,
            f"OR-Tools introuvable ({erreur}) : installer les dépendances du projet "
            "(« python -m pip install -e . »)",
        )
    version_lue = ortools.__version__
    try:
        numerique = tuple(int(partie) for partie in version_lue.split(".")[:2])
    except ValueError:
        return ECHEC, f"OR-Tools : version illisible ({version_lue!r})"
    requis = ".".join(map(str, ORTOOLS_MINIMUM))
    if numerique < ORTOOLS_MINIMUM:
        return (
            ECHEC,
            f"OR-Tools {version_lue} : la version {requis} ou plus est requise "
            "(« python -m pip install -e . » la met à jour)",
        )

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
        return (
            ECHEC,
            f"Base {etat.url} inaccessible ({etat.erreur}) : "
            f"{expliquer_erreur_base(Exception(etat.erreur))}",
        )
    mode = f", mode {etat.mode_journal}" if etat.mode_journal else ""
    return OK, f"Base {etat.url} accessible{mode}"


def _controler_ecriture(etat: EtatBase | str) -> tuple[str, str]:
    """La base accepte-t-elle une écriture ? Résultat de l'écriture d'essai du diagnostic.

    `services.etat.verifier_ecriture` l'a tentée sans attendre de verrou : une base occupée
    par une autre écriture n'est pas une panne.
    """
    if isinstance(etat, str) or not etat.accessible:
        return ATTENTION, "Écriture non vérifiée : la base est inaccessible"
    if etat.inscriptible is None:
        return (
            ATTENTION,
            "Écriture non vérifiée : une autre écriture occupe la base (calcul, chargement, "
            "import) ; relancer « python -m edt doctor » une fois celle-ci terminée",
        )
    if not etat.inscriptible:
        return ECHEC, f"Écriture dans la base impossible : {etat.ecriture_impossible}"
    return OK, "Écriture dans la base possible (écriture d'essai annulée)"


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
    panne = panne_journalisation()
    if panne:
        return ECHEC, f"Journaux : {panne}"
    dossier = config.log_dir
    temoin = dossier / f".doctor-{uuid.uuid4().hex}"
    try:
        dossier.mkdir(parents=True, exist_ok=True)
        temoin.write_text("ok", encoding="utf-8")
        temoin.unlink()
        with (dossier / NOM_FICHIER).open("a", encoding="utf-8"):
            pass
    except OSError as erreur:
        return (
            ECHEC,
            f"Journaux : impossible d'écrire dans « {dossier} » ({erreur}) : corriger "
            "EDT_LOG_DIR ou les droits de ce dossier",
        )
    return OK, f"Journaux : dossier « {dossier} » et fichier {NOM_FICHIER} accessibles en écriture"


def _controler_journaux_calcul(config: Configuration) -> tuple[str, str]:
    """Le sous-dossier des journaux de calcul doit exister (ou pouvoir être créé) et s'écrire."""
    dossier = config.log_dir / DOSSIER_CALCULS
    temoin = dossier / f".doctor-{uuid.uuid4().hex}"
    try:
        dossier.mkdir(parents=True, exist_ok=True)
        temoin.write_text("ok", encoding="utf-8")
        temoin.unlink()
    except OSError as erreur:
        return (
            ECHEC,
            f"Journaux de calcul : impossible d'écrire dans « {dossier} » ({erreur}) : aucun "
            "calcul n'aurait de journal ; ce chemin doit être un dossier inscriptible",
        )
    return OK, f"Journaux de calcul : dossier « {dossier} » accessible en écriture"


def _controler_port(config: Configuration) -> tuple[str, str]:
    adresse = f"{config.host}:{config.port}"
    try:
        socket.getaddrinfo(config.host, config.port, type=socket.SOCK_STREAM)
    except socket.gaierror as erreur:
        return (
            ECHEC,
            f"Adresse d'écoute « {config.host} » introuvable ({erreur}) : corriger EDT_HOST "
            "(adresse IP ou nom d'hôte de cette machine)",
        )
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


# --------------------------------------------------------------------------- #
# Sauvegarde de la base : outils de `sauvegarder` et `verifier-sauvegarde`.
# --------------------------------------------------------------------------- #


def _maintenant_utc() -> dt.datetime:
    """Heure courante en UTC : un nom de sauvegarde sans heure répétée au changement d'heure."""
    return dt.datetime.now(dt.UTC)


def _fichier_de_la_base() -> Path:
    """Fichier SQLite de la base configurée (`EDT_DB_URL`), tel que l'application l'ouvre.

    `obtenir_moteur` refuse toute URL qui n'est pas un fichier SQLite, avec le remède.
    """
    obtenir_moteur()
    chemin = chemin_base(charger_configuration().db_url)
    if chemin is None:  # impossible après `obtenir_moteur`, gardé pour le typage
        raise ErreurConfiguration("EDT_DB_URL ne désigne pas un fichier SQLite.")
    return chemin


def _taille_base(base: Path) -> int:
    """Taille de la base, journal WAL compris (ce que la sauvegarde va recopier au plus)."""
    wal = base.with_name(base.name + "-wal")
    return base.stat().st_size + (wal.stat().st_size if wal.exists() else 0)


def _lisible(octets: int) -> str:
    """Taille lisible : « 3,2 Mo »."""
    return f"{octets / (1024 * 1024):.1f} Mo".replace(".", ",")


def _supprimer_partiels_abandonnes(dossier: Path, prefixe: str) -> None:
    """Retire les fichiers « .partiel » de ce préfixe laissés par un échec ancien."""
    limite = time.time() - AGE_PARTIEL_ABANDONNE_S
    for chemin in dossier.glob(f"{prefixe}-*.db.partiel"):
        try:
            abandonne = chemin.stat().st_mtime < limite
        except FileNotFoundError:
            continue  # retiré entre-temps par une autre sauvegarde : rien à faire
        if abandonne:
            chemin.unlink(missing_ok=True)
            journal.warning("Sauvegarde partielle abandonnée supprimée : %s", chemin)


def _faire_tourner(dossier: Path, prefixe: str, garder: int) -> list[str]:
    """Supprime les plus anciennes sauvegardes de ce préfixe au-delà de `garder` (0 : aucune).

    Les noms portent la date et l'heure UTC : l'ordre des noms est celui des dates.
    """
    if garder == 0:
        return []
    sauvegardes = sorted(dossier.glob(f"{prefixe}-*.db"), key=lambda chemin: chemin.name)
    anciennes = sauvegardes[: max(0, len(sauvegardes) - garder)]
    supprimees = []
    for chemin in anciennes:
        try:
            chemin.unlink()
        except OSError as erreur:
            # La sauvegarde du jour est faite et vérifiée : un fichier ancien tenu par un autre
            # programme (antivirus, explorateur) ne la fait pas passer pour un échec.
            journal.warning("Ancienne sauvegarde %s non supprimée : %s", chemin, erreur)
            typer.echo(f"  ancienne sauvegarde non supprimée : {chemin.name} ({erreur})", err=True)
            continue
        journal.info("Ancienne sauvegarde supprimée : %s", chemin)
        supprimees.append(chemin.name)
    return supprimees


def _verifier_integrite(copie: Path, fichier: Path) -> int:
    """Contrôle d'intégrité de la copie d'une sauvegarde ; renvoie son nombre de tables."""
    try:
        with closing(sqlite3.connect(copie)) as connexion:
            verdict = connexion.execute("PRAGMA integrity_check").fetchone()[0]
            nb_tables = connexion.execute(
                "SELECT count(*) FROM sqlite_master WHERE type = 'table'"
            ).fetchone()[0]
    except sqlite3.Error as erreur:
        raise ErreurDonneesInvalides(
            f"{fichier} n'est pas une base SQLite lisible ({erreur})."
        ) from erreur
    if verdict != "ok":
        raise ErreurDonneesInvalides(
            f"Sauvegarde {fichier} abîmée : intégrité en échec ({verdict})."
        )
    return nb_tables


def _comparer_au_modele(copie: Path) -> tuple[list[str], list[str], dict[str, int]]:
    """Tables du modèle absentes, écarts de schéma et lignes par table de la copie."""
    moteur = create_engine(f"sqlite:///{copie.as_posix()}")
    try:
        manquantes = tables_manquantes(moteur)
        ecarts = ecarts_schema(moteur)
        with moteur.connect() as connexion:
            lignes = {
                table: connexion.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one()
                for table in sorted(Base.metadata.tables)
                if table not in manquantes
            }
    finally:
        moteur.dispose()
    return manquantes, ecarts, lignes
