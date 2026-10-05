"""Couche datée : `verifier_occurrences`."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest

from edt.domaine.enumerations import (
    CibleIndisponibilite,
    Gravite,
    ModeVolume,
    NatureJourFerme,
    StatutOccurrence,
)
from edt.domaine.instantane import Occurrence, Placement
from edt.erreurs import ErreurDonneesInvalides
from edt.verification import verifier_occurrences

from .fabrique import MARDI, Fabrique, codes, h, occurrence

MARDI_6_OCT = dt.date(2026, 10, 6)
MARDI_13_OCT = dt.date(2026, 10, 13)
MERCREDI_11_NOV = dt.date(2026, 11, 11)


def base(**options_martin):
    f = Fabrique()
    n204, n205 = f.salle("N204"), f.salle("N205")
    martin = f.enseignant("Mme Martin", **options_martin)
    a1 = f.groupe("A1", effectif=40)
    g1, g2 = f.groupe("A1-G1", parent=a1), f.groupe("A1-G2", parent=a1)
    maths = f.cours("Mathématiques TD", [g1], [martin])
    physique = f.cours("Physique TD", [g2], [martin])
    return f, n204, n205, martin, maths, physique


def test_seances_datees_valides():
    f, n204, n205, _, maths, physique = base()
    occurrences = [
        occurrence(maths, MARDI_6_OCT, "8h30", n204, identifiant=1),
        occurrence(physique, MARDI_6_OCT, "10h45", n205, identifiant=2),
    ]
    assert verifier_occurrences(f.construire(), occurrences) == []


def test_conflit_a_la_meme_date_seulement():
    f, n204, n205, martin, maths, physique = base()
    instantane = f.construire()
    meme_date = [
        occurrence(maths, MARDI_6_OCT, "8h30", n204, identifiant=1),
        occurrence(physique, MARDI_6_OCT, "8h30", n205, identifiant=2),
    ]
    anomalies = verifier_occurrences(instantane, meme_date)
    assert codes(anomalies) == ["ENSEIGNANT_UNIQUE"]
    assert anomalies[0].message.startswith(
        "Mme Martin donne deux séances en même temps le mardi 6 octobre 2026 de 8 h 30 à 10 h 30"
    )
    assert anomalies[0].date == MARDI_6_OCT
    assert anomalies[0].jour is None
    assert anomalies[0].occurrences == (1, 2)
    assert anomalies[0].enseignants == (martin,)
    # Même jour de la semaine, dates différentes : aucun conflit.
    autre_semaine = [meme_date[0], replace(meme_date[1], date=MARDI_13_OCT)]
    assert verifier_occurrences(instantane, autre_semaine) == []


def test_statuts_qui_n_occupent_pas_les_ressources():
    f, n204, _, _, maths, physique = base()
    occurrences = [
        occurrence(maths, MARDI_6_OCT, "8h30", n204, identifiant=1),
        occurrence(
            physique, MARDI_6_OCT, "8h30", n204, identifiant=2, statut=StatutOccurrence.ANNULEE
        ),
        occurrence(
            physique,
            MARDI_6_OCT,
            "8h30",
            n204,
            identifiant=3,
            statut=StatutOccurrence.A_PROGRAMMER,
        ),
    ]
    anomalies = verifier_occurrences(f.construire(), occurrences)
    assert codes(anomalies) == ["VOLUME"]
    assert anomalies[0].gravite is Gravite.AVERTISSEMENT
    assert anomalies[0].occurrences == (3,)
    assert "reste à programmer" in anomalies[0].message


def test_occurrence_deplacee_occupe_les_ressources():
    f, n204, _, _, maths, physique = base()
    occurrences = [
        occurrence(maths, MARDI_6_OCT, "8h30", n204, identifiant=1),
        occurrence(
            physique, MARDI_6_OCT, "8h30", n204, identifiant=2, statut=StatutOccurrence.DEPLACEE
        ),
    ]
    assert "SALLE_UNIQUE" in codes(verifier_occurrences(f.construire(), occurrences))


def test_jour_ferme():
    f, n204, _, _, maths, _ = base()
    f.jour_ferme(MERCREDI_11_NOV, libelle="Armistice de 1918")
    anomalies = verifier_occurrences(
        f.construire(), [occurrence(maths, MERCREDI_11_NOV, "8h30", n204)], ["JOUR_FERME"]
    )
    assert codes(anomalies) == ["JOUR_FERME"]
    assert anomalies[0].message == (
        "La séance Mathématiques TD (A1-G1) a lieu le mercredi 11 novembre 2026 de 8 h 30 à "
        "10 h 30 alors que ce jour est fermé (Armistice de 1918, jour férié)."
    )


def test_jour_ferme_pour_un_autre_groupe_ou_ponctuel_en_periode_d_examens():
    f, n204, _, _, maths, _ = base()
    autre = f.groupe("B1")
    f.jour_ferme(MARDI_6_OCT, libelle="Sortie B1", groupes=[autre])
    examen = f.cours("Examen", [1], mode_volume=ModeVolume.PONCTUEL, date_ponctuelle=MARDI_13_OCT)
    f.jour_ferme(MARDI_13_OCT, libelle="Partiels", nature=NatureJourFerme.EXAMENS)
    occurrences = [
        occurrence(maths, MARDI_6_OCT, "8h30", n204),
        occurrence(examen, MARDI_13_OCT, "13h45", n204),
    ]
    assert verifier_occurrences(f.construire(), occurrences, ["JOUR_FERME"]) == []


def test_indisponibilite_datee_a_la_date():
    f, n204, _, martin, maths, _ = base()
    f.indisponibilite(
        CibleIndisponibilite.ENSEIGNANT,
        martin,
        "8h00",
        "12h00",
        date_debut=MARDI_6_OCT,
        date_fin=MARDI_6_OCT,
        motif="jury",
    )
    instantane = f.construire()
    anomalies = verifier_occurrences(
        instantane, [occurrence(maths, MARDI_6_OCT, "8h30", n204)], ["DISPO_ENSEIGNANT"]
    )
    assert codes(anomalies) == ["DISPO_ENSEIGNANT"]
    assert "le mardi 6 octobre 2026 de 8 h 00 à 12 h 00 (jury)" in anomalies[0].message
    autre_date = [occurrence(maths, MARDI_13_OCT, "8h30", n204)]
    assert verifier_occurrences(instantane, autre_date, ["DISPO_ENSEIGNANT"]) == []


def test_indisponibilite_recurrente_a_la_date():
    f, n204, _, martin, maths, _ = base()
    f.indisponibilite(CibleIndisponibilite.ENSEIGNANT, martin, "8h00", "12h00", jour=MARDI)
    anomalies = verifier_occurrences(
        f.construire(), [occurrence(maths, MARDI_13_OCT, "8h30", n204)], ["DISPO_ENSEIGNANT"]
    )
    assert codes(anomalies) == ["DISPO_ENSEIGNANT"]


def test_maxima_par_date_et_par_semaine_reelle():
    f, n204, _, _, maths, physique = base(max_jour=3 * 60, max_semaine=5 * 60)
    mercredi = MARDI_6_OCT + dt.timedelta(days=1)
    occurrences = [
        occurrence(maths, MARDI_6_OCT, "8h30", n204),
        occurrence(physique, MARDI_6_OCT, "10h45", n204),
        occurrence(maths, mercredi, "8h30", n204),
        occurrence(physique, MARDI_13_OCT, "8h30", n204),
    ]
    anomalies = verifier_occurrences(f.construire(), occurrences, ["MAX_ENSEIGNANT"])
    assert [a.message for a in anomalies] == [
        "Mme Martin dépasse son maximum de 5 h de cours par semaine la semaine du lundi "
        "5 octobre 2026 : 6 h de cours.",
        "Mme Martin dépasse son maximum de 3 h de cours par jour le mardi 6 octobre 2026 : "
        "4 h de cours.",
    ]


def test_horaires_date_ponctuelle():
    f, n204, _, _, _, _ = base()
    examen = f.cours("Examen", [1], mode_volume=ModeVolume.PONCTUEL, date_ponctuelle=MARDI_13_OCT)
    instantane = f.construire()
    assert (
        verifier_occurrences(
            instantane, [occurrence(examen, MARDI_13_OCT, "8h30", n204)], ["HORAIRES"]
        )
        == []
    )
    anomalies = verifier_occurrences(
        instantane, [occurrence(examen, MARDI_6_OCT, "8h30", n204)], ["HORAIRES"]
    )
    assert "elle doit avoir lieu le mardi 13 octobre 2026" in anomalies[0].message


def test_verrou_d_une_occurrence_planifiee():
    f = Fabrique()
    n204, n205 = f.salle("N204"), f.salle("N205")
    besoin = f.besoin("Anglais TD", [f.groupe("A1-G1")])
    seance = f.seance(besoin, verrou=Placement("1-1", MARDI, h("8h30"), h("10h30"), n204))
    instantane = f.construire()
    bonne = occurrence(seance, MARDI_6_OCT, "8h30", n204)
    assert verifier_occurrences(instantane, [bonne], ["VERROU"]) == []
    deplacee = replace(bonne, salle_id=n205)
    anomalies = verifier_occurrences(instantane, [deplacee], ["VERROU"])
    assert codes(anomalies) == ["VERROU"]
    assert (
        verifier_occurrences(
            instantane, [replace(deplacee, statut=StatutOccurrence.DEPLACEE)], ["VERROU"]
        )
        == []
    )


def test_besoin_ou_seance_inconnus():
    f, n204, _, _, maths, _ = base()
    instantane = f.construire()
    inconnu = Occurrence(7, 99, None, MARDI_6_OCT, h("8h30"), h("10h30"), n204)
    anomalies = verifier_occurrences(instantane, [inconnu])
    assert codes(anomalies) == ["VOLUME"]
    assert anomalies[0].gravite is Gravite.ERREUR
    assert anomalies[0].occurrences == (7,)
    assert anomalies[0].besoins == (99,)

    perimee = replace(occurrence(maths, MARDI_6_OCT, "8h30", n204), seance_id="1-4")
    anomalies = verifier_occurrences(instantane, [perimee])
    assert codes(anomalies) == ["VOLUME"]
    assert anomalies[0].gravite is Gravite.AVERTISSEMENT


@pytest.mark.parametrize(
    "changements",
    [{"fin": h("8h00")}, {"debut": -1}, {"salle_id": 99}, {"seance_id": "2-1"}, {"date": None}],
)
def test_occurrences_invalides(changements):
    f, n204, _, _, maths, _ = base()
    with pytest.raises(ErreurDonneesInvalides):
        verifier_occurrences(
            f.construire(), [replace(occurrence(maths, MARDI_6_OCT, "8h30", n204), **changements)]
        )


def test_identifiant_d_occurrence_duplique():
    f, n204, _, _, maths, _ = base()
    double = occurrence(maths, MARDI_6_OCT, "8h30", n204, identifiant=5)
    with pytest.raises(ErreurDonneesInvalides) as erreur:
        verifier_occurrences(f.construire(), [double, replace(double, date=MARDI_13_OCT)])
    assert "identifiant déjà porté" in erreur.value.details[0]


def test_occurrence_candidate_sans_identifiant():
    f, n204, _, _, maths, physique = base()
    candidates = [
        occurrence(maths, MARDI_6_OCT, "8h30", n204),
        occurrence(physique, MARDI_6_OCT, "8h30", n204),
    ]
    anomalies = verifier_occurrences(f.construire(), candidates, ["SALLE_UNIQUE"])
    assert codes(anomalies) == ["SALLE_UNIQUE"]
    assert anomalies[0].occurrences == ()
    assert anomalies[0].seances == ("1-1", "2-1")


def test_liste_vide():
    assert verifier_occurrences(Fabrique().construire(), []) == []
