"""Règles de salle : ACCESSIBILITE, CAPACITE, TYPE_SALLE, EQUIPEMENT, et salle à définir (VOLUME)."""

from __future__ import annotations

from edt.domaine.enumerations import Accessibilite, Gravite
from edt.verification import verifier

from .fabrique import ISSY, MARDI, Fabrique, codes, placer

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
    assert "ne fait pas partie des types acceptés" in anomalies[0].message


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
    assert anomalies[0].message.endswith("il manque les équipements n° 4 et 7.")


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
