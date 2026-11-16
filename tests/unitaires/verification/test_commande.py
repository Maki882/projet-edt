"""Point d'entrée `cli.main` de la commande `verifier`, appelé en Python sur le jeu minimal.

Les tests qui lancent la commande par `CliRunner` sont dans `tests/integration/verification/test_commande.py` ;
`version_sans_occurrences` vient de `tests/outils/verification/fixtures.py` (par `conftest.py`).
"""

from __future__ import annotations

from edt import cli


def test_point_d_entree(version_sans_occurrences, jeu_minimal):
    assert cli.main(["verifier", "--semestre", str(jeu_minimal.semestre_id)]) == 0
    assert cli.main(["verifier", "--version", "999"]) == 2
