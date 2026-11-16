"""Commande de vérification : `python -m edt verifier`.

Vérifie un emploi du temps enregistré : la semaine type d'une version (par
défaut), ou ses séances datées avec `--datees`. La version est désignée par
`--version`, ou c'est la version active du semestre `--semestre`, ou à
défaut celle du semestre en cours (`edt.services.verification.version_a_verifier`).

Codes de sortie :

- `0` : aucune anomalie de gravité « erreur » (l'emploi du temps est valide) ;
- `1` : au moins une erreur ;
- `2` : vérification impossible (version ou semestre introuvable, code de
  contrainte inconnu, donnée enregistrée mal formée, aucune séance datée à
  vérifier...). Le message est affiché sur la sortie d'erreur et journalisé.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Annotated

import typer

from edt.db.moteur import session_base
from edt.domaine.enumerations import Gravite
from edt.erreurs import ErreurDonneesInvalides, ErreurEDT
from edt.services.verification import (
    SEANCES_DATEES,
    RapportVerification,
    verifier_occurrences_version,
    verifier_version,
    version_a_verifier,
)

commandes = typer.Typer()
journal = logging.getLogger(__name__)

CODE_VALIDE = 0
CODE_ERREURS = 1
CODE_IMPOSSIBLE = 2

_TITRES = {
    Gravite.ERREUR: "Erreurs",
    Gravite.AVERTISSEMENT: "Avertissements",
    Gravite.INFORMATION: "Informations",
}


@commandes.command("verifier")
def verifier(
    version: Annotated[
        int | None, typer.Option("--version", help="Identifiant de la version à vérifier.")
    ] = None,
    semestre: Annotated[
        int | None,
        typer.Option(
            "--semestre", help="Identifiant du semestre (sa version active est vérifiée)."
        ),
    ] = None,
    datees: Annotated[
        bool,
        typer.Option("--datees", help="Vérifie les séances datées au lieu de la semaine type."),
    ] = False,
    du: Annotated[
        dt.datetime | None,
        typer.Option(
            "--du", formats=["%Y-%m-%d"], help="Avec --datees : premier jour (AAAA-MM-JJ)."
        ),
    ] = None,
    au: Annotated[
        dt.datetime | None,
        typer.Option(
            "--au", formats=["%Y-%m-%d"], help="Avec --datees : dernier jour (AAAA-MM-JJ)."
        ),
    ] = None,
    contraintes: Annotated[
        list[str] | None,
        typer.Option(
            "--contrainte",
            "-c",
            help="Code d'une contrainte à vérifier (option répétable ; défaut : toutes).",
        ),
    ] = None,
    sortie_json: Annotated[
        bool, typer.Option("--json", help="Affiche le rapport complet en JSON.")
    ] = False,
) -> None:
    """Vérifie un emploi du temps enregistré et liste ses anomalies (code 1 s'il y a une erreur)."""
    try:
        if (du is not None or au is not None) and not datees:
            raise ErreurDonneesInvalides("Les options --du et --au s'emploient avec --datees.")
        with session_base() as session:
            version_id = version_a_verifier(session, version_id=version, semestre_id=semestre)
            if datees:
                rapport = verifier_occurrences_version(
                    session,
                    version_id,
                    date_debut=None if du is None else du.date(),
                    date_fin=None if au is None else au.date(),
                    contraintes_actives=contraintes,
                )
            else:
                rapport = verifier_version(session, version_id, contraintes_actives=contraintes)
        if rapport.objet == SEANCES_DATEES and rapport.nb_examines == 0:
            raise ErreurDonneesInvalides(
                f"La version « {rapport.version} » n'a aucune séance datée"
                f"{' sur cette période' if du or au else ''} : rien à vérifier. Les séances "
                "datées naissent de l'instanciation de la semaine type sur le calendrier."
            )
    except ErreurEDT as erreur:
        journal.error("Vérification impossible : %s", erreur)
        typer.echo(f"Vérification impossible : {erreur}", err=True)
        raise typer.Exit(CODE_IMPOSSIBLE) from erreur

    if sortie_json:
        typer.echo(json.dumps(rapport.vers_dict(), ensure_ascii=False, indent=2))
    else:
        for ligne in lignes_du_rapport(rapport):
            typer.echo(ligne)
    raise typer.Exit(CODE_VALIDE if rapport.valide else CODE_ERREURS)


def lignes_du_rapport(rapport: RapportVerification) -> list[str]:
    """Texte lisible d'un rapport : en-tête, anomalies par gravité, indicateurs, verdict."""
    objet = "des séances datées" if rapport.objet == SEANCES_DATEES else "de la semaine type"
    debut, fin = rapport.periode
    periode = ""
    if debut or fin:
        periode = f", du {_date(debut) if debut else 'début'} au {_date(fin) if fin else 'terme'}"
    if rapport.objet == SEANCES_DATEES:
        elements = "séance(s) datée(s) examinée(s)"
    else:
        elements = "placement(s) examiné(s)"
    lignes = [
        f"Vérification {objet} : version « {rapport.version} » (n° {rapport.version_id}), "
        f"semestre « {rapport.semestre} »{periode}.",
        f"{len(rapport.contraintes)} contrainte(s) vérifiée(s), {rapport.nb_examines} {elements}.",
    ]
    for gravite, titre in _TITRES.items():
        anomalies = [a for a in rapport.anomalies if a.gravite is gravite]
        if anomalies:
            lignes += ["", f"{titre} ({len(anomalies)}) :"]
            lignes += [
                f"  - [{anomalie.code}"
                + (f" {anomalie.regle_code}" if anomalie.regle_code else "")
                + f"] {anomalie.message}"
                for anomalie in anomalies
            ]
    if rapport.indicateurs is not None:
        lignes += ["", *_lignes_indicateurs(rapport)]
    verdict = "emploi du temps valide" if rapport.valide else "emploi du temps NON valide"
    lignes += [
        "",
        f"Résultat : {verdict} ({rapport.nb_erreurs} erreur(s), "
        f"{rapport.nb_avertissements} avertissement(s), {rapport.nb_informations} information(s)).",
    ]
    return lignes


def _lignes_indicateurs(rapport: RapportVerification) -> list[str]:
    indicateurs = rapport.indicateurs
    assert indicateurs is not None  # appelée seulement pour une semaine type
    semaines = indicateurs.nb_semaines
    return [
        f"Indicateurs (totaux sur les {semaines} semaines du semestre) :",
        f"  - séances placées : {indicateurs.nb_seances_placees} sur {indicateurs.nb_seances} "
        f"({_pourcentage(indicateurs.pourcentage_seances_placees)})",
        f"  - utilisation des salles : {_pourcentage(indicateurs.pourcentage_utilisation_salles)}",
        f"  - trous des groupes : {indicateurs.trous_creneaux} créneau(x) libre(s), "
        f"{indicateurs.trous_minutes_journee} min",
        f"  - changements de campus : {indicateurs.changements_campus_groupes} pour les groupes, "
        f"{indicateurs.changements_campus_enseignants} pour les enseignants",
        f"  - séances le samedi : {indicateurs.seances_samedi} ; sur les créneaux extrêmes : "
        f"{indicateurs.seances_creneaux_extremes}",
        f"  - valeur de l'objectif : {indicateurs.valeur_objectif}",
    ]


def _pourcentage(valeur: float | None) -> str:
    return "sans objet" if valeur is None else f"{valeur:.1f} %".replace(".", ",")


def _date(jour: dt.date) -> str:
    return f"{jour:%d/%m/%Y}"
