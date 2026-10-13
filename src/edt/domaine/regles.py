"""Règles personnalisées : catalogue des types, validation, phrase, résolution.

Conception validée : `docs/regles-personnalisees.md`. Une **règle** est une
donnée (saisie, importée, proposée par l'assistant) qui applique l'un des
types de ce catalogue à une **portée** (établissement, groupe, enseignant,
campus, type de séance, matière ou besoin) avec des **paramètres** validés
par le schéma du type, à un **niveau** (nécessaire, important, facultatif).

Ce module est pur (aucune base, aucun ORM). Il donne :

- les énumérations stables (`TypeRegle`, `NaturePortee`, `EtatRegle`,
  `OrigineRegle`, `SorteRegle`, `FamilleRegle`) ;
- la définition de chaque type (`CATALOGUE_TYPES`, `definition_type`) : sorte,
  famille, portées admises, schéma des paramètres, niveau par défaut, exemples ;
- la validation d'un brouillon (`valider_parametres`, `valider_portee`,
  `valider_regle`) : valeurs canoniques, une erreur par champ (`ErreurSaisie`,
  clé = chemin JSON comme `parametres.duree`) ;
- la phrase générée (`phrase`) ;
- la résolution d'une règle sur un instantané (`resoudre_regle`) et des
  réglages d'un instantané (`resoudre_reglages`), précédence des jours comprise ;
- les règles de base proposées à chaque année (`REGLES_DE_BASE`) ;
- le registre des implémentations (`enregistrer`, `types_non_couverts`), que
  le solveur et le vérificateur remplissent, un poseur et un vérificateur par type.

Valeurs canoniques des paramètres (ce qui est enregistré en base et échangé
en JSON) : jours en noms français (« lundi »), heures en « HH:MM », durées en
minutes, choix par leur valeur, cibles (campus, salles, types de séance) par
leur code, demi-journées en « lundi matin » / « lundi après-midi ».

Précédence des jours (§ 1.3 de la conception, décision 1)
------------------------------------------------------
Le **standard** d'une séance est `Parametres.jours_travailles`, plus le samedi
si son type de séance l'autorise et qu'aucune règle `JOURS_TRAVAILLES` ne vise
ce type. Chaque **axe** de la séance résout sa lignée du plus précis au moins
précis et ne retient que la première règle rencontrée : chaque groupe feuille
concerné (demi-groupe → classe → promotion), chaque enseignant non fictif,
l'enseignement (besoin → matière → type de séance), le lieu (campus examiné).
**Jours permis = intersection des valeurs des règles retenues ; sans aucune
règle retenue, le standard.** Un axe sans règle est neutre : il ne s'oppose
pas à l'ouverture d'un jour par un autre axe. Une séance placée le jour *j*
enfreint chaque règle retenue qui ne contient pas *j* (à son niveau) et le
standard (contrainte `JOURS`, niveau du calcul) si *j* est hors du standard
et qu'aucune règle retenue ne contient *j*.

Période d'une règle : sur la semaine type, une règle s'applique aux séances
dont les semaines actives croisent ses semaines couvertes ; sur les séances
datées, au jour près (`RegleResolue.semaines`, `Regle.couvre`).
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from edt.domaine.contraintes import CodeContrainte, Niveau
from edt.domaine.enumerations import JOURS, SAMEDI, DemiJournee, EnumLibelle, NatureGroupe
from edt.domaine.heures import duree_vers_texte, minutes_vers_hhmm, minutes_vers_texte
from edt.domaine.heures import texte_vers_minutes as _texte_vers_minutes
from edt.domaine.parametres import (
    DEJEUNER_AUCUN,
    DEJEUNER_FIXE,
    DEJEUNER_LIBRE,
    MODES_DEJEUNER,
    Parametres,
)
from edt.erreurs import ErreurDonneesInvalides

if TYPE_CHECKING:
    from edt.domaine.calendrier import Semaine
    from edt.domaine.instantane import Instantane


# --------------------------------------------------------------------------- #
# Énumérations
# --------------------------------------------------------------------------- #


class TypeRegle(EnumLibelle):
    """Types de règle, codes stables : on n'en renomme jamais, on en ajoute."""

    JOURS_TRAVAILLES = "JOURS_TRAVAILLES", "Jours travaillés"
    PLAGE_HORAIRE = "PLAGE_HORAIRE", "Horaires"
    CRENEAUX_INTERDITS = "CRENEAUX_INTERDITS", "Créneaux interdits"
    CAMPUS = "CAMPUS", "Campus imposés ou interdits"
    SALLES = "SALLES", "Salles imposées ou interdites"
    PAUSE_DEJEUNER = "PAUSE_DEJEUNER", "Pause déjeuner"
    PAUSE_MINIMALE = "PAUSE_MINIMALE", "Pause entre deux séances"
    MAX_PAR_JOUR = "MAX_PAR_JOUR", "Maximum par jour"
    JOURNEES_LIBRES = "JOURNEES_LIBRES", "Demi-journées ou journées libres"
    SANS_TROU = "SANS_TROU", "Trous limités"
    CAMPUS_PAR_JOUR = "CAMPUS_PAR_JOUR", "Un seul campus par jour"
    MATIERES_PAR_JOUR = "MATIERES_PAR_JOUR", "Matières par jour"
    ORDRE_SEMAINE = "ORDRE_SEMAINE", "Ordre dans la semaine"
    REPARTITION_MATIERE = "REPARTITION_MATIERE", "Regroupement ou étalement d'une matière"


class NaturePortee(EnumLibelle):
    """Nature de la portée d'une règle (pas de portée salle : décision 5)."""

    ETABLISSEMENT = "etablissement", "Établissement"
    GROUPE = "groupe", "Groupe"
    ENSEIGNANT = "enseignant", "Enseignant"
    CAMPUS = "campus", "Campus"
    TYPE_SEANCE = "type_seance", "Type de séance"
    MATIERE = "matiere", "Matière"
    BESOIN = "besoin", "Besoin"


class EtatRegle(EnumLibelle):
    """État d'une règle : seule une règle active est posée et vérifiée."""

    PROPOSEE = "proposee", "Proposée"
    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"


class OrigineRegle(EnumLibelle):
    """D'où vient une règle."""

    SAISIE = "saisie", "Saisie"
    IMPORT = "import", "Import"
    ASSISTANT = "assistant", "Assistant"
    BASE = "base", "Règle de base"
    REPRISE = "reprise", "Reprise de l'année précédente"


class SorteRegle(EnumLibelle):
    """Sur quoi porte un type : chaque séance, l'emploi du temps d'une personne, des paires."""

    SEANCE = "seance", "Séance"
    EMPLOI_DU_TEMPS = "emploi_du_temps", "Emploi du temps"
    RELATION = "relation", "Relation entre séances"


class FamilleRegle(EnumLibelle):
    """Réglage (exception à un paramètre global) ou exigence (qui s'ajoute)."""

    REGLAGE = "reglage", "Réglage"
    EXIGENCE = "exigence", "Exigence"


class NaturePersonne(StrEnum):
    """Les deux sortes de personnes qui ont un emploi du temps."""

    GROUPE = "groupe"
    ENSEIGNANT = "enseignant"


class SorteChamp(StrEnum):
    """Sorte d'un paramètre de règle (voir les valeurs canoniques en tête de module)."""

    JOURS = "jours"
    HEURE = "heure"
    DUREE = "duree"
    ENTIER = "entier"
    CHOIX = "choix"
    CODES = "codes"
    CODE = "code"
    MOMENTS = "moments"


PORTEES_COURANTES: frozenset[NaturePortee] = frozenset(NaturePortee)
PERSONNES_GROUPES, PERSONNES_ENSEIGNANTS, PERSONNES_TOUS = "groupes", "enseignants", "tous"
MODE_IMPOSER, MODE_INTERDIRE = "imposer", "interdire"
UNITE_MINUTES, UNITE_SEANCES = "minutes", "seances"
UNITE_DEMI_JOURNEE, UNITE_JOURNEE = "demi_journee", "journee"
MEME_JOUR, JOURS_DIFFERENTS = "meme_jour", "jours_differents"


class ErreurSaisie(ErreurDonneesInvalides):
    """Brouillon de règle invalide : un message par champ (clé = chemin JSON)."""

    def __init__(self, erreurs: Mapping[str, str], message: str = "Règle invalide.") -> None:
        self.erreurs: dict[str, str] = dict(erreurs)
        super().__init__(message, [f"{champ} : {texte}" for champ, texte in self.erreurs.items()])


# --------------------------------------------------------------------------- #
# Définition des types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ChampRegle:
    """Un paramètre d'un type de règle.

    - `defaut` : valeur canonique prise quand le brouillon ne dit rien ;
    - `defaut_global` : champ de `Parametres` qui fournit le défaut (valeur
      convertie dans la forme canonique) ;
    - `obligatoire` : sans défaut, le brouillon doit le donner ;
    - `entite` : pour `CODES` et `CODE`, l'entité désignée (« campus »,
      « salle », « type_seance ») ;
    - `condition` : le champ ne compte que si un autre champ a l'une des valeurs
      données, par exemple `duree` seulement en mode libre ;
    - `minimum_vide` : pour une liste, nombre minimal d'éléments.
    """

    nom: str
    sorte: SorteChamp
    libelle: str
    description: str = ""
    unite: str = ""
    defaut: Any = None
    defaut_global: str | None = None
    obligatoire: bool = False
    minimum: int | None = None
    maximum: int | None = None
    choix: tuple[tuple[str, str], ...] = ()
    entite: str | None = None
    condition: tuple[str, tuple[str, ...]] | None = None
    minimum_elements: int = 0

    def concerne(self, valeurs: Mapping[str, Any]) -> bool:
        """Vrai si le champ s'applique étant donné les autres valeurs (voir `condition`)."""
        if self.condition is None:
            return True
        autre, permises = self.condition
        return valeurs.get(autre) in permises


@dataclass(frozen=True)
class DefinitionType:
    """Définition d'un type de règle."""

    code: TypeRegle
    sorte: SorteRegle
    famille: FamilleRegle
    portees: frozenset[NaturePortee]
    champs: tuple[ChampRegle, ...]
    niveau_defaut: Niveau
    description: str
    exemples: tuple[str, ...]
    refus: tuple[tuple[NaturePortee, str], ...] = ()
    """Portées admises par principe mais refusées avec une explication (voir § 1.4)."""

    @property
    def libelle(self) -> str:
        """Libellé du type."""
        return self.code.libelle

    def champ(self, nom: str) -> ChampRegle:
        """Le champ `nom` ; lève `ErreurDonneesInvalides` s'il n'existe pas."""
        for champ in self.champs:
            if champ.nom == nom:
                return champ
        raise ErreurDonneesInvalides(f"Le type {self.code} n'a pas de paramètre « {nom} ».")


