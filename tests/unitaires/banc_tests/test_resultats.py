"""Regroupement des comptes rendus de pytest par fonction de test, et historique des résultats sur disque."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from edt.erreurs import ErreurEDT
from edt.services.banc_tests import resultats as res

LE = dt.datetime(2026, 10, 5, 14, 30)
"""Date fixe des résultats : les tests ne dépendent pas de l'heure réelle."""

F = "tests/unitaires/x/test_x.py"


def _cas(identifiant: str, *issues: tuple[str, str], **champs) -> list[dict]:
    """Comptes rendus des phases d'un cas : (phase, issue)…, puis teardown réussi si absent."""
    evenements = [{"type": "debut", "id": identifiant}]
    for phase, issue in issues:
        evenements.append(
            {
                "type": "rapport",
                "id": identifiant,
                "phase": phase,
                "issue": issue,
                "duree": 0.5,
                **champs,
            }
        )
    if not any(phase == "teardown" for phase, _ in issues):
        evenements.append(
            {
                "type": "rapport",
                "id": identifiant,
                "phase": "teardown",
                "issue": "passed",
                "duree": 0.0,
            }
        )
    return evenements


def _regrouper(evenements: list[dict]) -> res.Regroupement:
    return res.regrouper(evenements, horloge=lambda: LE)


def test_cas_parametres_regroupes_echec_si_un_cas_echoue():
    ids = [f"{F}::test_p[1]", f"{F}::test_p[2]", f"{F}::test_p[3]"]
    evenements = [{"type": "collecte", "ids": ids}]
    evenements += _cas(ids[0], ("setup", "passed"), ("call", "passed"))
    evenements += _cas(ids[1], ("setup", "passed"), ("call", "failed"), detail="assert 2 < 2")
    regroupement = _regrouper(evenements)
    assert regroupement.termines == []  # le troisième cas n'a pas encore tourné

    for evenement in _cas(ids[2], ("setup", "passed"), ("call", "passed")):
        regroupement.ajouter(evenement)

    resultat = regroupement.resultats[f"{F}::test_p"]
    assert regroupement.termines == [f"{F}::test_p"]
    assert (resultat.etat, resultat.nb_cas, resultat.nb_echecs) == (res.ECHEC, 3, 1)
    assert "test_p[2]" in resultat.detail and "assert 2 < 2" in resultat.detail
    assert resultat.duree_s == pytest.approx(3.0) and resultat.le == LE


def test_issues_d_une_fonction():
    evenements = [
        {
            "type": "collecte",
            "ids": [
                f"{F}::test_ok",
                f"{F}::test_saute",
                f"{F}::test_prepa",
                f"{F}::test_mixte[a]",
                f"{F}::test_mixte[b]",
                f"{F}::test_attendu",
            ],
        },
        *_cas(f"{F}::test_ok", ("setup", "passed"), ("call", "passed")),
        *_cas(f"{F}::test_saute", ("setup", "skipped"), raison="pas sous Windows"),
        *_cas(f"{F}::test_prepa", ("setup", "failed"), detail="fixture cassée"),
        *_cas(f"{F}::test_mixte[a]", ("setup", "passed"), ("call", "passed")),
        *_cas(f"{F}::test_mixte[b]", ("setup", "skipped"), raison="cas sans objet"),
        *_cas(
            f"{F}::test_attendu", ("setup", "passed"), ("call", "skipped"), attendu="défaut connu"
        ),
        {"type": "fin", "code": 1},
    ]

    r = _regrouper(evenements).resultats

    assert r[f"{F}::test_ok"].etat == res.REUSSI
    assert (r[f"{F}::test_saute"].etat, r[f"{F}::test_saute"].raison) == (
        res.IGNORE,
        "pas sous Windows",
    )
    assert r[f"{F}::test_prepa"].etat == res.ECHEC
    assert (
        "setup" in r[f"{F}::test_prepa"].detail and "fixture cassée" in r[f"{F}::test_prepa"].detail
    )
    assert (r[f"{F}::test_mixte"].etat, r[f"{F}::test_mixte"].nb_ignores) == (res.REUSSI, 1)
    assert r[f"{F}::test_attendu"].etat == res.IGNORE
    assert r[f"{F}::test_attendu"].raison == "échec attendu : défaut connu"


