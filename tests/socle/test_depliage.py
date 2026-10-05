"""Dépliage des besoins en séances hebdomadaires (les trois modes de volume)."""

from __future__ import annotations

import datetime as dt

from edt.domaine.calendrier import semaines_entre
from edt.domaine.contraintes import CodeContrainte
from edt.domaine.depliage import deplier, repartition_reguliere
from edt.domaine.enumerations import ModeVolume, NatureJourFerme
from edt.domaine.instantane import Besoin, JourFerme

# Quinze semaines, du lundi 7 septembre au vendredi 18 décembre 2026.
SEMAINES = semaines_entre(dt.date(2026, 9, 7), dt.date(2026, 12, 18))
TOUSSAINT = JourFerme(
    dt.date(2026, 10, 26), dt.date(2026, 11, 1), "Toussaint", NatureJourFerme.VACANCES
)  # semaine 8, entièrement fermée
ARMISTICE = JourFerme(
    dt.date(2026, 11, 11), dt.date(2026, 11, 11), "Armistice", NatureJourFerme.FERIE
)  # mercredi de la semaine 10


def besoin(identifiant: int = 1, **champs) -> Besoin:
    valeurs = dict(
        id=identifiant,
        libelle=f"Besoin {identifiant}",
        matiere_id=1,
        matiere_code="IM.1101",
        type_seance_id=1,
        type_seance_code="TD",
        samedi_autorise=False,
        duree=120,
        groupes=frozenset({10}),
        enseignants=frozenset({1}),
        effectif=30,
        accessibilite_requise=False,
    )
    valeurs.update(champs)
    return Besoin(**valeurs)


def test_par_semaine_une_seance_par_rang_sur_toutes_les_semaines_ouvertes():
    resultat = deplier([besoin(nombre=2)], SEMAINES, [TOUSSAINT, ARMISTICE])

    assert [s.id for s in resultat.seances] == ["1-1", "1-2"]
    attendues = frozenset(range(1, 16)) - {8}  # la semaine entièrement fermée disparaît
    assert all(s.semaines == attendues for s in resultat.seances)
    assert 10 in attendues  # un seul jour férié ne ferme pas la semaine
    assert resultat.problemes == ()


def test_par_semaine_limite_a_la_fenetre_du_besoin():
    fenetre = besoin(date_debut=dt.date(2026, 10, 5), date_fin=dt.date(2026, 10, 23))
    resultat = deplier([fenetre], SEMAINES, [])
    assert resultat.seances[0].semaines == frozenset({5, 6, 7})


def test_total_reparti_regulierement():
    assert repartition_reguliere(10, 15) == [1, 1, 0] * 5
    assert repartition_reguliere(20, 15) == [2, 1, 1] * 5

    total = besoin(mode_volume=ModeVolume.TOTAL, nombre=5, max_par_semaine=1)
    resultat = deplier([total], SEMAINES, [])

    (seance,) = resultat.seances
    assert seance.semaines == frozenset({1, 4, 7, 10, 13})  # une semaine sur trois, dès la première
    assert resultat.problemes == ()


def test_total_avec_plusieurs_seances_par_semaine():
    total = besoin(mode_volume=ModeVolume.TOTAL, nombre=20, max_par_semaine=2)
    resultat = deplier([total], SEMAINES, [])

    rang_1, rang_2 = resultat.seances
    assert rang_1.semaines == frozenset(range(1, 16))
    assert rang_2.semaines == frozenset({1, 4, 7, 10, 13})
    assert sum(len(s.semaines) for s in resultat.seances) == 20


def test_total_impossible_est_signale_sans_etre_corrige():
    trop = besoin(mode_volume=ModeVolume.TOTAL, nombre=20, max_par_semaine=1)
    resultat = deplier([trop], SEMAINES, [])

    (probleme,) = resultat.problemes
    assert probleme.besoin_id == 1
    assert probleme.code is CodeContrainte.VOLUME
    assert "20 séances demandées" in probleme.message
    # Ce qui tient est déplié : 15 semaines à une séance.
    assert sum(len(s.semaines) for s in resultat.seances) == 15


def test_ponctuel_actif_la_seule_semaine_de_sa_date():
    dst = besoin(
        mode_volume=ModeVolume.PONCTUEL,
        date_ponctuelle=dt.date(2026, 11, 19),
        debut_impose=825,
    )
    resultat = deplier([dst], SEMAINES, [TOUSSAINT])

    (seance,) = resultat.seances
    assert seance.semaines == frozenset({11})
    assert seance.jour_impose == 3  # un jeudi
    assert seance.debut_impose == 825
    assert resultat.problemes == ()


def test_ponctuel_hors_semestre_ou_un_jour_ferme_est_signale():
    hors_semestre = besoin(1, mode_volume=ModeVolume.PONCTUEL, date_ponctuelle=dt.date(2027, 3, 1))
    jour_ferie = besoin(2, mode_volume=ModeVolume.PONCTUEL, date_ponctuelle=dt.date(2026, 11, 11))
    resultat = deplier([hors_semestre, jour_ferie], SEMAINES, [ARMISTICE])

    codes = {p.besoin_id: p.code for p in resultat.problemes}
    assert codes == {1: CodeContrainte.VOLUME, 2: CodeContrainte.JOUR_FERME}
    assert [s.id for s in resultat.seances] == ["2-1"]  # la séance hors semestre n'existe pas


def test_semaine_d_examens_ferme_les_cours_mais_pas_les_seances_ponctuelles():
    partiels = JourFerme(
        dt.date(2026, 12, 7),
        dt.date(2026, 12, 11),
        "Partiels",
        NatureJourFerme.EXAMENS,
        groupes=frozenset({10, 11}),
    )
    cours = besoin(1)
    autre_groupe = besoin(2, groupes=frozenset({20}))
    examen = besoin(3, mode_volume=ModeVolume.PONCTUEL, date_ponctuelle=dt.date(2026, 12, 8))
    resultat = deplier([cours, autre_groupe, examen], SEMAINES, [partiels])

    par_id = {s.id: s for s in resultat.seances}
    assert 14 not in par_id["1-1"].semaines  # le groupe en examens n'a pas cours
    assert 14 in par_id["2-1"].semaines  # la fermeture ne vise pas l'autre groupe
    assert par_id["3-1"].semaines == frozenset({14})  # l'examen, lui, a bien lieu
    assert resultat.problemes == ()
