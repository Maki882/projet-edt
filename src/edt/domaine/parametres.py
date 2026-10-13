"""Paramètres globaux de l'application : définition typée et valeurs par défaut.

`Parametres` est la source unique : chaque champ porte son type, sa valeur
par défaut et, en métadonnées, son libellé, sa description et ses bornes (ou
ses choix). La lecture et l'écriture en base sont dans
`edt.services.parametres` ; l'instantané d'un semestre embarque un objet
`Parametres` figé.

Pour ajouter un paramètre : ajouter un champ ici (avec `_champ`), c'est tout.
Il apparaît automatiquement dans la table `parametre` à la prochaine
initialisation et dans l'écran de configuration. Un paramètre **à choix**
(`choix=((valeur, libellé), ...)`) est une chaîne parmi une liste fermée ; sa
définition a `type == "choix"`. Un paramètre **ensemble de jours**
(`jours=True`) est un tuple trié de numéros de jour (0 = lundi), enregistré
en JSON comme une liste ; sa définition a `type == "jours"`.

Jours travaillés : `jours_travailles` est le **standard** des jours de cours
de l'établissement (lundi à vendredi par défaut). Un jour de la grille hors
du standard (le samedi) est un jour d'appoint, ouvert seulement par une
exception : la case « samedi autorisé » d'un type de séance ou une règle
personnalisée (`edt.domaine.regles`). Le dépliage, la contrainte `JOURS` et
les écrans lisent ce paramètre ; rien ne code plus « lundi à vendredi » en dur.

Pause déjeuner
--------------
`dejeuner_mode` choisit l'un des trois modes ; `dejeuner_debut_min` (X) et
`dejeuner_fin_min` (Y) bornent la pause dans les deux premiers :

- `fixe` (horaires déterminés) : la plage `[X, Y[` est réservée pour tous,
  aucune séance d'aucun groupe ni d'aucun enseignant ne la recoupe.
  `plage_dejeuner` vaut `(X, Y)`. Validation : X < Y, dans les heures d'ouverture.
- `libre` (pause libre bornée) : pour chaque groupe et chaque enseignant,
  chaque jour où il a au moins une séance qui recoupe la fenêtre `[X, Y]`, il
  existe un intervalle libre d'au moins `dejeuner_duree_min` (N) minutes
  consécutives inclus dans `[X, Y]` ; un jour sans séance dans la fenêtre est
  satisfait. Le moteur place cette pause où il veut. `fenetre_dejeuner` vaut
  `(X, Y, N)`. Validation : X + N ≤ Y, dans les heures d'ouverture.
- `aucun` (pas de pause) : rien n'est réservé ; X, Y et N sont conservés mais
  ignorés. `dejeuner_actif` est faux.

Le vérificateur, le solveur et les indicateurs ne lisent que `plage_dejeuner`,
`fenetre_dejeuner` et `dejeuner_actif`, jamais le mode en clair.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from typing import Any

from edt.domaine.enumerations import JOURS
from edt.domaine.heures import minutes_vers_texte
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable

ValeurParametre = int | bool | str | tuple[int, ...]

Choix = tuple[tuple[str, str], ...]
"""Valeurs permises d'un paramètre à choix : couples (valeur enregistrée, libellé affiché)."""

TYPE_CHOIX = "choix"
"""Valeur de `DefinitionParametre.type` pour un paramètre à choix."""

TYPE_JOURS = "jours"
"""Valeur de `DefinitionParametre.type` pour un ensemble de jours (numéros, 0 = lundi)."""

JOURS_TRAVAILLES_PAR_DEFAUT: tuple[int, ...] = (0, 1, 2, 3, 4)

DEJEUNER_FIXE = "fixe"
DEJEUNER_LIBRE = "libre"
DEJEUNER_AUCUN = "aucun"
MODES_DEJEUNER: Choix = (
    (DEJEUNER_FIXE, "Horaires déterminés"),
    (DEJEUNER_LIBRE, "Pause libre bornée"),
    (DEJEUNER_AUCUN, "Pas de pause"),
)


def _champ(
    defaut: ValeurParametre,
    libelle: str,
    description: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    unite: str = "",
    choix: Choix | None = None,
    jours: bool = False,
) -> Any:
    return field(
        default=defaut,
        metadata={
            "libelle": libelle,
            "description": description,
            "minimum": minimum,
            "maximum": maximum,
            "unite": unite,
            "choix": choix or (),
            "jours": jours,
        },
    )


