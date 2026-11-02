"""Cas limites des écrans de l'emploi du temps : adresses erronées, états inattendus, données vides.

Une adresse mal copiée, un lien périmé, un cookie forgé, une base sans grille
ou une version vide ne doivent jamais produire d'erreur 500 : la page dit ce
qui ne va pas (introuvable, saisie invalide) ou retombe sur un affichage par
défaut, en le disant.
"""

from __future__ import annotations

import logging
import threading

import pytest
from sqlalchemy import delete, func, select

from edt import solveur
from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import OrigineVersion
from edt.services import generation
from edt.services.emploi_du_temps import modifications, vues
from edt.services.emploi_du_temps.identifiants import IDENTIFIANT_MAX, identifiant_possible
from edt.solveur.contrat import ResultatSolveur, StatutSolveur

from .conftest import DELAI, autre_place, seance_du_besoin

HX = {"HX-Request": "true"}
ENORME = "99999999999999999999"  # au-delà de 2^63 : aucune ligne SQLite ne peut le porter


def _reperes(version_generee) -> dict[str, int]:
    """Séance, occurrence, calcul et version connus de la version générée."""
    with session_base() as session:
        seance = seance_du_besoin(session, version_generee.version_id, "Mathématiques", "CM", "A1")
        occurrence = session.scalar(
            select(m.Occurrence.id)
            .where(m.Occurrence.version_id == version_generee.version_id)
            .order_by(m.Occurrence.id)
            .limit(1)
        )
        return {
            "seance": seance.id,
            "occurrence": occurrence,
            "calcul": version_generee.calcul_id,
            "version": version_generee.version_id,
        }


def _version_vide(semestre_id: int, *, active: bool = False) -> int:
    """Une version sans aucune séance (import resté vide, référence non remplie...)."""
    with session_base() as session:
        if active:
            session.execute(
                m.VersionEdt.__table__.update()
                .where(m.VersionEdt.semestre_id == semestre_id)
                .values(active=False)
            )
        numero = (
            session.scalar(
                select(func.max(m.VersionEdt.numero)).where(m.VersionEdt.semestre_id == semestre_id)
            )
            or 0
        ) + 1
        version = m.VersionEdt(
            semestre_id=semestre_id,
            numero=numero,
            libelle="Version vide",
            origine=OrigineVersion.REFERENCE,
            active=active,
        )
        session.add(version)
        session.flush()
        return version.id


# --------------------------------------------------------------------------- #
# Identifiants énormes, négatifs ou inconnus
# --------------------------------------------------------------------------- #


def test_bornes_des_identifiants():
    assert identifiant_possible(1) and identifiant_possible(IDENTIFIANT_MAX)
    for impossible in (0, -1, IDENTIFIANT_MAX + 1, int(ENORME), True, "12", None, 1.0):
        assert not identifiant_possible(impossible), impossible


