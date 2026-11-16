"""Service de vérification sur le jeu minimal (base temporaire).

La version vérifiée est l'emploi du temps saisi à la main de `versions.py`
(fixture `version_id` de `conftest.py`) : sa semaine type est valide ; ses
séances datées, instanciées naïvement, donnent un jour férié et une salle en
travaux.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select
from tests.outils.verification.versions import LUNDI, VENDREDI

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import Gravite
from edt.domaine.heures import texte_vers_minutes
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable, ErreurRegleMetier
from edt.services.instantane import charger_instantane
from edt.services.verification import (
    anomalies_introduites,
    verifier_modification,
    verifier_occurrences_version,
    verifier_version,
    version_a_verifier,
)
from edt.verification import Anomalie


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


def test_modification_ignore_les_violations_deja_presentes(version_id, jeu_minimal, session):
    # Constat n° 16 de la revue : une violation antérieure et indépendante du déplacement
    # (ici le maximum hebdomadaire de M. Bernard, dépassé dans la version enregistrée) ne
    # doit pas figer ses séances.
    bernard = session.scalar(select(m.Enseignant).where(m.Enseignant.identifiant == "ENS002"))
    bernard.max_minutes_semaine = 4 * 60
    session.commit()
    instantane = charger_instantane(session, jeu_minimal.semestre_id)
    td_g2 = next(b for b in instantane.besoins if b.libelle == "TD Mathématiques - A1-G2")
    n204 = next(s.id for s in instantane.salles if s.code == "N204")
    seance, h = f"{td_g2.id}-1", texte_vers_minutes

    audit = verifier_version(session, version_id)
    assert audit.par_code == {"MAX_ENSEIGNANT": 1}  # toujours signalé par l'audit
    assert seance in audit.anomalies[0].seances

    libre = verifier_modification(
        session, version_id, seance, VENDREDI, h("13h45"), h("15h45"), n204
    )
    assert libre.valide and libre.anomalies == ()

    # Les erreurs nouvelles restent signalées, sans la violation ancienne.
    conflit = verifier_modification(
        session, version_id, seance, LUNDI, h("10h45"), h("12h45"), n204
    )
    assert conflit.par_code == {"ENSEIGNANT_UNIQUE": 1, "GROUPE_UNIQUE": 1}

    # Paramètre facultatif : vérifier seulement les contraintes actives au calcul.
    seule_salle = verifier_modification(
        session,
        version_id,
        seance,
        LUNDI,
        h("10h45"),
        h("12h45"),
        n204,
        contraintes_actives=["SALLE_UNIQUE"],
    )
    assert seule_salle.valide and seule_salle.contraintes == ("SALLE_UNIQUE",)


def test_anomalies_introduites_nouvelles_ou_aggravees():
    def anomalie(code, gravite, message, seances=("1-1",)):
        return Anomalie(CodeContrainte(code), gravite, message, seances=seances)

    deja = anomalie("MAX_ENSEIGNANT", Gravite.ERREUR, "maximum dépassé")
    info = anomalie("HORAIRES", Gravite.INFORMATION, "hors grille, imposée")
    dispo = anomalie("DISPO_SALLE", Gravite.ERREUR, "salle fermée le lundi")
    autre = anomalie("SALLE_UNIQUE", Gravite.ERREUR, "conflit", seances=("2-1", "3-1"))
    avant = [deja, info, dispo, autre]
    apres = [
        deja,  # inchangée : écartée
        anomalie("HORAIRES", Gravite.ERREUR, "hors grille"),  # aggravée : gardée
        anomalie("DISPO_SALLE", Gravite.ERREUR, "salle fermée le vendredi"),  # même nombre
        anomalie("DISPO_SALLE", Gravite.ERREUR, "salle fermée le vendredi, bis"),  # une de plus
        anomalie("PAUSE", Gravite.ERREUR, "pause", seances=("1-1", "4-1")),  # nouvelle
        autre,  # ne cite pas la séance déplacée : écartée
    ]
    gardees = anomalies_introduites(avant, apres, "1-1")
    assert [(str(a.code), a.message) for a in gardees] == [
        ("HORAIRES", "hors grille"),
        ("DISPO_SALLE", "salle fermée le vendredi"),
        ("DISPO_SALLE", "salle fermée le vendredi, bis"),
        ("PAUSE", "pause"),
    ]


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


# --------------------------------------------------------------------------- #
# Cas limites (banque de tests : `pytest -m cas_limite`)
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
def test_version_sans_seance_datee_signalee_explicitement(
    version_sans_seances_datees, session, caplog
):
    """Cas : une version sans aucune séance datée (instanciation absente ou échouée) est vérifiée sur le calendrier.

    Risque : rapport vide dit « valide » ; un écran afficherait « Aucune anomalie » pour un
    calendrier qui n'existe pas.
    """
    rapport = verifier_occurrences_version(session, version_sans_seances_datees)
    assert rapport.nb_examines == 0
    (anomalie,) = rapport.anomalies
    assert (str(anomalie.code), anomalie.gravite) == ("VOLUME", Gravite.AVERTISSEMENT)
    assert anomalie.message == (
        "La version « Version saisie à la main » n'a aucune séance datée : produire ses "
        "séances datées (instanciation sur le calendrier) pour vérifier le calendrier réel."
    )
    assert rapport.par_code == {"VOLUME": 1}
    # Sans VOLUME à vérifier, le signal reste dans le journal, pas dans le rapport.
    with caplog.at_level("WARNING", logger="edt.services.verification"):
        restreint = verifier_occurrences_version(
            session, version_sans_seances_datees, contraintes_actives=["SALLE_UNIQUE"]
        )
    assert restreint.anomalies == ()
    assert "aucune séance datée" in caplog.text


@pytest.mark.cas_limite
def test_seances_datees_apres_la_fin_d_un_semestre_raccourci(version_id, jeu_minimal, session):
    """Cas : le semestre est raccourci de deux semaines après l'instanciation de la version.

    Risque : les séances datées d'après la nouvelle fin passent sans anomalie, et les règles
    personnalisées, bornées aux semaines du semestre, se taisent sur elles.
    """
    semestre = session.get(m.Semestre, jeu_minimal.semestre_id)
    fin = semestre.date_fin - dt.timedelta(weeks=2)
    semestre.date_fin = fin
    session.commit()
    apres = set(
        session.scalars(
            select(m.Occurrence.id).where(
                m.Occurrence.version_id == version_id, m.Occurrence.date > fin
            )
        )
    )
    assert apres
    rapport = verifier_occurrences_version(session, version_id, contraintes_actives=["JOUR_FERME"])
    hors = [a for a in rapport.anomalies if "en dehors du semestre" in a.message]
    assert {o for a in hors for o in a.occurrences} == apres
    assert all(len(a.occurrences) == 1 and a.bloquante for a in hors)


@pytest.mark.cas_limite
def test_salle_supprimee_apres_le_calcul_degrade_en_salle_a_definir(
    version_id, salle_n204_supprimee, session
):
    """Cas : la salle N204 est supprimée après l'enregistrement et l'instanciation de la version.

    Risque : un avertissement « salle à définir » par séance datée (une quinzaine pour une
    seule cause), ou une vérification impossible.
    """
    hebdo = verifier_version(session, version_id)
    assert [(str(a.code), a.gravite) for a in hebdo.anomalies] == [
        ("VOLUME", Gravite.AVERTISSEMENT)
    ]
    assert "TD Mathématiques - A1-G2" in hebdo.anomalies[0].message
    assert "n'a pas encore de salle (salle à définir)" in hebdo.anomalies[0].message
    assert hebdo.valide

    datees = verifier_occurrences_version(session, version_id)
    (a_definir,) = [a for a in datees.anomalies if str(a.code) == "VOLUME"]
    nb_datees = session.scalar(
        select(func.count())
        .select_from(m.Occurrence)
        .where(m.Occurrence.version_id == version_id, m.Occurrence.salle_id.is_(None))
    )
    # Un avertissement pour la séance, qui cite toutes ses séances datées, pas un par date.
    assert nb_datees > 1 and len(a_definir.occurrences) == nb_datees
    assert f"pour {nb_datees} séances datées" in a_definir.message


@pytest.mark.cas_limite
def test_salle_orpheline_nommee_sans_rendre_la_verification_impossible(
    version_id, salle_n204_orpheline, session
):
    """Cas : la salle N204 est effacée sans que ses séances soient mises à vide (base importée, retouche SQL).

    Risque : `ErreurDonneesInvalides` « salle inconnue » : plus aucune vérification de la
    version (À traiter en erreur, commande en code 2).
    """
    hebdo = verifier_version(session, version_id)
    (anomalie,) = hebdo.anomalies
    assert (str(anomalie.code), anomalie.gravite) == ("VOLUME", Gravite.AVERTISSEMENT)
    assert f"une salle qui n'existe plus (n° {salle_n204_orpheline})" in anomalie.message
    assert "TD Mathématiques - A1-G2" in anomalie.message
    assert anomalie.salles == ()
    datees = verifier_occurrences_version(session, version_id)
    (orpheline,) = [a for a in datees.anomalies if str(a.code) == "VOLUME"]
    assert f"une salle qui n'existe plus (n° {salle_n204_orpheline})" in orpheline.message
    # Une modification vers une salle inconnue est refusée : elle ne vaut pas « salle à définir ».
    (seance,) = anomalie.seances
    with pytest.raises(ErreurIntrouvable, match=f"salle n° {salle_n204_orpheline}"):
        verifier_modification(session, version_id, seance, VENDREDI, 825, 945, salle_n204_orpheline)


@pytest.mark.cas_limite
def test_periode_aux_bornes_extremes_du_calendrier(version_id, session):
    """Cas : séances datées vérifiées du 1er janvier de l'an 1 au 31 décembre 9999 (`date.min`, `date.max`).

    Risque : `OverflowError` en étendant la période à la fin de sa semaine (erreur 500, ou
    code 2 avec une trace pour `--au 9999-12-31`).
    """
    total = session.scalar(
        select(func.count()).select_from(m.Occurrence).where(m.Occurrence.version_id == version_id)
    )
    rapport = verifier_occurrences_version(session, version_id, dt.date.min, dt.date.max)
    assert rapport.nb_examines == total
    assert rapport.periode == (dt.date.min, dt.date.max)
    assert rapport.par_code == {"DISPO_SALLE": 2, "JOUR_FERME": 4}


@pytest.mark.cas_limite
def test_liste_de_contraintes_vide_refusee(version_id, session):
    """Cas : un appelant transmet une liste de contraintes vide (aucune case cochée) ou des niveaux vides.

    Risque : rapport « valide » sans rien avoir vérifié : une modification manuelle acceptée
    sans contrôle.
    """
    ligne = session.scalar(
        select(m.SeanceHebdo).where(m.SeanceHebdo.version_id == version_id).limit(1)
    )
    appels = (
        lambda: verifier_version(session, version_id, contraintes_actives=[]),
        lambda: verifier_version(session, version_id, niveaux={}),
        lambda: verifier_occurrences_version(session, version_id, contraintes_actives=[]),
        lambda: verifier_modification(
            session,
            version_id,
            f"{ligne.besoin_id}-{ligne.rang}",
            VENDREDI,
            825,
            945,
            None,
            contraintes_actives=[],
        ),
    )
    for appel in appels:
        with pytest.raises(ErreurDonneesInvalides, match="Aucune contrainte à vérifier"):
            appel()


@pytest.mark.cas_limite
def test_identifiant_hors_bornes_de_la_base_introuvable(jeu_minimal, session):
    """Cas : un identifiant de version ou de semestre hors des bornes de la base (2**70, saisi dans une adresse ou la commande).

    Risque : `OverflowError` de SQLite (erreur 500, trace dans la commande) au lieu de
    « n'existe pas ».
    """
    hors_bornes = 2**70
    appels = (
        lambda: version_a_verifier(session, version_id=hors_bornes),
        lambda: version_a_verifier(session, semestre_id=hors_bornes),
        lambda: verifier_version(session, hors_bornes),
        lambda: verifier_occurrences_version(session, hors_bornes),
        lambda: verifier_modification(session, hors_bornes, "1-1", LUNDI, 510, 630, None),
        lambda: version_a_verifier(session, version_id=0),  # nul ou négatif : de même
    )
    for appel in appels:
        with pytest.raises(ErreurIntrouvable, match="n'existe pas"):
            appel()
