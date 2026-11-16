"""Dépôt factice pour les tests du banc : quelques tests minuscules, jamais la vraie suite.

`ecrire_depot(racine)` crée sous `racine` un `pyproject.toml` et :

- `tests/unitaires/demo/test_exemple.py` : un test réussi, un en échec, un
  ignoré, un paramétré à trois cas (dont un en échec), un test de charge et une
  méthode de classe ;
- `tests/integration/demo/test_environnement.py` : un test qui vérifie, depuis
  le sous-processus, que l'environnement est assaini ;
- `tests/integration/lent/test_lent.py` : un test qui dure une minute si la
  variable `BANC_TESTS_LENT` vaut 1 (fixture `lent` : arrêt, durée maximale),
  immédiat sinon ;

- `tests/integration/casse/test_casse.py` : un fichier qui ne se charge pas.

Fixtures : `depot` (dépôt écrit, `EDT_TESTS_RACINE` posée, exécution
précédente oubliée et toute exécution arrêtée à la fin) ; aide
`attendre_fin`, qui attend la fin d'une exécution et vérifie qu'elle a
tourné dans un autre processus.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

from edt.services.banc_tests import execution as ex
from edt.services.banc_tests.catalogue import VARIABLE_RACINE

DELAI_S = 30.0
"""Attente maximale d'une exécution du dépôt factice (quelques secondes en pratique)."""

FICHIERS: dict[str, str] = {
    "pyproject.toml": """
        [tool.pytest.ini_options]
        markers = ["perf: test de charge"]
        """,
    "tests/unitaires/demo/test_exemple.py": '''
        """Petits tests du dépôt factice."""
        import pytest


        def test_reussi():
            """Réussit toujours. Seconde phrase, absente du résumé."""
            assert 1 + 1 == 2


        def test_en_echec():
            """Échoue toujours."""
            assert 1 + 1 == 3, "message de l'assertion"


        def test_ignore():
            pytest.skip("raison de démonstration")


        @pytest.mark.parametrize("n", [1, 2, 3])
        def test_parametre(n):
            assert n < 3


        @pytest.mark.perf
        def test_charge():
            assert True


        class TestGroupe:
            def test_methode(self):
                assert True
        ''',
    "tests/integration/demo/test_environnement.py": '''
        import os


        def test_environnement_assaini():
            """Aucune variable EDT_ héritée ; base et journaux dans le dossier temporaire."""
            assert "EDT_HERITEE" not in os.environ
            assert "edt-tests-" in os.environ["EDT_DB_URL"]
            assert "edt-tests-" in os.environ["EDT_LOG_DIR"]
            assert os.environ["PYTHONDONTWRITEBYTECODE"] == "1"
        ''',
    "tests/integration/lent/test_lent.py": """
        import os
        import time


        def test_lent():
            if os.environ.get("BANC_TESTS_LENT") == "1":
                time.sleep(60)
        """,
    "tests/integration/casse/test_casse.py": """
        import module_qui_n_existe_pas


        def test_jamais_charge():
            assert module_qui_n_existe_pas
        """,
}

EXEMPLE = "tests/unitaires/demo/test_exemple.py"
CLE_REUSSI = f"{EXEMPLE}::test_reussi"
CLE_ECHEC = f"{EXEMPLE}::test_en_echec"
CLE_IGNORE = f"{EXEMPLE}::test_ignore"
CLE_PARAMETRE = f"{EXEMPLE}::test_parametre"
CLE_CHARGE = f"{EXEMPLE}::test_charge"
CLE_METHODE = f"{EXEMPLE}::TestGroupe::test_methode"
CLE_ENVIRONNEMENT = "tests/integration/demo/test_environnement.py::test_environnement_assaini"
CLE_LENT = "tests/integration/lent/test_lent.py::test_lent"
CLE_CASSE = "tests/integration/casse/test_casse.py::test_jamais_charge"


def ecrire_depot(racine: Path) -> Path:
    """Écrit le dépôt factice sous `racine` et renvoie `racine`."""
    for chemin, contenu in FICHIERS.items():
        fichier = racine / chemin
        fichier.parent.mkdir(parents=True, exist_ok=True)
        fichier.write_text(textwrap.dedent(contenu).lstrip(), encoding="utf-8")
    return racine


@pytest.fixture
def depot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Dépôt factice désigné par `EDT_TESTS_RACINE` ; aucune exécution ne lui survit."""
    racine = ecrire_depot(tmp_path / "depot")
    monkeypatch.setenv(VARIABLE_RACINE, str(racine))
    monkeypatch.setattr(ex, "_courante", None)
    yield racine
    courante = ex.execution_courante()
    if courante is not None and courante.en_cours:
        ex.demander_arret()
        ex.attendre(courante, DELAI_S)


@pytest.fixture
def lent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le test `test_lent` du dépôt factice dure une minute (variable héritée par le sous-processus)."""
    monkeypatch.setenv("BANC_TESTS_LENT", "1")


def attendre_fin(execution: ex.Execution) -> ex.Suivi:
    """Attend la fin de l'exécution et vérifie qu'elle a tourné dans un autre processus pytest."""
    suivi = ex.attendre(execution, DELAI_S)
    assert not suivi.en_cours, f"exécution toujours en cours après {DELAI_S} s"
    assert isinstance(execution.processus, subprocess.Popen)
    assert execution.processus.pid != os.getpid()
    assert execution.processus.returncode is not None  # le processus est bien terminé
    return suivi