@pytest.mark.parametrize("g", [ENORME, "999999"], ids=["enorme", "inconnu"])
def test_identifiants_enormes_ou_negatifs_donnent_introuvable(version_generee, client, caplog, g):
    r = _reperes(version_generee)
    adresses = [
        ("GET", f"/emploi-du-temps?version_id={g}", None),
        ("GET", "/emploi-du-temps?version_id=-1", None),
        ("GET", f"/calculs/{g}", None),
        ("GET", f"/calculs/{g}/etat", HX),
        ("POST", f"/calculs/{g}/arreter", None),
        ("POST", f"/calculs/{g}/relancer", None),
        ("POST", f"/versions/{g}/activer", None),
        ("POST", f"/versions/{g}/supprimer", None),
        ("POST", f"/emploi-du-temps/versions/{g}/seances-datees", None),
        ("GET", f"/emploi-du-temps/seances/{g}/panneau", HX),
        ("GET", f"/emploi-du-temps/seances/{g}/formulaire", HX),
        ("POST", f"/emploi-du-temps/seances/{g}/modifier", HX),
        ("POST", f"/emploi-du-temps/seances/{g}/verrou", None),
        ("POST", f"/emploi-du-temps/seances/{r['seance']}/modifier?salle", HX),
        ("GET", f"/emploi-du-temps/occurrences/{g}/panneau", HX),
        ("GET", f"/emploi-du-temps/occurrences/{g}/formulaire", HX),
        ("POST", f"/emploi-du-temps/occurrences/{g}/deplacer", HX),
        ("POST", f"/emploi-du-temps/occurrences/{r['occurrence']}/deplacer?salle", HX),
        ("POST", f"/emploi-du-temps/occurrences/{g}/annuler", None),
        ("GET", f"/a-traiter?semestre_id={g}", None),
        ("GET", "/a-traiter?semestre_id=-1", None),
        ("GET", f"/calculs?semestre_id={g}", None),
        ("GET", f"/versions?semestre_id={g}", None),
        ("GET", f"/calculs/verifications?semestre_id={g}", HX),
    ]
    with caplog.at_level(logging.INFO):
        for methode, adresse, entetes in adresses:
            donnees = {"jour": 0, "debut": 510, "date": "2026-10-06", "verrou": "true"}
            if adresse.endswith("?salle"):  # salle choisie énorme : la séance existe
                adresse = adresse.removesuffix("?salle")
                donnees["salle_id"] = g
            if methode == "GET":
                reponse = client.get(adresse, headers=entetes or {}, follow_redirects=False)
            else:
                reponse = client.post(
                    adresse, data=donnees, headers=entetes or {}, follow_redirects=False
                )
            assert reponse.status_code == 404, (adresse, reponse.status_code)
            assert "n&#39;existe pas" in reponse.text or "introuvable" in reponse.text, adresse
            assert "Traceback" not in reponse.text and "Overflow" not in reponse.text
    assert not [e for e in caplog.records if e.levelno >= logging.ERROR]


@pytest.mark.parametrize("parametre", ["enseignant_id", "salle_id", "groupe_id"])
def test_cible_enorme_dans_l_adresse_montre_la_premiere_classe(version_generee, client, parametre):
    page = client.get(
        f"/emploi-du-temps?version_id={version_generee.version_id}&{parametre}={ENORME}"
    )
    assert page.status_code == 200
    assert f"n° {ENORME} n&#39;existe pas" in page.text
    assert "Affichage de la première classe à la place" in page.text


def test_seance_enorme_dans_l_adresse_ouvre_la_page_sans_panneau(version_generee, client):
    for seance in (f"h{ENORME}", f"o{ENORME}", "h0", "x12"):
        page = client.get(
            f"/emploi-du-temps?version_id={version_generee.version_id}&seance={seance}"
        )
        assert page.status_code == 200, seance
        assert "La séance a pu être déplacée ou supprimée" in page.text, seance


def test_cookie_de_cible_illisible_ou_enorme_est_ignore(version_generee, client, caplog):
    with session_base() as session:
        annee = session.get(m.Semestre, version_generee.semestre_id).annee_scolaire_id
        defaut = vues.cible_par_defaut(session, annee).valeur  # première classe de l'année
    for valeur in (
        f"enseignant:{ENORME}",
        "salle:-5",
        "groupe:abc",
        "enseignant:" + "9" * 5000,
        "inconnu:3",
    ):
        client.cookies.clear()  # sinon le cookie posé par la page précédente reste envoyé
        client.cookies.set("edt_derniere_cible", valeur)
        with caplog.at_level(logging.INFO, logger="edt.web.routes.emploi_du_temps"):
            caplog.clear()
            page = client.get("/emploi-du-temps")
        assert page.status_code == 200, valeur[:30]
        assert page.cookies.get("edt_derniere_cible") == defaut, valeur[:30]
        journal = caplog.text
        assert "Dernière cible consultée introuvable" in journal or "illisible" in journal


# --------------------------------------------------------------------------- #
# Fragments, panneaux, séances disparues
# --------------------------------------------------------------------------- #


