"""Service `edt.services.jeux` et commande `python -m edt jeu`.

Ces tests utilisent la base de l'environnement de test (fixtures `environnement`
et `base` de `tests/conftest.py`), comme les commandes réelles.
"""

from __future__ import annotations

import types

import pytest
from sqlalchemy import func, select
from typer.testing import CliRunner

from edt import cli
from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.erreurs import ErreurCalculEnCours, ErreurRegleMetier
from edt.services import jeux


@pytest.fixture
def lanceur() -> CliRunner:
    return CliRunner()


def _nombre(modele) -> int:
    with session_base() as session:
        return session.scalar(select(func.count()).select_from(modele))


def test_tailles_disponibles():
    tailles = {t.nom: t for t in jeux.tailles_disponibles()}
    assert list(tailles) == ["petit", "moyen", "grand"]
    assert tailles["grand"].nb_promotions == 5 and tailles["grand"].semestres == ("S1", "S2")
    assert all(t.description for t in tailles.values())


def test_commande_jeu_charge_et_affiche_le_resume(lanceur, base):
    resultat = lanceur.invoke(cli.app, ["jeu", "--taille", "petit", "--reference"])
    assert resultat.exit_code == 0, resultat.output
    assert "Jeu fictif « petit » (graine 0)" in resultat.output
    assert "vérificateur sur le témoin : 0 erreur(s)" in resultat.output
    assert _nombre(m.Besoin) > 0 and _nombre(m.VersionEdt) == 1


def test_remise_a_zero_demande_confirmation(lanceur, base):
    assert lanceur.invoke(cli.app, ["jeu", "--taille", "petit"]).exit_code == 0
    besoins = _nombre(m.Besoin)

    refus = lanceur.invoke(cli.app, ["jeu", "--taille", "petit", "--reset"], input="n\n")
    assert refus.exit_code != 0
    assert _nombre(m.Besoin) == besoins

    accord = lanceur.invoke(
        cli.app, ["jeu", "--taille", "petit", "--graine", "5", "--reset", "--oui"]
    )
    assert accord.exit_code == 0, accord.output
    assert "(graine 5)" in accord.output


def test_remise_a_zero_sur_une_base_neuve(lanceur, environnement):
    # Le fichier de base n'existe pas encore : « jeu --reset » doit créer les tables
    # puis charger le jeu, sans chercher un calcul en cours dans une table absente.
    resultat = lanceur.invoke(cli.app, ["jeu", "--taille", "petit", "--reset", "--oui"])
    assert resultat.exit_code == 0, resultat.output
    assert _nombre(m.Besoin) > 0


def test_second_chargement_refuse_avec_un_message_clair(lanceur, base):
    assert lanceur.invoke(cli.app, ["jeu", "--taille", "petit"]).exit_code == 0
    second = lanceur.invoke(cli.app, ["jeu", "--taille", "petit"])
    assert second.exit_code == 1
    assert "déjà des données" in second.output


def test_taille_inconnue(lanceur, base):
    resultat = lanceur.invoke(cli.app, ["jeu", "--taille", "geant"])
    assert resultat.exit_code == 1
    assert "Taille de jeu inconnue" in resultat.output


def test_base_non_initialisee_refusee(environnement):
    with pytest.raises(ErreurRegleMetier, match="pas initialisée"):
        jeux.recharger_jeu_fictif("petit")


def test_refus_pendant_un_calcul(base, monkeypatch):
    monkeypatch.setattr(jeux, "calcul_en_cours", lambda: types.SimpleNamespace(id=42))
    with pytest.raises(ErreurCalculEnCours, match="n°42"):
        jeux.recharger_jeu_fictif("petit", reinitialiser=True)
    with session_base() as session, pytest.raises(ErreurCalculEnCours):
        jeux.charger_jeu_fictif(session, "petit")
