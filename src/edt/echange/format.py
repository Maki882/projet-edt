"""Le format d'échange : description déclarative des feuilles et de leurs colonnes.

C'est la **seule** description du format. Le gabarit vide, l'export des
données, la validation à l'import, l'onglet d'explications et les tableaux
de `docs/import-export.md` en découlent.

Une feuille est une table (un onglet du classeur XLSX ou un fichier CSV).
Chaque colonne a un nom stable (l'en-tête attendu), un libellé, un type, un
caractère obligatoire, une aide, un exemple et l'indication qu'elle fait
partie de l'**identifiant métier** de la ligne : c'est sur cet identifiant
qu'un réimport retrouve la ligne existante pour la mettre à jour au lieu
d'en créer une seconde.

Les feuilles de `FEUILLES` sont rangées dans l'ordre de traitement imposé
par leurs dépendances (une salle référence un campus, un besoin référence
une matière, des groupes, des enseignants...). `FEUILLE_SEANCES` décrit le
tableau plat d'un emploi du temps existant (import d'emploi du temps).
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from edt.domaine.contraintes import Niveau
from edt.domaine.enumerations import (
    Accessibilite,
    CibleIndisponibilite,
    EnumLibelle,
    ModeVolume,
    NatureGroupe,
    NatureJourFerme,
    StatutEnseignant,
)
from edt.domaine.regles import CATALOGUE_TYPES, EtatRegle, NaturePortee, TypeRegle
from edt.echange import valeurs as v
from edt.erreurs import ErreurDonneesInvalides


class TypeColonne(StrEnum):
    """Type d'une colonne : il fixe la lecture tolérante et l'écriture de la valeur."""

    TEXTE = "texte"
    CODE = "code"
    ENTIER = "entier"
    BOOLEEN = "booleen"
    DATE = "date"
    HEURE = "heure"
    DUREE = "duree"
    JOUR = "jour"
    LISTE = "liste"
    CHOIX = "choix"


LIBELLES_TYPES: dict[TypeColonne, str] = {
    TypeColonne.TEXTE: "texte",
    TypeColonne.CODE: "code",
    TypeColonne.ENTIER: "nombre entier",
    TypeColonne.BOOLEEN: "oui / non",
    TypeColonne.DATE: "date JJ/MM/AAAA",
    TypeColonne.HEURE: "heure (08:30, 8h30)",
    TypeColonne.DUREE: "durée (2 h, 1 h 30, 90 min)",
    TypeColonne.JOUR: "jour (lundi ... dimanche)",
    TypeColonne.LISTE: "liste de codes séparés par ; ou ,",
    TypeColonne.CHOIX: "une valeur de la liste",
}

_LECTEURS: dict[TypeColonne, Callable[[Any], Any]] = {
    TypeColonne.TEXTE: v.lire_texte,
    TypeColonne.CODE: v.lire_code,
    TypeColonne.ENTIER: v.lire_entier,
    TypeColonne.BOOLEEN: v.lire_booleen,
    TypeColonne.DATE: v.lire_date,
    TypeColonne.HEURE: v.lire_heure,
    TypeColonne.DUREE: v.lire_duree,
    TypeColonne.JOUR: v.lire_jour,
    TypeColonne.LISTE: v.lire_liste,
}

_ECRIVAINS: dict[TypeColonne, Callable[[Any], str]] = {
    TypeColonne.DATE: v.ecrire_date,
    TypeColonne.HEURE: v.ecrire_heure,
    TypeColonne.DUREE: v.ecrire_duree,
    TypeColonne.BOOLEEN: v.ecrire_booleen,
    TypeColonne.JOUR: v.ecrire_jour,
    TypeColonne.LISTE: v.ecrire_liste,
}


class StatutSeance(EnumLibelle):
    """Statut d'une séance d'un emploi du temps existant (colonne `statut`).

    Sans statut, une séance est considérée comme réalisée. Une séance
    annulée n'est pas comptée ; une séance déplacée compte à sa position
    finale (celle de la ligne).
    """

    REALISEE = "realisee", "Réalisée"
    PLANIFIEE = "planifiee", "Planifiée"
    DEPLACEE = "deplacee", "Déplacée"
    ANNULEE = "annulee", "Annulée"
    A_PROGRAMMER = "a_programmer", "À programmer"


@dataclass(frozen=True)
class Colonne:
    """Une colonne d'une feuille.

    - `nom` : en-tête attendu (reconnu sans casse ni accents, comme le libellé) ;
    - `identifiant` : la colonne fait partie de l'identifiant métier de la ligne ;
    - `choix` : liste fermée des valeurs permises (type `CHOIX`) ;
    - `defaut` : valeur retenue quand la cellule est vide (documentée dans l'aide) ;
    - `defaut_de` : nom d'une autre colonne dont la valeur est reprise quand la
      cellule est vide (dernier jour = premier jour) ;
    - `export_seulement` : colonne écrite par l'export pour information (la
      phrase d'une règle), ignorée à l'import ;
    - `maximum` : plus grande valeur admise (nombre, ou minutes d'une durée) ;
    - `longueur` : plus grand nombre de caractères d'un texte ou de chaque code
      d'une liste, celui de la colonne du modèle (`LONGUEURS`).
    """

    nom: str
    libelle: str
    type: TypeColonne
    obligatoire: bool = False
    aide: str = ""
    exemple: str = ""
    identifiant: bool = False
    choix: type[StrEnum] | None = None
    defaut: Any = None
    minimum: int | None = None
    defaut_de: str | None = None
    export_seulement: bool = False
    maximum: int | None = None
    longueur: int | None = None

    def __post_init__(self) -> None:
        if (self.type is TypeColonne.CHOIX) != (self.choix is not None):
            raise ValueError(
                f"Colonne {self.nom} : `choix` va avec le type CHOIX, et seulement lui."
            )

    def lire(self, valeur: Any) -> Any:
        """Valeur typée de la cellule (`None` si vide) ; lève `ErreurDonneesInvalides` sinon."""
        if self.type is TypeColonne.CHOIX:
            resultat = v.lire_choix(valeur, self.choix)
        else:
            resultat = _LECTEURS[self.type](valeur)
        if resultat is None:
            return None
        if self.minimum is not None and resultat < self.minimum:
            raise ErreurDonneesInvalides(
                f"Valeur trop petite : {self._afficher(resultat)} "
                f"(au moins {self._afficher(self.minimum)})."
            )
        if self.maximum is not None and resultat > self.maximum:
            raise ErreurDonneesInvalides(
                f"Valeur trop grande : {self._afficher(resultat)} "
                f"(au plus {self._afficher(self.maximum)})."
            )
        if self.longueur is not None:
            for texte in resultat if isinstance(resultat, tuple) else (resultat,):
                if isinstance(texte, str) and len(texte) > self.longueur:
                    raise ErreurDonneesInvalides(
                        f"Texte trop long : {len(texte)} caractères, {self.longueur} au plus "
                        f"(« {texte[:20]}... »)."
                    )
        return resultat

    def _afficher(self, valeur: Any) -> str:
        if self.type is TypeColonne.DUREE:
            return v.ecrire_duree(valeur)
        return str(valeur)

    def ecrire(self, valeur: Any) -> str:
        """Texte de la valeur typée, tel qu'il s'écrit dans un CSV."""
        if valeur is None:
            return ""
        ecrivain = _ECRIVAINS.get(self.type)
        if ecrivain is not None:
            return ecrivain(valeur)
        if isinstance(valeur, StrEnum):
            return valeur.value
        return str(valeur)

    @property
    def aide_complete(self) -> str:
        """Aide, complétée des valeurs permises et de la valeur par défaut."""
        morceaux = [self.aide] if self.aide else []
        if self.choix is not None:
            morceaux.append("Valeurs : " + ", ".join(m.value for m in self.choix) + ".")
        if self.defaut is not None:
            morceaux.append(f"Vide : {self.ecrire(self.defaut)}.")
        if self.defaut_de is not None:
            morceaux.append(f"Vide : même valeur que {self.defaut_de}.")
        if self.export_seulement:
            morceaux.append("Export seulement : ignorée à l'import.")
        if self.maximum is not None and self.type is TypeColonne.DUREE:
            morceaux.append(f"Au plus {self._afficher(self.maximum)}.")
        if self.longueur is not None:
            morceaux.append(f"{self.longueur} caractères au plus.")
        return " ".join(morceaux)


@dataclass(frozen=True)
class Feuille:
    """Une feuille du format : un onglet du classeur ou un fichier CSV."""

    nom: str
    titre: str
    description: str
    colonnes: tuple[Colonne, ...]
    facultative: bool = False

    def colonne(self, nom: str) -> Colonne:
        """Colonne de nom `nom` (erreur de programmation si elle n'existe pas)."""
        for colonne in self.colonnes:
            if colonne.nom == nom:
                return colonne
        raise KeyError(f"Feuille {self.nom} : pas de colonne {nom!r}")

    @property
    def entetes(self) -> tuple[str, ...]:
        """Noms des colonnes, dans l'ordre du gabarit."""
        return tuple(colonne.nom for colonne in self.colonnes)

    @property
    def identifiant(self) -> tuple[str, ...]:
        """Colonnes qui forment l'identifiant métier."""
        return tuple(colonne.nom for colonne in self.colonnes if colonne.identifiant)

    @property
    def obligatoires(self) -> tuple[str, ...]:
        """Colonnes obligatoires."""
        return tuple(colonne.nom for colonne in self.colonnes if colonne.obligatoire)

    def reconnaitre_entete(self, entete: str) -> Colonne | None:
        """Colonne désignée par un en-tête, par son nom ou son libellé (sans casse ni accents)."""
        mot = v.cle_mot(entete)
        for colonne in self.colonnes:
            if mot in (v.cle_mot(colonne.nom), v.cle_mot(colonne.libelle)):
                return colonne
        return None


def _c(nom: str, libelle: str, type_: TypeColonne, **options: Any) -> Colonne:
    return Colonne(nom, libelle, type_, **options)


T = TypeColonne

ANNEE = _c(
    "annee",
    "Année scolaire",
    T.CODE,
    obligatoire=True,
    identifiant=True,
    aide="Libellé de l'année scolaire (feuille annees).",
    exemple="2026-2027",
)

FEUILLES: tuple[Feuille, ...] = (
    Feuille(
        "annees",
        "Années",
        "Années scolaires. Une année créée par import ne reçoit ni semestre ni jour férié "
        "automatiquement : les décrire dans les feuilles semestres et jours_fermes.",
        (
            _c(
                "libelle",
                "Libellé",
                T.CODE,
                obligatoire=True,
                identifiant=True,
                exemple="2026-2027",
            ),
            _c("date_debut", "Début", T.DATE, obligatoire=True, exemple="01/09/2026"),
            _c("date_fin", "Fin", T.DATE, obligatoire=True, exemple="31/08/2027"),
        ),
    ),
    Feuille(
        "semestres",
        "Semestres",
        "Semestres d'une année : l'unité de génération d'un emploi du temps.",
        (
            ANNEE,
            _c("code", "Code", T.CODE, obligatoire=True, identifiant=True, exemple="S1"),
            _c("libelle", "Libellé", T.TEXTE, obligatoire=True, exemple="Semestre 1"),
            _c(
                "date_debut",
                "Premier jour de cours",
                T.DATE,
                obligatoire=True,
                exemple="07/09/2026",
            ),
            _c("date_fin", "Dernier jour de cours", T.DATE, obligatoire=True, exemple="29/01/2027"),
        ),
    ),
    Feuille(
        "campus",
        "Campus",
        "Sites d'enseignement (campus de l'école ou établissement voisin).",
        (
            _c("code", "Code", T.CODE, obligatoire=True, identifiant=True, exemple="NDC"),
            _c("nom", "Nom", T.TEXTE, obligatoire=True, exemple="Notre-Dame-des-Champs"),
            _c(
                "prefixe_salle",
                "Préfixe des salles",
                T.CODE,
                aide="Préfixe des codes de salle du site.",
                exemple="N",
            ),
        ),
    ),
    Feuille(
        "trajets",
        "Trajets",
        "Temps de trajet entre deux campus (symétrique ; sans ligne, le paramètre "
        "« trajet par défaut » s'applique).",
        (
            _c("campus_a", "Campus A", T.CODE, obligatoire=True, identifiant=True, exemple="NDC"),
            _c("campus_b", "Campus B", T.CODE, obligatoire=True, identifiant=True, exemple="ISSY"),
            _c("duree", "Durée du trajet", T.DUREE, obligatoire=True, exemple="1 h"),
        ),
    ),
    Feuille(
        "salles",
        "Salles",
        "Salles de cours. Un code de salle est unique sur son campus.",
        (
            _c("code", "Code", T.CODE, obligatoire=True, identifiant=True, exemple="N204"),
            _c("campus", "Campus", T.CODE, obligatoire=True, identifiant=True, exemple="NDC"),
            _c("capacite", "Capacité", T.ENTIER, obligatoire=True, minimum=0, exemple="40"),
            _c(
                "type",
                "Type de salle",
                T.CODE,
                obligatoire=True,
                aide="Code d'un type de salle (liste configurable).",
                exemple="COURS",
            ),
            _c("batiment", "Bâtiment", T.TEXTE, exemple="Principal"),
            _c("etage", "Étage", T.ENTIER, aide="0 = rez-de-chaussée.", exemple="2"),
            _c(
                "equipements",
                "Équipements",
                T.LISTE,
                aide="Codes d'équipements (liste configurable).",
                exemple="VIDEOPROJ; VISIO",
            ),
            _c(
                "accessible",
                "Accessible PMR",
                T.CHOIX,
                choix=Accessibilite,
                defaut=Accessibilite.INCONNU,
                exemple="oui",
            ),
        ),
    ),
    Feuille(
        "enseignants",
        "Enseignants",
        "Enseignants, identifiés par un identifiant stable (matricule).",
        (
            _c(
                "identifiant",
                "Identifiant",
                T.CODE,
                obligatoire=True,
                identifiant=True,
                exemple="ENS001",
            ),
            _c("civilite", "Civilité", T.TEXTE, exemple="Mme"),
            _c("nom", "Nom", T.TEXTE, obligatoire=True, exemple="Martin"),
            _c("prenom", "Prénom", T.TEXTE, exemple="Claire"),
            _c(
                "statut",
                "Statut",
                T.CHOIX,
                choix=StatutEnseignant,
                defaut=StatutEnseignant.PERMANENT,
                exemple="vacataire",
            ),
            _c("max_par_jour", "Maximum par jour", T.DUREE, exemple="6 h"),
            _c("max_par_semaine", "Maximum par semaine", T.DUREE, exemple="18 h"),
            _c(
                "fictif",
                "Fictif",
                T.BOOLEEN,
                defaut=False,
                aide="Enseignant de remplacement (inconnu à l'import) : exclu des contraintes.",
                exemple="non",
            ),
        ),
    ),
    Feuille(
        "groupes",
        "Groupes",
        "Groupes d'étudiants : promotion > classe > demi-groupe, et groupes à part "
        "(langues, options, tiers-temps). Un code de groupe est unique dans son année.",
        (
            ANNEE,
            _c("code", "Code", T.CODE, obligatoire=True, identifiant=True, exemple="A1-G1"),
            _c("libelle", "Libellé", T.TEXTE, obligatoire=True, exemple="1re année, classe 1"),
            _c("nature", "Nature", T.CHOIX, obligatoire=True, choix=NatureGroupe, exemple="classe"),
            _c(
                "parent",
                "Groupe parent",
                T.CODE,
                aide="Code du groupe qui le contient (vide pour une promotion ou un groupe à part).",
                exemple="A1",
            ),
            _c("effectif", "Effectif", T.ENTIER, obligatoire=True, minimum=0, exemple="28"),
            _c(
                "accessibilite_requise",
                "Salle accessible requise",
                T.BOOLEEN,
                defaut=False,
                exemple="non",
            ),
            _c(
                "chevauche",
                "Chevauche",
                T.LISTE,
                aide="Groupes qui partagent des étudiants avec celui-ci (déclaration symétrique : "
                "une seule des deux lignes suffit).",
                exemple="A1-ANG-B2",
            ),
        ),
    ),
    Feuille(
        "jours_fermes",
        "Jours fermés",
        "Fermetures du calendrier (fériés, vacances, examens, semaines spéciales), globales "
        "ou propres à un groupe et à ses sous-groupes.",
        (
            ANNEE,
            _c(
                "date_debut",
                "Premier jour",
                T.DATE,
                obligatoire=True,
                identifiant=True,
                exemple="26/10/2026",
            ),
            _c(
                "date_fin",
                "Dernier jour",
                T.DATE,
                identifiant=True,
                defaut_de="date_debut",
                aide="Inclus.",
                exemple="01/11/2026",
            ),
            _c("libelle", "Libellé", T.TEXTE, obligatoire=True, exemple="Vacances de la Toussaint"),
            _c(
                "nature",
                "Nature",
                T.CHOIX,
                obligatoire=True,
                choix=NatureJourFerme,
                exemple="vacances",
            ),
            _c(
                "groupe",
                "Groupe concerné",
                T.CODE,
                identifiant=True,
                aide="Vide = fermeture globale.",
                exemple="A1",
            ),
        ),
    ),
    Feuille(
        "etudiants",
        "Étudiants",
        "Étudiants (facultatif, fictifs ou pseudonymisés) : ils servent à détecter les "
        "groupes qui partagent des étudiants. Aucune donnée de santé.",
        (
            _c(
                "identifiant",
                "Identifiant",
                T.CODE,
                obligatoire=True,
                identifiant=True,
                exemple="ETU001",
            ),
            _c("nom", "Nom", T.TEXTE, obligatoire=True, exemple="Petit"),
            _c("prenom", "Prénom", T.TEXTE, exemple="Camille"),
        ),
        facultative=True,
    ),
    Feuille(
        "inscriptions",
        "Inscriptions",
        "Inscription d'un étudiant dans un groupe (facultatif), une ligne par inscription.",
        (
            ANNEE,
            _c(
                "etudiant", "Étudiant", T.CODE, obligatoire=True, identifiant=True, exemple="ETU001"
            ),
            _c("groupe", "Groupe", T.CODE, obligatoire=True, identifiant=True, exemple="A1-G1a"),
        ),
        facultative=True,
    ),
    Feuille(
        "matieres",
        "Matières",
        "Matières, identifiées par leur code module et leur promotion de rattachement.",
        (
            _c(
                "code", "Code module", T.CODE, obligatoire=True, identifiant=True, exemple="IM.1101"
            ),
            _c("libelle", "Libellé", T.TEXTE, obligatoire=True, exemple="Mathématiques"),
            _c(
                "annee",
                "Année scolaire",
                T.CODE,
                identifiant=True,
                aide="Obligatoire si la promotion est renseignée.",
                exemple="2026-2027",
            ),
            _c(
                "promotion",
                "Promotion",
                T.CODE,
                identifiant=True,
                aide="Code du groupe de la promotion de rattachement.",
                exemple="A1",
            ),
        ),
    ),
    Feuille(
        "besoins",
        "Besoins",
        "La demande d'enseignement : ce que le générateur doit placer. Un besoin est identifié "
        "par son semestre, sa matière, son type, ses groupes, son libellé et sa date ponctuelle.",
        (
            ANNEE,
            _c("semestre", "Semestre", T.CODE, obligatoire=True, identifiant=True, exemple="S1"),
            _c("matiere", "Matière", T.CODE, obligatoire=True, identifiant=True, exemple="IM.1101"),
            _c(
                "promotion",
                "Promotion de la matière",
                T.CODE,
                identifiant=True,
                aide="Distingue deux matières de même code ; vide = la matière sans promotion, "
                "ou la seule qui porte ce code.",
                exemple="A1",
            ),
            _c(
                "type",
                "Type de séance",
                T.CODE,
                obligatoire=True,
                identifiant=True,
                aide="Code d'un type de séance (liste configurable).",
                exemple="TD",
            ),
            _c(
                "libelle",
                "Libellé",
                T.TEXTE,
                identifiant=True,
                aide="Précision facultative ; distingue deux besoins par ailleurs identiques.",
                exemple="Partiel",
            ),
            _c("groupes", "Groupes", T.LISTE, obligatoire=True, identifiant=True, exemple="A1-G1"),
            _c("enseignants", "Enseignants", T.LISTE, aide="Identifiants.", exemple="ENS001"),
            _c("duree", "Durée d'une séance", T.DUREE, obligatoire=True, exemple="2 h"),
            _c(
                "mode",
                "Mode de volume",
                T.CHOIX,
                choix=ModeVolume,
                defaut=ModeVolume.PAR_SEMAINE,
                aide="par_semaine : nombre de séances chaque semaine ; total : nombre de séances "
                "réparties dans la période ; ponctuel : une séance datée (colonne date).",
                exemple="par_semaine",
            ),
            _c("nombre", "Nombre de séances", T.ENTIER, minimum=1, defaut=1, exemple="1"),
            _c(
                "max_par_semaine",
                "Maximum par semaine",
                T.ENTIER,
                minimum=1,
                defaut=1,
                aide="Mode total.",
                exemple="1",
            ),
            _c("date_debut", "Début de la période", T.DATE, aide="Vide = début du semestre."),
            _c("date_fin", "Fin de la période", T.DATE, aide="Vide = fin du semestre."),
            _c(
                "date",
                "Date ponctuelle",
                T.DATE,
                identifiant=True,
                aide="Mode ponctuel seulement.",
                exemple="19/11/2026",
            ),
            _c("jour_impose", "Jour imposé", T.JOUR, exemple="jeudi"),
            _c("debut_impose", "Heure de début imposée", T.HEURE, exemple="13:45"),
            _c(
                "salle_imposee",
                "Salle imposée",
                T.CODE,
                aide="Code de la salle ; « code@campus » si le code existe sur plusieurs campus.",
                exemple="N016",
            ),
            _c("campus_impose", "Campus imposé", T.CODE, exemple="NDC"),
            _c(
                "types_salle",
                "Types de salle acceptés",
                T.LISTE,
                aide="Vide = le type par défaut du type de séance.",
                exemple="INFO",
            ),
            _c("equipements", "Équipements requis", T.LISTE, exemple="ORDINATEURS"),
            _c("commentaire", "Commentaire", T.TEXTE),
        ),
    ),
    Feuille(
        "indisponibilites",
        "Indisponibilités",
        "Indisponibilités d'un enseignant, d'une salle ou d'un groupe : récurrentes (colonne "
        "jour) ou datées (colonnes date_debut et date_fin).",
        (
            _c(
                "cible",
                "Cible",
                T.CHOIX,
                obligatoire=True,
                identifiant=True,
                choix=CibleIndisponibilite,
                exemple="enseignant",
            ),
            _c(
                "ressource",
                "Ressource",
                T.CODE,
                obligatoire=True,
                identifiant=True,
                aide="Identifiant de l'enseignant, code de la salle (« code@campus » si ambigu) "
                "ou code du groupe.",
                exemple="ENS004",
            ),
            _c(
                "annee",
                "Année scolaire",
                T.CODE,
                identifiant=True,
                aide="Obligatoire pour un groupe.",
                exemple="2026-2027",
            ),
            _c("jour", "Jour", T.JOUR, identifiant=True, aide="Forme récurrente.", exemple="lundi"),
            _c("date_debut", "Premier jour", T.DATE, identifiant=True, aide="Forme datée."),
            _c(
                "date_fin",
                "Dernier jour",
                T.DATE,
                identifiant=True,
                defaut_de="date_debut",
                aide="Forme datée, inclus.",
            ),
            _c("debut", "Début", T.HEURE, identifiant=True, defaut=0, exemple="13:45"),
            _c("fin", "Fin", T.HEURE, identifiant=True, defaut=24 * 60, exemple="18:00"),
            _c("motif", "Motif", T.TEXTE, exemple="Activité professionnelle"),
            _c("dure", "Contrainte dure", T.BOOLEEN, defaut=True, aide="Toujours oui en V1."),
            _c(
                "poids",
                "Poids",
                T.ENTIER,
                minimum=0,
                defaut=0,
                aide="Préférences (versions futures).",
            ),
        ),
    ),
    Feuille(
        "regles",
        "Règles",
        "Règles personnalisées d'une année (docs/regles-personnalisees.md) : un type, une "
        "portée et sa cible, des paramètres, un niveau, un état et une période. Une règle "
        "créée par import a l'origine « import ». Réimport : mise à jour par année et code.",
        (
            ANNEE,
            _c(
                "code",
                "Code",
                T.CODE,
                identifiant=True,
                aide="Code de la règle dans l'année ; vide = attribué à l'import (R001, "
                "R002...). Donner le code pour réimporter sans doublon ; un code garde son type.",
                exemple="R007",
            ),
            _c(
                "type",
                "Type de règle",
                T.CHOIX,
                obligatoire=True,
                choix=TypeRegle,
                exemple="PAUSE_DEJEUNER",
            ),
            _c("portee", "Portée", T.CHOIX, obligatoire=True, choix=NaturePortee, exemple="groupe"),
            _c(
                "cible",
                "Cible",
                T.CODE,
                aide="Code du groupe, identifiant de l'enseignant, code du campus, du type de "
                "séance ou de la matière ; un besoin s'écrit « matière / type / groupes » "
                "(ex. « IM.1101 / CM / A1 ») ; vide pour l'établissement.",
                exemple="A2-G3",
            ),
            _c(
                "parametres",
                "Paramètres",
                T.TEXTE,
                aide="« clé = valeur » séparés par des points-virgules, listes séparées par des "
                "virgules ; clés et valeurs selon le type (onglet Lisez-moi) ; vide = défauts.",
                exemple="mode = libre ; debut = 11:45 ; fin = 14:15 ; duree = 1 h 15",
            ),
            _c(
                "niveau",
                "Niveau",
                T.CHOIX,
                choix=Niveau,
                aide="Vide = niveau par défaut du type.",
                exemple="important",
            ),
            _c("etat", "État", T.CHOIX, choix=EtatRegle, defaut=EtatRegle.ACTIVE, exemple="active"),
            _c(
                "semestre",
                "Semestre",
                T.CODE,
                aide="Code du semestre ; semestre et dates vides = toute l'année.",
                exemple="S1",
            ),
            _c(
                "date_debut",
                "Début de la période",
                T.DATE,
                aide="Période datée, au lieu d'un semestre.",
            ),
            _c("date_fin", "Fin de la période", T.DATE),
            _c("commentaire", "Commentaire", T.TEXTE, exemple="Demande de la responsable"),
            _c(
                "origine",
                "Origine",
                T.TEXTE,
                export_seulement=True,
                aide="Saisie, import, assistant, base ou reprise.",
            ),
            _c(
                "phrase",
                "Phrase",
                T.TEXTE,
                export_seulement=True,
                aide="Phrase générée de la règle.",
            ),
        ),
    ),
)
"""Les feuilles des données de l'établissement, dans l'ordre de traitement."""

LONGUEURS: dict[tuple[str, str], int] = {
    ("annees", "libelle"): 32,
    ("semestres", "annee"): 32,
    ("semestres", "code"): 16,
    ("semestres", "libelle"): 64,
    ("campus", "code"): 16,
    ("campus", "nom"): 128,
    ("campus", "prefixe_salle"): 16,
    ("trajets", "campus_a"): 16,
    ("trajets", "campus_b"): 16,
    ("salles", "code"): 32,
    ("salles", "campus"): 16,
    ("salles", "type"): 32,
    ("salles", "batiment"): 64,
    ("salles", "equipements"): 32,
    ("enseignants", "identifiant"): 64,
    ("enseignants", "civilite"): 16,
    ("enseignants", "nom"): 128,
    ("enseignants", "prenom"): 128,
    ("groupes", "annee"): 32,
    ("groupes", "code"): 64,
    ("groupes", "libelle"): 128,
    ("groupes", "parent"): 64,
    ("groupes", "chevauche"): 64,
    ("jours_fermes", "annee"): 32,
    ("jours_fermes", "libelle"): 128,
    ("jours_fermes", "groupe"): 64,
    ("etudiants", "identifiant"): 64,
    ("etudiants", "nom"): 128,
    ("etudiants", "prenom"): 128,
    ("inscriptions", "annee"): 32,
    ("inscriptions", "etudiant"): 64,
    ("inscriptions", "groupe"): 64,
    ("matieres", "code"): 32,
    ("matieres", "libelle"): 128,
    ("matieres", "annee"): 32,
    ("matieres", "promotion"): 64,
    ("besoins", "annee"): 32,
    ("besoins", "semestre"): 16,
    ("besoins", "matiere"): 32,
    ("besoins", "promotion"): 64,
    ("besoins", "type"): 32,
    ("besoins", "libelle"): 128,
    ("besoins", "groupes"): 64,
    ("besoins", "enseignants"): 64,
    ("besoins", "salle_imposee"): 49,  # « code@campus » : 32 + 1 + 16
    ("besoins", "campus_impose"): 16,
    ("besoins", "types_salle"): 32,
    ("besoins", "equipements"): 32,
    ("indisponibilites", "ressource"): 64,
    ("indisponibilites", "annee"): 32,
    ("indisponibilites", "motif"): 255,
    ("regles", "annee"): 32,
    ("regles", "code"): 16,
    ("regles", "cible"): 255,  # un besoin s'écrit « matière / type / groupes »
    ("regles", "semestre"): 16,
    ("seances", "matiere"): 32,
    ("seances", "type"): 32,
    ("seances", "groupes"): 64,
    ("seances", "enseignants"): 64,
    ("seances", "salle"): 49,
    ("seances", "libelle"): 128,
}
"""Plus grand nombre de caractères par colonne : celui de la colonne du modèle
(`edt.db.modeles`, vérifié par un test), ou du code référencé. SQLite ne
l'impose pas : sans cette borne, un texte de plusieurs milliers de caractères
entrerait et déformerait listes, menus et noms de fichiers."""

MAXIMA: dict[tuple[str, str], int] = {
    ("trajets", "duree"): 12 * 60,
    ("enseignants", "max_par_jour"): 24 * 60,
    ("enseignants", "max_par_semaine"): 7 * 24 * 60,
    ("besoins", "duree"): 24 * 60,
}
"""Plus grande durée admise, en minutes (au-delà, la valeur n'a pas de sens)."""


def _borner(feuille_: Feuille) -> Feuille:
    """La feuille avec les bornes de `LONGUEURS` et `MAXIMA` posées sur ses colonnes."""
    colonnes = tuple(
        dataclasses.replace(
            colonne,
            longueur=LONGUEURS.get((feuille_.nom, colonne.nom), colonne.longueur),
            maximum=MAXIMA.get((feuille_.nom, colonne.nom), colonne.maximum),
        )
        for colonne in feuille_.colonnes
    )
    return dataclasses.replace(feuille_, colonnes=colonnes)


FEUILLES = tuple(_borner(f) for f in FEUILLES)

FEUILLE_SEANCES = Feuille(
    "seances",
    "Séances",
    "Emploi du temps existant, une ligne par séance : datée (colonne date) ou "
    "hebdomadaire (colonne jour, la séance a lieu chaque semaine ouverte du semestre).",
    (
        _c(
            "date",
            "Date",
            T.DATE,
            aide="Date de la séance ; vide si la colonne jour est remplie.",
            exemple="05/10/2026",
        ),
        _c(
            "jour",
            "Jour",
            T.JOUR,
            aide="Séance hebdomadaire, si la date est vide.",
            exemple="lundi",
        ),
        _c("debut", "Début", T.HEURE, obligatoire=True, exemple="08:30"),
        _c("fin", "Fin", T.HEURE, obligatoire=True, exemple="10:30"),
        _c("matiere", "Matière", T.CODE, obligatoire=True, aide="Code module.", exemple="IM.1101"),
        _c(
            "type",
            "Type de séance",
            T.CODE,
            obligatoire=True,
            aide="Code d'un type de séance ; AUTRE pour un événement spécial (conférence, forum).",
            exemple="CM",
        ),
        _c("groupes", "Groupes", T.LISTE, obligatoire=True, exemple="A1"),
        _c(
            "enseignants",
            "Enseignants",
            T.LISTE,
            aide="Identifiants ; vide ou « à recruter » = enseignant fictif de la matière.",
            exemple="ENS001",
        ),
        _c(
            "salle",
            "Salle",
            T.CODE,
            aide="Code de la salle ; vide ou « à définir » = salle choisie par le générateur.",
            exemple="N016",
        ),
        _c(
            "statut",
            "Statut",
            T.CHOIX,
            choix=StatutSeance,
            aide="Vide = réalisée ; une séance annulée n'est pas comptée.",
            exemple="realisee",
        ),
        _c(
            "libelle",
            "Libellé",
            T.TEXTE,
            aide="Intitulé libre (événement spécial).",
            exemple="Forum entreprises",
        ),
    ),
)
FEUILLE_SEANCES = _borner(FEUILLE_SEANCES)
"""Le tableau plat d'un emploi du temps existant (import d'emploi du temps)."""

NOMS_LISEZ_MOI = frozenset({"lisez_moi", "lisezmoi", "explications", "aide", "readme", "lire_moi"})
"""Onglets et fichiers d'explications, sautés à la lecture."""


def feuille(nom: str) -> Feuille:
    """Feuille de nom `nom` (erreur de programmation si elle n'existe pas)."""
    for candidate in (*FEUILLES, FEUILLE_SEANCES):
        if candidate.nom == nom:
            return candidate
    raise KeyError(f"Pas de feuille {nom!r} dans le format")


def est_lisez_moi(nom: str) -> bool:
    """Vrai pour l'onglet ou le fichier d'explications (sauté à la lecture)."""
    return v.cle_mot(re.sub(r"\.[a-z0-9]+$", "", nom, flags=re.IGNORECASE)) in NOMS_LISEZ_MOI


def identifier_feuille(
    nom: str, entetes: Sequence[str], feuilles: Sequence[Feuille] = FEUILLES
) -> Feuille | None:
    """Feuille désignée par un nom d'onglet ou de fichier, à défaut par ses en-têtes.

    Le nom est reconnu sans casse ni accents, par le nom de la feuille ou son
    titre, éventuellement suivi d'un complément (« salles_ndc.csv »). Sinon,
    c'est la feuille dont toutes les colonnes obligatoires sont présentes et
    qui reconnaît le plus d'en-têtes, si elle est seule dans ce cas.
    """
    base = re.split(r"[/\\]", nom)[-1]  # dernier élément d'un chemin d'archive
    mot = v.cle_mot(re.sub(r"\.[a-z0-9]+$", "", base, flags=re.IGNORECASE))
    par_nom = []
    for candidate in feuilles:
        for forme in (candidate.nom, v.cle_mot(candidate.titre)):
            if mot == forme or mot.startswith(forme + "_"):
                par_nom.append((len(forme), candidate))
    if par_nom:
        return max(par_nom, key=lambda paire: paire[0])[1]

    scores = []
    for candidate in feuilles:
        reconnues = {c.nom for e in entetes if (c := candidate.reconnaitre_entete(e)) is not None}
        if set(candidate.obligatoires) <= reconnues and reconnues:
            scores.append((len(reconnues), candidate))
    if not scores:
        return None
    meilleur = max(score for score, _ in scores)
    gagnantes = [candidate for score, candidate in scores if score == meilleur]
    return gagnantes[0] if len(gagnantes) == 1 else None


# --------------------------------------------------------------------------- #
# Documentation générée depuis la description
# --------------------------------------------------------------------------- #

ENTETES_DESCRIPTION = (
    "Colonne",
    "Libellé",
    "Type",
    "Obligatoire",
    "Identifiant",
    "Aide",
    "Exemple",
)


def lignes_description(feuille_: Feuille) -> list[tuple[str, ...]]:
    """Une ligne par colonne : nom, libellé, type, obligatoire, identifiant, aide, exemple."""
    return [
        (
            colonne.nom,
            colonne.libelle,
            LIBELLES_TYPES[colonne.type],
            "oui" if colonne.obligatoire else "",
            "oui" if colonne.identifiant else "",
            colonne.aide_complete,
            colonne.exemple,
        )
        for colonne in feuille_.colonnes
    ]


def tableau_markdown(feuille_: Feuille) -> str:
    """Tableau Markdown des colonnes d'une feuille (repris tel quel dans la documentation)."""

    def cellule(texte: str) -> str:
        return texte.replace("|", "\\|") if texte else ""

    lignes = [
        "| " + " | ".join(ENTETES_DESCRIPTION) + " |",
        "|" + "|".join("---" for _ in ENTETES_DESCRIPTION) + "|",
    ]
    for ligne in lignes_description(feuille_):
        nom, *reste = ligne
        lignes.append("| " + " | ".join([f"`{nom}`", *(cellule(x) for x in reste)]) + " |")
    return "\n".join(lignes)


def documentation_markdown() -> str:
    """Section « Feuilles » de `docs/import-export.md`, générée depuis la description."""
    blocs = []
    for numero, feuille_ in enumerate((*FEUILLES, FEUILLE_SEANCES), start=1):
        facultative = " (facultative)" if feuille_.facultative else ""
        identifiant = ", ".join(f"`{nom}`" for nom in feuille_.identifiant) or "aucun"
        blocs.append(
            f"#### {numero}. `{feuille_.nom}` : {feuille_.titre}{facultative}\n\n"
            f"{feuille_.description}\n\n"
            f"Identifiant métier : {identifiant}.\n\n"
            f"{tableau_markdown(feuille_)}"
        )
    blocs.append(
        "#### Types de règle et paramètres (colonnes `type` et `parametres` de `regles`)\n\n"
        + types_regles_markdown()
    )
    return "\n\n".join(blocs)


_SORTES_CHAMP = {
    "jours": "liste de jours",
    "heure": "heure",
    "duree": "durée",
    "entier": "nombre",
    "choix": "choix",
    "codes": "liste de codes",
    "code": "code",
    "moments": "liste de jours ou de demi-journées",
}


def description_parametre(champ: Any) -> str:
    """« duree (durée ; 15 à 180 min ; si mode = libre) » : un paramètre d'un type de règle."""
    if champ.choix:
        morceaux = [" / ".join(valeur for valeur, _ in champ.choix)]
    else:
        morceaux = [_SORTES_CHAMP[champ.sorte.value]]
    if champ.minimum is not None and champ.maximum is not None:
        unite = f" {champ.unite}" if champ.unite else ""
        morceaux.append(f"{champ.minimum} à {champ.maximum}{unite}")
    if champ.minimum_elements:
        morceaux.append(f"au moins {champ.minimum_elements}")
    if champ.obligatoire:
        morceaux.append("obligatoire")
    elif champ.defaut not in (None, [], ()):
        morceaux.append(f"défaut {champ.defaut}")
    elif champ.defaut_global is not None:
        morceaux.append("défaut : paramètre global")
    if champ.condition is not None:
        autre, valeurs = champ.condition
        morceaux.append(f"si {autre} = {' ou '.join(valeurs)}")
    return f"{champ.nom} ({' ; '.join(morceaux)})"


def lignes_types_regles() -> list[tuple[str, str, str, str, str]]:
    """Par type de règle : code, libellé, portées admises, paramètres, niveau par défaut."""
    lignes = []
    for code, definition in CATALOGUE_TYPES.items():
        refusees = {nature for nature, _ in definition.refus}
        admises = definition.portees - refusees
        portees = ", ".join(p.value for p in NaturePortee if p in admises)
        parametres = "; ".join(description_parametre(c) for c in definition.champs) or "aucun"
        lignes.append(
            (code.value, definition.libelle, portees, parametres, definition.niveau_defaut.value)
        )
    return lignes


def types_regles_markdown() -> str:
    """Tableau Markdown des types de règle et de leurs paramètres."""
    lignes = [
        "| Type | Libellé | Portées admises | Paramètres | Niveau par défaut |",
        "|---|---|---|---|---|",
    ]
    for code, libelle, portees, parametres, niveau in lignes_types_regles():
        lignes.append(f"| `{code}` | {libelle} | {portees} | {parametres} | {niveau} |")
    return "\n".join(lignes)


def texte_lisez_moi(listes: Mapping[str, Iterable[tuple[str, str]]] | None = None) -> list[str]:
    """Paragraphes de l'onglet d'explications (et du fichier LISEZ-MOI.txt de l'archive).

    `listes` : codes des listes configurables présents en base, par titre
    (« Types de salle » -> [(code, libellé), ...]).
    """
    paragraphes = [
        "Format d'échange des données de l'établissement (emplois du temps EDT).",
        "Chaque onglet (ou chaque fichier CSV) décrit une table ; la première ligne porte les "
        "en-têtes. Les colonnes peuvent être dans n'importe quel ordre ; les colonnes inconnues "
        "sont conservées telles quelles quand c'est possible et signalées dans le rapport.",
        "Import partiel : chaque ligne valide entre, chaque ligne invalide est listée dans le "
        "rapport (fichier, feuille, ligne, colonne, message). Une simulation montre le bilan "
        "sans rien écrire. Réimporter un fichier met à jour les lignes existantes par leur "
        "identifiant métier : aucun doublon n'est créé, rien n'est supprimé (sauf « tout remplacer »).",
        "Formats acceptés : dates 05/10/2026 ou 2026-10-05 ; heures 08:30, 8h30, 8 h 30 ; "
        "durées 2 h, 1 h 30, 90 min ; oui/non, vrai/faux, 1/0 ; listes séparées par ; ou , ; "
        "les codes sont comparés sans tenir compte de la casse ni des espaces superflus.",
        "Ordre de traitement : " + ", ".join(f.nom for f in FEUILLES) + ".",
    ]
    for titre, entrees in (listes or {}).items():
        codes = ", ".join(f"{code} ({libelle})" for code, libelle in entrees)
        paragraphes.append(f"{titre} : {codes or 'aucun'}.")
    for feuille_ in FEUILLES:
        identifiant = ", ".join(feuille_.identifiant)
        paragraphes.append(
            f"Feuille {feuille_.nom} : {feuille_.description} Identifiant : {identifiant}."
        )
    paragraphes.append(
        "Types de règle (feuille regles) : colonne type, portées admises, puis les clés de la "
        "colonne parametres, écrites « clé = valeur ; clé = valeur »."
    )
    for code, libelle, portees, parametres, niveau in lignes_types_regles():
        paragraphes.append(
            f"{code} ({libelle}) : portées {portees} ; paramètres : {parametres} ; "
            f"niveau par défaut : {niveau}."
        )
    return paragraphes