def test_fragments_ouverts_sans_htmx_menent_a_la_page(version_generee, client):
    r = _reperes(version_generee)
    attendus = {
        f"/calculs/{r['calcul']}/etat": f"/calculs/{r['calcul']}",
        f"/calculs/verifications?semestre_id={version_generee.semestre_id}": (
            f"/calculs?semestre_id={version_generee.semestre_id}"
        ),
        f"/emploi-du-temps/seances/{r['seance']}/formulaire": f"seance=h{r['seance']}",
        f"/emploi-du-temps/seances/{r['seance']}/panneau": f"seance=h{r['seance']}",
        f"/emploi-du-temps/occurrences/{r['occurrence']}/formulaire": (
            f"seance=o{r['occurrence']}"
        ),
    }
    for adresse, cible in attendus.items():
        sans = client.get(adresse, follow_redirects=False)
        assert sans.status_code == 303 and cible in sans.headers["location"], adresse
        avec = client.get(adresse, headers=HX)
        assert avec.status_code == 200 and "<html" not in avec.text, adresse


def test_panneau_d_une_seance_supprimee_entre_temps(version_generee, client, faux_verificateur):
    r = _reperes(version_generee)
    with session_base() as session:
        session.execute(delete(m.SeanceHebdo).where(m.SeanceHebdo.id == r["seance"]))

    panneau = client.get(f"/emploi-du-temps/seances/{r['seance']}/panneau", headers=HX)
    assert panneau.status_code == 404 and "<html" not in panneau.text
    assert "n&#39;existe pas (ou plus)" in panneau.text
    modification = client.post(
        f"/emploi-du-temps/seances/{r['seance']}/modifier",
        data={"jour": 0, "debut": 510},
        headers=HX,
    )
    assert modification.status_code == 404
    page = client.get(f"/emploi-du-temps?version_id={r['version']}&seance=h{r['seance']}")
    assert page.status_code == 200
    assert "La séance a pu être déplacée ou supprimée" in page.text


def test_deplacement_vers_un_creneau_ou_un_jour_inexistant(
    version_generee, client, faux_verificateur
):
    with session_base() as session:
        seance = seance_du_besoin(session, version_generee.version_id, "Mathématiques", "CM", "A1")
        identifiant, jour, avant = seance.id, seance.jour, (seance.jour, seance.debut_min)
    for donnees, attendu in (
        ({"jour": jour, "debut": 7}, "n&#39;est pas un début de créneau"),
        ({"jour": jour, "debut": 100000}, "n&#39;est pas un début de créneau"),
        ({"jour": 9, "debut": 510}, "Jour invalide"),
        ({"jour": "lundi", "debut": 510}, "Saisie invalide"),
    ):
        reponse = client.post(
            f"/emploi-du-temps/seances/{identifiant}/modifier", data=donnees, headers=HX
        )
        assert reponse.status_code == 422, donnees
        assert attendu in reponse.text and "Traceback" not in reponse.text, donnees
    assert faux_verificateur.appels == []  # refusé avant toute vérification
    with session_base() as session:
        seance = session.get(m.SeanceHebdo, identifiant)
        assert (seance.jour, seance.debut_min) == avant


def test_seance_annulee_ni_reannulee_ni_deplacee(seances_datees, client, faux_verificateur):
    with session_base() as session:
        occurrence = session.scalar(
            select(m.Occurrence)
            .where(m.Occurrence.version_id == seances_datees.version_id)
            .order_by(m.Occurrence.id)
            .limit(1)
        )
        identifiant, date, debut = occurrence.id, occurrence.date, occurrence.debut_min
    adresse = f"/emploi-du-temps/occurrences/{identifiant}"
    assert client.post(f"{adresse}/annuler", data={"motif": "Grève"}).status_code == 200

    deux_fois = client.post(f"{adresse}/annuler", data={"motif": "Grève"})  # deux onglets
    assert deux_fois.status_code == 409 and "déjà annulée" in deux_fois.text
    deplacee = client.post(
        f"{adresse}/deplacer", data={"date": date.isoformat(), "debut": debut}, headers=HX
    )
    assert deplacee.status_code == 409 and "annulée ne peut pas être déplacée" in deplacee.text
    with session_base() as session:
        annulee = session.get(m.Occurrence, identifiant)
        assert annulee.statut.value == "annulee"
        assert annulee.commentaire.count("Annulée : Grève") == 1  # motif pas répété


