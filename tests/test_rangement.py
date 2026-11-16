"""Contrôle du rangement des tests en « unitaires » et « intégration ».

Ce fichier est à la racine de `tests/` : il ne porte ni le marqueur
`unitaire` ni le marqueur `integration`. Il vérifie que :

1. chaque test de `tests/unitaires/` est unitaire selon la règle, et chaque
   test de `tests/integration/` est d'intégration (règle et méthode décrites
   dans `tests/outils/classement.py` et `tests/README.md`) ;
2. aucun fichier de tests n'existe hors de ces deux dossiers (sauf celui-ci) ;
3. aucun module de tests n'importe un autre module de tests : les aides
   partagées vivent dans `tests/outils/` ;
4. le classeur lui-même reconnaît les deux côtés sur de petits exemples.

Lancer : `python -m pytest tests/test_rangement.py`.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest
from tests.outils.classement import (
    DOSSIERS,
    INTEGRATION,
    UNITAIRE,
    Verdict,
    classer,
    expliquer,
    mal_ranges,
)

RACINE = Path(__file__).resolve().parents[1]
"""Racine du dépôt."""

TESTS = RACINE / "tests"


def _modules_de_tests() -> list[Path]:
    """Fichiers que pytest collecte comme modules de tests (`test_*.py` ou `*_test.py`)."""
    return sorted(
        {*TESTS.rglob("test_*.py"), *TESTS.rglob("*_test.py")},
    )


# --- 1. Chaque test est dans le dossier de son côté -------------------------


def test_chaque_test_est_dans_le_dossier_de_son_cote():
    """Un test d'intégration rangé dans `tests/unitaires/` (ou l'inverse) fait échouer ce test."""
    classement = classer(RACINE)
    cotes = {verdict.cote for verdict in classement.values()}
    assert cotes == {UNITAIRE, INTEGRATION}, (
        "le classeur n'a pas trouvé les deux côtés : analyse cassée ?"
    )

    fautifs = mal_ranges(classement)
    assert not fautifs, (
        f"{len(fautifs)} test(s) mal rangé(s) selon la règle de tests/README.md :\n"
        + "\n".join(
            f"  - {expliquer(identifiant, classement[identifiant])}" for identifiant in fautifs
        )
    )


# --- 2. Aucun fichier de tests hors des deux dossiers -----------------------


def test_aucun_fichier_de_tests_hors_des_deux_dossiers():
    """Tout module de tests est sous `tests/unitaires/` ou `tests/integration/`, sauf ce contrôle."""
    autorises = {TESTS / dossier for dossier in DOSSIERS.values()}
    egares = [
        fichier.relative_to(RACINE).as_posix()
        for fichier in _modules_de_tests()
        if fichier != Path(__file__).resolve() and not any(d in fichier.parents for d in autorises)
    ]
    assert not egares, (
        "fichier(s) de tests hors de tests/unitaires/ et tests/integration/ "
        "(à ranger dans l'un des deux selon la règle de tests/README.md) :\n"
        + "\n".join(f"  - {f}" for f in egares)
    )


# --- 3. Aucun module de tests n'importe un autre module de tests -------------


def _imports_de_modules_de_tests(fichier: Path) -> list[str]:
    """Imports du fichier qui visent un `test_*.py` ou un `conftest.py` (par leur nom de module)."""
    trouves: list[str] = []
    for noeud in ast.walk(ast.parse(fichier.read_text(encoding="utf-8"))):
        if isinstance(noeud, ast.Import):
            chemins = [alias.name for alias in noeud.names]
        elif isinstance(noeud, ast.ImportFrom):
            base = "." * noeud.level + (noeud.module or "")
            chemins = [base] + [f"{base}.{alias.name}" for alias in noeud.names]
        else:
            continue
        for chemin in chemins:
            dernier = chemin.rstrip(".").rsplit(".", 1)[-1]
            if dernier == "conftest" or dernier.startswith("test_"):
                trouves.append(f"ligne {noeud.lineno} : {chemin}")
                break
    return trouves


def test_aucun_module_de_tests_n_importe_un_autre_module_de_tests():
    """Les aides partagées passent par `tests/outils/`, jamais par un `test_*.py` ou un `conftest.py`."""
    fautes = [
        f"{fichier.relative_to(RACINE).as_posix()} {ligne}"
        for fichier in sorted(TESTS.rglob("*.py"))
        for ligne in _imports_de_modules_de_tests(fichier)
    ]
    assert not fautes, (
        "import(s) d'un module de tests (déplacer l'aide dans tests/outils/) :\n"
        + "\n".join(f"  - {f}" for f in fautes)
    )


# --- 4. Le classeur lui-même -------------------------------------------------


def _ecrire(racine: Path, chemin: str, code: str) -> None:
    """Écrit un fichier Python sous `racine` (dossiers créés, `__init__.py` compris)."""
    fichier = racine / chemin
    fichier.parent.mkdir(parents=True, exist_ok=True)
    for dossier in fichier.relative_to(racine).parents:
        if dossier != Path("."):
            (racine / dossier / "__init__.py").touch()
    fichier.write_text(textwrap.dedent(code), encoding="utf-8")


def _verdict(racine: Path, identifiant: str) -> Verdict:
    """Verdict du classeur pour un test du dépôt miniature."""
    classement = classer(racine)
    assert identifiant in classement, f"test non trouvé par le classeur : {identifiant}"
    return classement[identifiant]


@pytest.fixture
def depot(tmp_path: Path) -> Path:
    """Dépôt miniature : un `conftest.py` avec une fixture neutre et une fixture `client`."""
    racine = tmp_path / "depot"
    _ecrire(
        racine,
        "tests/conftest.py",
        """
        import pytest
        from fastapi.testclient import TestClient

        @pytest.fixture
        def base(tmp_path):
            return tmp_path / "edt.db"

        @pytest.fixture
        def client(base):
            return TestClient(object())
        """,
    )
    return racine


CAS_D_INTEGRATION = {
    "TestClient direct": (
        """
        from fastapi.testclient import TestClient

        def test_cas():
            assert TestClient(object())
        """,
        "requête HTTP",
    ),
    "TestClient par une fixture": (
        """
        def test_cas(client):
            assert client.get("/").status_code == 200
        """,
        "requête HTTP",
    ),
    "TestClient par une aide du module": (
        """
        from fastapi.testclient import TestClient

        def _ouvrir():
            return TestClient(object())

        def test_cas():
            assert _ouvrir()
        """,
        "requête HTTP",
    ),
    "TestClient par une aide importée": (
        """
        from tests.outils.aides import ouvrir

        def test_cas():
            assert ouvrir()
        """,
        "requête HTTP",
    ),
    "CliRunner par une fixture du module": (
        """
        import pytest
        from typer.testing import CliRunner

        @pytest.fixture
        def lanceur():
            return CliRunner()

        def test_cas(lanceur):
            assert lanceur.invoke(object(), ["--help"])
        """,
        "ligne de commande",
    ),
    "autre processus": (
        """
        import subprocess

        def test_cas():
            subprocess.run(["python", "--version"], check=True)
        """,
        "autre processus",
    ),
    "calcul complet": (
        """
        from edt.services import generation

        def test_cas(base):
            assert generation.lancer_calcul(1)
        """,
        "calcul complet",
    ),
}


@pytest.mark.parametrize("cas", list(CAS_D_INTEGRATION))
def test_le_classeur_reconnait_un_test_d_integration(depot: Path, cas: str):
    """Chaque forme de preuve (directe, par fixture, par aide locale ou importée) rend le test d'intégration."""
    code, raison = CAS_D_INTEGRATION[cas]
    _ecrire(
        depot,
        "tests/outils/aides.py",
        """
        from fastapi.testclient import TestClient

        def ouvrir():
            return TestClient(object())
        """,
    )
    _ecrire(depot, "tests/integration/demo/test_cas.py", code)

    verdict = _verdict(depot, "tests/integration/demo/test_cas.py::test_cas")

    assert verdict.cote == INTEGRATION
    assert raison in verdict.raisons


