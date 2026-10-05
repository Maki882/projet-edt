"""Ligne de commande : commandes du socle et mécanisme d'extension."""

from __future__ import annotations

import sys
import types

import pytest
import typer
from sqlalchemy import func, select
from typer.testing import CliRunner

from edt import __version__, cli
from edt.db.initialisation import base_initialisee
from edt.db.modeles import Besoin, Salle
from edt.db.moteur import obtenir_moteur, session_base
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
