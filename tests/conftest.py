"""Fixtures communes à tous les tests.

Chaque test tourne dans un dossier temporaire qui lui est propre : la base
SQLite et les journaux n'atteignent jamais le dépôt (`environnement` est
appliquée d'office à tous les tests).

- `environnement` : variables `EDT_*` pointant vers le dossier temporaire ;
- `aujourdhui` : date de référence des tests (5 octobre 2026) ;
- `base` : base initialisée (schéma et données par défaut), sans référentiels ;
- `session` : session SQLAlchemy sur cette base (non validée automatiquement) ;
- `jeu_minimal` : base chargée du jeu minimal (`edt.db.jeux`), calculs sans
  amélioration (« objectif_actif » coupé) ;
- `client` : client de test de l'application web.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from edt.db.initialisation import RapportInitialisation, initialiser_base
from edt.db.jeux import JeuMinimal, creer_jeu_minimal
from edt.db.moteur import fabrique_sessions, liberer_moteurs, session_base
from edt.journal import reinitialiser_journalisation
from edt.services.parametres import ecrire_parametre

# Les aides de tests/outils gardent des messages d'assertion détaillés, comme les modules de tests.
pytest.register_assert_rewrite("tests.outils")

AUJOURDHUI = dt.date(2026, 10, 5)
"""Date de référence des tests : l'année scolaire créée par défaut est 2026-2027."""


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Pose le marqueur `unitaire` ou `integration` d'après le dossier du test.

    `pytest -m unitaire` et `pytest -m integration` choisissent ainsi une
    famille sans que chaque test ait à porter le marqueur.
    """
    for item in items:
        dossiers = item.path.parts
        if "integration" in dossiers:
            item.add_marker(pytest.mark.integration)
        elif "unitaires" in dossiers:
            item.add_marker(pytest.mark.unitaire)


@pytest.fixture(autouse=True)
def environnement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Isole le test : base et journaux dans un dossier temporaire, moteurs libérés à la fin."""
    monkeypatch.setenv("EDT_DB_URL", f"sqlite:///{(tmp_path / 'edt.db').as_posix()}")
    monkeypatch.setenv("EDT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("EDT_ENV", "dev")
    for variable in ("EDT_HOST", "EDT_PORT", "EDT_LOG_LEVEL"):
        monkeypatch.delenv(variable, raising=False)
    yield tmp_path
    liberer_moteurs()
    reinitialiser_journalisation()


@pytest.fixture
def aujourdhui() -> dt.date:
    """Date de référence des tests (année scolaire 2026-2027)."""
    return AUJOURDHUI


@pytest.fixture
def base(environnement: Path, aujourdhui: dt.date) -> RapportInitialisation:
    """Base initialisée (schéma et données par défaut de l'année 2026-2027)."""
    return initialiser_base(aujourdhui=aujourdhui)


@pytest.fixture
def session(base: RapportInitialisation) -> Iterator[Session]:
    """Session sur la base initialisée ; le test valide lui-même s'il en a besoin."""
    with fabrique_sessions()() as session:
        yield session


@pytest.fixture
def jeu_minimal(base: RapportInitialisation) -> JeuMinimal:
    """Base chargée du jeu minimal ; renvoie ses repères (identifiant du semestre...).

    Le réglage « objectif_actif » y est coupé : un test qui lance un calcul veut
    un emploi du temps valide, pas le meilleur, et prouver l'optimum coûterait
    plus d'une seconde à chaque calcul. Un test qui veut les réglages par
    défaut de l'application les rétablit (`reinitialiser_parametres`) ou passe
    ses propres options.
    """
    with session_base() as session:
        ecrire_parametre(session, "objectif_actif", False)
        return creer_jeu_minimal(session)


@pytest.fixture
def client(base: RapportInitialisation):
    """Client de test de l'application web, sur la base initialisée."""
    from fastapi.testclient import TestClient

    from edt.web.app import creer_app

    return TestClient(creer_app())
