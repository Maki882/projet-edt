"""Indisponibilités : DISPO_ENSEIGNANT, DISPO_SALLE, DISPO_GROUPE (semaine type)."""

from __future__ import annotations

import datetime as dt

import pytest
from tests.outils.verification.fabrique import MARDI, MERCREDI, Fabrique, codes, placer

from edt.domaine.enumerations import CibleIndisponibilite
from edt.erreurs import ErreurDonneesInvalides
from edt.verification import verifier

ENSEIGNANT, SALLE, GROUPE = (
    CibleIndisponibilite.ENSEIGNANT,
    CibleIndisponibilite.SALLE,
    CibleIndisponibilite.GROUPE,
)
DISPOS = ["DISPO_ENSEIGNANT", "DISPO_SALLE", "DISPO_GROUPE"]


def base(fictif=False, semaines=None):
    f = Fabrique()
    n204 = f.salle("N204")
    martin = f.enseignant("Mme Martin", fictif=fictif)
    a1 = f.groupe("A1", effectif=40)
    g1 = f.groupe("A1-G1", parent=a1)
    td = f.cours("Mathématiques TD", [g1], [martin], semaines=semaines)
    return f, n204, martin, a1, g1, td


def test_indisponibilite_recurrente_d_un_enseignant():
    f, n204, martin, _, _, td = base()
    f.indisponibilite(ENSEIGNANT, martin, "8h30", "12h45", jour=MARDI, motif="conseil d'école")
    anomalies = verifier(f.construire(), [placer(td, MARDI, "10h45", n204)], DISPOS)
    assert codes(anomalies) == ["DISPO_ENSEIGNANT"]
    assert anomalies[0].message == (
        "Mme Martin est indisponible le mardi de 8 h 30 à 12 h 45 (conseil d'école), pendant la "
        "séance Mathématiques TD (A1-G1) prévue de 10 h 45 à 12 h 45."
    )
    assert anomalies[0].enseignants == (martin,)


def test_indisponibilite_contigue_ou_autre_jour():
    f, n204, martin, _, _, td = base()
    f.indisponibilite(ENSEIGNANT, martin, "8h30", "10h45", jour=MARDI)
    instantane = f.construire()
    assert verifier(instantane, [placer(td, MARDI, "10h45", n204)], DISPOS) == []
    assert verifier(instantane, [placer(td, MERCREDI, "8h30", n204)], DISPOS) == []


def test_enseignant_fictif_et_preference_ignores():
    f, n204, martin, _, _, td = base(fictif=True)
    f.indisponibilite(ENSEIGNANT, martin, "8h30", "18h00", jour=MARDI)
    assert verifier(f.construire(), [placer(td, MARDI, "8h30", n204)], DISPOS) == []

    f, n204, martin, _, _, td = base()
    f.indisponibilite(ENSEIGNANT, martin, "8h30", "18h00", jour=MARDI, dure=False)
    assert verifier(f.construire(), [placer(td, MARDI, "8h30", n204)], DISPOS) == []


def test_indisponibilite_recurrente_quelles_que_soient_les_semaines_actives():
    # Une indisponibilité récurrente vaut toutes les semaines, même pour un module court.
    f, n204, martin, _, _, td = base(semaines=range(5, 9))
    f.indisponibilite(ENSEIGNANT, martin, "8h30", "12h45", jour=MARDI)
    anomalies = verifier(f.construire(), [placer(td, MARDI, "8h30", n204)], DISPOS)
    assert codes(anomalies) == ["DISPO_ENSEIGNANT"]
    assert anomalies[0].message.endswith("prévue de 8 h 30 à 10 h 30.")


def test_indisponibilite_datee_ignoree_sur_la_semaine_type():
    # Datée : elle relève de la couche datée (l'occurrence de ce jour-là est replacée).
    mardi_s3 = dt.date(2026, 9, 22)
    f, n204, martin, _, _, td = base()
    f.indisponibilite(ENSEIGNANT, martin, "8h30", "12h45", date_debut=mardi_s3, date_fin=mardi_s3)
    f.indisponibilite(SALLE, n204, "8h00", "18h00", date_debut=mardi_s3, date_fin=mardi_s3)
    assert verifier(f.construire(), [placer(td, MARDI, "8h30", n204)], DISPOS) == []


def test_indisponibilite_de_salle():
    f, n204, _, _, _, td = base()
    f.indisponibilite(SALLE, n204, "8h00", "18h00", jour=MARDI, motif="travaux")
    anomalies = verifier(f.construire(), [placer(td, MARDI, "13h45", n204)], DISPOS)
    assert codes(anomalies) == ["DISPO_SALLE"]
    assert anomalies[0].message.startswith("La salle N204 est indisponible le mardi")
    assert anomalies[0].salles == (n204,)


def test_indisponibilite_d_un_groupe_lie():
    # L'indisponibilité de la promotion A1 vaut pour son demi-groupe A1-G1, et inversement.
    f, n204, martin, a1, g1, td = base()
    cm = f.cours("Chimie CM", [a1], [martin], type_seance="CM")
    f.indisponibilite(GROUPE, a1, "13h45", "18h00", jour=MARDI, motif="sport")
    f.indisponibilite(GROUPE, g1, "8h30", "10h30", jour=MERCREDI)
    instantane = f.construire()
    anomalies = verifier(instantane, [placer(td, MARDI, "13h45", n204)], DISPOS)
    assert codes(anomalies) == ["DISPO_GROUPE"]
    assert anomalies[0].message.startswith("Le groupe A1 est indisponible le mardi")
    assert anomalies[0].groupes == (a1,)
    anomalies = verifier(instantane, [placer(cm, MERCREDI, "8h30", n204)], DISPOS)
    assert codes(anomalies) == ["DISPO_GROUPE"]
    assert anomalies[0].groupes == (g1,)


def test_disponibilites_desactivees():
    f, n204, martin, _, _, td = base()
    f.indisponibilite(ENSEIGNANT, martin, "8h30", "18h00", jour=MARDI)
    placements = [placer(td, MARDI, "8h30", n204)]
    assert verifier(f.construire(), placements, ["DISPO_SALLE", "DISPO_GROUPE"]) == []


def test_indisponibilite_incoherente():
    f, n204, martin, _, _, td = base()
    f.indisponibilite(ENSEIGNANT, martin, "10h30", "8h30", jour=MARDI)  # fin avant le début
    with pytest.raises(ErreurDonneesInvalides) as erreur:
        verifier(f.construire(), [placer(td, MARDI, "8h30", n204)], DISPOS)
    assert "L'indisponibilité n° 1 (enseignant n° 1) est incohérente" in erreur.value.message
