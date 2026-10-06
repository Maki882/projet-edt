"""Description déclarative d'une entité éditable : champs, colonnes, filtres, règles.

Une entité (salle, enseignant, besoin...) est décrite **une seule fois** par un
objet `Entite`. Les fonctions génériques de `edt.services.referentiels.operations`
s'en servent pour lister (tri, recherche, filtres, pagination), lire, créer,
modifier et supprimer ; l'interface web s'en sert pour construire ses listes
et ses formulaires ; un assistant en langage naturel pourra s'en servir pour
savoir quels champs existent (`operations.decrire`).

Exemple (abrégé) :

    TYPE_SALLE = Entite(
        code="type_salle",
        modele=TypeSalle,
        nom="type de salle", pluriel="types de salle", feminin=False,
        demonstratif="Ce type de salle", nouveau="Nouveau type de salle",
        libelle=lambda t: t.libelle,
        champs=(
            Champ("code", "Code", obligatoire=True),
            Champ("libelle", "Libellé", obligatoire=True),
        ),
        colonnes=(Colonne("code", "Code", lambda t: t.code, tri=TypeSalle.code),),
        tri_defaut="code",
        uniques=(Unicite(("code",), "Ce code est déjà pris.", "code"),),
        usages=(Decompte(par_colonne(Salle.type_salle_id), "est le type d'une salle", "..."),),
    )
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from edt.domaine.enumerations import JOURS, EnumLibelle
from edt.erreurs import ErreurIntrouvable

EFFECTIF_MAX = 100_000
"""Borne raisonnable d'une capacité ou d'un effectif (protège aussi la base d'un nombre absurde)."""


class TypeChamp(StrEnum):
    """Nature d'un champ : décide de la conversion de la saisie et du contrôle affiché."""

    TEXTE = "texte"
    TEXTE_LONG = "texte_long"
    ENTIER = "entier"
    BOOLEEN = "booleen"
    CHOIX = "choix"
    """Une valeur parmi `Champ.choix` (énumération, jour de la semaine...)."""
    REFERENCE = "reference"
    """Identifiant d'un élément d'une autre table (`Champ.reference`)."""
    REFERENCES = "references"
    """Liste d'identifiants d'éléments d'une autre table (relation plusieurs à plusieurs)."""
    DATE = "date"
    HEURE = "heure"
    """Heure du jour, stockée en minutes depuis minuit ; saisie « 08:30 » ou « 8 h 30 »."""
    DUREE = "duree"
    """Durée en minutes ; saisie « 2 h », « 1 h 30 », « 90 min »."""
    COULEUR = "couleur"
    """Couleur « #rrggbb »."""


@dataclass(frozen=True)
class Reference:
    """Cible d'un champ `REFERENCE` ou `REFERENCES`.

    - `modele` : classe ORM visée ;
    - `libelle` : texte d'un élément dans une liste de choix ;
    - `ordre` : tri des choix ;
    - `condition` : restreint les choix permis (fonction sans argument qui
      renvoie une condition SQL), contrôlée aussi à l'enregistrement ;
    - `chargements` : options de chargement utiles à `libelle`.
    """

    modele: type
    libelle: Callable[[Any], str]
    ordre: tuple[Any, ...] = ()
    condition: Callable[[], Any] | None = None
    chargements: tuple[Any, ...] = ()


@dataclass(frozen=True)
class Champ:
    """Un champ saisissable d'une entité.

    `nom` est l'attribut ORM écrit (sauf champ `virtuel`, que l'entité traduit
    elle-même avec `preparer` et `apres_ecriture`, et lit avec `lire`).
    `longueur_max` vide : déduite de la colonne. `visible_si` : `(champ, valeurs)`,
    le champ n'est utile que si l'autre champ prend l'une de ces valeurs.
    """

    nom: str
    libelle: str
    type: TypeChamp = TypeChamp.TEXTE
    obligatoire: bool = False
    aide: str = ""
    defaut: object = None
    minimum: int | None = None
    maximum: int | None = None
    longueur_max: int | None = None
    unite: str = ""
    choix: tuple[tuple[object, str], ...] = ()
    reference: Reference | None = None
    groupe: str = ""
    visible_si: tuple[str, tuple[str, ...]] | None = None
    virtuel: bool = False
    lire: Callable[[Session, Any], object] | None = None
    large: bool = False


@dataclass(frozen=True)
class Colonne:
    """Une colonne de liste : `valeur` donne le texte (ou nombre, booléen) affiché.

    Une valeur peut être une **liste** (groupes, équipements...) : la liste
    affiche ses `apercu` premiers éléments suivis de « +n ».

    - `tri` : expression SQL (ou tuple d'expressions) de tri ; vide = non triable ;
    - `nombre` : valeur numérique (alignée à droite) ;
    - `format` : présentation particulière (« couleur » : pastille de la couleur) ;
    - `texte` : texte libre qui peut tenir sur deux lignes (les autres colonnes
      restent sur une ligne) ;
    - `detail` : texte complet affiché au survol d'une valeur abrégée ;
    - `filtre` : nom du filtre qui fixe cette valeur ; la colonne est omise
      quand ce filtre est appliqué.
    """

    cle: str
    libelle: str
    valeur: Callable[[Any], object]
    tri: Any = None
    nombre: bool = False
    format: str = ""
    texte: bool = False
    apercu: int = 2
    detail: Callable[[Any], str] | None = None
    filtre: str = ""


@dataclass(frozen=True)
class Filtre:
    """Un filtre de liste : une liste de choix et la condition SQL d'un choix.

    - `defaut` : valeur appliquée quand l'appelant ne précise rien (le choix
      vide « Tous » reste possible) ;
    - `champ` : champ de formulaire prérempli par ce filtre lors d'un ajout.
    """

    nom: str
    libelle: str
    options: Callable[[Session], list[tuple[str, str]]]
    condition: Callable[[str], Any]
    defaut: Callable[[Session], str | None] | None = None
    champ: str = ""


@dataclass(frozen=True)
class Unicite:
    """Combinaison de champs qui doit être unique, avec le message et le champ fautif."""

    champs: tuple[str, ...]
    message: str
    champ_erreur: str


@dataclass(frozen=True)
class Decompte:
    """Un décompte d'éléments liés, avec sa phrase au singulier et au pluriel (`{n}`).

    Sert aux **usages** (ce qui empêche une suppression) et aux **conséquences**
    (ce que la suppression emporte ou modifie).

    `lien` (facultatif) : adresse de la liste des éléments comptés, où `{id}`
    est l'identifiant de l'élément à supprimer (par exemple
    `/besoins?semestre=&salle_imposee={id}`) ; `objets` nomme ces éléments
    pour le libellé du lien (« Voir ces 12 besoins »).
    """

    compter: Callable[[Session, int], int]
    singulier: str
    pluriel: str
    lien: str = ""
    objets: tuple[str, str] = ("cet élément", "ces {n} éléments")

    def texte(self, nombre: int) -> str:
        """Phrase accordée au nombre."""
        return (self.singulier if nombre == 1 else self.pluriel).format(n=nombre)

    def adresse(self, ident: int) -> str:
        """Adresse de la liste des éléments comptés pour l'élément `ident` (vide sans lien)."""
        return self.lien.format(id=ident) if self.lien else ""

    def libelle_lien(self, nombre: int) -> str:
        """Libellé du lien : « Voir ce besoin », « Voir ces 12 besoins »."""
        return "Voir " + (self.objets[0] if nombre == 1 else self.objets[1]).format(n=nombre)


BESOINS = ("ce besoin", "ces {n} besoins")
"""Noms des besoins comptés, pour `Decompte.objets`."""
SALLES = ("cette salle", "ces {n} salles")
"""Noms des salles comptées, pour `Decompte.objets`."""
TOUS_SEMESTRES = "/besoins?semestre="
"""Début d'adresse de la liste des besoins de tous les semestres (le décompte les compte tous)."""


Valeurs = dict[str, Any]


@dataclass(frozen=True)
class Entite:
    """Description complète d'une entité éditable.

    Crochets facultatifs (appelés par `operations`) :

    - `valider(session, valeurs, actuel)` : contrôles entre champs ; renvoie
      les erreurs par nom de champ. `valeurs` ne contient que les champs
      convertis sans erreur ; `actuel` est l'objet modifié (`None` à la création) ;
    - `preparer(session, valeurs)` : traduit les valeurs du formulaire en
      attributs ORM (champs virtuels, valeurs propres à un mode) ;
    - `apres_ecriture(session, objet, valeurs)` : écritures annexes une fois
      l'objet enregistré (tables d'association sans relation ORM) ;
    - `analyser(session, objet)` : refus et conséquences de suppression
      calculés (en plus de `usages` et `consequences`), en textes ou en
      `operations.Raison` (texte et lien vers les éléments concernés).
    """

    code: str
    modele: type
    nom: str
    pluriel: str
    feminin: bool
    demonstratif: str
    nouveau: str
    libelle: Callable[[Any], str]
    champs: tuple[Champ, ...]
    colonnes: tuple[Colonne, ...]
    tri_defaut: str
    recherche: tuple[Any, ...] = ()
    recherche_avancee: tuple[Callable[[str], Any], ...] = ()
    jointures: tuple[tuple[Any, Any], ...] = ()
    chargements: tuple[Any, ...] = ()
    filtres: tuple[Filtre, ...] = ()
    uniques: tuple[Unicite, ...] = ()
    usages: tuple[Decompte, ...] = ()
    consequences: tuple[Decompte, ...] = ()
    valider: Callable[[Session, Valeurs, Any], dict[str, str]] | None = None
    preparer: Callable[[Session, Valeurs], Valeurs] | None = None
    apres_ecriture: Callable[[Session, Any, Valeurs], None] | None = None
    analyser: Callable[[Session, Any], tuple[list[Any], list[Any]]] | None = None
    _index: dict[str, Champ] = field(default_factory=dict, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._index.update({champ.nom: champ for champ in self.champs})

    def champ(self, nom: str) -> Champ:
        """Le champ `nom` ; lève `ErreurIntrouvable` s'il n'existe pas."""
        try:
            return self._index[nom]
        except KeyError:
            raise ErreurIntrouvable(f"Champ inconnu pour {self.nom} : « {nom} ».") from None

    def a_champ(self, nom: str) -> bool:
        """Vrai si l'entité a un champ `nom`."""
        return nom in self._index

    def colonne(self, cle: str) -> Colonne | None:
        """La colonne `cle`, ou `None`."""
        return next((colonne for colonne in self.colonnes if colonne.cle == cle), None)

    def filtre(self, nom: str) -> Filtre | None:
        """Le filtre `nom`, ou `None`."""
        return next((filtre for filtre in self.filtres if filtre.nom == nom), None)

    def accorde(self, participe: str) -> str:
        """Participe accordé au genre de l'entité : « créé » donne « créée » pour une salle."""
        return participe + "e" if self.feminin else participe

    @property
    def cherchable(self) -> bool:
        """Vrai si la liste propose une recherche plein texte."""
        return bool(self.recherche or self.recherche_avancee)

    @property
    def titre(self) -> str:
        """Nom pluriel avec majuscule, pour un titre de page."""
        return self.pluriel[:1].upper() + self.pluriel[1:]

    @property
    def nom_majuscule(self) -> str:
        """Nom singulier avec majuscule."""
        return self.nom[:1].upper() + self.nom[1:]


# --------------------------------------------------------------------------- #
# Aides aux déclarations
# --------------------------------------------------------------------------- #


def choix_enum(enumeration: type[EnumLibelle]) -> tuple[tuple[object, str], ...]:
    """Choix d'un champ à partir d'une énumération (valeur, libellé)."""
    return tuple((membre.value, membre.libelle) for membre in enumeration)


def choix_jours(jusqua: int = 6) -> tuple[tuple[object, str], ...]:
    """Jours de la semaine, du lundi (0) au jour `jusqua` inclus."""
    return tuple((numero, JOURS[numero].capitalize()) for numero in range(jusqua + 1))


def par_colonne(colonne: Any) -> Callable[[Session, int], int]:
    """Compteur des lignes dont `colonne` vaut l'identifiant donné (pour un `Decompte`)."""

    def compter(session: Session, ident: int) -> int:
        return session.scalar(select(func.count()).where(colonne == ident)) or 0

    return compter


def compter_ou(session: Session, colonne: Any, ident: int) -> int:
    """Nombre de lignes dont `colonne` vaut `ident` (pour les colonnes calculées d'une liste)."""
    return session.scalar(select(func.count()).where(colonne == ident)) or 0


def options_reference(session: Session, reference: Reference) -> list[tuple[str, str]]:
    """Choix permis d'une référence : (identifiant en texte, libellé)."""
    requete = select(reference.modele).options(*reference.chargements)
    if reference.condition is not None:
        requete = requete.where(reference.condition())
    if reference.ordre:
        requete = requete.order_by(*reference.ordre)
    return [(str(objet.id), reference.libelle(objet)) for objet in session.scalars(requete)]


def libelle_choix(choix: tuple[tuple[object, str], ...], valeur: object) -> str:
    """Libellé d'une valeur de choix (la valeur elle-même si elle est inconnue)."""
    for cle, libelle in choix:
        if str(cle) == str(valeur):
            return libelle
    return "" if valeur is None else str(valeur)


def libelles(objets: Iterable[Any], attribut: str = "code") -> str:
    """Liste d'objets jointe par des virgules (par leur attribut `attribut`)."""
    return ", ".join(str(getattr(objet, attribut)) for objet in objets)
