"""Règles personnalisées, de bout en bout : copie avec le calcul, suivi et ligne de commande.

Calcul complet par le service de génération et commandes `edt generer` et `edt pre-verifier`
sur le jeu minimal (R001 nécessaire, R002 importante). Les tests unitaires des règles sont
dans `tests/unitaires/solveur/test_regles.py`.
"""

from __future__ import annotations

import dataclasses

from sqlalchemy import select
from tests.outils.solveur.en_base import regles_du_jeu as _regles_du_jeu
from tests.outils.solveur.fabrique import OPTIONS
from typer.testing import CliRunner

from edt import cli
from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.contraintes import Niveau
from edt.domaine.enumerations import EtatCalcul
from edt.services import generation

# --------------------------------------------------------------------------- #
# Services et ligne de commande (jeu minimal : R001 nécessaire, R002 importante)
# --------------------------------------------------------------------------- #


def test_les_regles_sont_copiees_avec_le_calcul_et_rendues_au_suivi(jeu_minimal):
    ids = _regles_du_jeu(jeu_minimal)
    assert {"R001", "R002"} <= set(ids)
    choisis = {ids["R001"]: Niveau.FACULTATIF}  # R002 ignorée pour ce calcul
    suivi = generation.attendre_calcul(
        generation.lancer_calcul(
            jeu_minimal.semestre_id, dataclasses.replace(OPTIONS, niveaux_regles=choisis)
        ),
        60,
    )
    assert suivi.etat is EtatCalcul.REUSSI, suivi.message
    assert suivi.niveaux_regles == choisis
    par_code = {regle["code"]: regle for regle in suivi.regles}
    assert par_code["R001"]["niveau"] == "facultatif"
    assert par_code["R002"]["niveau"] is None  # ignorée : copiée quand même, sans niveau
    assert par_code["R001"]["niveau_regle"] == "necessaire"  # le niveau propre de la règle
    assert par_code["R002"]["niveau_regle"] == "important"
    assert par_code["R001"]["type"] == "JOURS_TRAVAILLES"
    assert par_code["R001"]["portee"]["nature"] == "groupe"
    assert par_code["R001"]["parametres"]["jours"] == [
        "lundi",
        "mardi",
        "mercredi",
        "jeudi",
        "vendredi",
    ]
    assert par_code["R001"]["phrase"]
    with session_base() as session:
        calcul = session.get(m.Calcul, suivi.id)
        assert calcul.parametres["niveaux_regles"] == {str(ids["R001"]): "facultatif"}
        assert [r["code"] for r in calcul.parametres["regles"]] == sorted(par_code)

    # Sans choix : chaque règle active à son niveau, rien d'ignoré.
    suivi = generation.attendre_calcul(
        generation.lancer_calcul(jeu_minimal.semestre_id, OPTIONS), 60
    )
    assert suivi.etat is EtatCalcul.REUSSI, suivi.message
    assert suivi.niveaux_regles is None
    niveaux = {regle["code"]: regle["niveau"] for regle in suivi.regles}
    assert niveaux["R001"] == "necessaire" and niveaux["R002"] == "important"


def test_commande_generer_regle(jeu_minimal):
    lanceur = CliRunner()
    semestre = str(jeu_minimal.semestre_id)
    base = ["generer", "--semestre", semestre, "--sans-optimiser", "--temps-limite", "30"]
    resultat = lanceur.invoke(
        cli.app, [*base, "--regle", "R001=facultatif", "--sans-regle", "R002"]
    )
    assert resultat.exit_code == 0, resultat.output
    assert "Règles personnalisées : R001 : facultatif ; R002 : ignorée." in resultat.output
    ids = _regles_du_jeu(jeu_minimal)
    with session_base() as session:
        (calcul,) = session.scalars(select(m.Calcul)).all()
        assert calcul.parametres["niveaux_regles"] == {str(ids["R001"]): "facultatif"}
        niveaux = {r["code"]: r["niveau"] for r in calcul.parametres["regles"]}
        assert niveaux["R001"] == "facultatif" and niveaux["R002"] is None

    for arguments, attendu in (
        (["--regle", "R001"], "CODE=niveau"),
        (["--regle", "R999=important"], "inconnue ou inactive"),
        (["--regle", "R001=facultatif", "--sans-regle", "R001"], "à la fois ignorée"),
        (["--regle", "R001=souhaitable"], "Niveau inconnu"),
    ):
        refuse = lanceur.invoke(cli.app, [*base, *arguments])
        assert refuse.exit_code == 1, refuse.output
        assert attendu in refuse.output

    # Espaces et casse tolérés.
    resultat = lanceur.invoke(
        cli.app, [*base, "--regle", "r001 = Facultatif", "--sans-regle", " r002 "]
    )
    assert resultat.exit_code == 0, resultat.output
    assert "Règles personnalisées : R001 : facultatif ; R002 : ignorée." in resultat.output

    # Sans option, rien à dire sur les règles.
    resultat = lanceur.invoke(cli.app, base)
    assert resultat.exit_code == 0 and "Règles personnalisées : " not in resultat.output


def test_commande_pre_verifier_regle(jeu_minimal):
    lanceur = CliRunner()
    resultat = lanceur.invoke(
        cli.app,
        ["pre-verifier", "--semestre", str(jeu_minimal.semestre_id), "--sans-regle", "R001"],
    )
    assert resultat.exit_code == 0, resultat.output
    assert "Règles personnalisées : R001 : ignorée." in resultat.output
