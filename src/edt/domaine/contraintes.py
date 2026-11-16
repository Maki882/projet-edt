"""Catalogue des contraintes : source unique pour le vérificateur, le solveur et l'interface.

Les codes sont ceux de la section 6 de `docs/decisions.md`. Ils sont
**stables** : on ne renomme jamais un code, on en ajoute. Toutes les
contraintes sont actives par défaut ; avant un calcul, l'utilisateur choisit
le **niveau** de chacune et chaque choix est tracé dans le résultat. Aucune
contrainte n'est jamais abandonnée automatiquement.

Niveaux
-------
Chaque contrainte a un niveau (`Niveau`), du plus exigeant au moins exigeant :

- **nécessaire** : contrainte dure, le moteur ne la viole jamais ; une
  entorse constatée par le vérificateur est une **erreur** ;
- **important** : contrainte souple à fort poids, le moteur ne l'enfreint
  que faute de choix ; une entorse est un **avertissement** ;
- **facultatif** : souhait, à faible poids ; une entorse est une **information**.

Certaines contraintes définissent le problème lui-même (une salle ne reçoit
qu'une séance à la fois, un verrou tient, le volume demandé est placé...) :
elles sont toujours nécessaires et `Contrainte.modifiable` vaut faux. Les
autres ont un niveau par défaut (`Contrainte.niveau_defaut`) que l'utilisateur
peut changer.

Les niveaux retenus pour un calcul forment un `Niveaux` (`dict[CodeContrainte,
Niveau]`). Convention : **une clé absente est une contrainte ignorée**, ni
posée par le solveur ni vérifiée par le vérificateur ; c'est ainsi que
`niveaux_depuis_desactivees` traduit un calcul enregistré sans niveaux, qui
ne porte que des contraintes désactivées. `normaliser_niveaux` produit toujours un
dictionnaire complet. Côté vérificateur, la gravité d'une anomalie découle du
niveau : nécessaire donne une erreur, important un avertissement, facultatif
une information. Côté solveur, une entorse à une contrainte souple coûte
`POIDS_NIVEAU[niveau]` dans l'objectif.

Pour ajouter une contrainte : ajouter un membre à `CodeContrainte`, son
entrée dans `CATALOGUE` (avec son niveau par défaut et, si elle définit le
problème, `_TOUJOURS_NECESSAIRES`), puis l'implémenter dans le vérificateur
**et** dans le solveur (le test `tests/unitaires/socle/test_contrats.py`
vérifie que le catalogue couvre tous les codes).

Les préférences (objectif à minimiser) sont décrites par `PREFERENCES` ;
leurs poids sont des paramètres globaux réglables (`edt.domaine.parametres`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
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
    REGLE = "REGLE"


class Niveau(StrEnum):
    """Niveau d'exigence d'une contrainte, du plus exigeant au moins exigeant."""

    NECESSAIRE = "necessaire"
    IMPORTANT = "important"
    FACULTATIF = "facultatif"

    @property
    def libelle(self) -> str:
        """Libellé français affichable."""
        return _LIBELLES_NIVEAU[self]

    @property
    def description(self) -> str:
        """Une phrase pour l'interface : ce que le niveau implique."""
        return _DESCRIPTIONS_NIVEAU[self]


_LIBELLES_NIVEAU = {
    Niveau.NECESSAIRE: "Nécessaire",
    Niveau.IMPORTANT: "Important",
    Niveau.FACULTATIF: "Facultatif",
}
_DESCRIPTIONS_NIVEAU = {
    Niveau.NECESSAIRE: "Toujours respectée : un emploi du temps qui l'enfreint est refusé.",
    Niveau.IMPORTANT: "Respectée sauf si rien d'autre n'est possible ; une entorse est signalée "
    "comme avertissement.",
    Niveau.FACULTATIF: "Souhait : respectée quand c'est possible, une entorse est seulement "
    "signalée pour information.",
}

POIDS_NIVEAU: dict[Niveau, int] = {Niveau.IMPORTANT: 1000, Niveau.FACULTATIF: 20}
"""Pénalité d'une entorse dans l'objectif du solveur, par niveau souple.

À comparer aux préférences : 10 par trou, 5 par changement de campus, 2 par
samedi, 1 par créneau extrême. Une contrainte importante vaut donc une
centaine de trous-semaines (sept trous sur un semestre de quinze semaines),
plus que tout gain de confort réaliste d'une seule entorse ; une facultative
deux trous-semaines, qu'un vrai gain de confort peut emporter. Valeurs
calibrées sur les établissements fictifs moyen et grand.
"""

Niveaux = dict[CodeContrainte, Niveau]
"""Niveaux retenus pour un calcul ; une clé absente = contrainte ignorée (voir le module)."""


@dataclass(frozen=True)
class Contrainte:
    """Une entrée du catalogue.

    - `active_par_defaut` : la contrainte est proposée cochée avant un calcul ;
    - `niveau_defaut` : niveau proposé avant un calcul ;
    - `modifiable` : faux pour une contrainte qui définit le problème : elle
      reste toujours nécessaire, l'utilisateur ne choisit pas son niveau.
    """

    code: CodeContrainte
    libelle: str
    description: str
    active_par_defaut: bool = True
    niveau_defaut: Niveau = Niveau.NECESSAIRE
    modifiable: bool = True


