"""Onglets des tests dans le menu, et tableau d'une famille : état affiché de chaque test et comptes."""

from __future__ import annotations

import dataclasses
import datetime as dt
import importlib

import pytest
from tests.outils.banc_tests.depot import CLE_CHARGE, CLE_ECHEC, CLE_IGNORE, CLE_REUSSI

from edt.services.banc_tests import catalogue as cat
from edt.services.banc_tests import execution as ex
from edt.services.banc_tests import resultats as res
from edt.services.banc_tests.tableau import tableau_famille, texte_duree
from edt.web.app import modules_de_routes
from edt.web.menu import (
    ONGLET_GENERER,
    ONGLET_TESTS_INTEGRATION,
    ONGLET_TESTS_UNITAIRES,
    construire_menu,
    onglet_actif,
)

LE = dt.datetime(2026, 10, 5, 14, 30)


def _menu_de_l_application():
    entrees = []
    for nom in modules_de_routes():
        entrees += getattr(importlib.import_module(nom), "MENU", [])
    return construire_menu(entrees)


def test_deux_onglets_de_tests_colores_a_la_place_de_a_traiter():
    menu = _menu_de_l_application()

    titres = [onglet.titre for onglet in menu]
    assert titres[-2:] == [ONGLET_TESTS_UNITAIRES, ONGLET_TESTS_INTEGRATION]
    assert "À traiter" not in titres
    classes = {onglet.titre: onglet.classe for onglet in menu}
    assert classes[ONGLET_TESTS_UNITAIRES] == "onglet-tests-unitaires"
    assert classes[ONGLET_TESTS_INTEGRATION] == "onglet-tests-integration"
    assert classes[ONGLET_GENERER] == ""
    assert onglet_actif(menu, "/tests/integration").titre == ONGLET_TESTS_INTEGRATION


def test_points_a_traiter_troisieme_entree_de_generer_avec_le_compteur():
    generer = next(o for o in _menu_de_l_application() if o.titre == ONGLET_GENERER)

    assert [e.libelle for e in generer.entrees][2] == "Points à traiter"
    assert generer.entrees[2].url == "/a-traiter"
    assert generer.url == "/calculs"  # l'onglet mène toujours à la première entrée
    assert generer.url_compteur == "/a-traiter/compteur"
    assert onglet_actif((generer,), "/a-traiter").titre == ONGLET_GENERER


def _suivi(**champs) -> ex.Suivi:
    valeurs = dict(
        id="e1",
        cle_cible="famille",
        libelle="tous les tests unitaires",
        famille="unitaires",
        debut=LE,
        total=5,
        termines=(CLE_REUSSI,),
        resultats={CLE_REUSSI: res.ResultatTest(res.REUSSI, 0.1, LE)},
        test_en_cours=CLE_ECHEC,
        tests_cible=frozenset({CLE_REUSSI, CLE_ECHEC, CLE_IGNORE}),
        arret_demande=False,
        ecoule_s=3.0,
        duree_max_s=900,
    )
    valeurs.update(champs)
    return ex.Suivi(**valeurs)


class _ExecutionFigee:
    """Tient lieu d'exécution : rend toujours le même suivi."""

    en_cours = False

    def __init__(self, suivi: ex.Suivi) -> None:

        self._suivi = suivi

    def suivi(self) -> ex.Suivi:
        return self._suivi


def test_etats_affiches_pendant_une_execution(depot, monkeypatch):
    res.mettre_a_jour(lambda h: h.tests.update({CLE_IGNORE: res.ResultatTest(res.ECHEC, 0.2, LE)}))
    monkeypatch.setattr(ex, "_courante", _ExecutionFigee(_suivi()))

    t = tableau_famille("unitaires")

    assert t.ligne(CLE_REUSSI).etat == res.REUSSI  # rendu par l'exécution en cours
    assert t.ligne(CLE_ECHEC).etat == res.EN_COURS
    assert t.ligne(CLE_IGNORE).etat == res.EN_ATTENTE  # son échec précédent reste lisible
    assert t.ligne(CLE_IGNORE).resultat.etat == res.ECHEC
    assert t.ligne(CLE_CHARGE).etat == res.JAMAIS
    assert (t.compte.reussis, t.compte.en_cours, t.compte.jamais, t.compte.charge) == (1, 2, 3, 1)
    assert t.composant_de(CLE_ECHEC).nom == "demo"
    assert t.fichier_de(CLE_ECHEC).fichier.nom == "test_exemple.py"


def test_execution_d_une_autre_famille_sans_effet(depot, monkeypatch):
    monkeypatch.setattr(ex, "_courante", _ExecutionFigee(_suivi(famille="integration")))

    t = tableau_famille("unitaires")

    assert t.suivi is None and t.ligne(CLE_ECHEC).etat == res.JAMAIS


@pytest.mark.cas_limite
def test_test_supprime_depuis_sa_derniere_execution_ignore(depot):
    """Cas : l'historique garde le résultat d'un test renommé ou supprimé depuis.

    Risque : ligne fantôme, compte faux ou erreur à l'affichage."""
    res.mettre_a_jour(
        lambda h: h.tests.update(
            {
                "tests/unitaires/demo/test_disparu.py::test_ancien": res.ResultatTest(
                    res.ECHEC, 1.0, LE
                ),
                CLE_REUSSI: res.ResultatTest(res.REUSSI, 0.1, LE),
            }
        )
    )

    t = tableau_famille("unitaires")

    assert t.ligne("tests/unitaires/demo/test_disparu.py::test_ancien") is None
    assert (t.compte.total, t.compte.reussis, t.compte.echecs) == (6, 1, 0)


def test_dates_de_derniere_execution_complete_par_groupe(depot):
    def modifier(h: res.Historique) -> None:
        h.groupes[cat.cle_groupe_famille("unitaires")] = LE
        h.groupes[cat.cle_groupe_fichier("tests/unitaires/demo/test_exemple.py")] = LE

    res.mettre_a_jour(modifier)

    t = tableau_famille("unitaires")

    assert t.complet_le == LE
    assert t.composants[0].fichiers[0].complet_le == LE
    assert t.composants[0].complet_le is None


def test_texte_duree():
    assert [texte_duree(s) for s in (0.04, 9.96, 12.4, 125)] == [
        "0,0 s",
        "10,0 s",
        "12 s",
        "2 min 05 s",
    ]
    assert texte_duree(None) == ""


def test_suivi_compte_par_etat():
    suivi = dataclasses.replace(_suivi(), issue=res.ISSUE_TERMINEE)
    assert suivi.compte(res.REUSSI) == 1 and not suivi.en_cours
