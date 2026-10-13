"""Pause déjeuner « libre bornée » (`DEJEUNER`, mode libre).

Paramètres : `Parametres.fenetre_dejeuner = (X, Y, N)`. Règle (commune au
solveur) : pour chaque groupe et chaque enseignant non fictif, chaque jour
où il a au moins une séance qui recoupe la fenêtre `[X, Y]`, il existe un
intervalle libre d'au moins N minutes consécutives inclus dans `[X, Y]`. Un
jour sans séance dans la fenêtre est satisfait. (Le mode « fixe » est
vérifié séance par séance dans `regles_seance` ; le mode « aucun » ne
vérifie rien.)

Emploi du temps d'une personne
------------------------------
- un enseignant non fictif : les séances dont il est enseignant ;
- un groupe : les séances du groupe et des groupes qui lui sont **liés**
  (`Instantane.groupes_lies` : ascendants, groupes déclarés chevauchants,
  groupes partageant un étudiant), **sauf ses descendants**. Une séance d'un
  sous-groupe ne concerne qu'une partie des étudiants : la compter priverait
  à tort la promotion de pause quand ses demi-groupes déjeunent en décalé
  (TD de A1-G1 de 12 h à 13 h, TD de A1-G2 de 13 h à 14 h : chacun a sa pause).
  Chaque sous-groupe est vérifié pour lui-même, avec les séances de ses
  ascendants.

Semaines : la semaine type raisonne par ensemble de semaines actives (deux
séances aux semaines disjointes ne se gênent pas) ; la couche datée raisonne
par date.

Anomalies : une par (personne, jour), rattachée aux séances du jour qui
recoupent la fenêtre dans les semaines en défaut. Plusieurs groupes dans la
même situation (mêmes séances, mêmes semaines) donnent une seule anomalie ;
le message ne nomme pas un groupe dont un ascendant est déjà nommé, le champ
`groupes` les liste tous. Gravité : erreur, ou information si toutes ces
séances sont imposées (début imposé, ponctuelle, verrou resté à sa place) ;
avec des niveaux, elle suit ensuite le niveau de `DEJEUNER`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import Gravite
from edt.domaine.heures import se_chevauchent
from edt.verification.contexte import Contexte, Element, cle_chronologique
from edt.verification.contrat import Anomalie
from edt.verification.messages import duree, enumerer, heure, plage

_MENTION_IMPOSEES = " Ces séances sont imposées : l'écart est signalé pour information."


@dataclass(frozen=True)
class _Defaut:
    """Jour d'une personne sans pause suffisante : séances en cause, semaines, plus longue pause."""

    seances: tuple[Element, ...]
    semaines: frozenset[int] | None
    plus_longue: int


def verifier_dejeuner_libre(ctx: Contexte, elements: Sequence[Element]) -> Iterator[Anomalie]:
    """Anomalies `DEJEUNER` du mode libre (voir l'en-tête du module) ; rien dans les autres modes."""
    fenetre = ctx.parametres.fenetre_dejeuner
    if fenetre is None or not ctx.actif(CodeContrainte.DEJEUNER):
        return
    x, y, n = fenetre
    dans_fenetre = [e for e in elements if se_chevauchent(e.debut, e.fin, x, y)]
    if not dans_fenetre:
        return

    # Enseignants : une anomalie par (enseignant, jour).
    par_enseignant: dict[int, list[Element]] = defaultdict(list)
    for element in dans_fenetre:
        for enseignant in ctx.enseignants_reels(element.besoin):
            par_enseignant[enseignant].append(element)
    for enseignant in sorted(par_enseignant):
        for du_jour in _par_jour(par_enseignant[enseignant]):
            defaut = _defaut(du_jour, x, y, n)
            if defaut is not None:
                sujet = f"{ctx.nom_enseignant(enseignant)} n'a"
                yield _anomalie(ctx, defaut, sujet, fenetre, enseignants=(enseignant,))

    # Groupes : une anomalie par situation (jour, séances, semaines), qui nomme les plus généraux.
    par_groupe: dict[int, list[Element]] = defaultdict(list)
    for element in dans_fenetre:
        for groupe in element.besoin.groupes:
            par_groupe[groupe].append(element)
    ascendants, descendants = _arbre(ctx)
    situations: dict[tuple, tuple[_Defaut, list[int]]] = {}
    for groupe in sorted(g.id for g in ctx.instantane.groupes):
        concernes = ctx.instantane.groupes_lies(groupe) - descendants[groupe]
        seances = {e.position: e for g in concernes for e in par_groupe.get(g, ())}
        for du_jour in _par_jour(list(seances.values())):
            defaut = _defaut(du_jour, x, y, n)
            if defaut is None:
                continue
            cle = (tuple(e.position for e in defaut.seances), defaut.semaines)
            situations.setdefault(cle, (defaut, []))[1].append(groupe)
    for defaut, groupes in situations.values():
        nommes = [g for g in groupes if ascendants[g].isdisjoint(groupes)]
        codes = sorted(ctx.code_groupe(g) for g in nommes)
        if len(codes) == 1:
            sujet = f"Le groupe {codes[0]} n'a"
        else:
            sujet = f"Les groupes {enumerer(codes)} n'ont"
        yield _anomalie(ctx, defaut, sujet, fenetre, groupes=groupes)


