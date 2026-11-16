"""Journalisation : fichier non ouvrable, rotation partagée entre processus, purge des calculs."""

from __future__ import annotations

import logging
import os

import pytest

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


@pytest.mark.cas_limite
def test_dossier_de_journaux_sous_un_fichier_est_une_erreur_de_configuration(tmp_path, monkeypatch):
    """Cas : EDT_LOG_DIR désigne un chemin sous un fichier existant.

    Risque : `NotADirectoryError` brute au lieu d'un message qui nomme la variable.
    """
    obstacle = tmp_path / "obstacle"
    obstacle.write_text("je suis un fichier", encoding="utf-8")
    monkeypatch.setenv("EDT_LOG_DIR", str(obstacle / "logs"))
    reinitialiser_journalisation()
    with pytest.raises(ErreurConfiguration, match="EDT_LOG_DIR"):
        configurer_journalisation()


@pytest.mark.cas_limite
def test_une_rotation_impossible_ne_perd_ni_ligne_ni_console(environnement, capsys):
    """Cas : la rotation d'`edt.log` échoue parce qu'un autre processus tient le fichier
    (service web et commande `edt` en même temps, cas de Windows).

    Risque : « --- Logging error --- » répété sur la console et lignes perdues ; la ligne
    doit être écrite dans le fichier courant, sans bruit.
    """
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


@pytest.mark.cas_limite
@pytest.mark.skipif(os.name == "nt", reason="Windows interdit de renommer un fichier ouvert")
def test_le_fichier_journal_suit_une_rotation_faite_par_un_autre_processus(environnement):
    """Cas : un autre processus (ou logrotate) a renommé `edt.log` pendant que celui-ci écrit.

    Risque : les lignes suivantes partiraient dans l'archive ou un fichier délié (incidents
    introuvables) ; le flux doit être rouvert sur le nouveau fichier.
    """
    chemin = configurer_journalisation()
    journal = logging.getLogger("edt.test.rotation")
    journal.info("avant la rotation")
    archive = chemin.with_name("edt.log.1")
    chemin.rename(archive)
    journal.info("après la rotation")

    assert "après la rotation" in chemin.read_text(encoding="utf-8")
    assert "après la rotation" not in archive.read_text(encoding="utf-8")


@pytest.mark.cas_limite
def test_les_journaux_de_calcul_les_plus_anciens_sont_purges(environnement, caplog):
    """Cas : des centaines de calculs ont laissé chacun leur fichier dans `generations/`.

    Risque : disque plein à terme (« database or disk is full » sur toute écriture) ; seuls
    les `NB_JOURNAUX_CALCUL` fichiers les plus récents doivent rester, et la purge est dite.
    """
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
    assert not (dossier / "calcul-000010-20260901-000000.log").exists()
    assert (dossier / "calcul-000011-20260901-000000.log").exists()
    assert "11 fichier(s) ancien(s) supprimé(s)" in caplog.text
