"""Règles de temps d'une séance : DEJEUNER, HORAIRES, JOURS."""

from __future__ import annotations

import datetime as dt

import pytest
from tests.outils.verification.fabrique import (
    DIMANCHE,
    JEUDI,
    LUNDI,
    MARDI,
    SAMEDI,
    Fabrique,
    codes,
    h,
    placer,
)

from edt.domaine.enumerations import Gravite, ModeVolume
from edt.verification import verifier


def une_seance(duree=120, type_seance="TD", **options):
    """Instantané à une séance (groupe A1-G1, salle N204) ; renvoie (fabrique, séance, salle)."""
    f = Fabrique()
    n204 = f.salle("N204")
    martin = f.enseignant("Mme Martin")
    g1 = f.groupe("A1-G1")
    seance = f.cours(
        "Mathématiques TD", [g1], [martin], duree=duree, type_seance=type_seance, **options
    )
    return f, seance, n204


# --------------------------------------------------------------------------- #
# Déjeuner
# --------------------------------------------------------------------------- #


def test_dejeuner_respecte_par_des_seances_contigues():
    f, seance, n204 = une_seance()
    assert verifier(f.construire(), [placer(seance, MARDI, "10h45", n204)], ["DEJEUNER"]) == []
    assert verifier(f.construire(), [placer(seance, MARDI, "13h45", n204)], ["DEJEUNER"]) == []


def test_dejeuner_empiete():
    f, seance, n204 = une_seance()
    anomalies = verifier(f.construire(), [placer(seance, MARDI, "12h00", n204)], ["DEJEUNER"])
    assert codes(anomalies) == ["DEJEUNER"]
    assert anomalies[0].gravite is Gravite.ERREUR
    assert anomalies[0].message == (
        "La séance Mathématiques TD (A1-G1) le mardi de 12 h 00 à 14 h 00 empiète sur la pause "
        "déjeuner (de 12 h 45 à 13 h 45)."
    )


def test_dejeuner_seance_imposee_information():
    f, seance, n204 = une_seance(debut_impose=h("12h00"))
    anomalies = verifier(f.construire(), [placer(seance, MARDI, "12h00", n204)], ["DEJEUNER"])
    assert anomalies[0].gravite is Gravite.INFORMATION
    assert "pour information" in anomalies[0].message


def test_dejeuner_sans_pause_ne_releve_rien():
    """Mode « aucun » (pas de pause) : la règle ne s'applique plus, même active."""
    f, seance, n204 = une_seance()
    midi = [placer(seance, MARDI, "12h00", n204)]
    assert codes(verifier(f.construire(), midi, ["DEJEUNER"])) == ["DEJEUNER"]
    f.parametres = f.parametres.avec(dejeuner_mode="aucun")
    assert verifier(f.construire(), midi, ["DEJEUNER"]) == []
    assert "DEJEUNER" not in codes(verifier(f.construire(), midi))  # contraintes par défaut


def test_dejeuner_desactive():
    f, seance, n204 = une_seance()
    assert verifier(f.construire(), [placer(seance, MARDI, "12h00", n204)], ["HORAIRES"]) != []
    assert verifier(f.construire(), [placer(seance, MARDI, "12h00", n204)], ["JOURS"]) == []


# --------------------------------------------------------------------------- #
# Horaires
# --------------------------------------------------------------------------- #


def horaires(f, placement):
    return verifier(f.construire(), [placement], ["HORAIRES"])


def test_horaires_sur_la_grille():
    f, seance, n204 = une_seance()
    for debut in ("8h30", "10h45", "13h45", "16h00"):
        assert horaires(f, placer(seance, LUNDI, debut, n204)) == []


def test_horaires_avant_ouverture_et_hors_grille():
    f, seance, n204 = une_seance()
    anomalies = horaires(f, placer(seance, LUNDI, "8h00", n204))
    assert codes(anomalies) == ["HORAIRES"]
    assert anomalies[0].gravite is Gravite.ERREUR
    assert anomalies[0].message == (
        "La séance Mathématiques TD (A1-G1) le lundi de 8 h 00 à 10 h 00 sort de la grille "
        "horaire : elle commence avant l'ouverture (8 h 30) ; elle ne commence pas sur un début "
        "de créneau de la grille du lundi (8 h 30, 10 h 45, 13 h 45 ou 16 h 00)."
    )


def test_horaires_apres_fermeture():
    f, seance, n204 = une_seance(duree=180)
    anomalies = horaires(f, placer(seance, LUNDI, "16h00", n204))
    assert "elle finit après la fermeture (18 h 00)" in anomalies[0].message


def test_horaires_debordement_de_demi_journee():
    f, seance, n204 = une_seance(duree=180)
    anomalies = horaires(f, placer(seance, LUNDI, "10h45", n204))
    assert codes(anomalies) == ["HORAIRES"]
    assert "elle déborde de sa demi-journée, dont le dernier créneau finit à 12 h 45" in (
        anomalies[0].message
    )


def test_horaires_seance_longue_dans_sa_demi_journee():
    # DST de 1re année : jeudi de 13 h 45 à 18 h, dans l'après-midi.
    f, seance, n204 = une_seance(duree=255)
    assert horaires(f, placer(seance, JEUDI, "13h45", n204)) == []


def test_horaires_duree_differente():
    f, seance, n204 = une_seance()
    anomalies = horaires(f, placer(seance, LUNDI, "8h30", n204, fin="10h00"))
    assert anomalies[0].gravite is Gravite.ERREUR
    assert "elle dure 1 h 30 au lieu des 2 h demandées" in anomalies[0].message


