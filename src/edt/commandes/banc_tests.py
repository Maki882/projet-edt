"""Commande `python -m edt tests` : lancer les tests du projet comme depuis l'application.

Passe par le même service que les onglets « Tests unitaires » et « Tests
d'intégration » (`edt.services.banc_tests`) : même sous-processus assaini,
même durée maximale, mêmes résultats enregistrés (visibles ensuite dans
l'application). Sans option, lance les deux familles (sans les tests de
charge). Sert à lancer la suite sur le serveur. `--test <clé>` lance un seul
test, y compris un test de charge, que le site ne lance pas.

Codes de sortie : `0` aucun échec ; `1` au moins un échec, exécution
incomplète (arrêt, durée maximale) ou lancement refusé.
"""

from __future__ import annotations

import logging
from typing import Annotated

import typer

from edt.erreurs import ErreurEDT
from edt.services.banc_tests import execution as ex
from edt.services.banc_tests import resultats as res
from edt.services.banc_tests.catalogue import (
    CIBLE_FAMILLE,
    FAMILLES,
    lire_catalogue,
    resoudre_cible,
)
from edt.services.banc_tests.tableau import texte_duree

commandes = typer.Typer()
journal = logging.getLogger(__name__)


def _compte(nombre: int, singulier: str, pluriel: str) -> str:
    return f"{nombre} {singulier if nombre <= 1 else pluriel}"


@commandes.command("tests")
def tests(
    famille: Annotated[
        str | None,
        typer.Option(
            "--famille", help="unitaires ou integration (par défaut : les deux familles)."
        ),
    ] = None,
    composant: Annotated[
        str | None,
        typer.Option("--composant", help="Un composant seulement (socle, solveur, parcours…)."),
    ] = None,
    test: Annotated[
        str | None,
        typer.Option(
            "--test",
            help="Un seul test, par sa clé (tests/…/test_x.py::test_nom), test de charge compris.",
        ),
    ] = None,
) -> None:
    """Lance les tests du projet et enregistre leurs résultats (code 1 s'il y a un échec)."""
    if famille is not None and famille not in FAMILLES:
        raise ErreurEDT(f"Famille inconnue : « {famille} ». Choisir parmi : {', '.join(FAMILLES)}.")
    catalogue = lire_catalogue()
    if test and composant:
        raise ErreurEDT("Choisir --test ou --composant, pas les deux.")
    demande = f"test:{test}" if test else f"composant:{composant}" if composant else CIBLE_FAMILLE
    cible = resoudre_cible(catalogue, demande, famille)
    typer.echo(f"Lancement de {cible.libelle} ({len(cible.tests)} tests)…")
    execution = ex.lancer(cible, catalogue.racine)

    def afficher(cle: str, resultat: res.ResultatTest) -> None:
        if resultat.etat == res.ECHEC:
            typer.echo(f"ÉCHEC  {cle}")

    try:
        suivi = ex.attendre(execution, rappel=afficher)
    except KeyboardInterrupt:
        # Le sous-processus est dans son propre groupe : Ctrl+C ne l'atteint pas, il est arrêté ici.
        typer.echo("Interruption : arrêt des tests en cours…", err=True)
        ex.demander_arret()
        suivi = ex.attendre(execution)
    reussis, echecs, ignores = (suivi.compte(e) for e in (res.REUSSI, res.ECHEC, res.IGNORE))
    typer.echo(
        f"{_compte(len(suivi.termines), 'test terminé', 'tests terminés')} en "
        f"{texte_duree(suivi.ecoule_s)} : {_compte(reussis, 'réussi', 'réussis')}, "
        f"{_compte(echecs, 'échec', 'échecs')}, {_compte(ignores, 'ignoré', 'ignorés')}. "
        f"{suivi.message}"
    )

    typer.echo(f"Journal complet : {execution.dossier / res.FICHIER_JOURNAL}")
    if echecs or suivi.issue != res.ISSUE_TERMINEE:
        raise typer.Exit(1)
