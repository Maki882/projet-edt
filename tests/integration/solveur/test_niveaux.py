"""Niveaux de règles, de bout en bout : enregistrement avec le calcul, suivi et ligne de commande.

Calculs complets par le service de génération (`lancer_calcul`, `attendre_calcul`) et
commandes `edt pre-verifier` et `edt generer` sur le jeu minimal (base SQLite temporaire).
Les tests unitaires des niveaux sont dans `tests/unitaires/solveur/test_niveaux.py`.
"""

from __future__ import annotations

import dataclasses

import pytest
from sqlalchemy import select
from tests.outils.solveur.aides import niveaux_avec
from tests.outils.solveur.fabrique import NIVEAUX_DURS, OPTIONS
from typer.testing import CliRunner

from edt import cli
from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.contraintes import CodeContrainte, niveaux_depuis_desactivees, normaliser_niveaux
from edt.domaine.enumerations import CibleIndisponibilite, EtatCalcul
from edt.services import generation
from edt.solveur import OptionsSolveur

C = CodeContrainte
DELAI = 60


def _generer(semestre_id: int, reglages: OptionsSolveur) -> generation.SuiviCalcul:
    return generation.attendre_calcul(generation.lancer_calcul(semestre_id, reglages), DELAI)


# --------------------------------------------------------------------------- #
# Enregistrement et suivi
# --------------------------------------------------------------------------- #


def test_les_niveaux_sont_enregistres_avec_le_calcul_et_rendus_au_suivi(jeu_minimal):
    choisis = normaliser_niveaux({"PAUSE": "facultatif"})
    del choisis[C.EQUIPEMENT]
    suivi = _generer(jeu_minimal.semestre_id, dataclasses.replace(OPTIONS, niveaux=choisis))
    assert suivi.etat is EtatCalcul.REUSSI, suivi.message
    assert suivi.niveaux == choisis
    assert suivi.contraintes_desactivees == ("EQUIPEMENT",)
    with session_base() as session:
        calcul = session.get(m.Calcul, suivi.id)
        assert calcul.parametres["niveaux"]["PAUSE"] == "facultatif"
        assert "EQUIPEMENT" not in calcul.parametres["niveaux"]
        assert calcul.contraintes_desactivees == ["EQUIPEMENT"]


def test_un_calcul_d_avant_les_niveaux_est_lu_avec_ses_contraintes_desactivees(jeu_minimal):
    suivi = _generer(
        jeu_minimal.semestre_id, dataclasses.replace(OPTIONS, contraintes_desactivees={"PAUSE"})
    )
    with session_base() as session:
        calcul = session.get(m.Calcul, suivi.id)
        parametres = dict(calcul.parametres)
        del parametres["niveaux"]  # comme un calcul enregistré avant les niveaux
        calcul.parametres = parametres
    relu = generation.etat_calcul(suivi.id)
    assert relu.niveaux == niveaux_depuis_desactivees({"PAUSE"})
    assert C.PAUSE not in relu.niveaux and relu.contraintes_desactivees == ("PAUSE",)


# --------------------------------------------------------------------------- #
# Ligne de commande
# --------------------------------------------------------------------------- #


def test_commande_pre_verifier_niveau(jeu_minimal):
    lanceur = CliRunner()
    semestre = str(jeu_minimal.semestre_id)
    resultat = lanceur.invoke(
        cli.app,
        [
            "pre-verifier",
            "--semestre",
            semestre,
            "--niveau",
            "PAUSE=facultatif",
            "-d",
            "EQUIPEMENT",
        ],
    )
    assert resultat.exit_code == 0, resultat.output
    assert "Pause entre deux séances : facultatif" in resultat.output
    assert "ignorée" in resultat.output and "Pré-vérifications du semestre" in resultat.output
    refuse = lanceur.invoke(
        cli.app, ["pre-verifier", "--semestre", semestre, "--niveau", "PAUSE=souhaitable"]
    )
    assert refuse.exit_code != 0 and "Niveau inconnu" in refuse.output


