"""Écrans d'import et d'export : dépôt, simulation, application, téléchargements."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import EtatCalcul
from edt.services.echange import ModeImport

HTMX = {"HX-Request": "true"}


def _compter(modele) -> int:
    with session_base() as session:
        return session.scalar(select(func.count()).select_from(modele))


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
    assert "data:text/csv;charset=utf-8;base64," in page
    assert 'formaction="/import/appliquer"' in page
    assert _compter(m.Salle) == jeu_minimal.nb_salles


def test_simulation_sans_htmx_rend_la_page_entiere(jeu_minimal, client, fichier_exemple):
    reponse = client.post("/import/simuler", **_envoi(fichier_exemple("salles_avec_erreurs.csv")))
    assert reponse.status_code == 200 and "<html" in reponse.text and "Bilan" in reponse.text


def test_application(jeu_minimal, client, fichier_exemple):
    reponse = client.post("/import/appliquer", **_envoi(fichier_exemple("salles_avec_erreurs.csv")))

    assert reponse.status_code == 200
    assert "Import appliqué" in reponse.text and "non importée(s)" in reponse.text
    assert _compter(m.Salle) == jeu_minimal.nb_salles + 2


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
    assert _compter(m.VersionEdt) == 1


def test_tout_remplacer_demande_confirmation(jeu_minimal, client, fichier_exemple):
    fichier = fichier_exemple("salles_avec_erreurs.csv")
    simulation = client.post("/import/simuler", headers=HTMX, **_envoi(fichier, tout_remplacer="1"))
    assert 'name="confirmer_remplacement"' in simulation.text
    assert "6 salles" in simulation.text

    refus = client.post("/import/appliquer", **_envoi(fichier, tout_remplacer="1"))
    assert refus.status_code == 409 and "confirmer" in refus.text
    assert _compter(m.Salle) == jeu_minimal.nb_salles

    accord = client.post(
        "/import/appliquer", **_envoi(fichier, tout_remplacer="1", confirmer_remplacement="1")
    )
    assert accord.status_code == 200
    assert _compter(m.Salle) == 0  # les salles du fichier visent un campus supprimé : rejetées


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
    assert "Version 1" in client.get("/export").text

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
