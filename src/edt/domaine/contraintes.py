"""Catalogue des contraintes : source unique pour le vérificateur, le solveur et l'interface.

Les codes sont ceux de la section 6 de `docs/decisions.md`. Ils sont
**stables** : on ne renomme jamais un code, on en ajoute. Toutes les
contraintes sont dures en V1 et actives par défaut ; l'utilisateur peut en
désactiver avant un calcul et chaque désactivation est tracée dans le
résultat. Aucune contrainte n'est jamais abandonnée automatiquement.

Pour ajouter une contrainte : ajouter un membre à `CodeContrainte`, son
entrée dans `CATALOGUE`, puis l'implémenter dans le vérificateur **et** dans
le solveur (le test `tests/socle/test_contrats.py` vérifie que le catalogue
couvre tous les codes).

Les préférences (objectif à minimiser) sont décrites par `PREFERENCES` ;
leurs poids sont des paramètres globaux réglables (`edt.domaine.parametres`).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from edt.erreurs import ErreurDonneesInvalides


class CodeContrainte(StrEnum):
    """Codes stables des contraintes, communs au vérificateur et au solveur."""

    SALLE_UNIQUE = "SALLE_UNIQUE"
    ENSEIGNANT_UNIQUE = "ENSEIGNANT_UNIQUE"
    GROUPE_UNIQUE = "GROUPE_UNIQUE"
    CAMPUS_TRAJET = "CAMPUS_TRAJET"
    ACCESSIBILITE = "ACCESSIBILITE"
    HORAIRES = "HORAIRES"
    JOURS = "JOURS"
    PAUSE = "PAUSE"
    DEJEUNER = "DEJEUNER"
    CAPACITE = "CAPACITE"
    TYPE_SALLE = "TYPE_SALLE"
    EQUIPEMENT = "EQUIPEMENT"
    DISPO_ENSEIGNANT = "DISPO_ENSEIGNANT"
    DISPO_SALLE = "DISPO_SALLE"
    DISPO_GROUPE = "DISPO_GROUPE"
    MAX_ENSEIGNANT = "MAX_ENSEIGNANT"
    VOLUME = "VOLUME"
    VERROU = "VERROU"
    JOUR_FERME = "JOUR_FERME"


@dataclass(frozen=True)
class Contrainte:
    """Une entrée du catalogue.

    `dure` et `poids` préparent les préférences des versions suivantes : en
    V1 toutes les contraintes sont dures et le poids est ignoré.
    """

    code: CodeContrainte
    libelle: str
    description: str
    active_par_defaut: bool = True
    dure: bool = True
    poids: int = 0


def _c(code: CodeContrainte, libelle: str, description: str) -> tuple[CodeContrainte, Contrainte]:
    return code, Contrainte(code=code, libelle=libelle, description=description)


CATALOGUE: dict[CodeContrainte, Contrainte] = dict(
    [
        _c(
            CodeContrainte.SALLE_UNIQUE,
            "Une séance par salle",
            "Une salle accueille une seule séance à la fois.",
        ),
        _c(
            CodeContrainte.ENSEIGNANT_UNIQUE,
            "Une séance par enseignant",
            "Un enseignant donne une seule séance à la fois.",
        ),
        _c(
            CodeContrainte.GROUPE_UNIQUE,
            "Une séance par groupe",
            "Deux groupes en conflit (l'un contient l'autre, ils sont déclarés chevauchants "
            "ou ils partagent un étudiant) n'ont pas cours en même temps.",
        ),
        _c(
            CodeContrainte.CAMPUS_TRAJET,
            "Trajet entre campus",
            "Pour un groupe ou un enseignant, deux séances du même jour sur deux campus "
            "différents sont séparées d'au moins le temps de trajet.",
        ),
        _c(
            CodeContrainte.ACCESSIBILITE,
            "Salle accessible",
            "Une séance dont un groupe requiert l'accessibilité a lieu dans une salle "
            "accessible (une accessibilité inconnue vaut « non », sauf réglage contraire).",
        ),
        _c(
            CodeContrainte.HORAIRES,
            "Horaires de la grille",
            "Une séance tient dans la plage d'ouverture et commence sur un début de créneau "
            "de la grille, sauf créneau imposé.",
        ),
        _c(
            CodeContrainte.JOURS,
            "Jours de cours",
            "Les séances ont lieu du lundi au vendredi ; le samedi matin est réservé aux "
            "types de séance qui l'autorisent.",
        ),
        _c(
            CodeContrainte.PAUSE,
            "Pause entre deux séances",
            "Deux séances consécutives d'une même ressource sont séparées par la pause "
            "réglementaire (15 minutes par défaut).",
        ),
        _c(
            CodeContrainte.DEJEUNER,
            "Pause déjeuner",
            "La plage de déjeuner reste libre pour chaque groupe et chaque enseignant.",
        ),
        _c(
            CodeContrainte.CAPACITE,
            "Capacité de la salle",
            "La capacité de la salle est supérieure ou égale à la somme des effectifs "
            "des groupes de la séance.",
        ),
        _c(
            CodeContrainte.TYPE_SALLE,
            "Type de salle",
            "La salle est d'un type accepté par le besoin.",
        ),
        _c(
            CodeContrainte.EQUIPEMENT,
            "Équipements de la salle",
            "La salle possède tous les équipements requis par le besoin.",
        ),
        _c(
            CodeContrainte.DISPO_ENSEIGNANT,
            "Disponibilité des enseignants",
            "Aucune séance pendant une indisponibilité de l'un de ses enseignants.",
        ),
        _c(
            CodeContrainte.DISPO_SALLE,
            "Disponibilité des salles",
            "Aucune séance dans une salle pendant une indisponibilité de cette salle.",
        ),
        _c(
            CodeContrainte.DISPO_GROUPE,
            "Disponibilité des groupes",
            "Aucune séance pendant une indisponibilité de l'un de ses groupes.",
        ),
        _c(
            CodeContrainte.MAX_ENSEIGNANT,
            "Maxima horaires des enseignants",
            "Les maxima par jour et par semaine d'un enseignant sont respectés lorsqu'ils "
            "sont renseignés.",
        ),
        _c(
            CodeContrainte.VOLUME,
            "Volume demandé",
            "Toutes les séances demandées par les besoins sont placées.",
        ),
        _c(
            CodeContrainte.VERROU,
            "Séances verrouillées",
            "Une séance verrouillée reste à sa place (jour, heure et salle).",
        ),
        _c(
            CodeContrainte.JOUR_FERME,
            "Jours fermés",
            "Aucune séance datée un jour fermé pour les groupes concernés.",
        ),
    ]
)


@dataclass(frozen=True)
class Preference:
    """Un terme de l'objectif à minimiser ; `cle_poids` est le paramètre global du poids."""

    code: str
    libelle: str
    description: str
    cle_poids: str