_CHAMP_PERSONNES = ChampRegle(
    "personnes",
    SorteChamp.CHOIX,
    "Personnes concernées",
    "Pour la portée établissement : les groupes, les enseignants ou les deux.",
    defaut=PERSONNES_GROUPES,
    choix=(
        (PERSONNES_GROUPES, "Les groupes"),
        (PERSONNES_ENSEIGNANTS, "Les enseignants"),
        (PERSONNES_TOUS, "Les groupes et les enseignants"),
    ),
)

_JOURS_SEMAINE = frozenset(NaturePortee) - {NaturePortee.ETABLISSEMENT}


def _d(
    code: TypeRegle,
    sorte: SorteRegle,
    famille: FamilleRegle,
    portees: Iterable[NaturePortee],
    champs: Iterable[ChampRegle],
    niveau: Niveau,
    description: str,
    exemples: Iterable[str],
    refus: Iterable[tuple[NaturePortee, str]] = (),
) -> tuple[TypeRegle, DefinitionType]:
    return code, DefinitionType(
        code=code,
        sorte=sorte,
        famille=famille,
        portees=frozenset(portees),
        champs=tuple(champs),
        niveau_defaut=niveau,
        description=description,
        exemples=tuple(exemples),
        refus=tuple(refus),
    )


_REFUS_BESOIN_SALLE = (
    NaturePortee.BESOIN,
    "Pour un seul besoin, renseigner sa salle ou son campus imposé dans le besoin lui-même.",
)
_REFUS_REGLAGE_ETABLISSEMENT = (
    NaturePortee.ETABLISSEMENT,
    "Pour tout l'établissement, modifier le paramètre global correspondant.",
)

CATALOGUE_TYPES: dict[TypeRegle, DefinitionType] = dict(
    [
        _d(
            TypeRegle.JOURS_TRAVAILLES,
            SorteRegle.SEANCE,
            FamilleRegle.REGLAGE,
            _JOURS_SEMAINE,
            [
                ChampRegle(
                    "jours",
                    SorteChamp.JOURS,
                    "Jours travaillés",
                    "Les jours où les séances peuvent avoir lieu ; peut ouvrir ou fermer un "
                    "jour par rapport au standard de l'établissement.",
                    obligatoire=True,
                    minimum_elements=1,
                ),
            ],
            Niveau.NECESSAIRE,
            "Les jours où la portée peut avoir cours. La règle la plus précise l'emporte le "
            "long de chaque lignée (demi-groupe, classe, promotion ; besoin, matière, type).",
            (
                "Le groupe A1 ne travaille pas le samedi.",
                "La classe A3-APP1 a aussi cours le samedi.",
                "M. Petit ne travaille que le lundi et le mardi.",
            ),
            [_REFUS_REGLAGE_ETABLISSEMENT],
        ),
        _d(
            TypeRegle.PLAGE_HORAIRE,
            SorteRegle.SEANCE,
            FamilleRegle.EXIGENCE,
            PORTEES_COURANTES,
            [
                ChampRegle(
                    "debut",
                    SorteChamp.HEURE,
                    "Au plus tôt",
                    "Heure avant laquelle une séance ne commence pas.",
                    defaut_global="ouverture_min",
                ),
                ChampRegle(
                    "fin",
                    SorteChamp.HEURE,
                    "Au plus tard",
                    "Heure après laquelle une séance ne finit pas.",
                    defaut_global="fermeture_min",
                ),
                ChampRegle(
                    "jours",
                    SorteChamp.JOURS,
                    "Jours visés",
                    "Vide : tous les jours.",
                    defaut=[],
                ),
            ],
            Niveau.NECESSAIRE,
            "Les séances de la portée tiennent entre deux heures, les jours visés.",
            (
                "Le lundi, les A3 commencent à 10 h au plus tôt.",
                "Le vendredi, l'établissement ferme à 17 h.",
            ),
        ),
        _d(
            TypeRegle.CRENEAUX_INTERDITS,
            SorteRegle.SEANCE,
            FamilleRegle.EXIGENCE,
            PORTEES_COURANTES,
            [
                ChampRegle(
                    "jours",
                    SorteChamp.JOURS,
                    "Jours",
                    "Les jours où la plage est interdite.",
                    obligatoire=True,
                    minimum_elements=1,
                ),
                ChampRegle(
                    "debut",
                    SorteChamp.HEURE,
                    "Début de la plage",
                    "Défaut : l'heure d'ouverture.",
                    defaut_global="ouverture_min",
                ),
                ChampRegle(
                    "fin",
                    SorteChamp.HEURE,
                    "Fin de la plage",
                    "Défaut : l'heure de fermeture.",
                    defaut_global="fermeture_min",
                ),
            ],
            Niveau.NECESSAIRE,
            "Aucune séance de la portée ne recoupe la plage, les jours visés.",
            (
                "Pas de CM le vendredi après-midi.",
                "Mme Roux n'a pas cours le lundi de 8 h 30 à 10 h 30.",
            ),
        ),
        _d(
            TypeRegle.CAMPUS,
            SorteRegle.SEANCE,
            FamilleRegle.EXIGENCE,
            [
                NaturePortee.ETABLISSEMENT,
                NaturePortee.GROUPE,
                NaturePortee.ENSEIGNANT,
                NaturePortee.TYPE_SEANCE,
                NaturePortee.MATIERE,
            ],
            [
                ChampRegle(
                    "mode",
                    SorteChamp.CHOIX,
                    "Imposer ou interdire",
                    obligatoire=True,
                    choix=((MODE_IMPOSER, "Imposer"), (MODE_INTERDIRE, "Interdire")),
                ),
                ChampRegle(
                    "campus",
                    SorteChamp.CODES,
                    "Campus",
                    "Codes des campus.",
                    obligatoire=True,
                    entite="campus",
                    minimum_elements=1,
                ),
            ],
            Niveau.NECESSAIRE,
            "Les séances de la portée ont lieu sur les campus imposés, ou jamais sur les "
            "campus interdits.",
            (
                "Les séances de la promotion A3 ont lieu à Issy.",
                "Les TP n'ont jamais lieu sur le site Stan.",
            ),
            [_REFUS_BESOIN_SALLE],
        ),
        _d(
            TypeRegle.SALLES,
            SorteRegle.SEANCE,
            FamilleRegle.EXIGENCE,
            [
                NaturePortee.GROUPE,
                NaturePortee.ENSEIGNANT,
                NaturePortee.TYPE_SEANCE,
                NaturePortee.MATIERE,
            ],
            [
                ChampRegle(
                    "mode",
                    SorteChamp.CHOIX,
                    "Imposer ou interdire",
                    obligatoire=True,
                    choix=((MODE_IMPOSER, "Imposer"), (MODE_INTERDIRE, "Interdire")),
                ),
                ChampRegle(
                    "salles",
                    SorteChamp.CODES,
                    "Salles",
                    "Codes des salles.",
                    obligatoire=True,
                    entite="salle",
                    minimum_elements=1,
                ),
            ],
            Niveau.NECESSAIRE,
            "Les séances de la portée ont lieu dans les salles imposées, ou jamais dans les "
            "salles interdites.",
            (
                "Les TP d'électronique des A2 ont lieu en N016 ou en N018.",
                "M. Garcia n'enseigne pas en L101.",
            ),
            [_REFUS_BESOIN_SALLE],
        ),
        _d(
            TypeRegle.PAUSE_DEJEUNER,
            SorteRegle.EMPLOI_DU_TEMPS,
            FamilleRegle.REGLAGE,
            [NaturePortee.GROUPE, NaturePortee.ENSEIGNANT],
            [
                ChampRegle(
                    "mode",
                    SorteChamp.CHOIX,
                    "Mode",
                    "Horaires déterminés, pause libre bornée ou pas de pause.",
                    defaut_global="dejeuner_mode",
                    choix=MODES_DEJEUNER,
                ),
                ChampRegle(
                    "debut",
                    SorteChamp.HEURE,
                    "Début",
                    "Début de la plage (mode fixe) ou de la fenêtre (mode libre).",
                    defaut_global="dejeuner_debut_min",
                    condition=("mode", (DEJEUNER_FIXE, DEJEUNER_LIBRE)),
                ),
                ChampRegle(
                    "fin",
                    SorteChamp.HEURE,
                    "Fin",
                    "Fin de la plage (mode fixe) ou de la fenêtre (mode libre).",
                    defaut_global="dejeuner_fin_min",
                    condition=("mode", (DEJEUNER_FIXE, DEJEUNER_LIBRE)),
                ),
                ChampRegle(
                    "duree",
                    SorteChamp.DUREE,
                    "Durée minimale",
                    "Mode libre : minutes libres d'un seul tenant dans la fenêtre.",
                    unite="min",
                    defaut_global="dejeuner_duree_min",
                    minimum=15,
                    maximum=180,
                    condition=("mode", (DEJEUNER_LIBRE,)),
                ),
            ],
            Niveau.IMPORTANT,
            "La pause déjeuner de chaque personne de la portée, à la place du réglage de "
            "l'établissement.",
            (
                "La classe A2-G3 a une pause déjeuner libre d'au moins 1 h 15 entre 11 h 45 "
                "et 14 h 15.",
                "M. Petit déjeune de 12 h à 13 h.",
            ),
            [_REFUS_REGLAGE_ETABLISSEMENT],
        ),
        _d(
            TypeRegle.PAUSE_MINIMALE,
            SorteRegle.EMPLOI_DU_TEMPS,
            FamilleRegle.REGLAGE,
            [NaturePortee.GROUPE, NaturePortee.ENSEIGNANT],
            [
                ChampRegle(
                    "duree",
                    SorteChamp.DUREE,
                    "Pause minimale",
                    "Minutes entre deux séances consécutives.",
                    unite="min",
                    defaut_global="pause_min",
                    minimum=0,
                    maximum=120,
                ),
            ],
            Niveau.IMPORTANT,
            "L'écart minimal entre deux séances consécutives de chaque personne de la "
            "portée, à la place du réglage de l'établissement.",
            ("Le demi-groupe A1-G2a a au moins 30 min entre deux séances.",),
            [_REFUS_REGLAGE_ETABLISSEMENT],
        ),
        _d(
            TypeRegle.MAX_PAR_JOUR,
            SorteRegle.EMPLOI_DU_TEMPS,
            FamilleRegle.EXIGENCE,
            [NaturePortee.ETABLISSEMENT, NaturePortee.GROUPE, NaturePortee.ENSEIGNANT],
            [
                _CHAMP_PERSONNES,
                ChampRegle(
                    "unite",
                    SorteChamp.CHOIX,
                    "Unité",
                    "Minutes de cours ou nombre de séances.",
                    defaut=UNITE_MINUTES,
                    choix=((UNITE_MINUTES, "Minutes"), (UNITE_SEANCES, "Séances")),
                ),
                ChampRegle(
                    "maximum",
                    SorteChamp.ENTIER,
                    "Maximum par jour",
                    "De 30 à 720 minutes, ou de 1 à 8 séances.",
                    obligatoire=True,
                    minimum=1,
                    maximum=720,
                ),
            ],
            Niveau.IMPORTANT,
            "Chaque personne de la portée n'a pas plus que le maximum par jour.",
            (
                "Les A1 n'ont pas plus de 6 h de cours par jour.",
                "Mme Roux ne donne pas plus de 3 séances par jour.",
            ),
        ),
        _d(
            TypeRegle.JOURNEES_LIBRES,
            SorteRegle.EMPLOI_DU_TEMPS,
            FamilleRegle.EXIGENCE,
            [NaturePortee.ETABLISSEMENT, NaturePortee.GROUPE, NaturePortee.ENSEIGNANT],
            [
                _CHAMP_PERSONNES,
                ChampRegle(
                    "unite",
                    SorteChamp.CHOIX,
                    "Unité",
                    "Demi-journées ou journées.",
                    defaut=UNITE_DEMI_JOURNEE,
                    choix=((UNITE_DEMI_JOURNEE, "Demi-journées"), (UNITE_JOURNEE, "Journées")),
                ),
                ChampRegle(
                    "nombre",
                    SorteChamp.ENTIER,
                    "Nombre",
                    "Demi-journées ou journées libres par semaine.",
                    defaut=1,
                    minimum=1,
                    maximum=6,
                ),
                ChampRegle(
                    "parmi",
                    SorteChamp.MOMENTS,
                    "Parmi",
                    "Demi-journées ou jours parmi lesquels les prendre ; vide : tous les "
                    "jours travaillés.",
                    defaut=[],
                ),
            ],
            Niveau.IMPORTANT,
            "Chaque personne de la portée garde au moins ce nombre de demi-journées ou de "
            "journées travaillées sans séance.",
            (
                "Chaque enseignant a au moins une demi-journée libre par semaine.",
                "Mme Roux vient au plus deux jours par semaine.",
            ),
        ),
        _d(
            TypeRegle.SANS_TROU,
            SorteRegle.EMPLOI_DU_TEMPS,
            FamilleRegle.EXIGENCE,
            [NaturePortee.GROUPE, NaturePortee.ENSEIGNANT],
            [
                ChampRegle(
                    "maximum",
                    SorteChamp.ENTIER,
                    "Créneaux de trou par semaine",
                    "0 : aucun trou.",
                    defaut=0,
                    minimum=0,
                    maximum=10,
                ),
            ],
            Niveau.FACULTATIF,
            "Chaque personne de la portée a au plus ce nombre de créneaux de trou par semaine.",
            ("La classe A3-APP1 n'a aucun trou dans la semaine.",),
        ),
        _d(
            TypeRegle.CAMPUS_PAR_JOUR,
            SorteRegle.EMPLOI_DU_TEMPS,
            FamilleRegle.EXIGENCE,
            [NaturePortee.ETABLISSEMENT, NaturePortee.GROUPE, NaturePortee.ENSEIGNANT],
            [_CHAMP_PERSONNES],
            Niveau.IMPORTANT,
            "Les séances d'une journée de chaque personne de la portée sont sur un seul campus.",
            ("Les A3 ne changent pas de campus dans la journée.",),
        ),
        _d(
            TypeRegle.MATIERES_PAR_JOUR,
            SorteRegle.EMPLOI_DU_TEMPS,
            FamilleRegle.EXIGENCE,
            [NaturePortee.GROUPE],
            [
                ChampRegle(
                    "maximum",
                    SorteChamp.ENTIER,
                    "Matières par jour",
                    obligatoire=True,
                    minimum=1,
                    maximum=8,
                ),
            ],
            Niveau.FACULTATIF,
            "Chaque groupe de la portée n'a pas plus de ce nombre de matières différentes "
            "dans la journée.",
            ("Les A1 n'ont pas plus de 3 matières différentes par jour.",),
        ),
        _d(
            TypeRegle.ORDRE_SEMAINE,
            SorteRegle.RELATION,
            FamilleRegle.EXIGENCE,
            [NaturePortee.ETABLISSEMENT, NaturePortee.GROUPE, NaturePortee.MATIERE],
            [
                ChampRegle(
                    "avant",
                    SorteChamp.CODE,
                    "Type qui précède",
                    defaut="CM",
                    entite="type_seance",
                ),
                ChampRegle(
                    "apres",
                    SorteChamp.CODE,
                    "Type qui suit",
                    defaut="TD",
                    entite="type_seance",
                ),
            ],
            Niveau.FACULTATIF,
            "Pour chaque groupe feuille et chaque matière, toute séance du premier type "
            "commence avant toute séance du second dans la même semaine.",
            ("Dans chaque matière, le CM précède les TD de la semaine.",),
        ),
        _d(
            TypeRegle.REPARTITION_MATIERE,
            SorteRegle.RELATION,
            FamilleRegle.EXIGENCE,
            [NaturePortee.ETABLISSEMENT, NaturePortee.GROUPE, NaturePortee.MATIERE],
            [
                ChampRegle(
                    "mode",
                    SorteChamp.CHOIX,
                    "Mode",
                    obligatoire=True,
                    choix=((MEME_JOUR, "Le même jour"), (JOURS_DIFFERENTS, "Des jours différents")),
                ),
            ],
            Niveau.FACULTATIF,
            "Les séances d'une même matière d'un groupe tombent le même jour, ou au plus "
            "une par jour.",
            ("Les séances de mathématiques des A1 ont lieu des jours différents.",),
        ),
    ]
)


