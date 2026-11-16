"""Journalisation vue par la ligne de commande : fichier journal non ouvrable, signalé par doctor."""

from __future__ import annotations

import pytest

from edt import cli
from edt.erreurs import ErreurConfiguration
from edt.journal import (
    configurer_journalisation,
    reinitialiser_journalisation,
)


@pytest.mark.cas_limite
def test_fichier_journal_non_ouvrable_leve_une_erreur_de_configuration_et_doctor_le_dit(
    tmp_path, monkeypatch, lanceur
):
    """Cas : `edt.log` ne peut pas être ouvert (un dossier porte son nom, ou il appartient à
    un autre utilisateur).

    Risque : `PermissionError` brute à chaque commande (code 2, « la trace est dans le
    journal » qui n'existe pas), message en double, boucle de redémarrage du service ;
    doctor, censé diagnostiquer, ne démarrait pas.
    """
    dossier = tmp_path / "journaux"
    (dossier / "edt.log").mkdir(parents=True)
    monkeypatch.setenv("EDT_LOG_DIR", str(dossier))
    reinitialiser_journalisation()

    with pytest.raises(ErreurConfiguration) as capture:
        configurer_journalisation()
    message = str(capture.value)
    assert str(dossier / "edt.log") in message and "EDT_LOG_DIR" in message
    assert "droits" in message

    resultat = lanceur.invoke(cli.app, ["version"])
    assert resultat.exit_code == cli.CODE_ERREUR_METIER
    assert resultat.output.count("Impossible d'ouvrir le fichier journal") == 1  # une seule fois
    assert "Erreur inattendue" not in resultat.output and "Traceback" not in resultat.output

    doctor = lanceur.invoke(cli.app, ["doctor"])  # tourne quand même, en repli console
    assert doctor.exit_code == 1, doctor.output
    assert "Journaux" in doctor.output and "ECHEC" in doctor.output
    assert "Impossible d'ouvrir le fichier journal" in doctor.output
