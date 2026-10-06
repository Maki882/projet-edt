"""Paramètre « pause déjeuner à horaires déterminés » : validation et lecture d'une ancienne base."""

from __future__ import annotations

import logging
import re

import pytest

from edt.db.modeles import Parametre
from edt.domaine.parametres import Parametres
from edt.erreurs import ErreurDonneesInvalides
from edt.services.parametres import lire_parametres


def test_horaires_determines_exigent_une_plage_non_vide_dans_l_ouverture():
    parametres = Parametres()
    assert parametres.dejeuner_horaires_determines is True
    assert parametres.plage_dejeuner == (12 * 60 + 45, 13 * 60 + 45)
    for changements, message in (
        ({"dejeuner_fin_min": 12 * 60}, "La fin du déjeuner doit venir après son début (12 h 45)."),
        ({"dejeuner_fin_min": 12 * 60 + 45}, "La fin du déjeuner doit venir après son début"),
        ({"dejeuner_debut_min": 8 * 60}, "ne peut pas commencer avant l'ouverture (8 h 30)."),
        ({"dejeuner_fin_min": 19 * 60}, "ne peut pas finir après la fermeture (18 h 00)."),
    ):
        with pytest.raises(ErreurDonneesInvalides, match=re.escape(message)):
            parametres.avec(**changements)


def test_sans_horaires_determines_les_heures_sont_gardees_mais_ignorees():
    parametres = Parametres().avec(
        dejeuner_horaires_determines=False, dejeuner_debut_min=13 * 60, dejeuner_fin_min=12 * 60
    )
    assert parametres.plage_dejeuner is None
    assert (parametres.dejeuner_debut_min, parametres.dejeuner_fin_min) == (780, 720)


def test_ancienne_plage_vide_lue_sans_horaires_determines(session, caplog):
    """Base d'avant le paramètre, où une plage vide voulait dire « pas de déjeuner » : lisible."""
    session.get(Parametre, "dejeuner_fin_min").valeur = 12 * 60 + 45
    session.delete(session.get(Parametre, "dejeuner_horaires_determines"))
    session.flush()
    with caplog.at_level(logging.WARNING, logger="edt.services.parametres"):
        parametres = lire_parametres(session)
    assert parametres.plage_dejeuner is None
    assert "lue comme « pas d'horaires déterminés »" in caplog.text
