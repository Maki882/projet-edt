"""Paramètres globaux de l'application : définition typée et valeurs par défaut.

`Parametres` est la source unique : chaque champ porte son type, sa valeur
par défaut et, en métadonnées, son libellé, sa description et ses bornes.
La lecture et l'écriture en base sont dans `edt.services.parametres` ;
l'instantané d'un semestre embarque un objet `Parametres` figé.

Pour ajouter un paramètre : ajouter un champ ici (avec `_champ`), c'est tout.
Il apparaît automatiquement dans la table `parametre` à la prochaine
initialisation et dans l'écran de configuration.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable

ValeurParametre = int | bool


def _champ(
    defaut: ValeurParametre,
    libelle: str,
    description: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    unite: str = "",
) -> Any:
    return field(
        default=defaut,
        metadata={
            "libelle": libelle,
            "description": description,
            "minimum": minimum,
            "maximum": maximum,
            "unite": unite,
        },
    )


@dataclass(frozen=True)
class Parametres:
    """Réglages globaux. Les heures sont en minutes depuis minuit."""

    temps_limite_s: int = _champ(
        60,
        "Temps limite d'un calcul",
        "Durée maximale d'une génération avant abandon.",
        minimum=1,
        maximum=900,
        unite="s",
    )
    temps_limite_long_s: int = _champ(
        300,
        "Temps limite d'une relance longue",
        "Durée maximale utilisée par le bouton « relancer 5 min ».",
        minimum=1,
        maximum=1800,
        unite="s",
    )
    nb_workers: int = _champ(
        0,
        "Fils de calcul du solveur",
        "Nombre de fils utilisés par le calcul ; 0 = deux au plus. Le calcul n'en utilise "
        "jamais plus que la machine n'a de cœurs.",
        minimum=0,
        maximum=8,
    )
    ouverture_min: int = _champ(
        8 * 60 + 30,
        "Ouverture",
        "Heure la plus matinale d'un début de séance.",
        minimum=0,
        maximum=1440,
        unite="heure",
    )
    fermeture_min: int = _champ(
        18 * 60,
        "Fermeture",
        "Heure la plus tardive d'une fin de séance.",
        minimum=0,
        maximum=1440,
        unite="heure",
    )
    pause_min: int = _champ(
        15,
        "Pause entre deux séances",
        "Durée minimale entre deux séances consécutives d'une même ressource.",
        minimum=0,
        maximum=120,
        unite="min",
    )
    dejeuner_debut_min: int = _champ(
        12 * 60 + 45,
        "Début du déjeuner",
        "Début de la plage laissée libre pour le déjeuner.",
        minimum=0,
        maximum=1440,
        unite="heure",
    )
    dejeuner_fin_min: int = _champ(
        13 * 60 + 45,
        "Fin du déjeuner",
        "Fin de la plage laissée libre pour le déjeuner.",
        minimum=0,
        maximum=1440,
        unite="heure",
    )
    trajet_defaut_min: int = _champ(
        60,
        "Trajet par défaut entre deux campus",
        "Temps de trajet appliqué quand la matrice des trajets ne dit rien pour deux campus.",
        minimum=0,
        maximum=600,
        unite="min",
    )
    accessibilite_inconnue_acceptee: bool = _champ(
        False,
        "Accepter les salles d'accessibilité inconnue",
        "Si non (défaut), une salle dont l'accessibilité est inconnue est traitée comme "
        "non accessible.",
    )
    objectif_actif: bool = _champ(
        False,
        "Optimiser la qualité de l'emploi du temps",
        "Si oui, le calcul minimise les trous, les changements de campus, le samedi et "
        "les créneaux extrêmes au lieu de s'arrêter à la première solution valide.",
    )
    poids_trous: int = _champ(
        10,
        "Poids des trous des groupes",
        "Poids des créneaux libres entre deux séances d'un groupe.",
        minimum=0,
        maximum=1000,
    )
    poids_changement_campus: int = _champ(
        5,
        "Poids des changements de campus",
        "Poids d'un changement de campus dans la journée.",
        minimum=0,
        maximum=1000,
    )
    poids_samedi: int = _champ(
        2,
        "Poids du samedi",
        "Poids d'une séance placée le samedi matin.",
        minimum=0,
        maximum=1000,
    )
    poids_creneaux_extremes: int = _champ(
        1,
        "Poids des créneaux extrêmes",
        "Poids d'une séance placée sur le premier ou le dernier créneau de la journée.",
        minimum=0,
        maximum=1000,
    )

    def __post_init__(self) -> None:
        for definition in definitions():
            _valider_valeur(definition, getattr(self, definition.cle))
        if self.ouverture_min >= self.fermeture_min:
            raise ErreurDonneesInvalides("L'heure d'ouverture doit précéder l'heure de fermeture.")
        if self.dejeuner_debut_min > self.dejeuner_fin_min:
            raise ErreurDonneesInvalides(
                "Le début du déjeuner doit précéder (ou égaler) la fin du déjeuner."
            )

    def avec(self, **changements: ValeurParametre) -> Parametres:
        """Copie modifiée (les valeurs sont validées)."""
        for cle in changements:
            definition(cle)
        return dataclasses.replace(self, **changements)


@dataclass(frozen=True)
class DefinitionParametre:
    """Description d'un paramètre pour l'interface et la validation."""

    cle: str
    type: type
    defaut: ValeurParametre
    libelle: str
    description: str
    minimum: int | None
    maximum: int | None
    unite: str


def definitions() -> tuple[DefinitionParametre, ...]:
    """Définitions de tous les paramètres, dans l'ordre de déclaration."""
    return _DEFINITIONS