def test_deplacer_une_seance_datee_hors_du_semestre(seances_datees, client, faux_verificateur):
    with session_base() as session:
        occurrence = session.scalar(
            select(m.Occurrence)
            .where(m.Occurrence.version_id == seances_datees.version_id)
            .order_by(m.Occurrence.id)
            .limit(1)
        )
        identifiant, avant = occurrence.id, occurrence.date
    for date in ("1999-01-04", "2100-01-04"):
        reponse = client.post(
            f"/emploi-du-temps/occurrences/{identifiant}/deplacer",
            data={"date": date, "debut": 510},
            headers=HX,
        )
        assert reponse.status_code == 422 and "en dehors du semestre" in reponse.text, date
    with session_base() as session:
        assert session.get(m.Occurrence, identifiant).date == avant


def test_date_tres_ancienne_ou_lointaine_sans_erreur(version_generee, client):
    for date in ("0001-01-01", "9999-12-31"):
        page = client.get(
            f"/emploi-du-temps?version_id={version_generee.version_id}&couche=date&date={date}"
        )
        assert page.status_code == 200, date
        assert "trop loin" in page.text, date


# --------------------------------------------------------------------------- #
# Filtres et paramètres inconnus
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("parametres", "attendu"),
    [
        ("couche=xyz", "couche"),
        ("affichage=mois", "affichage"),
        ("etendu=peutetre", "etendu"),
        ("date=pas-une-date", "date"),
        ("affichage=jour&jour=-1", "Jour invalide"),
        ("affichage=jour&jour=99", "Jour invalide"),
        ("groupe_id=1&salle_id=2", "Choisir un seul groupe"),
        ("cible=bureau:3", "Choix d&#39;affichage invalide"),
    ],
)
def test_filtres_inconnus_refuses_lisiblement(version_generee, client, parametres, attendu):
    reponse = client.get(f"/emploi-du-temps?version_id={version_generee.version_id}&{parametres}")
    assert reponse.status_code == 422
    assert attendu in reponse.text and "Traceback" not in reponse.text


# --------------------------------------------------------------------------- #
# Calculs : relance pendant un calcul
# --------------------------------------------------------------------------- #


def test_relance_pendant_un_calcul_mene_au_calcul_en_cours(version_generee, client, monkeypatch):
    demarre = threading.Event()

    def moteur_lent(instantane, options, arret=None):
        demarre.set()
        arret.wait(DELAI)
        return ResultatSolveur(StatutSolveur.ARRETE)

    monkeypatch.setattr(solveur, "resoudre", moteur_lent)
    en_cours = generation.lancer_calcul(version_generee.semestre_id)
    try:
        assert demarre.wait(DELAI)
        for donnees in ({"longue": "1"}, {"autre_solution": "1"}, {}):
            reponse = client.post(
                f"/calculs/{version_generee.calcul_id}/relancer",
                data=donnees,
                follow_redirects=False,
            )
            assert reponse.status_code == 303, donnees
            assert reponse.headers["location"] == f"/calculs/{en_cours}"
        page = client.get(f"/calculs/{en_cours}").text
        assert "Un calcul est déjà en cours" in page
        with session_base() as session:
            assert session.scalar(select(func.count()).select_from(m.Calcul)) == 2
    finally:
        generation.demander_arret(en_cours)
        generation.attendre_calcul(en_cours, DELAI)


# --------------------------------------------------------------------------- #
# Données vides : version sans séance, semestre sans grille
# --------------------------------------------------------------------------- #


