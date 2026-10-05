"""Onglet « Emploi du temps » : affichages, groupes liés, corrections vérifiées, séances datées."""

from __future__ import annotations

import datetime as dt
import sys
import types

import pytest
from sqlalchemy import func, select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import Gravite, StatutOccurrence
from edt.domaine.instantane import id_seance
from edt.services.emploi_du_temps import modifications, vues
from edt.services.emploi_du_temps.vues import Affichage, Cible, Couche, NatureCible
from edt.verification.contrat import Anomalie

from .conftest import JOUR_TEST, identifiant_groupe, instancier, seance_du_besoin

HX = {"HX-Request": "true"}


def _cibles(session) -> dict[str, str]:
    return {
        "groupe": f"groupe_id={identifiant_groupe(session, 'A1-G1')}",
        "enseignant": "enseignant_id="
        + str(session.scalar(select(m.Enseignant.id).where(m.Enseignant.nom == "Martin"))),
        "salle": "salle_id="
        + str(session.scalar(select(m.Salle.id).where(m.Salle.code == "N016"))),
    }


@pytest.mark.parametrize("nature", ["groupe", "enseignant", "salle"])
@pytest.mark.parametrize(
    ("parametres", "attendu"),
    [
        ("couche=hebdo&affichage=semaine", "Semaine type"),
        (f"couche=date&affichage=semaine&date={JOUR_TEST.isoformat()}", "Semaine 6 du semestre"),
        ("couche=hebdo&affichage=jour&jour=0", "Semaine type : lundi"),
        (f"couche=date&affichage=jour&date={JOUR_TEST.isoformat()}", "Lundi 12 octobre 2026"),
    ],
)
def test_trois_affichages_pour_un_groupe_un_enseignant_une_salle(
    seances_datees, client, nature, parametres, attendu
):
    with session_base() as session:
        cible = _cibles(session)[nature]

    reponse = client.get(
        f"/emploi-du-temps?version_id={seances_datees.version_id}&{cible}&{parametres}"
    )

    assert reponse.status_code == 200
    page = reponse.text
    assert attendu in page
    assert 'class="edt-bloc edt-couleur-' in page  # au moins une séance dans la grille
    assert "Mathématiques" in page
    assert "Traceback" not in page
    assert f"/export/emploi-du-temps.csv?version_id={seances_datees.version_id}" in page


def test_le_groupe_voit_aussi_les_seances_des_groupes_qui_partagent_ses_etudiants(version_generee):
    with session_base() as session:
        groupe = identifiant_groupe(session, "A1-G1")
        vue = vues.construire_vue(
            session,
            version_id=version_generee.version_id,
            cible=Cible(NatureCible.GROUPE, groupe),
        )

    par_groupes = {(b.matiere, b.type_code, b.groupes): b for b in vue.blocs}
    assert not par_groupes[("Mathématiques", "CM", ("A1",))].partielle  # ascendant : tout le groupe
    assert not par_groupes[("Mathématiques", "TD", ("A1-G1",))].partielle
    assert par_groupes[("Algorithmique", "TP", ("A1-G1a",))].partielle  # descendant : une partie
    assert par_groupes[("Anglais", "LANGUE", ("A1-ANG-B2",))].partielle  # chevauchement déclaré
    assert ("Mathématiques", "TD", ("A1-G2",)) not in par_groupes  # classe sœur : pas de conflit
    assert {"A1", "A1-G1a", "A1-ANG-B2"} <= set(vue.groupes_lies)
    assert all(vue.disposition.positions[b.cle].largeur > 0 for b in vue.blocs)
    # Une séance qui n'a lieu que certaines semaines l'indique.
    elec = next(b for b in vue.blocs if b.matiere == "Électronique" and b.type_code == "TP")
    assert elec.semaines_texte.startswith("Semaines ")
    dst = next(b for b in vue.blocs if b.type_code == "DST")
    assert dst.semaines_texte == "Le 19/11/2026 seulement"


def test_semaine_datee_grise_les_vacances_et_navigue(seances_datees, client):
    with session_base() as session:
        groupe = identifiant_groupe(session, "A1-G1")
    page = client.get(
        f"/emploi-du-temps?version_id={seances_datees.version_id}&groupe_id={groupe}"
        "&couche=date&date=2026-10-28"
    ).text

    assert "Vacances de la Toussaint" in page and "edt-colonne-fermee" in page
    assert "date=2026-10-19" in page and "date=2026-11-02" in page  # semaines voisines


