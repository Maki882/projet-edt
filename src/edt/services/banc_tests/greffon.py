"""Greffon pytest du banc de tests : une ligne JSON par événement, dans un fichier.

Chargé dans le sous-processus par `-p edt.services.banc_tests.greffon`. Le
fichier est désigné par la variable `BANC_TESTS_EVENEMENTS` ; sans elle, le
greffon ne fait rien (pytest lancé à la main reste inchangé). Le service lit
ce fichier au fur et à mesure : l'affichage de pytest n'est jamais analysé.

Événements (champ `type`) :

- `collecte` : identifiants des cas retenus (`ids`), après la sélection ;
- `erreur_collecte` : un fichier n'a pas pu être chargé (`id`, `detail`) ;
- `debut` : un cas commence (`id`) ;
- `rapport` : compte rendu d'une phase d'un cas (`id`, `phase` : setup, call
  ou teardown, `issue` : passed, failed ou skipped, `duree`, `detail` pour un
  échec, `raison` pour un test ignoré, `attendu` pour un échec attendu) ;
- `fin` : code de sortie de pytest (`code`).

Chaque ligne est écrite d'un bloc puis vidée : une ligne lue est complète,
sauf la dernière si le processus est tué au milieu (le lecteur l'ignore).
"""

from __future__ import annotations

import json
import os

import pytest

VARIABLE_EVENEMENTS = "BANC_TESTS_EVENEMENTS"
"""Variable d'environnement qui donne le chemin du fichier des événements."""

TAILLE_DETAIL_MAX = 20_000
"""Au-delà, le détail d'un échec est tronqué (le journal complet garde tout)."""

_flux = None


def _ecrire(evenement: dict) -> None:
    if _flux is None:
        return
    _flux.write(json.dumps(evenement, ensure_ascii=False) + "\n")
    _flux.flush()


def _tronquer(texte: str) -> str:
    if len(texte) <= TAILLE_DETAIL_MAX:
        return texte
    return texte[:TAILLE_DETAIL_MAX] + "\n[… détail tronqué ; voir le journal complet]"


def pytest_configure(config: pytest.Config) -> None:
    """Ouvre le fichier des événements (seulement si la variable est posée)."""
    global _flux
    chemin = os.environ.get(VARIABLE_EVENEMENTS)
    if chemin and _flux is None:
        _flux = open(chemin, "a", encoding="utf-8")  # fermé par pytest_unconfigure


def pytest_collectreport(report: pytest.CollectReport) -> None:
    """Signale un fichier de tests qui n'a pas pu être chargé."""
    if report.failed:
        _ecrire(
            {
                "type": "erreur_collecte",
                "id": report.nodeid,
                "detail": _tronquer(report.longreprtext),
            }
        )


def pytest_collection_finish(session: pytest.Session) -> None:
    """Liste les cas qui vont tourner (après la sélection par marqueur)."""
    _ecrire({"type": "collecte", "ids": [item.nodeid for item in session.items]})


def pytest_runtest_logstart(nodeid: str, location) -> None:
    """Un cas commence."""
    _ecrire({"type": "debut", "id": nodeid})


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Compte rendu d'une phase d'un cas."""
    evenement = {
        "type": "rapport",
        "id": report.nodeid,
        "phase": report.when,
        "issue": report.outcome,
        "duree": round(report.duration, 4),
    }
    attendu = getattr(report, "wasxfail", None)
    if attendu is not None:
        evenement["attendu"] = str(attendu)
    if report.failed:
        evenement["detail"] = _tronquer(report.longreprtext)
    elif report.skipped:
        if isinstance(report.longrepr, tuple) and len(report.longrepr) == 3:
            evenement["raison"] = str(report.longrepr[2]).removeprefix("Skipped: ")
        else:
            evenement["raison"] = report.longreprtext
    _ecrire(evenement)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Code de sortie de pytest."""
    _ecrire({"type": "fin", "code": int(exitstatus)})


def pytest_unconfigure(config: pytest.Config) -> None:
    """Ferme le fichier des événements."""
    global _flux
    if _flux is not None:
        _flux.close()
        _flux = None
