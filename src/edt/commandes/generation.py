"""Commandes du lot « solveur » : `generer`, `pre-verifier`, `instancier`.

    python -m edt pre-verifier --semestre 1
    python -m edt generer --semestre 1 --temps-limite 120 --optimiser --graine 3
    python -m edt generer -d SALLE_UNIQUE -d PAUSE        # contraintes désactivées
    python -m edt instancier --version 4

`generer` lance un calcul par le même service que l'interface
(`edt.services.generation`), attend sa fin et affiche le résultat : en cas de
succès, une nouvelle version d'emploi du temps est enregistrée, contrôlée par
le vérificateur, et ses séances datées sont produites. `instancier` refait
seulement les séances datées d'une version existante.

Codes de sortie : `0` si tout a réussi ; `1` si le calcul n'aboutit pas
(impossible, temps dépassé, arrêté, refusé) ou, pour `pre-verifier`, si une
impossibilité certaine est relevée ; `2` pour une erreur inattendue.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Annotated

import typer

from edt.db.modeles import Semestre
from edt.db.moteur import session_base
from edt.domaine.enumerations import Gravite
from edt.erreurs import ErreurIntrouvable
from edt.services import generation
from edt.services.calendrier import semestre_par_defaut
from edt.services.instanciation import RapportInstanciation, instancier_version
from edt.services.parametres import lire_parametres
from edt.services.verification import version_a_verifier
from edt.solveur.contrat import OptionsSolveur

commandes = typer.Typer()
journal = logging.getLogger(__name__)

CODE_ECHEC = 1

OptionSemestre = Annotated[
    int | None,
    typer.Option("--semestre", help="Identifiant du semestre (défaut : le semestre en cours)."),
]
OptionDesactiver = Annotated[
    list[str] | None,
    typer.Option(
        "--desactiver",
        "-d",
        help="Code d'une contrainte à ignorer, par exemple PAUSE (option répétable).",
    ),
]


def _semestre(session, semestre_id: int | None) -> Semestre:
    """Le semestre demandé, ou celui proposé par défaut."""
    if semestre_id is None:
        return semestre_par_defaut(session)
    semestre = session.get(Semestre, semestre_id)
    if semestre is None:
        raise ErreurIntrouvable(f"Le semestre {semestre_id} n'existe pas.")
    return semestre


@commandes.command("pre-verifier")
def pre_verifier(
    semestre: OptionSemestre = None,
    desactiver: OptionDesactiver = None,
) -> None:
    """Dit, avant tout calcul, ce qui rend un emploi du temps impossible ou difficile."""
    with session_base() as session:
        cible = _semestre(session, semestre)
        libelle = cible.libelle
        anomalies = generation.pre_verifier_semestre(session, cible.id, desactiver or ())
    erreurs = [a for a in anomalies if a.gravite is Gravite.ERREUR]
    alertes = [a for a in anomalies if a.gravite is Gravite.AVERTISSEMENT]
    typer.echo(f"Pré-vérifications du semestre « {libelle} » :")
    for titre, liste in (("Impossibilités", erreurs), ("Avertissements", alertes)):
        if liste:
            typer.echo(f"{titre} ({len(liste)}) :")
            for anomalie in liste:
                typer.echo(f"  - [{anomalie.code.value}] {anomalie.message}")
    if erreurs:
        typer.echo("Résultat : le calcul serait refusé.")
        raise typer.Exit(CODE_ECHEC)
    typer.echo(
        "Résultat : rien n'empêche de lancer le calcul"
        + (f" ({len(alertes)} avertissement(s))." if alertes else ".")
    )


@commandes.command("generer")
def generer(
    semestre: OptionSemestre = None,
    temps_limite: Annotated[
        float | None,
        typer.Option(
            "--temps-limite", help="Durée maximale du calcul, en secondes (défaut : paramètre)."
        ),
    ] = None,
    graine: Annotated[
        int, typer.Option("--graine", help="Graine : une autre graine donne une autre solution.")
    ] = 0,
    optimiser: Annotated[
        bool | None,
        typer.Option(
            "--optimiser/--sans-optimiser",
            help="Minimiser trous, changements de campus, samedi et créneaux extrêmes "
            "(défaut : paramètre « objectif_actif »).",
        ),
    ] = None,
    desactiver: OptionDesactiver = None,
    fils: Annotated[
        int | None,
        typer.Option("--fils", help="Fils de calcul (défaut : paramètre ; 0 = au plus deux)."),
    ] = None,
    sans_indices: Annotated[
        bool,
        typer.Option("--sans-indices", help="Ne pas partir de l'emploi du temps actif."),
    ] = False,
    tolerer: Annotated[
        bool,
        typer.Option(
            "--tolerer-violations-heritees",
            help="Accepter des séances verrouillées qui se contredisent déjà entre elles.",
        ),
    ] = False,
    sans_activer: Annotated[
        bool,
        typer.Option("--sans-activer", help="Enregistrer la version sans la rendre active."),
    ] = False,
) -> None:
    """Génère l'emploi du temps d'un semestre et attend le résultat (code 1 si le calcul échoue)."""
    with session_base() as session:
        cible = _semestre(session, semestre)
        semestre_id, libelle = cible.id, cible.libelle
        options = OptionsSolveur.depuis_parametres(
            lire_parametres(session),
            contraintes_desactivees=desactiver or (),
            graine=graine,
            tolerer_violations_heritees=tolerer,
        )
    changements: dict[str, object] = {"utiliser_indices": not sans_indices}
    if temps_limite is not None:
        changements["temps_limite_s"] = temps_limite
    if optimiser is not None:
        changements["optimiser"] = optimiser
    if fils is not None:
        changements["nb_workers"] = fils
    options = dataclasses.replace(options, **changements)

    typer.echo(
        f"Génération de l'emploi du temps du semestre « {libelle} » "
        f"(temps limite {options.temps_limite_s:g} s, "
        f"{'avec' if options.optimiser else 'sans'} optimisation)..."
    )
    identifiant = generation.lancer_calcul(semestre_id, options, activer=not sans_activer)
    suivi = generation.attendre_calcul(identifiant)

    typer.echo(f"Calcul n° {suivi.id} : {suivi.etat.libelle} en {suivi.temps_ecoule_s:g} s.")
    typer.echo(suivi.message)
    for avertissement in suivi.avertissements:
        typer.echo(f"  Avertissement : {avertissement}")
    indicateurs = suivi.statistiques.get("indicateurs")
    if indicateurs and "trous" not in suivi.statistiques:
        # Sans amélioration, le message du calcul ne dit rien de la qualité : on la donne ici.
        typer.echo(
            f"Bilan sur l'ensemble du semestre : {indicateurs.get('trous_creneaux')} créneau(x) "
            f"libre(s) entre deux cours dans les journées des groupes et "
            f"{indicateurs.get('seances_samedi')} séance(s) le samedi."
        )
    if suivi.fichier_journal:
        typer.echo(f"Journal du calcul : {suivi.fichier_journal}")
    generation.exiger_reussite(suivi)
    typer.echo(f"Version enregistrée : n° {suivi.version_id}.")


@commandes.command("instancier")
def instancier(
    version: Annotated[
        int | None, typer.Option("--version", help="Identifiant de la version à instancier.")
    ] = None,
    semestre: Annotated[
        int | None,
        typer.Option("--semestre", help="Identifiant du semestre (sa version active)."),
    ] = None,
) -> None:
    """Produit les séances datées d'une version et replace celles qui tombent un jour fermé."""
    with session_base() as session:
        version_id = version_a_verifier(session, version_id=version, semestre_id=semestre)
        rapport = instancier_version(session, version_id)
    for ligne in lignes_du_rapport(rapport):
        typer.echo(ligne)


def lignes_du_rapport(rapport: RapportInstanciation) -> list[str]:
    """Texte lisible d'un rapport d'instanciation."""
    lignes = [f"Version n° {rapport.version_id} : {rapport.resume()}"]
    if rapport.nb_conservees:
        lignes.append(
            f"{rapport.nb_conservees} séance(s) saisie(s) à la main ou importée(s) laissée(s) "
            "en place."
        )
    if rapport.a_programmer:
        lignes.append(f"À programmer ({rapport.nb_a_programmer}) :")
        lignes.extend(
            f"  - {entree.besoin}, prévue le {entree.date_origine:%d/%m/%Y} ({entree.horaire}) : "
            f"{entree.raison}"
            for entree in rapport.a_programmer
        )
    return lignes
