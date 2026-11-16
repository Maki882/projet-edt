"""Écrans d'import et d'export : dépôt, simulation, application, téléchargements."""

from __future__ import annotations

import datetime as dt
import html
import os

import pytest
from tests.outils.echange.fabrique import compter

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import EtatCalcul
from edt.services.echange import ModeImport
from edt.services.emploi_du_temps import prechauffage
from edt.services.generation import CLE_PROCESSUS

HTMX = {"HX-Request": "true"}


def _envoi(fichier, **donnees):
    return {
        "files": [("fichiers", (fichier.nom, fichier.contenu, "application/octet-stream"))],
        "data": {"mode": "donnees", **donnees},
    }


def test_menu_et_page_d_import(client):
    accueil = client.get("/").text
    assert 'href="/import"' in accueil and 'href="/export"' in accueil

    page = client.get("/import")
    assert page.status_code == 200
    assert 'name="fichiers"' in page.text and 'hx-post="/import/simuler"' in page.text
    assert "Emploi du temps de référence" in page.text
    # Semestre et semaine type ne s'affichent que pour un emploi du temps existant.
    assert page.text.count('data-visible-si="mode:reference,verrous,a_replacer"') == 2
    assert ".csv, .txt, .tsv, .xlsx, .zip ou .ics" in page.text


def test_simulation_en_htmx_affiche_apercu_bilan_rapport(jeu_minimal, client, fichier_exemple):
    reponse = client.post(
        "/import/simuler", headers=HTMX, **_envoi(fichier_exemple("salles_avec_erreurs.csv"))
    )

    assert reponse.status_code == 200
    page = reponse.text
    assert "<html" not in page  # fragment
    assert "rien n&#39;a été écrit" in page or "rien n'a été écrit" in page
    assert "2 créé(s), 4 rejeté(s)" in page
    assert "Campus « LYON » inconnu" in page
    assert "N401" in page  # aperçu des premières lignes
    assert "data:text/csv" not in page  # le rapport n'est pas encodé dans la page
    assert 'formaction="/import/rapport.csv"' in page
    assert 'formaction="/import/appliquer"' in page
    assert compter(m.Salle) == jeu_minimal.nb_salles


def test_simulation_sans_htmx_rend_la_page_entiere(jeu_minimal, client, fichier_exemple):
    reponse = client.post("/import/simuler", **_envoi(fichier_exemple("salles_avec_erreurs.csv")))
    assert reponse.status_code == 200 and "<html" in reponse.text and "Bilan" in reponse.text


def test_application(jeu_minimal, client, fichier_exemple, monkeypatch):
    prechauffages = []
    monkeypatch.setattr(prechauffage, "lancer", prechauffages.append)
    reponse = client.post("/import/appliquer", **_envoi(fichier_exemple("salles_avec_erreurs.csv")))

    assert reponse.status_code == 200
    assert "Import appliqué" in reponse.text and "non importée(s)" in reponse.text
    assert compter(m.Salle) == jeu_minimal.nb_salles + 2
    assert len(prechauffages) == 1  # écrans suivants préparés après l'enregistrement


def test_import_d_emploi_du_temps_par_le_web(jeu_minimal, client, fichier_exemple):
    envoi = _envoi(
        fichier_exemple("seances_existantes.csv"),
        mode=ModeImport.REFERENCE.value,
        semestre_id=str(jeu_minimal.semestre_id),
    )
    reponse = client.post("/import/appliquer", **envoi)

    assert reponse.status_code == 200
    assert (
        "Version d&#39;emploi du temps créée" in reponse.text
        or "Version d'emploi du temps créée" in reponse.text
    )
    assert "Semaine type de A1" in reponse.text
    assert compter(m.VersionEdt) == 1


def test_tout_remplacer_demande_confirmation(jeu_minimal, client, fichier_exemple):
    fichier = fichier_exemple("salles_avec_erreurs.csv")
    simulation = client.post("/import/simuler", headers=HTMX, **_envoi(fichier, tout_remplacer="1"))
    assert 'name="confirmer_remplacement"' in simulation.text
    assert "6 salles" in simulation.text

    refus = client.post("/import/appliquer", **_envoi(fichier, tout_remplacer="1"))
    assert refus.status_code == 409 and "confirmer" in refus.text
    assert compter(m.Salle) == jeu_minimal.nb_salles

    accord = client.post(
        "/import/appliquer", **_envoi(fichier, tout_remplacer="1", confirmer_remplacement="1")
    )
    assert accord.status_code == 200
    assert compter(m.Salle) == 0  # les salles du fichier visent un campus supprimé : rejetées