def definition_type(code: str) -> DefinitionType:
    """Définition du type `code` ; lève `ErreurDonneesInvalides` si le code est inconnu."""
    try:
        return CATALOGUE_TYPES[TypeRegle(code)]
    except ValueError as erreur:
        raise ErreurDonneesInvalides(
            f"Type de règle inconnu : {code!r} (types : "
            + ", ".join(t.value for t in TypeRegle)
            + ")."
        ) from erreur


# --------------------------------------------------------------------------- #
# Règle et portée
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Portee:
    """Portée d'une règle : une nature et, sauf pour l'établissement, une cible.

    `cible_code` et `cible_libelle` servent aux phrases ; `nature_groupe`
    précise le mot employé pour un groupe (promotion, classe, demi-groupe...).
    """

    nature: NaturePortee
    cible_id: int | None = None
    cible_code: str | None = None
    cible_libelle: str | None = None
    nature_groupe: NatureGroupe | None = None

    @property
    def designation(self) -> str:
        """Désignation avec article, en minuscules : « la classe A2-G3 », « M. Petit »."""
        code = self.cible_code or ""
        if self.nature is NaturePortee.ETABLISSEMENT:
            return "l'établissement"
        if self.nature is NaturePortee.GROUPE:
            mots = {
                NatureGroupe.PROMOTION: "la promotion",
                NatureGroupe.CLASSE: "la classe",
                NatureGroupe.DEMI_GROUPE: "le demi-groupe",
                NatureGroupe.TRANSVERSE: "le groupe",
                NatureGroupe.TIERS_TEMPS: "le groupe tiers-temps",
            }
            return f"{mots.get(self.nature_groupe, 'le groupe')} {code}"
        if self.nature is NaturePortee.ENSEIGNANT:
            return self.cible_libelle or code
        if self.nature is NaturePortee.CAMPUS:
            return f"le campus {self.cible_libelle or code}"
        if self.nature is NaturePortee.TYPE_SEANCE:
            return f"les {code}"
        if self.nature is NaturePortee.MATIERE:
            return f"la matière {self.cible_libelle or code}"
        return f"le besoin {self.cible_libelle or code}"

    @property
    def complement(self) -> str:
        """La désignation précédée de « de », contractée : « du besoin X », « des TP »."""
        designation = self.designation
        if designation.startswith("le "):
            return "du " + designation[3:]
        if designation.startswith("les "):
            return "des " + designation[4:]
        if designation.startswith("l'"):
            return "de " + designation
        return "de " + designation

    @property
    def est_personne(self) -> bool:
        """Vrai pour une portée dont on parle comme d'une personne (groupe, enseignant)."""
        return self.nature in (NaturePortee.GROUPE, NaturePortee.ENSEIGNANT)


@dataclass(frozen=True)
class Regle:
    """Une règle validée, aux valeurs canoniques (ce que la base enregistre)."""

    type: TypeRegle
    portee: Portee
    parametres: dict[str, Any]
    niveau: Niveau
    etat: EtatRegle = EtatRegle.ACTIVE
    id: int | None = None
    code: str | None = None
    annee_scolaire_id: int | None = None
    semestre_id: int | None = None
    semestre_code: str | None = None
    date_debut: dt.date | None = None
    date_fin: dt.date | None = None
    origine: OrigineRegle = OrigineRegle.SAISIE
    commentaire: str = ""
    reprise_de_id: int | None = None

    @property
    def definition(self) -> DefinitionType:
        """Définition de son type."""
        return CATALOGUE_TYPES[self.type]

    @property
    def active(self) -> bool:
        """Vrai si la règle est posée et vérifiée."""
        return self.etat is EtatRegle.ACTIVE

    def couvre(self, jour: dt.date) -> bool:
        """Vrai si la période datée de la règle contient `jour` (toujours vrai sans dates)."""
        if self.date_debut is not None and jour < self.date_debut:
            return False
        return self.date_fin is None or jour <= self.date_fin

    def semaines_couvertes(self, semaines: Iterable[Semaine]) -> frozenset[int]:
        """Numéros des semaines (d'un semestre) que la période datée recoupe."""
        return frozenset(
            semaine.numero
            for semaine in semaines
            if self.couvre(semaine.dimanche)
            and self.couvre(semaine.lundi)
            or any(self.couvre(semaine.date_du(jour)) for jour in range(7))
        )

    def periode_texte(self) -> str:
        """Période lisible : « toute l'année », « S1 », « du 01/10/2026 au 15/12/2026 »."""
        if self.semestre_code:
            return self.semestre_code
        if self.date_debut or self.date_fin:
            debut = f"du {self.date_debut:%d/%m/%Y}" if self.date_debut else ""
            fin = f"au {self.date_fin:%d/%m/%Y}" if self.date_fin else ""
            return " ".join(p for p in (debut, fin) if p)
        return "toute l'année"


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def _sans_accent(texte: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFD", texte.strip().lower())
        if not unicodedata.combining(c)
    )


