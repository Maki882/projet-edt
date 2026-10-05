"""Onglet « Emploi du temps » : affichages, groupes liés, corrections vérifiées, séances datées."""

from __future__ import annotations

import datetime as dt
import sys

import pytest
from sqlalchemy import delete, func, select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import JOURS, Gravite, OrigineOccurrence, StatutOccurrence
from edt.domaine.instantane import id_seance
from edt.services.emploi_du_temps import modifications, presentation, vues
from edt.services.emploi_du_temps.vues import Affichage, Cible, Couche, NatureCible
from edt.verification.contrat import Anomalie

from .conftest import date_avec_seances, identifiant_groupe, jour_avec_seance, seance_du_besoin

HX = {"HX-Request": "true"}


def _cible(session, nature: str) -> tuple[str, object]:
    """Paramètre d'adresse de la cible et clause SQL de ses séances (sur `Besoin`/`SeanceHebdo`)."""
    if nature == "groupe":
        groupe = identifiant_groupe(session, "A1-G1")
        seances = m.Besoin.id.in_(
            select(m.besoin_groupe.c.besoin_id).where(m.besoin_groupe.c.groupe_id == groupe)
        )
        return f"groupe_id={groupe}", seances
    if nature == "enseignant":
        enseignant = session.scalar(select(m.Enseignant.id).where(m.Enseignant.nom == "Martin"))
        seances = m.Besoin.id.in_(
            select(m.besoin_enseignant.c.besoin_id).where(
                m.besoin_enseignant.c.enseignant_id == enseignant
            )
        )
        return f"enseignant_id={enseignant}", seances
    salle = session.scalar(
        select(m.SeanceHebdo.salle_id)
        .where(m.SeanceHebdo.salle_id.is_not(None))
        .group_by(m.SeanceHebdo.salle_id)
        .order_by(func.count().desc(), m.SeanceHebdo.salle_id)
        .limit(1)
    )
    return f"salle_id={salle}", m.SeanceHebdo.salle_id == salle


def _date_de_la_cible(session, version_id: int, filtre) -> dt.date:
    """Une date où la cible a une séance datée qui a lieu (lue dans la version)."""
    date = session.scalar(
        select(m.Occurrence.date)
        .join(m.SeanceHebdo, m.SeanceHebdo.id == m.Occurrence.seance_hebdo_id)
        .join(m.Besoin, m.Besoin.id == m.Occurrence.besoin_id)
        .where(
            m.Occurrence.version_id == version_id,
            m.Occurrence.statut.in_([StatutOccurrence.PLANIFIEE, StatutOccurrence.DEPLACEE]),
            filtre,
        )
        .order_by(m.Occurrence.date)
        .limit(1)
    )
    assert date is not None
    return date


@pytest.mark.parametrize("nature", ["groupe", "enseignant", "salle"])
@pytest.mark.parametrize(
    ("couche", "affichage"),
    [("hebdo", "semaine"), ("date", "semaine"), ("hebdo", "jour"), ("date", "jour")],
)
def test_trois_affichages_pour_un_groupe_un_enseignant_une_salle(
    seances_datees, client, nature, couche, affichage
):
    version = seances_datees.version_id
    with session_base() as session:
        cible, filtre = _cible(session, nature)
        date = _date_de_la_cible(session, version, filtre)
        jour = jour_avec_seance(session, version, filtre)
    parametres = f"couche={couche}&affichage={affichage}"
    if couche == "date":
        parametres += f"&date={date.isoformat()}"
        attendu = (
            presentation.date_longue(date).capitalize() if affichage == "jour" else "du semestre"
        )
    else:
        parametres += f"&jour={jour}" if affichage == "jour" else ""
        attendu = f"Semaine type : {JOURS[jour]}" if affichage == "jour" else "Semaine type"

    reponse = client.get(f"/emploi-du-temps?version_id={version}&{cible}&{parametres}")

    assert reponse.status_code == 200
    page = reponse.text
    assert attendu in page
    assert 'class="edt-bloc edt-couleur-' in page  # au moins une séance dans la grille
    assert "Traceback" not in page
    assert "pas encore disponible" not in page
    assert f"/export/emploi-du-temps.csv?version_id={version}" in page


