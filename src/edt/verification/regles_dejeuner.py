"""Pause déjeuner par personne : mode libre du standard, et briques de la règle `PAUSE_DEJEUNER`.

Le déjeuner de chaque **personne** (groupe feuille, enseignant non fictif :
`Instantane.emplois`) est résolu par `Instantane.reglages` : le standard de
l'établissement (`DEJEUNER`), ou une règle personnalisée `PAUSE_DEJEUNER` de
sa lignée. Ce module vérifie :

- le **standard en mode libre** (`DEJEUNER`), pour les personnes dont le
  déjeuner vient du standard : chaque jour où elle a au moins une séance qui
  recoupe la fenêtre `[X, Y]`, il existe un intervalle libre d'au moins N
  minutes consécutives inclus dans `[X, Y]` ; un jour sans séance dans la
  fenêtre est satisfait. (Le mode fixe du standard est vérifié séance par
  séance dans `regles_seance` ; le mode « aucun » ne vérifie rien.)
- et donne les briques (`defaut_libre`, `defaut_fixe`, `par_personne`,
  `situations`) au vérificateur de la règle `PAUSE_DEJEUNER`
  (`regles_personnalisees`), qui raisonne de même avec les valeurs de la règle.

Emploi du temps d'une personne : celui de `Instantane.emplois` (un groupe
feuille suit ses séances et celles de ses ascendants ; un enseignant les
siennes). Les séances d'un sous-groupe ne concernent qu'une partie des
étudiants et ne comptent pas pour la promotion ; un groupe transverse a son
propre emploi du temps.

Semaines : la semaine type raisonne par ensemble de semaines actives (deux
séances aux semaines disjointes ne se gênent pas) ; la couche datée raisonne
par date.

Anomalies : une par (personne, jour), rattachée aux séances du jour qui
recoupent la fenêtre dans les semaines en défaut. Plusieurs groupes dans la
même situation (mêmes séances, mêmes semaines) donnent une seule anomalie ;
le message nomme un ascendant dont toutes les feuilles sont en défaut plutôt
que ses feuilles (le champ `groupes` les liste toutes). Gravité : erreur, ou
information si toutes ces séances sont imposées ; avec des niveaux, elle
suit ensuite le niveau de `DEJEUNER`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass

from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import Gravite
from edt.domaine.heures import se_chevauchent
from edt.domaine.regles import NaturePersonne, Personne, RegleResolue
from edt.verification.contexte import Contexte, Element, cle_chronologique
from edt.verification.contrat import Anomalie
from edt.verification.messages import duree, enumerer, heure, plage

MENTION_IMPOSEES = " Ces séances sont imposées : l'écart est signalé pour information."


@dataclass(frozen=True)
class Defaut:
    """Jour d'une personne sans pause conforme : séances en cause, semaines, plus longue pause.

    `semaines` vaut `None` dans la couche datée ; `plus_longue` est la plus
    longue plage libre trouvée (mode libre), `None` en mode fixe.
    """

    seances: tuple[Element, ...]
    semaines: frozenset[int] | None
    plus_longue: int | None


def verifier_dejeuner_libre(ctx: Contexte, elements: Sequence[Element]) -> Iterator[Anomalie]:
    """Anomalies `DEJEUNER` du standard en mode libre (voir l'en-tête du module)."""
    fenetre = ctx.parametres.fenetre_dejeuner
    if fenetre is None or not ctx.actif(CodeContrainte.DEJEUNER):
        return
    x, y, n = fenetre
    personnes = [
        emploi.personne
        for emploi in ctx.instantane.emplois
        if (reglage := ctx.dejeuner_de(emploi.personne)) is not None
        and reglage.source.est_standard
        and reglage.fenetre is not None
    ]
    defauts = [
        (personne, defaut)
        for personne, seances in par_personne(ctx, elements, personnes, x, y).items()
        for defaut in jours_en_defaut(seances, lambda du_jour: defaut_libre(du_jour, x, y, n))
    ]
    for defaut, groupes, enseignants in situations(defauts):
        for enseignant in enseignants:
            sujet = f"{ctx.nom_enseignant(enseignant)} n'a"
            yield _anomalie(ctx, defaut, sujet, (x, y, n), enseignants=(enseignant,))
        if groupes:
            nommes, en_cause = ctx.nommer_groupes(groupes)
            codes = sorted(ctx.code_groupe(g) for g in nommes)
            if len(codes) == 1:
                sujet = f"Le groupe {codes[0]} n'a"
            else:
                sujet = f"Les groupes {enumerer(codes)} n'ont"
            yield _anomalie(ctx, defaut, sujet, (x, y, n), groupes=en_cause)


# --------------------------------------------------------------------------- #
# Briques communes au standard et à la règle PAUSE_DEJEUNER
# --------------------------------------------------------------------------- #


def par_personne(
    ctx: Contexte,
    elements: Sequence[Element],
    personnes: Iterable[Personne],
    x: int,
    y: int,
    regle: RegleResolue | None = None,
) -> dict[Personne, list[Element]]:
    """Séances de chaque personne qui recoupent `[x, y]` (et, si donnée, la période de `regle`).

    `regle` : sur la semaine type, les semaines actives des séances sont
    restreintes à celles de la règle ; sur la couche datée, une séance hors de
    sa période est écartée (`Contexte.restreindre`).
    """
    voulues = set(personnes)
    resultat: dict[Personne, list[Element]] = defaultdict(list)
    for element in elements:
        if not se_chevauchent(element.debut, element.fin, x, y):
            continue
        if regle is not None:
            element = ctx.restreindre(element, regle)
            if element is None:
                continue
        for personne in ctx.personnes_de(element):
            if personne in voulues:
                resultat[personne].append(element)
    return resultat


def jours_en_defaut(
    seances: list[Element], examiner: Callable[[list[Element]], Defaut | None]
) -> list[Defaut]:
    """`examiner` appliqué à chaque jour (jour de la semaine type ou date) ; défauts trouvés."""
    jours: dict[object, list[Element]] = defaultdict(list)
    for element in seances:
        jours[element.cle_jour].append(element)
    defauts = [examiner(jours[cle]) for cle in sorted(jours, key=str)]
    return [defaut for defaut in defauts if defaut is not None]


def _configurations(
    seances: list[Element],
) -> list[tuple[list[Element], frozenset[int] | None]]:
    """Ensembles de séances actives ensemble, avec leurs semaines (`None` : couche datée)."""
    if all(e.semaines is None for e in seances):
        return [(seances, None)]
    par_ensemble: dict[tuple[int, ...], set[int]] = defaultdict(set)
    for semaine in set().union(*(e.semaines or () for e in seances)):
        actifs = tuple(i for i, e in enumerate(seances) if semaine in (e.semaines or ()))
        par_ensemble[actifs].add(semaine)
    return [
        ([seances[i] for i in actifs], frozenset(semaines))
        for actifs, semaines in par_ensemble.items()
    ]


def defaut_libre(seances: list[Element], x: int, y: int, n: int) -> Defaut | None:
    """Défaut de pause libre d'un jour, ou `None` si chaque semaine a sa pause de `n` minutes.

    Semaine type : les semaines sont regroupées par ensemble de séances
    actives ; le défaut réunit les séances et les semaines des ensembles sans
    pause. Couche datée : un seul ensemble.
    """
    en_cause: dict[int, Element] = {}
    semaines_en_cause: set[int] = set()
    plus_longue: int | None = None
    for actives, semaines in _configurations(seances):
        libre = plus_longue_pause(x, y, [(e.debut, e.fin) for e in actives])
        if libre >= n:
            continue
        en_cause.update((e.position, e) for e in actives)
        semaines_en_cause |= semaines or set()
        plus_longue = libre if plus_longue is None else min(plus_longue, libre)
    if plus_longue is None:
        return None
    return Defaut(
        seances=tuple(sorted(en_cause.values(), key=cle_chronologique)),
        semaines=frozenset(semaines_en_cause) if semaines_en_cause else None,
        plus_longue=plus_longue,
    )


def defaut_fixe(seances: list[Element], x: int, y: int) -> Defaut | None:
    """Défaut d'une plage fixe : les séances du jour qui recoupent `[x, y[`, ou `None`."""
    en_cause = [e for e in seances if se_chevauchent(e.debut, e.fin, x, y)]
    if not en_cause:
        return None
    semaines = None
    if any(e.semaines is not None for e in en_cause):
        semaines = frozenset().union(*(e.semaines or () for e in en_cause))
    return Defaut(tuple(sorted(en_cause, key=cle_chronologique)), semaines, None)


def situations(
    defauts: Iterable[tuple[Personne, Defaut]],
) -> list[tuple[Defaut, list[int], list[int]]]:
    """Défauts réunis par situation (mêmes séances, mêmes semaines) : (défaut, groupes, enseignants)."""
    reunis: dict[tuple, tuple[Defaut, list[int], list[int]]] = {}
    for personne, defaut in defauts:
        cle = (tuple(e.position for e in defaut.seances), defaut.semaines)
        entree = reunis.setdefault(cle, (defaut, [], []))
        if personne.nature is NaturePersonne.GROUPE:
            entree[1].append(personne.id)
        else:
            entree[2].append(personne.id)
    return list(reunis.values())


def plus_longue_pause(x: int, y: int, occupes: Sequence[tuple[int, int]]) -> int:
    """Plus long intervalle libre (minutes consécutives) inclus dans `[x, y]`, hors de `occupes`."""
    meilleur, curseur = 0, x
    for debut, fin in sorted(occupes):
        debut, fin = max(debut, x), min(fin, y)
        if fin <= debut:
            continue
        if debut > curseur:
            meilleur = max(meilleur, debut - curseur)
        curseur = max(curseur, fin)
    return max(meilleur, y - curseur)


def liste_seances(ctx: Contexte, defaut: Defaut) -> str:
    """« Cours 1 (A1-G1) de 11 h 00 à 13 h 00 et Cours 2 (A1-G1) de 13 h 30 à 15 h 30 »."""
    return enumerer([f"{ctx.libelle(e)} {plage(e.debut, e.fin)}" for e in defaut.seances])


def _anomalie(
    ctx: Contexte,
    defaut: Defaut,
    sujet: str,
    fenetre: tuple[int, int, int],
    *,
    groupes: Sequence[int] = (),
    enseignants: Sequence[int] = (),
) -> Anomalie:
    x, y, n = fenetre
    premiere = defaut.seances[0]
    imposees = all(e.imposee for e in defaut.seances)
    libre = (
        "aucun moment libre"
        if not defaut.plus_longue
        else f"au plus {duree(defaut.plus_longue)} libres d'affilée"
    )
    message = (
        f"{sujet} pas de pause déjeuner d'au moins {duree(n)} entre {heure(x)} et {heure(y)} "
        f"{ctx.quand(premiere)}{ctx.precision_semaines(defaut.semaines)} ({libre}) : "
        f"{liste_seances(ctx, defaut)}."
    )
    return ctx.anomalie(
        CodeContrainte.DEJEUNER,
        Gravite.INFORMATION if imposees else Gravite.ERREUR,
        message + (MENTION_IMPOSEES if imposees else ""),
        defaut.seances,
        groupes=groupes,
        enseignants=enseignants,
        **ctx.reperes(premiere),
    )
