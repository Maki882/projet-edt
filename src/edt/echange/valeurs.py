"""Lecture tolérante et écriture des valeurs d'une cellule (fonctions pures).

Une cellule vient d'un CSV (toujours du texte) ou d'un classeur XLSX (texte,
nombre, booléen, date, heure). Chaque fonction `lire_*` accepte les deux et
renvoie la valeur typée, ou `None` pour une cellule vide. Une valeur
illisible lève `ErreurDonneesInvalides` avec un message français ; l'appelant
y ajoute la position (fichier, ligne, colonne). **Aucune valeur n'est
corrigée** : soit elle se lit sans ambiguïté, soit elle est refusée.

Formats acceptés :

- dates : « 05/10/2026 », « 5/10/2026 », « 05-10-2026 », « 05.10.2026 »,
  ISO « 2026-10-05 » (éventuellement suivie de « 00:00:00 ») ;
- heures : « 8h30 », « 8 h 30 », « 08:30 », « 8:30 », « 18 h », « 08:30:00 » ;
- durées : « 2 h », « 1 h 30 », « 1h30 », « 90 min », « 01:30 » (un nombre
  seul est refusé : l'unité serait devinée) ;
- booléens : oui/non, vrai/faux, 1/0 (et true/false) ;
- jours : lundi ... dimanche, ou leurs trois premières lettres ;
- listes : valeurs séparées par un point-virgule ou une virgule ;
- codes : espaces de début et de fin retirés, espaces multiples réduits ;
  la comparaison de deux codes ignore la casse (`cle_code`).

Pièges de tableur reconnus et expliqués (jamais convertis en silence) : une
date ou une heure dans une colonne de codes (« 1-2 » devenu le 2 janvier),
une date ou une heure enregistrée comme nombre de série (46300, 0,354166),
un entier hors limites (`ENTIER_MAX`). Les caractères de contrôle invisibles
(`caracteres_de_controle`) sont signalés par la lecture des lignes ; le
classeur XLSX les échappe en `_xHHHH_` (`echapper_controle`) et la lecture
les restitue (`restituer_controle`), pour un aller-retour exact.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from collections.abc import Iterable
from enum import StrEnum
from typing import Any

from edt.domaine.enumerations import JOURS
from edt.domaine.heures import (
    MINUTES_PAR_JOUR,
    duree_vers_texte,
    minutes_vers_hhmm,
    texte_vers_minutes,
)
from edt.erreurs import ErreurDonneesInvalides

_ESPACES = re.compile(r"\s+")
_DATE_FR = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")
_DATE_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ]00:00(?::00)?)?$")
_HEURE_SECONDES = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})$")
_DUREE_HEURES = re.compile(r"^(\d{1,3})\s*h\s*(?:(\d{1,2})\s*(?:min)?)?$", re.IGNORECASE)
_DUREE_MINUTES = re.compile(r"^(\d{1,4})\s*min$", re.IGNORECASE)
_DUREE_HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")
_ENTIER = re.compile(r"^[+-]?\d+$")
_NOMBRE_DECIMAL = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")
_CONTROLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_CONTROLE_ECHAPPE = re.compile(r"_x(000[0-8]|000[bBcCeEfF]|001[0-9a-fA-F])_")

ENTIER_MAX = 1_000_000
"""Plus grand entier admis dans une cellule, en valeur absolue (capacités, effectifs, volumes)."""
_SEPARATEURS_LISTE = re.compile(r"[;,\n]")

_NOMBRE = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")
DEBUTS_FORMULE = ("=", "+", "-", "@", chr(9), chr(13))
"""Débuts de texte qu'un tableur interprète comme une formule."""

VRAI = frozenset({"oui", "vrai", "1", "true"})
FAUX = frozenset({"non", "faux", "0", "false"})


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #


def sans_accents(texte: str) -> str:
    """Retire les accents : « Électronique » donne « Electronique »."""
    decompose = unicodedata.normalize("NFKD", texte)
    return "".join(c for c in decompose if not unicodedata.combining(c))


def cle_code(texte: str) -> str:
    """Clé de comparaison d'un code ou d'un identifiant : sans espaces superflus, sans casse."""
    return _ESPACES.sub(" ", texte).strip().casefold()


def cle_mot(texte: str) -> str:
    """Clé de comparaison d'un mot (en-tête, valeur d'une liste fermée, jour).

    Sans accents, sans casse, espaces et tirets remplacés par « _ » :
    « Demi-groupe » et « demi groupe » donnent « demi_groupe ».
    """
    return re.sub(r"[\s\-']+", "_", sans_accents(texte).strip().casefold()).strip("_")