def test_le_classeur_reconnait_un_test_unitaire(depot: Path):
    """Un test qui n'appelle que des fonctions Python, même avec une base temporaire, est unitaire."""
    _ecrire(
        depot,
        "tests/outils/aides.py",
        """
        def doubler(x):
            return 2 * x
        """,
    )
    _ecrire(
        depot,
        "tests/unitaires/demo/test_cas.py",
        """
        from tests.outils.aides import doubler

        def _tripler(x):
            return 3 * x

        def test_cas(base):
            assert doubler(_tripler(1)) == 6
        """,
    )

    verdict = _verdict(depot, "tests/unitaires/demo/test_cas.py::test_cas")

    assert verdict == Verdict(UNITAIRE, (), ())


def test_le_classeur_signale_les_tests_mal_ranges_dans_les_deux_sens(depot: Path):
    """Un test d'intégration dans `unitaires/` et un test unitaire dans `integration/` sont signalés."""
    _ecrire(
        depot,
        "tests/unitaires/demo/test_web.py",
        """
        def test_page(client):
            assert client.get("/")
        """,
    )
    _ecrire(
        depot,
        "tests/integration/demo/test_calcul.py",
        """
        def test_somme():
            assert 1 + 1 == 2
        """,
    )

    classement = classer(depot)
    fautifs = mal_ranges(classement)

    assert fautifs == [
        "tests/integration/demo/test_calcul.py::test_somme",
        "tests/unitaires/demo/test_web.py::test_page",
    ]
    message_web = expliquer(fautifs[1], classement[fautifs[1]])
    assert "TestClient" in message_web
    assert "tests/integration/demo/" in message_web
    message_calcul = expliquer(fautifs[0], classement[fautifs[0]])
    assert "aucune preuve" in message_calcul
    assert "tests/unitaires/demo/" in message_calcul
