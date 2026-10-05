"""Commandes gabarit, importer, exporter-donnees et exporter-edt."""

from __future__ import annotations

from pathlib import Path

import pytest
from icalendar import Calendar
from sqlalchemy import func, select
from typer.testing import CliRunner

from edt import cli
from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.echange.lecture import FichierEnvoye, lire_fichier

DOSSIER_FICHIERS = Path(__file__).parent / "fichiers"


@pytest.fixture
def lanceur() -> CliRunner:
    return CliRunner()


def _compter(modele) -> int:
    with session_base() as session:
        return session.scalar(select(func.count()).select_from(modele))


def test_gabarit(lanceur, base, tmp_path):
    sortie = tmp_path / "gabarit.xlsx"
    resultat = lanceur.invoke(cli.app, ["gabarit", "--sortie", str(sortie)])

    assert resultat.exit_code == 0, resultat.output
    lu, _ = lire_fichier(FichierEnvoye(sortie.name, sortie.read_bytes()))
    assert [t.nom for t in lu.tableaux][:3] == ["annees", "semestres", "campus"]
    assert all(not t.lignes for t in lu.tableaux)


def test_exporter_puis_simuler_le_reimport(lanceur, jeu_minimal, tmp_path):
    export = lanceur.invoke(
        cli.app, ["exporter-donnees", "--format", "zip", "--sortie", str(tmp_path)]
    )
    assert export.exit_code == 0, export.output
    (archive,) = tmp_path.glob("donnees-edt-*.zip")

    resultat = lanceur.invoke(cli.app, ["importer", str(archive), "--simuler"])

    assert resultat.exit_code == 0, resultat.output
    assert "rien n'a été écrit" in resultat.output
    assert "salles : 6 inchangé(s)" in resultat.output


def test_importer_avec_rejets_sort_en_erreur(lanceur, jeu_minimal, tmp_path):
    rapport = tmp_path / "rapport.csv"
    resultat = lanceur.invoke(
        cli.app,
        ["importer", str(DOSSIER_FICHIERS / "salles_avec_erreurs.csv"), "--rapport", str(rapport)],
    )

    assert resultat.exit_code == 1
    assert "salles : 2 créé(s), 4 rejeté(s)" in resultat.output
    assert "[erreur] salles_avec_erreurs.csv, ligne 3, colonne capacite" in resultat.output
    assert _compter(m.Salle) == jeu_minimal.nb_salles + 2  # les lignes valides sont entrées
    assert "Campus « LYON » inconnu" in rapport.read_text(encoding="utf-8-sig")


def test_tout_remplacer_demande_confirmation(lanceur, jeu_minimal, tmp_path):
    campus = tmp_path / "campus.csv"
    campus.write_text("code;nom\nNDC;Notre-Dame-des-Champs\n", encoding="utf-8")

    refus = lanceur.invoke(cli.app, ["importer", str(campus), "--tout-remplacer"], input="n\n")
    assert refus.exit_code == 1 and "rien n'a été modifié" in refus.output
    assert _compter(m.Salle) == jeu_minimal.nb_salles

    accord = lanceur.invoke(cli.app, ["importer", str(campus), "--tout-remplacer", "--oui"])
    assert accord.exit_code == 0, accord.output
    assert _compter(m.Salle) == 0 and _compter(m.Campus) == 1


def test_importer_un_emploi_du_temps_puis_l_exporter(lanceur, jeu_minimal, tmp_path):
    resultat = lanceur.invoke(
        cli.app,
        [
            "importer",
            str(DOSSIER_FICHIERS / "seances_existantes.csv"),
            "--mode",
            "reference",
            "--semestre",
            str(jeu_minimal.semestre_id),
        ],
    )
    assert resultat.exit_code == 1  # une séance au groupe inconnu est mise de côté
    assert "Version d'emploi du temps créée : n° 1" in resultat.output
    assert "Semaine type de A1" in resultat.output

    sortie = tmp_path / "g1.ics"
    export = lanceur.invoke(
        cli.app,
        ["exporter-edt", "--version", "1", "--format", "ics", "--couche", "date"]
        + ["--groupe", "A1-G1", "--sortie", str(sortie)],
    )
    assert export.exit_code == 0, export.output
    evenements = Calendar.from_ical(sortie.read_bytes()).walk("VEVENT")
    assert len(evenements) == 7  # A1-G1 suit aussi les séances de sa promotion A1


def test_fichier_introuvable_et_groupe_inconnu(lanceur, jeu_minimal, tmp_path):
    absent = lanceur.invoke(cli.app, ["importer", str(tmp_path / "absent.csv")])
    assert absent.exit_code == 1 and "introuvable" in absent.output

    inconnu = lanceur.invoke(cli.app, ["exporter-edt", "--version", "1", "--groupe", "A1-G1"])
    assert inconnu.exit_code == 1 and "n'existe pas" in inconnu.output
