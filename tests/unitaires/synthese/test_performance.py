"""Durée de génération du jeu « grand » (test de charge, exclu de la CI courante)."""

from __future__ import annotations

import time

import pytest
from tests.outils.synthese.aides import generer

SEUIL_S = 60


@pytest.mark.cas_limite
@pytest.mark.perf
def test_jeu_grand_en_moins_d_une_minute(tmp_path):
    """Cas : chargement de l'établissement grand (le plus long) depuis l'écran ou la commande.

    Risque : génération qui dérive au-delà d'une minute et bloque l'écran de chargement.
    """
    debut = time.perf_counter()
    jeu = generer(tmp_path / "grand", "grand", reference=True)
    duree = time.perf_counter() - debut
    assert duree < SEUIL_S, f"génération du jeu grand en {duree:.1f} s"
    assert jeu.rapport.besoins_ecartes == ()
