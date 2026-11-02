"""Commande du lot « données fictives » : `python -m edt jeu`.

Charge un établissement fictif (petit, moyen ou grand) dans la base, après
une remise à zéro si on la demande, puis affiche le résumé du rapport :
contenu, charges, verdict du vérificateur sur le témoin, besoins écartés.

    python -m edt jeu --taille grand --graine 0 --reset --oui --reference

La remise à zéro supprime toutes les données : elle demande une
confirmation, sauf avec `--oui`. La graine (0 à 2 147 483 647) et l'année
(2000 à 2100) sont vérifiées **avant** : une valeur refusée ne touche pas
à la base.
"""

from __future__ import annotations

import logging
from typing import Annotated

import typer

from edt.services.jeux import recharger_jeu_fictif, tailles_disponibles
from edt.synthese.configuration import configuration_nommee
from edt.synthese.generateur import valider_demande

commandes = typer.Typer()
journal = logging.getLogger(__name__)


@commandes.command("jeu")
def jeu(
    taille: Annotated[
        str,
        typer.Option(
            "--taille",
            help="Taille du jeu : "
            + ", ".join(f"« {t.nom} »" for t in tailles_disponibles())
            + ".",
        ),
    ] = "moyen",
    graine: Annotated[
        int, typer.Option("--graine", help="Graine des tirages : même graine, mêmes données.")
    ] = 0,
    annee: Annotated[
        int | None,
        typer.Option(
            "--annee",
            help="Année de début de l'année scolaire (ex. 2026 pour 2026-2027) ; "
            "défaut : la plus récente de la base.",
        ),
    ] = None,
    reset: Annotated[
        bool,
        typer.Option(
            "--reset", help="Remet d'abord la base à zéro : toutes les données sont perdues."
        ),
    ] = False,
    oui: Annotated[
        bool, typer.Option("--oui", "-y", help="Confirme la remise à zéro sans la demander.")
    ] = False,
    reference: Annotated[
        bool,
        typer.Option(
            "--reference",
            help="Enregistre aussi le témoin comme version « référence » (non active) "
            "de chaque semestre.",
        ),
    ] = False,
) -> None:
    """Charge un établissement fictif complet et réaliste (données fictives)."""
    # Tout ce qui peut être refusé l'est avant la remise à zéro.
    configuration_nommee(taille)
    valider_demande(graine, annee)
    if reset and not oui:
        typer.confirm(
            "La remise à zéro supprime TOUTES les données de la base. Continuer ?", abort=True
        )
    rapport = recharger_jeu_fictif(
        taille,
        graine,
        reinitialiser=reset,
        annee_debut=annee,
        enregistrer_reference=reference,
    )
    typer.echo(rapport.resume())