_JOURS_NORMALISES = {_sans_accent(nom): numero for numero, nom in enumerate(JOURS)}
_DEMI_JOURNEES = {
    "matin": DemiJournee.MATIN,
    "apres-midi": DemiJournee.APRES_MIDI,
    "apres midi": DemiJournee.APRES_MIDI,
    "apres_midi": DemiJournee.APRES_MIDI,
    "am": DemiJournee.APRES_MIDI,
}
_MOTIF_DUREE = re.compile(r"^\s*(?:(?P<h>\d{1,2})\s*h)?\s*(?P<m>\d{1,3})?\s*(?:min)?\s*$", re.I)


def jour_depuis_texte(valeur: object) -> int:
    """Numéro d'un jour depuis un nom français (« Lundi », « lundi ») ou un numéro 0 à 6."""
    if isinstance(valeur, bool):
        raise ErreurDonneesInvalides(f"Jour invalide : {valeur!r}.")
    if isinstance(valeur, int):
        if 0 <= valeur <= 6:
            return valeur
        raise ErreurDonneesInvalides(f"Jour invalide : {valeur!r} (attendu : 0 à 6).")
    if isinstance(valeur, str):
        texte = _sans_accent(valeur)
        if texte in _JOURS_NORMALISES:
            return _JOURS_NORMALISES[texte]
        if texte.isdigit() and 0 <= int(texte) <= 6:
            return int(texte)
    raise ErreurDonneesInvalides(
        f"Jour invalide : {valeur!r} (attendu : " + ", ".join(JOURS) + ")."
    )


def heure_depuis_texte(valeur: object) -> int:
    """Minutes depuis minuit depuis « HH:MM », « 8 h 30 » ou un entier de minutes."""
    if isinstance(valeur, bool):
        raise ErreurDonneesInvalides(f"Heure invalide : {valeur!r}.")
    if isinstance(valeur, int):
        if 0 <= valeur <= 1440:
            return valeur
        raise ErreurDonneesInvalides(f"Heure invalide : {valeur!r} (minutes de 0 à 1440).")
    if isinstance(valeur, str):
        return _texte_vers_minutes(valeur)
    raise ErreurDonneesInvalides(f"Heure invalide : {valeur!r}.")


def duree_depuis_texte(valeur: object) -> int:
    """Minutes depuis un entier, « 75 », « 1 h 15 » ou « 45 min »."""
    if isinstance(valeur, bool):
        raise ErreurDonneesInvalides(f"Durée invalide : {valeur!r}.")
    if isinstance(valeur, int):
        return valeur
    if isinstance(valeur, str):
        lu = _MOTIF_DUREE.match(valeur)
        if lu and (lu["h"] or lu["m"]):
            return int(lu["h"] or 0) * 60 + int(lu["m"] or 0)
    raise ErreurDonneesInvalides(
        f"Durée invalide : {valeur!r} (attendu : des minutes, ex. 75 ou « 1 h 15 »)."
    )


def moment_depuis_texte(valeur: object) -> tuple[int, DemiJournee | None]:
    """Un jour (« mardi ») ou une demi-journée (« mardi matin », « mardi après-midi »)."""
    if isinstance(valeur, (list, tuple)) and len(valeur) == 2:
        jour = jour_depuis_texte(valeur[0])
        demi = valeur[1]
        if demi is None:
            return jour, None
        texte = _sans_accent(str(demi))
        if texte in _DEMI_JOURNEES:
            return jour, _DEMI_JOURNEES[texte]
        raise ErreurDonneesInvalides(f"Demi-journée invalide : {demi!r} (matin ou après-midi).")
    if isinstance(valeur, (int, str)) and not isinstance(valeur, bool):
        texte = _sans_accent(str(valeur))
        mots = texte.split(None, 1)
        if len(mots) == 2 and mots[1] in _DEMI_JOURNEES:
            return jour_depuis_texte(mots[0]), _DEMI_JOURNEES[mots[1]]
        return jour_depuis_texte(texte), None
    raise ErreurDonneesInvalides(f"Moment invalide : {valeur!r} (ex. « mardi », « mardi matin »).")


def moment_canonique(jour: int, demi: DemiJournee | None) -> str:
    """Forme canonique d'un moment : « mardi » ou « mardi après-midi »."""
    if demi is None:
        return JOURS[jour]
    return f"{JOURS[jour]} {'matin' if demi is DemiJournee.MATIN else 'après-midi'}"


def _defaut_global_canonique(champ: ChampRegle, parametres: Parametres) -> Any:
    valeur = getattr(parametres, champ.defaut_global)
    if champ.sorte is SorteChamp.HEURE:
        return minutes_vers_hhmm(int(valeur))
    return valeur


def _liste(valeur: object, nom: str) -> list:
    if isinstance(valeur, str):
        return [v.strip() for v in re.split(r"[,;]", valeur) if v.strip()]
    if isinstance(valeur, (list, tuple, set, frozenset)):
        return list(valeur)
    raise ErreurDonneesInvalides(f"Le paramètre « {nom} » attend une liste.")


def _canonique(
    champ: ChampRegle, valeur: object, codes_connus: Mapping[str, Collection[str]] | None
) -> Any:
    """Convertit une valeur de brouillon dans sa forme canonique, en la validant."""
    nom = champ.nom
    if champ.sorte is SorteChamp.JOURS:
        jours = sorted({jour_depuis_texte(v) for v in _liste(valeur, nom)})
        if len(jours) < champ.minimum_elements:
            raise ErreurDonneesInvalides("Au moins un jour est attendu.")
        return [JOURS[j] for j in jours]
    if champ.sorte is SorteChamp.HEURE:
        return minutes_vers_hhmm(heure_depuis_texte(valeur))
    if champ.sorte in (SorteChamp.DUREE, SorteChamp.ENTIER):
        nombre = duree_depuis_texte(valeur) if champ.sorte is SorteChamp.DUREE else valeur
        if isinstance(nombre, str) and nombre.strip().lstrip("-").isdigit():
            nombre = int(nombre)
        if not isinstance(nombre, int) or isinstance(nombre, bool):
            raise ErreurDonneesInvalides(f"Un nombre entier est attendu (lu : {valeur!r}).")
        if champ.minimum is not None and nombre < champ.minimum:
            raise ErreurDonneesInvalides(
                f"{champ.libelle} : au moins {champ.minimum} (lu : {nombre})."
            )
        if champ.maximum is not None and nombre > champ.maximum:
            raise ErreurDonneesInvalides(
                f"{champ.libelle} : au plus {champ.maximum} (lu : {nombre})."
            )
        return nombre
    if champ.sorte is SorteChamp.CHOIX:
        texte = _sans_accent(str(valeur)).replace(" ", "_") if valeur is not None else ""
        permises = [permise for permise, _ in champ.choix]
        if texte not in permises:
            liste = ", ".join(f"{libelle} (« {permise} »)" for permise, libelle in champ.choix)
            raise ErreurDonneesInvalides(f"Valeur {valeur!r} inconnue ; choisir parmi : {liste}.")
        return texte
    if champ.sorte is SorteChamp.CODES:
        codes = list(dict.fromkeys(str(v).strip() for v in _liste(valeur, nom) if str(v).strip()))
        if len(codes) < champ.minimum_elements:
            raise ErreurDonneesInvalides(
                f"Au moins un code de {champ.libelle.lower()} est attendu."
            )
        _verifier_codes(champ, codes, codes_connus)
        return codes
    if champ.sorte is SorteChamp.CODE:
        code = str(valeur).strip()
        if not code:
            raise ErreurDonneesInvalides(f"{champ.libelle} : un code est attendu.")
        _verifier_codes(champ, [code], codes_connus)
        return code
    if champ.sorte is SorteChamp.MOMENTS:
        moments = sorted({moment_depuis_texte(v) for v in _liste(valeur, nom)})
        return [moment_canonique(jour, demi) for jour, demi in moments]
    raise ErreurDonneesInvalides(f"Sorte de paramètre inconnue : {champ.sorte}.")


def _verifier_codes(
    champ: ChampRegle, codes: list[str], codes_connus: Mapping[str, Collection[str]] | None
) -> None:
    if codes_connus is None or champ.entite is None or champ.entite not in codes_connus:
        return
    inconnus = [code for code in codes if code not in codes_connus[champ.entite]]
    if inconnus:
        raise ErreurDonneesInvalides(
            f"{champ.libelle} : code(s) inconnu(s) : " + ", ".join(inconnus) + "."
        )


def valider_parametres(
    type_regle: TypeRegle,
    brouillon: Mapping[str, Any] | None,
    parametres: Parametres | None = None,
    codes_connus: Mapping[str, Collection[str]] | None = None,
    portee: NaturePortee | None = None,
) -> dict[str, Any]:
    """Paramètres canoniques d'une règle, ou `ErreurSaisie` (une erreur par champ).

    - les champs absents prennent leur défaut (celui du type, ou le paramètre
      global correspondant si `parametres` est donné) ;
    - `codes_connus` (`{"campus": codes, "salle": codes, "type_seance": codes}`)
      permet de refuser un code inconnu ;
    - `portee` permet les refus qui en dépendent (`MAX_PAR_JOUR` en minutes
      pour un enseignant).
    """
    definition = CATALOGUE_TYPES[type_regle]
    brouillon = dict(brouillon or {})
    erreurs: dict[str, str] = {}
    valeurs: dict[str, Any] = {}
    noms = {champ.nom for champ in definition.champs}
    for nom in brouillon:
        if nom not in noms:
            erreurs[f"parametres.{nom}"] = (
                f"Paramètre inconnu pour {definition.libelle} ; attendus : "
                + (", ".join(sorted(noms)) or "aucun")
                + "."
            )
    for champ in definition.champs:
        if not champ.concerne({**valeurs, **brouillon}):
            continue
        valeur = brouillon.get(champ.nom)
        if valeur is None or valeur == "":
            if champ.defaut_global is not None and parametres is not None:
                valeurs[champ.nom] = _defaut_global_canonique(champ, parametres)
            elif champ.defaut is not None:
                valeurs[champ.nom] = (
                    list(champ.defaut) if isinstance(champ.defaut, list) else champ.defaut
                )
            elif champ.obligatoire or champ.defaut_global is None:
                erreurs[f"parametres.{champ.nom}"] = f"{champ.libelle} : valeur attendue."
            continue
        try:
            valeurs[champ.nom] = _canonique(champ, valeur, codes_connus)
        except ErreurDonneesInvalides as erreur:
            erreurs[f"parametres.{champ.nom}"] = erreur.message
    if not erreurs:
        erreurs.update(_coherence(definition, valeurs, portee))
    if erreurs:
        raise ErreurSaisie(erreurs, f"Paramètres invalides pour {definition.libelle}.")
    return valeurs