def test_evenements_d_avancement():
    regroupement = _regrouper(
        [
            {"type": "collecte", "ids": [f"{F}::test_a", f"{F}::test_b[1]"]},
            {
                "type": "erreur_collecte",
                "id": "tests/unitaires/x/test_casse.py",
                "detail": "ImportError",
            },
            {"type": "debut", "id": f"{F}::test_b[1]"},
        ]
    )
    assert regroupement.nb_cas_collectes == 2
    assert regroupement.en_cours == f"{F}::test_b"
    assert regroupement.erreurs_collecte == [("tests/unitaires/x/test_casse.py", "ImportError")]

    regroupement.ajouter({"type": "fin", "code": 2})
    assert regroupement.code == 2 and regroupement.en_cours is None


def test_historique_ecrit_puis_relu(tmp_path: Path):
    resultat = res.ResultatTest(res.ECHEC, 1.25, LE, nb_cas=3, nb_echecs=1, detail="assert 1 == 2")
    bilan = res.BilanExecution(
        "le fichier test_x.py (x)",
        "unitaires",
        LE,
        LE,
        res.ISSUE_TERMINEE,
        "Exécution terminée.",
        1,
        0,
        1,
        0,
    )

    def modifier(historique: res.Historique) -> None:
        historique.tests[f"{F}::test_a"] = resultat
        historique.groupes[f"fichier:{F}"] = LE
        historique.derniere = bilan

    res.mettre_a_jour(modifier, tmp_path)
    relu = res.lire_historique(tmp_path)

    assert relu.tests == {f"{F}::test_a": resultat}
    assert relu.groupes == {f"fichier:{F}": LE}
    assert relu.derniere == bilan and relu.erreur is None
    assert list(tmp_path.iterdir()) == [
        tmp_path / res.FICHIER_RESULTATS
    ]  # aucun fichier temporaire


def test_historique_absent_est_vide(tmp_path: Path):
    historique = res.lire_historique(tmp_path)
    assert historique.tests == {} and historique.erreur is None


@pytest.mark.cas_limite
def test_historique_illisible_signale_puis_mis_de_cote(tmp_path: Path, caplog):
    """Cas : fichier des résultats tronqué (disque plein, arrêt brutal) ou modifié à la main.

    Risque : erreur 500 sur les deux onglets, ou historique écrasé sans trace."""
    (tmp_path / res.FICHIER_RESULTATS).write_text('{"tests": {"a": ', encoding="utf-8")

    historique = res.lire_historique(tmp_path)

    assert historique.tests == {} and "illisible" in historique.erreur
    assert "illisible" in caplog.text

    res.mettre_a_jour(
        lambda h: h.tests.update({"x": res.ResultatTest(res.REUSSI, 0.1, LE)}), tmp_path
    )
    assert (tmp_path / res.FICHIER_ILLISIBLE).read_text(encoding="utf-8") == '{"tests": {"a": '
    assert res.lire_historique(tmp_path).tests.keys() == {"x"}


@pytest.mark.cas_limite
def test_entrees_mal_formees_ignorees_une_a_une(tmp_path: Path):
    """Cas : une entrée du fichier des résultats a un état inconnu ou une date illisible.

    Risque : tout l'historique perdu pour une seule ligne fausse."""
    donnees = {
        "tests": {
            "bon": {"etat": "reussi", "duree_s": 0.1, "le": LE.isoformat()},
            "etat_inconnu": {"etat": "bizarre", "le": LE.isoformat()},
            "date_fausse": {"etat": "echec", "le": "hier"},
        },
        "groupes": {"famille:unitaires": "pas une date"},
    }
    (tmp_path / res.FICHIER_RESULTATS).write_text(json.dumps(donnees), encoding="utf-8")

    historique = res.lire_historique(tmp_path)

    assert historique.tests.keys() == {"bon"}
    assert historique.groupes == {}
    assert historique.erreur.startswith("3 résultat(s)")


@pytest.mark.cas_limite
def test_ecriture_impossible_levee_avec_le_chemin(tmp_path: Path):
    """Cas : dossier des journaux en lecture seule ou disque plein au moment d'enregistrer.

    Risque : résultats perdus sans le dire."""
    occupe = tmp_path / "fichier-et-non-dossier"
    occupe.write_text("x", encoding="utf-8")

    with pytest.raises(ErreurEDT, match="EDT_LOG_DIR"):
        res.mettre_a_jour(lambda h: None, occupe)
