"""Accueil enrichi : prochaine étape conseillée, version active, indicateurs, éléments à traiter."""

from __future__ import annotations

import threading

from edt import solveur
from edt.domaine.enumerations import Gravite
from edt.services import generation
from edt.solveur.contrat import ResultatSolveur, StatutSolveur
from edt.verification.contrat import Anomalie

from .conftest import DELAI


def test_base_vide_conseille_de_saisir_les_donnees(client):
    page = client.get("/").text

    assert "Prochaine étape" in page and "Saisir les données" in page
    assert "La base est initialisée" in page  # l'état de la base reste affiché


def test_donnees_sans_emploi_du_temps_conseille_de_generer(jeu_minimal, client, aujourdhui_fige):
    page = client.get("/").text

    assert "Vérifier les données et lancer le calcul" in page
    assert f'href="/calculs?semestre_id={jeu_minimal.semestre_id}"' in page
    assert '<span class="compteur-nombre">6</span>' in page  # compteurs de la base conservés


def test_version_active_sans_verificateur(
    version_generee, client, aujourdhui_fige, sans_verificateur
):
    page = client.get("/").text

    assert "Consulter l&#39;emploi du temps" in page or "Consulter l'emploi du temps" in page
    assert f"/emploi-du-temps?version_id={version_generee.version_id}" in page
    assert "Les vérifications automatiques ne sont pas encore disponibles" in page
    assert "conflit" not in page  # pas de « 0 conflit » inventé


def test_indicateurs_et_points_a_traiter(
    version_generee, client, aujourdhui_fige, faux_verificateur
):
    faux_verificateur.hebdo = [
        Anomalie("SALLE_UNIQUE", Gravite.ERREUR, "La salle N016 est prise deux fois.")
    ]

    page = client.get("/").text

    assert "100 %" in page and "37,5 %" in page  # séances placées, utilisation des salles
    assert (
        '<span class="compteur-nombre">1</span>\n    <span class="compteur-libelle">conflit' in page
    )
    assert '<span class="compteur-nombre">8</span>' in page  # changements de campus
    assert "1 point à traiter" in page
    assert f'href="/a-traiter?semestre_id={version_generee.semestre_id}"' in page


def test_calcul_en_cours_conseille_de_le_suivre(jeu_minimal, client, aujourdhui_fige, monkeypatch):
    demarre = threading.Event()

    def moteur_lent(instantane, options, arret=None):
        demarre.set()
        arret.wait(DELAI)
        return ResultatSolveur(StatutSolveur.ARRETE)

    monkeypatch.setattr(solveur, "resoudre", moteur_lent)
    calcul_id = generation.lancer_calcul(jeu_minimal.semestre_id)
    try:
        assert demarre.wait(DELAI)
        page = client.get("/").text
        assert "Suivre le calcul" in page and f'href="/calculs/{calcul_id}"' in page
    finally:
        generation.demander_arret(calcul_id)
        generation.attendre_calcul(calcul_id, DELAI)


def test_indicateurs_du_vrai_verificateur(version_generee, client, aujourdhui_fige):
    page = client.get("/").text

    assert "des séances placées" in page and "utilisation des salles" in page
    assert "changements de campus" in page
    assert "Les vérifications automatiques ne sont pas encore disponibles" not in page


def test_base_au_schema_perime(client):
    from edt.db.moteur import obtenir_moteur

    with obtenir_moteur().begin() as connexion:
        connexion.exec_driver_sql("ALTER TABLE besoin ADD COLUMN ancien_champ VARCHAR(10)")

    page = client.get("/").text
    assert "Le schéma de la base ne correspond plus à l&#39;application" in page
    assert "colonne en trop" in page and "init-db --reset" in page
    assert "pas encore initialisée" not in page

    sante = client.get("/sante")
    assert sante.status_code == 503
    assert any("colonne en trop" in ecart for ecart in sante.json()["base"]["ecarts_schema"])
