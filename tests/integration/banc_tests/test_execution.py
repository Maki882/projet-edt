"""Exécution réelle des tests d'un dépôt factice par le service, dans un sous-processus pytest : résultats, arrêt, durée maximale."""

from __future__ import annotations

import time

import pytest
from tests.outils.banc_tests.depot import (
    CLE_CASSE,
    CLE_CHARGE,
    CLE_ECHEC,
    CLE_ENVIRONNEMENT,
    CLE_IGNORE,
    CLE_LENT,
    CLE_METHODE,
    CLE_PARAMETRE,
    CLE_REUSSI,
    EXEMPLE,
    attendre_fin,
)

from edt.services.banc_tests import catalogue as cat
from edt.services.banc_tests import execution as ex
from edt.services.banc_tests import resultats as res


def _lancer(cible: str, famille: str = "unitaires") -> ex.Execution:
    catalogue = cat.lire_catalogue()
    return ex.lancer(cat.resoudre_cible(catalogue, cible, famille), catalogue.racine)


def _attendre_le_test(execution: ex.Execution, cle: str) -> None:
    """Attend que le test `cle` tourne dans le sous-processus (30 s au plus)."""
    echeance = time.monotonic() + 30
    while execution.suivi().test_en_cours != cle:
        assert execution.en_cours, execution.message
        assert time.monotonic() < echeance, f"{cle} n'a pas démarré"
        time.sleep(0.05)


def test_famille_lancee_de_bout_en_bout_et_resultats_enregistres(depot):

    suivi = attendre_fin(_lancer("famille"))

    assert suivi.issue == res.ISSUE_TERMINEE
    etats = {cle: r.etat for cle, r in suivi.resultats.items()}
    assert etats == {
        CLE_REUSSI: res.REUSSI,
        CLE_ECHEC: res.ECHEC,
        CLE_IGNORE: res.IGNORE,
        CLE_PARAMETRE: res.ECHEC,  # un cas sur trois échoue
        CLE_METHODE: res.REUSSI,
    }  # le test de charge n'est pas lancé avec le groupe
    assert suivi.total == 5 and len(suivi.termines) == 5
    assert "message de l'assertion" in suivi.resultats[CLE_ECHEC].detail
    assert suivi.resultats[CLE_IGNORE].raison == "raison de démonstration"
    assert suivi.resultats[CLE_PARAMETRE].nb_cas == 3

    historique = res.lire_historique()
    assert historique.tests.keys() == etats.keys()
    assert {"famille:unitaires", "composant:unitaires/demo", f"fichier:{EXEMPLE}"} <= set(
        historique.groupes
    )
    assert historique.derniere.nb_echecs == 2 and historique.derniere.issue == res.ISSUE_TERMINEE
    journal = (res.dossier_resultats() / res.FICHIER_JOURNAL).read_text(encoding="utf-8")
    assert "2 failed" in journal
    assert not (res.dossier_resultats() / ex.FICHIER_VERROU).exists()


def test_environnement_du_sous_processus_assaini(depot, monkeypatch):
    monkeypatch.setenv("EDT_HERITEE", "valeur du serveur")

    suivi = attendre_fin(_lancer(f"test:{CLE_ENVIRONNEMENT}", "integration"))

    assert suivi.resultats[CLE_ENVIRONNEMENT].etat == res.REUSSI, suivi.resultats[
        CLE_ENVIRONNEMENT
    ].detail


def test_test_de_charge_lance_seul(depot):
    suivi = attendre_fin(_lancer(f"test:{CLE_CHARGE}"))

    assert suivi.resultats[CLE_CHARGE].etat == res.REUSSI
    assert res.lire_historique().groupes == {}  # un test seul ne date aucun groupe


@pytest.mark.cas_limite
def test_seconde_demande_pendant_une_execution(depot, lent):
    """Cas : double clic sur « Lancer », ou deux visiteurs qui lancent en même temps.

    Risque : deux sous-processus pytest sur un petit serveur, résultats mêlés."""
    premiere = _lancer(f"test:{CLE_LENT}", "integration")

    with pytest.raises(ex.ErreurTestsEnCours) as meme:
        _lancer(f"test:{CLE_LENT}", "integration")
    with pytest.raises(ex.ErreurTestsEnCours) as autre:
        _lancer("famille")

    assert meme.value.meme_cible and not autre.value.meme_cible
    assert ex.execution_courante() is premiere
    ex.demander_arret()
    attendre_fin(premiere)


@pytest.mark.cas_limite
def test_arret_demande_tue_le_processus_et_garde_les_resultats(depot, lent):
    """Cas : « Arrêter l'exécution » pendant un test qui dure.

    Risque : processus pytest orphelin qui continue de charger le serveur, état « en cours » bloqué."""
    execution = _lancer("composant:lent", "integration")
    _attendre_le_test(execution, CLE_LENT)
    depart = time.monotonic()

    ex.demander_arret()
    suivi = attendre_fin(execution)

    assert time.monotonic() - depart < 5
    assert suivi.issue == res.ISSUE_ARRETEE and "arrêtée à la demande" in suivi.message
    assert CLE_LENT not in suivi.resultats  # interrompu : aucun résultat inventé
    assert "composant:integration/lent" not in res.lire_historique().groupes


@pytest.mark.cas_limite
def test_duree_maximale_depassee(depot, lent, monkeypatch):
    """Cas : un test bloqué (attente sans fin) pendant une exécution.

    Risque : le serveur reste chargé indéfiniment et plus aucun test ne peut être lancé."""
    monkeypatch.setenv(ex.VARIABLE_DUREE_MAX, "60")
    execution = _lancer(f"test:{CLE_LENT}", "integration")
    _attendre_le_test(execution, CLE_LENT)
    # Échéance ramenée à maintenant : le test bloqué est en cours, quelle que soit la charge.
    execution.duree_max_s = 1
    depart = time.monotonic()

    suivi = attendre_fin(execution)

    assert time.monotonic() - depart < 5

    assert suivi.issue == res.ISSUE_TEMPS_DEPASSE and ex.VARIABLE_DUREE_MAX in suivi.message
    assert suivi.resultats[CLE_LENT].etat == res.ECHEC
    assert "durée maximale" in suivi.resultats[CLE_LENT].detail
    assert res.lire_historique().derniere.issue == res.ISSUE_TEMPS_DEPASSE


@pytest.mark.cas_limite
def test_fichier_qui_ne_se_charge_pas(depot):
    """Cas : un fichier de tests importe un module absent (dépendance manquante sur le serveur).

    Risque : ses tests restent « jamais lancés » sans explication, ou toute l'exécution échoue."""
    suivi = attendre_fin(_lancer("famille", "integration"))

    assert suivi.resultats[CLE_CASSE].etat == res.ECHEC
    assert "n'a pas pu être chargé" in suivi.resultats[CLE_CASSE].detail
    assert "module_qui_n_existe_pas" in suivi.resultats[CLE_CASSE].detail
    assert suivi.resultats[CLE_ENVIRONNEMENT].etat == res.REUSSI  # les autres fichiers tournent
