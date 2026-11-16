"""Fixtures de l'assistant partagées par ses tests unitaires et d'intégration.

Chaque côté les importe par leur nom dans son `conftest.py` :

- `sans_cle` (automatique dans les dossiers `assistant`) : aucune variable
  d'identifiant, aucun profil du poste (`ANTHROPIC_CONFIG_DIR` vers un dossier
  vide), aucune adresse de service personnalisée, aucune variable
  `EDT_ASSISTANT_*` héritée, registre et compteurs de l'assistant neufs ;
- `cle_factice` : pose une valeur d'identifiant factice (`CLE_FACTICE`), pour
  les tests « configuré » ; elle ne sert jamais à appeler le service ;
- `version_minimale` : jeu minimal et sa version de référence active (repères) ;
- `faux_client` : un `FauxClient` (`tests.outils.assistant.faux_client`) à remplir
  de réponses (`faux_client.reponses`), injecté à la place du client du SDK ;
- `jour_fige` : la date vue par l'assistant (`temps.aujourdhui`), le 5 octobre 2026 ;
- `attendre(echange)` (fonction) : attend la fin d'un échange lancé dans son fil.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from tests.outils.assistant.faux_client import FauxClient
from tests.outils.verification.versions import enregistrer_version

from edt.db.jeux import JeuMinimal
from edt.db.moteur import session_base
from edt.services.assistant import client as module_client
from edt.services.assistant import reinitialiser, temps
from edt.services.assistant.configuration import oublier_annonce
from edt.services.assistant.conversations import Echange

CLE_FACTICE = "sk-ant-test-NE-JAMAIS-AFFICHER"
"""Valeur d'identifiant factice : aucun texte produit par l'application ne doit la contenir."""

VARIABLES_EFFACEES = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_PROFILE",
    "ANTHROPIC_LOG",
    "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_IDENTITY_TOKEN",
    "ANTHROPIC_IDENTITY_TOKEN_FILE",
)


@pytest.fixture
def sans_cle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Environnement sans identifiant ni réglage de l'assistant ; état de l'assistant neuf."""
    import os

    for nom in VARIABLES_EFFACEES:
        monkeypatch.delenv(nom, raising=False)
    for nom in [n for n in os.environ if n.startswith("EDT_ASSISTANT_")]:
        monkeypatch.delenv(nom, raising=False)
    dossier = tmp_path / "config-anthropic"
    dossier.mkdir()
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(dossier))
    oublier_annonce()
    reinitialiser()
    yield
    reinitialiser()
    oublier_annonce()


@pytest.fixture
def cle_factice(monkeypatch: pytest.MonkeyPatch) -> str:
    """Identifiant factice dans l'environnement : l'assistant se dit configuré."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", CLE_FACTICE)
    return CLE_FACTICE


@dataclass(frozen=True)
class Reperes:
    """Repères du jeu minimal avec sa version saisie à la main (`version_minimale`)."""

    annee_id: int
    semestre_id: int
    version_id: int


@pytest.fixture
def version_minimale(jeu_minimal: JeuMinimal) -> Reperes:
    """Jeu minimal et sa version de référence active (`tests.outils.verification.versions`).

    Jeudi après-midi : N016 accueille le devoir surveillé (une semaine), N105,
    N204 et L115 sont libres ; L115 est en travaux du 2 au 6 novembre 2026.
    """
    with session_base() as session:
        version_id = enregistrer_version(session, jeu_minimal.semestre_id)
    return Reperes(jeu_minimal.annee_scolaire_id, jeu_minimal.semestre_id, version_id)


@pytest.fixture
def faux_client(monkeypatch: pytest.MonkeyPatch, cle_factice: str) -> FauxClient:
    """Client factice injecté à la place du client du SDK (aucun appel réseau)."""
    faux = FauxClient([])
    monkeypatch.setattr(module_client, "creer_client", lambda reglages: faux)
    return faux


@pytest.fixture
def jour_fige(monkeypatch: pytest.MonkeyPatch) -> dt.date:
    """La date vue par l'assistant est le 5 octobre 2026 (semestre S1 de 2026-2027)."""
    jour = dt.date(2026, 10, 5)
    monkeypatch.setattr(temps, "aujourdhui", lambda: jour)
    return jour


def attendre(echange: Echange, delai_s: float = 5.0) -> Echange:
    """Attend que l'échange lancé dans son fil soit terminé (échec du test au-delà du délai)."""
    fin = time.monotonic() + delai_s
    while not echange.termine:
        if time.monotonic() > fin:
            raise AssertionError(f"échange {echange.reference} non terminé en {delai_s} s")
        time.sleep(0.01)
    return echange
