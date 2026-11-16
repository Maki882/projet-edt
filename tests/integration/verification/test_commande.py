"""Commande `verifier` lancée par `CliRunner` : codes de sortie, options.

`version_sans_occurrences` vient de `tests/outils/verification/fixtures.py` (par `conftest.py`).
"""

from __future__ import annotations

import json

import pytest
from tests.outils.verification.versions import enregistrer_version
from typer.testing import CliRunner

from edt import cli
from edt.db.moteur import session_base


@pytest.fixture
def lanceur() -> CliRunner:
    return CliRunner()


@pytest.fixture
def version_avec_occurrences(jeu_minimal) -> int:
    with session_base() as session:
        return enregistrer_version(session, jeu_minimal.semestre_id, occurrences=True)


def test_semaine_type_valide_code_0(lanceur, version_sans_occurrences, jeu_minimal):
    resultat = lanceur.invoke(cli.app, ["verifier", "--semestre", str(jeu_minimal.semestre_id)])
    assert resultat.exit_code == 0, resultat.output
    assert "Vérification de la semaine type : version « Version saisie à la main »" in (
        resultat.output
    )
    assert "17 placement(s) examiné(s)" in resultat.output
    assert "séances placées : 17 sur 17 (100,0 %)" in resultat.output
    assert "Résultat : emploi du temps valide (0 erreur(s)" in resultat.output


def test_seances_datees_avec_erreurs_code_1(lanceur, version_avec_occurrences):
    resultat = lanceur.invoke(
        cli.app, ["verifier", "--version", str(version_avec_occurrences), "--datees"]
    )
    assert resultat.exit_code == 1, resultat.output
    assert "Erreurs (6) :" in resultat.output
    assert "  - [JOUR_FERME] La séance" in resultat.output
    assert "emploi du temps NON valide" in resultat.output


def test_periode_et_contrainte_choisie(lanceur, version_avec_occurrences):
    resultat = lanceur.invoke(
        cli.app,
        [
            "verifier",
            "--version",
            str(version_avec_occurrences),
            "--datees",
            "--du",
            "2026-11-09",
            "--au",
            "2026-11-13",
            "-c",
            "DISPO_SALLE",
        ],
    )
    assert resultat.exit_code == 0, resultat.output
    assert "du 09/11/2026 au 13/11/2026" in resultat.output
    assert "1 contrainte(s) vérifiée(s)" in resultat.output


def test_sortie_json(lanceur, version_avec_occurrences):
    resultat = lanceur.invoke(
        cli.app, ["verifier", "--version", str(version_avec_occurrences), "--datees", "--json"]
    )
    assert resultat.exit_code == 1
    donnees = json.loads(resultat.stdout)
    assert donnees["par_code"] == {"DISPO_SALLE": 2, "JOUR_FERME": 4}
    assert donnees["valide"] is False


@pytest.mark.parametrize(
    "arguments",
    [
        ["--version", "999"],
        ["--semestre", "999"],
        ["--semestre", "{semestre}", "-c", "PAS_UN_CODE"],
        ["--semestre", "{semestre}", "--du", "2026-11-09"],
        ["--semestre", "{semestre}", "--datees"],  # aucune séance datée : rien à vérifier
    ],
)
def test_verification_impossible_code_2(lanceur, version_sans_occurrences, jeu_minimal, arguments):
    arguments = [a.format(semestre=jeu_minimal.semestre_id) for a in arguments]
    resultat = lanceur.invoke(cli.app, ["verifier", *arguments])
    assert resultat.exit_code == 2, resultat.output
    assert "Vérification impossible" in resultat.output


def test_semestre_sans_version_active(lanceur, jeu_minimal):
    resultat = lanceur.invoke(cli.app, ["verifier", "--semestre", str(jeu_minimal.semestre_id)])
    assert resultat.exit_code == 2
    assert "n'a pas de version active" in resultat.output