def test_seances_datees_absentes_puis_produites(version_generee, client, monkeypatch):
    adresse = (
        f"/emploi-du-temps?version_id={version_generee.version_id}&couche=date&date=2026-10-12"
    )
    assert "pas encore été produites" in client.get(adresse).text

    # Instanciation pas encore livrée : on le dit, rien n'est produit.
    indisponible = client.post(
        f"/emploi-du-temps/versions/{version_generee.version_id}/seances-datees",
        data={"retour": adresse},
    )
    assert "pas encore disponible" in indisponible.text

    module = types.ModuleType("edt.services.instanciation")
    module.instancier_version = instancier
    monkeypatch.setitem(sys.modules, "edt.services.instanciation", module)
    produite = client.post(
        f"/emploi-du-temps/versions/{version_generee.version_id}/seances-datees",
        data={"retour": adresse},
    )
    assert "séance(s) datée(s) produite(s)" in produite.text
    with session_base() as session:
        assert session.scalar(select(func.count()).select_from(m.Occurrence)) > 0


def test_page_avec_seance_ouvre_le_panneau_et_le_fragment_htmx(version_generee, client):
    with session_base() as session:
        seance = seance_du_besoin(session, version_generee.version_id, "Mathématiques", "CM", "A1")
        groupe = identifiant_groupe(session, "A1")
    page = client.get(
        f"/emploi-du-temps?version_id={version_generee.version_id}&groupe_id={groupe}&seance=h{seance.id}"
    ).text
    assert "edt-bloc-selection" in page and "Déplacer la séance" in page
    assert "Groupes : promotions" in page and "Salles : Notre-Dame-des-Champs" in page

    fragment = client.get(f"/emploi-du-temps/seances/{seance.id}/panneau", headers=HX).text
    assert "<html" not in fragment
    assert "Exigences" in fragment and "56 places au moins" in fragment
    assert "pas encore disponibles" in fragment  # anomalies de la séance : vérificateur absent


def test_modification_acceptee_puis_verrou(version_generee, client, faux_verificateur):
    faux_verificateur.modification = [
        Anomalie("HORAIRES", Gravite.INFORMATION, "Séance sur le dernier créneau de la journée.")
    ]
    with session_base() as session:
        seance = seance_du_besoin(session, version_generee.version_id, "Mathématiques", "CM", "A1")
        identifiant, salle = seance.id, seance.salle_id
        attendu = id_seance(seance.besoin_id, seance.rang)

    reponse = client.post(
        f"/emploi-du-temps/seances/{identifiant}/modifier",
        data={"jour": 2, "debut": 16 * 60, "salle_id": str(salle), "retour": "/emploi-du-temps"},
        headers=HX,
    )

    assert reponse.status_code == 204 and reponse.headers["HX-Redirect"] == "/emploi-du-temps"
    assert ("modification", attendu, 2, 960, 1080, salle) in faux_verificateur.appels
    with session_base() as session:
        modifiee = session.get(m.SeanceHebdo, identifiant)
        assert (modifiee.jour, modifiee.debut_min, modifiee.fin_min) == (2, 960, 1080)
    message = client.get("/emploi-du-temps").text
    assert "Séance déplacée au mercredi à 16 h 00" in message and "À noter" in message

    client.post(f"/emploi-du-temps/seances/{identifiant}/verrou", data={"verrou": "true"})
    with session_base() as session:
        assert session.get(m.SeanceHebdo, identifiant).verrou
    page = client.get(
        f"/emploi-du-temps?version_id={version_generee.version_id}&salle_id={salle}"
    ).text
    assert 'aria-label="Séance verrouillée"' in page


def test_modification_refusee_par_une_erreur_du_verificateur(
    version_generee, client, faux_verificateur
):
    faux_verificateur.modification = [
        Anomalie(
            "SALLE_UNIQUE", Gravite.ERREUR, "La salle N016 est déjà occupée le mercredi à 16 h."
        )
    ]
    with session_base() as session:
        seance = seance_du_besoin(session, version_generee.version_id, "Mathématiques", "CM", "A1")
        avant = (seance.id, seance.jour, seance.debut_min, seance.salle_id)

    reponse = client.post(
        f"/emploi-du-temps/seances/{avant[0]}/modifier",
        data={"jour": 2, "debut": 960, "salle_id": str(avant[3]), "retour": "/emploi-du-temps"},
        headers=HX,
    )

    assert reponse.status_code == 200
    assert "n&#39;a pas été enregistrée" in reponse.text
    assert "La salle N016 est déjà occupée" in reponse.text
    with session_base() as session:
        seance = session.get(m.SeanceHebdo, avant[0])
        assert (seance.id, seance.jour, seance.debut_min, seance.salle_id) == avant


