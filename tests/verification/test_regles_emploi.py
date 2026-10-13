"""Règles personnalisées du temps B : emploi du temps par jour ou par semaine, relations.

Pour chaque type : un cas satisfait, un cas violé (message, séances, unité),
la gravité selon le niveau retenu, la règle ignorée, et les séances datées.
"""

from __future__ import annotations

import datetime as dt

import pytest

from edt.domaine.contraintes import CodeContrainte, Niveau
from edt.domaine.enumerations import Gravite
from edt.verification import verifier, verifier_occurrences

from .fabrique import ISSY, JEUDI, LUNDI, MARDI, MERCREDI, VENDREDI, Fabrique, occurrence, placer

REGLE = CodeContrainte.REGLE
MARDI_6_OCT = dt.date(2026, 10, 6)


def regles(anomalies):
    return [a for a in anomalies if a.code is REGLE]


def base():
    """A1 (promotion) > A1-G1, A1-G2 ; salles N204, N205 (NDC), L101 (Issy) ; M. Petit."""
    f = Fabrique()
    salles = (f.salle("N204"), f.salle("N205"), f.salle("L101", campus=ISSY))
    a1 = f.groupe("A1", effectif=40)
    g1, g2 = f.groupe("A1-G1", parent=a1), f.groupe("A1-G2", parent=a1)
    petit = f.enseignant("M. Petit")
    return f, salles, (a1, g1, g2), petit


# --------------------------------------------------------------------------- #
# Un scénario violé par type (règle R001), pour les tests communs
# --------------------------------------------------------------------------- #


def scenario_max_par_jour():
    f, (n204, *_), (_, g1, _), _ = base()
    f.regle("MAX_PAR_JOUR", "groupe", g1, maximum=240)
    tds = [f.cours(f"TD {i}", [g1]) for i in range(3)]
    return f, [
        placer(td, MARDI, debut, n204)
        for td, debut in zip(tds, ("8h30", "10h45", "13h45"), strict=True)
    ]


def scenario_campus_par_jour():
    f, (n204, _, l101), (_, g1, _), _ = base()
    f.regle("CAMPUS_PAR_JOUR", "groupe", g1)
    a, b = f.cours("TD 1", [g1]), f.cours("TD 2", [g1])
    return f, [placer(a, MARDI, "8h30", n204), placer(b, MARDI, "13h45", l101)]


def scenario_matieres_par_jour():
    f, (n204, *_), (_, g1, _), _ = base()
    f.regle("MATIERES_PAR_JOUR", "groupe", g1, maximum=2)
    cours = [f.cours(nom, [g1], matiere=nom) for nom in ("MATH", "PHYS", "CHIM")]
    debuts = ("8h30", "10h45", "13h45")
    return f, [placer(c, MARDI, d, n204) for c, d in zip(cours, debuts, strict=True)]


def scenario_journees_libres():
    f, (n204, *_), (_, g1, _), petit = base()
    f.regle("JOURNEES_LIBRES", "enseignant", petit, unite="journee", nombre=2)
    cours = [f.cours(f"TD {j}", [g1], [petit]) for j in range(4)]
    return f, [
        placer(c, jour, "8h30", n204)
        for c, jour in zip(cours, (LUNDI, MARDI, MERCREDI, JEUDI), strict=True)
    ]


def scenario_sans_trou():
    f, (n204, *_), (_, g1, _), _ = base()
    f.regle("SANS_TROU", "groupe", g1)
    a, b = f.cours("TD 1", [g1]), f.cours("TD 2", [g1])
    return f, [placer(a, MARDI, "8h30", n204), placer(b, MARDI, "16h00", n204)]


def scenario_ordre_semaine():
    f, (n204, *_), (a1, g1, _), _ = base()
    cm = f.cours("Mathématiques CM", [a1], type_seance="CM", matiere="MATH")
    td = f.cours("Mathématiques TD", [g1], type_seance="TD", matiere="MATH")
    f.regle("ORDRE_SEMAINE", "groupe", a1)
    return f, [placer(cm, MERCREDI, "8h30", n204), placer(td, MARDI, "8h30", n204)]


