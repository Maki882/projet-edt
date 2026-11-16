"""Aides partagées des tests de concurrence des chargements de jeux fictifs (revue n° 14).

- `DELAI_S` : garde-fou des attentes entre fils ;
- `_rapport_factice(graine)` : ce que la page de résultat lit d'un `RapportJeu` ;
- `_en_arriere_plan(fonction)` : lance `fonction` dans un fil, renvoie le fil et
  un dictionnaire qui recevra `valeur` ou `erreur`.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

DELAI_S = 10


def _rapport_factice(graine: int) -> SimpleNamespace:
    """Ce que la page de résultat lit d'un `RapportJeu`."""
    return SimpleNamespace(
        graine=graine,
        duree_s=0.0,
        compteurs={"salle": 0, "enseignant": 0},
        nb_besoins=0,
        resume=lambda: f"Jeu fictif (graine {graine})",
    )


def _en_arriere_plan(fonction) -> tuple[threading.Thread, dict]:
    resultat: dict = {}

    def executer():
        try:
            resultat["valeur"] = fonction()
        except BaseException as erreur:  # transmis au test, qui le relance
            resultat["erreur"] = erreur

    fil = threading.Thread(target=executer)
    fil.start()
    return fil, resultat
