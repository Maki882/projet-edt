"""Occupation d'un créneau : plage d'un moment, salles libres, séances d'une ressource.

Données : le jeu minimal et sa version saisie à la main (`version_minimale`).
Jeudi après-midi (13 h 45 - 18 h 00), N016 accueille le devoir surveillé de
mathématiques une seule semaine ; L115 est en travaux du 2 au 6 novembre 2026.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import CibleIndisponibilite
from edt.erreurs import ErreurDonneesInvalides, ErreurRegleMetier
from edt.services.emploi_du_temps import occupation

LUNDI, MARDI, MERCREDI, JEUDI, VENDREDI, SAMEDI = range(6)
MATIN = (8 * 60 + 30, 12 * 60 + 45)
APRES_MIDI = (13 * 60 + 45, 18 * 60)


def _codes(salles) -> list[str]:
    return [salle.code for salle in salles]


def _id(session, modele, **filtres) -> int:
    requete = select(modele.id)
    for nom, valeur in filtres.items():
        requete = requete.where(getattr(modele, nom) == valeur)
    return session.scalar(requete)


# --------------------------------------------------------------------------- #
# Plage d'un moment
# --------------------------------------------------------------------------- #


def test_plage_du_moment_suit_la_grille_et_les_horaires(base, session):
    assert occupation.plage_du_moment(session, JEUDI, "matin") == MATIN
    assert occupation.plage_du_moment(session, JEUDI, "apres_midi") == APRES_MIDI
    assert occupation.plage_du_moment(session, JEUDI, "journee") == (8 * 60 + 30, 18 * 60)


@pytest.mark.cas_limite
def test_plage_d_un_moment_sans_creneau_refusee_clairement(base, session):
    """Cas : « le samedi après-midi », alors que la grille n'a que le samedi matin ; un moment
    inconnu.

    Risque : une plage vide ou inventée, et une réponse « toutes les salles sont libres ».
    """
    with pytest.raises(ErreurDonneesInvalides, match="aucun créneau le samedi l'après-midi"):
        occupation.plage_du_moment(session, SAMEDI, "apres_midi")
    with pytest.raises(ErreurDonneesInvalides, match="Moment inconnu"):
        occupation.plage_du_moment(session, JEUDI, "soir")


# --------------------------------------------------------------------------- #
# Salles libres, semaine type
# --------------------------------------------------------------------------- #


def test_salles_libres_le_jeudi_apres_midi(version_minimale, session):
    r = occupation.salles_libres(
        session,
        semestre_id=version_minimale.semestre_id,
        jour=JEUDI,
        debut=APRES_MIDI[0],
        fin=APRES_MIDI[1],
        capacite_min=30,
    )
    assert (r.semestre_code, r.version_numero, r.date) == ("S1", 1, None)
    assert _codes(r.libres) == ["L115", "N105", "N204"]  # par campus puis par code
    ((salle, semaines),) = r.libres_certaines_semaines
    assert salle.code == "N016" and 0 < len(semaines) < r.nb_semaines
    assert r.occupees == ()
    ((exception,),) = [r.exceptions]
    assert exception.salle.code == "L115" and exception.dates == (dt.date(2026, 11, 5),)
    assert exception.motif == "Travaux"


def test_filtres_capacite_campus_et_type(version_minimale, session):
    commun = {
        "semestre_id": version_minimale.semestre_id,
        "jour": JEUDI,
        "debut": APRES_MIDI[0],
        "fin": APRES_MIDI[1],
    }
    plus_de_40 = occupation.salles_libres(session, capacite_min=41, **commun)
    assert _codes(plus_de_40.libres) == [] and [
        s.code for s, _ in plus_de_40.libres_certaines_semaines
    ] == ["N016"]
    assert _codes(occupation.salles_libres(session, campus="issy", **commun).libres) == [
        "L012",
        "L115",
    ]
    assert _codes(occupation.salles_libres(session, type_salle="Info", **commun).libres) == [
        "L012",
        "N310",
    ]
    with pytest.raises(ErreurDonneesInvalides, match="Campus inconnu.*ISSY, NDC"):
        occupation.salles_libres(session, campus="Lyon", **commun)
    with pytest.raises(ErreurDonneesInvalides, match="Type de salle inconnu"):
        occupation.salles_libres(session, type_salle="PISCINE", **commun)


def test_salle_occupee_toutes_les_semaines_et_indisponibilite_recurrente(version_minimale, session):
    # Le vendredi matin, L115 accueille les deux TP d'électronique (toutes les semaines du besoin).
    session.add(
        m.Indisponibilite(
            cible=CibleIndisponibilite.SALLE,
            salle_id=_id(session, m.Salle, code="N204"),
            jour=JEUDI,
            debut_min=14 * 60,
            fin_min=16 * 60,
            motif="Réunion",
        )
    )
    session.flush()
    r = occupation.salles_libres(
        session,
        semestre_id=version_minimale.semestre_id,
        jour=JEUDI,
        debut=APRES_MIDI[0],
        fin=APRES_MIDI[1],
        capacite_min=30,
    )
    assert _codes(r.libres) == ["L115", "N105"]
    (occupee,) = r.occupees
    assert occupee.salle.code == "N204" and occupee.libelle == "indisponible (Réunion)"
    assert (occupee.debut, occupee.fin) == (14 * 60, 16 * 60)


def test_seance_qui_recoupe_le_creneau_occupe_la_salle(version_minimale, session):
    # Lundi 13 h 45 - 15 h 45 : TD de mathématiques de A1-G1 en N105.
    seance = session.scalar(
        select(m.SeanceHebdo).where(
            m.SeanceHebdo.version_id == version_minimale.version_id,
            m.SeanceHebdo.salle_id == _id(session, m.Salle, code="N105"),
            m.SeanceHebdo.jour == LUNDI,
        )
    )
    commun = {
        "semestre_id": version_minimale.semestre_id,
        "jour": LUNDI,
        "debut": 14 * 60,
        "fin": 15 * 60,
        "campus": "NDC",
    }
    r = occupation.salles_libres(session, **commun)
    assert "N105" not in _codes(r.libres)

    seance.semaines = list(range(1, 25))  # toutes les semaines : occupée sans exception
    session.flush()
    r = occupation.salles_libres(session, **commun)
    (occupee,) = [o for o in r.occupees if o.salle.code == "N105"]
    assert occupee.libelle == "TD Mathématiques, A1-G1"
    assert (occupee.debut, occupee.fin) == (13 * 60 + 45, 15 * 60 + 45)
    assert "N105" not in [s.code for s, _ in r.libres_certaines_semaines]

    seance.semaines = [3]  # une seule semaine : libre les autres
    session.flush()
    r = occupation.salles_libres(session, **commun)
    ((salle, libres),) = [p for p in r.libres_certaines_semaines if p[0].code == "N105"]
    assert 3 not in libres and len(libres) == r.nb_semaines - 1


@pytest.mark.cas_limite
def test_salles_homonymes_citees_chacune_avec_son_campus(version_minimale, session):
    """Cas : deux salles « N105 », une par campus.

    Risque : une seule citée, ou l'occupation de l'une attribuée à l'autre.
    """
    session.add(
        m.Salle(
            code="N105",
            campus_id=_id(session, m.Campus, code="ISSY"),
            capacite=50,
            type_salle_id=_id(session, m.TypeSalle, code="COURS"),
        )
    )
    session.flush()
    r = occupation.salles_libres(
        session,
        semestre_id=version_minimale.semestre_id,
        jour=JEUDI,
        debut=APRES_MIDI[0],
        fin=APRES_MIDI[1],
        capacite_min=40,
    )
    assert [(s.code, s.campus) for s in r.libres] == [
        ("N105", "ISSY"),
        ("N105", "NDC"),
        ("N204", "NDC"),
    ]


@pytest.mark.cas_limite
def test_creneau_invalide_ou_jour_et_date_ensemble_refuses(version_minimale, session):
    """Cas : début après la fin ; ni jour ni date ; un jour et une date à la fois ; une date
    hors du semestre.

    Risque : réponse vide prise pour « aucune salle libre », ou résultat d'un autre semestre.
    """
    s = version_minimale.semestre_id
    with pytest.raises(ErreurDonneesInvalides, match="le début doit précéder la fin"):
        occupation.salles_libres(session, semestre_id=s, jour=JEUDI, debut=900, fin=900)
    with pytest.raises(ErreurDonneesInvalides, match="pas les deux"):
        occupation.salles_libres(session, semestre_id=s, debut=800, fin=900)
    with pytest.raises(ErreurDonneesInvalides, match="pas les deux"):
        occupation.salles_libres(
            session, semestre_id=s, jour=JEUDI, date=dt.date(2026, 10, 8), debut=800, fin=900
        )
    with pytest.raises(ErreurDonneesInvalides, match="hors du semestre S1"):
        occupation.salles_libres(
            session, semestre_id=s, date=dt.date(2027, 6, 3), debut=800, fin=900
        )


def test_semestre_sans_version_active(jeu_minimal, session):
    with pytest.raises(ErreurRegleMetier, match="pas encore d'emploi du temps actif"):
        occupation.salles_libres(
            session, semestre_id=jeu_minimal.semestre_id, jour=JEUDI, debut=800, fin=900
        )


# --------------------------------------------------------------------------- #
# Salles libres à une date
# --------------------------------------------------------------------------- #


def test_a_une_date_l_indisponibilite_datee_occupe_la_salle(version_minimale, session):
    r = occupation.salles_libres(
        session,
        semestre_id=version_minimale.semestre_id,
        date=dt.date(2026, 11, 5),
        debut=APRES_MIDI[0],
        fin=APRES_MIDI[1],
        capacite_min=30,
    )
    assert r.date == dt.date(2026, 11, 5) and r.jour == JEUDI and r.fermeture is None
    assert "L115" not in _codes(r.libres)
    assert [(o.salle.code, o.libelle) for o in r.occupees if o.salle.code == "L115"] == [
        ("L115", "indisponible (Travaux)")
    ]


def test_a_une_date_de_vacances_la_fermeture_est_dite(version_minimale, session):
    r = occupation.salles_libres(
        session,
        semestre_id=version_minimale.semestre_id,
        date=dt.date(2026, 10, 29),
        debut=APRES_MIDI[0],
        fin=APRES_MIDI[1],
    )
    assert r.fermeture == "Vacances de la Toussaint" and r.occupees == ()


def test_a_une_date_les_seances_datees_priment(jeu_minimal):
    from tests.outils.verification.versions import enregistrer_version

    with session_base() as session:
        version_id = enregistrer_version(session, jeu_minimal.semestre_id, occurrences=True)
    with session_base() as session:
        occurrence = session.scalar(
            select(m.Occurrence)
            .where(m.Occurrence.version_id == version_id)
            .order_by(m.Occurrence.date)
        )
        salle = session.get(m.Salle, occurrence.salle_id).code
        r = occupation.salles_libres(
            session,
            semestre_id=jeu_minimal.semestre_id,
            date=occurrence.date,
            debut=occurrence.debut_min,
            fin=occurrence.fin_min,
        )
        assert salle in [o.salle.code for o in r.occupees]
        # Annulée, elle libère la salle.
        session.get(m.Occurrence, occurrence.id).statut = "annulee"
        session.flush()
        r = occupation.salles_libres(
            session,
            semestre_id=jeu_minimal.semestre_id,
            date=occurrence.date,
            debut=occurrence.debut_min,
            fin=occurrence.fin_min,
        )
        assert salle in _codes(r.libres)


# --------------------------------------------------------------------------- #
# Séances d'une ressource sur un créneau
# --------------------------------------------------------------------------- #


def test_seances_d_un_enseignant_sur_un_creneau_recurrent(version_minimale, session):
    martin = _id(session, m.Enseignant, identifiant="ENS001")
    seances = occupation.seances_sur_creneau(
        session,
        semestre_id=version_minimale.semestre_id,
        cible="enseignant",
        ressource_id=martin,
        jour=LUNDI,
        debut=MATIN[0],
        fin=MATIN[1],
    )
    assert seances == ("lundi 8 h 30 - 10 h 30 : CM Mathématiques, A1",)
    assert (
        occupation.seances_sur_creneau(
            session,
            semestre_id=version_minimale.semestre_id,
            cible="enseignant",
            ressource_id=martin,
            jour=SAMEDI,
            debut=MATIN[0],
            fin=MATIN[1],
        )
        == ()
    )


def test_seances_d_un_groupe_comprennent_celles_de_ses_ascendants(version_minimale, session):
    demi_groupe = _id(session, m.Groupe, code="A1-G1a")
    seances = occupation.seances_sur_creneau(
        session,
        semestre_id=version_minimale.semestre_id,
        cible=CibleIndisponibilite.GROUPE,
        ressource_id=demi_groupe,
        jour=LUNDI,
        debut=MATIN[0],
        fin=APRES_MIDI[1],
    )
    assert seances[0] == "lundi 8 h 30 - 10 h 30 : CM Mathématiques, A1"
    assert "lundi 13 h 45 - 15 h 45 : TD Mathématiques, A1-G1" in seances
    assert not any("A1-G2" in texte for texte in seances)


def test_seances_d_une_periode_datee(version_minimale, session):
    salle = _id(session, m.Salle, code="N016")
    seances = occupation.seances_sur_creneau(
        session,
        semestre_id=version_minimale.semestre_id,
        cible="salle",
        ressource_id=salle,
        date_debut=dt.date(2026, 10, 5),
        date_fin=dt.date(2026, 10, 6),
        debut=0,
        fin=24 * 60,
    )
    assert seances[0].startswith("lundi 05/10/2026 8 h 30 - 10 h 30 : CM Mathématiques")
    assert any(texte.startswith("mardi 06/10/2026") for texte in seances)
    assert (
        occupation.seances_sur_creneau(
            session,
            semestre_id=version_minimale.semestre_id,
            cible="salle",
            ressource_id=salle,
            date_debut=dt.date(2027, 8, 1),
            debut=0,
            fin=24 * 60,
        )
        == ()
    )


def test_sans_version_active_aucune_seance(jeu_minimal, session):
    martin = _id(session, m.Enseignant, identifiant="ENS001")
    assert (
        occupation.seances_sur_creneau(
            session,
            semestre_id=jeu_minimal.semestre_id,
            cible="enseignant",
            ressource_id=martin,
            jour=LUNDI,
            debut=MATIN[0],
            fin=MATIN[1],
        )
        == ()
    )
