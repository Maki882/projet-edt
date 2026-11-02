"""Identifiants venus de l'extérieur (adresse, formulaire, cookie) : vérifiés avant la base.

SQLite range ses identifiants sur 64 bits signés : un entier hors de
`[1, IDENTIFIANT_MAX]` ne peut désigner aucune ligne, et le donner à la base
lève `OverflowError` (une erreur 500 pour un lien mal copié). Les services de
ces écrans vérifient donc chaque identifiant reçu : hors bornes, c'est
« introuvable », exactement comme un identifiant inconnu (`exiger_identifiant`,
`obtenir`).

Côté adresse, une valeur vide (`?semestre_id=`, l'option « Tous les
semestres » d'un sélecteur) vaut « non précisé » au lieu d'une saisie
invalide (`EntierFacultatif`).
"""

from __future__ import annotations

from typing import Annotated, Any, TypeVar

from pydantic import BeforeValidator
from sqlalchemy.orm import Session

from edt.erreurs import ErreurIntrouvable

IDENTIFIANT_MAX = 2**63 - 1
"""Plus grand identifiant qu'une ligne SQLite puisse porter."""

T = TypeVar("T")


def identifiant_possible(ident: object) -> bool:
    """Vrai si `ident` peut désigner une ligne en base (entier de 1 à `IDENTIFIANT_MAX`)."""
    return isinstance(ident, int) and not isinstance(ident, bool) and 0 < ident <= IDENTIFIANT_MAX


def exiger_identifiant(ident: int, introuvable: str) -> int:
    """`ident` s'il peut exister ; sinon `ErreurIntrouvable(introuvable)`, sans interroger la base."""
    if not identifiant_possible(ident):
        raise ErreurIntrouvable(introuvable)
    return ident


def obtenir(session: Session, modele: type[T], ident: object) -> T | None:
    """`session.get(modele, ident)`, ou `None` pour un identifiant qui ne peut pas exister."""
    return session.get(modele, ident) if identifiant_possible(ident) else None


def vide_en_rien(valeur: Any) -> Any:
    """Une chaîne vide (ou d'espaces) devient `None` : « non précisé »."""
    if isinstance(valeur, str) and not valeur.strip():
        return None
    return valeur


EntierFacultatif = Annotated[int | None, BeforeValidator(vide_en_rien)]
"""Paramètre d'adresse entier facultatif, vide accepté comme « non précisé »."""