def _coherence(
    definition: DefinitionType, valeurs: dict[str, Any], portee: NaturePortee | None
) -> dict[str, str]:
    """Contrôles entre champs, par type."""
    erreurs: dict[str, str] = {}
    code = definition.code
    heures = {
        nom: heure_depuis_texte(valeurs[nom])
        for nom in ("debut", "fin")
        if isinstance(valeurs.get(nom), str)
    }
    if code in (TypeRegle.PLAGE_HORAIRE, TypeRegle.CRENEAUX_INTERDITS) and "debut" in heures:
        if heures["debut"] >= heures["fin"]:
            erreurs["parametres.fin"] = (
                f"La fin doit venir après le début ({minutes_vers_texte(heures['debut'])})."
            )
    elif code is TypeRegle.PAUSE_DEJEUNER and valeurs.get("mode") != DEJEUNER_AUCUN:
        if "debut" in heures and "fin" in heures and heures["debut"] >= heures["fin"]:
            erreurs["parametres.fin"] = (
                f"La fin du déjeuner doit venir après son début "
                f"({minutes_vers_texte(heures['debut'])})."
            )
        elif valeurs.get("mode") == DEJEUNER_LIBRE and "duree" in valeurs:
            fenetre = heures["fin"] - heures["debut"]
            if valeurs["duree"] > fenetre:
                erreurs["parametres.duree"] = (
                    f"Une pause de {valeurs['duree']} min ne tient pas dans la fenêtre du "
                    f"déjeuner ({minutes_vers_texte(heures['debut'])} - "
                    f"{minutes_vers_texte(heures['fin'])}, soit {fenetre} min)."
                )
    elif code is TypeRegle.MAX_PAR_JOUR:
        unite, maximum = valeurs.get("unite"), valeurs.get("maximum")
        if unite == UNITE_SEANCES and isinstance(maximum, int) and maximum > 8:
            erreurs["parametres.maximum"] = f"Au plus 8 séances par jour (lu : {maximum})."
        if unite == UNITE_MINUTES and isinstance(maximum, int) and maximum < 30:
            erreurs["parametres.maximum"] = f"Au moins 30 minutes par jour (lu : {maximum})."
        if unite == UNITE_MINUTES and portee is NaturePortee.ENSEIGNANT:
            erreurs["parametres.unite"] = (
                "Pour un enseignant, le maximum en minutes se règle dans sa fiche "
                "(maxima horaires) ; ici, compter en séances."
            )
    elif code is TypeRegle.JOURNEES_LIBRES:
        unite = valeurs.get("unite")
        for moment in valeurs.get("parmi", ()):
            jour, demi = moment_depuis_texte(moment)
            if unite == UNITE_JOURNEE and demi is not None:
                erreurs["parametres.parmi"] = (
                    f"En journées, « parmi » ne contient que des jours (lu : « {moment} »)."
                )
            if unite == UNITE_DEMI_JOURNEE and demi is None:
                erreurs["parametres.parmi"] = (
                    "En demi-journées, « parmi » contient des demi-journées, par exemple "
                    f"« {JOURS[jour]} matin » (lu : « {moment} »)."
                )
    elif code is TypeRegle.ORDRE_SEMAINE and valeurs.get("avant") == valeurs.get("apres"):
        erreurs["parametres.apres"] = "Le type qui suit doit différer du type qui précède."
    return erreurs


def valider_portee(type_regle: TypeRegle, nature: NaturePortee) -> None:
    """Lève `ErreurSaisie` si le type n'admet pas cette portée, avec la raison."""
    definition = CATALOGUE_TYPES[type_regle]
    for refusee, raison in definition.refus:
        if refusee is nature:
            raise ErreurSaisie({"portee.nature": raison})
    if nature not in definition.portees:
        admises = ", ".join(sorted(p.libelle.lower() for p in definition.portees))
        raise ErreurSaisie(
            {
                "portee.nature": f"{definition.libelle} n'admet pas la portée "
                f"« {nature.libelle.lower()} » ; portées admises : {admises}."
            }
        )


def valider_regle(
    regle: Regle,
    parametres: Parametres | None = None,
    codes_connus: Mapping[str, Collection[str]] | None = None,
) -> Regle:
    """Revalide entièrement une règle (portée, paramètres, période) et la renvoie canonique."""
    erreurs: dict[str, str] = {}
    try:
        valider_portee(regle.type, regle.portee.nature)
    except ErreurSaisie as erreur:
        erreurs.update(erreur.erreurs)
    if regle.portee.nature is not NaturePortee.ETABLISSEMENT and regle.portee.cible_id is None:
        erreurs["portee.cible"] = "Une cible est attendue pour cette portée."
    valeurs = regle.parametres
    try:
        valeurs = valider_parametres(
            regle.type, regle.parametres, parametres, codes_connus, regle.portee.nature
        )
    except ErreurSaisie as erreur:
        erreurs.update(erreur.erreurs)
    if regle.semestre_id is not None and (regle.date_debut or regle.date_fin):
        erreurs["periode"] = "Choisir un semestre ou des dates, pas les deux."
    if regle.date_debut and regle.date_fin and regle.date_fin < regle.date_debut:
        erreurs["periode.date_fin"] = "La date de fin précède la date de début."
    if erreurs:
        raise ErreurSaisie(erreurs)
    return Regle(**{**regle.__dict__, "parametres": valeurs})


# --------------------------------------------------------------------------- #
# Phrase
# --------------------------------------------------------------------------- #


def _majuscule(texte: str) -> str:
    return texte[:1].upper() + texte[1:]


def _nombre(n: int) -> str:
    """« une », « deux », « trois »... jusqu'à six, sinon le chiffre."""
    return {1: "une", 2: "deux", 3: "trois", 4: "quatre", 5: "cinq", 6: "six"}.get(n, str(n))


def _jours_texte(jours: Iterable[int]) -> str:
    """« le lundi », « le lundi et le mardi », « du lundi au vendredi »."""
    jours = sorted(set(jours))
    if not jours:
        return "aucun jour"
    if len(jours) >= 3 and jours == list(range(jours[0], jours[-1] + 1)):
        return f"du {JOURS[jours[0]]} au {JOURS[jours[-1]]}"
    noms = [f"le {JOURS[j]}" for j in jours]
    return noms[0] if len(noms) == 1 else ", ".join(noms[:-1]) + " et " + noms[-1]


def _heure_texte(hhmm: str) -> str:
    return minutes_vers_texte(heure_depuis_texte(hhmm))


def _moments_texte(moments: Iterable[str]) -> str:
    noms = [f"le {m}" for m in moments]
    if not noms:
        return ""
    return noms[0] if len(noms) == 1 else ", ".join(noms[:-1]) + " et " + noms[-1]


def _sujet_personnes(portee: Portee, valeurs: Mapping[str, Any]) -> str:
    """Sujet d'une règle de sorte « emploi du temps » : « chaque groupe », « la classe A2-G3 »."""
    if portee.nature is NaturePortee.ETABLISSEMENT:
        return {
            PERSONNES_GROUPES: "chaque groupe",
            PERSONNES_ENSEIGNANTS: "chaque enseignant",
            PERSONNES_TOUS: "chaque groupe et chaque enseignant",
        }[valeurs.get("personnes", PERSONNES_GROUPES)]
    return portee.designation


def _sujet_seances(portee: Portee) -> tuple[str, bool]:
    """Sujet d'une règle de sorte « séance » et son pluriel : (« les TP », True)."""
    if portee.nature is NaturePortee.ETABLISSEMENT:
        return "l'établissement", False
    if portee.est_personne:
        return portee.designation, False
    if portee.nature is NaturePortee.TYPE_SEANCE:
        return portee.designation, True
    if portee.nature is NaturePortee.CAMPUS:
        return f"les séances sur {portee.designation}", True
    return f"les séances {portee.complement}", True


