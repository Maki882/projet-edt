"""Charge : chargement de l'instantané d'un établissement complet (test marqué `perf`).

Le jeu « grand » compte environ 600 besoins, 230 groupes et 75 fermetures par
semestre. Son instantané est rechargé à chaque affichage qui suit une
modification des données : il doit rester rapide. Avant l'accélération du
dépliage, ce chargement prenait 0,43 s sur un poste de développement ; il en
prend environ 0,06 s. La borne est large pour ne pas dépendre de la machine.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.services.instantane import charger_instantane
from edt.synthese.generateur import generer_jeu

SEUIL_S = 0.3
NB_MESURES = 3

pytestmark = pytest.mark.perf


def test_chargement_de_l_instantane_du_jeu_grand(base):
    with session_base() as session:
        generer_jeu(session, "grand", 0)
    with session_base() as session:
        semestre_id = session.scalar(select(m.Semestre.id).where(m.Semestre.code == "S1"))
        instantane = charger_instantane(session, semestre_id)  # premier chargement, à froid
    assert len(instantane.besoins) > 500 and len(instantane.jours_fermes) > 50

    durees = []
    for _ in range(NB_MESURES):
        with session_base() as session:
            debut = time.perf_counter()
            charger_instantane(session, semestre_id)
            durees.append(time.perf_counter() - debut)

    # La meilleure mesure : on borne le coût du calcul, pas les à-coups de la machine.
    assert min(durees) < SEUIL_S, f"instantané du jeu grand chargé en {min(durees):.3f} s"
