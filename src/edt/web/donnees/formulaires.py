"""Passage entre les descriptions d'entités et les formulaires HTML.

- `saisies_formulaire` : lit un formulaire envoyé et en tire les saisies à
  passer aux services (`edt.services.referentiels.operations`) ;
- `champs_affiches` : prépare, pour le gabarit, chaque champ à afficher
  (contrôle HTML, valeur, choix, erreur, condition d'affichage), regroupés
  par rubrique.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from edt.services.referentiels.conversion import valeur_de_saisie
from edt.services.referentiels.description import Champ, Entite, TypeChamp, options_reference
from edt.services.referentiels.operations import longueur_max

TYPES_HTML = {
    TypeChamp.TEXTE: "text",
    TypeChamp.TEXTE_LONG: "textarea",
    TypeChamp.ENTIER: "number",
    TypeChamp.BOOLEEN: "checkbox",
    TypeChamp.CHOIX: "select",
    TypeChamp.REFERENCE: "select",
    TypeChamp.REFERENCES: "multiple",
    TypeChamp.DATE: "date",
    TypeChamp.HEURE: "time",
    TypeChamp.DUREE: "text",
    TypeChamp.COULEUR: "color",
}


@dataclass(frozen=True)
class ChampAffiche:
    """Un champ prêt pour la macro `ui.champ`."""

    nom: str
    libelle: str
    type: str
    valeur: Any
    requis: bool
    aide: str
    erreur: str | None
    options: tuple[tuple[str, str], ...] = ()
    attributs: dict[str, Any] = field(default_factory=dict)
    unite: str | None = None
    conteneur: dict[str, Any] = field(default_factory=dict)
    large: bool = False


@dataclass(frozen=True)
class RubriqueChamps:
    """Champs d'une même rubrique du formulaire (`titre` vide : sans titre).

    `erreur` : message pour la rubrique entière, affiché sous son titre ;
    `conteneur` : attributs du `fieldset` (par exemple `data-visible-si`).
    """

    titre: str
    champs: tuple[ChampAffiche, ...]
    erreur: str | None = None
    conteneur: dict[str, Any] = field(default_factory=dict)


def saisies_formulaire(entite: Entite, formulaire: FormData) -> dict[str, Any]:
    """Saisies d'un formulaire pour les champs de l'entité.

    Une case décochée est absente du formulaire : elle vaut « non ». Un choix
    multiple sans case cochée vaut la liste vide. Un champ masqué (désactivé
    par le navigateur) est absent : le service lui donne sa valeur par défaut.
    """
    saisies: dict[str, Any] = {}
    for champ in entite.champs:
        if champ.type is TypeChamp.BOOLEEN:
            saisies[champ.nom] = "1" if champ.nom in formulaire else ""
        elif champ.type is TypeChamp.REFERENCES:
            saisies[champ.nom] = formulaire.getlist(champ.nom)
        elif champ.nom in formulaire:
            saisies[champ.nom] = formulaire[champ.nom]
    return saisies


def _options(session: Session, champ: Champ, valeur: Any) -> tuple[tuple[str, str], ...]:
    if champ.reference is not None:
        choix = options_reference(session, champ.reference)
    else:
        choix = [(str(cle), libelle) for cle, libelle in champ.choix]
    if champ.type is TypeChamp.REFERENCES:
        retenus = set(valeur or [])
        return tuple(sorted(choix, key=lambda option: option[0] not in retenus))
    vide = "Choisir..." if champ.obligatoire else "(aucun)"
    if champ.obligatoire and valeur not in ("", None):
        return tuple(choix)
    return (("", vide), *choix)


def _attributs(entite: Entite, champ: Champ) -> dict[str, Any]:
    attributs: dict[str, Any] = {}
    if champ.type is TypeChamp.ENTIER:
        attributs.update(step=1, min=champ.minimum, max=champ.maximum)
    elif champ.type in (TypeChamp.TEXTE, TypeChamp.TEXTE_LONG):
        attributs["maxlength"] = longueur_max(entite, champ)
    elif champ.type is TypeChamp.DUREE:
        attributs.update(placeholder="2 h", inputmode="text")
    return {cle: valeur for cle, valeur in attributs.items() if valeur is not None}


def champs_affiches(
    session: Session,
    entite: Entite,
    valeurs: Mapping[str, Any],
    erreurs: Mapping[str, str] | None = None,
    *,
    noms: tuple[str, ...] | None = None,
) -> tuple[RubriqueChamps, ...]:
    """Champs de l'entité à afficher, regroupés par rubrique, dans l'ordre de déclaration.

    `valeurs` : valeurs canoniques (élément existant) ou saisies brutes
    (formulaire réaffiché) ; `noms` : restreint aux champs cités (ajout rapide).
    """
    erreurs = erreurs or {}
    rubriques: dict[str, list[ChampAffiche]] = {}
    for champ in entite.champs:
        if noms is not None and champ.nom not in noms:
            continue
        valeur = valeur_de_saisie(champ, valeurs.get(champ.nom))
        conteneur = {}
        if champ.visible_si is not None:
            nom, attendues = champ.visible_si
            conteneur["data-visible-si"] = f"{nom}:{','.join(attendues)}"
        affiche = ChampAffiche(
            nom=champ.nom,
            libelle=champ.libelle,
            type=TYPES_HTML[champ.type],
            valeur=valeur,
            requis=champ.obligatoire,
            aide=champ.aide,
            erreur=erreurs.get(champ.nom),
            options=_options(session, champ, valeur)
            if champ.type in (TypeChamp.CHOIX, TypeChamp.REFERENCE, TypeChamp.REFERENCES)
            else (),
            attributs=_attributs(entite, champ),
            unite=champ.unite or None,
            conteneur=conteneur,
            large=champ.large or champ.type is TypeChamp.TEXTE_LONG,
        )
        rubriques.setdefault(champ.groupe, []).append(affiche)
    return tuple(RubriqueChamps(titre, tuple(liste)) for titre, liste in rubriques.items())
