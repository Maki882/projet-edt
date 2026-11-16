"""Garde-fous du lancement des tests, vérifiés sans lancer de sous-processus : réglages, refus, verrou, environnement."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from tests.outils.banc_tests.depot import CLE_CHARGE, CLE_REUSSI

from edt.erreurs import ErreurConfiguration
from edt.services import generation
from edt.services.banc_tests import catalogue as cat
from edt.services.banc_tests import execution as ex
from edt.services.banc_tests import resultats as res

PID_INEXISTANT = 2**31 - 2
"""Numéro de processus qui n'existe sur aucune machine de test."""


def _cible(cle: str = "famille") -> cat.Cible:
    return cat.resoudre_cible(cat.lire_catalogue(), cle, "unitaires")


def test_reglages_par_defaut_et_valeurs_lues(monkeypatch):
    monkeypatch.delenv(ex.VARIABLE_DUREE_MAX, raising=False)
    monkeypatch.delenv(ex.VARIABLE_LANCEMENT, raising=False)
    assert ex.lire_reglages() == ex.Reglages(duree_max_s=900, lancement_permis=True)

    monkeypatch.setenv(ex.VARIABLE_DUREE_MAX, "120")
    monkeypatch.setenv(ex.VARIABLE_LANCEMENT, "NON")
    assert ex.lire_reglages() == ex.Reglages(duree_max_s=120, lancement_permis=False)


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("variable", "valeur"),
    [
        (ex.VARIABLE_DUREE_MAX, "0"),
        (ex.VARIABLE_DUREE_MAX, "15 min"),
        (ex.VARIABLE_LANCEMENT, "peut-être"),
    ],
)
def test_reglage_invalide_refuse_avec_son_nom(monkeypatch, variable: str, valeur: str):
    """Cas : variable EDT_TESTS_* mal écrite dans le fichier d'environnement du serveur.

    Risque : durée maximale ignorée en silence (tests sans limite sur un petit serveur)."""
    monkeypatch.setenv(variable, valeur)

    with pytest.raises(ErreurConfiguration, match=variable):
        ex.lire_reglages()


@pytest.mark.cas_limite
def test_lancement_desactive_laisse_les_resultats_consultables(depot, monkeypatch):
    """Cas : EDT_TESTS_LANCEMENT=non sur le serveur.

    Risque : un visiteur lance quand même des tests, ou la page cache les résultats."""
    monkeypatch.setenv(ex.VARIABLE_LANCEMENT, "non")

    assert "désactivé" in ex.raison_indisponible(cat.lire_catalogue())
    with pytest.raises(ex.ErreurLancementImpossible, match="désactivé"):
        ex.lancer(_cible(), depot)
    assert ex.execution_courante() is None


@pytest.mark.cas_limite
def test_pytest_absent_refuse_le_lancement(depot, monkeypatch):
    """Cas : serveur installé sans les dépendances de développement (pas de pytest).

    Risque : sous-processus qui échoue aussitôt, avec un message incompréhensible."""
    assert ex.pytest_installe()  # l'environnement des tests l'a, évidemment
    monkeypatch.setattr(ex, "pytest_installe", lambda: False)

    assert "pytest" in ex.raison_indisponible(cat.lire_catalogue())
    with pytest.raises(ex.ErreurLancementImpossible, match="pytest"):
        ex.lancer(_cible(), depot)


@pytest.mark.cas_limite
def test_refus_pendant_un_calcul(depot, base, monkeypatch):
    """Cas : un calcul d'emploi du temps tourne quand quelqu'un lance les tests.

    Risque : les deux se disputent les deux cœurs du serveur ; le calcul atteint son temps limite."""
    monkeypatch.setattr(generation, "calcul_en_cours", lambda: object())
    dossier = res.dossier_resultats()

    with pytest.raises(ex.ErreurLancementImpossible, match="calcul"):
        ex.lancer(_cible(), depot)
    assert ex.execution_courante() is None
    assert not (dossier / ex.FICHIER_VERROU).exists()


