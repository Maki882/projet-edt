"""Instantané : identifiants hors des entiers SQLite, côté commandes (pre-verifier, verifier)."""

from __future__ import annotations

import pytest

from edt import cli
from edt.commandes.verification import CODE_IMPOSSIBLE
from edt.erreurs import ErreurIntrouvable
from edt.services.instantane import charger_instantane, charger_occurrences, charger_placements
from edt.services.niveaux import niveaux_de_version, niveaux_regles_de_version


@pytest.mark.cas_limite
@pytest.mark.parametrize("identifiant", [2**63, 10**30, -1, 0])
def test_un_identifiant_hors_des_entiers_sqlite_est_introuvable(
    base, session, identifiant, lanceur
):
    """Cas : un identifiant de semestre ou de version au-delà de 2**63, négatif ou nul arrive
    par une adresse web ou une option de commande.

    Risque : `OverflowError` brute du pilote (500 côté web, code 2 en ligne de commande) alors
    que -1 donnait proprement « n'existe pas » ; tous doivent être introuvables.
    """
    for chargeur in (charger_instantane, charger_placements, charger_occurrences):
        with pytest.raises(ErreurIntrouvable, match="n'existe pas"):
            chargeur(session, identifiant)
    # Même garde pour les niveaux d'une version.
    for lecteur in (niveaux_de_version, niveaux_regles_de_version):
        with pytest.raises(ErreurIntrouvable, match="n'existe pas"):
            lecteur(session, identifiant)


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("commande", "identifiant", "code_attendu"),
    [
        (["pre-verifier", "--semestre"], 2**63, cli.CODE_ERREUR_METIER),
        (["pre-verifier", "--semestre"], 10**30, cli.CODE_ERREUR_METIER),
        (["verifier", "--version"], 2**63, CODE_IMPOSSIBLE),
    ],
    ids=["pre-verifier-2^63", "pre-verifier-10^30", "verifier-2^63"],
)
def test_une_commande_avec_un_identifiant_hors_bornes_repond_n_existe_pas(
    base, commande, identifiant, code_attendu, lanceur
):
    """Cas : `pre-verifier --semestre 99999999999999999999` ou `verifier --version 2**63`
    (identifiant au-delà des entiers de SQLite).

    Risque : `OverflowError` brute du pilote, « Erreur inattendue » avec trace, au lieu de
    « n'existe pas » avec le code de sortie que la commande rend déjà pour -1 : 1 (erreur
    métier) pour `pre-verifier`, 2 (« vérification impossible ») pour `verifier`.
    """
    resultat = lanceur.invoke(cli.app, [*commande, str(identifiant)])
    assert resultat.exit_code == code_attendu, resultat.output
    assert "n'existe pas" in resultat.output and "Traceback" not in resultat.output
