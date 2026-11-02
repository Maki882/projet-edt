"""Journalisation : fichier non ouvrable, rotation partagée entre processus, purge des calculs."""

from __future__ import annotations

import logging
import os

import pytest
from typer.testing import CliRunner

from edt import cli
from edt.erreurs import ErreurConfiguration
from edt.journal import (
    NB_JOURNAUX_CALCUL,
    _FichierTournant,
    configurer_journalisation,
    ouvrir_journal_calcul,
    reinitialiser_journalisation,
)


def _fichier_tournant() -> _FichierTournant:
    (gestionnaire,) = [g for g in logging.getLogger().handlers if isinstance(g, _FichierTournant)]
    return gestionnaire


def test_fichier_journal_non_ouvrable_leve_une_erreur_de_configuration_et_doctor_le_dit(
    tmp_path, monkeypatch
):
    dossier = tmp_path / "journaux"
    (dossier / "edt.log").mkdir(parents=True)  # un DOSSIER porte le nom du fichier journal
    monkeypatch.setenv("EDT_LOG_DIR", str(dossier))
    reinitialiser_journalisation()

    with pytest.raises(ErreurConfiguration) as capture:
        configurer_journalisation()
    message = str(capture.value)
    assert str(dossier / "edt.log") in message and "EDT_LOG_DIR" in message
    assert "droits" in message

    lanceur = CliRunner()
    resultat = lanceur.invoke(cli.app, ["version"])
    assert resultat.exit_code == cli.CODE_ERREUR_METIER
    assert resultat.output.count("Impossible d'ouvrir le fichier journal") == 1  # une seule fois
    assert "Erreur inattendue" not in resultat.output and "Traceback" not in resultat.output

    doctor = lanceur.invoke(cli.app, ["doctor"])  # tourne quand même, en repli console
    assert doctor.exit_code == 1, doctor.output
    assert "Journaux" in doctor.output and "ECHEC" in doctor.output
    assert "Impossible d'ouvrir le fichier journal" in doctor.output


def test_dossier_de_journaux_sous_un_fichier_est_une_erreur_de_configuration(tmp_path, monkeypatch):
    obstacle = tmp_path / "obstacle"
    obstacle.write_text("je suis un fichier", encoding="utf-8")
    monkeypatch.setenv("EDT_LOG_DIR", str(obstacle / "logs"))
    reinitialiser_journalisation()
    with pytest.raises(ErreurConfiguration, match="EDT_LOG_DIR"):
        configurer_journalisation()


def test_une_rotation_impossible_ne_perd_ni_ligne_ni_console(environnement, capsys):
    chemin = configurer_journalisation()
    gestionnaire = _fichier_tournant()
    gestionnaire.maxBytes = 50  # la prochaine ligne déclenche une rotation

    def rotation_refusee(source: str, destination: str) -> None:
        raise PermissionError(f"{source} tenu par un autre processus")

    gestionnaire.rotate = rotation_refusee  # type: ignore[method-assign]
    journal = logging.getLogger("edt.test.rotation")
    journal.info("ligne qui déclenche la rotation")
    journal.info("ligne suivante")

    contenu = chemin.read_text(encoding="utf-8")
    assert "ligne qui déclenche la rotation" in contenu and "ligne suivante" in contenu
    assert "Logging error" not in capsys.readouterr().err


@pytest.mark.skipif(os.name == "nt", reason="Windows interdit de renommer un fichier ouvert")
def test_le_fichier_journal_suit_une_rotation_faite_par_un_autre_processus(environnement):
    chemin = configurer_journalisation()
    journal = logging.getLogger("edt.test.rotation")
    journal.info("avant la rotation")
    archive = chemin.with_name("edt.log.1")
    chemin.rename(archive)  # un autre processus vient de tourner le fichier
    journal.info("après la rotation")

    assert "après la rotation" in chemin.read_text(encoding="utf-8")
    assert "après la rotation" not in archive.read_text(encoding="utf-8")


def test_les_journaux_de_calcul_les_plus_anciens_sont_purges(environnement, caplog):
    dossier = environnement / "logs" / "generations"
    dossier.mkdir(parents=True)
    for numero in range(60):
        fichier = dossier / f"calcul-{numero:06d}-20260901-000000.log"
        fichier.write_text("ancien", encoding="utf-8")
        horodatage = 1_600_000_000 + numero  # du plus ancien (0) au plus récent (59)
        os.utime(fichier, (horodatage, horodatage))

    with caplog.at_level(logging.INFO, logger="edt.journal"), ouvrir_journal_calcul(61):
        pass

    restants = sorted(dossier.glob("calcul-*.log"))
    assert len(restants) == NB_JOURNAUX_CALCUL
    assert restants[-1].name.startswith("calcul-000061-")  # le nouveau est là
    assert not (dossier / "calcul-000010-20260901-000000.log").exists()  # les plus anciens partent
    assert (dossier / "calcul-000011-20260901-000000.log").exists()
    assert "11 fichier(s) ancien(s) supprimé(s)" in caplog.text
