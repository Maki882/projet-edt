"""Pause déjeuner « libre bornée » (mode libre de `DEJEUNER`) : semaine type et couche datée.

Fenêtre des tests : au moins 1 h libre d'affilée entre 12 h 00 et 14 h 00.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest
from tests.outils.verification.fabrique import (
    MARDI,
    MARDI_6_OCT,
    Fabrique,
    codes,
    h,
    occurrence,
    placer,
)

from edt.domaine.contraintes import CodeContrainte, Niveau
from edt.domaine.enumerations import Gravite
from edt.erreurs import ErreurDonneesInvalides
from edt.verification import calculer_indicateurs, verifier, verifier_occurrences
from edt.verification.regles_dejeuner import plus_longue_pause

DEJEUNER = ["DEJEUNER"]
FENETRE = {
    "dejeuner_mode": "libre",
    "dejeuner_debut_min": h("12h00"),
    "dejeuner_fin_min": h("14h00"),
    "dejeuner_duree_min": 60,
}


def journee(*plages, semaines=(None, None), meme_enseignant=False, **options):
    """Groupe A1-G1 : une séance par plage (début, fin) le mardi.

    Enseignants fictifs (exclus des contraintes d'enseignant), sauf avec
    `meme_enseignant` : Mme Martin donne alors toutes les séances.
    """
    f = Fabrique(**FENETRE)
    salles = [f.salle(f"N20{i}") for i in range(len(plages))]
    g1 = f.groupe("A1-G1")
    commun = f.enseignant("Mme Martin")
    seances, placements = [], []
    for i, (debut, fin) in enumerate(plages):
        enseignant = commun if meme_enseignant else f.enseignant(f"À définir {i}", fictif=True)
        seance = f.cours(
            f"Cours {i + 1}",
            [g1],
            [enseignant],
            duree=h(fin) - h(debut),
            semaines=semaines[i] if i < len(semaines) else None,
            **options,
        )
        seances.append(seance)
        placements.append(placer(seance, MARDI, debut, salles[i]))
    return f, seances, placements


def test_plus_longue_pause():
    assert plus_longue_pause(720, 840, []) == 120
    assert plus_longue_pause(720, 840, [(660, 780), (810, 900)]) == 30
    assert plus_longue_pause(720, 840, [(720, 840)]) == 0
    assert plus_longue_pause(720, 840, [(600, 700)]) == 120


def test_pause_presente():
    f, _, placements = journee(("10h45", "12h45"), ("13h45", "15h45"))
    assert verifier(f.construire(), placements, DEJEUNER) == []


def test_fenetre_pleine():
    f, _, placements = journee(("11h00", "13h00"), ("13h30", "15h30"))
    anomalies = verifier(f.construire(), placements, DEJEUNER)
    assert codes(anomalies) == ["DEJEUNER"]  # une seule anomalie pour le groupe, pas une par séance
    (anomalie,) = anomalies
    assert anomalie.gravite is Gravite.ERREUR
    assert anomalie.message == (
        "Le groupe A1-G1 n'a pas de pause déjeuner d'au moins 1 h entre 12 h 00 et 14 h 00 le "
        "mardi (au plus 30 min libres d'affilée) : Cours 1 (A1-G1) de 11 h 00 à 13 h 00 et "
        "Cours 2 (A1-G1) de 13 h 30 à 15 h 30."
    )
    assert anomalie.seances == ("1-1", "2-1")
    assert anomalie.groupes == (1,)
    assert anomalie.jour == MARDI


def test_une_seance_couvre_toute_la_fenetre():
    f, _, placements = journee(("12h00", "14h00"))
    (anomalie,) = verifier(f.construire(), placements, DEJEUNER)
    assert "(aucun moment libre)" in anomalie.message


def test_jour_sans_seance_dans_la_fenetre():
    # La séance du matin finit pile à 12 h : elle ne recoupe pas la fenêtre.
    f, _, placements = journee(("10h00", "12h00"), ("16h00", "18h00"))
    assert verifier(f.construire(), placements, DEJEUNER) == []


def test_seances_aux_semaines_disjointes():
    # Semaines 1 à 7 : pause de 13 h à 14 h ; semaines 8 à 15 : pause de 12 h à 13 h.
    disjointes = (range(1, 8), range(8, 16))
    f, _, placements = journee(("11h00", "13h00"), ("13h00", "15h00"), semaines=disjointes)
    assert verifier(f.construire(), placements, DEJEUNER) == []

    # Semaines qui se croisent (7 et 8) : pas de pause ces semaines-là seulement.
    croisees = (range(1, 9), range(7, 16))
    f, _, placements = journee(("11h00", "13h00"), ("13h00", "15h00"), semaines=croisees)
    (anomalie,) = verifier(f.construire(), placements, DEJEUNER)
    assert "le mardi (semaines 7 et 8)" in anomalie.message


def test_enseignant_sans_pause_et_enseignant_fictif():
    f, _, placements = journee(("11h00", "13h00"), ("13h30", "15h30"), meme_enseignant=True)
    anomalies = verifier(f.construire(), placements, DEJEUNER)
    assert sorted((a.enseignants, a.groupes) for a in anomalies) == [((), (1,)), ((1,), ())]
    assert any(a.message.startswith("Mme Martin n'a pas de pause déjeuner") for a in anomalies)


def test_demi_groupes_qui_dejeunent_en_decale():
    # A1-G1 a cours de 12 h à 13 h, A1-G2 de 13 h à 14 h : chacun a sa pause, la promotion
    # aussi (les séances d'un sous-groupe ne concernent qu'une partie de ses étudiants).
    f = Fabrique(**FENETRE)
    a1 = f.groupe("A1", effectif=40)
    g1, g2 = f.groupe("A1-G1", parent=a1), f.groupe("A1-G2", parent=a1)
    td1 = f.cours("TD 1", [g1], duree=60)
    td2 = f.cours("TD 2", [g2], duree=60)
    placements = [placer(td1, MARDI, "12h00"), placer(td2, MARDI, "13h00")]
    assert verifier(f.construire(), placements, DEJEUNER) == []

    # Un CM de la promotion sur toute la fenêtre : une seule anomalie, qui nomme A1 et
    # liste dans `groupes` tous les groupes privés de pause.
    cm = f.cours("CM", [a1], duree=120)
    (anomalie,) = verifier(f.construire(), [placer(cm, MARDI, "12h00")], DEJEUNER)
    assert anomalie.message.startswith("Le groupe A1 n'a pas de pause déjeuner")
    assert anomalie.groupes == (a1, g1, g2)


def test_seances_imposees_information():
    f, _, placements = journee(("12h00", "14h00"), debut_impose=h("12h00"))
    (anomalie,) = verifier(f.construire(), placements, DEJEUNER)
    assert anomalie.gravite is Gravite.INFORMATION
    assert "imposées : l'écart est signalé pour information" in anomalie.message


def test_gravite_selon_le_niveau():
    f, _, placements = journee(("11h00", "13h00"), ("13h30", "15h30"))
    instantane = f.construire()
    for niveau, gravite in (
        (Niveau.NECESSAIRE, Gravite.ERREUR),
        (Niveau.IMPORTANT, Gravite.AVERTISSEMENT),
        (Niveau.FACULTATIF, Gravite.INFORMATION),
    ):
        (anomalie,) = verifier(instantane, placements, niveaux={CodeContrainte.DEJEUNER: niveau})
        assert (anomalie.gravite, anomalie.niveau) == (gravite, niveau)


def test_modes_fixe_et_aucun_inchanges():
    # Mode fixe par défaut (12 h 45 - 13 h 45) : la règle porte sur chaque séance.
    f = Fabrique()
    g1 = f.groupe("A1-G1")
    seance = f.cours("Cours", [g1], duree=60)
    anomalies = verifier(f.construire(), [placer(seance, MARDI, "13h00")], DEJEUNER)
    assert anomalies[0].message.startswith("La séance Cours (A1-G1) le mardi de 13 h 00")
    f.parametres = f.parametres.avec(dejeuner_mode="aucun")
    assert verifier(f.construire(), [placer(seance, MARDI, "13h00")], DEJEUNER) == []


def test_couche_datee():
    f, seances, _ = journee(("11h00", "13h00"), ("13h30", "15h30"))
    instantane = f.construire()
    mardi, mardi_suivant = dt.date(2026, 10, 6), dt.date(2026, 10, 13)
    meme_date = [
        occurrence(seances[0], mardi, "11h00", identifiant=1),
        occurrence(seances[1], mardi, "13h30", identifiant=2),
    ]
    (anomalie,) = verifier_occurrences(instantane, meme_date, DEJEUNER)
    assert anomalie.date == mardi
    assert anomalie.occurrences == (1, 2)
    assert "le mardi 6 octobre 2026 (au plus 30 min libres d'affilée)" in anomalie.message
    dates_differentes = [meme_date[0], occurrence(seances[1], mardi_suivant, "13h30")]
    assert verifier_occurrences(instantane, dates_differentes, DEJEUNER) == []


# --------------------------------------------------------------------------- #
# Cas limites (banque de tests : `pytest -m cas_limite`)
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
def test_cycle_de_groupes_ne_rend_pas_le_verificateur_muet():
    """Cas : deux groupes parents l'un de l'autre (import, retouche de la base), un cours de B à midi en déjeuner libre.

    Risque : plus aucun groupe feuille, donc plus aucune personne : déjeuner, pause, maxima et
    règles personnalisées ne vérifient plus rien, sans un mot.
    """
    f = Fabrique(**FENETRE)
    a = f.groupe("A")
    b = f.groupe("B", parent=a)
    f.groupe("C")  # hors de la boucle
    f.groupes[a - 1] = dataclasses.replace(f.groupes[a - 1], parent_id=b)  # parents croisés
    cours = f.cours("Cours", [b], duree=120)
    instantane, placements = f.construire(), [placer(cours, MARDI, "12h00")]
    for verification in (
        lambda: verifier(instantane, placements, DEJEUNER),
        lambda: verifier_occurrences(instantane, [occurrence(cours, MARDI_6_OCT, "12h00")]),
        lambda: calculer_indicateurs(instantane, placements, []),
    ):
        with pytest.raises(ErreurDonneesInvalides, match="boucle") as erreur:
            verification()
        assert "« A »" in erreur.value.message and "« B »" in erreur.value.message
        assert "« C »" not in erreur.value.message
