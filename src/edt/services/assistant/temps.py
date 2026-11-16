"""Horloges de l'assistant : la date du jour et le temps écoulé.

Les autres modules les appellent par ce module (`temps.aujourdhui()`), jamais
par une copie importée : un test fixe ainsi la date ou fait avancer le temps
sans dépendre de l'horloge réelle.
"""

from __future__ import annotations

import datetime as dt
import time


def aujourdhui() -> dt.date:
    """Date du jour (heure locale du serveur)."""
    return dt.date.today()


def maintenant() -> float:
    """Secondes d'une horloge monotone (échéances, expiration des conversations)."""
    return time.monotonic()
