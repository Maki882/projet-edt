"""Règles de salle : accessibilité, capacité, type, équipement."""

from __future__ import annotations

import datetime as dt

import pytest
from tests.outils.verification.fabrique import (
    ISSY,
    MARDI,
    MARDI_6_OCT,
    MARDI_13_OCT,
    Fabrique,
    codes,
    occurrence,
    placer,
)

from edt.domaine.enumerations import Accessibilite, Gravite
from edt.verification import verifier, verifier_occurrences

REGLES_SALLE = ["ACCESSIBILITE", "CAPACITE", "TYPE_SALLE", "EQUIPEMENT", "VOLUME"]


def seance_en_salle(salle_options=None, *, accessibilite_requise=False, effectif=20, **besoin):
    f = Fabrique()
    salle = f.salle("N204", **(salle_options or {}))
    g1 = f.groupe("A1-G1", effectif=effectif, accessibilite_requise=accessibilite_requise)
    seance = f.cours("Physique TP", [g1], [f.enseignant("Mme Martin")], **besoin)
    return f, seance, salle


def anomalies_salle(f, seance, salle):
    return verifier(f.construire(), [placer(seance, MARDI, "8h30", salle)], REGLES_SALLE)


def test_salle_conforme():
    f, seance, salle = seance_en_salle(
        {"capacite": 20, "type_salle": 3, "equipements": [1, 2]},
        types_salle_acceptes=frozenset({3}),
        equipements_requis=frozenset({1}),
    )
    assert anomalies_salle(f, seance, salle) == []


def test_accessibilite():
    f, seance, salle = seance_en_salle(
        {"accessibilite": Accessibilite.NON}, accessibilite_requise=True
    )
    anomalies = anomalies_salle(f, seance, salle)
    assert codes(anomalies) == ["ACCESSIBILITE"]
    assert anomalies[0].message == (
        "La salle N204 n'est pas accessible, alors que la séance Physique TP (A1-G1) le mardi "
        "de 8 h 30 à 10 h 30 requiert une salle accessible."
    )
    assert anomalies[0].salles == (salle,)


def test_accessibilite_inconnue_selon_le_parametre():
    f, seance, salle = seance_en_salle(
        {"accessibilite": Accessibilite.INCONNU}, accessibilite_requise=True
    )
    anomalies = anomalies_salle(f, seance, salle)
    assert "accessibilité inconnue, comptée comme non accessible" in anomalies[0].message
    f.parametres = f.parametres.avec(accessibilite_inconnue_acceptee=True)
    assert anomalies_salle(f, seance, salle) == []


def test_accessibilite_non_requise():
    f, seance, salle = seance_en_salle({"accessibilite": Accessibilite.NON})
    assert anomalies_salle(f, seance, salle) == []


def test_capacite():
    f, seance, salle = seance_en_salle({"capacite": 25}, effectif=25)
    assert anomalies_salle(f, seance, salle) == []  # égalité permise
    f, seance, salle = seance_en_salle({"capacite": 25}, effectif=40)
    anomalies = anomalies_salle(f, seance, salle)
    assert codes(anomalies) == ["CAPACITE"]
    assert "La salle N204 (25 places) est trop petite" in anomalies[0].message
    assert "qui réunit 40 étudiants" in anomalies[0].message


def test_type_de_salle():
    f, seance, salle = seance_en_salle({"type_salle": 1}, types_salle_acceptes=frozenset({2, 3}))
    anomalies = anomalies_salle(f, seance, salle)
    assert codes(anomalies) == ["TYPE_SALLE"]
    assert anomalies[0].message.endswith(
        ": son type de salle (« Salle de cours ») n'est pas accepté (attendu : « Amphithéâtre » "
        "ou « Salle de TP »)."
    )


def test_salle_et_campus_imposes():
    f = Fabrique()
    n204, l101 = f.salle("N204"), f.salle("L101", campus=ISSY)
    g1 = f.groupe("A1-G1")
    seance = f.cours("Physique TP", [g1], salle_imposee_id=l101, campus_impose_id=ISSY)
    anomalies = anomalies_salle(f, seance, n204)
    assert codes(anomalies) == ["TYPE_SALLE"]
    assert anomalies[0].message.endswith(": la salle imposée est L101 ; le campus imposé est Issy.")
    assert anomalies_salle(f, seance, l101) == []


