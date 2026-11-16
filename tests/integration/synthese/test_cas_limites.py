"""Une graine ou une année refusée ne vide jamais la base.

Une graine ou une année refusée par la commande ne doit jamais vider la base par la
remise à zéro. Les autres cas limites du générateur sont dans
`tests/unitaires/synthese/test_cas_limites.py`. Chaque test commence par
« Cas : ... Risque : ... » et porte le marqueur `cas_limite`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from edt import cli
from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.synthese.configuration import GRAINE_MAX

pytestmark = pytest.mark.cas_limite


@pytest.mark.parametrize(
    "arguments",
    [["--graine", "-1"], ["--graine", str(GRAINE_MAX + 1)], ["--annee", "9999"]],
)
def test_commande_refuse_sans_toucher_a_la_base(lanceur, base, arguments):
    """Cas : `python -m edt jeu --reset --oui` avec une graine ou une année refusée.
    Risque : la base est vidée par la remise à zéro, puis la génération échoue."""
    assert lanceur.invoke(cli.app, ["jeu", "--taille", "petit"]).exit_code == 0
    with session_base() as session:
        besoins = session.scalar(select(func.count()).select_from(m.Besoin))

    resultat = lanceur.invoke(cli.app, ["jeu", "--taille", "petit", "--reset", "--oui", *arguments])

    assert resultat.exit_code == 1, resultat.output
    assert "invalide" in resultat.output
    with session_base() as session:
        assert session.scalar(select(func.count()).select_from(m.Besoin)) == besoins