@pytest.mark.cas_limite
def test_simulation_d_une_image_sans_bouton_appliquer(base, client):
    """Cas : une image envoyée à l'import, puis simulée.

    Risque : page d'erreur brute ; ou bouton « Appliquer l'import » proposé alors que rien
    ne serait écrit, et compte rendu « 0 ligne(s) non importée(s), 1 erreur(s) ».
    """
    envoi = {
        "files": [("fichiers", ("photo.png", b"\x89PNG\r\n\x1a\n" + bytes(300), "image/png"))],
        "data": {"mode": "donnees"},
    }
    reponse = client.post("/import/simuler", headers=HTMX, **envoi)
    assert reponse.status_code == 200
    texte = html.unescape(reponse.text)
    assert "« photo.png » n'est pas un fichier de données lisible ici" in texte
    assert "Rien à appliquer" in texte and "Appliquer l'import" not in texte
    assert "0 ligne(s)" not in texte and "1 erreur(s)" in texte


def test_envoi_sans_fichier_ou_trop_gros(base, client):
    vide = client.post("/import/simuler", data={"mode": "donnees"})
    assert vide.status_code == 422 and "Aucun fichier" in vide.text

    gros = b"x" * (21 * 1024 * 1024)
    trop = client.post("/import/simuler", files=[("fichiers", ("gros.csv", gros, "text/csv"))])
    assert trop.status_code == 422 and "20 Mo" in trop.text


def test_import_refuse_pendant_un_calcul(jeu_minimal, client, fichier_exemple):
    with session_base() as session:
        session.add(
            m.Calcul(
                semestre_id=jeu_minimal.semestre_id,
                etat=EtatCalcul.EN_COURS,
                demarre_le=dt.datetime.now(),
                # Calcul d'un autre processus encore vivant (ici le parent des tests) :
                # sans cela il serait pris pour abandonné et clos.
                parametres={"temps_limite_s": 300},
                statistiques={CLE_PROCESSUS: os.getppid()},
            )
        )
    reponse = client.post("/import/appliquer", **_envoi(fichier_exemple("salles_avec_erreurs.csv")))
    assert reponse.status_code == 409 and "calcul" in reponse.text


def test_page_et_telechargements_d_export(jeu_minimal, client):
    page = client.get("/export")
    assert page.status_code == 200 and "Aucune version" in page.text

    for chemin, type_ in (
        ("/export/gabarit.xlsx", "spreadsheetml"),
        ("/export/gabarit.zip", "zip"),
        ("/export/donnees.xlsx", "spreadsheetml"),
        ("/export/donnees.zip", "zip"),
    ):
        reponse = client.get(chemin)
        assert reponse.status_code == 200, chemin
        assert type_ in reponse.headers["content-type"]
        assert reponse.headers["content-disposition"].startswith("attachment;")
    assert client.get("/export/gabarit.pdf").status_code == 422


def test_export_d_emploi_du_temps(version_generee, client):
    page = client.get("/export").text
    assert "Version 1" in page
    principal = page[page.index("<main") : page.index("</main>")]
    assert principal.count("bouton-principal") == 1  # « Télécharger en CSV »
    assert page.index("<h2>Emploi du temps</h2>") < page.index("<h2>Gabarit vide</h2>")

    csv = client.get(
        "/export/emploi-du-temps.csv",
        params={"version_id": version_generee, "couche": "hebdo", "groupe_id": "", "salle_id": ""},
    )
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert 'filename="emploi-du-temps-v1-hebdo.csv"' in csv.headers["content-disposition"]

    ics = client.get(
        "/export/emploi-du-temps.ics", params={"version_id": version_generee, "couche": "date"}
    )
    assert ics.status_code == 200 and ics.headers["content-type"].startswith("text/calendar")
    assert b"BEGIN:VCALENDAR" in ics.content and b"TZID:Europe/Paris" in ics.content

    deux = client.get(
        "/export/emploi-du-temps.csv",
        params={"version_id": version_generee, "groupe_id": 1, "salle_id": 1},
    )
    assert deux.status_code == 422
    assert client.get("/export/emploi-du-temps.csv", params={"version_id": 999}).status_code == 404
    assert client.get("/export/emploi-du-temps.csv").status_code == 422


# --------------------------------------------------------------------------- #
# Cas limites des écrans d'échange (banque de tests)
# --------------------------------------------------------------------------- #