def texte_cellule(valeur: Any) -> str | None:
    """Texte d'une cellule, sans espaces de début et de fin ; `None` si elle est vide.

    Un nombre entier venu d'un classeur (101.0) s'écrit sans décimale (« 101 »).
    """
    if valeur is None:
        return None
    if isinstance(valeur, bool):
        texte = "oui" if valeur else "non"
    elif isinstance(valeur, float) and valeur.is_integer():
        texte = str(int(valeur))
    elif isinstance(valeur, dt.datetime):
        texte = valeur.isoformat(sep=" ")
    elif isinstance(valeur, (dt.date, dt.time)):
        texte = valeur.isoformat()
    else:
        texte = str(valeur)
    texte = texte.replace("\u00a0", " ").strip()
    return texte or None


# --------------------------------------------------------------------------- #
# Lecture
# --------------------------------------------------------------------------- #


def lire_texte(valeur: Any) -> str | None:
    """Texte libre (libellé, motif) : seuls les espaces de début et de fin sont retirés."""
    return texte_cellule(valeur)


def _refuser_date_du_tableur(valeur: Any) -> None:
    """Une date ou une heure dans une colonne de codes vient d'une conversion du tableur."""
    if isinstance(valeur, (dt.date, dt.time)):
        quoi = (
            f"une heure ({valeur:%H:%M})"
            if isinstance(valeur, dt.time)
            else f"une date ({valeur:%d/%m/%Y})"
        )
        raise ErreurDonneesInvalides(
            f"La cellule contient {quoi} au lieu d'un code : le tableur a converti la saisie "
            "(« 1-2 » devient le 2 janvier). Mettre la colonne au format Texte et ressaisir le code."
        )


def lire_code(valeur: Any) -> str | None:
    """Code ou identifiant : espaces de début et de fin retirés, espaces multiples réduits."""
    _refuser_date_du_tableur(valeur)
    texte = texte_cellule(valeur)
    return None if texte is None else _ESPACES.sub(" ", texte)


def lire_entier(valeur: Any) -> int | None:
    """Nombre entier : « 40 », « 1 200 », ou un nombre entier venu d'un classeur."""
    if valeur is None:
        return None
    if isinstance(valeur, bool):
        raise ErreurDonneesInvalides(f"Nombre entier attendu, pas {texte_cellule(valeur)!r}.")
    if isinstance(valeur, int):
        return _borner_entier(valeur)
    if isinstance(valeur, float):
        if valeur.is_integer():
            return _borner_entier(int(valeur))
        raise ErreurDonneesInvalides(f"Nombre entier attendu, pas {valeur!r}.")
    texte = texte_cellule(valeur)
    if texte is None:
        return None
    compact = texte.replace(" ", "")
    if not _ENTIER.match(compact):
        raise ErreurDonneesInvalides(f"Nombre entier attendu, pas {texte!r}.")
    if len(compact.lstrip("+-")) > 15:  # au-delà, inutile de convertir : hors limites
        raise ErreurDonneesInvalides(_message_hors_limites(texte))
    return _borner_entier(int(compact))


def _borner_entier(nombre: int) -> int:
    if abs(nombre) > ENTIER_MAX:
        raise ErreurDonneesInvalides(_message_hors_limites(str(nombre)))
    return nombre


def _message_hors_limites(texte: str) -> str:
    apercu = texte if len(texte) <= 30 else texte[:27] + "..."
    return f"Nombre hors limites : {apercu} (au plus {ENTIER_MAX:,} en valeur absolue).".replace(
        ",", " "
    )


def lire_booleen(valeur: Any) -> bool | None:
    """Oui ou non : « oui »/« non », « vrai »/« faux », « 1 »/« 0 »."""
    if valeur is None:
        return None
    if isinstance(valeur, bool):
        return valeur
    if isinstance(valeur, (int, float)) and valeur in (0, 1):
        return bool(valeur)
    texte = texte_cellule(valeur)
    if texte is None:
        return None
    mot = cle_mot(texte)
    if mot in VRAI:
        return True
    if mot in FAUX:
        return False
    raise ErreurDonneesInvalides(
        f"Valeur oui/non attendue, pas {texte!r} (accepté : oui/non, vrai/faux, 1/0)."
    )