def test_version_sans_seance_s_affiche_et_s_exporte(version_generee, client):
    vide = _version_vide(version_generee.semestre_id)
    for adresse in (
        f"/emploi-du-temps?version_id={vide}",
        f"/emploi-du-temps?version_id={vide}&couche=date",
        f"/emploi-du-temps?version_id={vide}&affichage=jour&jour=0",
        f"/versions?semestre_id={version_generee.semestre_id}",
    ):
        page = client.get(adresse)
        assert page.status_code == 200, adresse
        assert "Traceback" not in page.text
    grille = client.get(f"/emploi-du-temps?version_id={vide}").text
    assert 'class="edt-bloc ' not in grille  # aucun bloc inventé
    # Les exports d'une version vide restent des fichiers valides (en-tête seul).
    csv = client.get(f"/export/emploi-du-temps.csv?version_id={vide}")
    assert csv.status_code == 200 and csv.text.lstrip("﻿").startswith("jour;debut;fin")
    assert len(csv.text.strip().splitlines()) == 1
    ics = client.get(f"/export/emploi-du-temps.ics?version_id={vide}")
    assert (
        ics.status_code == 200 and "BEGIN:VCALENDAR" in ics.text and "BEGIN:VEVENT" not in ics.text
    )


def test_version_active_vide_sur_a_traiter_et_l_accueil(version_generee, client, aujourdhui_fige):
    _version_vide(version_generee.semestre_id, active=True)
    for adresse in ("/a-traiter", f"/a-traiter?semestre_id={version_generee.semestre_id}", "/"):
        page = client.get(adresse)
        assert page.status_code == 200, adresse
        assert "Traceback" not in page.text
    assert "Version vide" in client.get("/").text


def test_semestre_sans_grille_reste_utilisable(version_generee, client, faux_verificateur):
    with session_base() as session:
        seance = seance_du_besoin(session, version_generee.version_id, "Mathématiques", "CM", "A1")
        identifiant, jour, debut = seance.id, seance.jour, seance.debut_min
        session.execute(delete(m.Creneau))
    for adresse in (
        f"/calculs?semestre_id={version_generee.semestre_id}",
        f"/emploi-du-temps?version_id={version_generee.version_id}",
    ):
        assert client.get(adresse).status_code == 200, adresse
    # Sans grille, la séance ne peut que rester où elle est : rien d'autre n'est proposé.
    with session_base() as session:
        formulaire = modifications.formulaire_seance(session, identifiant)
    assert [j for j, _ in formulaire.jours] == [jour]
    assert [d for d, _ in formulaire.debuts] == [debut]
    autre_jour = (jour + 1) % 5
    reponse = client.post(
        f"/emploi-du-temps/seances/{identifiant}/modifier",
        data={"jour": autre_jour, "debut": debut},
        headers=HX,
    )
    assert reponse.status_code == 422 and "ne peut pas être proposée" in reponse.text


# --------------------------------------------------------------------------- #
# Exports (lot échange) : identifiant énorme
# --------------------------------------------------------------------------- #


@pytest.mark.xfail(
    strict=True,
    reason="à corriger par le lot échange : src/edt/web/routes/echange.py (identifiant au-delà "
    "de 2^63 : OverflowError SQLite, erreur 500)",
)
@pytest.mark.parametrize("extension", ["csv", "ics"])
def test_export_d_une_version_enorme_dit_introuvable(version_generee, client, extension):
    from fastapi.testclient import TestClient

    from edt.web.app import creer_app

    sans_trace = TestClient(creer_app(), raise_server_exceptions=False)
    reponse = sans_trace.get(f"/export/emploi-du-temps.{extension}?version_id={ENORME}")
    assert reponse.status_code == 404


def test_reperes_suffisants(version_generee):
    """Garde-fou des tests ci-dessus : la version générée a des séances et des occurrences."""
    r = _reperes(version_generee)
    assert all(r.values()), r
    with session_base() as session:
        seance = session.get(m.SeanceHebdo, r["seance"])
        assert autre_place(session, seance) != (seance.jour, seance.debut_min)
