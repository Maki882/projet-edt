"""Règles personnalisées dans le solveur : niveaux du calcul et poseurs par type.

Le catalogue, la validation, la résolution et la précédence des jours sont
dans `edt.domaine.regles` ; l'instantané porte les règles actives résolues
(`Instantane.regles`), les emplois du temps (`Instantane.emplois`) et les
réglages par séance et par personne (`Instantane.reglages`). Ce module donne
au solveur :

- `appliquer_niveaux(instantane, niveaux_regles)` : l'instantané **du
  calcul**, avec les seules règles retenues, chacune au niveau choisi pour ce
  calcul (`OptionsSolveur.niveaux_regles`). Une règle absente du dictionnaire
  est ignorée ; `None` garde chaque règle active à son propre niveau. Les
  réglages (`Instantane.reglages`) se recalculent sur cet instantané : une
  règle de réglage ignorée rend la personne au standard ;
- le registre des **poseurs** (`edt.domaine.regles.enregistrer("solveur",
  type)`), un par type pris en charge ; `types_non_couverts("solveur")` le
  compare au catalogue, et `types_sans_poseur` dit quelles règles actives le
  moteur ne sait pas poser (le calcul est alors refusé en les nommant).

Types de sorte « séance » (`PAR_CHOIX`) : le poseur est une fonction
`(regle, choix) -> bool`, vraie si le **choix** (jour, heure, campus, salle)
enfreint la règle. La préparation (`edt.solveur.preparation`) l'applique à
chaque choix d'une séance concernée : en dur, le choix est retiré du domaine
(un domaine vidé est une impossibilité certaine qui nomme la règle) ; en
souple, le choix porte une entorse imputée à la règle (`Regles.Violation`).
Le modèle n'a aucune variable à poser pour ces types. `JOURS_TRAVAILLES`
obéit en plus à la précédence des axes : ce sont les règles **retenues** pour
la séance (`Reglages.jours_de(...).entorses`) qui sont examinées, pas toutes
celles qui la concernent ; `CAMPUS` se décide sur le campus du choix, `SALLES`
sur la salle, dans la phase des salles. Une règle d'horaires (`PLAGE_HORAIRE`,
`CRENEAUX_INTERDITS`) de **portée campus** ne joue que pour un choix sur ce
campus (`sur_le_campus`) : la préparation la juge par (début, campus), le
replacement au choix de la salle.

Types de réglage d'une personne (`PAUSE_DEJEUNER`, `PAUSE_MINIMALE`) : la
valeur de chaque personne est dans `Instantane.reglages`, avec sa source ; le
modèle (`_pause_dejeuner`, `_pauses_et_trajets`) et le replacement lisent ces
valeurs par `reglage_dejeuner` et `reglage_pause` et imputent chaque entorse
à sa source, la règle ou le paramètre global.

Les types d'emploi du temps et de relation (maximum par jour, journées
libres, trous, campus par jour, matières par jour, ordre dans la semaine,
répartition d'une matière) ont leur poseur dans `edt.solveur.regles_emploi`,
appelé par le modèle des horaires (`ModeleHoraires._regles_personnalisees`).
`types_non_couverts("solveur")` est vide.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from edt.domaine.contraintes import Niveau
from edt.domaine.heures import se_chevauchent
from edt.domaine.instantane import Instantane
from edt.domaine.regles import (
    MODE_IMPOSER,
    REGISTRES,
    NaturePortee,
    Personne,
    ReglageDejeuner,
    ReglagePause,
    Reglages,
    RegleResolue,
    SorteRegle,
    TypeRegle,
    enregistrer,
)

journal = logging.getLogger(__name__)


def appliquer_niveaux(
    instantane: Instantane, niveaux_regles: Mapping[int, Niveau] | None
) -> Instantane:
    """L'instantané du calcul : les règles retenues, chacune au niveau choisi pour ce calcul.

    `None` : toutes les règles actives, à leur niveau. Sinon, une règle
    absente du dictionnaire est ignorée pour ce calcul. L'instantané rendu
    est le même objet quand rien ne change (caches conservés).
    """
    if niveaux_regles is None:
        return instantane
    retenues = tuple(
        regle
        if regle.niveau is niveaux_regles[regle.id]
        else dataclasses.replace(regle, niveau=Niveau(niveaux_regles[regle.id]))
        for regle in instantane.regles
        if regle.id in niveaux_regles
    )
    if retenues == instantane.regles:
        return instantane
    journal.debug(
        "Règles personnalisées du calcul : %d retenue(s) sur %d (%s)",
        len(retenues),
        len(instantane.regles),
        ", ".join(f"{r.code} {r.niveau.value}" for r in retenues) or "aucune",
    )
    return dataclasses.replace(instantane, regles=retenues)


@dataclass(frozen=True)
class Choix:
    """Un choix examiné pour une séance : jour, plage `[minute, fin[`, campus et salle (s'ils sont connus)."""

    jour: int
    minute: int
    fin: int
    campus_id: int | None = None
    salle_id: int | None = None


Poseur = Callable[[RegleResolue, Choix], bool]
"""Poseur d'un type de sorte « séance » : vrai si le choix enfreint la règle."""


@enregistrer("solveur", TypeRegle.JOURS_TRAVAILLES)
def jours_travailles(regle: RegleResolue, choix: Choix) -> bool:
    """Le jour du choix n'est pas dans les jours de la règle (règle retenue pour la séance)."""
    return choix.jour not in regle.jours


def sur_le_campus(regle: RegleResolue, choix: Choix) -> bool:
    """Vrai si la règle joue pour ce choix : toujours, sauf une portée campus sur un autre campus.

    Une règle de portée campus ne vise que les séances placées sur ce campus
    (`RegleResolue.seances` les contient toutes) ; un choix dont le campus
    est inconnu (`None`) n'est pas jugé par elle.
    """
    return regle.portee is not NaturePortee.CAMPUS or choix.campus_id == regle.cible_id


@enregistrer("solveur", TypeRegle.PLAGE_HORAIRE)
def plage_horaire(regle: RegleResolue, choix: Choix) -> bool:
    """La séance déborde de `[debut, fin]` un jour visé (tous les jours si `jours` est vide)."""
    jours = regle.jours
    if not sur_le_campus(regle, choix) or (jours and choix.jour not in jours):
        return False
    return choix.minute < regle.valeurs["debut"] or choix.fin > regle.valeurs["fin"]


@enregistrer("solveur", TypeRegle.CRENEAUX_INTERDITS)
def creneaux_interdits(regle: RegleResolue, choix: Choix) -> bool:
    """La séance recoupe la plage interdite un jour visé."""
    if not sur_le_campus(regle, choix):
        return False
    return choix.jour in regle.jours and se_chevauchent(
        choix.minute, choix.fin, regle.valeurs["debut"], regle.valeurs["fin"]
    )


@enregistrer("solveur", TypeRegle.CAMPUS)
def campus(regle: RegleResolue, choix: Choix) -> bool:
    """Le campus du choix est refusé : hors des campus imposés, ou parmi les campus interdits."""
    if choix.campus_id is None:
        return False
    dedans = choix.campus_id in regle.valeurs["campus"]
    return not dedans if regle.valeurs["mode"] == MODE_IMPOSER else dedans


@enregistrer("solveur", TypeRegle.SALLES)
def salles(regle: RegleResolue, choix: Choix) -> bool:
    """La salle du choix est refusée : hors des salles imposées, ou parmi les salles interdites."""
    if choix.salle_id is None:
        return False
    dedans = choix.salle_id in regle.valeurs["salles"]
    return not dedans if regle.valeurs["mode"] == MODE_IMPOSER else dedans


@enregistrer("solveur", TypeRegle.PAUSE_DEJEUNER)
def reglage_dejeuner(reglages: Reglages, personne: Personne) -> ReglageDejeuner:
    """La pause déjeuner d'une personne, avec sa source (la règle retenue ou le standard)."""
    return reglages.dejeuner[personne]


@enregistrer("solveur", TypeRegle.PAUSE_MINIMALE)
def reglage_pause(reglages: Reglages, personne: Personne) -> ReglagePause:
    """La pause entre deux séances d'une personne, avec sa source."""
    return reglages.pause[personne]


PAR_CHOIX: dict[TypeRegle, Poseur] = {
    TypeRegle.JOURS_TRAVAILLES: jours_travailles,
    TypeRegle.PLAGE_HORAIRE: plage_horaire,
    TypeRegle.CRENEAUX_INTERDITS: creneaux_interdits,
    TypeRegle.CAMPUS: campus,
    TypeRegle.SALLES: salles,
}
"""Poseurs des types de sorte « séance », appliqués choix par choix par la préparation."""

POSEURS = REGISTRES["solveur"]
"""Le registre des poseurs du solveur (voir `edt.domaine.regles.enregistrer`)."""


def types_sans_poseur(instantane: Instantane) -> list[RegleResolue]:
    """Règles de l'instantané dont le type n'a pas de poseur : le calcul doit les refuser."""
    return [regle for regle in instantane.regles if regle.type not in POSEURS]


def enfreint(regle: RegleResolue, choix: Choix) -> bool:
    """Vrai si le choix enfreint cette règle de sorte « séance »."""
    return PAR_CHOIX[regle.type](regle, choix)


def regles_de_seance(instantane: Instantane) -> dict[str, tuple[RegleResolue, ...]]:
    """Pour chaque séance, les règles d'exigence de sorte « séance » qui la concernent.

    `JOURS_TRAVAILLES` n'en fait pas partie : c'est un réglage soumis à la
    précédence, lu dans `Instantane.reglages`.
    """
    par_seance: dict[str, list[RegleResolue]] = {}
    exigences = [
        regle
        for regle in instantane.regles
        if regle.sorte is SorteRegle.SEANCE and regle.type is not TypeRegle.JOURS_TRAVAILLES
    ]
    if not exigences:
        return {}
    for seance in instantane.seances:
        concernantes = [
            regle for regle in exigences if regle.concerne_seance(seance.id, seance.semaines)
        ]
        if concernantes:
            par_seance[seance.id] = concernantes
    return {seance_id: tuple(liste) for seance_id, liste in par_seance.items()}
