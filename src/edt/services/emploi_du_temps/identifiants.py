"""Identifiants venus de l'extérieur (adresse, formulaire, cookie) : vérifiés avant la base.

SQLite range ses identifiants sur 64 bits signés : un entier hors de
`[1, 2**63 - 1]` (`edt.db.moteur.IDENTIFIANT_MAX`) ne peut désigner aucune
ligne, et le donner à la base lève `OverflowError` (une erreur 500 pour un lien
mal copié). Les services de ces écrans vérifient donc chaque identifiant reçu
avec la garde commune du socle, `edt.db.moteur.identifiant_valide` : hors
bornes, c'est « introuvable », exactement comme un identifiant inconnu
(`exiger_identifiant`, `obtenir`).

Côté adresse, une valeur vide (`?semestre_id=`, l'option « Tous les
semestres » d'un sélecteur) vaut « non précisé » au lieu d'une saisie
invalide (`EntierFacultatif`).

Un nombre écrit dans un texte (`seance=h12`, `niveau_regle_12`, `salle_id`,
une durée) est lu par `entier_ecrit` : chiffres ASCII seulement, au plus
`CHIFFRES_MAX` chiffres significatifs (Python refuse de convertir plus de
4 300 chiffres, et `str.isdigit` accepte « ² », que `int()` refuse). Une
saisie recopiée dans un message ou le journal passe par `apercu` : 5 000
caractères reçus n'y sont pas recopiés.
"""

from __future__ import annotations

from typing import Annotated, Any, TypeVar

from pydantic import BeforeValidator
from sqlalchemy.orm import Session

from edt.db.moteur import identifiant_valide
from edt.erreurs import ErreurIntrouvable

CHIFFRES_MAX = 30
"""Au-delà, un nombre écrit ne désigne rien et n'est même pas converti (même borne que les
adresses, `edt.web.gabarits.CHIFFRES_ADRESSE_MAX`, et les saisies de l'espace Données)."""

APERCU_MAX = 40
"""Longueur au-delà de laquelle une saisie recopiée dans un message ou le journal est coupée."""

T = TypeVar("T")


def exiger_identifiant(ident: int, introuvable: str) -> int:
    """`ident` s'il peut exister ; sinon `ErreurIntrouvable(introuvable)`, sans interroger la base."""
    if not identifiant_valide(ident):
        raise ErreurIntrouvable(introuvable)
    return ident


def obtenir(session: Session, modele: type[T], ident: object) -> T | None:
    """`session.get(modele, ident)`, ou `None` pour un identifiant qui ne peut pas exister."""
    return session.get(modele, ident) if identifiant_valide(ident) else None


def entier_ecrit(texte: str) -> int | None:
    """L'entier naturel écrit en chiffres ASCII dans `texte`, ou `None` pour toute autre saisie.

    `None` aussi pour « ² » ou « ١٢ » (des chiffres pour `str.isdigit`, que `int()`
    refuse ou lit autrement), un signe, des espaces et plus de `CHIFFRES_MAX`
    chiffres significatifs. Ne lève jamais : à l'appelant de dire ce qu'il attendait.
    """
    if not (texte.isascii() and texte.isdigit()) or len(texte.lstrip("0")) > CHIFFRES_MAX:
        return None
    return int(texte)


def apercu(texte: str, longueur: int = APERCU_MAX) -> str:
    """`texte` coupé à `longueur` caractères (« … » à la fin), pour un message ou le journal."""
    return texte if len(texte) <= longueur else texte[: longueur - 1] + "…"


def vide_en_rien(valeur: Any) -> Any:
    """Une chaîne vide (ou d'espaces) devient `None` : « non précisé »."""
    if isinstance(valeur, str) and not valeur.strip():
        return None
    return valeur


EntierFacultatif = Annotated[int | None, BeforeValidator(vide_en_rien)]
"""Paramètre d'adresse entier facultatif, vide accepté comme « non précisé »."""
