"""Couche datée : `verifier_occurrences`."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest
from tests.outils.verification.fabrique import (
    MARDI,
    MARDI_6_OCT,
    MARDI_13_OCT,
    Fabrique,
    codes,
    h,
    occurrence,
)

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


def test_indisponibilite_incoherente_dans_la_couche_datee():
    f, n204, _, martin, maths, _ = base()
    f.indisponibilite(CibleIndisponibilite.ENSEIGNANT, martin, "8h30", "10h30")  # ni jour ni dates
    with pytest.raises(ErreurDonneesInvalides):
        verifier_occurrences(f.construire(), [occurrence(maths, MARDI_6_OCT, "8h30", n204)])


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
    assert anomalies[0].message.startswith(
        "La séance n° 4 de « Mathématiques TD (A1-G1) » du mardi 6 octobre 2026 vient d'une "
        "séance de la semaine type que le besoin ne demande plus"
    )
    assert "1-4" not in anomalies[0].message


@pytest.mark.parametrize(
    "changements",
    [{"fin": h("8h00")}, {"debut": -1}, {"salle_id": "N204"}, {"seance_id": "2-1"}, {"date": None}],
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


def test_verrou_ne_couvre_pas_une_occurrence_deplacee_a_la_main():
    # Constat n° 15 de la revue : le verrou fixe la place de la semaine type, pas une nouvelle
    # date choisie à la main. Déplacée un samedi interdit, l'occurrence est une ERREUR JOURS,
    # sans le faux « créneau imposé ».
    f = Fabrique()
    n204 = f.salle("N204")
    besoin = f.besoin("Traitement du signal TD", [f.groupe("A3-G1")])
    seance = f.seance(besoin, verrou=Placement("1-1", MARDI, h("8h30"), h("10h30"), n204))
    instantane = f.construire()
    samedi = dt.date(2026, 11, 28)
    deplacee = occurrence(seance, samedi, "8h30", n204, statut=StatutOccurrence.DEPLACEE)
    anomalies = verifier_occurrences(instantane, [deplacee], ["JOURS"])
    assert codes(anomalies) == ["JOURS"]
    assert anomalies[0].gravite is Gravite.ERREUR
    assert "imposé" not in anomalies[0].message

    # Même jour de la semaine que le verrou mais autre heure : plus imposée non plus.
    autre_heure = occurrence(seance, MARDI_6_OCT, "12h00", n204, statut=StatutOccurrence.DEPLACEE)
    anomalies = verifier_occurrences(instantane, [autre_heure], ["DEJEUNER"])
    assert [a.gravite for a in anomalies] == [Gravite.ERREUR]


def test_seance_verrouillee_hors_grille_restee_a_sa_place_reste_une_information():
    f = Fabrique()
    n204 = f.salle("N204")
    besoin = f.besoin("Conférence", [f.groupe("A1")])
    seance = f.seance(besoin, verrou=Placement("1-1", MARDI, h("12h00"), h("14h00"), n204))
    a_sa_place = occurrence(seance, MARDI_6_OCT, "12h00", n204)
    anomalies = verifier_occurrences(f.construire(), [a_sa_place], ["HORAIRES", "DEJEUNER"])
    assert codes(anomalies) == ["HORAIRES", "DEJEUNER"]
    assert {a.gravite for a in anomalies} == {Gravite.INFORMATION}


# --------------------------------------------------------------------------- #
# Cas limites (banque de tests : `pytest -m cas_limite`)
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
def test_occurrence_hors_du_semestre_signalee():
    """Cas : séances datées avant le premier ou après le dernier jour du semestre (semestre raccourci, import qui déborde).

    Risque : aucune anomalie (rapport « valide »), et les règles personnalisées, bornées aux
    semaines du semestre, muettes sur ces dates.
    """
    # Semestre de la fabrique : du lundi 7 septembre au vendredi 18 décembre 2026. Hors de
    # ses dates, le calendrier est fermé, comme pour l'instanciation : JOUR_FERME.
    f, n204, _, _, maths, _ = base()
    instantane = f.construire()
    dates = {
        1: dt.date(2026, 9, 1),  # avant le premier jour
        2: dt.date(2026, 7, 7),  # bien avant
        3: dt.date(2027, 3, 2),  # après le dernier jour
        4: dt.date(2026, 12, 19),  # samedi de la dernière semaine, après le dernier jour
        5: dt.date(2026, 9, 7),  # premier jour : dans le semestre
        6: dt.date(2026, 12, 18),  # dernier jour : dans le semestre
    }
    occurrences = [
        occurrence(maths, jour, "8h30", n204, identifiant=identifiant)
        for identifiant, jour in dates.items()
    ]
    anomalies = verifier_occurrences(instantane, occurrences, ["JOUR_FERME"])
    assert [(str(a.code), a.gravite, a.occurrences) for a in anomalies] == [
        ("JOUR_FERME", Gravite.ERREUR, (2,)),
        ("JOUR_FERME", Gravite.ERREUR, (1,)),
        ("JOUR_FERME", Gravite.ERREUR, (4,)),
        ("JOUR_FERME", Gravite.ERREUR, (3,)),
    ]
    assert anomalies[1].message == (
        "La séance Mathématiques TD (A1-G1) a lieu le mardi 1er septembre 2026 de 8 h 30 à "
        "10 h 30, en dehors du semestre « Semestre de test » (du lundi 7 septembre 2026 au "
        "vendredi 18 décembre 2026) : la replacer dans le semestre ou la supprimer."
    )
    assert anomalies[1].date == dt.date(2026, 9, 1) and anomalies[1].seances == ("1-1",)
    # Sans JOUR_FERME, rien : les autres règles vérifient ces séances comme les autres.
    assert verifier_occurrences(instantane, occurrences, ["SALLE_UNIQUE"]) == []


@pytest.mark.cas_limite
def test_occurrence_dupliquee_signalee_comme_doublon():
    """Cas : la même séance datée deux fois à la même date, heure et salle (réimport, instanciation reprise).

    Risque : trois conflits « X et X » (salle, enseignant, groupe) pour un seul défaut, dont
    la cause, le doublon, n'est jamais nommée.
    """
    f, n204, _, _, maths, _ = base()
    instantane = f.construire()
    copies = [occurrence(maths, MARDI_6_OCT, "8h30", n204, identifiant=i) for i in (1, 2)]
    anomalies = verifier_occurrences(instantane, copies)
    assert [(str(a.code), a.gravite, a.occurrences) for a in anomalies] == [
        ("VOLUME", Gravite.ERREUR, (1, 2))
    ]
    assert anomalies[0].message == (
        "La séance Mathématiques TD (A1-G1) a lieu 2 fois en même temps le mardi 6 octobre "
        "2026 (de 8 h 30 à 10 h 30) : garder une seule de ces séances datées."
    )
    assert anomalies[0].date == MARDI_6_OCT and anomalies[0].seances == ("1-1",)
    # Deux séances datées d'une même séance à des heures disjointes (rattrapage) : pas un doublon.
    rattrapage = [copies[0], occurrence(maths, MARDI_6_OCT, "13h45", n204, identifiant=2)]
    assert verifier_occurrences(instantane, rattrapage, ["VOLUME", "SALLE_UNIQUE"]) == []
    # Sans VOLUME, les conflits des copies restent signalés : le doublon ne passe pas inaperçu.
    assert codes(verifier_occurrences(instantane, copies, ["SALLE_UNIQUE"])) == ["SALLE_UNIQUE"]


@pytest.mark.cas_limite
def test_date_sous_forme_de_datetime_refusee():
    """Cas : une séance datée reçue avec un `datetime` (date et heure) au lieu d'une date.

    Risque : `TypeError` au tri des jours (erreur 500), ou conflit manqué entre ce
    `datetime` et une séance du même jour, jamais égaux.
    """
    f, n204, _, _, maths, physique = base()
    instantane = f.construire()
    avec_heure = Occurrence(
        1, maths.besoin.id, maths.id, dt.datetime(2026, 10, 6, 8, 30), 510, 630, n204
    )
    meme_jour = occurrence(physique, MARDI_6_OCT, "8h30", n204, identifiant=2)
    with pytest.raises(ErreurDonneesInvalides) as erreur:
        verifier_occurrences(instantane, [avec_heure, meme_jour])
    assert "date absente ou invalide" in erreur.value.details[0]


@pytest.mark.cas_limite
def test_maxima_hebdomadaires_a_cheval_sur_deux_annees_civiles():
    """Cas : un enseignant au maximum hebdomadaire a cours le 29 décembre 2026 et le 2 janvier 2027, même semaine réelle.

    Risque : un regroupement par année civile couperait la semaine du Nouvel An et masquerait
    le dépassement.
    """
    f, n204, n205, _, maths, physique = base(max_semaine=3 * 60)
    # Les deux dates sont après la fin du semestre de la fabrique (18 décembre) : seule
    # MAX_ENSEIGNANT est vérifiée, sinon JOUR_FERME les signalerait aussi.
    occurrences = [
        occurrence(maths, dt.date(2026, 12, 29), "8h30", n204, identifiant=1),
        occurrence(physique, dt.date(2027, 1, 2), "8h30", n205, identifiant=2),
    ]
    (anomalie,) = verifier_occurrences(f.construire(), occurrences, ["MAX_ENSEIGNANT"])
    assert anomalie.date == dt.date(2026, 12, 28)
    assert anomalie.message.endswith("la semaine du lundi 28 décembre 2026 : 4 h de cours.")
    assert anomalie.occurrences == (1, 2)