def scenario_repartition_matiere():
    f, (n204, *_), (_, g1, _), _ = base()
    a = f.cours("Mathématiques 1", [g1], matiere="MATH")
    b = f.cours("Mathématiques 2", [g1], matiere="MATH")
    f.regle("REPARTITION_MATIERE", "groupe", g1, mode="jours_differents")
    return f, [placer(a, MARDI, "8h30", n204), placer(b, MARDI, "13h45", n204)]


SCENARIOS = {
    "MAX_PAR_JOUR": scenario_max_par_jour,
    "CAMPUS_PAR_JOUR": scenario_campus_par_jour,
    "MATIERES_PAR_JOUR": scenario_matieres_par_jour,
    "JOURNEES_LIBRES": scenario_journees_libres,
    "SANS_TROU": scenario_sans_trou,
    "ORDRE_SEMAINE": scenario_ordre_semaine,
    "REPARTITION_MATIERE": scenario_repartition_matiere,
}


@pytest.mark.parametrize("type_regle", SCENARIOS)
def test_entorse_au_niveau_de_la_regle_puis_ignoree(type_regle):
    f, placements = SCENARIOS[type_regle]()
    instantane = f.construire()
    (regle,) = instantane.regles
    trouvees = regles(verifier(instantane, placements, ["REGLE"]))
    assert trouvees and all(a.regle_id == regle.id for a in trouvees)
    attendue = {
        Niveau.NECESSAIRE: Gravite.ERREUR,
        Niveau.IMPORTANT: Gravite.AVERTISSEMENT,
        Niveau.FACULTATIF: Gravite.INFORMATION,
    }[regle.niveau]
    assert {a.gravite for a in trouvees} == {attendue}
    assert all(a.message.startswith(("Règle R001 (", "Entorse à une règle")) for a in trouvees)

    souple = verifier(instantane, placements, ["REGLE"], niveaux_regles={regle.id: "facultatif"})
    assert {(a.gravite, a.niveau) for a in souple} == {(Gravite.INFORMATION, Niveau.FACULTATIF)}
    dure = verifier(instantane, placements, ["REGLE"], niveaux_regles={regle.id: "necessaire"})
    assert {a.gravite for a in dure} == {Gravite.ERREUR}
    assert verifier(instantane, placements, ["REGLE"], niveaux_regles={}) == []


@pytest.mark.parametrize("type_regle", SCENARIOS)
def test_entorse_sur_les_seances_datees(type_regle):
    f, placements = SCENARIOS[type_regle]()
    instantane = f.construire()
    lundi = dt.date(2026, 10, 5)
    occurrences = [
        occurrence(
            instantane.seance(p.seance_id),
            lundi + dt.timedelta(days=p.jour),
            p.debut,
            p.salle_id,
            fin=p.fin,
            identifiant=i,
        )
        for i, p in enumerate(placements, start=1)
    ]
    trouvees = regles(verifier_occurrences(instantane, occurrences, ["REGLE"]))
    assert trouvees and all(a.regle_code == "R001" and a.date is not None for a in trouvees)
    assert all(a.jour is None for a in trouvees)
    # Une autre semaine réelle pour la moitié des séances : plus rien ne les réunit.
    decalees = [
        o if i % 2 else o.__class__(**{**o.__dict__, "date": o.date + dt.timedelta(weeks=1)})
        for i, o in enumerate(occurrences)
    ]
    if type_regle not in ("JOURNEES_LIBRES",):
        assert regles(verifier_occurrences(instantane, decalees, ["REGLE"])) == []


# --------------------------------------------------------------------------- #
# Messages et cas satisfaits, type par type
# --------------------------------------------------------------------------- #