PREFERENCES: tuple[Preference, ...] = (
    Preference(
        "TROUS",
        "Trous des groupes",
        "Créneaux libres entre deux séances d'un groupe dans la même journée.",
        "poids_trous",
    ),
    Preference(
        "CHANGEMENT_CAMPUS",
        "Changements de campus",
        "Changements de campus d'un groupe ou d'un enseignant dans la même journée.",
        "poids_changement_campus",
    ),
    Preference(
        "SAMEDI",
        "Séances du samedi",
        "Séances placées le samedi matin.",
        "poids_samedi",
    ),
    Preference(
        "CRENEAUX_EXTREMES",
        "Créneaux extrêmes",
        "Séances placées sur le premier ou le dernier créneau de la journée.",
        "poids_creneaux_extremes",
    ),
)


def contrainte(code: str) -> Contrainte:
    """Entrée du catalogue pour `code` ; lève `ErreurDonneesInvalides` si le code est inconnu."""
    try:
        return CATALOGUE[CodeContrainte(code)]
    except ValueError as erreur:
        raise ErreurDonneesInvalides(f"Contrainte inconnue : {code!r}.") from erreur


def codes_par_defaut() -> frozenset[CodeContrainte]:
    """Codes des contraintes actives par défaut."""
    return frozenset(code for code, entree in CATALOGUE.items() if entree.active_par_defaut)


def normaliser_codes(codes: Iterable[str]) -> frozenset[CodeContrainte]:
    """Valide une liste de codes et la renvoie typée ; lève une erreur sur tout code inconnu."""
    return frozenset(contrainte(code).code for code in codes)


def codes_actifs(desactivees: Iterable[str] = ()) -> frozenset[CodeContrainte]:
    """Contraintes actives quand l'utilisateur a désactivé `desactivees`."""
    return codes_par_defaut() - normaliser_codes(desactivees)
