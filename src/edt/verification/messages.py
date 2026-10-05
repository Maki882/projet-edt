"""Mise en forme des messages du vérificateur : jours, dates, heures, durées, listes, semaines.

Les messages s'adressent à un responsable de scolarité : jours en toutes
lettres (« mardi »), dates longues (« mardi 6 octobre 2026 »), heures au
format du module `edt.domaine.heures` (« 10 h 45 »), durées lisibles
(« 1 h 30 », « 15 min »), listes à la française (« A, B et C »).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence

from edt.domaine.enumerations import JOURS
from edt.domaine.heures import duree_vers_texte, minutes_vers_texte

MOIS: tuple[str, ...] = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)
"""Noms des mois, indexés à partir de 0 (janvier)."""


def nom_jour(jour: int) -> str:
    """Nom du jour de la semaine : 1 donne « mardi »."""
    return JOURS[jour]


def date_texte(jour: dt.date) -> str:
    """Date longue : « mardi 6 octobre 2026 », « jeudi 1er octobre 2026 »."""
    numero = "1er" if jour.day == 1 else str(jour.day)
    return f"{JOURS[jour.weekday()]} {numero} {MOIS[jour.month - 1]} {jour.year}"


def heure(minutes: int) -> str:
    """Heure en clair : 645 donne « 10 h 45 »."""
    return minutes_vers_texte(minutes)


def plage(debut: int, fin: int) -> str:
    """Plage horaire dans une phrase : « de 10 h 45 à 12 h 45 »."""
    return f"de {heure(debut)} à {heure(fin)}"


def duree(minutes: int) -> str:
    """Durée en clair : 90 donne « 1 h 30 », 15 donne « 15 min »."""
    return duree_vers_texte(minutes)


def enumerer(elements: Sequence[str], conjonction: str = "et") -> str:
    """Liste à la française : « A », « A et B », « A, B et C ».

    Lève `ValueError` sur une liste vide (erreur de programmation : un
    message ne doit jamais énumérer « rien »).
    """
    if not elements:
        raise ValueError("Énumération d'une liste vide dans un message du vérificateur.")
    if len(elements) == 1:
        return elements[0]
    return f"{', '.join(elements[:-1])} {conjonction} {elements[-1]}"


def semaines_texte(numeros: Iterable[int], article: bool = False) -> str:
    """Numéros de semaines regroupés en intervalles.

    `{3, 4, 5, 6, 9}` donne « semaines 3 à 6 et 9 » (« les semaines 3 à 6
    et 9 » avec `article=True`) ; `{4}` donne « semaine 4 » (« la semaine 4 »).
    """
    tries = sorted(set(numeros))
    if not tries:
        raise ValueError("Aucune semaine à décrire dans un message du vérificateur.")
    if len(tries) == 1:
        return f"{'la ' if article else ''}semaine {tries[0]}"
    morceaux: list[str] = []
    debut = precedent = tries[0]
    for numero in [*tries[1:], None]:
        if numero is not None and numero == precedent + 1:
            precedent = numero
            continue
        if precedent - debut >= 2:
            morceaux.append(f"{debut} à {precedent}")
        else:
            morceaux.extend(str(n) for n in range(debut, precedent + 1))
        if numero is not None:
            debut = precedent = numero
    return f"{'les ' if article else ''}semaines {enumerer(morceaux)}"


def majuscule(texte: str) -> str:
    """Met la première lettre en majuscule (début de phrase)."""
    return texte[:1].upper() + texte[1:]