def test_aucun_calcul_sans_base(environnement):
    assert ex.calcul_en_cours() is False  # la base du test n'existe pas encore


def test_aucun_calcul_sur_une_base_neuve(base):
    assert ex.calcul_en_cours() is False


@pytest.mark.cas_limite
def test_verrou_abandonne_retire_et_interruption_notee(depot):
    """Cas : serveur arrêté ou redémarré pendant une exécution des tests.

    Risque : l'état « en cours » reste bloqué et plus aucun test ne peut être lancé."""
    dossier = res.dossier_resultats()
    dossier.mkdir(parents=True)
    verrou = dossier / ex.FICHIER_VERROU
    verrou.write_text(
        json.dumps(
            {
                "pid": PID_INEXISTANT,
                "execution": "ancienne",
                "libelle": "tous les tests unitaires",
                "famille": "unitaires",
                "debut": "2026-10-05T14:00:00",
            }
        ),
        encoding="utf-8",
    )

    assert ex.execution_ailleurs() is None

    assert not verrou.exists()
    bilan = res.lire_historique().derniere
    assert bilan.issue == res.ISSUE_INTERROMPUE and "interrompue" in bilan.message


@pytest.mark.cas_limite
def test_verrou_d_un_processus_vivant_refuse_le_lancement(depot):
    """Cas : la commande `python -m edt tests` tourne pendant qu'un visiteur clique sur « Lancer ».

    Risque : deux exécutions en même temps, résultats écrasés l'un par l'autre."""
    dossier = res.dossier_resultats()
    dossier.mkdir(parents=True)
    (dossier / ex.FICHIER_VERROU).write_text(
        json.dumps(
            {
                "pid": os.getppid(),
                "execution": "autre",
                "libelle": "le composant demo",
                "debut": "2026-10-05T14:00:00",
            }
        ),
        encoding="utf-8",
    )

    assert ex.execution_ailleurs()["libelle"] == "le composant demo"
    with pytest.raises(ex.ErreurTestsEnCours, match="ligne de commande"):
        ex.lancer(_cible(), depot)
    assert (dossier / ex.FICHIER_VERROU).exists()  # le verrou de l'autre processus est gardé


def test_environnement_assaini(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EDT_DB_URL", "sqlite:///base-reelle.db")
    monkeypatch.setenv("EDT_HERITEE", "1")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-x")
    monkeypatch.setenv("PYTHONPATH", "ailleurs")

    env = ex.environnement_assaini(tmp_path, tmp_path / "evenements.jsonl", tmp_path)

    assert "EDT_HERITEE" not in env and "PYTEST_ADDOPTS" not in env
    assert env["EDT_DB_URL"] == f"sqlite:///{(tmp_path / 'edt.db').as_posix()}"
    assert env["EDT_LOG_DIR"] == str(tmp_path / "journaux")
    assert env["PYTHONDONTWRITEBYTECODE"] == "1" and env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONPATH"] != "ailleurs" and Path(env["PYTHONPATH"], "edt").is_dir()
    assert env[ex.VARIABLE_EVENEMENTS] == str(tmp_path / "evenements.jsonl")


def test_commande_pytest_faite_des_seuls_chemins_du_catalogue(depot, tmp_path):
    groupe = ex.commande_pytest(_cible(), depot, tmp_path)
    seul = ex.commande_pytest(_cible(f"test:{CLE_CHARGE}"), depot, tmp_path)

    assert groupe[:4] == [sys.executable, "-m", "pytest", "tests/unitaires"]
    assert groupe[-2:] == ["-m", "not perf"]  # tests de charge exclus d'un groupe
    for option in ("-p", ex.GREFFON, "no:cacheprovider", "--color=no"):
        assert option in groupe
    assert seul[3] == CLE_CHARGE and "not perf" not in seul
    assert ex.commande_pytest(_cible(f"test:{CLE_REUSSI}"), depot, tmp_path)[3] == CLE_REUSSI
