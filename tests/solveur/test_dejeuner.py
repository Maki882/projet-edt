"""Pause déjeuner sans horaires déterminés : le solveur ne réserve plus la plage de midi."""

from __future__ import annotations

from tests.solveur.fabrique import LUNDI, OPTIONS, Fabrique, H, verifier_solution

from edt.domaine.enumerations import DemiJournee
from edt.domaine.instantane import Creneau
from edt.solveur import StatutSolveur, resoudre

MIDI = (Creneau(LUNDI, 12 * H, 14 * H, DemiJournee.MATIN),)
"""Un seul créneau, de 12 h à 14 h : il recoupe la pause déjeuner par défaut (12 h 45-13 h 45)."""


def probleme(**parametres):
    """Une séance de 2 h qui ne peut avoir lieu que sur le créneau de midi."""
    f = Fabrique(creneaux=MIDI, **parametres)
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()])
    return f.instantane()


def test_sans_horaires_determines_une_seance_peut_chevaucher_midi():
    avec = resoudre(probleme(), OPTIONS)
    assert avec.statut is StatutSolveur.INFAISABLE and "pause déjeuner" in avec.message

    instantane = probleme(dejeuner_horaires_determines=False)
    resultat = resoudre(instantane, OPTIONS)
    assert resultat.contraintes_desactivees == frozenset()  # DEJEUNER reste active
    places = verifier_solution(instantane, resultat)  # l'oracle ne relève rien non plus
    assert places["1-1"].debut == 12 * H