def definition(cle: str) -> DefinitionParametre:
    """Définition du paramètre `cle` ; lève `ErreurIntrouvable` s'il n'existe pas."""
    for entree in _DEFINITIONS:
        if entree.cle == cle:
            return entree
    raise ErreurIntrouvable(f"Paramètre inconnu : {cle!r}.")


def valider(cle: str, valeur: object) -> ValeurParametre:
    """Vérifie le type et les bornes de `valeur` pour le paramètre `cle` et la renvoie."""
    return _valider_valeur(definition(cle), valeur)


def borner(cle: str, valeur: object) -> object:
    """Ramène un entier hors bornes à la borne la plus proche du paramètre `cle`.

    Sert à la lecture d'une valeur enregistrée avant un resserrement des
    bornes : elle ne doit pas rendre l'application inutilisable. Toute autre
    valeur est rendue telle quelle (`valider` dira si elle est acceptable).
    """
    entree = definition(cle)
    if entree.type is not int or not isinstance(valeur, int) or isinstance(valeur, bool):
        return valeur
    if entree.minimum is not None and valeur < entree.minimum:
        return entree.minimum
    if entree.maximum is not None and valeur > entree.maximum:
        return entree.maximum
    return valeur


def convertir_texte(cle: str, texte: str) -> ValeurParametre:
    """Convertit une saisie de formulaire (texte) vers le type du paramètre, puis la valide."""
    entree = definition(cle)
    brut = texte.strip().lower()
    if entree.type is bool:
        if brut in ("1", "true", "vrai", "oui", "on"):
            return True
        if brut in ("0", "false", "faux", "non", "off", ""):
            return False
        raise ErreurDonneesInvalides(
            f"{entree.libelle} : répondre par oui ou non (lu : {texte!r})."
        )
    try:
        nombre = int(brut)
    except ValueError as erreur:
        raise ErreurDonneesInvalides(
            f"{entree.libelle} : un nombre entier est attendu (lu : {texte!r})."
        ) from erreur
    return _valider_valeur(entree, nombre)


def _valider_valeur(entree: DefinitionParametre, valeur: object) -> ValeurParametre:
    if entree.type is bool:
        if not isinstance(valeur, bool):
            raise ErreurDonneesInvalides(
                f"{entree.libelle} : oui ou non attendu (lu : {valeur!r})."
            )
        return valeur
    if not isinstance(valeur, int) or isinstance(valeur, bool):
        raise ErreurDonneesInvalides(
            f"{entree.libelle} : un nombre entier est attendu (lu : {valeur!r})."
        )
    if entree.minimum is not None and valeur < entree.minimum:
        raise ErreurDonneesInvalides(
            f"{entree.libelle} : la valeur minimale est {entree.minimum} (lu : {valeur})."
        )
    if entree.maximum is not None and valeur > entree.maximum:
        raise ErreurDonneesInvalides(
            f"{entree.libelle} : la valeur maximale est {entree.maximum} (lu : {valeur})."
        )
    return valeur


_TYPES = {"int": int, "bool": bool}

_DEFINITIONS: tuple[DefinitionParametre, ...] = tuple(
    DefinitionParametre(
        cle=champ.name,
        type=_TYPES[champ.type] if isinstance(champ.type, str) else champ.type,
        defaut=champ.default,
        libelle=champ.metadata["libelle"],
        description=champ.metadata["description"],
        minimum=champ.metadata["minimum"],
        maximum=champ.metadata["maximum"],
        unite=champ.metadata["unite"],
    )
    for champ in dataclasses.fields(Parametres)
)