_TOUJOURS_NECESSAIRES: frozenset[CodeContrainte] = frozenset(
    {
        CodeContrainte.SALLE_UNIQUE,
        CodeContrainte.ENSEIGNANT_UNIQUE,
        CodeContrainte.GROUPE_UNIQUE,
        CodeContrainte.VERROU,
        CodeContrainte.JOUR_FERME,
        CodeContrainte.VOLUME,
        CodeContrainte.REGLE,
    }
)
"""Contraintes qui définissent le problème : toujours nécessaires, niveau non modifiable.

`REGLE` y figure pour une autre raison : chaque règle personnalisée porte son
propre niveau, le code n'en a pas à choisir (il n'apparaît pas dans le tableau
des niveaux de la page Générer).
"""

_IMPORTANTES_PAR_DEFAUT: frozenset[CodeContrainte] = frozenset(
    {
        CodeContrainte.PAUSE,
        CodeContrainte.DEJEUNER,
        CodeContrainte.EQUIPEMENT,
        CodeContrainte.MAX_ENSEIGNANT,
    }
)
"""Contraintes proposées au niveau « important » ; toutes les autres sont nécessaires par défaut."""


def _c(code: CodeContrainte, libelle: str, description: str) -> tuple[CodeContrainte, Contrainte]:
    return code, Contrainte(
        code=code,
        libelle=libelle,
        description=description,
        niveau_defaut=Niveau.IMPORTANT if code in _IMPORTANTES_PAR_DEFAUT else Niveau.NECESSAIRE,
        modifiable=code not in _TOUJOURS_NECESSAIRES,
    )


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
            "Deux groupes qui ont des étudiants en commun (l'un contient l'autre, ou ils "
            "partagent des étudiants) n'ont pas cours en même temps.",
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
            "Un groupe ou un enseignant a une pause entre deux séances qui se suivent (durée "
            "réglée dans les paramètres).",
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
            "Heures maximales des enseignants",
            "Les heures maximales par jour et par semaine d'un enseignant sont respectées "
            "lorsqu'elles sont renseignées.",
        ),
        _c(
            CodeContrainte.VOLUME,
            "Toutes les séances placées",
            "Chaque séance demandée par les besoins a un jour, une heure et une salle.",
        ),
        _c(
            CodeContrainte.VERROU,
            "Séances verrouillées",
            "Une séance verrouillée reste à sa place (jour, heure et salle).",
        ),
        _c(
            CodeContrainte.JOUR_FERME,
            "Jours fermés",
            "Aucune séance un jour fermé pour les groupes concernés.",
        ),
        _c(
            CodeContrainte.REGLE,
            "Règle personnalisée",
            "Chaque règle personnalisée de l'établissement est respectée, à son propre niveau.",
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


def niveau(valeur: str) -> Niveau:
    """Niveau pour `valeur` (« necessaire », « important », « facultatif ») ; erreur sinon."""
    try:
        return Niveau(valeur)
    except ValueError as erreur:
        raise ErreurDonneesInvalides(
            f"Niveau inconnu : {valeur!r} (attendu : "
            + ", ".join(f"« {membre.value} »" for membre in Niveau)
            + ")."
        ) from erreur


def niveaux_par_defaut() -> Niveaux:
    """Niveau par défaut de chaque contrainte du catalogue (toutes présentes)."""
    return {code: entree.niveau_defaut for code, entree in CATALOGUE.items()}


def normaliser_niveaux(choix: Mapping[str, str]) -> Niveaux:
    """Valide les niveaux choisis par l'utilisateur et renvoie un dictionnaire complet.

    Les codes absents de `choix` prennent leur niveau par défaut. Lève
    `ErreurDonneesInvalides` pour un code ou un niveau inconnu, et pour un
    niveau autre que « nécessaire » sur une contrainte non modifiable.
    """
    niveaux = niveaux_par_defaut()
    for code_choisi, valeur in choix.items():
        entree = contrainte(code_choisi)
        retenu = niveau(valeur)
        if not entree.modifiable and retenu is not Niveau.NECESSAIRE:
            raise ErreurDonneesInvalides(
                f"La contrainte « {entree.libelle} » ({entree.code}) définit le problème : elle "
                f"reste nécessaire, son niveau ne peut pas être « {retenu.libelle} »."
            )
        niveaux[entree.code] = retenu
    return niveaux


def niveaux_depuis_desactivees(desactivees: Iterable[str] = ()) -> Niveaux:
    """Niveaux d'un calcul qui ne connaissait que des contraintes désactivées.

    Les niveaux par défaut, sans les codes désactivés : une clé absente est
    une contrainte ignorée, ni posée ni vérifiée. Lève une erreur sur un code inconnu.
    """
    retirees = normaliser_codes(desactivees)
    return {code: n for code, n in niveaux_par_defaut().items() if code not in retirees}
