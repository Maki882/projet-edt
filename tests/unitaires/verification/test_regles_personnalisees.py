"""Un vérificateur par type de règle personnalisée.

Chaque type : une entorse signalée, la gravité selon le niveau de la règle, la
règle ignorée (absente de `niveaux_regles`) ; plus les réglages par personne
(pas de double signalement avec `JOURS`, `DEJEUNER`, `PAUSE`).
"""

from __future__ import annotations

import datetime as dt

import pytest
from tests.outils.verification.fabrique import (
    ISSY,
    LUNDI,
    MARDI,
    SAMEDI,
    VENDREDI,
    Fabrique,
    h,
    occurrence,
    placer,
)

from edt.domaine.contraintes import CodeContrainte, Niveau
from edt.domaine.enumerations import Gravite
from edt.domaine.regles import REGISTRES, ErreurSaisie, TypeRegle, types_non_couverts
from edt.verification import calculer_indicateurs, verifier, verifier_occurrences

REGLE = CodeContrainte.REGLE
EN_SEMAINE = ["lundi", "mardi", "mercredi", "jeudi", "vendredi"]


def resume(anomalies):
    return [(str(a.code), a.gravite, a.regle_code) for a in anomalies]


def regles_de(anomalies):
    return [a for a in anomalies if a.code is REGLE]


def promotion():
    """A1 (promotion) > A1-G1, A1-G2 ; B1 (promotion seule) ; salles N204 (NDC), L101 (Issy)."""
    f = Fabrique()
    n204, l101 = f.salle("N204"), f.salle("L101", campus=ISSY)
    a1 = f.groupe("A1", effectif=40)
    g1, g2 = f.groupe("A1-G1", parent=a1), f.groupe("A1-G2", parent=a1)
    b1 = f.groupe("B1")
    return f, (n204, l101), (a1, g1, g2, b1)


# --------------------------------------------------------------------------- #
# Registre
# --------------------------------------------------------------------------- #


def test_tous_les_types_ont_un_verificateur():
    assert types_non_couverts("verificateur") == frozenset()


def test_type_sans_verificateur_signale_et_jamais_bloquant(monkeypatch):
    # Garde-fou d'un type ajouté au catalogue sans vérificateur : simulé en retirant le sien.
    monkeypatch.delitem(REGISTRES["verificateur"], TypeRegle.MAX_PAR_JOUR)
    f, _, (a1, *_rest) = promotion()
    f.regle("MAX_PAR_JOUR", "groupe", a1, "necessaire", maximum=360)
    (anomalie,) = verifier(f.construire(), [])
    assert (anomalie.code, anomalie.gravite, anomalie.regle_code) == (
        REGLE,
        Gravite.AVERTISSEMENT,
        "R001",
    )
    assert "n'est pas encore vérifié automatiquement" in anomalie.message


# --------------------------------------------------------------------------- #
# Jours travaillés
# --------------------------------------------------------------------------- #


def test_regle_qui_ferme_le_samedi_remplace_la_permission_du_type():
    f, (n204, _), (a1, g1, _, _) = promotion()
    regle = f.regle("JOURS_TRAVAILLES", "groupe", a1, jours=EN_SEMAINE)
    tp = f.cours("TP Algorithmique", [g1], type_seance="TP", samedi_autorise=True)
    instantane = f.construire()
    anomalies = verifier(instantane, [placer(tp, SAMEDI, "8h30", n204)])
    assert resume(anomalies) == [("REGLE", Gravite.ERREUR, "R001")]  # pas de JOURS en plus
    (anomalie,) = anomalies
    assert anomalie.regle_id == regle.id
    assert anomalie.regle_phrase == "La promotion A1 ne travaille que du lundi au vendredi."
    assert anomalie.niveau is Niveau.NECESSAIRE
    assert anomalie.message == (
        "Règle R001 (La promotion A1 ne travaille que du lundi au vendredi) : la séance TP "
        "Algorithmique "
        "(A1-G1) le samedi de 8 h 30 à 10 h 30 a lieu un jour que la règle exclut."
    )
    assert anomalie.seances == ("1-1",) and anomalie.jour == SAMEDI
    # Règle ignorée pour cette vérification : la permission du type reprend sa place.
    assert verifier(instantane, [placer(tp, SAMEDI, "8h30", n204)], niveaux_regles={}) == []


