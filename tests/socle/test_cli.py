"""Ligne de commande : commandes du socle et mécanisme d'extension."""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
import stat
import sys
import types

import pytest
import typer
from sqlalchemy import func, select
from typer.testing import CliRunner

from edt import __version__, cli
from edt.commandes import base as module_base
from edt.db import moteur as module_moteur
from edt.db.initialisation import base_initialisee
from edt.db.modeles import Besoin, Calcul, Parametre, Salle
from edt.db.moteur import liberer_moteurs, obtenir_moteur, session_base
from edt.domaine.enumerations import EtatCalcul
from edt.erreurs import ErreurConfiguration, ErreurRegleMetier

NOM_MODULE_ESSAI = "edt.commandes.essai_socle"


@pytest.fixture
def lanceur() -> CliRunner:
    return CliRunner()


def test_version(lanceur):
    resultat = lanceur.invoke(cli.app, ["version"])
    assert resultat.exit_code == 0
    assert __version__ in resultat.output


def test_init_db_cree_la_base(lanceur, environnement):
    assert not base_initialisee(obtenir_moteur())

    resultat = lanceur.invoke(cli.app, ["init-db"])

    assert resultat.exit_code == 0, resultat.output
    assert "Base initialisée" in resultat.output
    assert base_initialisee(obtenir_moteur())
    assert (environnement / "logs" / "edt.log").exists()


def test_init_db_reset_efface_les_donnees(lanceur, jeu_minimal):
    resultat = lanceur.invoke(cli.app, ["init-db", "--reset"])

    assert resultat.exit_code == 0, resultat.output
    assert "remise à zéro" in resultat.output
    with session_base() as session:
        assert session.scalar(select(func.count()).select_from(Salle)) == 0


def test_jeu_minimal_puis_refus_du_second_chargement(lanceur, base):
    premier = lanceur.invoke(cli.app, ["jeu-minimal"])
    assert premier.exit_code == 0, premier.output
    assert "16 besoins" in premier.output
    with session_base() as session:
        assert session.scalar(select(func.count()).select_from(Besoin)) == 16

    second = lanceur.invoke(cli.app, ["jeu-minimal"])
    assert second.exit_code == cli.CODE_ERREUR_METIER
    assert "contient déjà" in second.output


def test_jeu_minimal_sans_base_initialisee(lanceur, environnement):
    resultat = lanceur.invoke(cli.app, ["jeu-minimal"])
    assert resultat.exit_code == cli.CODE_ERREUR_METIER
    assert "init-db" in resultat.output


def test_doctor(lanceur, base):
    resultat = lanceur.invoke(cli.app, ["doctor"])

    assert resultat.exit_code == 0, resultat.output
    for mot in ("Python", "OR-Tools", "Base", "Journaux", "Port"):
        assert mot in resultat.output
    assert "ECHEC" not in resultat.output
    assert "Tout est prêt." in resultat.output


def test_doctor_signale_une_base_non_initialisee(lanceur, environnement):
    resultat = lanceur.invoke(cli.app, ["doctor"])
    assert resultat.exit_code == 0  # un avertissement, pas un échec
    assert "ATTENTION" in resultat.output and "non initialisée" in resultat.output


def test_configuration_invalide_donne_un_message_clair(lanceur, monkeypatch):
    monkeypatch.setenv("EDT_PORT", "pas-un-port")
    resultat = lanceur.invoke(cli.app, ["doctor"])
    assert resultat.exit_code == cli.CODE_ERREUR_METIER
    assert "EDT_PORT" in resultat.output


def test_module_de_commandes_decouvert(lanceur, monkeypatch):
    commandes = typer.Typer()

    @commandes.command("essai-refus")
    def essai_refus() -> None:
        """Commande d'essai qui échoue sur une règle métier."""
        raise ErreurRegleMetier("Refus d'essai.")

    @commandes.command("essai-panne")
    def essai_panne() -> None:
        """Commande d'essai qui plante."""
        raise RuntimeError("panne d'essai")

    module = types.ModuleType(NOM_MODULE_ESSAI)
    module.commandes = commandes
    monkeypatch.setitem(sys.modules, NOM_MODULE_ESSAI, module)
    reels = cli.modules_de_commandes()
    monkeypatch.setattr(cli, "modules_de_commandes", lambda: [*reels, NOM_MODULE_ESSAI])
    app = cli.construire_app()

    assert "essai-refus" in lanceur.invoke(app, ["--help"]).output
    refus = lanceur.invoke(app, ["essai-refus"])
    assert refus.exit_code == cli.CODE_ERREUR_METIER and "Refus d'essai." in refus.output
    panne = lanceur.invoke(app, ["essai-panne"])
    assert panne.exit_code == cli.CODE_ERREUR_INATTENDUE and "journal" in panne.output


def test_module_de_commandes_sans_objet_commandes_refuse(monkeypatch):
    monkeypatch.setitem(sys.modules, NOM_MODULE_ESSAI, types.ModuleType(NOM_MODULE_ESSAI))
    monkeypatch.setattr(cli, "modules_de_commandes", lambda: [NOM_MODULE_ESSAI])
    with pytest.raises(ErreurConfiguration, match="commandes"):
        cli.construire_app()


