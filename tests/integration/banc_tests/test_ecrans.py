"""Écrans « Tests unitaires » et « Tests d'intégration » par HTTP : onglets, schéma, lancement, suivi en direct, refus."""

from __future__ import annotations

import re

import pytest
from tests.outils.banc_tests.depot import CLE_CHARGE, CLE_ECHEC, CLE_LENT, CLE_REUSSI, attendre_fin

from edt.services.banc_tests import execution as ex
from edt.services.banc_tests import resultats as res
from edt.services.banc_tests.tableau import identifiant_html
from edt.web.gabarits import DOSSIER_STATIQUE

HTMX = {"HX-Request": "true"}
DATE_AFFICHEE = re.compile(r"\d{2}/\d{2}/\d{4} à \d{2} h \d{2}")


def _onglets(page: str) -> str:
    return re.search(r'<nav class="onglets".*?</nav>', page, re.S).group(0)


def test_deux_onglets_colores_sur_toutes_les_pages_et_a_traiter_dans_generer(client, depot):
    for adresse in ("/", "/calculs", "/tests/unitaires", "/tests/integration"):
        onglets = _onglets(client.get(adresse).text)
        assert '<li class="onglet onglet-tests-unitaires">' in onglets
        assert '<li class="onglet onglet-tests-integration">' in onglets
        assert ">À traiter" not in onglets  # plus d'onglet à lui
        assert '<a href="/a-traiter">Points à traiter</a>' in onglets  # dans le menu de Générer

    page = client.get("/a-traiter").text
    assert 'class="actif" aria-current="true">Générer' in page  # la page vit dans Générer
    assert '<a href="/a-traiter" class="actif" aria-current="page">Points à traiter</a>' in page
    actif = client.get("/tests/integration").text
    assert (
        'href="/tests/integration" class="actif" aria-current="page">Tests d&#39;intégration'
        in actif
    )


def test_page_avec_schema_resume_et_tests_jamais_lances(client, depot):
    unitaires = client.get("/tests/unitaires")
    integration = client.get("/tests/integration").text

    assert unitaires.status_code == 200
    page = unitaires.text
    assert "Comment sont organisés ces tests" in page
    assert '<svg class="banc-svg"' in page and '<title id="schema-u-titre">' in page
    assert '<desc id="schema-u-desc">' in page  # texte de remplacement du schéma
    assert 'class="banc banc-unitaires"' in page and "/static/css/tests.css" in page
    assert "6 tests" in page and "6 jamais lancés" in page
    assert "Reussi" in page and "Réussit toujours." in page
    assert "Test de charge" in page
    assert '<title id="schema-i-titre">' in integration and "Six parcours" in integration
    assert 'class="banc banc-integration"' in integration
    # Libellés des boutons accordés au nombre de tests (fichiers d'un seul test en intégration).
    assert 'title="Lancer les 6 tests du fichier"' in page
    assert 'title="Lancer les 5 tests du composant"' in page  # le test de charge est exclu
    assert 'title="Lancer le test du fichier"' in integration
    assert 'title="Lancer le test du composant"' in integration
    assert "les 1 test" not in page + integration