def test_samedi_ouvert_pour_un_groupe_et_pas_pour_un_autre():
    f, (n204, l101), (_, g1, _, b1) = promotion()
    f.regle("JOURS_TRAVAILLES", "groupe", g1, "important", jours=[*EN_SEMAINE, "samedi"])
    td_g1 = f.cours("TD G1", [g1])
    td_b1 = f.cours("TD B1", [b1])
    instantane = f.construire()
    placements = [placer(td_g1, SAMEDI, "8h30", n204), placer(td_b1, SAMEDI, "8h30", l101)]
    anomalies = verifier(instantane, placements, ["JOURS", "REGLE"])
    assert [(str(a.code), a.seances) for a in anomalies] == [("JOURS", ("2-1",))]
    assert "le type de séance TD n'est pas autorisé le samedi" in anomalies[0].message
    # Règle ignorée : le standard ne permet plus le samedi à A1-G1 non plus.
    ignoree = verifier(instantane, placements, ["JOURS", "REGLE"], niveaux_regles={})
    assert [(str(a.code), a.seances) for a in ignoree] == [("JOURS", ("1-1",)), ("JOURS", ("2-1",))]


@pytest.mark.parametrize(
    ("niveau", "gravite"),
    [
        (Niveau.NECESSAIRE, Gravite.ERREUR),
        (Niveau.IMPORTANT, Gravite.AVERTISSEMENT),
        (Niveau.FACULTATIF, Gravite.INFORMATION),
    ],
)
def test_gravite_selon_le_niveau_retenu(niveau, gravite):
    f, (n204, _), (a1, g1, _, _) = promotion()
    regle = f.regle("JOURS_TRAVAILLES", "groupe", a1, jours=["lundi", "mardi"])
    td = f.cours("TD", [g1])
    instantane = f.construire()
    (anomalie,) = verifier(
        instantane, [placer(td, VENDREDI, "8h30", n204)], niveaux_regles={regle.id: niveau}
    )
    assert (anomalie.gravite, anomalie.niveau) == (gravite, niveau)
    if niveau is Niveau.IMPORTANT:
        assert anomalie.message.startswith("Entorse à une règle importante : Règle R001 (")


def test_niveau_par_defaut_de_la_regle_meme_en_dur():
    f, (n204, _), (a1, g1, _, _) = promotion()
    f.regle("JOURS_TRAVAILLES", "groupe", a1, "facultatif", jours=["lundi"])
    td = f.cours("TD", [g1])
    (anomalie,) = verifier(f.construire(), [placer(td, MARDI, "8h30", n204)])
    assert (anomalie.gravite, anomalie.niveau) == (Gravite.INFORMATION, Niveau.FACULTATIF)


def test_regle_non_verifiee_sans_le_code_regle():
    f, (n204, _), (a1, g1, _, _) = promotion()
    f.regle("JOURS_TRAVAILLES", "groupe", a1, jours=["lundi"])
    td = f.cours("TD", [g1])
    instantane = f.construire()
    assert verifier(instantane, [placer(td, MARDI, "8h30", n204)], ["SALLE_UNIQUE"]) == []
    sans_regle = {CodeContrainte.JOURS: Niveau.NECESSAIRE}
    assert verifier(instantane, [placer(td, MARDI, "8h30", n204)], niveaux=sans_regle) == []


def test_seance_imposee_information():
    f, (n204, _), (a1, g1, _, _) = promotion()
    f.regle("JOURS_TRAVAILLES", "groupe", a1, jours=["lundi"])
    dst = f.cours("DST", [g1], debut_impose=h("8h30"))
    (anomalie,) = verifier(f.construire(), [placer(dst, MARDI, "8h30", n204)])
    assert anomalie.gravite is Gravite.INFORMATION
    assert "Son créneau est imposé" in anomalie.message


