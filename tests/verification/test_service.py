"""Intégration : service de vérification sur le jeu minimal du socle (base SQLite temporaire).

L'emploi du temps ci-dessous est construit à la main pour le jeu minimal
(`edt.db.jeux`) et enregistré comme version de référence. Il respecte toutes
les contraintes de la semaine type, y compris des cas délicats : deux
séances au même créneau sur des semaines disjointes (CM de physique et
partiel), un changement de campus séparé par le déjeuner, un DST imposé de
trois heures, des salles accessibles pour les groupes liés au groupe PMR.

Instancié naïvement sur le calendrier (chaque séance, chaque semaine active,
sans replacement), il donne en revanche des anomalies datées attendues : le
mercredi 11 novembre est férié et la salle L115 est en travaux du 2 au
6 novembre.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import OrigineVersion
from edt.domaine.heures import texte_vers_minutes
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable, ErreurRegleMetier
from edt.services.instantane import charger_instantane
from edt.services.verification import (
    verifier_modification,
    verifier_occurrences_version,
    verifier_version,
    version_a_verifier,
)

LUNDI, MARDI, MERCREDI, JEUDI, VENDREDI = range(5)

EMPLOI_DU_TEMPS = {
    # (libellé du besoin, rang) : (jour, début, salle)
    ("CM Mathématiques - A1", 1): (LUNDI, "8h30", "N016"),
    ("CM Algorithmique - A1", 1): (LUNDI, "10h45", "N016"),
    ("TD Mathématiques - A1-G1", 1): (LUNDI, "13h45", "N105"),
    ("TD Mathématiques - A1-G2", 1): (LUNDI, "16h00", "N204"),
    # Semaines 1 à 7 et semaine 19 : même créneau, même salle, sans conflit.
    ("CM Physique - A1", 1): (MARDI, "8h30", "N016"),
    ("Partiel d'algorithmique - A1", 1): (MARDI, "8h30", "N016"),
    ("CM Physique - A1", 2): (MARDI, "10h45", "N016"),
    ("TP Algorithmique - A1-G2a", 1): (MARDI, "13h45", "N310"),
    ("TP Algorithmique - A1-G2b", 1): (MARDI, "16h00", "N310"),
    # A1-G1a requiert une salle accessible : L012, à Issy.
    ("TP Algorithmique - A1-G1a", 1): (MERCREDI, "8h30", "L012"),
    ("TP Algorithmique - A1-G1b", 1): (MERCREDI, "10h45", "L012"),
    # A1-G1b quitte Issy à 12 h 45 et a anglais à 13 h 45 à Notre-Dame : 1 h de trajet.
    ("LANGUE Anglais - A1-ANG-B2", 1): (MERCREDI, "13h45", "N105"),
    ("PROJET Projet d'intégration - A1-G1, A1-G2", 1): (MERCREDI, "16h00", "N016"),
    ("CM Électronique - A1", 1): (JEUDI, "8h30", "N016"),
    ("Devoir surveillé de mathématiques - A1", 1): (JEUDI, "13h45", "N016"),
    ("TP Électronique - A1-G1", 1): (VENDREDI, "8h30", "L115"),
    ("TP Électronique - A1-G2", 1): (VENDREDI, "10h45", "L115"),
}


def enregistrer_version(session, semestre_id: int, *, occurrences: bool = False) -> int:
    """Enregistre EMPLOI_DU_TEMPS comme version active ; renvoie son identifiant.

    Avec `occurrences`, instancie aussi chaque séance chaque semaine active
    (sans replacement des jours fermés).
    """
    instantane = charger_instantane(session, semestre_id)
    besoins = {besoin.libelle: besoin for besoin in instantane.besoins}
    salles = {salle.code: salle.id for salle in instantane.salles}
    version = m.VersionEdt(
        semestre_id=semestre_id,
        numero=1,
        libelle="Version saisie à la main",
        origine=OrigineVersion.REFERENCE,
        active=True,
    )
    lignes = []
    for (libelle, rang), (jour, debut, code_salle) in EMPLOI_DU_TEMPS.items():
        besoin = besoins[libelle]
        seance = instantane.seance(f"{besoin.id}-{rang}")
        debut_min = texte_vers_minutes(debut)
        ligne = m.SeanceHebdo(
            besoin_id=besoin.id,
            rang=rang,
            jour=jour,
            debut_min=debut_min,
            fin_min=debut_min + besoin.duree,
            salle_id=salles[code_salle],
            semaines=sorted(seance.semaines),
        )
        version.seances.append(ligne)
        lignes.append((ligne, seance))
    session.add(version)
    session.flush()
    if occurrences:
        for ligne, seance in lignes:
            for numero in sorted(seance.semaines):
                session.add(
                    m.Occurrence(
                        version_id=version.id,
                        seance_hebdo_id=ligne.id,
                        besoin_id=ligne.besoin_id,
                        date=instantane.date_de(numero, ligne.jour),
                        debut_min=ligne.debut_min,
                        fin_min=ligne.fin_min,
                        salle_id=ligne.salle_id,
                    )
                )
    session.commit()
    return version.id


@pytest.fixture
def version_id(jeu_minimal, session) -> int:
    return enregistrer_version(session, jeu_minimal.semestre_id, occurrences=True)


def test_semaine_type_construite_a_la_main_valide(version_id, session):
    rapport = verifier_version(session, version_id)
    assert rapport.anomalies == ()
    assert rapport.valide
    assert (rapport.nb_erreurs, rapport.nb_avertissements, rapport.nb_informations) == (0, 0, 0)
    assert rapport.nb_examines == 17
    assert rapport.version == "Version saisie à la main"
    assert rapport.indicateurs is not None
    assert rapport.indicateurs.pourcentage_seances_placees == 100.0
    assert rapport.vers_dict()["valide"] is True


def test_seances_datees_jour_ferie_et_salle_en_travaux(version_id, session):
    rapport = verifier_occurrences_version(session, version_id)
    assert not rapport.valide
    assert rapport.par_code == {"DISPO_SALLE": 2, "JOUR_FERME": 4}
    assert rapport.indicateurs is None
    feries = [a for a in rapport.anomalies if str(a.code) == "JOUR_FERME"]
    assert {a.date for a in feries} == {dt.date(2026, 11, 11)}
    assert "Armistice" in feries[0].message
    travaux = [a for a in rapport.anomalies if str(a.code) == "DISPO_SALLE"]
    assert {a.date for a in travaux} == {dt.date(2026, 11, 6)}
    assert travaux[0].message.startswith(
        "La salle L115 est indisponible le vendredi 6 novembre 2026 toute la journée (Travaux), "
        "pendant la séance TP Électronique - A1-G1 prévue de 8 h 30 à 10 h 30."
    )


def test_seances_datees_sur_une_periode(version_id, session):
    vendredi = dt.date(2026, 11, 6)
    rapport = verifier_occurrences_version(session, version_id, vendredi, vendredi)
    assert rapport.nb_examines == 2
    assert rapport.par_code == {"DISPO_SALLE": 2}
    assert rapport.periode == (vendredi, vendredi)
    with pytest.raises(ErreurDonneesInvalides):
        verifier_occurrences_version(session, version_id, vendredi, vendredi - dt.timedelta(1))


def test_contraintes_choisies(version_id, session):
    rapport = verifier_occurrences_version(session, version_id, contraintes_actives=["DISPO_SALLE"])
    assert rapport.par_code == {"DISPO_SALLE": 2}
    assert rapport.contraintes == ("DISPO_SALLE",)
    with pytest.raises(ErreurDonneesInvalides):
        verifier_version(session, version_id, ["PAS_UN_CODE"])


def test_modification_envisagee_sans_rien_enregistrer(version_id, jeu_minimal, session):
    instantane = charger_instantane(session, jeu_minimal.semestre_id)
    td_g2 = next(b for b in instantane.besoins if b.libelle == "TD Mathématiques - A1-G2")
    n204 = next(s.id for s in instantane.salles if s.code == "N204")
    avant = session.scalar(select(func.count()).select_from(m.SeanceHebdo))

    # Lundi 10 h 45 : M. Bernard et la promotion A1 y ont déjà le CM d'algorithmique.
    seance = f"{td_g2.id}-1"
    h = texte_vers_minutes
    conflit = verifier_modification(
        session, version_id, seance, LUNDI, h("10h45"), h("12h45"), n204
    )
    assert not conflit.valide
    assert conflit.par_code == {"ENSEIGNANT_UNIQUE": 1, "GROUPE_UNIQUE": 1}
    assert all(seance in a.seances for a in conflit.anomalies)

    # Vendredi 13 h 45 : A1-G2 revient d'Issy pendant le déjeuner, rien ne s'y oppose.
    libre = verifier_modification(
        session, version_id, seance, VENDREDI, h("13h45"), h("15h45"), n204
    )
    assert libre.valide and libre.anomalies == ()

    session.expire_all()
    assert session.scalar(select(func.count()).select_from(m.SeanceHebdo)) == avant
    ligne = session.scalar(select(m.SeanceHebdo).where(m.SeanceHebdo.besoin_id == td_g2.id))
    assert (ligne.jour, ligne.debut_min) == (LUNDI, h("16h00"))

    with pytest.raises(ErreurIntrouvable):
        verifier_modification(session, version_id, "999-1", LUNDI, h("8h30"), h("10h30"), n204)
    with pytest.raises(ErreurDonneesInvalides):
        verifier_modification(session, version_id, seance, 9, h("8h30"), h("10h30"), n204)


def test_version_a_verifier(version_id, jeu_minimal, session):
    assert version_a_verifier(session, version_id=version_id) == version_id
    assert version_a_verifier(session, semestre_id=jeu_minimal.semestre_id) == version_id
    with pytest.raises(ErreurIntrouvable):
        version_a_verifier(session, version_id=999)
    with pytest.raises(ErreurIntrouvable):
        version_a_verifier(session, semestre_id=999)
    with pytest.raises(ErreurRegleMetier):
        version_a_verifier(session, version_id=version_id, semestre_id=jeu_minimal.semestre_id + 1)
    autre = session.scalar(select(m.Semestre).where(m.Semestre.id != jeu_minimal.semestre_id))
    with pytest.raises(ErreurIntrouvable, match="pas de version active"):
        version_a_verifier(session, semestre_id=autre.id)


def test_rapport_serialisable(version_id, session):
    import json

    with session_base() as autre_session:
        rapport = verifier_occurrences_version(autre_session, version_id)
    donnees = json.loads(json.dumps(rapport.vers_dict(), ensure_ascii=False))
    assert donnees["nb_erreurs"] == 6
    assert donnees["anomalies"][0]["gravite"] == "erreur"
    assert donnees["anomalies"][0]["date"] is not None
