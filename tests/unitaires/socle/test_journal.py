"""Journalisation : fichier non ouvrable, rotation partagée entre processus, purge des calculs."""

from __future__ import annotations

import logging
import os
import shutil

import pytest

from edt.erreurs import ErreurConfiguration
from edt.journal import (
    NB_ARCHIVES,
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
    obstacle.write_text("pas un dossier", encoding="utf-8")
    monkeypatch.setenv("EDT_LOG_DIR", str(obstacle / "logs"))
    reinitialiser_journalisation()
    with pytest.raises(ErreurConfiguration, match="EDT_LOG_DIR"):
        configurer_journalisation()


@pytest.mark.cas_limite
def test_une_rotation_impossible_ne_perd_ni_ligne_ni_archive_ni_console(environnement, capsys):
    """Cas : la rotation d'`edt.log` échoue parce qu'un autre processus tient le fichier
    (service web et commande `edt` en même temps, cas de Windows), et cinq archives existent.

    Risque : « --- Logging error --- » répété sur la console, lignes perdues, et une archive
    détruite à chaque ligne (archives décalées avant le renommage refusé) ; la ligne doit
    être écrite dans le fichier courant, les archives rester intactes, l'échec être dit une
    seule fois.
    """
    chemin = configurer_journalisation()
    gestionnaire = _fichier_tournant()
    archives = [chemin.with_name(f"{chemin.name}.{rang}") for rang in range(1, NB_ARCHIVES + 1)]
    for rang, archive in enumerate(archives, start=1):
        archive.write_text(f"archive {rang}", encoding="utf-8")
    gestionnaire.maxBytes = 50  # chaque ligne suivante déclenche une rotation

    def rotation_refusee(source: str, destination: str) -> None:
        raise PermissionError(f"{source} tenu par un autre processus")

    gestionnaire.rotate = rotation_refusee  # type: ignore[method-assign]
    journal = logging.getLogger("edt.test.rotation")
    journal.info("ligne qui déclenche la rotation")
    for numero in range(4):
        journal.info("ligne suivante %d", numero)

    contenu = chemin.read_text(encoding="utf-8")
    assert "ligne qui déclenche la rotation" in contenu and "ligne suivante 3" in contenu
    assert contenu.count("Rotation de") == 1 and "tenu par un autre processus" in contenu
    for rang, archive in enumerate(archives, start=1):
        assert archive.read_text(encoding="utf-8") == f"archive {rang}"
    assert "Logging error" not in capsys.readouterr().err


@pytest.mark.cas_limite
def test_une_rotation_reussie_decale_les_archives(environnement):
    """Cas : `edt.log` atteint sa taille maximale alors que deux archives existent.

    Risque : une rotation qui perd ou écrase une archive ; `.1` doit devenir `.2`, le
    fichier courant `.1`, et la ligne suivante partir dans un fichier neuf.
    """
    chemin = configurer_journalisation()
    gestionnaire = _fichier_tournant()
    chemin.with_name("edt.log.1").write_text("archive 1", encoding="utf-8")
    chemin.with_name("edt.log.2").write_text("archive 2", encoding="utf-8")
    journal = logging.getLogger("edt.test.rotation")
    journal.info("avant la rotation")
    gestionnaire.maxBytes = 50
    journal.info("après la rotation")

    assert "après la rotation" in chemin.read_text(encoding="utf-8")
    assert "avant la rotation" in chemin.with_name("edt.log.1").read_text(encoding="utf-8")
    assert chemin.with_name("edt.log.2").read_text(encoding="utf-8") == "archive 1"
    assert chemin.with_name("edt.log.3").read_text(encoding="utf-8") == "archive 2"


@pytest.mark.cas_limite
def test_un_dossier_de_journaux_supprime_ne_fait_pas_lever_le_code_qui_journalise(
    environnement, capsys
):
    """Cas : le dossier des journaux est supprimé pendant que le serveur tourne (nettoyage,
    disque démonté).

    Risque : `FileNotFoundError` levée par la réouverture du fichier dans le code qui
    journalise, à chaque ligne : requête web en erreur 500, calcul interrompu. La panne
    doit passer par le traitement d'erreur de `logging`, et l'écriture reprendre quand le
    dossier revient.
    """
    chemin = configurer_journalisation()
    journal = logging.getLogger("edt.test.rotation")
    journal.info("avant la suppression")
    _fichier_tournant().close()  # Windows refuse de supprimer un fichier ouvert
    shutil.rmtree(chemin.parent)

    journal.warning("pendant la suppression")  # ne lève pas
    journal.warning("encore pendant la suppression")
    assert "Logging error" in capsys.readouterr().err  # la panne est dite, pas tue

    chemin.parent.mkdir()
    journal.warning("après le retour du dossier")
    assert "après le retour du dossier" in chemin.read_text(encoding="utf-8")


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
    L'ordre suit la date portée par le nom, ni la date de modification (une copie ou une
    restauration la change) ni le numéro (il repart de 1 après une remise à zéro) : ici,
    les dates de modification vont à rebours des noms et le plus récent des anciens
    fichiers porte le numéro 1.
    """
    dossier = environnement / "logs" / "generations"
    dossier.mkdir(parents=True)
    nb_anciens = 60
    noms = [f"calcul-{numero + 100:06d}-20260901-{numero:06d}.log" for numero in range(nb_anciens)]
    noms[-1] = f"calcul-000001-20260901-{nb_anciens - 1:06d}.log"
    for rang, nom in enumerate(noms):
        fichier = dossier / nom
        fichier.write_text("ancien", encoding="utf-8")
        horodatage = 1_600_000_000 - rang
        os.utime(fichier, (horodatage, horodatage))

    with caplog.at_level(logging.INFO, logger="edt.journal"), ouvrir_journal_calcul(61):
        pass

    supprimes = nb_anciens + 1 - NB_JOURNAUX_CALCUL
    restants = {fichier.name for fichier in dossier.glob("calcul-*.log")}
    assert len(restants) == NB_JOURNAUX_CALCUL
    assert any(nom.startswith("calcul-000061-") for nom in restants)  # le nouveau est là
    assert restants.isdisjoint(noms[:supprimes]) and set(noms[supprimes:]) <= restants
    assert f"{supprimes} fichier(s) ancien(s) supprimé(s)" in caplog.text