def lire_date(valeur: Any) -> dt.date | None:
    """Date : « JJ/MM/AAAA », ISO « AAAA-MM-JJ », ou une date de classeur."""
    if valeur is None:
        return None
    if isinstance(valeur, dt.datetime):
        if valeur.time() != dt.time(0, 0):
            raise ErreurDonneesInvalides(
                f"Date attendue, pas une date avec une heure ({valeur:%d/%m/%Y %H:%M})."
            )
        return valeur.date()
    if isinstance(valeur, dt.date):
        return valeur
    if isinstance(valeur, dt.time):
        raise ErreurDonneesInvalides(f"Date attendue, pas une heure ({valeur:%H:%M}).")
    texte = texte_cellule(valeur)
    if texte is None:
        return None
    if isinstance(valeur, (int, float)) or _NOMBRE_DECIMAL.match(texte):
        raise ErreurDonneesInvalides(
            f"Date attendue, pas le nombre {texte} : le tableur a enregistré la date comme un "
            "nombre de série. Mettre la colonne au format Date (JJ/MM/AAAA) et ressaisir la date."
        )
    correspondance = _DATE_FR.match(texte)
    if correspondance:
        jour, mois, annee = (int(x) for x in correspondance.groups())
    else:
        correspondance = _DATE_ISO.match(texte)
        if not correspondance:
            raise ErreurDonneesInvalides(
                f"Date invalide : {texte!r} (formats acceptés : « 05/10/2026 », « 2026-10-05 »)."
            )
        annee, mois, jour = (int(x) for x in correspondance.groups())
    try:
        return dt.date(annee, mois, jour)
    except ValueError:
        raise ErreurDonneesInvalides(f"Date inexistante : {texte!r}.") from None


def lire_heure(valeur: Any) -> int | None:
    """Heure en minutes depuis minuit : « 8h30 », « 8 h 30 », « 08:30 », ou une heure de classeur."""
    if valeur is None:
        return None
    if isinstance(valeur, dt.time):
        if valeur.second or valeur.microsecond:
            raise ErreurDonneesInvalides(f"Heure avec des secondes : {valeur.isoformat()}.")
        return valeur.hour * 60 + valeur.minute
    if isinstance(valeur, (dt.date, bool)):
        raise ErreurDonneesInvalides(
            f"Heure attendue, pas {texte_cellule(valeur)!r} (formats acceptés : « 8h30 », « 08:30 »)."
        )
    texte = texte_cellule(valeur)
    if texte is None:
        return None
    # Un nombre n'est une heure que s'il est une heure entière (« 8 » comme « 8 h ») ;
    # sinon c'est le plus souvent une heure enregistrée en fraction de journée (0,354166).
    if _NOMBRE_DECIMAL.match(texte) and not texte_est_une_heure(texte):
        raise ErreurDonneesInvalides(
            f"Heure attendue, pas le nombre {texte} : le tableur a enregistré l'heure comme une "
            "fraction de journée. Mettre la colonne au format Heure (08:30) et ressaisir l'heure."
        )
    secondes = _HEURE_SECONDES.match(texte)
    if secondes:
        if int(secondes.group(3)):
            raise ErreurDonneesInvalides(f"Heure avec des secondes : {texte!r}.")
        texte = f"{secondes.group(1)}:{secondes.group(2)}"
    return texte_vers_minutes(texte)


def texte_est_une_heure(texte: str) -> bool:
    """Vrai pour une heure entière écrite seule (« 8 », « 18 ») : un nombre qui reste une heure."""
    return texte.isdigit() and int(texte) <= 24


def lire_duree(valeur: Any) -> int | None:
    """Durée en minutes : « 2 h », « 1 h 30 », « 90 min », « 01:30 », ou une durée de classeur."""
    if valeur is None:
        return None
    if isinstance(valeur, dt.time):
        return valeur.hour * 60 + valeur.minute
    texte = texte_cellule(valeur)
    if texte is None:
        return None
    if isinstance(valeur, (int, float)) or _ENTIER.match(texte):
        raise ErreurDonneesInvalides(
            f"Durée sans unité : {texte!r}. Préciser l'unité : « 2 h », « 1 h 30 » ou « 90 min »."
        )
    for motif, en_heures in ((_DUREE_HEURES, True), (_DUREE_HHMM, True), (_DUREE_MINUTES, False)):
        correspondance = motif.match(texte)
        if correspondance:
            if en_heures:
                heures, minutes = int(correspondance.group(1)), int(correspondance.group(2) or 0)
                if minutes > 59:
                    break
                return heures * 60 + minutes
            return int(correspondance.group(1))
    raise ErreurDonneesInvalides(
        f"Durée invalide : {texte!r} (formats acceptés : « 2 h », « 1 h 30 », « 90 min »)."
    )