def test_max_par_jour():
    f, placements = scenario_max_par_jour()
    instantane = f.construire()
    (anomalie,) = verifier(instantane, placements, ["REGLE"])
    assert anomalie.message == (
        "Entorse à une règle importante : Règle R001 (Le demi-groupe A1-G1 n'a pas plus de 4 h "
        "de cours par jour) : le groupe A1-G1 a plus de 4 h le mardi (6 h de cours les "
        "semaines 1 à 15) : TD 0 (A1-G1) de 8 h 30 à 10 h 30, TD 1 (A1-G1) de 10 h 45 à "
        "12 h 45 et TD 2 (A1-G1) de 13 h 45 à 15 h 45."
    )
    assert anomalie.jour == MARDI and anomalie.groupes == (2,)
    assert len(anomalie.seances) == 3
    assert verifier(instantane, placements[:2], ["REGLE"]) == []


def test_max_par_jour_en_seances_et_personnes_reunies():
    f, (n204, n205, _), (a1, g1, g2), petit = base()
    f.regle("MAX_PAR_JOUR", "groupe", a1, unite="seances", maximum=2)
    cms = [f.cours(f"CM {i}", [a1], [petit], type_seance="CM") for i in range(3)]
    placements = [
        placer(cm, LUNDI, d, n204) for cm, d in zip(cms, ("8h30", "10h45", "13h45"), strict=True)
    ]
    (anomalie,) = verifier(f.construire(), placements, ["REGLE"])
    # A1-G1 et A1-G2 sont dans la même situation : une anomalie, qui nomme la promotion.
    assert ": le groupe A1 a plus de 2 séances le lundi (3 séances les semaines 1 à 15)" in (
        anomalie.message
    )
    assert anomalie.groupes == (a1, g1, g2)


def test_max_par_jour_semaines_disjointes():
    f, (n204, *_), (_, g1, _), _ = base()
    f.regle("MAX_PAR_JOUR", "groupe", g1, maximum=240)
    a = f.cours("Court 1", [g1], semaines=range(1, 8))
    b = f.cours("Court 2", [g1], semaines=range(8, 16))
    c = f.cours("TD", [g1])
    placements = [
        placer(a, MARDI, "8h30", n204),
        placer(b, MARDI, "10h45", n204),
        placer(c, MARDI, "13h45", n204),
    ]
    assert verifier(f.construire(), placements, ["REGLE"]) == []


def test_campus_par_jour():
    f, placements = scenario_campus_par_jour()
    instantane = f.construire()
    (anomalie,) = verifier(instantane, placements, ["REGLE"])
    assert (
        ": le groupe A1-G1 a cours sur plusieurs campus le mardi (Issy et Notre-Dame-des-Champs"
        in (anomalie.message)
    )
    n205 = 2
    meme_campus = [
        placements[0],
        placements[1].__class__(**{**placements[1].__dict__, "salle_id": n205}),
    ]
    assert verifier(instantane, meme_campus, ["REGLE"]) == []


def test_matieres_par_jour():
    f, placements = scenario_matieres_par_jour()
    instantane = f.construire()
    (anomalie,) = verifier(instantane, placements, ["REGLE"])
    assert "a plus de 2 matières différentes le mardi (3 matières les semaines 1 à 15)" in (
        anomalie.message
    )
    assert verifier(instantane, placements[:2], ["REGLE"]) == []


def test_journees_libres_en_journees():
    f, placements = scenario_journees_libres()
    instantane = f.construire()
    (anomalie,) = verifier(instantane, placements, ["REGLE"])
    assert anomalie.message.endswith(
        ": M. Petit n'a pas 2 journées libres par semaine (1 libre les semaines 1 à 15)."
    )
    assert anomalie.enseignants == (1,) and len(anomalie.seances) == 4 and anomalie.jour is None
    assert verifier(instantane, placements[:3], ["REGLE"]) == []


def test_journees_libres_en_demi_journees_parmi():
    f, (n204, *_), (_, g1, _), _ = base()
    f.regle(
        "JOURNEES_LIBRES",
        "groupe",
        g1,
        unite="demi_journee",
        parmi=["lundi matin", "vendredi après-midi"],
    )
    a, b = f.cours("TD 1", [g1]), f.cours("TD 2", [g1])
    instantane = f.construire()
    pris = [placer(a, LUNDI, "8h30", n204), placer(b, VENDREDI, "13h45", n204)]
    (anomalie,) = verifier(instantane, pris, ["REGLE"])
    assert (
        "n'a pas 1 demi-journée libre par semaine (0 libre les semaines 1 à 15)" in anomalie.message
    )
    libre = [placer(a, LUNDI, "8h30", n204), placer(b, VENDREDI, "8h30", n204)]
    assert verifier(instantane, libre, ["REGLE"]) == []