@dataclass(frozen=True)
class Parametres:
    """Réglages globaux. Les heures sont en minutes depuis minuit."""

    temps_limite_s: int = _champ(
        300,
        "Temps limite d'un calcul",
        "Durée maximale d'un calcul : passé ce délai, il s'arrête et garde le meilleur emploi "
        "du temps trouvé.",
        minimum=1,
        maximum=900,
        unite="s",
    )
    temps_limite_long_s: int = _champ(
        900,
        "Temps limite d'une relance longue",
        "Durée maximale utilisée par le bouton de relance longue.",
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
    jours_travailles: tuple[int, ...] = _champ(
        JOURS_TRAVAILLES_PAR_DEFAUT,
        "Jours travaillés de l'établissement",
        "Les jours où les cours ont lieu en temps normal. Un autre jour de la grille (le "
        "samedi) n'est ouvert que par exception : un type de séance qui l'autorise ou une "
        "règle personnalisée.",
        jours=True,
    )
    pause_min: int = _champ(
        15,
        "Pause entre deux séances",
        "Durée minimale entre deux séances consécutives d'une même ressource.",
        minimum=0,
        maximum=120,
        unite="min",
    )
    dejeuner_mode: str = _champ(
        DEJEUNER_FIXE,
        "Pause déjeuner",
        "Horaires déterminés : la plage du début à la fin du déjeuner reste libre pour tous. "
        "Pause libre bornée : chaque groupe et chaque enseignant dispose, les jours où il a "
        "cours à ces heures, d'au moins la durée minimale de libre d'un seul tenant entre le "
        "début et la fin du déjeuner, où le calcul le décide. Pas de pause : rien n'est réservé.",
        choix=MODES_DEJEUNER,
    )
    dejeuner_debut_min: int = _champ(
        12 * 60 + 45,
        "Début du déjeuner",
        "Début de la plage réservée (horaires déterminés) ou de la fenêtre dans laquelle "
        "chacun prend sa pause (pause libre bornée).",
        minimum=0,
        maximum=1440,
        unite="heure",
    )
    dejeuner_fin_min: int = _champ(
        13 * 60 + 45,
        "Fin du déjeuner",
        "Fin de la plage réservée (horaires déterminés) ou de la fenêtre dans laquelle "
        "chacun prend sa pause (pause libre bornée).",
        minimum=0,
        maximum=1440,
        unite="heure",
    )
    dejeuner_duree_min: int = _champ(
        60,
        "Durée minimale de la pause libre",
        "Pause libre bornée seulement : minutes libres consécutives que chaque groupe et "
        "chaque enseignant doit avoir dans la fenêtre du déjeuner.",
        minimum=15,
        maximum=180,
        unite="min",
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
        True,
        "Optimiser la qualité de l'emploi du temps",
        "Si oui, une fois un emploi du temps valide trouvé, le calcul emploie le temps qui "
        "reste à réduire les trous, les changements de campus, le samedi et les créneaux "
        "extrêmes. Si non, il s'arrête à la première solution valide.",
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
        defauts = defauts_dejeuner(
            self.dejeuner_debut_min,
            self.dejeuner_fin_min,
            self.ouverture_min,
            self.fermeture_min,
            mode=self.dejeuner_mode,
            duree=self.dejeuner_duree_min,
        )
        if defauts:
            raise ErreurDonneesInvalides(" ".join(defauts.values()))

    @property
    def dejeuner_actif(self) -> bool:
        """Vrai si une pause déjeuner est exigée (horaires déterminés ou pause libre bornée)."""
        return self.dejeuner_mode != DEJEUNER_AUCUN

    @property
    def plage_dejeuner(self) -> tuple[int, int] | None:
        """Plage `[début, fin[` réservée au déjeuner (horaires déterminés), sinon `None`."""
        if self.dejeuner_mode != DEJEUNER_FIXE:
            return None
        return self.dejeuner_debut_min, self.dejeuner_fin_min

    @property
    def fenetre_dejeuner(self) -> tuple[int, int, int] | None:
        """`(début, fin, durée minimale)` de la pause libre bornée, sinon `None`."""
        if self.dejeuner_mode != DEJEUNER_LIBRE:
            return None
        return self.dejeuner_debut_min, self.dejeuner_fin_min, self.dejeuner_duree_min

    def avec(self, **changements: ValeurParametre) -> Parametres:
        """Copie modifiée (les valeurs sont validées)."""
        for cle in changements:
            definition(cle)
        return dataclasses.replace(self, **changements)


@dataclass(frozen=True)
class DefinitionParametre:
    """Description d'un paramètre pour l'interface et la validation.

    `type` vaut `int`, `bool`, la chaîne `"choix"` (`choix` donne alors les
    couples valeur / libellé permis) ou la chaîne `"jours"` (ensemble de
    numéros de jour, 0 = lundi).
    """

    cle: str
    type: type | str
    defaut: ValeurParametre
    libelle: str
    description: str
    minimum: int | None
    maximum: int | None
    unite: str
    choix: Choix = ()


def defauts_dejeuner(
    debut: int,
    fin: int,
    ouverture: int,
    fermeture: int,
    *,
    mode: str = DEJEUNER_FIXE,
    duree: int = 60,
) -> dict[str, str]:
    """Défauts des réglages du déjeuner, par paramètre fautif, selon le mode.

    Horaires déterminés : la plage doit être non vide et dans les heures
    d'ouverture. Pause libre bornée : la fenêtre doit être dans les heures
    d'ouverture et assez longue pour la durée minimale. Pas de pause : rien
    à vérifier. Renvoie `{clé du paramètre : message}` (au plus un message
    par paramètre), vide si tout convient. Sert à la validation de
    `Parametres` et aux formulaires, qui affichent chaque message sous son champ.
    """
    defauts: dict[str, str] = {}
    if mode == DEJEUNER_AUCUN:
        return defauts
    if debut < ouverture:
        defauts["dejeuner_debut_min"] = (
            f"Le déjeuner ne peut pas commencer avant l'ouverture ({minutes_vers_texte(ouverture)})."
        )
    if debut >= fin:
        defauts["dejeuner_fin_min"] = (
            f"La fin du déjeuner doit venir après son début ({minutes_vers_texte(debut)})."
        )
    elif fin > fermeture:
        defauts["dejeuner_fin_min"] = (
            f"Le déjeuner ne peut pas finir après la fermeture ({minutes_vers_texte(fermeture)})."
        )
    if mode == DEJEUNER_LIBRE and debut < fin and debut + duree > fin:
        defauts["dejeuner_duree_min"] = (
            f"Une pause de {duree} min ne tient pas dans la fenêtre du déjeuner "
            f"({minutes_vers_texte(debut)} - {minutes_vers_texte(fin)}, soit {fin - debut} min)."
        )
    return defauts


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
    """Vérifie le type et les bornes (ou les choix) de `valeur` pour `cle` et la renvoie."""
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
    """Convertit une saisie de formulaire (texte) vers le type du paramètre, puis la valide.

    Un paramètre à choix reçoit la valeur telle quelle (espaces autour retirés).
    """
    entree = definition(cle)
    if entree.type == TYPE_CHOIX:
        return _valider_valeur(entree, texte.strip())
    if entree.type == TYPE_JOURS:
        return _valider_valeur(entree, _jours_depuis_texte(entree, texte))
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


def _jours_depuis_texte(entree: DefinitionParametre, texte: str) -> tuple[int, ...]:
    """Lit « lundi, mardi » ou « 0,1 » (séparateurs : virgule, point-virgule, espace)."""
    noms = {nom: numero for numero, nom in enumerate(JOURS)}
    jours = []
    for mot in re.split(r"[,;\s]+", texte.strip().lower()):
        if not mot:
            continue
        if mot in noms:
            jours.append(noms[mot])
        elif mot.isdigit():
            jours.append(int(mot))
        else:
            raise ErreurDonneesInvalides(
                f"{entree.libelle} : jour inconnu « {mot} » (attendu : " + ", ".join(JOURS) + ")."
            )
    return tuple(jours)


def _valider_valeur(entree: DefinitionParametre, valeur: object) -> ValeurParametre:
    if entree.type == TYPE_JOURS:
        if isinstance(valeur, str | bytes) or not isinstance(
            valeur, list | tuple | set | frozenset
        ):
            raise ErreurDonneesInvalides(
                f"{entree.libelle} : une liste de jours est attendue (lu : {valeur!r})."
            )
        jours = []
        for jour in valeur:
            if isinstance(jour, bool) or not isinstance(jour, int) or not 0 <= jour <= 6:
                raise ErreurDonneesInvalides(
                    f"{entree.libelle} : jour invalide {jour!r} (numéros de 0, lundi, à 6)."
                )
            jours.append(jour)
        if not jours:
            raise ErreurDonneesInvalides(f"{entree.libelle} : au moins un jour est attendu.")
        return tuple(sorted(set(jours)))
    if entree.type == TYPE_CHOIX:
        permises = [permise for permise, _ in entree.choix]
        if not isinstance(valeur, str) or valeur not in permises:
            liste = ", ".join(f"{libelle} (« {permise} »)" for permise, libelle in entree.choix)
            raise ErreurDonneesInvalides(
                f"{entree.libelle} : valeur {valeur!r} inconnue ; choisir parmi : {liste}."
            )
        return valeur
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


_TYPES = {"int": int, "bool": bool, "str": str}


def _type_de(champ: dataclasses.Field) -> type | str:
    if champ.metadata["jours"]:
        return TYPE_JOURS
    if champ.metadata["choix"]:
        return TYPE_CHOIX
    return _TYPES[champ.type] if isinstance(champ.type, str) else champ.type


_DEFINITIONS: tuple[DefinitionParametre, ...] = tuple(
    DefinitionParametre(
        cle=champ.name,
        type=_type_de(champ),
        defaut=champ.default,
        libelle=champ.metadata["libelle"],
        description=champ.metadata["description"],
        minimum=champ.metadata["minimum"],
        maximum=champ.metadata["maximum"],
        unite=champ.metadata["unite"],
        choix=champ.metadata["choix"],
    )
    for champ in dataclasses.fields(Parametres)
)
