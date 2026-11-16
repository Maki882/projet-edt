"""Commande `python -m edt tests` sur le dépôt factice : code de sortie, échecs affichés, résultats enregistrés."""

from __future__ import annotations

from tests.outils.banc_tests.depot import CLE_ECHEC, CLE_ENVIRONNEMENT
from typer.testing import CliRunner

from edt.cli import construire_app
from edt.services.banc_tests import resultats as res


def test_commande_sort_en_erreur_s_il_y_a_un_echec(depot):
    resultat = CliRunner().invoke(construire_app(), ["tests", "--famille", "unitaires"])

    assert resultat.exit_code == 1, resultat.output
    assert f"ÉCHEC  {CLE_ECHEC}" in resultat.output
    assert "5 tests terminés" in resultat.output and "2 échecs" in resultat.output
    historique = res.lire_historique()
    assert historique.tests[CLE_ECHEC].etat == res.ECHEC  # visibles ensuite dans l'application
    assert historique.derniere.famille == "unitaires"


def test_commande_reussie_sur_un_composant(depot):
    resultat = CliRunner().invoke(
        construire_app(), ["tests", "--famille", "integration", "--composant", "demo"]
    )

    assert resultat.exit_code == 0, resultat.output
    assert "1 test terminé" in resultat.output and "0 échec," in resultat.output
    assert res.lire_historique().tests[CLE_ENVIRONNEMENT].etat == res.REUSSI


def test_commande_refuse_une_cible_inconnue(depot):
    inconnu = CliRunner().invoke(construire_app(), ["tests", "--composant", "inconnu"])
    famille = CliRunner().invoke(construire_app(), ["tests", "--famille", "fonctionnels"])

    assert inconnu.exit_code == 1 and "Composant de tests inconnu" in inconnu.output
    assert famille.exit_code == 1 and "Famille inconnue" in famille.output
