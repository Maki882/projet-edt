"""Ligne de commande : `python -m edt <commande>`.

Mécanisme d'extension
---------------------
Ce fichier ne déclare aucune commande. Au démarrage, il importe tous les
modules du paquet `edt.commandes` et rattache l'objet `commandes` (un
`typer.Typer`) que chacun doit exposer. Une commande s'ajoute donc en créant
un module dans `edt/commandes/`, sans toucher à ce fichier :

    # src/edt/commandes/exemple.py
    import typer

    commandes = typer.Typer()

    @commandes.command("bonjour")
    def bonjour(nom: str = "monde") -> None:
        \"\"\"Dit bonjour (cette phrase devient l'aide de la commande).\"\"\"
        typer.echo(f"Bonjour {nom}")

Les modules dont le nom commence par `_` sont ignorés (code partagé).

Erreurs
-------
Une commande lève simplement les erreurs métier (`ErreurEDT`) : elles sont
affichées en clair sur la sortie d'erreur et donnent le code de sortie 1.
Une panne du pilote de base (base verrouillée par un autre processus, fichier
en lecture seule ou corrompu, disque plein) est traduite en phrase française
avec son remède, code 1 également (`edt.db.moteur.expliquer_erreur_base`).
Toute autre exception est journalisée avec sa trace complète et donne le
code de sortie 2.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
import sys
from collections.abc import Sequence

import typer
import typer.core
from sqlalchemy.exc import DatabaseError

from edt import __version__, commandes
from edt.db.moteur import expliquer_erreur_base
from edt.erreurs import ErreurConfiguration, ErreurEDT
from edt.journal import configurer_journalisation, journalisation_de_secours

journal = logging.getLogger(__name__)

NOM_ATTRIBUT = "commandes"
CODE_ERREUR_METIER = 1
CODE_ERREUR_INATTENDUE = 2


ERREURS_DE_TYPER: tuple[type[BaseException], ...] = (
    typer.TyperException,
    typer.Exit,
    typer.Abort,
)
"""Exceptions propres à Typer (erreur d'usage, sortie demandée, interruption) : à laisser passer."""


class GroupeEDT(typer.core.TyperGroup):
    """Groupe racine : traduit les erreurs en message lisible et en code de sortie."""

    def invoke(self, ctx: typer.Context):
        """Exécute la commande ; une erreur devient un message et un code de sortie."""
        try:
            return super().invoke(ctx)
        except ERREURS_DE_TYPER:
            raise
        except ErreurEDT as erreur:
            # Sans gestionnaire (journalisation en panne), `logging` répéterait le message
            # sur la console : le message n'est journalisé que si un gestionnaire est en place.
            if logging.getLogger().handlers:
                journal.error("Commande en échec : %s", erreur)
            typer.echo(f"Erreur : {erreur}", err=True)
            raise typer.Exit(CODE_ERREUR_METIER) from erreur
        except DatabaseError as erreur:
            message = expliquer_erreur_base(erreur)
            journal.error("Commande en échec, base de données inaccessible : %s", erreur)
            typer.echo(f"Erreur : {message}", err=True)
            raise typer.Exit(CODE_ERREUR_METIER) from erreur
        except Exception as erreur:
            journal.exception("Erreur inattendue dans une commande")
            typer.echo(
                "Erreur inattendue : "
                f"{type(erreur).__name__}: {erreur}. La trace complète est dans le journal.",
                err=True,
            )
            raise typer.Exit(CODE_ERREUR_INATTENDUE) from erreur


def modules_de_commandes() -> list[str]:
    """Noms complets des modules de `edt.commandes` à charger (`base` en premier)."""
    noms = [
        info.name
        for info in pkgutil.iter_modules(commandes.__path__)
        if not info.name.startswith("_")
    ]
    return [f"{commandes.__name__}.{nom}" for nom in sorted(noms, key=lambda n: (n != "base", n))]


def construire_app() -> typer.Typer:
    """Construit l'application Typer en rattachant les commandes de chaque module découvert."""
    app = typer.Typer(
        cls=GroupeEDT,
        name="edt",
        help="EDT : générateur automatique d'emplois du temps.",
        no_args_is_help=True,
        add_completion=False,
        pretty_exceptions_enable=False,
    )

    @app.callback()
    def racine(ctx: typer.Context) -> None:
        """Prépare la journalisation avant toute commande."""
        try:
            configurer_journalisation()
        except ErreurConfiguration as erreur:
            if ctx.invoked_subcommand != "doctor":
                raise
            # doctor doit pouvoir diagnostiquer un dossier de journaux défaillant.
            journalisation_de_secours(str(erreur))

    for nom_module in modules_de_commandes():
        module = importlib.import_module(nom_module)
        groupe = getattr(module, NOM_ATTRIBUT, None)
        if not isinstance(groupe, typer.Typer):
            raise ErreurConfiguration(
                f"Le module {nom_module} doit exposer un objet « {NOM_ATTRIBUT} » de type "
                "typer.Typer (voir docs/architecture.md, « Ajouter une commande »)."
            )
        app.add_typer(groupe)
    return app


app = construire_app()


def _forcer_sorties_utf8() -> None:
    """Écrit la sortie standard et la sortie d'erreur en UTF-8.

    Sous Windows, une sortie redirigée (tube, fichier) prend sinon l'encodage
    régional et les accents deviennent illisibles pour qui la relit en UTF-8.
    Un flux qui ne sait pas changer d'encodage (flux de test) est laissé tel quel.
    """
    for flux in (sys.stdout, sys.stderr):
        reconfigurer = getattr(flux, "reconfigure", None)
        if callable(reconfigurer) and (flux.encoding or "").lower().replace("-", "") != "utf8":
            reconfigurer(encoding="utf-8", errors="replace")


def main(arguments: Sequence[str] | None = None) -> int:
    """Point d'entrée : exécute la commande et renvoie le code de sortie."""
    _forcer_sorties_utf8()
    try:
        resultat = app(
            args=list(arguments) if arguments is not None else None, standalone_mode=False
        )
    except typer.Abort:
        typer.echo("Interrompu.", err=True)
        return 130
    except typer.TyperException as erreur:
        # Erreur d'usage (commande ou option inconnue) : Typer sait l'afficher avec l'aide.
        afficher = getattr(erreur, "show", None)
        if callable(afficher):
            afficher()
        else:
            typer.echo(f"Erreur : {erreur}", err=True)
        return erreur.exit_code
    return resultat if isinstance(resultat, int) else 0


__all__ = ["app", "construire_app", "main", "modules_de_commandes", "__version__"]
