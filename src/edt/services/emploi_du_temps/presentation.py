"""Mises en forme françaises partagées par les écrans de l'emploi du temps.

Fonctions pures, sans base : durées en secondes, listes de semaines,
couleur stable d'une matière, libellés des contraintes. Les gabarits les
reçoivent sous le nom `fmt` (voir les routes) ; la ligne de commande peut
les réutiliser.
"""

from __future__ import annotations

import datetime as dt
import re
import zlib
from collections.abc import Iterable, Sequence

from edt.domaine.contraintes import CATALOGUE, CodeContrainte
from edt.domaine.enumerations import JOURS
from edt.domaine.heures import minutes_vers_texte

NB_COULEURS = 12
"""Nombre de teintes de la palette des matières (classes CSS `edt-couleur-0` à `-11`)."""

_MOTIF_COULEUR = re.compile(r"^#[0-9a-fA-F]{6}$")

MOIS = (
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


def indice_couleur(cle: str) -> int:
    """Indice de palette stable pour une matière (même code, même couleur, partout et toujours).

    On utilise CRC32 et non `hash()`, dont la valeur change d'un processus à l'autre.
    """
    return zlib.crc32(cle.encode("utf-8")) % NB_COULEURS


def couleur_valide(couleur: str | None) -> str | None:
    """La couleur si elle est au format `#rrggbb`, sinon `None` (jamais injectée telle quelle)."""
    if couleur and _MOTIF_COULEUR.match(couleur):
        return couleur
    return None


def duree_secondes(secondes: float | None) -> str:
    """Durée lisible : « moins d'une seconde », « 12 s », « 1 min 05 s », « 5 minutes »."""
    if secondes is None:
        return ""
    total = int(round(secondes))
    if total < 1:
        return "moins d'une seconde"
    if total < 60:
        return f"{total} s"
    minutes, reste = divmod(total, 60)
    if minutes < 60:
        if reste == 0:
            return f"{minutes} minute" + ("s" if minutes > 1 else "")
        return f"{minutes} min {reste:02d} s"
    heures, minutes = divmod(minutes, 60)
    return f"{heures} h {minutes:02d} min"


def plages_de_semaines(semaines: Iterable[int]) -> str:
    """Numéros de semaines regroupés en plages : « 2 à 5, 8 et 10 à 11 »."""
    numeros = sorted(set(semaines))
    if not numeros:
        return ""
    plages: list[tuple[int, int]] = []
    debut = fin = numeros[0]
    for numero in numeros[1:]:
        if numero == fin + 1:
            fin = numero
            continue
        plages.append((debut, fin))
        debut = fin = numero
    plages.append((debut, fin))
    textes = [str(a) if a == b else f"{a} à {b}" for a, b in plages]
    if len(textes) == 1:
        return textes[0]
    return ", ".join(textes[:-1]) + " et " + textes[-1]


def texte_semaines(semaines: Sequence[int], nb_semaines_semestre: int | None = None) -> str:
    """Phrase complète : « Semaines 2 à 5 et 8 (5 semaines sur 18) »."""
    nombre = len(set(semaines))
    if nombre == 0:
        return "Aucune semaine active"
    prefixe = "Semaine " if nombre == 1 else "Semaines "
    texte = prefixe + plages_de_semaines(semaines)
    if nb_semaines_semestre:
        texte += f" ({nombre} sur {nb_semaines_semestre})"
    return texte


def date_longue(jour: dt.date | None) -> str:
    """« lundi 5 octobre 2026 »."""
    if jour is None:
        return ""
    return f"{JOURS[jour.weekday()]} {jour.day} {MOIS[jour.month - 1]} {jour.year}"


def date_courte(jour: dt.date | None) -> str:
    """« 05/10 »."""
    if jour is None:
        return ""
    return jour.strftime("%d/%m")


def plage_horaire(debut: int, fin: int) -> str:
    """« 8 h 30 - 10 h 30 »."""
    return f"{minutes_vers_texte(debut)} - {minutes_vers_texte(fin)}"


def libelle_contrainte(code: str) -> str:
    """Libellé du catalogue pour un code de contrainte (le code lui-même s'il est inconnu)."""
    try:
        return CATALOGUE[CodeContrainte(code)].libelle
    except ValueError:
        return str(code)


def libelles_contraintes(codes: Iterable[str]) -> tuple[str, ...]:
    """Libellés de plusieurs contraintes, dans l'ordre du catalogue."""
    ordre = {code: rang for rang, code in enumerate(CATALOGUE)}
    tries = sorted(set(codes), key=lambda code: (ordre.get(code, len(ordre)), str(code)))
    return tuple(libelle_contrainte(code) for code in tries)


def pourcentage(valeur: float | None) -> str:
    """« 87,5 % » ; vide si la valeur n'a pas de sens."""
    if valeur is None:
        return ""
    texte = f"{valeur:.1f}".replace(".", ",")
    if texte.endswith(",0"):
        texte = texte[:-2]
    return f"{texte} %"


def pluriel(nombre: int, singulier: str, pluriel_: str | None = None) -> str:
    """« 1 séance », « 3 séances »."""
    mot = singulier if nombre in (0, 1) else (pluriel_ or singulier + "s")
    return f"{nombre} {mot}"