def test_horaires_jour_et_debut_imposes():
    f, seance, n204 = une_seance(jour_impose=JEUDI)
    anomalies = horaires(f, placer(seance, MARDI, "8h30", n204))
    assert codes(anomalies) == ["HORAIRES"]
    assert "elle doit avoir lieu un jeudi" in anomalies[0].message
    assert horaires(f, placer(seance, JEUDI, "8h30", n204)) == []

    f, seance, n204 = une_seance(debut_impose=h("13h45"))
    anomalies = horaires(f, placer(seance, MARDI, "16h00", n204))
    assert [a.gravite for a in anomalies] == [Gravite.ERREUR]
    assert "elle doit commencer à 13 h 45" in anomalies[0].message


def test_horaires_creneau_impose_hors_grille_information():
    # DST de 2e année : lundi de 8 h à 12 h, créneau fixe.
    f, seance, n204 = une_seance(duree=240, debut_impose=h("8h00"), jour_impose=LUNDI)
    anomalies = horaires(f, placer(seance, LUNDI, "8h00", n204))
    assert [a.gravite for a in anomalies] == [Gravite.INFORMATION]
    assert "Son créneau est imposé : l'écart est signalé pour information." in anomalies[0].message


def test_horaires_imposee_mais_duree_fausse_reste_une_erreur():
    f, seance, n204 = une_seance(duree=240, debut_impose=h("8h00"))
    anomalies = horaires(f, placer(seance, LUNDI, "8h00", n204, fin="11h00"))
    assert sorted(a.gravite for a in anomalies) == sorted([Gravite.ERREUR, Gravite.INFORMATION])


def test_horaires_date_ponctuelle_respectee_par_le_jour():
    jeudi = dt.date(2026, 10, 8)
    f, seance, n204 = une_seance(mode_volume=ModeVolume.PONCTUEL, date_ponctuelle=jeudi)
    assert horaires(f, placer(seance, JEUDI, "8h30", n204)) == []
    anomalies = horaires(f, placer(seance, MARDI, "8h30", n204))
    assert "elle doit avoir lieu un jeudi" in anomalies[0].message


# --------------------------------------------------------------------------- #
# Jours
# --------------------------------------------------------------------------- #


def jours(f, placement):
    return verifier(f.construire(), [placement], ["JOURS"])


def test_jours_semaine_valide():
    f, seance, n204 = une_seance()
    assert jours(f, placer(seance, MARDI, "8h30", n204)) == []


def test_samedi_non_autorise():
    f, seance, n204 = une_seance()
    anomalies = jours(f, placer(seance, SAMEDI, "8h30", n204))
    assert anomalies[0].gravite is Gravite.ERREUR
    assert anomalies[0].message == (
        "La séance Mathématiques TD (A1-G1) est placée le samedi alors que le type de séance TD "
        "n'est pas autorisé le samedi."
    )


def test_samedi_autorise_pour_un_tp():
    f, seance, n204 = une_seance(type_seance="TP", samedi_autorise=True)
    assert jours(f, placer(seance, SAMEDI, "8h30", n204)) == []


def test_jour_sans_creneau():
    f, seance, n204 = une_seance()
    anomalies = jours(f, placer(seance, DIMANCHE, "8h30", n204))
    assert codes(anomalies) == ["JOURS"]
    # La grille vide du dimanche relève de HORAIRES (test suivant) : JOURS ne dit que
    # l'écart aux jours travaillés.
    assert "le dimanche ne fait pas partie des jours travaillés" in anomalies[0].message
    assert "aucun créneau" not in anomalies[0].message


def test_jours_seance_imposee_information():
    f, seance, n204 = une_seance(debut_impose=h("8h30"))
    anomalies = jours(f, placer(seance, SAMEDI, "8h30", n204))
    assert anomalies[0].gravite is Gravite.INFORMATION


# --------------------------------------------------------------------------- #
# Cas limites (banque de tests : `pytest -m cas_limite`)
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
def test_jour_sans_creneau_signale_une_seule_fois():
    """Cas : une séance placée un jour sans créneau dans la grille (dimanche, ou grille d'un jour vidée),
    `HORAIRES` vérifiée ou ignorée.

    Risque : la même cause signalée deux fois (HORAIRES et JOURS) : compteurs gonflés et
    panneau Points à traiter redondant ; ou, `HORAIRES` ignorée, signalée nulle part alors que le
    moteur la compte (JOURS).
    """
    f, seance, n204 = une_seance()
    dimanche = placer(seance, DIMANCHE, "8h30", n204)
    anomalies = verifier(f.construire(), [dimanche], ["HORAIRES", "JOURS"])
    assert codes(anomalies) == ["HORAIRES", "JOURS"]
    assert ["aucun créneau" in a.message for a in anomalies] == [True, False]
    assert "le dimanche ne fait pas partie des jours travaillés" in anomalies[1].message
    # Un jour travaillé sans créneau (grille du mardi vidée) : HORAIRES seulement.
    f.creneaux = tuple(c for c in f.creneaux if c.jour != MARDI)
    mardi = placer(seance, MARDI, "8h30", n204)
    assert codes(verifier(f.construire(), [mardi], ["HORAIRES", "JOURS"])) == ["HORAIRES"]
    # `HORAIRES` ignorée : JOURS le dit, comme le moteur (`Regles.violations_de_jour`).
    (seule,) = verifier(f.construire(), [mardi], ["JOURS"])
    assert seule.code == "JOURS" and "la grille horaire n'a aucun créneau le mardi" in seule.message
