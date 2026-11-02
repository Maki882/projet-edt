"""Banque des cas limites des écrans de l'emploi du temps : Générer, versions, grille, À traiter.

Une adresse mal copiée, un lien périmé, un cookie forgé, un fragment ouvert
sans HTMX, une base sans grille ou une version vide ne doivent jamais produire
d'erreur 500 : la page dit ce qui ne va pas (introuvable, saisie invalide) ou
retombe sur un affichage par défaut, en le disant.

Chaque test porte le marqueur `cas_limite` et sa docstring dit le cas et le
risque (« Cas : … Risque : … »). Cette banque seule :
`python -m pytest -m cas_limite tests/web_edt`.

Aides communes, dans `conftest.py` : `ENORME`, `HX`, `reperes`,
`client_sans_trace`, `creer_version_vide`, `premiere_occurrence`,
`calcul_qui_tourne`. Trois cas sont testés près de leur écran, avec le même
marqueur : l'option « Tous les semestres » (`test_a_traiter.py`), le jour où
une séance ne tient nulle part (`test_emploi_du_temps.py`), l'arrêt d'un
calcul terminé (`test_calculs.py`). Comportements attendus :
`docs/interface-emploi-du-temps.md`, section « Cas limites ».
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import delete, func, select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import StatutOccurrence
from edt.services.emploi_du_temps import modifications, vues
from edt.services.emploi_du_temps.identifiants import IDENTIFIANT_MAX, identifiant_possible

from .conftest import ENORME, HX, calcul_qui_tourne, creer_version_vide

# --------------------------------------------------------------------------- #
# Identifiants énormes, négatifs ou inconnus
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
def test_bornes_des_identifiants():
    """Cas : identifiant nul, négatif, au-delà de 2^63, booléen, texte, absent ou décimal.
    Risque : un identifiant qu'aucune ligne SQLite ne peut porter atteint la base
    (OverflowError, erreur 500), ou `True` passe pour l'identifiant 1.
    """
    assert identifiant_possible(1) and identifiant_possible(IDENTIFIANT_MAX)
    for impossible in (0, -1, IDENTIFIANT_MAX + 1, int(ENORME), True, "12", None, 1.0):
        assert not identifiant_possible(impossible), impossible


@pytest.mark.cas_limite
@pytest.mark.parametrize("g", [ENORME, "999999"], ids=["enorme", "inconnu"])
def test_identifiants_enormes_ou_negatifs_donnent_introuvable(reperes, client, caplog, g):
    """Cas : lien mal copié ou périmé dont l'identifiant a vingt chiffres, est négatif ou
    n'existe pas, sur chaque adresse des onglets Générer, versions, Emploi du temps, À traiter.
    Risque : OverflowError de SQLite non rattrapée, donc erreur 500 et ligne ERROR au journal,
    au lieu d'un « introuvable » (404).
    """
    formulaire = {"jour": 0, "debut": 510, "date": "2026-10-06", "verrou": "true"}
    salle_introuvable = {**formulaire, "salle_id": g}  # la séance existe, pas la salle choisie
    lectures = [
        (f"/emploi-du-temps?version_id={g}", None),
        ("/emploi-du-temps?version_id=-1", None),
        (f"/calculs/{g}", None),
        (f"/calculs/{g}/etat", HX),
        (f"/emploi-du-temps/seances/{g}/panneau", HX),
        (f"/emploi-du-temps/seances/{g}/formulaire", HX),
        (f"/emploi-du-temps/occurrences/{g}/panneau", HX),
        (f"/emploi-du-temps/occurrences/{g}/formulaire", HX),
        (f"/a-traiter?semestre_id={g}", None),
        ("/a-traiter?semestre_id=-1", None),
        (f"/calculs?semestre_id={g}", None),
        (f"/versions?semestre_id={g}", None),
        (f"/calculs/verifications?semestre_id={g}", HX),
    ]
    envois = [
        (f"/calculs/{g}/arreter", None, formulaire),
        (f"/calculs/{g}/relancer", None, formulaire),
        (f"/versions/{g}/activer", None, formulaire),
        (f"/versions/{g}/supprimer", None, formulaire),
        (f"/emploi-du-temps/versions/{g}/seances-datees", None, formulaire),
        (f"/emploi-du-temps/seances/{g}/modifier", HX, formulaire),
        (f"/emploi-du-temps/seances/{g}/verrou", None, formulaire),
        (f"/emploi-du-temps/seances/{reperes.seance}/modifier", HX, salle_introuvable),
        (f"/emploi-du-temps/occurrences/{g}/deplacer", HX, formulaire),
        (f"/emploi-du-temps/occurrences/{reperes.occurrence}/deplacer", HX, salle_introuvable),
        (f"/emploi-du-temps/occurrences/{g}/annuler", None, formulaire),
    ]
    with caplog.at_level(logging.INFO):
        reponses = [
            (adresse, client.get(adresse, headers=entetes or {}, follow_redirects=False))
            for adresse, entetes in lectures
        ] + [
            (
                adresse,
                client.post(adresse, data=corps, headers=entetes or {}, follow_redirects=False),
            )
            for adresse, entetes, corps in envois
        ]
    for adresse, reponse in reponses:
        assert reponse.status_code == 404, (adresse, reponse.status_code)
        assert "n&#39;existe pas" in reponse.text or "introuvable" in reponse.text, adresse
        assert "Traceback" not in reponse.text and "Overflow" not in reponse.text, adresse
    erreurs = [e.getMessage() for e in caplog.records if e.levelno >= logging.ERROR]
    assert not erreurs, erreurs


@pytest.mark.cas_limite
@pytest.mark.parametrize("parametre", ["enseignant_id", "salle_id", "groupe_id"])
def test_cible_enorme_dans_l_adresse_montre_la_premiere_classe(version_generee, client, parametre):
    """Cas : groupe, enseignant ou salle de vingt chiffres dans l'adresse de la grille.
    Risque : erreur 500 au lieu de la première classe, affichée en disant que la cible
    demandée n'existe pas.
    """
    page = client.get(
        f"/emploi-du-temps?version_id={version_generee.version_id}&{parametre}={ENORME}"
    )
    assert page.status_code == 200
    assert f"n° {ENORME} n&#39;existe pas" in page.text
    assert "Affichage de la première classe à la place" in page.text


@pytest.mark.cas_limite
def test_seance_enorme_dans_l_adresse_ouvre_la_page_sans_panneau(version_generee, client):
    """Cas : séance à ouvrir (`seance=h…` ou `o…`) énorme, nulle ou de préfixe inconnu dans
    l'adresse de la grille (lien partagé, favori ancien).
    Risque : erreur 500 au lieu de la page sans panneau, avec un message.
    """
    for seance in (f"h{ENORME}", f"o{ENORME}", "h0", "x12"):
        page = client.get(
            f"/emploi-du-temps?version_id={version_generee.version_id}&seance={seance}"
        )
        assert page.status_code == 200, seance
        assert "La séance a pu être déplacée ou supprimée" in page.text, seance


@pytest.mark.cas_limite
def test_cookie_de_cible_illisible_ou_enorme_est_ignore(version_generee, client, caplog):
    """Cas : cookie de dernière cible forgé ou laissé par une autre instance du même hôte
    (identifiant énorme ou négatif, valeur illisible, nature inconnue).
    Risque : l'onglet Emploi du temps répond 500 à chaque ouverture tant que le cookie
    n'est pas effacé.
    """
    with session_base() as session:
        defaut = vues.cible_par_defaut(session, version_generee.annee_scolaire_id).valeur
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


@pytest.mark.cas_limite
def test_fragments_ouverts_sans_htmx_menent_a_la_page(reperes, client, faux_verificateur):
    """Cas : fragment HTMX (suivi d'un calcul, vérifications préalables, formulaire ou panneau
    d'une séance) ouvert sans en-tête HX-Request : adresse saisie, nouvel onglet, lien.
    Risque : un morceau de page nu, sans menu ni style, au lieu de la page complète.
    """
    attendus = {
        f"/calculs/{reperes.calcul}/etat": f"/calculs/{reperes.calcul}",
        f"/calculs/verifications?semestre_id={reperes.semestre}": (
            f"/calculs?semestre_id={reperes.semestre}"
        ),
        f"/emploi-du-temps/seances/{reperes.seance}/formulaire": f"seance=h{reperes.seance}",
        f"/emploi-du-temps/seances/{reperes.seance}/panneau": f"seance=h{reperes.seance}",
        f"/emploi-du-temps/occurrences/{reperes.occurrence}/formulaire": (
            f"seance=o{reperes.occurrence}"
        ),
        f"/emploi-du-temps/occurrences/{reperes.occurrence}/panneau": (
            f"seance=o{reperes.occurrence}"
        ),
    }
    for adresse, cible in attendus.items():
        sans = client.get(adresse, follow_redirects=False)
        assert sans.status_code == 303 and cible in sans.headers["location"], adresse
        avec = client.get(adresse, headers=HX)
        assert avec.status_code == 200 and "<html" not in avec.text, adresse


@pytest.mark.cas_limite
def test_panneau_d_une_seance_supprimee_entre_temps(reperes, client, faux_verificateur):
    """Cas : panneau, enregistrement ou lien d'une séance supprimée entre-temps (autre onglet,
    version régénérée).
    Risque : erreur 500, ou page entière dans le panneau, au lieu d'un « n'existe pas (ou plus) ».
    """
    with session_base() as session:
        session.execute(delete(m.SeanceHebdo).where(m.SeanceHebdo.id == reperes.seance))
    adresse = f"/emploi-du-temps/seances/{reperes.seance}"

    panneau = client.get(f"{adresse}/panneau", headers=HX)
    assert panneau.status_code == 404 and "<html" not in panneau.text
    assert "n&#39;existe pas (ou plus)" in panneau.text
    modification = client.post(f"{adresse}/modifier", data={"jour": 0, "debut": 510}, headers=HX)
    assert modification.status_code == 404
    page = client.get(f"/emploi-du-temps?version_id={reperes.version}&seance=h{reperes.seance}")
    assert page.status_code == 200
    assert "La séance a pu être déplacée ou supprimée" in page.text


@pytest.mark.cas_limite
def test_deplacement_vers_un_creneau_ou_un_jour_inexistant(reperes, client, faux_verificateur):
    """Cas : séance déplacée vers un début hors grille, un jour hors semaine ou un jour illisible
    (formulaire forgé ou périmé).
    Risque : séance enregistrée à une place impossible, ou erreur 500, au lieu d'un refus
    lisible qui la laisse en place.
    """
    with session_base() as session:
        seance = session.get(m.SeanceHebdo, reperes.seance)
        avant = (seance.jour, seance.debut_min)
    for donnees, attendu in (
        ({"jour": avant[0], "debut": 7}, "n&#39;est pas un début de créneau"),
        ({"jour": avant[0], "debut": 100000}, "n&#39;est pas un début de créneau"),
        ({"jour": 9, "debut": 510}, "Jour invalide"),
        ({"jour": "lundi", "debut": 510}, "Saisie invalide"),
    ):
        reponse = client.post(
            f"/emploi-du-temps/seances/{reperes.seance}/modifier", data=donnees, headers=HX
        )
        assert reponse.status_code == 422, donnees
        assert attendu in reponse.text and "Traceback" not in reponse.text, donnees
    assert faux_verificateur.appels == []  # refusé avant toute vérification
    with session_base() as session:
        seance = session.get(m.SeanceHebdo, reperes.seance)
        assert (seance.jour, seance.debut_min) == avant


@pytest.mark.cas_limite
def test_seance_annulee_ni_reannulee_ni_deplacee(reperes, client, faux_verificateur):
    """Cas : séance datée annulée deux fois (deux onglets ouverts), puis déplacée.
    Risque : motif répété dans le commentaire, ou séance annulée qui revient en étant déplacée.
    """
    with session_base() as session:
        occurrence = session.get(m.Occurrence, reperes.occurrence)
        date, debut = occurrence.date, occurrence.debut_min
    adresse = f"/emploi-du-temps/occurrences/{reperes.occurrence}"
    assert client.post(f"{adresse}/annuler", data={"motif": "Grève"}).status_code == 200

    deux_fois = client.post(f"{adresse}/annuler", data={"motif": "Grève"})
    assert deux_fois.status_code == 409 and "déjà annulée" in deux_fois.text
    deplacee = client.post(
        f"{adresse}/deplacer", data={"date": date.isoformat(), "debut": debut}, headers=HX
    )
    assert deplacee.status_code == 409 and "annulée ne peut pas être déplacée" in deplacee.text
    with session_base() as session:
        annulee = session.get(m.Occurrence, reperes.occurrence)
        assert annulee.statut is StatutOccurrence.ANNULEE
        assert annulee.commentaire.count("Annulée : Grève") == 1  # motif pas répété


@pytest.mark.cas_limite
def test_deplacer_une_seance_datee_hors_du_semestre(reperes, client, faux_verificateur):
    """Cas : séance datée déplacée au 4 janvier 1999 ou au 4 janvier 2100 (date saisie à la main).
    Risque : séance placée hors du semestre, donc absente de la grille et des vérifications.
    """
    with session_base() as session:
        avant = session.get(m.Occurrence, reperes.occurrence).date
    for date in ("1999-01-04", "2100-01-04"):
        reponse = client.post(
            f"/emploi-du-temps/occurrences/{reperes.occurrence}/deplacer",
            data={"date": date, "debut": 510},
            headers=HX,
        )
        assert reponse.status_code == 422 and "en dehors du semestre" in reponse.text, date
    with session_base() as session:
        assert session.get(m.Occurrence, reperes.occurrence).date == avant


@pytest.mark.cas_limite
def test_date_tres_ancienne_ou_lointaine_sans_erreur(version_generee, client):
    """Cas : grille datée demandée au 1er janvier de l'an 1 ou au 31 décembre 9999 (adresse
    modifiée à la main).
    Risque : la semaine autour sort du calendrier de Python (OverflowError, erreur 500) au
    lieu de la semaine en cours du semestre, affichée en le disant.
    """
    for date in ("0001-01-01", "9999-12-31"):
        page = client.get(
            f"/emploi-du-temps?version_id={version_generee.version_id}&couche=date&date={date}"
        )
        assert page.status_code == 200, date
        assert "trop loin" in page.text, date


# --------------------------------------------------------------------------- #
# Filtres et paramètres inconnus
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
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
    """Cas : paramètre d'affichage inconnu ou contradictoire dans l'adresse de la grille
    (couche, affichage, étendu, date, jour, deux cibles, nature de cible).
    Risque : erreur 500, ou affichage faux sans le dire, au lieu d'une page 422 qui nomme
    le paramètre.
    """
    reponse = client.get(f"/emploi-du-temps?version_id={version_generee.version_id}&{parametres}")
    assert reponse.status_code == 422
    assert attendu in reponse.text and "Traceback" not in reponse.text


# --------------------------------------------------------------------------- #
# Calculs : relance pendant un calcul
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
def test_relance_pendant_un_calcul_mene_au_calcul_en_cours(version_generee, client, monkeypatch):
    """Cas : « Relancer » (plus long, autre solution, simple) depuis le suivi d'un ancien calcul
    alors qu'un calcul du semestre tourne déjà.
    Risque : deux calculs en parallèle sur le même semestre, ou une erreur, au lieu d'être mené
    au calcul en cours.
    """
    with calcul_qui_tourne(version_generee.semestre_id, monkeypatch) as en_cours:
        for donnees in ({"longue": "1"}, {"autre_solution": "1"}, {}):
            reponse = client.post(
                f"/calculs/{version_generee.calcul_id}/relancer",
                data=donnees,
                follow_redirects=False,
            )
            assert reponse.status_code == 303, donnees
            assert reponse.headers["location"] == f"/calculs/{en_cours}", donnees
        assert "Un calcul est déjà en cours" in client.get(f"/calculs/{en_cours}").text
        with session_base() as session:  # le calcul de la version générée et celui en cours
            assert session.scalar(select(func.count()).select_from(m.Calcul)) == 2


# --------------------------------------------------------------------------- #
# Données vides : version sans séance, semestre sans grille
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
def test_version_sans_seance_s_affiche_et_s_exporte(version_generee, client):
    """Cas : version sans aucune séance (import resté vide, référence non remplie) affichée en
    semaine type, datée et par jour, listée dans l'historique, exportée en CSV et en iCal.
    Risque : erreur 500 (minimum ou maximum d'une liste vide), blocs inventés, ou fichier
    d'export illisible par le tableur ou l'agenda.
    """
    vide = creer_version_vide(version_generee.semestre_id)
    for adresse in (
        f"/emploi-du-temps?version_id={vide}",
        f"/emploi-du-temps?version_id={vide}&couche=date",
        f"/emploi-du-temps?version_id={vide}&affichage=jour&jour=0",
        f"/versions?semestre_id={version_generee.semestre_id}",
    ):
        page = client.get(adresse)
        assert page.status_code == 200, adresse
        assert "Traceback" not in page.text, adresse
    grille = client.get(f"/emploi-du-temps?version_id={vide}").text
    assert 'class="edt-bloc ' not in grille  # aucun bloc inventé

    csv = client.get(f"/export/emploi-du-temps.csv?version_id={vide}")
    assert csv.status_code == 200 and csv.text.lstrip("﻿").startswith("jour;debut;fin")
    assert len(csv.text.strip().splitlines()) == 1  # l'en-tête seul
    ics = client.get(f"/export/emploi-du-temps.ics?version_id={vide}")
    assert ics.status_code == 200
    assert "BEGIN:VCALENDAR" in ics.text and "BEGIN:VEVENT" not in ics.text


@pytest.mark.cas_limite
def test_version_active_vide_sur_a_traiter_et_l_accueil(version_generee, client, aujourdhui_fige):
    """Cas : la version active du semestre n'a aucune séance (import resté vide).
    Risque : erreur 500 sur « À traiter » ou l'accueil, ou version vide présentée comme un
    emploi du temps sans défaut.
    """
    creer_version_vide(version_generee.semestre_id, active=True)
    for adresse in ("/a-traiter", f"/a-traiter?semestre_id={version_generee.semestre_id}"):
        page = client.get(adresse)
        assert page.status_code == 200, adresse
        assert "n&#39;est pas placée" in page.text, adresse  # chaque séance demandée manque
        assert "Aucune anomalie" not in page.text, adresse
    accueil = client.get("/")
    assert accueil.status_code == 200
    assert "Version vide" in accueil.text and "Aucun point à traiter" not in accueil.text


@pytest.mark.cas_limite
def test_semestre_sans_grille_reste_utilisable(reperes, client, faux_verificateur):
    """Cas : la grille de créneaux est vidée après la génération (grille en cours de refonte).
    Risque : erreur 500 sur Générer ou sur la grille, ou séance proposée à une place qui
    n'existe plus.
    """
    with session_base() as session:
        seance = session.get(m.SeanceHebdo, reperes.seance)
        jour, debut = seance.jour, seance.debut_min
        session.execute(delete(m.Creneau))
    for adresse in (
        f"/calculs?semestre_id={reperes.semestre}",
        f"/emploi-du-temps?version_id={reperes.version}",
    ):
        assert client.get(adresse).status_code == 200, adresse
    # Sans grille, la séance ne peut que rester où elle est : rien d'autre n'est proposé.
    with session_base() as session:
        formulaire = modifications.formulaire_seance(session, reperes.seance)
    assert [j for j, _ in formulaire.jours] == [jour]
    assert [d for d, _ in formulaire.debuts] == [debut]
    reponse = client.post(
        f"/emploi-du-temps/seances/{reperes.seance}/modifier",
        data={"jour": (jour + 1) % 5, "debut": debut},
        headers=HX,
    )
    assert reponse.status_code == 422 and "ne peut pas être proposée" in reponse.text


# --------------------------------------------------------------------------- #
# Exports (lot échange) : identifiant énorme
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
@pytest.mark.xfail(
    strict=True,
    reason="à corriger par le lot échange : src/edt/web/routes/echange.py (identifiant au-delà "
    "de 2^63 : OverflowError SQLite, erreur 500)",
)
@pytest.mark.parametrize("extension", ["csv", "ics"])
def test_export_d_une_version_enorme_dit_introuvable(jeu_minimal, client_sans_trace, extension):
    """Cas : export CSV ou iCal d'une version de vingt chiffres (lien mal copié).
    Risque : OverflowError de SQLite non rattrapée : erreur 500 au lieu d'un « introuvable » (404).
    """
    reponse = client_sans_trace.get(f"/export/emploi-du-temps.{extension}?version_id={ENORME}")
    assert reponse.status_code == 404
