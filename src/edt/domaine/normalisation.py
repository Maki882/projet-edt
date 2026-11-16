"""Normalisation des codes et identifiants saisis ou importés.

Deux codes qui ne diffèrent que par la forme Unicode (« é » précomposé ou
décomposé en « e » + accent) ou par des espaces invisibles (espace insécable
collé depuis un traitement de texte, tabulation, espace de largeur nulle)
désignent la même chose pour l'œil mais passeraient l'unicité de la base :
doublons invisibles, introuvables à la recherche. Les modèles ORM appellent
`normaliser_code` à chaque affectation d'un code ou d'un identifiant, donc à
la saisie comme à l'import.
"""

from __future__ import annotations

import unicodedata

_INVISIBLES = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)
"""Caractères de largeur nulle (dont la marque d'ordre des octets) : supprimés.

Ce ne sont pas des espaces pour Python (`isspace()` faux) : `str.split()` ne les retire pas.
Les espaces insécables, fines, idéographiques et les tabulations, eux, sont des espaces :
`str.split()` sans argument coupe dessus."""


def normaliser_code(valeur: str) -> str:
    """Forme canonique d'un code : NFC, sans caractère invisible, espaces réduits à un, bords nets.

    « A1 G2 » (deux espaces insécables) devient « A1 G2 », « école » avec un
    « e » suivi d'un accent combinant devient « école » précomposé, « N105 »
    précédé d'une espace de largeur nulle devient « N105 ». Une valeur qui
    n'est pas un texte est rendue telle quelle (la base la refusera).
    """
    if not isinstance(valeur, str):
        return valeur
    texte = unicodedata.normalize("NFC", valeur)
    return " ".join(texte.translate(_INVISIBLES).split())