def test_sans_trou():
    f, placements = scenario_sans_trou()
    instantane = f.construire()
    (anomalie,) = verifier(instantane, placements, ["REGLE"])
    assert anomalie.message.endswith(
        ": le groupe A1-G1 a des créneaux de trou dans la semaine (2 créneaux les semaines 1 à 15)."
    )
    contigues = [
        placements[0],
        placements[1].__class__(**{**placements[1].__dict__, "debut": 645, "fin": 765}),
    ]
    assert verifier(instantane, contigues, ["REGLE"]) == []


def test_ordre_semaine():
    f, placements = scenario_ordre_semaine()
    instantane = f.construire()
    (anomalie,) = verifier(instantane, placements, ["REGLE"])
    assert anomalie.message.endswith(
        ": le groupe A1-G1 a Mathématiques TD (A1-G1) (le mardi de 8 h 30 à 10 h 30) avant "
        "Mathématiques CM (A1) (le mercredi de 8 h 30 à 10 h 30)."
    )
    assert anomalie.seances == ("1-1", "2-1")
    dans_l_ordre = [
        placements[0].__class__(**{**placements[0].__dict__, "jour": LUNDI}),
        placements[1],
    ]
    assert verifier(instantane, dans_l_ordre, ["REGLE"]) == []


def test_ordre_semaine_semaines_disjointes():
    f, (n204, *_), (a1, g1, _), _ = base()
    cm = f.cours("Maths CM", [a1], type_seance="CM", matiere="MATH", semaines=range(8, 16))
    td = f.cours("Maths TD", [g1], type_seance="TD", matiere="MATH", semaines=range(1, 8))
    f.regle("ORDRE_SEMAINE", "groupe", a1)
    placements = [placer(cm, MERCREDI, "8h30", n204), placer(td, MARDI, "8h30", n204)]
    assert verifier(f.construire(), placements, ["REGLE"]) == []


def test_repartition_matiere_jours_differents_et_meme_jour():
    f, placements = scenario_repartition_matiere()
    instantane = f.construire()
    (anomalie,) = verifier(instantane, placements, ["REGLE"])
    assert ": le groupe A1-G1 a 2 séances de la matière MATH le mardi : " in anomalie.message
    assert anomalie.jour == MARDI
    etalees = [placements[0], placements[1].__class__(**{**placements[1].__dict__, "jour": JEUDI})]
    assert verifier(instantane, etalees, ["REGLE"]) == []

    f, (n204, *_), (_, g1, _), _ = base()
    a = f.cours("Maths 1", [g1], matiere="MATH")
    b = f.cours("Maths 2", [g1], matiere="MATH")
    f.regle("REPARTITION_MATIERE", "groupe", g1, mode="meme_jour")
    instantane = f.construire()
    (anomalie,) = verifier(
        instantane, [placer(a, MARDI, "8h30", n204), placer(b, JEUDI, "8h30", n204)], ["REGLE"]
    )
    assert "les séances de la matière MATH sur 2 jours (mardi et jeudi) au lieu d'un seul" in (
        anomalie.message
    )
    assert (
        verifier(
            instantane, [placer(a, MARDI, "8h30", n204), placer(b, MARDI, "13h45", n204)], ["REGLE"]
        )
        == []
    )


def test_journees_libres_datees_par_semaine_reelle():
    f, placements = scenario_journees_libres()
    instantane = f.construire()
    lundi = dt.date(2026, 10, 5)
    occurrences = [
        occurrence(
            instantane.seance(p.seance_id), lundi + dt.timedelta(days=p.jour), p.debut, p.salle_id
        )
        for p in placements
    ]
    (anomalie,) = verifier_occurrences(instantane, occurrences, ["REGLE"])
    assert anomalie.date == lundi
    assert "(1 libre, semaine du lundi 5 octobre 2026)" in anomalie.message
