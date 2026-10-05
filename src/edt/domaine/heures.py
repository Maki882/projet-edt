"""Conversions d'heures et petits calculs sur les plages horaires.

Dans tout le projet, une heure est un **entier de minutes depuis minuit**
(8 h 30 = 510), en heure locale Europe/Paris, sans fuseau. Une plage est
semi-ouverte : `[debut, fin[`, donc deux séances contiguës ne se chevauchent
pas.
"""

from __future__ import annotations

import re

from edt.erreurs import ErreurDonneesInvalides

MINUTES_PAR_JOUR = 24 * 60

_MOTIF_HEURE = re.compile(
    r"""^\s*(?P<h>\d{1,2})\s*
        (?:(?:h|:)\s*(?P<m>\d{1,2})?)?
        \s*$""",
    re.IGNORECASE | re.VERBOSE,
)


def minutes_vers_texte(minutes: int) -> str:
    """Formate des minutes depuis minuit à la française : 510 donne « 8 h 30 »."""
    _valider_minutes(minutes)
    heures, reste = divmod(minutes, 60)
    return f"{heures} h {reste:02d}"


def minutes_vers_hhmm(minutes: int) -> str:
    """Formate des minutes depuis minuit en `HH:MM` (champs de formulaire, exports)."""
    _valider_minutes(minutes)
    heures, reste = divmod(minutes, 60)
    return f"{heures:02d}:{reste:02d}"


def texte_vers_minutes(texte: str) -> int:
    """Lit une heure saisie (« 8 h 30 », « 8h30 », « 08:30 », « 18 h ») en minutes.

    Lève `ErreurDonneesInvalides` si le texte n'est pas une heure valide.
    """
    correspondance = _MOTIF_HEURE.match(texte) if isinstance(texte, str) else None
    if correspondance is None:
        raise ErreurDonneesInvalides(
            f"Heure invalide : {texte!r} (formats acceptés : « 8 h 30 », « 8h30 », « 08:30 »)."
        )
    heures = int(correspondance["h"])
    minutes = int(correspondance["m"] or 0)
    if heures > 24 or minutes > 59 or (heures == 24 and minutes > 0):
        raise ErreurDonneesInvalides(f"Heure invalide : {texte!r}.")
    return heures * 60 + minutes


def duree_vers_texte(minutes: int) -> str:
    """Formate une durée : 120 donne « 2 h », 90 donne « 1 h 30 », 45 donne « 45 min »."""
    if minutes < 0:
        raise ErreurDonneesInvalides(f"Durée négative : {minutes} minutes.")
    heures, reste = divmod(minutes, 60)
    if heures == 0:
        return f"{reste} min"
    if reste == 0:
        return f"{heures} h"
    return f"{heures} h {reste:02d}"


def plage_vers_texte(debut: int, fin: int) -> str:
    """Formate une plage horaire : « 8 h 30 - 10 h 30 »."""
    return f"{minutes_vers_texte(debut)} - {minutes_vers_texte(fin)}"


def se_chevauchent(debut_a: int, fin_a: int, debut_b: int, fin_b: int) -> bool:
    """Vrai si les plages semi-ouvertes `[debut_a, fin_a[` et `[debut_b, fin_b[` se recoupent."""
    return debut_a < fin_b and debut_b < fin_a


def ecart(debut_a: int, fin_a: int, debut_b: int, fin_b: int) -> int:
    """Minutes libres entre deux plages d'un même jour (0 si elles se touchent ou se recoupent)."""
    return max(0, max(debut_a, debut_b) - min(fin_a, fin_b))


def _valider_minutes(minutes: int) -> None:
    if (
        not isinstance(minutes, int)
        or isinstance(minutes, bool)
        or not 0 <= minutes <= MINUTES_PAR_JOUR
    ):
        raise ErreurDonneesInvalides(
            f"Heure invalide : {minutes!r} (attendu : minutes depuis minuit, de 0 à 1440)."
        )
