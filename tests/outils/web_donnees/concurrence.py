"""Aides partagées des tests de concurrence des chargements de jeux fictifs.

- `DELAI_S` : garde-fou des attentes entre fils ;
- `_rapport_factice(graine)` : ce que la page de résultat lit d'un `RapportJeu` ;
- `_en_arriere_plan(fonction)` : lance `fonction` dans un fil, renvoie le fil et
  un dictionnaire qui recevra `valeur` ou `erreur` ;
- `attendre_fil(fil, resultat)` : attend la fin du fil et renvoie `valeur`, ou
  échoue en disant que le fil tourne encore, ou relance son `erreur`.
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


def attendre_fil(fil: threading.Thread, resultat: dict):
    """Valeur rendue par le fil lancé par `_en_arriere_plan`, une fois fini (`DELAI_S` au plus)."""
    fil.join(DELAI_S)
    assert not fil.is_alive(), "le fil n'a pas fini dans le délai (il tient peut-être un verrou)"
    if "erreur" in resultat:
        raise resultat["erreur"]
    return resultat["valeur"]