def lire_jour(valeur: Any) -> int | None:
    """Jour de la semaine (0 = lundi) : « lundi », « Mardi », « mer »."""
    texte = texte_cellule(valeur)
    if texte is None:
        return None
    mot = cle_mot(texte).rstrip(".")
    for numero, nom in enumerate(JOURS):
        if mot == nom or (len(mot) == 3 and nom.startswith(mot)):
            return numero
    raise ErreurDonneesInvalides(f"Jour invalide : {texte!r} (attendu : lundi ... dimanche).")


def lire_choix(valeur: Any, enumeration: type[StrEnum]) -> StrEnum | None:
    """Valeur d'une liste fermée, reconnue par sa valeur ou son libellé, sans casse ni accents."""
    texte = texte_cellule(valeur)
    if texte is None:
        return None
    mot = cle_mot(texte)
    for membre in enumeration:
        libelle = getattr(membre, "libelle", membre.value)
        if mot in (cle_mot(membre.value), cle_mot(libelle)):
            return membre
    permis = ", ".join(membre.value for membre in enumeration)
    raise ErreurDonneesInvalides(f"Valeur {texte!r} inconnue (valeurs permises : {permis}).")


def lire_liste(valeur: Any) -> tuple[str, ...] | None:
    """Liste de codes séparés par « ; » ou « , » ; les doublons ne comptent qu'une fois."""
    _refuser_date_du_tableur(valeur)
    texte = texte_cellule(valeur)
    if texte is None:
        return None
    vus: dict[str, str] = {}
    for morceau in _SEPARATEURS_LISTE.split(texte):
        code = _ESPACES.sub(" ", morceau).strip()
        if code and cle_code(code) not in vus:
            vus[cle_code(code)] = code
    return tuple(vus.values()) or None


def caracteres_de_controle(texte: str) -> list[str]:
    """Caractères de contrôle invisibles d'un texte, en notation « U+0001 » (sans doublon).

    La tabulation et les fins de ligne sont des caractères ordinaires d'une
    cellule ; les autres caractères de contrôle ne s'affichent pas et sont
    interdits dans un classeur.
    """
    return sorted({f"U+{ord(c):04X}" for c in _CONTROLE.findall(texte)})


def echapper_controle(texte: str) -> str:
    """Caractères de contrôle -> « _x0001_ », la notation du format XLSX (Excel fait de même)."""
    return _CONTROLE.sub(lambda m: f"_x{ord(m.group(0)):04X}_", texte)


def restituer_controle(texte: str) -> str:
    """Inverse d'`echapper_controle`, pour un texte lu dans un classeur."""
    if "_x" not in texte:
        return texte
    return _CONTROLE_ECHAPPE.sub(lambda m: chr(int(m.group(1), 16)), texte)


# --------------------------------------------------------------------------- #
# Écriture
# --------------------------------------------------------------------------- #


def neutraliser_formule(valeur: Any) -> Any:
    """Un texte qui commence comme une formule de tableur n'en devient pas une.

    Un texte qui commence par « = », « + », « - », « @ », une tabulation ou un
    retour chariot reçoit une espace insécable en tête, que la relecture
    retire (`texte_cellule`) : ouvrir un export dans un tableur n'exécute
    rien. Un nombre (« -1 ») et toute autre valeur restent tels quels.
    """
    if isinstance(valeur, str) and valeur.startswith(DEBUTS_FORMULE) and not _NOMBRE.match(valeur):
        return "\u00a0" + valeur
    return valeur


def ecrire_date(valeur: dt.date | None) -> str:
    """« 05/10/2026 »."""
    return "" if valeur is None else valeur.strftime("%d/%m/%Y")


def ecrire_heure(minutes: int | None) -> str:
    """« 08:30 » (24:00 pour la fin de journée)."""
    return "" if minutes is None else minutes_vers_hhmm(minutes)


def ecrire_duree(minutes: int | None) -> str:
    """« 2 h », « 1 h 30 », « 45 min »."""
    if minutes is None:
        return ""
    if not 0 <= minutes <= 100 * MINUTES_PAR_JOUR:
        raise ErreurDonneesInvalides(f"Durée invalide : {minutes} minutes.")
    return duree_vers_texte(minutes)


def ecrire_booleen(valeur: bool | None) -> str:
    """« oui » ou « non »."""
    if valeur is None:
        return ""
    return "oui" if valeur else "non"


def ecrire_jour(jour: int | None) -> str:
    """« lundi »."""
    return "" if jour is None else JOURS[jour]


def ecrire_liste(codes: Iterable[str] | None) -> str:
    """« A1-G1; A1-G2 »."""
    return "" if codes is None else "; ".join(codes)