VERSIONS_INEXISTANTES = ["-7", "0", "9" * 18, str(2**63), "9" * 20, "9" * 5000]
"""Négatif, nul, sous la borne de SQLite, juste au-delà, vingt chiffres, plus que int() n'en lit."""


@pytest.mark.cas_limite
@pytest.mark.parametrize("format_", ["csv", "ics"])
def test_export_d_une_version_negative_nulle_ou_enorme_dit_introuvable(format_, base, client):
    """Cas : GET /export/emploi-du-temps.csv (ou .ics) avec version_id=-7, 0 ou vingt chiffres.

    Risque : OverflowError de SQLite au-delà de 2**63 (ValueError au-delà de 4 300
    chiffres) : page « erreur inattendue » 500 au lieu du 404 « n'existe pas » des
    écrans Données.
    """
    for version in VERSIONS_INEXISTANTES:
        reponse = client.get(f"/export/emploi-du-temps.{format_}", params={"version_id": version})
        texte = html.unescape(reponse.text)
        assert reponse.status_code == 404, (version[:30], texte[:300])
        assert f"La version d'emploi du temps {version[:20]}" in texte
        assert "n'existe pas" in texte and "inattendue" not in texte


@pytest.mark.cas_limite
@pytest.mark.parametrize("format_", ["csv", "ics"])
def test_ressource_hostile_dans_l_adresse_donne_une_erreur_lisible(
    format_, version_reference, client
):
    """Cas : un filtre groupe, enseignant ou salle énorme ou négatif, ou un identifiant non numérique.

    Risque : OverflowError dans la recherche du filtre : « erreur inattendue » 500
    au lieu de « n'existe pas » (404), ou de « nombre attendu » (422) pour « ² ».
    """
    cas = [
        ({"groupe_id": "9" * 20}, 404, "Le groupe 99999999999999999999 n'existe pas"),
        ({"enseignant_id": "-1"}, 404, "L'enseignant -1 n'existe pas"),
        ({"salle_id": str(2**63)}, 404, f"La salle {2**63} n'existe pas"),
        ({"version_id": "²"}, 422, "nombre attendu"),  # chiffre selon isdigit, pas selon int()
        ({"groupe_id": "A1-G1"}, 422, "nombre attendu"),
    ]
    for parametres, statut, attendu in cas:
        reponse = client.get(
            f"/export/emploi-du-temps.{format_}",
            params={"version_id": version_reference, **parametres},
        )
        texte = html.unescape(reponse.text)
        assert reponse.status_code == statut, (parametres, texte[:300])
        assert attendu in texte and "inattendue" not in texte, parametres


@pytest.mark.cas_limite
def test_semestre_enorme_ou_negatif_a_l_import_donne_une_erreur_lisible(
    jeu_minimal, client, fichier_exemple
):
    """Cas : un import d'emploi du temps envoyé avec semestre_id=99999999999999999999 ou -7.

    Risque : OverflowError dans la recherche du semestre : « erreur inattendue »
    au lieu de dire que la valeur est hors limites ou que le semestre n'existe pas.
    """
    fichier = fichier_exemple("seances_existantes.csv")

    enorme = client.post(
        "/import/simuler", headers=HTMX, **_envoi(fichier, mode="reference", semestre_id="9" * 20)
    )
    negatif = client.post(
        "/import/simuler", headers=HTMX, **_envoi(fichier, mode="reference", semestre_id="-7")
    )

    assert enorme.status_code == 422
    assert "Nombre hors limites" in html.unescape(enorme.text)
    assert negatif.status_code == 404
    assert "Le semestre -7 n'existe pas" in html.unescape(negatif.text)
    assert compter(m.VersionEdt) == 0


@pytest.mark.cas_limite
def test_application_sans_htmx_d_un_fichier_refuse_ne_dit_pas_0_ligne_non_importee(base, client):
    """Cas : un document qui n'est pas un fichier de données (PDF) envoyé à « Appliquer » sans
    JavaScript (page complète) : le fichier entier est refusé.

    Risque : le bandeau du haut annonce « Import appliqué : 0 ligne(s) ou séance(s) non
    importée(s) », qui laisse croire que tout est entré.
    """
    envoi = {
        "files": [("fichiers", ("faux.pdf", b"%PDF-1.4 bidon", "application/pdf"))],
        "data": {"mode": "donnees"},
    }
    reponse = client.post("/import/appliquer", **envoi)
    assert reponse.status_code == 200
    texte = html.unescape(reponse.text)
    assert "0 ligne(s)" not in texte and "Rien n'a été importé" in texte