def test_commande_generer_niveau(jeu_minimal):
    lanceur = CliRunner()
    semestre = str(jeu_minimal.semestre_id)
    resultat = lanceur.invoke(
        cli.app,
        ["generer", "--semestre", semestre, "--sans-optimiser", "--temps-limite", "30"]
        + [
            "--niveau",
            "PAUSE=facultatif",
            "--niveau",
            "MAX_ENSEIGNANT=necessaire",
            "-d",
            "EQUIPEMENT",
        ],
    )
    assert resultat.exit_code == 0, resultat.output
    assert "Règles : " in resultat.output
    assert "Pause entre deux séances : facultatif" in resultat.output
    assert "ignorée" in resultat.output
    assert "Maxima horaires des enseignants : nécessaire" in resultat.output
    with session_base() as session:
        (calcul,) = session.scalars(select(m.Calcul)).all()
        assert calcul.parametres["niveaux"]["PAUSE"] == "facultatif"
        assert calcul.parametres["niveaux"]["MAX_ENSEIGNANT"] == "necessaire"
        assert "EQUIPEMENT" not in calcul.parametres["niveaux"]

    # Sans option : rien de particulier à dire sur les règles.
    resultat = lanceur.invoke(
        cli.app, ["generer", "--semestre", semestre, "--sans-optimiser", "--temps-limite", "30"]
    )
    assert resultat.exit_code == 0 and "Règles : " not in resultat.output


@pytest.mark.parametrize(
    ("arguments", "attendu"),
    [
        (["--niveau", "PAUSE"], "CODE=niveau"),
        (["--niveau", "PAUSE=souhaitable"], "Niveau inconnu"),
        (["--niveau", "SALLE_UNIQUE=important"], "définit le problème"),
        (["--niveau", "PAUSE=facultatif", "-d", "PAUSE"], "à la fois ignorée"),
        (["--niveau", "INCONNUE=important"], "Contrainte inconnue"),
    ],
)
def test_commande_generer_niveau_refuse(jeu_minimal, arguments, attendu):
    resultat = CliRunner().invoke(
        cli.app, ["generer", "--semestre", str(jeu_minimal.semestre_id), *arguments]
    )
    assert resultat.exit_code == 1, resultat.output
    assert attendu in resultat.output
    with session_base() as session:
        assert session.scalars(select(m.Calcul)).all() == []  # rien n'a été lancé


# --------------------------------------------------------------------------- #
# Contraintes souples (une entorse coûte, une contrainte nécessaire interdit)
# --------------------------------------------------------------------------- #


def test_calcul_reussi_malgre_une_indisponibilite_impossible_a_respecter(jeu_minimal):
    """Jeu minimal, Mme Martin indisponible toute la semaine : refusé si la règle est nécessaire ;
    emploi du temps trouvé avec ses entorses si elle est importante, sans que la couche datée
    déplace les séances concernées."""
    with session_base() as session:
        martin = session.scalar(select(m.Enseignant).where(m.Enseignant.nom == "Martin"))
        for jour in range(6):
            session.add(
                m.Indisponibilite(
                    cible=CibleIndisponibilite.ENSEIGNANT,
                    enseignant=martin,
                    jour=jour,
                    motif="Absente toute la semaine",
                )
            )
    refus = _generer(jeu_minimal.semestre_id, dataclasses.replace(OPTIONS, niveaux=NIVEAUX_DURS))
    assert refus.etat is EtatCalcul.INFAISABLE
    souples = niveaux_avec(DISPO_ENSEIGNANT="important")
    suivi = _generer(jeu_minimal.semestre_id, dataclasses.replace(OPTIONS, niveaux=souples))
    assert suivi.etat is EtatCalcul.REUSSI, suivi.message
    entorses = suivi.statistiques["entorses"]
    assert set(entorses) == {"DISPO_ENSEIGNANT"} and entorses["DISPO_ENSEIGNANT"] >= 2
    assert "à des règles importantes (disponibilité des enseignants" in suivi.message
    # Les séances datées de Mme Martin ne sont pas replacées pour cette indisponibilité souple.
    with session_base() as session:
        occurrences = session.scalars(
            select(m.Occurrence).where(m.Occurrence.version_id == suivi.version_id)
        ).all()
        assert not any(
            "indisponible" in (o.commentaire or "") and "Martin" in (o.commentaire or "")
            for o in occurrences
        )