def phrase(
    type_regle: TypeRegle,
    portee: Portee,
    valeurs: Mapping[str, Any],
    parametres: Parametres | None = None,
) -> str:
    """Phrase française d'une règle aux valeurs canoniques.

    Les paramètres globaux, s'ils sont donnés, affinent la phrase : « ne
    travaille pas le samedi » plutôt que la liste des jours, « ferme à 17 h »
    plutôt que les deux heures.
    """
    v = valeurs
    code = type_regle
    standard_jours = parametres.jours_travailles if parametres is not None else None
    if code is TypeRegle.JOURS_TRAVAILLES:
        jours = {jour_depuis_texte(j) for j in v.get("jours", ())}
        sujet, pluriel = _sujet_seances(portee)
        verbe = "ont lieu" if pluriel else "travaille"
        if standard_jours is not None:
            standard = set(standard_jours)
            fermes, ouverts = standard - jours, jours - standard
            if fermes and not ouverts and len(fermes) <= len(jours):
                negation = "n'ont pas lieu" if pluriel else "ne travaille pas"
                return _majuscule(f"{sujet} {negation} {_jours_texte(fermes)}.")
            if ouverts and not fermes:
                verbe = "ont aussi lieu" if pluriel else "a aussi cours"
                return _majuscule(f"{sujet} {verbe} {_jours_texte(ouverts)}.")
        seulement = "n'ont lieu que" if pluriel else "ne travaille que"
        return _majuscule(f"{sujet} {seulement} {_jours_texte(jours)}.")
    if code is TypeRegle.PLAGE_HORAIRE:
        sujet, pluriel = _sujet_seances(portee)
        jours = v.get("jours") or ()
        prefixe = (
            _majuscule(_jours_texte(jour_depuis_texte(j) for j in jours)) + ", " if jours else ""
        )
        debut, fin = _heure_texte(v["debut"]), _heure_texte(v["fin"])
        debut_standard = (
            parametres is not None and heure_depuis_texte(v["debut"]) == parametres.ouverture_min
        )
        fin_standard = (
            parametres is not None and heure_depuis_texte(v["fin"]) == parametres.fermeture_min
        )
        if portee.nature is NaturePortee.ETABLISSEMENT:
            ouvre, ferme = f"ouvre à {debut}", f"ferme à {fin}"
            morceaux = [
                m
                for m, standard in ((ouvre, debut_standard), (ferme, fin_standard))
                if not standard
            ]
            corps = f"l'établissement {' et '.join(morceaux or [ouvre, ferme])}"
        elif pluriel:
            if debut_standard and not fin_standard:
                corps = f"{sujet} finissent à {fin} au plus tard"
            elif fin_standard and not debut_standard:
                corps = f"{sujet} commencent à {debut} au plus tôt"
            else:
                corps = f"{sujet} ont lieu entre {debut} et {fin}"
        elif debut_standard and not fin_standard:
            corps = f"{sujet} finit à {fin} au plus tard"
        elif fin_standard and not debut_standard:
            corps = f"{sujet} commence à {debut} au plus tôt"
        else:
            corps = f"{sujet} commence à {debut} au plus tôt et finit à {fin} au plus tard"
        return _majuscule(f"{prefixe}{corps}.")
    if code is TypeRegle.CRENEAUX_INTERDITS:
        sujet, pluriel = _sujet_seances(portee)
        jours = _jours_texte(jour_depuis_texte(j) for j in v["jours"])
        plage = f"de {_heure_texte(v['debut'])} à {_heure_texte(v['fin'])}"
        if portee.nature is NaturePortee.ETABLISSEMENT:
            return _majuscule(f"aucune séance {jours} {plage}.")
        negation = "n'ont pas lieu" if pluriel else "n'a pas cours"
        return _majuscule(f"{sujet} {negation} {jours} {plage}.")
    if code in (TypeRegle.CAMPUS, TypeRegle.SALLES):
        cles = v["campus"] if code is TypeRegle.CAMPUS else v["salles"]
        liste = (
            " ou ".join(f"en {c}" for c in cles)
            if code is TypeRegle.SALLES
            else " ou ".join(f"sur le campus {c}" for c in cles)
        )
        if portee.nature is NaturePortee.ETABLISSEMENT:
            sujet = "toutes les séances"
        elif portee.nature is NaturePortee.TYPE_SEANCE:
            sujet = portee.designation
        else:
            sujet = f"les séances {portee.complement}"
        if v["mode"] == MODE_IMPOSER:
            return _majuscule(f"{sujet} ont lieu {liste}.")
        return _majuscule(f"{sujet} n'ont jamais lieu {liste}.")
    if code is TypeRegle.PAUSE_DEJEUNER:
        sujet = portee.designation
        mode = v.get("mode")
        if mode == DEJEUNER_AUCUN:
            return _majuscule(f"{sujet} n'a pas de pause déjeuner réservée.")
        debut, fin = _heure_texte(v["debut"]), _heure_texte(v["fin"])
        if mode == DEJEUNER_FIXE:
            return _majuscule(f"{sujet} déjeune de {debut} à {fin}.")
        return _majuscule(
            f"{sujet} a une pause déjeuner libre d'au moins {duree_vers_texte(v['duree'])} "
            f"entre {debut} et {fin}."
        )
    if code is TypeRegle.PAUSE_MINIMALE:
        return _majuscule(
            f"{portee.designation} a au moins {duree_vers_texte(v['duree'])} entre deux séances."
        )
    if code is TypeRegle.MAX_PAR_JOUR:
        sujet = _sujet_personnes(portee, v)
        if v.get("unite") == UNITE_SEANCES:
            verbe = "ne donne pas" if portee.nature is NaturePortee.ENSEIGNANT else "n'a pas"
            return _majuscule(f"{sujet} {verbe} plus de {v['maximum']} séances par jour.")
        return _majuscule(
            f"{sujet} n'a pas plus de {duree_vers_texte(v['maximum'])} de cours par jour."
        )
    if code is TypeRegle.JOURNEES_LIBRES:
        sujet = _sujet_personnes(portee, v)
        nombre = v.get("nombre", 1)
        parmi = f" parmi {_moments_texte(v['parmi'])}" if v.get("parmi") else ""
        if v.get("unite") == UNITE_JOURNEE:
            if portee.nature is NaturePortee.ENSEIGNANT and standard_jours is not None:
                presence = len(set(standard_jours)) - nombre
                if presence >= 1 and not parmi:
                    jours_ = "jour" if presence == 1 else "jours"
                    return _majuscule(
                        f"{sujet} vient au plus {_nombre(presence)} {jours_} par semaine."
                    )
            unite = "journée libre" if nombre == 1 else "journées libres"
        else:
            unite = "demi-journée libre" if nombre == 1 else "demi-journées libres"
        return _majuscule(f"{sujet} a au moins {_nombre(nombre)} {unite} par semaine{parmi}.")
    if code is TypeRegle.SANS_TROU:
        maximum = v.get("maximum", 0)
        if maximum == 0:
            return _majuscule(f"{portee.designation} n'a aucun trou dans la semaine.")
        return _majuscule(
            f"{portee.designation} a au plus {maximum} créneau(x) de trou par semaine."
        )
    if code is TypeRegle.CAMPUS_PAR_JOUR:
        return _majuscule(f"{_sujet_personnes(portee, v)} ne change pas de campus dans la journée.")
    if code is TypeRegle.MATIERES_PAR_JOUR:
        return _majuscule(
            f"{portee.designation} n'a pas plus de {v['maximum']} matières différentes par jour."
        )
    if code is TypeRegle.ORDRE_SEMAINE:
        corps = f"le {v['avant']} précède les {v['apres']} de la semaine"
        if portee.nature is NaturePortee.MATIERE:
            return _majuscule(f"en {portee.cible_libelle or portee.cible_code}, {corps}.")
        if portee.nature is NaturePortee.GROUPE:
            return _majuscule(f"pour {portee.designation}, dans chaque matière, {corps}.")
        return _majuscule(f"dans chaque matière, {corps}.")
    if code is TypeRegle.REPARTITION_MATIERE:
        mode = "le même jour" if v["mode"] == MEME_JOUR else "des jours différents"
        if portee.nature is NaturePortee.MATIERE:
            return _majuscule(
                f"les séances de {portee.cible_libelle or portee.cible_code} ont lieu {mode} "
                "(pour chaque groupe)."
            )
        if portee.nature is NaturePortee.GROUPE:
            return _majuscule(
                f"les séances d'une même matière {portee.complement} ont lieu {mode}."
            )
        return _majuscule(f"les séances d'une même matière ont lieu {mode}.")
    raise ErreurDonneesInvalides(f"Pas de phrase pour le type {type_regle}.")


# --------------------------------------------------------------------------- #
# Règles de base
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RegleDeBase:
    """Une règle proposée à chaque nouvelle année (jamais nécessaire, jamais imposée)."""

    type: TypeRegle
    parametres: dict[str, Any]
    niveau: Niveau
    commentaire: str

    @property
    def phrase(self) -> str:
        """Phrase de la règle (portée établissement)."""
        return phrase(self.type, Portee(NaturePortee.ETABLISSEMENT), self.parametres)


REGLES_DE_BASE: tuple[RegleDeBase, ...] = (
    RegleDeBase(
        TypeRegle.ORDRE_SEMAINE,
        {"avant": "CM", "apres": "TD"},
        Niveau.FACULTATIF,
        "Règle de base : un cours magistral avant les travaux dirigés de la semaine.",
    ),
    RegleDeBase(
        TypeRegle.JOURNEES_LIBRES,
        {"personnes": PERSONNES_ENSEIGNANTS, "unite": UNITE_DEMI_JOURNEE, "nombre": 1, "parmi": []},
        Niveau.FACULTATIF,
        "Règle de base : une demi-journée sans cours par semaine pour chaque enseignant.",
    ),
)


# --------------------------------------------------------------------------- #
# Résolution sur un instantané
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Personne:
    """Une personne qui a un emploi du temps : un groupe feuille ou un enseignant non fictif."""

    nature: NaturePersonne
    id: int


@dataclass(frozen=True)
class Emploi:
    """L'emploi du temps d'une personne : ses séances et, pour un groupe, sa lignée.

    Un groupe feuille suit ses séances et celles de ses ascendants ; un
    enseignant suit les siennes. `lignee` va de la personne vers la racine.
    """

    personne: Personne
    seances: frozenset[str]
    lignee: tuple[int, ...] = ()


@dataclass(frozen=True)
class RegleResolue:
    """Une règle active, résolue sur un instantané : ce que lisent le solveur et le vérificateur.

    - `valeurs` : paramètres convertis (jours en numéros, heures et durées en
      minutes, cibles en identifiants, moments en couples (jour, demi-journée)) ;
    - `semaines` : semaines du semestre que la période de la règle recoupe ;
    - `seances` : séances concernées (sorte séance ou relation) ; pour une
      portée campus, toutes les séances, la règle ne jouant que pour celles
      placées sur `cible_id` ;
    - `personnes` : personnes concernées (sorte emploi du temps ou relation).
    """

    id: int
    code: str
    type: TypeRegle
    niveau: Niveau
    portee: NaturePortee
    cible_id: int | None
    phrase: str
    valeurs: dict[str, Any]
    semaines: frozenset[int]
    seances: frozenset[str] = frozenset()
    personnes: frozenset[Personne] = frozenset()
    date_debut: dt.date | None = None
    date_fin: dt.date | None = None

    def couvre(self, jour: dt.date) -> bool:
        """Vrai si la période datée de la règle contient `jour` (toujours vrai sans dates).

        Sur la semaine type, `semaines` suffit ; sur les séances datées, cette
        méthode affine au jour près.
        """
        if self.date_debut is not None and jour < self.date_debut:
            return False
        return self.date_fin is None or jour <= self.date_fin

    @property
    def definition(self) -> DefinitionType:
        """Définition de son type."""
        return CATALOGUE_TYPES[self.type]

    @property
    def sorte(self) -> SorteRegle:
        """Sorte du type (séance, emploi du temps, relation)."""
        return self.definition.sorte

    @property
    def famille(self) -> FamilleRegle:
        """Famille du type (réglage ou exigence)."""
        return self.definition.famille

    @property
    def jours(self) -> frozenset[int]:
        """Jours (numéros) d'une règle `JOURS_TRAVAILLES`, `PLAGE_HORAIRE` ou `CRENEAUX_INTERDITS`."""
        return frozenset(self.valeurs.get("jours", ()))

    def concerne_seance(self, seance_id: str, semaines: Iterable[int] | None = None) -> bool:
        """Vrai si la séance est concernée et, si `semaines` est donné, croise la période."""
        if seance_id not in self.seances:
            return False
        return semaines is None or not self.semaines.isdisjoint(semaines)


@dataclass(frozen=True)
class Source:
    """D'où vient une valeur retenue : une règle, ou le standard (un paramètre global)."""

    regle: RegleResolue | None = None
    contrainte: CodeContrainte | None = None

    @property
    def est_standard(self) -> bool:
        """Vrai pour une valeur venue des paramètres globaux."""
        return self.regle is None

    @property
    def texte(self) -> str:
        """« règle R007 » ou « établissement »."""
        return "établissement" if self.regle is None else f"règle {self.regle.code}"