def test_point_d_entree_renvoie_le_code_de_sortie(base):
    assert cli.main(["version"]) == 0
    assert cli.main(["commande-inconnue"]) == 2


def test_les_sorties_sont_forcees_en_utf8(monkeypatch):
    """Sous Windows, une sortie redirigée doit rester lisible : les accents sortent en UTF-8."""
    import io
    import sys

    from edt.cli import _forcer_sorties_utf8

    tampon = io.BytesIO()
    flux = io.TextIOWrapper(tampon, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", flux)

    _forcer_sorties_utf8()
    print("séances placées", file=flux, flush=True)

    assert flux.encoding.lower().replace("-", "") == "utf8"
    assert tampon.getvalue().decode("utf-8").strip() == "séances placées"


# --------------------------------------------------------------------------- #
# Cas limites : base inaccessible, remise à zéro
# --------------------------------------------------------------------------- #


def _compter_salles() -> int:
    with session_base() as session:
        return session.scalar(select(func.count()).select_from(Salle))


@pytest.mark.parametrize("panne", ["verrou", "lecture_seule", "corrompue"])
def test_une_base_verrouillee_lecture_seule_ou_corrompue_donne_un_message_clair_et_le_code_1(
    lanceur, base, environnement, monkeypatch, panne
):
    chemin = environnement / "edt.db"
    with session_base() as session:  # init-db aura une ligne à écrire
        session.delete(session.get(Parametre, "pause_min"))
    liberer_moteurs()
    monkeypatch.setattr(module_moteur, "DELAI_VERROU_MS", 400)
    brute = None
    try:
        if panne == "verrou":
            brute = sqlite3.connect(chemin)
            brute.execute("BEGIN IMMEDIATE")  # un autre processus tient le verrou d'écriture
            attendu = "verrouillée"
        elif panne == "lecture_seule":
            os.chmod(chemin, stat.S_IREAD)
            attendu = "lecture seule"
        else:
            for suffixe in ("-wal", "-shm"):
                (environnement / f"edt.db{suffixe}").unlink(missing_ok=True)
            chemin.write_text("x" * 1200, encoding="utf-8")  # plus une base SQLite
            attendu = "n'est pas une base SQLite"
        for commande in (["init-db"], ["jeu-minimal"]):
            resultat = lanceur.invoke(cli.app, commande)
            assert resultat.exit_code == cli.CODE_ERREUR_METIER, (commande, resultat.output)
            assert attendu in resultat.output, (commande, resultat.output)
            assert "Traceback" not in resultat.output and "inattendue" not in resultat.output
    finally:
        if brute is not None:
            brute.rollback()
            brute.close()
        if panne == "lecture_seule":
            os.chmod(chemin, stat.S_IWRITE | stat.S_IREAD)


def test_init_db_reset_exige_une_confirmation_dans_un_terminal_et_aucun_calcul_en_cours(
    lanceur, jeu_minimal, environnement, monkeypatch
):
    monkeypatch.setattr(module_base, "_entree_interactive", lambda: True)
    refus = lanceur.invoke(cli.app, ["init-db", "--reset"], input="n\n")
    assert refus.exit_code != 0
    assert _compter_salles() == jeu_minimal.nb_salles  # rien d'effacé

    # Un calcul en cours dans un autre processus (vivant) : refus, même avec --oui.
    with session_base() as session:
        calcul = Calcul(
            semestre_id=jeu_minimal.semestre_id,
            etat=EtatCalcul.EN_COURS,
            demarre_le=dt.datetime.now(),
            statistiques={"pid": os.getppid()},
        )
        session.add(calcul)
        session.flush()
        calcul_id = calcul.id
    pendant = lanceur.invoke(cli.app, ["init-db", "--reset", "--oui"])
    assert pendant.exit_code == cli.CODE_ERREUR_METIER, pendant.output
    assert "calcul" in pendant.output and _compter_salles() == jeu_minimal.nb_salles
    with session_base() as session:
        session.get(Calcul, calcul_id).etat = EtatCalcul.ERREUR

    # Hors calcul, en production : sauvegarde du fichier puis remise à zéro.
    monkeypatch.setenv("EDT_ENV", "prod")
    fait = lanceur.invoke(cli.app, ["init-db", "--reset", "--oui"])
    assert fait.exit_code == 0, fait.output
    assert "Sauvegarde" in fait.output and _compter_salles() == 0
    (copie,) = environnement.glob("edt-*-avant-reset.db")
    with sqlite3.connect(copie) as sauvegarde:
        assert (
            sauvegarde.execute("SELECT count(*) FROM salle").fetchone()[0] == jeu_minimal.nb_salles
        )


def test_init_db_reset_non_interactif_ne_pose_pas_de_question(lanceur, jeu_minimal, environnement):
    # Un script (déploiement) n'est pas interrogé ; en développement, pas de sauvegarde.
    resultat = lanceur.invoke(cli.app, ["init-db", "--reset"])
    assert resultat.exit_code == 0, resultat.output
    assert _compter_salles() == 0 and not list(environnement.glob("edt-*-avant-reset.db"))
