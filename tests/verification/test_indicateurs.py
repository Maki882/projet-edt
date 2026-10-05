"""Indicateurs : `calculer_indicateurs`, valeurs calculées à la main."""

from __future__ import annotations

import json

import pytest

from edt.domaine.contraintes import CodeContrainte
from edt.verification import calculer_indicateurs

from .fabrique import ISSY, LUNDI, SAMEDI, Fabrique, placer


def semaine_type():
    """A1 (promotion) avec deux demi-groupes, 15 semaines.

    - TD de A1-G1 le lundi de 8 h 30 à 10 h 30 en N204 (Notre-Dame) ;
    - TD de A1-G1 le lundi de 16 h 00 à 18 h 00 en L101 (Issy), même enseignante ;
    - CM de A1 le samedi de 8 h 30 à 10 h 30 en N204, semaines 1 à 5 ;
    - un TD de A1-G2 non placé.
    """
    f = Fabrique()
    n204, l101 = f.salle("N204"), f.salle("L101", campus=ISSY)
    martin = f.enseignant("Mme Martin")
    a1 = f.groupe("A1", effectif=40)
    g1, g2 = f.groupe("A1-G1", parent=a1), f.groupe("A1-G2", parent=a1)
    matin = f.cours("Mathématiques TD", [g1], [martin])
    soir = f.cours("Physique TD", [g1], [martin])
    samedi = f.cours(
        "Chimie CM", [a1], type_seance="CM", samedi_autorise=True, semaines=range(1, 6)
    )
    f.cours("Anglais TD", [g2])
    placements = [
        placer(matin, LUNDI, "8h30", n204),
        placer(soir, LUNDI, "16h00", l101),
        placer(samedi, SAMEDI, "8h30", n204),
    ]
    return f.construire(), placements, (n204, l101)


def test_indicateurs_calcules_a_la_main():
    instantane, placements, (n204, l101) = semaine_type()
    indicateurs = calculer_indicateurs(instantane, placements)

    assert indicateurs.nb_seances == 4
    assert indicateurs.nb_seances_placees == 3
    assert indicateurs.pourcentage_seances_placees == 75.0
    assert indicateurs.anomalies_bloquantes_par_code == ((CodeContrainte.VOLUME, 1),)
    assert indicateurs.nb_anomalies_bloquantes == 1
    assert indicateurs.nb_semaines == 15

    # Grille : 5 jours x 4 créneaux de 2 h + 2 créneaux le samedi = 2 640 min par semaine.
    offertes = 2640 * 15
    assert indicateurs.utilisation_par_salle == (
        (n204, pytest.approx(100 * (120 * 15 + 120 * 5) / offertes)),
        (l101, pytest.approx(100 * (120 * 15) / offertes)),
    )
    assert indicateurs.pourcentage_utilisation_salles == pytest.approx(
        100 * (120 * 15 + 120 * 5 + 120 * 15) / (2 * offertes)
    )

    # A1-G1 le lundi : de 8 h 30 à 18 h = 570 min, dont 240 de cours et 60 de déjeuner.
    assert indicateurs.trous_minutes_journee == 270 * 15
    assert indicateurs.trous_minutes_demi_journee == 0
    assert indicateurs.trous_creneaux == 2 * 15  # 10 h 45 et 13 h 45
    assert indicateurs.changements_campus_groupes == 15
    assert indicateurs.changements_campus_enseignants == 15
    assert indicateurs.seances_samedi == 5
    assert indicateurs.seances_creneaux_extremes == 15 + 15 + 5
    assert indicateurs.valeur_objectif == 10 * 30 + 5 * 30 + 2 * 5 + 1 * 35


def test_anomalies_fournies_et_serialisation():
    instantane, placements, _ = semaine_type()
    indicateurs = calculer_indicateurs(instantane, placements, anomalies=[])
    assert indicateurs.nb_anomalies_bloquantes == 0
    donnees = json.loads(json.dumps(indicateurs.vers_dict()))
    assert donnees["pourcentage_seances_placees"] == 75.0
    assert donnees["anomalies_bloquantes_par_code"] == {}


def test_semaines_disjointes_ne_font_pas_de_trou():
    # Deux modules courts au même créneau sur des semaines disjointes, puis un TD : aucun trou.
    f = Fabrique()
    n204 = f.salle("N204")
    g1 = f.groupe("A1-G1")
    court_1 = f.cours("Python", [g1], semaines=range(1, 8))
    court_2 = f.cours("Réseaux", [g1], semaines=range(8, 16))
    td = f.cours("Mathématiques TD", [g1])
    placements = [
        placer(court_1, LUNDI, "8h30", n204),
        placer(court_2, LUNDI, "8h30", n204),
        placer(td, LUNDI, "10h45", n204),
    ]
    indicateurs = calculer_indicateurs(f.construire(), placements)
    assert indicateurs.trous_minutes_journee == 15 * 15  # la pause de 15 min, chaque semaine
    assert indicateurs.trous_creneaux == 0


def test_rien_a_placer():
    indicateurs = calculer_indicateurs(Fabrique().construire(), [])
    assert indicateurs.pourcentage_seances_placees is None
    assert indicateurs.pourcentage_utilisation_salles is None
    assert indicateurs.valeur_objectif == 0
