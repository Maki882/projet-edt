"""Cas limites des services des écrans de l'emploi du temps : bornes des identifiants.

Chaque test porte le marqueur `cas_limite` et sa docstring dit le cas et le
risque (« Cas : … Risque : … »). Les cas limites passant par les écrans sont
dans `tests/integration/web_edt/test_cas_limites.py`.
"""

from __future__ import annotations

import pytest
from tests.outils.web_edt.aides import ENORME

from edt.db import modeles as m
from edt.db.moteur import IDENTIFIANT_MAX
from edt.erreurs import ErreurIntrouvable
from edt.services.emploi_du_temps.identifiants import exiger_identifiant, obtenir


class _SessionEspion:
    """Session réduite à `get`, qui note chaque interrogation de la base (et n'en trouve aucune)."""

    def __init__(self) -> None:
        self.interrogations: list[object] = []

    def get(self, modele, ident):
        self.interrogations.append(ident)
        return None


@pytest.mark.cas_limite
def test_bornes_des_identifiants(session):
    """Cas : identifiant nul, négatif, au-delà de 2^63, booléen, texte, absent ou décimal reçu
    par les services de ces écrans (`exiger_identifiant`, `obtenir`), à côté de la borne
    2^63 - 1, acceptée.
    Risque : un identifiant qu'aucune ligne SQLite ne peut porter atteint la base
    (OverflowError, erreur 500) au lieu d'un « introuvable », ou `True` passe pour l'identifiant 1.
    """
    impossibles = (0, -1, IDENTIFIANT_MAX + 1, int(ENORME), True, "12", None, 1.0)
    espion = _SessionEspion()
    for impossible in impossibles:
        with pytest.raises(ErreurIntrouvable, match="Calcul introuvable"):
            exiger_identifiant(impossible, "Calcul introuvable.")
        assert obtenir(espion, m.Calcul, impossible) is None, impossible
    assert espion.interrogations == []  # rien n'a atteint la base

    assert exiger_identifiant(1, "Calcul introuvable.") == 1
    assert exiger_identifiant(IDENTIFIANT_MAX, "Calcul introuvable.") == IDENTIFIANT_MAX
    assert obtenir(espion, m.Calcul, IDENTIFIANT_MAX) is None
    assert espion.interrogations == [IDENTIFIANT_MAX]  # la borne, elle, est cherchée
    assert obtenir(session, m.Calcul, IDENTIFIANT_MAX) is None  # et SQLite l'accepte