def test_couche_datee_et_periode_de_la_regle():
    f, (n204, _), (a1, g1, _, _) = promotion()
    # Semaines 1 à 3 du semestre (lundi 7 septembre au dimanche 27 septembre 2026).
    f.regle(
        "JOURS_TRAVAILLES",
        "groupe",
        a1,
        jours=["lundi"],
        date_debut=dt.date(2026, 9, 7),
        date_fin=dt.date(2026, 9, 27),
    )
    td = f.cours("TD", [g1])
    instantane = f.construire()
    (anomalie,) = verifier(instantane, [placer(td, MARDI, "8h30", n204)])
    assert "(semaines 1 à 3)" in anomalie.message
    dans = occurrence(td, dt.date(2026, 9, 15), "8h30", n204, identifiant=1)
    hors = occurrence(td, dt.date(2026, 10, 6), "8h30", n204, identifiant=2)
    anomalies = verifier_occurrences(instantane, [dans, hors])
    assert [(a.regle_code, a.occurrences, a.date) for a in anomalies] == [
        ("R001", (1,), dt.date(2026, 9, 15))
    ]


def test_ouverture_datee_du_samedi_au_jour_pres_sur_les_seances_datees():
    # A1-G1 a aussi cours le samedi, du 7 au 27 septembre 2026 seulement.
    f, (n204, _), (_, g1, _, _) = promotion()
    f.regle(
        "JOURS_TRAVAILLES",
        "groupe",
        g1,
        "important",
        jours=[*EN_SEMAINE, "samedi"],
        date_debut=dt.date(2026, 9, 7),
        date_fin=dt.date(2026, 9, 27),
    )
    td = f.cours("TD", [g1])
    instantane = f.construire()
    dans = occurrence(td, dt.date(2026, 9, 19), "8h30", n204, identifiant=1)
    hors = occurrence(td, dt.date(2026, 10, 10), "8h30", n204, identifiant=2)
    assert verifier_occurrences(instantane, [dans], ["JOURS", "REGLE"]) == []
    anomalies = verifier_occurrences(instantane, [hors], ["JOURS", "REGLE"])
    assert [(str(a.code), a.occurrences, a.regle_id) for a in anomalies] == [("JOURS", (2,), None)]
    assert "le type de séance TD n'est pas autorisé le samedi" in anomalies[0].message


@pytest.mark.parametrize("type_regle", ["PAUSE_DEJEUNER", "PAUSE_MINIMALE"])
def test_reglage_de_personne_date_refuse(type_regle):
    f, _, (_, g1, _, _) = promotion()
    with pytest.raises(ErreurSaisie, match="un réglage de personne n'a qu'une valeur"):
        f.regle(type_regle, "groupe", g1, date_debut=dt.date(2026, 9, 7))


# --------------------------------------------------------------------------- #
# Horaires et créneaux interdits
# --------------------------------------------------------------------------- #


def test_plage_horaire():
    f, (n204, _), (a1, g1, _, _) = promotion()
    f.regle("PLAGE_HORAIRE", "groupe", a1, debut="10:00", jours=["lundi"])
    td = f.cours("TD", [g1])
    instantane = f.construire()
    (anomalie,) = verifier(instantane, [placer(td, LUNDI, "8h30", n204)], ["REGLE"])
    assert anomalie.message.endswith("le lundi de 8 h 30 à 10 h 30 commence avant 10 h 00.")
    assert verifier(instantane, [placer(td, MARDI, "8h30", n204)], ["REGLE"]) == []
    assert verifier(instantane, [placer(td, LUNDI, "10h45", n204)], ["REGLE"]) == []


def test_creneaux_interdits_par_type_de_seance():
    f, (n204, _), (_, g1, _, _) = promotion()
    cm = f.cours("CM", [g1], type_seance="CM")
    td = f.cours("TD", [g1])
    f.regle(
        "CRENEAUX_INTERDITS", "type_seance", f.type_seance("CM"), jours=["vendredi"], debut="12:45"
    )
    instantane = f.construire()
    anomalies = verifier(
        instantane,
        [placer(cm, VENDREDI, "13h45", n204), placer(td, VENDREDI, "16h00", n204)],
        ["REGLE"],
    )
    assert [a.seances for a in anomalies] == [("1-1",)]
    assert "recoupe la plage interdite de 12 h 45 à 18 h 00" in anomalies[0].message