def _par_jour(elements: list[Element]) -> list[list[Element]]:
    """Séances regroupées par jour (semaine type) ou par date (couche datée)."""
    jours: dict[object, list[Element]] = defaultdict(list)
    for element in elements:
        jours[element.cle_jour].append(element)
    return [jours[cle] for cle in sorted(jours, key=str)]


def _defaut(seances: list[Element], x: int, y: int, n: int) -> _Defaut | None:
    """Défaut de pause d'une personne un jour donné, ou `None` si chaque semaine a sa pause.

    Semaine type : les semaines sont regroupées par ensemble de séances
    actives ; le défaut réunit les séances et les semaines des ensembles sans
    pause. Couche datée (`semaines` à `None`) : un seul ensemble.
    """
    if all(e.semaines is None for e in seances):
        configurations: list[tuple[list[Element], frozenset[int] | None]] = [(seances, None)]
    else:
        par_ensemble: dict[tuple[int, ...], set[int]] = defaultdict(set)
        for semaine in set().union(*(e.semaines or () for e in seances)):
            actifs = tuple(i for i, e in enumerate(seances) if semaine in (e.semaines or ()))
            par_ensemble[actifs].add(semaine)
        configurations = [
            ([seances[i] for i in actifs], frozenset(semaines))
            for actifs, semaines in par_ensemble.items()
        ]
    en_cause: dict[int, Element] = {}
    semaines_en_cause: set[int] = set()
    plus_longue: int | None = None
    for actives, semaines in configurations:
        libre = plus_longue_pause(x, y, [(e.debut, e.fin) for e in actives])
        if libre >= n:
            continue
        en_cause.update((e.position, e) for e in actives)
        semaines_en_cause |= semaines or set()
        plus_longue = libre if plus_longue is None else min(plus_longue, libre)
    if plus_longue is None:
        return None
    return _Defaut(
        seances=tuple(sorted(en_cause.values(), key=cle_chronologique)),
        semaines=frozenset(semaines_en_cause) if semaines_en_cause else None,
        plus_longue=plus_longue,
    )


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


def _arbre(ctx: Contexte) -> tuple[dict[int, frozenset[int]], dict[int, frozenset[int]]]:
    """Ascendants et descendants stricts de chaque groupe, d'après `Groupe.parent_id`."""
    parents = {g.id: g.parent_id for g in ctx.instantane.groupes}
    ascendants: dict[int, frozenset[int]] = {}
    for groupe in parents:
        lignee: list[int] = []
        courant = parents.get(groupe)
        while courant is not None and courant not in lignee and courant != groupe:
            lignee.append(courant)
            courant = parents.get(courant)
        ascendants[groupe] = frozenset(lignee)
    descendants: dict[int, set[int]] = {groupe: set() for groupe in parents}
    for groupe, lignee in ascendants.items():
        for ascendant in lignee:
            descendants.setdefault(ascendant, set()).add(groupe)
    return ascendants, {g: frozenset(d) for g, d in descendants.items()}


def _anomalie(
    ctx: Contexte,
    defaut: _Defaut,
    sujet: str,
    fenetre: tuple[int, int, int],
    *,
    groupes: Sequence[int] = (),
    enseignants: Sequence[int] = (),
) -> Anomalie:
    x, y, n = fenetre
    premiere = defaut.seances[0]
    imposees = all(e.imposee for e in defaut.seances)
    seances = enumerer([f"{ctx.libelle(e)} {plage(e.debut, e.fin)}" for e in defaut.seances])
    libre = (
        "aucun moment libre"
        if defaut.plus_longue == 0
        else f"au plus {duree(defaut.plus_longue)} libres d'affilée"
    )
    message = (
        f"{sujet} pas de pause déjeuner d'au moins {duree(n)} entre {heure(x)} et {heure(y)} "
        f"{ctx.quand(premiere)}{ctx.precision_semaines(defaut.semaines)} ({libre}) : {seances}."
    )
    return ctx.anomalie(
        CodeContrainte.DEJEUNER,
        Gravite.INFORMATION if imposees else Gravite.ERREUR,
        message + (_MENTION_IMPOSEES if imposees else ""),
        defaut.seances,
        groupes=groupes,
        enseignants=enseignants,
        **ctx.reperes(premiere),
    )