def test_equipement_manquant():
    f, seance, salle = seance_en_salle(
        {"equipements": [1]}, equipements_requis=frozenset({1, 4, 7})
    )
    anomalies = anomalies_salle(f, seance, salle)
    assert codes(anomalies) == ["EQUIPEMENT"]
    # L'équipement 7 n'a pas de nom dans l'instantané : son numéro est affiché.
    assert anomalies[0].message.endswith(
        "il manque les équipements « Matériel d'électronique » et n° 7."
    )


def test_salle_a_definir_avertissement_sans_regle_de_salle():
    f, seance, _ = seance_en_salle(
        accessibilite_requise=True, equipements_requis=frozenset({9}), effectif=500
    )
    anomalies = verifier(f.construire(), [placer(seance, MARDI, "8h30", None)], REGLES_SALLE)
    assert codes(anomalies) == ["VOLUME"]
    assert anomalies[0].gravite is Gravite.AVERTISSEMENT
    assert "n'a pas encore de salle (salle à définir)" in anomalies[0].message
    assert not anomalies[0].bloquante


def test_regles_de_salle_desactivees():
    f, seance, salle = seance_en_salle({"capacite": 5}, effectif=40)
    assert verifier(f.construire(), [placer(seance, MARDI, "8h30", salle)], ["TYPE_SALLE"]) == []


# --------------------------------------------------------------------------- #
# Cas limites (banque de tests : `pytest -m cas_limite`)
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
def test_salle_inconnue_verifiee_comme_salle_a_definir(caplog):
    """Cas : un placement désigne une salle absente de l'instantané (supprimée depuis le calcul, base importée).

    Risque : `ErreurDonneesInvalides` « salle inconnue » : toute la vérification de la
    version devient impossible.
    """
    f, seance, _ = seance_en_salle(effectif=500)
    placement = placer(seance, MARDI, "8h30", 99)
    anomalies = verifier(f.construire(), [placement], REGLES_SALLE)
    # Une seule anomalie : aucune règle de salle (capacité...) sur une salle disparue.
    assert codes(anomalies) == ["VOLUME"]
    assert anomalies[0].gravite is Gravite.AVERTISSEMENT
    assert anomalies[0].message == (
        "La séance Physique TP (A1-G1) le mardi de 8 h 30 à 10 h 30 est placée dans une salle "
        "qui n'existe plus (n° 99) : salle à définir."
    )
    assert anomalies[0].salles == ()
    # Sans VOLUME, aucune anomalie, mais le journal le dit : jamais en silence.
    with caplog.at_level("WARNING", logger="edt.verification"):
        assert verifier(f.construire(), [placement], ["CAPACITE"]) == []
    assert "salle qui n'existe plus dans l'instantané (n° 99 : 1)" in caplog.text


@pytest.mark.cas_limite
def test_salles_a_definir_regroupees_par_seance_dans_la_couche_datee():
    """Cas : une séance a plusieurs séances datées sans salle, et une dans une salle disparue.

    Risque : un avertissement « salle à définir » par date (une quinzaine par séance) noie le
    panneau des anomalies.
    """
    f, seance, _ = seance_en_salle()
    dates = [MARDI_6_OCT, MARDI_13_OCT, dt.date(2026, 10, 20)]
    sans_salle = [
        occurrence(seance, jour, "8h30", identifiant=i) for i, jour in enumerate(dates, 1)
    ]
    disparue = occurrence(seance, dt.date(2026, 10, 27), "8h30", 99, identifiant=4)
    anomalies = verifier_occurrences(f.construire(), [*sans_salle, disparue], REGLES_SALLE)
    assert [(str(a.code), a.gravite, a.occurrences) for a in anomalies] == [
        ("VOLUME", Gravite.AVERTISSEMENT, (1, 2, 3)),
        ("VOLUME", Gravite.AVERTISSEMENT, (4,)),
    ]
    assert anomalies[0].message == (
        "La séance Physique TP (A1-G1) n'a pas encore de salle pour 3 séances datées, du mardi "
        "6 octobre 2026 au mardi 20 octobre 2026 (salle à définir)."
    )
    assert anomalies[1].message == (
        "La séance Physique TP (A1-G1) le mardi 27 octobre 2026 de 8 h 30 à 10 h 30 est placée "
        "dans une salle qui n'existe plus (n° 99) : salle à définir."
    )