# --------------------------------------------------------------------------- #
# Campus et salles
# --------------------------------------------------------------------------- #


def test_campus_impose_et_seance_sans_salle():
    f, (n204, l101), (a1, g1, _, _) = promotion()
    f.regle("CAMPUS", "groupe", a1, mode="imposer", campus=["ISSY"])
    td = f.cours("TD", [g1])
    instantane = f.construire()
    (anomalie,) = verifier(instantane, [placer(td, LUNDI, "8h30", n204)], ["REGLE"])
    assert "a lieu en salle N204, sur le campus Notre-Dame-des-Champs (campus imposé : Issy)" in (
        anomalie.message
    )
    assert anomalie.salles == (n204,)
    assert verifier(instantane, [placer(td, LUNDI, "8h30", l101)], ["REGLE"]) == []
    assert verifier(instantane, [placer(td, LUNDI, "8h30", None)], ["REGLE"]) == []


def test_salles_imposees_ou_interdites():
    f, (n204, l101), (_, g1, _, _) = promotion()
    n016 = f.salle("N016")
    tp = f.cours("TP", [g1], type_seance="TP", matiere="ELEC")
    f.regle("SALLES", "matiere", f.matieres["ELEC"], mode="imposer", salles=["N016"])
    f.regle(
        "SALLES",
        "type_seance",
        f.type_seance("TP"),
        "facultatif",
        mode="interdire",
        salles=["L101"],
    )
    instantane = f.construire()
    assert verifier(instantane, [placer(tp, LUNDI, "8h30", n016)], ["REGLE"]) == []
    (anomalie,) = verifier(instantane, [placer(tp, LUNDI, "8h30", n204)], ["REGLE"])
    assert anomalie.regle_code == "R001" and "hors des salles imposées (N016)" in anomalie.message
    anomalies = verifier(instantane, [placer(tp, LUNDI, "8h30", l101)], ["REGLE"])
    assert [(a.regle_code, a.gravite) for a in anomalies] == [
        ("R001", Gravite.ERREUR),
        ("R002", Gravite.INFORMATION),
    ]


# --------------------------------------------------------------------------- #
# Réglages par personne : déjeuner et pause
# --------------------------------------------------------------------------- #


def test_pause_dejeuner_libre_d_un_groupe_remplace_le_standard():
    # Standard : déjeuner fixe de 12 h 45 à 13 h 45. A1-G1 : libre, 1 h 15 entre 11 h 45 et 14 h 15.
    f, (n204, l101), (_, g1, g2, _) = promotion()
    f.regle("PAUSE_DEJEUNER", "groupe", g1, mode="libre", debut="11:45", fin="14:15", duree=75)
    td_g1 = f.cours("TD G1", [g1], duree=60)
    td_g2 = f.cours("TD G2", [g2], duree=60)
    instantane = f.construire()
    # A1-G1 a cours de 12 h 30 à 13 h 30 : 45 min libres avant, 45 après, pas 1 h 15.
    # A1-G2, au standard, empiète sur la plage réservée de 12 h 45 à 13 h 45.
    placements = [placer(td_g1, MARDI, "12h30", n204), placer(td_g2, MARDI, "12h30", l101)]
    anomalies = verifier(instantane, placements, ["DEJEUNER", "REGLE"])
    assert [(str(a.code), a.seances, a.niveau) for a in anomalies] == [
        ("DEJEUNER", ("2-1",), None),
        ("REGLE", ("1-1",), Niveau.IMPORTANT),
    ]
    regle = anomalies[1]
    assert regle.gravite is Gravite.AVERTISSEMENT
    assert regle.groupes == (g1,)
    assert regle.message == (
        "Entorse à une règle importante : Règle R001 (Le demi-groupe A1-G1 a une pause déjeuner libre "
        "d'au moins 1 h 15 entre 11 h 45 et 14 h 15) : le groupe A1-G1 n'a pas 1 h 15 de pause "
        "entre 11 h 45 et 14 h 15 le mardi (au plus 45 min libres d'affilée) : TD G1 (A1-G1) de "
        "12 h 30 à 13 h 30."
    )
    # Règle ignorée : A1-G1 retrouve le standard et empiète, lui aussi, sur la plage réservée.
    ignoree = verifier(instantane, placements, ["DEJEUNER", "REGLE"], niveaux_regles={})
    assert [(str(a.code), a.seances) for a in ignoree] == [
        ("DEJEUNER", ("1-1",)),
        ("DEJEUNER", ("2-1",)),
    ]


