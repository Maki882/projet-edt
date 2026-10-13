"""Règles personnalisées sur l'emploi du temps des personnes et les relations entre séances (temps B).

Poseurs déclarés (`@enregistrer("solveur", TypeRegle.X)`, vue `PAR_MODELE`)
pour `MAX_PAR_JOUR`, `JOURNEES_LIBRES`, `SANS_TROU`, `CAMPUS_PAR_JOUR`,
`MATIERES_PAR_JOUR` (sorte « emploi du temps ») et `ORDRE_SEMAINE`,
`REPARTITION_MATIERE` (sorte « relation »), selon le § 2.2 de
`docs/regles-personnalisees.md`. Chaque poseur reçoit le modèle des horaires
(`ModeleHoraires`, étape `_regles_personnalisees`) et une règle résolue, et
pose ses contraintes sur les booléens de choix existants.

Personnes : celles de la règle (`RegleResolue.personnes`), avec leur emploi
du temps (`Instantane.emplois`) ; pour une relation, seules les séances de la
règle comptent (`RegleResolue.seances`). Les personnes au même emploi du
temps partagent les contraintes mais comptent chacune pour une entorse.

Semaines : chaque séance est ramenée aux semaines de la règle, puis les
semaines sont regroupées par ensemble de séances actives ensemble
(`_configurations`, la définition du vérificateur : deux séances aux
semaines disjointes ne se gênent pas).

Dur ou souple : au niveau de la règle. En dur, l'inégalité est posée telle
quelle. En souple, un littéral d'**entorse** `e` relâche l'inégalité
(`expression <= maximum + marge × e`, `_depasse`) et pèse `POIDS_NIVEAU` par
personne concernée ; `ModeleHoraires.bilan` le recalcule à partir des choix
(nature « depasse » de `_completer`).

Unité d'entorse, alignée sur l'anomalie du vérificateur
(`edt.verification.regles_emploi`) :

- `MAX_PAR_JOUR`, `CAMPUS_PAR_JOUR`, `MATIERES_PAR_JOUR` : une personne et un
  jour en défaut (quelles que soient les semaines) ;
- `JOURNEES_LIBRES`, `SANS_TROU` : une personne en défaut (une semaine au
  moins) ;
- `ORDRE_SEMAINE` : une paire dans le mauvais ordre (une « après » qui ne
  commence pas strictement après une « avant », même matière, semaines
  communes) ;
- `REPARTITION_MATIERE` : même jour, un couple (groupe, matière) sur plusieurs
  jours dans un ensemble de semaines ; jours différents, un jour avec plus
  d'une séance de la matière.

Définitions communes au vérificateur : le **matin** va jusqu'au début de la
plage du déjeuner (`dejeuner_debut_min`), l'après-midi commence là, et une
séance occupe chaque demi-journée qu'elle recoupe ; les **jours travaillés**
de `JOURNEES_LIBRES` sans `parmi` sont le standard de l'établissement, un
moment sans demi-journée vaut ses deux demi-journées ; un **créneau de
trou** est celui de l'indicateur `trous_creneaux` (`ModeleHoraires._trous`) ;
pour l'**ordre**, « après » commence strictement plus tard dans la semaine
(jour puis heure).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import TYPE_CHECKING

from edt.domaine.enumerations import DemiJournee
from edt.domaine.heures import MINUTES_PAR_JOUR, se_chevauchent
from edt.domaine.regles import (
    MEME_JOUR,
    UNITE_JOURNEE,
    UNITE_SEANCES,
    Personne,
    RegleResolue,
    TypeRegle,
    enregistrer,
)

if TYPE_CHECKING:
    from edt.solveur.modele import Litteral, ModeleHoraires

Poseur = Callable[["ModeleHoraires", RegleResolue], None]

PAR_MODELE: dict[TypeRegle, Poseur] = {}
"""Poseurs des types de sorte « emploi du temps » et « relation », par type."""


def _poseur(type_regle: TypeRegle) -> Callable[[Poseur], Poseur]:
    def decorer(fonction: Poseur) -> Poseur:
        enregistrer("solveur", type_regle)(fonction)
        PAR_MODELE[type_regle] = fonction
        return fonction

    return decorer


# --------------------------------------------------------------------------- #
# Outils communs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Situation:
    """Un emploi du temps (indices de séances, triés) et les personnes de la règle qui le suivent."""

    indices: tuple[int, ...]
    personnes: tuple[Personne, ...]


def _situations(modele: ModeleHoraires, regle: RegleResolue, relation: bool) -> list[_Situation]:
    """Emplois du temps distincts des personnes de la règle.

    Pour une relation, l'emploi du temps est restreint aux séances de la
    règle (`RegleResolue.seances`). Une personne sans séance est omise.
    """
    par_indices: dict[tuple[int, ...], list[Personne]] = defaultdict(list)
    for personne in sorted(regle.personnes, key=lambda p: (p.nature, p.id)):
        emploi = modele.instantane.emploi(personne)
        ids = emploi.seances & regle.seances if relation else emploi.seances
        indices = tuple(sorted(modele.index_de[s] for s in ids if s in modele.index_de))
        if indices:
            par_indices[indices].append(personne)
    return [_Situation(indices, tuple(p)) for indices, p in sorted(par_indices.items())]


def _masque(regle: RegleResolue) -> int:
    return sum(1 << numero for numero in regle.semaines)


def _configurations(
    modele: ModeleHoraires, indices: Sequence[int], masque: int
) -> list[tuple[list[int], int]]:
    """Séances actives ensemble, semaine par semaine de la règle : `(séances, nombre de semaines)`."""
    configurations: dict[tuple[int, ...], int] = defaultdict(int)
    for semaine in modele.instantane.semaines:
        if not masque >> semaine.numero & 1:
            continue
        actives = tuple(i for i in indices if modele.seances[i].semaines >> semaine.numero & 1)
        if actives:
            configurations[actives] += 1
    return [(list(actives), nombre) for actives, nombre in sorted(configurations.items())]


def _par_jour(
    modele: ModeleHoraires, indices: Sequence[int]
) -> dict[int, list[tuple[int, int, int, Litteral]]]:
    """Par jour, les choix des séances : `(indice, début en minutes, fin, littéral)`."""
    pas = modele.preparation.pas
    par_jour: dict[int, list[tuple[int, int, int, Litteral]]] = defaultdict(list)
    for index in indices:
        duree = modele.seances[index].duree
        for option, litteral in zip(modele.options[index], modele.litteraux[index], strict=True):
            jour, minute = divmod(option.debut, pas)
            par_jour[jour].append((index, minute, minute + duree, litteral))
    return par_jour


def _dure(modele: ModeleHoraires, regle: RegleResolue) -> bool:
    return modele.regles.dure_imputation(regle.id)


def _depasse(
    modele: ModeleHoraires,
    regle: RegleResolue,
    termes: Sequence[tuple[int, Litteral]],
    constante: int,
    maximum: int,
) -> Litteral | None:
    """Pose « constante + Σ coef × littéral <= maximum », en dur ou en souple.

    Dur : l'inégalité est posée (une contradiction certaine rend le modèle
    infaisable) et rien n'est rendu. Souple : rend le littéral d'entorse
    `e`, vrai dès que l'inégalité est dépassée (`False` si elle ne peut pas
    l'être, `True` si elle l'est à coup sûr).
    """
    variables = [(coef, lit) for coef, lit in termes if lit is not True and lit is not False]
    acquis = constante + sum(coef for coef, lit in termes if lit is True)
    pire = acquis + sum(max(coef, 0) for coef, _ in variables)
    if pire <= maximum:
        return None if _dure(modele, regle) else False
    somme = sum(coef * lit for coef, lit in variables)
    if _dure(modele, regle):
        if variables:
            modele.cp.add(somme + acquis <= maximum)
        else:
            modele.cp.add_bool_or([])  # dépassé par des séances à horaire fixe : impossible
        return None
    if not variables:
        return True
    entorse = modele.cp.new_bool_var("")
    modele.cp.add(somme + acquis <= maximum + (pire - maximum) * entorse)
    modele.definir(entorse, "depasse", (variables, acquis, maximum))
    return entorse


def _imputer(
    modele: ModeleHoraires, regle: RegleResolue, situation: _Situation, litteraux: list[Litteral]
) -> int:
    """Une entorse par personne de la situation, vraie dès que l'un des `litteraux` l'est."""
    litteraux = [lit for lit in litteraux if lit is not False]
    if not litteraux:
        return 0
    entorse = modele.au_moins_un(litteraux)
    modele.noter_entorse(regle.id, len(situation.personnes), entorse)
    return len(situation.personnes)


# --------------------------------------------------------------------------- #
# Par personne et par jour
# --------------------------------------------------------------------------- #


@_poseur(TypeRegle.MAX_PAR_JOUR)
def max_par_jour(modele: ModeleHoraires, regle: RegleResolue) -> None:
    """Chaque jour, une personne n'a pas plus de `maximum` minutes de cours (ou séances)."""
    maximum = regle.valeurs["maximum"]
    en_seances = regle.valeurs.get("unite") == UNITE_SEANCES
    masque = _masque(regle)
    for situation in _situations(modele, regle, relation=False):
        par_jour_entorses: dict[int, list[Litteral]] = defaultdict(list)
        for actives, _ in _configurations(modele, situation.indices, masque):
            for jour, choix in _par_jour(modele, actives).items():
                termes = [(1 if en_seances else fin - debut, lit) for _, debut, fin, lit in choix]
                entorse = _depasse(modele, regle, termes, 0, maximum)
                if entorse is not None:
                    par_jour_entorses[jour].append(entorse)
        for jour in sorted(par_jour_entorses):
            _imputer(modele, regle, situation, par_jour_entorses[jour])


