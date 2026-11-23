"""Parcours REC-BOUT-2 : parcours complet en ligne de commande (`python -m edt ...`)."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from edt import cli
from edt.db import modeles as m
from edt.db.moteur import session_base


@pytest.mark.integration
def test_parcours_ligne_de_commande(environnement):
    """Scénario : un administrateur prépare la base et produit l'emploi du temps sans le site web.

    Étapes :
    1. `init-db` crée la base, `jeu-minimal` charge les données de démonstration.
    2. `generer` calcule l'emploi du temps du semestre (vrai solveur).
    3. `verifier` contrôle la version produite : code de sortie 0 (aucune erreur).
    4. `exporter-edt` écrit le fichier CSV de l'emploi du temps.

    Ce que cela prouve : les commandes s'enchaînent sur la même base, chacune relit ce que la
    précédente a écrit ; un script d'exploitation peut produire un emploi du temps de bout en bout.
    """
    lanceur = CliRunner()

    def lancer(*arguments: str) -> str:
        resultat = lanceur.invoke(cli.app, list(arguments))
        assert resultat.exit_code == 0, resultat.output
        return resultat.output

    lancer("init-db")
    lancer("jeu-minimal")
    with session_base() as session:  # le semestre qui porte les besoins du jeu
        semestre = session.scalar(select(m.Besoin.semestre_id).limit(1))
    lancer("generer", "--semestre", str(semestre), "--sans-optimiser", "--temps-limite", "30")
    with session_base() as session:
        version = session.scalar(select(m.VersionEdt.id).where(m.VersionEdt.active.is_(True)))
    lancer("verifier", "--version", str(version))

    fichier = environnement / "edt.csv"
    lancer("exporter-edt", "--version", str(version), "--sortie", str(fichier))
    assert fichier.exists() and fichier.stat().st_size > 0
    assert "Mathématiques" in fichier.read_text(encoding="utf-8-sig")