def test_pause_dejeuner_fixe_d_un_enseignant():
    f, (n204, _), (_, g1, _, b1) = promotion()
    petit = f.enseignant("M. Petit")
    f.regle("PAUSE_DEJEUNER", "enseignant", petit, mode="fixe", debut="12:00", fin="13:00")
    td = f.cours("TD", [g1], [petit], duree=60)
    instantane = f.construire()
    anomalies = verifier(instantane, [placer(td, MARDI, "11h30", n204)], ["DEJEUNER", "REGLE"])
    assert [(str(a.code), a.enseignants, a.groupes) for a in anomalies] == [("REGLE", (petit,), ())]
    assert ": M. Petit a cours pendant la pause déjeuner (de 12 h 00 à 13 h 00) le mardi" in (
        anomalies[0].message
    )


def test_pause_minimale_d_un_demi_groupe():
    f, (n204, l101), (_, g1, _, _) = promotion()
    f.regle("PAUSE_MINIMALE", "groupe", g1, "necessaire", duree=30)
    a = f.cours("Cours 1", [g1])
    b = f.cours("Cours 2", [g1])
    instantane = f.construire()
    placements = [placer(a, LUNDI, "8h30", n204), placer(b, LUNDI, "10h45", l101)]
    anomalies = verifier(instantane, placements, ["PAUSE", "REGLE"])
    assert resume(anomalies) == [("REGLE", Gravite.ERREUR, "R001")]
    assert anomalies[0].message.endswith(
        ": le groupe A1-G1 n'a que 15 min de pause le lundi entre Cours 1 (A1-G1), qui finit à "
        "10 h 30, et Cours 2 (A1-G1), qui commence à 10 h 45 ; il faut au moins 30 min."
    )
    # Contiguës : la règle relève l'entorse, pas PAUSE (pas de double signalement).
    contigues = [placer(a, LUNDI, "8h30", n204), placer(b, LUNDI, "10h30", l101, fin="12h30")]
    anomalies = verifier(instantane, contigues, ["PAUSE", "REGLE"])
    assert resume(anomalies) == [("REGLE", Gravite.ERREUR, "R001")]
    # Règle ignorée : le standard (15 min) vaut de nouveau.
    anomalies = verifier(instantane, contigues, ["PAUSE", "REGLE"], niveaux_regles={})
    assert [str(a.code) for a in anomalies] == ["PAUSE"]


def test_pause_dejeuner_couche_datee():
    f, (n204, _), (_, g1, _, _) = promotion()
    f.regle("PAUSE_DEJEUNER", "groupe", g1, mode="libre", debut="11:45", fin="14:15", duree=75)
    td = f.cours("TD", [g1], duree=60)
    mardi = dt.date(2026, 10, 6)
    anomalies = verifier_occurrences(
        f.construire(), [occurrence(td, mardi, "12h30", n204, identifiant=9)], ["REGLE"]
    )
    assert [(a.regle_code, a.occurrences, a.date) for a in anomalies] == [("R001", (9,), mardi)]


def test_indicateurs_entorses_par_regle():
    f, (n204, _), (a1, g1, _, _) = promotion()
    f.regle("JOURS_TRAVAILLES", "groupe", a1, "important", jours=["lundi"])
    td1, td2 = f.cours("TD 1", [g1]), f.cours("TD 2", [g1])
    instantane = f.construire()
    placements = [placer(td1, MARDI, "8h30", n204), placer(td2, MARDI, "10h45", n204)]
    indicateurs = calculer_indicateurs(instantane, placements)
    assert indicateurs.entorses_par_regle == ((1, 2),)
    assert indicateurs.nb_anomalies_bloquantes == 0
    assert indicateurs.vers_dict()["entorses_par_regle"] == {"1": 2}
