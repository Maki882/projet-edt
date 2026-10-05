"""Conversion des saisies vers les valeurs typées des champs, et retour au texte.

Une saisie peut venir d'un formulaire (texte, liste de textes) ou d'un appel
programmatique (API, assistant) avec des valeurs déjà typées : les deux sont
acceptés. Toute valeur refusée lève `ErreurChamp`, dont le message français
s'affiche à côté du champ.

Valeurs **canoniques** (celles que renvoient les services, sérialisables en
JSON) : entier, booléen, texte, valeur d'énumération (texte), identifiant
(entier), liste d'identifiants, date ISO (« 2026-09-07 »), heure et durée en
minutes.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from edt.domaine.heures import duree_vers_texte, minutes_vers_hhmm, texte_vers_minutes
from edt.erreurs import ErreurDonneesInvalides
from edt.services.referentiels.description import Champ, TypeChamp

VRAI = frozenset({"1", "true", "vrai", "oui", "on", "yes"})
FAUX = frozenset({"0", "false", "faux", "non", "off", "no", ""})

_DUREE_HEURES = re.compile(r"^(?P<h>\d{1,3})(?:h|:)(?P<m>\d{1,2})?(?:min|mn)?$")
_DUREE_MINUTES = re.compile(r"^(?P<m>\d{1,5})(?:min|mn|m)$")
_DUREE_DECIMALE = re.compile(r"^(?P<h>\d{1,3})(?:[.,](?P<f>\d{1,2}))?(?:h)?$")
_COULEUR = re.compile(r"^#[0-9a-fA-F]{6}$")

ENTIER_MAX = 2**63 - 1
"""Plus grand entier que la base (SQLite) sait enregistrer ; au-delà, refus clair."""


class ErreurChamp(ErreurDonneesInvalides):
    """Valeur refusée pour un champ précis (message court, affiché sous le champ)."""


def est_vide(valeur: object) -> bool:
    """Vrai pour une saisie absente : `None`, texte blanc, liste vide."""
    if valeur is None:
        return True
    if isinstance(valeur, str):
        return valeur.strip() == ""
    if isinstance(valeur, list | tuple | set | frozenset):
        return len(valeur) == 0
    return False


def lire_duree(texte: str) -> int:
    """Lit une durée saisie en minutes : « 2 h », « 1 h 30 », « 1:30 », « 90 min », « 1,5 ».

    Un nombre seul s'entend en heures (« 2 » donne 120).
    """
    brut = texte.strip().lower().replace(" ", "")
    correspondance = _DUREE_HEURES.match(brut)
    if correspondance:
        minutes = int(correspondance["m"] or 0)
        if minutes > 59:
            raise ErreurChamp("Minutes invalides : 59 au plus.")
        return int(correspondance["h"]) * 60 + minutes
    correspondance = _DUREE_MINUTES.match(brut)
    if correspondance:
        return int(correspondance["m"])
    correspondance = _DUREE_DECIMALE.match(brut)
    if correspondance:
        fraction = correspondance["f"] or "0"
        return round((int(correspondance["h"]) + int(fraction) / 10 ** len(fraction)) * 60)
    raise ErreurChamp("Durée invalide : écrire par exemple « 2 h », « 1 h 30 » ou « 90 min ».")


def lire_date(valeur: object) -> dt.date:
    """Lit une date : objet date, « 2026-09-07 » ou « 07/09/2026 »."""
    if isinstance(valeur, dt.datetime):
        return valeur.date()
    if isinstance(valeur, dt.date):
        return valeur
    texte = str(valeur).strip()
    for format_ in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(texte, format_).date()
        except ValueError:
            continue
    raise ErreurChamp("Date invalide : format attendu jj/mm/aaaa.")


def _entier(valeur: object, *, borner: bool = True) -> int:
    """Entier saisi ; `borner` refuse ce que la base ne sait pas enregistrer."""
    if isinstance(valeur, bool):
        raise ErreurChamp("Nombre entier attendu.")
    if isinstance(valeur, int):
        nombre = valeur
    else:
        texte = str(valeur).strip()
        chiffres = texte.removeprefix("-").removeprefix("+")
        # « ² » ou « ٣ » sont des chiffres pour Python, pas pour une saisie : refusés.
        if not (chiffres.isascii() and chiffres.isdigit()):
            raise ErreurChamp("Nombre entier attendu.")
        nombre = int(texte)
    if borner and abs(nombre) > ENTIER_MAX:
        raise ErreurChamp("Nombre beaucoup trop grand.")
    return nombre


def _bornes(champ: Champ, nombre: int) -> int:
    if champ.minimum is not None and nombre < champ.minimum:
        raise ErreurChamp(f"La valeur minimale est {texte_valeur(champ, champ.minimum)}.")
    if champ.maximum is not None and nombre > champ.maximum:
        raise ErreurChamp(f"La valeur maximale est {texte_valeur(champ, champ.maximum)}.")
    return nombre


def _liste(valeur: object) -> list[Any]:
    if isinstance(valeur, str):
        return [morceau for morceau in (m.strip() for m in valeur.split(",")) if morceau]
    if isinstance(valeur, Iterable):
        return [element for element in valeur if not est_vide(element)]
    return [valeur]


def convertir(session: Session, champ: Champ, valeur: object, longueur_max: int | None) -> Any:
    """Convertit une saisie pour `champ` ; lève `ErreurChamp` si elle est refusée.

    Une saisie vide donne `None` (`False` pour un booléen, `[]` pour une liste),
    ou l'erreur « obligatoire » si le champ l'est.
    """
    type_ = champ.type
    if type_ is TypeChamp.BOOLEEN:
        if isinstance(valeur, bool):
            return valeur
        texte = "" if valeur is None else str(valeur).strip().lower()
        if texte in VRAI:
            return True
        if texte in FAUX:
            return False
        raise ErreurChamp("Répondre par oui ou non.")

    if type_ is TypeChamp.REFERENCES:
        identifiants = list(dict.fromkeys(_entier(element) for element in _liste(valeur or [])))
        if not identifiants:
            if champ.obligatoire:
                raise ErreurChamp("Choisir au moins un élément.")
            return []
        reference = champ.reference
        assert reference is not None, f"Champ {champ.nom} : référence manquante"
        requete = select(reference.modele.id).where(reference.modele.id.in_(identifiants))
        if reference.condition is not None:
            requete = requete.where(reference.condition())
        trouves = set(session.scalars(requete))
        if len(trouves) != len(identifiants):
            raise ErreurChamp("Un des éléments choisis n'existe pas ou n'est pas permis.")
        return identifiants

    if est_vide(valeur):
        if champ.obligatoire:
            raise ErreurChamp("Ce champ est obligatoire.")
        return None

    if type_ in (TypeChamp.TEXTE, TypeChamp.TEXTE_LONG):
        texte = str(valeur).strip()
        if longueur_max is not None and len(texte) > longueur_max:
            raise ErreurChamp(f"{longueur_max} caractères au plus.")
        return texte

    if type_ is TypeChamp.ENTIER:
        # Bornes du champ d'abord (« la valeur maximale est 100000 »), puis celle de la base.
        nombre = _bornes(champ, _entier(valeur, borner=False))
        if abs(nombre) > ENTIER_MAX:
            raise ErreurChamp("Nombre beaucoup trop grand.")
        return nombre

    if type_ is TypeChamp.CHOIX:
        valeur_texte = valeur.value if isinstance(valeur, Enum) else str(valeur).strip()
        for cle, _libelle in champ.choix:
            if str(cle) == str(valeur_texte):
                return cle
        raise ErreurChamp("Choix invalide.")

    if type_ is TypeChamp.REFERENCE:
        ident = _entier(valeur)
        reference = champ.reference
        assert reference is not None, f"Champ {champ.nom} : référence manquante"
        requete = select(reference.modele.id).where(reference.modele.id == ident)
        if reference.condition is not None:
            requete = requete.where(reference.condition())
        if session.scalar(requete) is None:
            raise ErreurChamp("Élément introuvable ou non permis.")
        return ident

    if type_ is TypeChamp.DATE:
        return lire_date(valeur)

    if type_ is TypeChamp.HEURE:
        if isinstance(valeur, int) and not isinstance(valeur, bool):
            minutes = valeur
        else:
            try:
                minutes = texte_vers_minutes(str(valeur))
            except ErreurDonneesInvalides:
                raise ErreurChamp("Heure invalide : écrire par exemple « 08:30 ».") from None
        if not 0 <= minutes <= 24 * 60:
            raise ErreurChamp("Heure invalide.")
        return _bornes(champ, minutes)

    if type_ is TypeChamp.DUREE:
        if isinstance(valeur, int) and not isinstance(valeur, bool):
            minutes = valeur
        else:
            minutes = lire_duree(str(valeur))
        return _bornes(champ, minutes)

    if type_ is TypeChamp.COULEUR:
        texte = str(valeur).strip()
        if not _COULEUR.match(texte):
            raise ErreurChamp("Couleur invalide : format attendu « #2f6fad ».")
        return texte.lower()

    raise ErreurDonneesInvalides(f"Type de champ non pris en charge : {type_}.")


def canonique(champ: Champ, valeur: object) -> Any:
    """Valeur sérialisable (JSON) d'un champ, à partir de la valeur de l'objet."""
    if valeur is None:
        return [] if champ.type is TypeChamp.REFERENCES else None
    if champ.type is TypeChamp.REFERENCES:
        return [element if isinstance(element, int) else element.id for element in valeur]
    if isinstance(valeur, Enum):
        return valeur.value
    if isinstance(valeur, dt.date):
        return valeur.isoformat()
    return valeur


def texte_valeur(champ: Champ, valeur: object) -> str:
    """Texte lisible d'une valeur (heure « 8 h 30 » en « 08:30 », durée « 2 h »)."""
    if valeur is None:
        return ""
    if champ.type is TypeChamp.HEURE and isinstance(valeur, int):
        return minutes_vers_hhmm(valeur)
    if champ.type is TypeChamp.DUREE and isinstance(valeur, int):
        return duree_vers_texte(valeur)
    return str(valeur)


def valeur_de_saisie(champ: Champ, valeur: object) -> Any:
    """Valeur à replacer dans un contrôle de formulaire (texte, liste de textes ou booléen).

    Accepte une valeur canonique (affichage d'un élément existant) comme une
    saisie brute (formulaire réaffiché après une erreur).
    """
    if champ.type is TypeChamp.BOOLEEN:
        if isinstance(valeur, bool):
            return valeur
        return str(valeur or "").strip().lower() in VRAI
    if champ.type is TypeChamp.REFERENCES:
        return [str(element) for element in _liste(valeur or [])]
    if valeur is None:
        return ""
    if isinstance(valeur, Enum):
        return valeur.value
    if isinstance(valeur, dt.date):
        return valeur.isoformat()
    if isinstance(valeur, int) and champ.type in (TypeChamp.HEURE, TypeChamp.DUREE):
        return texte_valeur(champ, valeur)
    return str(valeur)
