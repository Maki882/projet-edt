"""Préchauffage des écrans après un calcul : vérifier en arrière-plan avant que l'utilisateur ne demande.

Après un calcul, la base a changé : la mémoire des écrans (`memoire`) est
périmée et le premier affichage de l'accueil, de « À traiter » ou de
« Générer » recalculerait les vérifications (environ deux secondes sur
l'établissement complet). Dès que l'écran du calcul constate sa fin, il lance
`lancer` : un fil d'arrière-plan refait ces calculs une fois, pendant que
l'utilisateur lit le résultat, et les écrans suivants s'affichent tout de
suite.

- au plus un préchauffage à la fois (les demandes suivantes sont ignorées
  tant qu'il tourne) ;
- il ne fait que remplir la mémoire : une erreur est journalisée avec sa
  trace et n'a pas d'autre effet (l'écran recalculera lui-même) ;
- `ACTIF` permet de le couper (les tests le coupent : un fil qui survit à sa
  base temporaire n'a pas de sens).
"""

from __future__ import annotations

import logging
import threading

from edt.db.moteur import session_base
from edt.services.emploi_du_temps import calculs, compteur
from edt.services.emploi_du_temps.versions import lister_semestres

journal = logging.getLogger(__name__)

ACTIF = True
"""Faux pour ne jamais lancer de préchauffage (tests)."""

_verrou = threading.Lock()
_en_cours = False


def lancer(url_base: str) -> bool:
    """Lance le préchauffage en arrière-plan ; renvoie faux s'il tourne déjà ou s'il est coupé."""
    global _en_cours
    if not ACTIF:
        return False
    with _verrou:
        if _en_cours:
            return False
        _en_cours = True
    threading.Thread(
        target=_prechauffer, args=(url_base,), name="prechauffage-ecrans", daemon=True
    ).start()
    return True


def _prechauffer(url_base: str) -> None:
    global _en_cours
    try:
        with session_base(url_base) as session:
            # Vérifie la semaine type et les séances datées de chaque version active.
            compteur.compteur_a_traiter(session)
            for semestre in lister_semestres(session):
                if semestre.version_active_id is not None:
                    calculs.pre_verifier(session, semestre.id)
        journal.info("Écrans préchauffés après un calcul")
    except Exception:
        journal.exception("Préchauffage des écrans interrompu (sans effet sur les données)")
    finally:
        with _verrou:
            _en_cours = False