def test_sans_verificateur_aucune_seance_n_est_deplacee(version_generee):
    with session_base() as session:
        seance = seance_du_besoin(session, version_generee.version_id, "Mathématiques", "CM", "A1")
        resultat = modifications.modifier_placement(
            session, seance.id, jour=2, debut=960, salle_id=seance.salle_id
        )
        assert not resultat.enregistree
        assert "pas encore disponibles" in resultat.message
        assert session.get(m.SeanceHebdo, seance.id).jour == seance.jour


def test_salles_compatibles_libres_d_abord(version_generee):
    with session_base() as session:
        version = version_generee.version_id
        # Le TP du demi-groupe a d'un étudiant à mobilité réduite : salle informatique accessible.
        tp = seance_du_besoin(session, version, "Algorithmique", "TP", "A1-G1a")
        codes = {
            s.code for s in modifications.formulaire_seance(session, tp.id).salles if not s.actuelle
        }
        assert codes <= {"L012"}  # N310 : accessibilité inconnue, refusée par défaut

        # On occupe N105 le vendredi à 8 h 30, puis on cherche une salle pour le TD de la classe 2.
        td_g1 = seance_du_besoin(session, version, "Mathématiques", "TD", "A1-G1")
        n105 = session.scalar(select(m.Salle).where(m.Salle.code == "N105"))
        td_g1.jour, td_g1.debut_min, td_g1.fin_min, td_g1.salle_id = 4, 510, 630, n105.id
        session.flush()
        td_g2 = seance_du_besoin(session, version, "Mathématiques", "TD", "A1-G2")
        formulaire = modifications.formulaire_seance(session, td_g2.id, jour=4, debut=510)

        libres = [s.libre for s in formulaire.salles]
        assert libres == sorted(libres, reverse=True)  # libres d'abord
        occupee = next(s for s in formulaire.salles if s.code == "N105")
        assert not occupee.libre and "Mathématiques (A1-G1)" in occupee.occupee_par
        assert formulaire.salle_id != n105.id  # une salle libre est proposée par défaut
        session.rollback()


def test_deplacer_puis_annuler_une_seance_datee(seances_datees, client, faux_verificateur):
    with session_base() as session:
        occurrence = session.scalar(
            select(m.Occurrence)
            .where(m.Occurrence.date == JOUR_TEST)
            .order_by(m.Occurrence.debut_min)
            .limit(1)
        )
        identifiant, salle = occurrence.id, occurrence.salle_id

    # Refus : le vérificateur signale une erreur sur cette occurrence ; rien ne change.
    faux_verificateur.datees = [
        Anomalie(
            "GROUPE_UNIQUE", Gravite.ERREUR, "Le groupe a déjà cours.", occurrences=(identifiant,)
        )
    ]
    mardi = JOUR_TEST + dt.timedelta(days=1)
    donnees = {
        "date": mardi.isoformat(),
        "debut": 645,
        "salle_id": str(salle),
        "retour": "/emploi-du-temps",
    }
    refus = client.post(
        f"/emploi-du-temps/occurrences/{identifiant}/deplacer", data=donnees, headers=HX
    )
    assert "Le groupe a déjà cours." in refus.text
    with session_base() as session:
        assert session.get(m.Occurrence, identifiant).date == JOUR_TEST

    faux_verificateur.datees = []
    client.post(f"/emploi-du-temps/occurrences/{identifiant}/deplacer", data=donnees, headers=HX)
    with session_base() as session:
        deplacee = session.get(m.Occurrence, identifiant)
        assert (deplacee.date, deplacee.debut_min, deplacee.statut) == (
            mardi,
            645,
            StatutOccurrence.DEPLACEE,
        )
        assert deplacee.date_initiale == JOUR_TEST

    client.post(f"/emploi-du-temps/occurrences/{identifiant}/annuler", data={"motif": "Grève"})
    with session_base() as session:
        annulee = session.get(m.Occurrence, identifiant)
        assert annulee.statut == StatutOccurrence.ANNULEE and "Grève" in annulee.commentaire
        version = annulee.version_id
    with session_base() as session:
        vue = vues.construire_vue(
            session,
            version_id=version,
            cible=Cible(NatureCible.SALLE, salle),
            couche=Couche.DATE,
            affichage=Affichage.SEMAINE,
            date=mardi,
        )
    assert any(b.occurrence_id == identifiant and b.annulee for b in vue.blocs)


def test_lien_vers_un_groupe_inconnu_affiche_le_groupe_par_defaut(version_generee, client):
    reponse = client.get(f"/emploi-du-temps?version_id={version_generee.version_id}&groupe_id=9999")
    assert reponse.status_code == 200
    assert "Affichage du premier groupe à la place" in reponse.text
