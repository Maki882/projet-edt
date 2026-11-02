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

_ESPACES_EXOTIQUES = dict.fromkeys(
    map(
        ord,
        "\u00a0\u202f\u2007\u2009\u200a\u2002\u2003\u2004\u2005\u2006\u2008\u3000\t",
    ),
    " ",
)
"""Espaces insécables, fines ou idéographiques et tabulations : ramenés à l'espace simple."""

_INVISIBLES = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)
"""Caractères de largeur nulle (dont la marque d'ordre des octets) : supprimés."""


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
    texte = texte.translate(_INVISIBLES).translate(_ESPACES_EXOTIQUES)
    return " ".join(texte.split())
