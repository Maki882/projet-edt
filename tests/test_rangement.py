"""Contrôle du rangement des tests en « unitaires » et « intégration ».

Ce fichier est à la racine de `tests/` : il ne porte ni le marqueur
`unitaire` ni le marqueur `integration`. Il vérifie que :

1. chaque test de `tests/unitaires/` est unitaire selon la règle, et chaque
   test de `tests/integration/` est d'intégration (règle et méthode décrites
   dans `tests/outils/classement.py` et `tests/README.md`) ;
2. aucun fichier de tests n'existe hors de ces deux dossiers (sauf celui-ci) ;
3. aucun module de tests n'importe un autre module de tests : les aides
   partagées vivent dans `tests/outils/` ;
4. le classeur lui-même reconnaît les deux côtés sur de petits exemples ;
5. les nombres et tableaux de fichiers générés de `tests/README.md`,
   `tests/unitaires/README.md` et `tests/integration/README.md` sont à jour
   (sinon : `python -m tests.outils.classement --ecrire`), et chaque fichier de
   tests a une docstring de module, dont la première phrase le décrit.

Lancer : `python -m pytest tests/test_rangement.py`.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest
from tests.outils.classement import (
    BLOCS,
    COMMANDE,
    DOSSIERS,
    INTEGRATION,
    SANS_DOCSTRING,
    UNITAIRE,
    Verdict,
    blocs_perimes,
    classer,
    debut_de_bloc,
    expliquer,
    fin_de_bloc,
    inventorier,
    main,
    mal_ranges,
    partie_generee,
    produire_famille,
    produire_vue_d_ensemble,
    remplacer,
    sans_docstring,
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


# --- 5. Les nombres et tableaux des documents de tests sont à jour ----------


def test_les_parties_generees_des_documents_de_tests_sont_a_jour():
    """Les nombres et tableaux de `tests/README.md` et des deux `README.md` de famille sont à jour."""
    perimes = blocs_perimes(RACINE)
    if perimes:
        pytest.fail(
            "nombres ou tableaux de tests périmés ; lancer, depuis la racine du dépôt : "
            f"{COMMANDE}\n" + "\n".join(f"  - {p}" for p in perimes),
            pytrace=False,
        )


def test_chaque_fichier_de_tests_a_une_docstring_de_module():
    """La première phrase de la docstring d'un module de tests est sa description dans les documents."""
    inventaire = inventorier(RACINE)
    assert len(inventaire.fichiers) > 50, "peu de fichiers de tests trouvés : analyse cassée ?"
    manquantes = sans_docstring(inventaire)
    assert not manquantes, (
        "fichier(s) de tests sans docstring de module (écrire en tête une docstring dont la "
        f"première phrase dit ce que vérifie le fichier, puis lancer {COMMANDE}) :\n"
        + "\n".join(f"  - {f}" for f in manquantes)
    )


@pytest.fixture
def depot_documente(tmp_path: Path) -> Path:
    """Dépôt miniature : deux familles, un composant titré, les trois documents avec leurs marqueurs."""
    racine = tmp_path / "depot"
    _ecrire(racine, "tests/test_rangement.py", "def test_controle():\n    pass\n")
    _ecrire(
        racine,
        "tests/unitaires/alpha/test_un.py",
        '''
        """Première phrase, avec `test_x.py` et 1.5 dedans. Seconde phrase ignorée.

        Paragraphe suivant ignoré.
        """
        import pytest

        pytestmark = pytest.mark.cas_limite

        def test_a():
            pass

        @pytest.mark.parametrize("x", [1, 2])
        @pytest.mark.perf
        def test_b(x):
            pass
        ''',
    )
    (racine / "tests/unitaires/alpha/__init__.py").write_text(
        '"""Composant alpha.\n\nDétail."""\n', encoding="utf-8"
    )
    _ecrire(
        racine,
        "tests/integration/beta_gamma/test_deux.py",
        '''
        """Barre | dans la description."""

        def test_c():
            pass
        ''',
    )
    for nom, (document, _) in BLOCS.items():
        fichier = racine / document
        fichier.write_text(
            f"# Titre\n\nIntro.\n\n{debut_de_bloc(nom)}\nancien 999 fonctions\n{fin_de_bloc(nom)}\n"
            "\nSuite.\n",
            encoding="utf-8",
        )
    return racine