@dataclass(frozen=True)
class ReglesJours:
    """Jours permis d'une séance : standard, règles retenues par axe, règles de campus."""

    seance_id: str
    standard: frozenset[int]
    retenues: tuple[RegleResolue, ...]
    par_campus: dict[int, RegleResolue]

    def jours_permis(self, campus_id: int | None = None) -> frozenset[int]:
        """Jours permis quand la séance est sur `campus_id` (ou sans considérer le lieu)."""
        explicites = [regle.jours for regle in self.retenues]
        if campus_id is not None and campus_id in self.par_campus:
            explicites.append(self.par_campus[campus_id].jours)
        if not explicites:
            return self.standard
        return frozenset.intersection(*explicites)

    def entorses(self, jour: int, campus_id: int | None = None) -> tuple[Source, ...]:
        """Sources enfreintes si la séance a lieu le `jour` sur `campus_id` (vide = permis)."""
        retenues = list(self.retenues)
        if campus_id is not None and campus_id in self.par_campus:
            retenues.append(self.par_campus[campus_id])
        sources = [Source(regle=regle) for regle in retenues if jour not in regle.jours]
        if jour not in self.standard and not any(jour in regle.jours for regle in retenues):
            sources.append(Source(contrainte=CodeContrainte.JOURS))
        return tuple(sources)

    @property
    def campus_regles(self) -> frozenset[int]:
        """Campus visés par une règle de jours."""
        return frozenset(self.par_campus)


@dataclass(frozen=True)
class ReglageDejeuner:
    """Pause déjeuner d'une personne : mode, bornes, durée, source."""

    mode: str
    debut: int
    fin: int
    duree: int
    source: Source

    @property
    def actif(self) -> bool:
        """Vrai si une pause est exigée."""
        return self.mode != DEJEUNER_AUCUN

    @property
    def plage(self) -> tuple[int, int] | None:
        """Plage réservée en mode fixe, sinon `None`."""
        return (self.debut, self.fin) if self.mode == DEJEUNER_FIXE else None

    @property
    def fenetre(self) -> tuple[int, int, int] | None:
        """`(début, fin, durée)` en mode libre, sinon `None`."""
        return (self.debut, self.fin, self.duree) if self.mode == DEJEUNER_LIBRE else None


@dataclass(frozen=True)
class ReglagePause:
    """Pause minimale entre deux séances d'une personne, et sa source."""

    duree: int
    source: Source


@dataclass(frozen=True)
class Reglages:
    """Réglages résolus d'un instantané : jours par séance, déjeuner et pause par personne."""

    jours: dict[str, ReglesJours]
    dejeuner: dict[Personne, ReglageDejeuner]
    pause: dict[Personne, ReglagePause]

    def jours_de(self, seance_id: str) -> ReglesJours:
        """Jours permis d'une séance."""
        return self.jours[seance_id]


def valeurs_resolues(
    type_regle: TypeRegle,
    canoniques: Mapping[str, Any],
    parametres: Parametres,
    identifiants: Mapping[str, Mapping[str, int]] | None = None,
) -> dict[str, Any]:
    """Paramètres canoniques convertis pour le calcul (numéros, minutes, identifiants).

    `identifiants` : `{"campus": {code: id}, "salle": {code: id},
    "type_seance": {code: id}}`. Un code sans identifiant lève
    `ErreurDonneesInvalides`. Les champs absents prennent le défaut global.
    """
    definition = CATALOGUE_TYPES[type_regle]
    resolues: dict[str, Any] = {}
    for champ in definition.champs:
        if not champ.concerne(canoniques):
            continue
        valeur = canoniques.get(champ.nom)
        if valeur is None:
            if champ.defaut_global is not None:
                valeur = _defaut_global_canonique(champ, parametres)
            elif champ.defaut is not None:
                valeur = champ.defaut
            else:
                raise ErreurDonneesInvalides(f"Paramètre « {champ.nom} » manquant.")
        if champ.sorte is SorteChamp.JOURS:
            resolues[champ.nom] = frozenset(jour_depuis_texte(j) for j in valeur)
        elif champ.sorte is SorteChamp.HEURE:
            resolues[champ.nom] = heure_depuis_texte(valeur)
        elif champ.sorte in (SorteChamp.CODES, SorteChamp.CODE):
            codes = valeur if champ.sorte is SorteChamp.CODES else [valeur]
            table = (identifiants or {}).get(champ.entite or "", {})
            ids = []
            for code in codes:
                if code not in table:
                    raise ErreurDonneesInvalides(
                        f"{champ.libelle} : {champ.entite} « {code} » inconnu."
                    )
                ids.append(table[code])
            resolues[champ.nom] = tuple(ids) if champ.sorte is SorteChamp.CODES else ids[0]
        elif champ.sorte is SorteChamp.MOMENTS:
            resolues[champ.nom] = tuple(moment_depuis_texte(m) for m in valeur)
        else:
            resolues[champ.nom] = valeur
    return resolues


def _feuilles_et_lignees(
    instantane: Instantane,
) -> tuple[dict[int, tuple[int, ...]], dict[int, set[int]]]:
    """Lignée de chaque groupe (lui puis ses ascendants) et descendants de chaque groupe."""
    parents = {groupe.id: groupe.parent_id for groupe in instantane.groupes}
    lignees: dict[int, tuple[int, ...]] = {}
    for groupe_id in parents:
        lignee, courant = [groupe_id], parents[groupe_id]
        while courant is not None and courant in parents and courant not in lignee:
            lignee.append(courant)
            courant = parents[courant]
        lignees[groupe_id] = tuple(lignee)
    descendants: dict[int, set[int]] = {groupe_id: set() for groupe_id in parents}
    for groupe_id, lignee in lignees.items():
        for ascendant in lignee[1:]:
            descendants[ascendant].add(groupe_id)
    return lignees, descendants


def personnes_concernees(
    regle: Regle | RegleResolue, instantane: Instantane
) -> frozenset[Personne]:
    """Personnes visées par une règle de sorte emploi du temps ou relation (§ 1.2)."""
    nature = regle.portee if isinstance(regle, RegleResolue) else regle.portee.nature
    cible = regle.cible_id if isinstance(regle, RegleResolue) else regle.portee.cible_id
    valeurs = regle.valeurs if isinstance(regle, RegleResolue) else regle.parametres
    emplois = instantane.emplois
    if nature is NaturePortee.ETABLISSEMENT:
        choix = valeurs.get("personnes", PERSONNES_GROUPES)
        natures = {
            PERSONNES_GROUPES: {NaturePersonne.GROUPE},
            PERSONNES_ENSEIGNANTS: {NaturePersonne.ENSEIGNANT},
            PERSONNES_TOUS: {NaturePersonne.GROUPE, NaturePersonne.ENSEIGNANT},
        }[choix]
        if CATALOGUE_TYPES[regle.type].sorte is SorteRegle.RELATION:
            natures = {NaturePersonne.GROUPE}
        return frozenset(e.personne for e in emplois if e.personne.nature in natures)
    if nature is NaturePortee.GROUPE:
        return frozenset(
            e.personne
            for e in emplois
            if e.personne.nature is NaturePersonne.GROUPE and cible in e.lignee
        )
    if nature is NaturePortee.ENSEIGNANT:
        return frozenset(
            e.personne
            for e in emplois
            if e.personne.nature is NaturePersonne.ENSEIGNANT and e.personne.id == cible
        )
    if nature is NaturePortee.MATIERE:
        return frozenset(e.personne for e in emplois if e.personne.nature is NaturePersonne.GROUPE)
    return frozenset()


def seances_concernees(regle: Regle | RegleResolue, instantane: Instantane) -> frozenset[str]:
    """Séances visées par une règle (§ 1.2) ; pour une portée campus, toutes les séances."""
    nature = regle.portee if isinstance(regle, RegleResolue) else regle.portee.nature
    cible = regle.cible_id if isinstance(regle, RegleResolue) else regle.portee.cible_id
    seances = instantane.seances
    if nature in (NaturePortee.ETABLISSEMENT, NaturePortee.CAMPUS):
        return frozenset(s.id for s in seances)
    if nature is NaturePortee.GROUPE:
        if regle.type is not TypeRegle.JOURS_TRAVAILLES:
            # Exigence : les séances d'un groupe lié, comme DISPO_GROUPE.
            lies = instantane.groupes_lies(cible)
            return frozenset(s.id for s in seances if not lies.isdisjoint(s.besoin.groupes))
        # Jours travaillés : les séances dont un groupe feuille a la cible dans sa lignée,
        # c'est-à-dire les séances des groupes du sous-arbre et de leurs ascendants.
        lignees, descendants = _feuilles_et_lignees(instantane)
        vises = {cible, *descendants.get(cible, ())} | set(lignees.get(cible, ()))
        return frozenset(s.id for s in seances if not vises.isdisjoint(s.besoin.groupes))
    if nature is NaturePortee.ENSEIGNANT:
        return frozenset(s.id for s in seances if cible in s.besoin.enseignants)
    if nature is NaturePortee.TYPE_SEANCE:
        return frozenset(s.id for s in seances if s.besoin.type_seance_id == cible)
    if nature is NaturePortee.MATIERE:
        return frozenset(s.id for s in seances if s.besoin.matiere_id == cible)
    return frozenset(s.id for s in seances if s.besoin.id == cible)


def resoudre_regle(
    regle: Regle,
    instantane: Instantane,
    identifiants: Mapping[str, Mapping[str, int]] | None = None,
) -> RegleResolue:
    """Résout une règle active sur un instantané (valeurs, semaines, cibles, phrase).

    La règle doit porter `id` et `code`. Une période datée qui ne recoupe
    aucune semaine donne `semaines` vide : la règle est alors sans effet.
    """
    if regle.id is None or regle.code is None:
        raise ErreurDonneesInvalides("Une règle à résoudre doit être enregistrée (id et code).")
    valeurs = valeurs_resolues(regle.type, regle.parametres, instantane.parametres, identifiants)
    semaines = (
        regle.semaines_couvertes(instantane.semaines)
        if regle.date_debut or regle.date_fin
        else frozenset(semaine.numero for semaine in instantane.semaines)
    )
    sorte = regle.definition.sorte
    resolue = RegleResolue(
        id=regle.id,
        code=regle.code,
        type=regle.type,
        niveau=regle.niveau,
        portee=regle.portee.nature,
        cible_id=regle.portee.cible_id,
        phrase=phrase(regle.type, regle.portee, regle.parametres, instantane.parametres),
        valeurs=valeurs,
        semaines=semaines,
        date_debut=regle.date_debut,
        date_fin=regle.date_fin,
    )
    seances: frozenset[str] = frozenset()
    personnes: frozenset[Personne] = frozenset()
    if sorte is SorteRegle.SEANCE:
        seances = seances_concernees(regle, instantane)
    elif sorte is SorteRegle.EMPLOI_DU_TEMPS:
        personnes = personnes_concernees(regle, instantane)
    else:  # relation : les séances des emplois du temps des personnes visées
        personnes = personnes_concernees(regle, instantane)
        emplois = {e.personne: e.seances for e in instantane.emplois}
        seances = frozenset(s for p in personnes for s in emplois.get(p, ()))
        if regle.portee.nature is NaturePortee.MATIERE:
            seances &= seances_concernees(regle, instantane)
    return RegleResolue(**{**resolue.__dict__, "seances": seances, "personnes": personnes})