def test_le_groupe_voit_aussi_les_seances_des_groupes_qui_partagent_ses_etudiants(version_generee):
    with session_base() as session:
        groupe = identifiant_groupe(session, "A1-G1")
        vue = vues.construire_vue(
            session,
            version_id=version_generee.version_id,
            cible=Cible(NatureCible.GROUPE, groupe),
            etendu=True,
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


def test_seances_datees_produites_par_le_calcul_puis_recalculees(version_generee, client):
    version = version_generee.version_id
    with session_base() as session:
        date = date_avec_seances(session, version)
    adresse = f"/emploi-du-temps?version_id={version}&couche=date&date={date.isoformat()}"

    page = client.get(adresse).text
    assert "pas encore été produites" not in page  # le calcul les a produites
    assert "Recalculer les séances datées" in page
    assert "Vos retouches à la main" in page

    recalcul = client.post(
        f"/emploi-du-temps/versions/{version}/seances-datees", data={"retour": adresse}
    )
    assert "Séances datées recalculées" in recalcul.text


def test_version_sans_seances_datees_puis_produites(version_generee, client):
    version = version_generee.version_id
    with session_base() as session:
        session.execute(delete(m.Occurrence).where(m.Occurrence.version_id == version))
    adresse = f"/emploi-du-temps?version_id={version}&couche=date"
    assert "pas encore été produites" in client.get(adresse).text

    # Instanciation simulée absente : on le dit, rien n'est produit.
    with pytest.MonkeyPatch.context() as temporaire:
        temporaire.setitem(sys.modules, "edt.services.instanciation", None)
        absente = client.post(
            f"/emploi-du-temps/versions/{version}/seances-datees", data={"retour": adresse}
        )
    assert "pas encore disponible" in absente.text

    produite = client.post(
        f"/emploi-du-temps/versions/{version}/seances-datees", data={"retour": adresse}
    )
    assert "Séances datées recalculées" in produite.text
    with session_base() as session:
        assert session.scalar(select(func.count()).select_from(m.Occurrence)) > 0


def test_les_retouches_survivent_au_recalcul_sans_doublon(
    seances_datees, client, faux_verificateur
):
    version = seances_datees.version_id
    with session_base() as session:
        date = date_avec_seances(session, version, minimum=2)
        premiere, seconde = session.scalars(
            select(m.Occurrence)
            .where(
                m.Occurrence.version_id == version,
                m.Occurrence.date == date,
                m.Occurrence.seance_hebdo_id.is_not(None),
                m.Occurrence.debut_min.in_(DEBUTS_DE_LA_GRILLE),
            )
            .order_by(m.Occurrence.debut_min, m.Occurrence.id)
            .limit(2)
        ).all()
        deplacee_id, annulee_id = premiere.id, seconde.id
        cles = {
            identifiant: (occurrence.seance_hebdo_id, date)
            for identifiant, occurrence in ((premiere.id, premiere), (seconde.id, seconde))
        }
        cible = _jour_ouvre_suivant(date, session.get(m.VersionEdt, version).semestre)
        salle, debut = premiere.salle_id, premiere.debut_min
    client.post(
        f"/emploi-du-temps/occurrences/{deplacee_id}/deplacer",
        data={"date": cible.isoformat(), "debut": debut, "salle_id": str(salle)},
        headers=HX,
    )
    client.post(f"/emploi-du-temps/occurrences/{annulee_id}/annuler", data={"motif": "Sortie"})

    with session_base() as session:
        bilan = modifications.recalculer_seances_datees(session, version)
        assert bilan.nb_retouches == 2 and bilan.nb_doublons_retires == 2
        deplacee = session.get(m.Occurrence, deplacee_id)
        annulee = session.get(m.Occurrence, annulee_id)
        assert (deplacee.date, deplacee.statut) == (cible, StatutOccurrence.DEPLACEE)
        assert annulee.statut == StatutOccurrence.ANNULEE
        for seance_hebdo_id, jour in cles.values():
            lundi = jour - dt.timedelta(days=jour.weekday())
            meme_semaine = [
                o
                for o in session.scalars(
                    select(m.Occurrence).where(m.Occurrence.seance_hebdo_id == seance_hebdo_id)
                )
                if lundi <= (o.date_initiale or o.date) <= lundi + dt.timedelta(days=6)
            ]
            assert len(meme_semaine) == 1  # la retouche, et pas de séance recalculée en double
    assert "retouche(s) à la main" in bilan.message


DEBUTS_DE_LA_GRILLE = (8 * 60 + 30, 10 * 60 + 45, 13 * 60 + 45, 16 * 60)
"""Débuts de créneau de la grille par défaut (une séance hors grille ne change pas de jour)."""


def _jour_ouvre_suivant(date: dt.date, semestre) -> dt.date:
    """Le jour ouvré qui suit `date` dans le semestre (sinon celui qui précède)."""
    suivant = date + dt.timedelta(days=3 if date.weekday() == 4 else 1)
    if suivant <= semestre.date_fin:
        return suivant
    return date - dt.timedelta(days=3 if date.weekday() == 0 else 1)


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
    assert "pas encore disponibles" not in fragment  # le vrai vérificateur a tourné


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


def test_sans_verificateur_aucune_seance_n_est_deplacee(version_generee, sans_verificateur):
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
        jour_initial = date_avec_seances(session, seances_datees.version_id)
        occurrence = session.scalar(
            select(m.Occurrence)
            .where(
                m.Occurrence.version_id == seances_datees.version_id,
                m.Occurrence.date == jour_initial,
            )
            .order_by(m.Occurrence.debut_min)
            .limit(1)
        )
        identifiant, salle = occurrence.id, occurrence.salle_id
        mardi = _jour_ouvre_suivant(
            jour_initial, session.get(m.VersionEdt, seances_datees.version_id).semestre
        )

    # Refus : le vérificateur signale une erreur sur cette occurrence ; rien ne change.
    faux_verificateur.datees = [
        Anomalie(
            "GROUPE_UNIQUE", Gravite.ERREUR, "Le groupe a déjà cours.", occurrences=(identifiant,)
        )
    ]
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
        assert session.get(m.Occurrence, identifiant).date == jour_initial

    faux_verificateur.datees = []
    client.post(f"/emploi-du-temps/occurrences/{identifiant}/deplacer", data=donnees, headers=HX)
    with session_base() as session:
        deplacee = session.get(m.Occurrence, identifiant)
        assert (deplacee.date, deplacee.debut_min, deplacee.statut) == (
            mardi,
            645,
            StatutOccurrence.DEPLACEE,
        )
        assert deplacee.date_initiale == jour_initial
        assert deplacee.origine == OrigineOccurrence.MANUELLE  # retouche conservée au recalcul

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
    assert "Affichage de la première classe à la place" in reponse.text


def test_le_vrai_verificateur_refuse_un_conflit_de_groupe(version_generee, client):
    with session_base() as session:
        version = version_generee.version_id
        cm = seance_du_besoin(session, version, "Mathématiques", "CM", "A1")
        td = seance_du_besoin(session, version, "Mathématiques", "TD", "A1-G1")
        n105 = session.scalar(select(m.Salle.id).where(m.Salle.code == "N105"))
        avant = (td.id, td.jour, td.debut_min, td.salle_id)
        cible = (cm.jour, cm.debut_min)

    # La classe 1 en TD pendant le cours magistral de sa promotion : conflit de groupes.
    reponse = client.post(
        f"/emploi-du-temps/seances/{avant[0]}/modifier",
        data={
            "jour": cible[0],
            "debut": cible[1],
            "salle_id": str(n105),
            "retour": "/emploi-du-temps",
        },
        headers=HX,
    )

    assert reponse.status_code == 200
    assert "n&#39;a pas été enregistrée" in reponse.text
    assert "Une séance par groupe" in reponse.text
    with session_base() as session:
        td = session.get(m.SeanceHebdo, avant[0])
        assert (td.id, td.jour, td.debut_min, td.salle_id) == avant


def test_le_panneau_revient_sur_la_page_d_ou_vient_htmx(version_generee, client):
    with session_base() as session:
        seance = seance_du_besoin(session, version_generee.version_id, "Mathématiques", "CM", "A1")
    page = f"/emploi-du-temps?version_id={version_generee.version_id}&couche=hebdo&affichage=jour&jour=0"

    fragment = client.get(
        f"/emploi-du-temps/seances/{seance.id}/panneau",
        headers={**HX, "HX-Current-URL": f"http://testserver{page}"},
    ).text

    attendu = f"{page}&seance=h{seance.id}".replace("&", "&amp;")
    assert f'name="retour" value="{attendu}"' in fragment
    # Une adresse hors de l'onglet n'est jamais reprise (pas de redirection ouverte).
    ailleurs = client.get(
        f"/emploi-du-temps/seances/{seance.id}/panneau",
        headers={**HX, "HX-Current-URL": "http://ailleurs.example/piege"},
    ).text
    assert "ailleurs.example" not in ailleurs


def test_une_classe_s_ouvre_sur_ses_seances_et_celles_de_sa_promotion(version_generee):
    with session_base() as session:
        classe = identifiant_groupe(session, "A1-G1")
        restreinte = vues.construire_vue(
            session,
            version_id=version_generee.version_id,
            cible=Cible(NatureCible.GROUPE, classe),
            verifier=False,
        )
        etendue = vues.construire_vue(
            session,
            version_id=version_generee.version_id,
            cible=Cible(NatureCible.GROUPE, classe),
            verifier=False,
            etendu=True,
        )

    assert {b.groupes for b in restreinte.blocs} <= {("A1",), ("A1-G1",), ("A1-G1", "A1-G2")}
    assert not any(b.partielle for b in restreinte.blocs)
    perimetre = restreinte.perimetre
    assert not perimetre.etendu and not perimetre.etendu_par_defaut
    assert perimetre.ascendants == ("A1",)
    assert perimetre.descendants == ("A1-G1a", "A1-G1b")
    assert "A1-ANG-B2" in perimetre.autres
    assert perimetre.nb_masquees == len(etendue.blocs) - len(restreinte.blocs) > 0


def test_un_demi_groupe_montre_tout_ce_que_suivent_ses_etudiants(version_generee):
    with session_base() as session:
        demi = identifiant_groupe(session, "A1-G1a")
        vue = vues.construire_vue(
            session,
            version_id=version_generee.version_id,
            cible=Cible(NatureCible.GROUPE, demi),
            verifier=False,
        )

    assert vue.perimetre.etendu and vue.perimetre.etendu_par_defaut
    assert {"A1", "A1-G1", "A1-PMR"} <= set(vue.groupes_lies)
    assert any(b.groupes == ("A1",) for b in vue.blocs)


def test_bandeau_et_bascule_vers_la_vue_etendue(version_generee, client):
    with session_base() as session:
        classe = identifiant_groupe(session, "A1-G1")
    adresse = f"/emploi-du-temps?version_id={version_generee.version_id}&groupe_id={classe}"

    restreinte = client.get(adresse).text
    assert "<strong>Affiché :</strong> les séances de A1-G1 et de A1" in restreinte
    assert "<strong>Non affiché :</strong>" in restreinte and "A1-G1a, A1-G1b" in restreinte
    assert "Afficher aussi les classes, demi-groupes et groupes de langues" in restreinte
    assert "etendu=1" in restreinte

    etendue = client.get(adresse + "&etendu=1").text
    assert "tout ce que suivent ses étudiants" in etendue
    assert "N&#39;afficher que les séances de tout le groupe" in etendue or (
        "N'afficher que les séances de tout le groupe" in etendue
    )
    # La navigation garde le choix de l'utilisateur.
    assert "etendu=1&amp;" in etendue or "&amp;etendu=1" in etendue


def test_la_page_s_ouvre_sur_une_classe_puis_sur_la_derniere_cible_consultee(
    version_generee, client
):
    with session_base() as session:
        martin = session.scalar(select(m.Enseignant.id).where(m.Enseignant.nom == "Martin"))
    client.cookies.clear()

    premiere = client.get("/emploi-du-temps")
    assert "Groupe A1-G1 - 1re année, classe 1" in premiere.text  # une classe, pas la promotion
    assert premiere.cookies.get("edt_derniere_cible") is not None

    client.get(f"/emploi-du-temps?enseignant_id={martin}")
    retour = client.get("/emploi-du-temps").text
    assert "Claire Martin" in retour and "dernier emploi du temps consulté" in retour

    # Une cible retenue qui n'existe plus ici : la page s'ouvre sur la classe, sans erreur.
    client.cookies.set("edt_derniere_cible", "groupe:9999")
    perdue = client.get("/emploi-du-temps")
    assert perdue.status_code == 200 and "Groupe A1-G1" in perdue.text
    assert "introuvable" not in perdue.text.lower()