@_poseur(TypeRegle.CAMPUS_PAR_JOUR)
def campus_par_jour(modele: ModeleHoraires, regle: RegleResolue) -> None:
    """Chaque jour, les séances d'une personne sont sur un seul campus."""
    masque = _masque(regle)
    pas = modele.preparation.pas
    for situation in _situations(modele, regle, relation=False):
        par_jour_entorses: dict[int, list[Litteral]] = defaultdict(list)
        for actives, _ in _configurations(modele, situation.indices, masque):
            presences: dict[int, dict[int, list[Litteral]]] = defaultdict(lambda: defaultdict(list))
            for index in actives:
                for option, lit in zip(modele.options[index], modele.litteraux[index], strict=True):
                    presences[option.debut // pas][option.campus].append(lit)
            for jour, par_campus in presences.items():
                if len(par_campus) < 2:
                    continue
                sur = [modele.au_moins_un(par_campus[campus]) for campus in sorted(par_campus)]
                if _dure(modele, regle):
                    for a, b in combinations(sur, 2):
                        # Pas les deux campus : clause vide si tous deux sont certains (impossible).
                        modele.cp.add_bool_or([lit.negated() for lit in (a, b) if lit is not True])
                    continue
                entorse = _depasse(modele, regle, [(1, lit) for lit in sur], 0, 1)
                if entorse is not None:
                    par_jour_entorses[jour].append(entorse)
        for jour in sorted(par_jour_entorses):
            _imputer(modele, regle, situation, par_jour_entorses[jour])


@_poseur(TypeRegle.MATIERES_PAR_JOUR)
def matieres_par_jour(modele: ModeleHoraires, regle: RegleResolue) -> None:
    """Chaque jour, un groupe n'a pas plus de `maximum` matières différentes."""
    maximum = regle.valeurs["maximum"]
    masque = _masque(regle)
    for situation in _situations(modele, regle, relation=False):
        par_jour_entorses: dict[int, list[Litteral]] = defaultdict(list)
        for actives, _ in _configurations(modele, situation.indices, masque):
            for jour, choix in _par_jour(modele, actives).items():
                par_matiere: dict[int, list[Litteral]] = defaultdict(list)
                for index, _, _, lit in choix:
                    par_matiere[modele.seances[index].besoin.matiere_id].append(lit)
                if len(par_matiere) <= maximum:
                    continue
                presentes = [modele.au_moins_un(lits) for _, lits in sorted(par_matiere.items())]
                entorse = _depasse(modele, regle, [(1, lit) for lit in presentes], 0, maximum)
                if entorse is not None:
                    par_jour_entorses[jour].append(entorse)
        for jour in sorted(par_jour_entorses):
            _imputer(modele, regle, situation, par_jour_entorses[jour])


# --------------------------------------------------------------------------- #
# Par personne et par semaine
# --------------------------------------------------------------------------- #


def _demi_journees(modele: ModeleHoraires) -> dict[DemiJournee, tuple[int, int]]:
    """Plages des deux demi-journées : le matin s'arrête au début de la plage du déjeuner."""
    bascule = modele.instantane.parametres.dejeuner_debut_min
    return {DemiJournee.MATIN: (0, bascule), DemiJournee.APRES_MIDI: (bascule, MINUTES_PAR_JOUR)}


def _candidates(
    modele: ModeleHoraires, regle: RegleResolue
) -> list[tuple[int, DemiJournee | None]]:
    """Demi-journées (ou journées) parmi lesquelles la personne doit en garder de libres."""
    journees = regle.valeurs.get("unite") == UNITE_JOURNEE
    parmi = regle.valeurs.get("parmi") or ()
    travailles = sorted(modele.instantane.jours_travailles)
    if journees:
        jours = sorted({jour for jour, _ in parmi}) or travailles
        return [(jour, None) for jour in jours]
    if parmi:
        return sorted(
            {
                (jour, demi)
                for jour, demi_choisie in parmi
                for demi in ((demi_choisie,) if demi_choisie else tuple(DemiJournee))
            },
            key=lambda c: (c[0], str(c[1])),
        )
    return [(jour, demi) for jour in travailles for demi in DemiJournee]


@_poseur(TypeRegle.JOURNEES_LIBRES)
def journees_libres(modele: ModeleHoraires, regle: RegleResolue) -> None:
    """Chaque semaine, une personne garde au moins `nombre` demi-journées (ou journées) libres."""
    nombre = regle.valeurs.get("nombre", 1)
    candidates = _candidates(modele, regle)
    demis = _demi_journees(modele)
    masque = _masque(regle)
    for situation in _situations(modele, regle, relation=False):
        entorses: list[Litteral] = []
        for actives, _ in _configurations(modele, situation.indices, masque):
            par_jour = _par_jour(modele, actives)
            occupees: list[Litteral] = []
            for jour, demi in candidates:
                plage = demis[demi] if demi is not None else (0, MINUTES_PAR_JOUR)
                dessus = [
                    lit
                    for _, debut, fin, lit in par_jour.get(jour, ())
                    if se_chevauchent(debut, fin, *plage)
                ]
                if dessus:
                    occupees.append(modele.au_moins_un(dessus))
            # Libres = candidates - occupées >= nombre  <=>  occupées <= candidates - nombre.
            entorse = _depasse(
                modele, regle, [(1, lit) for lit in occupees], 0, len(candidates) - nombre
            )
            if entorse is not None:
                entorses.append(entorse)
        _imputer(modele, regle, situation, entorses)


@_poseur(TypeRegle.SANS_TROU)
def sans_trou(modele: ModeleHoraires, regle: RegleResolue) -> None:
    """Chaque semaine, une personne a au plus `maximum` créneaux de trou (indicateur `trous_creneaux`)."""
    maximum = regle.valeurs.get("maximum", 0)
    masque = _masque(regle)
    for situation in _situations(modele, regle, relation=False):
        entorses: list[Litteral] = []
        for actives, _ in _configurations(modele, situation.indices, masque):
            trous = modele.trous(actives)
            entorse = _depasse(modele, regle, [(1, trou) for trou in trous], 0, maximum)
            if entorse is not None:
                entorses.append(entorse)
        _imputer(modele, regle, situation, entorses)


# --------------------------------------------------------------------------- #
# Relations entre séances
# --------------------------------------------------------------------------- #


def _par_matiere(modele: ModeleHoraires, indices: Sequence[int]) -> dict[int, list[int]]:
    par_matiere: dict[int, list[int]] = defaultdict(list)
    for index in indices:
        par_matiere[modele.seances[index].besoin.matiere_id].append(index)
    return par_matiere


@_poseur(TypeRegle.ORDRE_SEMAINE)
def ordre_semaine(modele: ModeleHoraires, regle: RegleResolue) -> None:
    """Dans chaque matière, toute séance « avant » commence strictement avant toute séance « après »
    de la même semaine (jour puis heure)."""
    avant, apres = regle.valeurs["avant"], regle.valeurs["apres"]
    masque = _masque(regle)
    for situation in _situations(modele, regle, relation=True):
        for indices in _par_matiere(modele, situation.indices).values():
            premieres = [i for i in indices if modele.seances[i].besoin.type_seance_id == avant]
            secondes = [i for i in indices if modele.seances[i].besoin.type_seance_id == apres]
            for a in premieres:
                for b in secondes:
                    if a == b or not (
                        modele.seances[a].semaines & modele.seances[b].semaines & masque
                    ):
                        continue
                    # début(a) - début(b) <= -1, les débuts codés croissant avec (jour, heure).
                    termes = [
                        (option.debut, lit)
                        for option, lit in zip(modele.options[a], modele.litteraux[a], strict=True)
                    ]
                    termes += [
                        (-option.debut, lit)
                        for option, lit in zip(modele.options[b], modele.litteraux[b], strict=True)
                    ]
                    entorse = _depasse(modele, regle, termes, 0, -1)
                    if entorse is not None:
                        _imputer(modele, regle, situation, [entorse])


@_poseur(TypeRegle.REPARTITION_MATIERE)
def repartition_matiere(modele: ModeleHoraires, regle: RegleResolue) -> None:
    """Les séances d'une matière d'un groupe tombent le même jour, ou au plus une par jour."""
    meme_jour = regle.valeurs["mode"] == MEME_JOUR
    masque = _masque(regle)
    for situation in _situations(modele, regle, relation=True):
        for indices in _par_matiere(modele, situation.indices).values():
            for actives, _ in _configurations(modele, indices, masque):
                par_jour = _par_jour(modele, actives)
                if meme_jour:
                    if len(actives) < 2 or len(par_jour) < 2:
                        continue
                    presents = [
                        modele.au_moins_un([lit for _, _, _, lit in par_jour[jour]])
                        for jour in sorted(par_jour)
                    ]
                    entorse = _depasse(modele, regle, [(1, lit) for lit in presents], 0, 1)
                    if entorse is not None:
                        _imputer(modele, regle, situation, [entorse])
                    continue
                for jour in sorted(par_jour):
                    choix = par_jour[jour]
                    if len({index for index, _, _, _ in choix}) < 2:
                        continue
                    entorse = _depasse(modele, regle, [(1, lit) for _, _, _, lit in choix], 0, 1)
                    if entorse is not None:
                        _imputer(modele, regle, situation, [entorse])