def resoudre_reglages(instantane: Instantane) -> Reglages:
    """Réglages d'un instantané : jours permis par séance, déjeuner et pause par personne.

    Applique la précédence décrite en tête de module. Lève
    `ErreurDonneesInvalides` si deux règles de réglage retenues au même rang
    d'un même axe se contredisent (même cible, périodes qui se recoupent).
    """
    parametres = instantane.parametres
    regles = [r for r in instantane.regles if r.famille is FamilleRegle.REGLAGE]
    par_type: dict[TypeRegle, dict[tuple[NaturePortee, int | None], list[RegleResolue]]] = {}
    for regle in regles:
        par_type.setdefault(regle.type, {}).setdefault((regle.portee, regle.cible_id), []).append(
            regle
        )
    lignees, _ = _feuilles_et_lignees(instantane)

    def retenue(
        type_regle: TypeRegle,
        nature: NaturePortee,
        cible: int | None,
        semaines: frozenset[int],
    ) -> RegleResolue | None:
        candidates = [
            r
            for r in par_type.get(type_regle, {}).get((nature, cible), ())
            if not r.semaines.isdisjoint(semaines)
        ]
        if len(candidates) > 1:
            codes = ", ".join(sorted(r.code for r in candidates))
            raise ErreurDonneesInvalides(
                f"Les règles {codes} ({type_regle.libelle}) règlent la même chose pour la même "
                "cible sur des périodes qui se recoupent : en garder une seule."
            )
        return candidates[0] if candidates else None

    def premiere(
        type_regle: TypeRegle, axes: Iterable[tuple[NaturePortee, int | None]], semaines
    ) -> RegleResolue | None:
        for nature, cible in axes:
            regle = retenue(type_regle, nature, cible, semaines)
            if regle is not None:
                return regle
        return None

    types_vises = {
        r.cible_id
        for r in par_type.get(TypeRegle.JOURS_TRAVAILLES, {}).values()
        for r in r
        if r.portee is NaturePortee.TYPE_SEANCE
    }
    jours: dict[str, ReglesJours] = {}
    for seance in instantane.seances:
        besoin = seance.besoin
        standard = set(parametres.jours_travailles)
        if besoin.samedi_autorise and besoin.type_seance_id not in types_vises:
            standard.add(SAMEDI)
        retenues: dict[int, RegleResolue] = {}
        for groupe in besoin.groupes:
            for feuille in _feuilles_de(groupe, lignees):
                regle = premiere(
                    TypeRegle.JOURS_TRAVAILLES,
                    ((NaturePortee.GROUPE, g) for g in lignees.get(feuille, (feuille,))),
                    seance.semaines,
                )
                if regle is not None:
                    retenues[regle.id] = regle
        for enseignant in besoin.enseignants:
            if instantane.enseignant(enseignant).fictif:
                continue
            regle = retenue(
                TypeRegle.JOURS_TRAVAILLES, NaturePortee.ENSEIGNANT, enseignant, seance.semaines
            )
            if regle is not None:
                retenues[regle.id] = regle
        regle = premiere(
            TypeRegle.JOURS_TRAVAILLES,
            (
                (NaturePortee.BESOIN, besoin.id),
                (NaturePortee.MATIERE, besoin.matiere_id),
                (NaturePortee.TYPE_SEANCE, besoin.type_seance_id),
            ),
            seance.semaines,
        )
        if regle is not None:
            retenues[regle.id] = regle
        par_campus = {}
        for campus in instantane.campus:
            regle = retenue(
                TypeRegle.JOURS_TRAVAILLES, NaturePortee.CAMPUS, campus.id, seance.semaines
            )
            if regle is not None:
                par_campus[campus.id] = regle
        jours[seance.id] = ReglesJours(
            seance_id=seance.id,
            standard=frozenset(standard),
            retenues=tuple(retenues[k] for k in sorted(retenues)),
            par_campus=par_campus,
        )

    toutes = frozenset(semaine.numero for semaine in instantane.semaines)
    dejeuner: dict[Personne, ReglageDejeuner] = {}
    pause: dict[Personne, ReglagePause] = {}
    for emploi in instantane.emplois:
        personne = emploi.personne
        if personne.nature is NaturePersonne.GROUPE:
            axes = [(NaturePortee.GROUPE, g) for g in emploi.lignee]
        else:
            axes = [(NaturePortee.ENSEIGNANT, personne.id)]
        regle = premiere(TypeRegle.PAUSE_DEJEUNER, axes, toutes)
        if regle is None:
            dejeuner[personne] = ReglageDejeuner(
                mode=parametres.dejeuner_mode,
                debut=parametres.dejeuner_debut_min,
                fin=parametres.dejeuner_fin_min,
                duree=parametres.dejeuner_duree_min,
                source=Source(contrainte=CodeContrainte.DEJEUNER),
            )
        else:
            v = regle.valeurs
            dejeuner[personne] = ReglageDejeuner(
                mode=v["mode"],
                debut=v.get("debut", parametres.dejeuner_debut_min),
                fin=v.get("fin", parametres.dejeuner_fin_min),
                duree=v.get("duree", parametres.dejeuner_duree_min),
                source=Source(regle=regle),
            )
        regle = premiere(TypeRegle.PAUSE_MINIMALE, axes, toutes)
        if regle is None:
            pause[personne] = ReglagePause(
                parametres.pause_min, Source(contrainte=CodeContrainte.PAUSE)
            )
        else:
            pause[personne] = ReglagePause(regle.valeurs["duree"], Source(regle=regle))
    return Reglages(jours=jours, dejeuner=dejeuner, pause=pause)


def _feuilles_de(groupe: int, lignees: Mapping[int, tuple[int, ...]]) -> tuple[int, ...]:
    """Groupes feuilles du sous-arbre de `groupe` (lui-même s'il est une feuille)."""
    descendants = [g for g, lignee in lignees.items() if groupe in lignee[1:]]
    avec_enfants = {lignee[1] for lignee in lignees.values() if len(lignee) > 1}
    feuilles = tuple(sorted(g for g in descendants if g not in avec_enfants))
    return feuilles or (groupe,)


# --------------------------------------------------------------------------- #
# Registre des implémentations
# --------------------------------------------------------------------------- #

REGISTRES: dict[str, dict[TypeRegle, Callable[..., Any]]] = {"solveur": {}, "verificateur": {}}
"""Implémentations déclarées par type : un poseur (solveur) et un vérificateur par type."""


def enregistrer(
    registre: str, type_regle: TypeRegle
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Décorateur : déclare l'implémentation de `type_regle` dans `registre`.

        @enregistrer("verificateur", TypeRegle.SANS_TROU)
        def verifier_sans_trou(...): ...

    Un registre inconnu ou un type déjà déclaré lèvent une erreur.
    """
    if registre not in REGISTRES:
        raise ErreurDonneesInvalides(f"Registre inconnu : {registre!r} ({', '.join(REGISTRES)}).")

    def decorer(fonction: Callable[..., Any]) -> Callable[..., Any]:
        if type_regle in REGISTRES[registre]:
            raise ErreurDonneesInvalides(
                f"Le type {type_regle} a déjà une implémentation dans le registre « {registre} »."
            )
        REGISTRES[registre][type_regle] = fonction
        return fonction

    return decorer


def types_non_couverts(registre: str) -> frozenset[TypeRegle]:
    """Types du catalogue sans implémentation déclarée dans `registre`."""
    if registre not in REGISTRES:
        raise ErreurDonneesInvalides(f"Registre inconnu : {registre!r} ({', '.join(REGISTRES)}).")
    return frozenset(CATALOGUE_TYPES) - REGISTRES[registre].keys()


def decrire_catalogue() -> dict[str, Any]:
    """Description sérialisable du catalogue (pour l'assistant et l'interface)."""
    return {
        "types": [
            {
                "code": d.code.value,
                "libelle": d.libelle,
                "description": d.description,
                "sorte": d.sorte.value,
                "famille": d.famille.value,
                "niveau_defaut": d.niveau_defaut.value,
                "portees": sorted(p.value for p in d.portees),
                "portees_refusees": {p.value: raison for p, raison in d.refus},
                "parametres": [
                    {
                        "nom": c.nom,
                        "sorte": c.sorte.value,
                        "libelle": c.libelle,
                        "description": c.description,
                        "unite": c.unite,
                        "defaut": c.defaut,
                        "defaut_global": c.defaut_global,
                        "obligatoire": c.obligatoire,
                        "minimum": c.minimum,
                        "maximum": c.maximum,
                        "choix": [
                            {"valeur": valeur, "libelle": libelle} for valeur, libelle in c.choix
                        ],
                        "entite": c.entite,
                        "condition": (
                            {"champ": c.condition[0], "valeurs": list(c.condition[1])}
                            if c.condition
                            else None
                        ),
                    }
                    for c in d.champs
                ],
                "exemples": list(d.exemples),
            }
            for d in CATALOGUE_TYPES.values()
        ],
        "niveaux": [{"valeur": n.value, "libelle": n.libelle} for n in Niveau],
        "portees": [{"valeur": p.value, "libelle": p.libelle} for p in NaturePortee],
        "etats": [{"valeur": e.value, "libelle": e.libelle} for e in EtatRegle],
        "jours": list(JOURS),
        "precedence_des_jours": (
            "Le standard est le paramètre « jours travaillés », plus le samedi si le type de "
            "séance l'autorise et qu'aucune règle ne vise ce type. Chaque axe d'une séance "
            "(demi-groupe → classe → promotion ; enseignant ; besoin → matière → type de "
            "séance ; campus) retient sa règle la plus précise. Jours permis = intersection "
            "des règles retenues ; sans règle, le standard. Un axe sans règle est neutre."
        ),
    }