def test_le_generateur_compte_les_fonctions_et_lit_titres_et_descriptions(depot_documente):
    """Fonctions comptées une fois, marqueurs vus, première phrase, titre par défaut d'un composant."""
    inventaire = inventorier(depot_documente)

    assert [(f.chemin, f.fonctions, f.cas_limite, f.perf) for f in inventaire.fichiers] == [
        ("alpha/test_un.py", 2, 2, 1),
        ("beta_gamma/test_deux.py", 1, 0, 0),
    ]
    assert inventaire.fichiers[0].description == ("Première phrase, avec `test_x.py` et 1.5 dedans")
    assert inventaire.titres == {
        (UNITAIRE, "alpha"): "Composant alpha",
        (INTEGRATION, "beta_gamma"): "Beta gamma",
    }
    assert inventaire.controle == 1

    vue = produire_vue_d_ensemble(inventaire)
    assert "| `alpha` | 2 | — | 2 |" in vue
    assert "| `beta_gamma` | — | 1 | 1 |" in vue
    assert "| **Total** | **2** | **1** | **3** |" in vue
    assert "| dont cas limites (`cas_limite`) | 2 | 0 | 2 |" in vue
    assert "`tests/test_rangement.py` : 1 fonction." in vue

    integration = produire_famille(inventaire, INTEGRATION)
    assert "### `beta_gamma/` : Beta gamma (1 fonction)" in integration
    assert (
        "| [`beta_gamma/test_deux.py`](beta_gamma/test_deux.py) | Barre \\| dans la description | 1 |"
    ) in integration


def test_ecrire_regenere_les_documents_et_le_controle_voit_chaque_ecart(depot_documente, capsys):
    """Bloc périmé signalé ; écriture limitée aux marqueurs ; nouveau fichier ; docstring absente."""
    assert len(blocs_perimes(depot_documente)) == len(BLOCS)

    assert main([str(depot_documente), "--ecrire"]) == 0
    assert "mis à jour" in capsys.readouterr().out
    assert blocs_perimes(depot_documente) == []
    texte = (depot_documente / "tests/unitaires/README.md").read_text(encoding="utf-8")
    assert texte.startswith(f"# Titre\n\nIntro.\n\n{debut_de_bloc('tests-unitaires')}\n\n")
    assert texte.endswith(f"\n{fin_de_bloc('tests-unitaires')}\n\nSuite.\n")
    assert "ancien" not in texte

    assert main([str(depot_documente), "--ecrire"]) == 0
    assert capsys.readouterr().out.count("déjà à jour") == len(BLOCS)

    # un nombre modifié à la main dans un bloc généré
    lisezmoi = depot_documente / "tests/unitaires/README.md"
    lisezmoi.write_text(texte.replace("| 2 |", "| 3 |"), encoding="utf-8")
    assert [p.split(" : ")[0] for p in blocs_perimes(depot_documente)] == [
        "tests/unitaires/README.md"
    ]

    # un nouveau composant, avec un fichier sans docstring de module
    _ecrire(depot_documente, "tests/unitaires/banc_tests/test_neuf.py", "def test_d():\n    pass\n")
    assert main([str(depot_documente), "--ecrire"]) == 1
    sortie = capsys.readouterr()
    assert "tests/unitaires/banc_tests/test_neuf.py" in sortie.err
    texte = lisezmoi.read_text(encoding="utf-8")
    assert "### `banc_tests/` : Banc tests (1 fonction)" in texte
    assert SANS_DOCSTRING in texte
    assert sans_docstring(inventorier(depot_documente)) == [
        "tests/unitaires/banc_tests/test_neuf.py"
    ]

    # des marqueurs absents
    lisezmoi.write_text("# Sans marqueurs\n", encoding="utf-8")
    assert "marqueurs du bloc « tests-unitaires » absents" in blocs_perimes(depot_documente)[0]
    assert main([str(depot_documente), "--ecrire"]) == 1
    assert "introuvables" in capsys.readouterr().err
    with pytest.raises(ValueError, match="introuvables"):
        remplacer("rien", "tests-unitaires", "contenu")


def test_ecrire_remplace_un_bloc_reste_en_conflit_de_fusion(depot_documente):
    """Des lignes de conflit de fusion entre les marqueurs disparaissent à la régénération."""
    lisezmoi = depot_documente / "tests/integration/README.md"
    nom = "tests-integration"
    lisezmoi.write_text(
        f"Avant.\n{debut_de_bloc(nom)}\n<<<<<<< HEAD\n| 1 |\n=======\n| 2 |\n>>>>>>> branche\n"
        f"{fin_de_bloc(nom)}\nAprès.\n",
        encoding="utf-8",
    )

    assert main([str(depot_documente), "--ecrire"]) == 0
    texte = lisezmoi.read_text(encoding="utf-8")
    assert "<<<<<<<" not in texte and ">>>>>>>" not in texte
    assert texte.startswith("Avant.\n") and texte.endswith("Après.\n")
    assert partie_generee(texte, nom) == produire_famille(inventorier(depot_documente), INTEGRATION)