def test_lancement_suivi_et_resultats_par_la_route_web(client, depot):
    reponse = client.post(
        "/tests/unitaires/lancer", data={"cible": "famille"}, follow_redirects=False
    )
    assert reponse.status_code == 303
    assert reponse.headers["location"] == "/tests/unitaires#suivi-tests"
    execution = ex.execution_courante()
    attendre_fin(execution)

    fragment = client.get(
        f"/tests/unitaires/suivi?execution={execution.id}&depuis=0", headers=HTMX
    ).text
    assert "hx-trigger" not in fragment  # terminée : l'interrogation s'arrête
    assert "5 tests terminés sur 5" in fragment
    assert f'id="{identifiant_html("t", CLE_REUSSI)}" hx-swap-oob="true"' in fragment
    assert f'id="{identifiant_html("t", CLE_ECHEC)}" hx-swap-oob="true"' in fragment
    assert 'id="banc-resume-famille" hx-swap-oob="true"' in fragment

    page = client.get("/tests/unitaires").text
    assert "Exécution lancée : tous les tests unitaires." in page
    assert "Voir le détail de l'échec" in page and "message de l&#39;assertion" in page
    assert "Ignoré : raison de démonstration" in page
    assert "3 cas, 1 en échec" in page
    assert DATE_AFFICHEE.search(page)
    # La date de chaque test porte son libellé, visible sur ordinateur comme sur téléphone.
    assert re.search(
        r'<div class="banc-test-date"><span class="banc-libelle">Dernière exécution</span> '
        + DATE_AFFICHEE.pattern,
        page,
    )
    feuille = (DOSSIER_STATIQUE / "css" / "tests.css").read_text(encoding="utf-8")
    hors_media = feuille.split("@media", 1)[0]
    regle = re.search(r"\.banc-libelle \{([^}]*)\}", hors_media)
    assert regle is not None and "display: none" not in regle.group(1)
    assert re.search(r"Dernière exécution complète : le " + DATE_AFFICHEE.pattern, page)
    assert "Journal complet de l'exécution" in page
    assert "2 failed" in client.get("/tests/journal").text


def test_suivi_pendant_l_execution_puis_arret(client, depot, lent):
    client.post("/tests/integration/lancer", data={"cible": f"test:{CLE_LENT}"})
    execution = ex.execution_courante()

    fragment = client.get(
        f"/tests/integration/suivi?execution={execution.id}&depuis=0", headers=HTMX
    ).text
    assert 'hx-trigger="every 1s"' in fragment and "En cours" in fragment
    assert "Arrêter l&#39;exécution" in fragment
    page = client.get("/tests/integration").text
    assert "En attente" in page or "En cours" in page

    arret = client.post("/tests/arreter", data={"famille": "integration"}, follow_redirects=False)
    assert arret.status_code == 303
    suivi = attendre_fin(execution)
    assert suivi.issue == res.ISSUE_ARRETEE
    assert "Arrêtée" in client.get("/tests/integration").text


@pytest.mark.cas_limite
def test_double_clic_ne_lance_qu_une_execution_sans_erreur(client, depot, lent):
    """Cas : double clic sur « Lancer » (deux envois du même formulaire).

    Risque : deux exécutions en parallèle, ou une page d'erreur pour un geste banal."""
    client.post("/tests/integration/lancer", data={"cible": f"test:{CLE_LENT}"})
    premiere = ex.execution_courante()

    seconde = client.post("/tests/integration/lancer", data={"cible": f"test:{CLE_LENT}"})

    assert seconde.status_code == 200 and ex.execution_courante() is premiere
    assert "Cette exécution est déjà en cours : voici son avancement." in seconde.text
    assert "message-erreur" not in seconde.text
    ex.demander_arret()
    attendre_fin(premiere)


@pytest.mark.cas_limite
def test_cible_inconnue_refusee_par_la_route_web(client, depot):
    """Cas : formulaire envoyé avec une cible fabriquée (chemin hors du dépôt).

    Risque : un texte du navigateur arrive dans la commande pytest."""
    reponse = client.post("/tests/unitaires/lancer", data={"cible": "fichier:../../secret.py"})

    assert reponse.status_code == 200
    assert "Fichier de tests inconnu" in reponse.text and "message-erreur" in reponse.text
    assert ex.execution_courante() is None


@pytest.mark.cas_limite
def test_lancement_desactive_par_la_route_web(client, depot, monkeypatch):
    """Cas : serveur réglé avec EDT_TESTS_LANCEMENT=non.

    Risque : boutons actifs qui mènent à une erreur, ou résultats masqués."""
    monkeypatch.setenv(ex.VARIABLE_LANCEMENT, "non")

    page = client.get("/tests/unitaires").text
    reponse = client.post("/tests/unitaires/lancer", data={"cible": "famille"})

    assert "désactivé" in page
    assert re.search(r'<button type="submit" form="form-lancer"[^>]*disabled', page)
    assert "désactivé" in reponse.text and "message-attention" in reponse.text
    assert ex.execution_courante() is None


