"""Garde-fous du lancement des tests, vérifiés sans lancer de sous-processus : réglages, refus, verrou, environnement."""

from __future__ import annotations

import datetime as dt
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

    monkeypatch.delenv(ex.VARIABLE_DELAI, raising=False)
    assert ex.lire_reglages().delai_s == 60

    monkeypatch.setenv(ex.VARIABLE_DUREE_MAX, "120")
    monkeypatch.setenv(ex.VARIABLE_LANCEMENT, "NON")
    monkeypatch.setenv(ex.VARIABLE_DELAI, "0")
    assert ex.lire_reglages() == ex.Reglages(duree_max_s=120, lancement_permis=False, delai_s=0)


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("variable", "valeur"),
    [
        (ex.VARIABLE_DUREE_MAX, "0"),
        (ex.VARIABLE_DUREE_MAX, "15 min"),
        (ex.VARIABLE_LANCEMENT, "peut-être"),
        (ex.VARIABLE_DELAI, "-1"),
        (ex.VARIABLE_DELAI, "une minute"),
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
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-du-serveur")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "jeton-du-serveur")

    env = ex.environnement_assaini(tmp_path, tmp_path / "evenements.jsonl", tmp_path)

    assert "EDT_HERITEE" not in env and "PYTEST_ADDOPTS" not in env
    # La clé de l'assistant, lue par le service dans son environnement, ne passe pas aux tests.
    assert not [nom for nom in env if nom.upper().startswith("ANTHROPIC_")]
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


# --- Constats de la relecture ------------------------------------------------

LE = dt.datetime(2026, 10, 5, 14, 30)
"""Heure fixe : les tests du délai ne dépendent pas de l'heure réelle."""


def _bilan_termine_le(fin: dt.datetime) -> None:
    def modifier(historique: res.Historique) -> None:
        historique.derniere = res.BilanExecution(
            "tous les tests unitaires",
            "unitaires",
            fin,
            fin,
            res.ISSUE_TERMINEE,
            "Exécution terminée.",
        )

    res.mettre_a_jour(modifier)


@pytest.mark.cas_limite
@pytest.mark.parametrize("verrou_coince", [False, True], ids=["disque-plein", "verrou-tenu-ouvert"])
def test_erreur_avant_le_sous_processus_termine_quand_meme_l_execution(
    depot, monkeypatch, verrou_coince: bool
):
    """Cas : dossier temporaire impossible à créer (disque plein), ou, sous Windows, fichier de
    verrou tenu ouvert par un lecteur au moment de le retirer.

    Risque : exécution « en cours » pour toujours, plus aucun lancement jusqu'au redémarrage."""

    def disque_plein(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(ex.tempfile, "mkdtemp", disque_plein)
    if verrou_coince:

        def verrou_tenu(dossier, execution):
            raise PermissionError(32, "fichier utilisé par un autre processus")

        monkeypatch.setattr(ex, "_rendre_verrou", verrou_tenu)

    suivi = ex.attendre(ex.lancer(_cible(), depot), 10)

    assert not suivi.en_cours and suivi.issue == res.ISSUE_ERREUR
    assert "No space left on device" in suivi.message
    assert res.lire_historique().derniere.issue == res.ISSUE_ERREUR
    if not verrou_coince:
        assert not (res.dossier_resultats() / ex.FICHIER_VERROU).exists()
    seconde = ex.lancer(_cible(), depot)  # pas « déjà en cours »
    assert not ex.attendre(seconde, 10).en_cours


@pytest.mark.cas_limite
def test_verrou_jamais_vu_vide_par_un_lecteur(depot, monkeypatch):
    """Cas : une page s'affiche pendant qu'un lancement écrit son fichier de verrou.

    Risque : le lecteur voit un fichier vide, retire le verrou d'une exécution vivante (une
    seconde exécution peut partir) ou, sous Windows, répond par une erreur 500."""
    dossier = res.dossier_resultats()
    dossier.mkdir(parents=True)
    vus: list[object] = []
    fdopen = os.fdopen

    def ecriture_observee(*args, **kwargs):
        try:  # un lecteur passe pendant l'écriture
            vus.append(ex.execution_ailleurs(dossier))
        except Exception as erreur:
            vus.append(erreur)
        return fdopen(*args, **kwargs)

    monkeypatch.setattr(ex.os, "fdopen", ecriture_observee)
    execution = ex.Execution(
        id="e1", cible=_cible(), racine=depot, dossier=dossier, duree_max_s=900, debut=LE
    )

    ex._prendre_verrou(dossier, execution)

    assert vus and all(v is None for v in vus), vus
    contenu = json.loads((dossier / ex.FICHIER_VERROU).read_text(encoding="utf-8"))
    assert (contenu["pid"], contenu["execution"]) == (os.getpid(), "e1")
    assert res.lire_historique().derniere is None  # aucune interruption inventée
    assert [f.name for f in dossier.iterdir()] == [ex.FICHIER_VERROU]  # aucun fichier temporaire


@pytest.mark.cas_limite
def test_delai_minimal_entre_deux_lancements_depuis_le_site(depot, monkeypatch):
    """Cas : un visiteur (ou un script) relance les tests dès la fin de l'exécution précédente.

    Risque : serveur de deux cœurs occupé en continu par les tests."""
    monkeypatch.setattr(res, "maintenant", lambda: LE)
    _bilan_termine_le(LE - dt.timedelta(seconds=10))

    with pytest.raises(ex.ErreurLancementImpossible) as refus:
        ex.lancer(_cible(), depot, depuis_le_site=True)

    assert "14 h 31" in str(refus.value) and "50 s" in str(refus.value)
    assert ex.execution_courante() is None
    assert ex.prochain_lancement_site() == LE + dt.timedelta(seconds=50)
    monkeypatch.setenv(ex.VARIABLE_DELAI, "5")
    assert ex.prochain_lancement_site() is None  # délai écoulé


@pytest.mark.cas_limite
def test_test_de_charge_refuse_depuis_le_site(depot):
    """Cas : un visiteur lance un à un les tests de charge (jusqu'à quinze minutes chacun).

    Risque : serveur saturé pendant la démonstration ; ces tests se lancent par la commande."""
    charge = _cible(f"test:{CLE_CHARGE}")
    assert charge.charge and not _cible(f"test:{CLE_REUSSI}").charge

    with pytest.raises(ex.ErreurLancementImpossible, match="python -m edt tests --test"):
        ex.lancer(charge, depot, depuis_le_site=True)
    assert ex.execution_courante() is None


class _EnCours:
    en_cours = True


def test_tests_en_cours_pour_un_autre_service(depot, monkeypatch):
    dossier = res.dossier_resultats()
    assert ex.tests_en_cours() is False

    monkeypatch.setattr(ex, "_courante", _EnCours())
    assert ex.tests_en_cours() is True  # exécution de ce processus
    monkeypatch.setattr(ex, "_courante", None)

    dossier.mkdir(parents=True)
    verrou = dossier / ex.FICHIER_VERROU
    verrou.write_text(json.dumps({"pid": os.getppid(), "execution": "x"}), encoding="utf-8")
    assert ex.tests_en_cours() is True  # exécution d'un autre processus vivant

    verrou.write_text(json.dumps({"pid": PID_INEXISTANT, "execution": "x"}), encoding="utf-8")
    assert ex.tests_en_cours() is False
    verrou.write_text("{pas du json", encoding="utf-8")
    assert ex.tests_en_cours() is False
    assert verrou.exists()  # simple lecture : rien n'est retiré ni écrit
    assert res.lire_historique().derniere is None