@pytest.mark.cas_limite
def test_pages_lisibles_sans_dossier_de_tests_et_avec_un_historique_illisible(
    client, tmp_path, monkeypatch
):
    """Cas : installation sans dossier « tests », fichier des résultats abîmé.

    Risque : erreur 500 sur les deux onglets pendant la démonstration."""
    monkeypatch.setenv("EDT_TESTS_RACINE", str(tmp_path / "vide"))
    dossier = res.dossier_resultats()
    dossier.mkdir(parents=True)
    (dossier / res.FICHIER_RESULTATS).write_text("{pas du json", encoding="utf-8")

    page = client.get("/tests/unitaires")

    assert page.status_code == 200
    assert "EDT_TESTS_RACINE" in page.text and "illisible" in page.text
    refus = client.post("/tests/unitaires/lancer", data={"cible": "famille"})
    assert refus.status_code == 200 and "EDT_TESTS_RACINE" in refus.text


def test_suivi_d_une_execution_inconnue_et_adresses_hors_htmx(client, depot):
    inconnue = client.get("/tests/unitaires/suivi?execution=disparue&depuis=0", headers=HTMX)
    assert inconnue.status_code == 200 and inconnue.headers["HX-Refresh"] == "true"

    sans_htmx = client.get("/tests/unitaires/suivi?execution=x", follow_redirects=False)
    assert sans_htmx.status_code == 303 and sans_htmx.headers["location"] == "/tests/unitaires"
    assert client.get("/tests", follow_redirects=False).headers["location"] == "/tests/unitaires"
    assert client.get("/tests/fonctionnels").status_code == 404
    assert "Aucune exécution" in client.get("/tests/journal").text


@pytest.mark.cas_limite
def test_relance_depuis_le_site_retardee_et_resultats_de_la_commande_affiches(client, depot):
    """Cas : un visiteur relance aussitôt après la fin d'une exécution ; puis la commande
    `edt tests` (autre processus) enregistre des résultats plus récents.

    Risque : serveur occupé sans répit ; page qui montre l'ancienne exécution du serveur au
    lieu des résultats de la commande, jusqu'au redémarrage."""
    client.post("/tests/unitaires/lancer", data={"cible": f"test:{CLE_REUSSI}"})
    attendre_fin(ex.execution_courante())

    relance = client.post("/tests/unitaires/lancer", data={"cible": f"test:{CLE_ECHEC}"})

    assert "prochain lancement depuis le site est possible à" in relance.text
    assert "message-attention" in relance.text
    assert ex.execution_courante().cible.cle == f"test:{CLE_REUSSI}"  # rien de relancé

    plus_tard = res.maintenant()

    def commande(h: res.Historique) -> None:  # ce qu'écrit `edt tests` dans un autre processus
        h.tests[CLE_REUSSI] = res.ResultatTest(
            res.ECHEC, 0.5, plus_tard, detail="écrit par la commande"
        )
        h.derniere = res.BilanExecution(
            "le composant banc_tests", "unitaires", plus_tard, plus_tard, res.ISSUE_TERMINEE, "Fin."
        )

    res.mettre_a_jour(commande)
    page = client.get("/tests/unitaires").text
    assert "Dernière exécution : le composant banc_tests" in page
    assert "écrit par la commande" in page


@pytest.mark.cas_limite
def test_tests_de_charge_par_la_commande_seulement(client, depot):
    """Cas : un visiteur lance un test de charge depuis la page (envoi fabriqué du formulaire).

    Risque : test de quinze minutes au plus sur le serveur public, enchaînable un par un."""
    page = client.get("/tests/unitaires").text
    ligne = re.search(rf'id="{identifiant_html("t", CLE_CHARGE)}".*?</div>\s*</div>', page, re.S)
    assert "Par la commande" in ligne.group(0) and 'value="test:' not in ligne.group(0)
    assert "python -m edt tests --test" in page

    refus = client.post("/tests/unitaires/lancer", data={"cible": f"test:{CLE_CHARGE}"})

    assert "ne se lancent pas depuis le site" in refus.text and "message-attention" in refus.text
    assert ex.execution_courante() is None
