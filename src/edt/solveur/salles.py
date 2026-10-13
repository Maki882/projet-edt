"""Affectation des salles : la seconde phase du calcul.

Les horaires sont fixés par la première phase (`edt.solveur.modele`) : chaque
séance a un début et la liste des salles possibles à ce début (`Option`). Il
reste à donner à chacune une salle sans jamais mettre dans la même salle deux
séances qui se chevauchent (`SALLE_UNIQUE`).

Modèle : un booléen par (séance, salle possible), exactement une salle par
séance, et pour chaque salle au plus une séance en cours à chaque instant,
parmi les séances actives une même semaine. Les horaires étant connus, ce
modèle est petit et se résout en une fraction de seconde.

Exigences de salle souples (`EQUIPEMENT`, `CAPACITE`, `TYPE_SALLE`,
`ACCESSIBILITE`, `DISPO_SALLE` de niveau « important » ou « facultatif ») :
une salle qui ne les satisfait pas reste possible, mais coûte `Regles.poids`
par exigence manquée ; le modèle minimise ce coût. Une entorse est **une
exigence non satisfaite par une séance** (une salle sans le matériel requis,
trop petite, du mauvais type, inaccessible, ou indisponible à cet horaire).
La première phase ne connaît pas ces coûts : elle choisit les horaires avec
toutes les salles admissibles, et c'est ici que les entorses sont évitées
quand les horaires le permettent.

Si l'affectation est impossible, on cherche un **conflit** : un sous-ensemble
de séances qui, à ces horaires, ne peuvent pas toutes recevoir une salle. La
première phase écarte alors cette combinaison d'horaires et recommence
(`ModeleHoraires.interdire`). Le résultat global reste exact : une solution
respecte toutes les contraintes dures, et une impossibilité n'est déclarée
que si elle est prouvée.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from edt.domaine.contraintes import CodeContrainte
from edt.solveur.modele import Litteral, Option, limiter_par_instant
from edt.solveur.preparation import Preparation
from edt.solveur.regles import Violation

journal = logging.getLogger(__name__)


@dataclass(frozen=True)
class AffectationSalles:
    """Résultat de la seconde phase.

    - `salles` : salle de chaque séance, dans l'ordre des séances ; `None` si
      aucune affectation n'a été trouvée ;
    - `conflit` : indices de séances qui ne peuvent pas toutes recevoir une
      salle à ces horaires (vide si `salles` est renseigné ou si rien n'est
      prouvé) ;
    - `prouve` : faux si la recherche a été interrompue par le temps limite ;
    - `entorses` : nombre d'exigences de salle souples non satisfaites, par
      contrainte, pour les salles retenues ;
    - `entorses_regles` : de même par règle `SALLES` souple (identifiant).
    """

    salles: tuple[int, ...] | None
    conflit: tuple[int, ...] = ()
    prouve: bool = True
    entorses: dict[CodeContrainte, int] = field(default_factory=dict)
    entorses_regles: dict[int, int] = field(default_factory=dict)


def affecter_salles(
    preparation: Preparation,
    horaires: Sequence[Option],
    graine: int = 0,
    temps_limite_s: float = 60.0,
) -> AffectationSalles:
    """Donne une salle à chaque séance, les horaires `horaires` étant fixés.

    `horaires[i]` est le choix retenu pour la séance d'indice `i`. La salle
    de l'indice (`SeanceAPlacer.indice`) est proposée comme point de départ.
    Les exigences de salle souples sont enfreintes le moins possible.
    """
    seances = preparation.seances
    if not preparation.regles.active(CodeContrainte.SALLE_UNIQUE):
        # Sans unicité des salles, toute salle admissible convient : la moins coûteuse.
        choisies = tuple(_preferee(preparation, i, h) for i, h in enumerate(horaires))
        entorses, regles = _entorses(preparation, horaires, choisies)
        return AffectationSalles(choisies, entorses=entorses, entorses_regles=regles)

    cp, presences, _ = _construire(preparation, horaires, avec_hypotheses=False)
    solveur = _solveur(graine, temps_limite_s)
    code = solveur.solve(cp)
    if code in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        choisies = tuple(
            next(
                salle
                for salle, litteral in presence.items()
                if litteral is True or solveur.boolean_value(litteral)
            )
            for presence in presences
        )
        entorses, regles = _entorses(preparation, horaires, choisies)
        if (entorses or regles) and code != cp_model.OPTIMAL:
            journal.info(
                "Affectation des salles rendue avant la preuve d'optimalité : %s",
                ", ".join(
                    f"{c.value} {n}" for c, n in sorted({**entorses, **regles}.items(), key=str)
                ),
            )
        return AffectationSalles(choisies, entorses=entorses, entorses_regles=regles)
    if code != cp_model.INFEASIBLE:
        journal.warning(
            "Affectation des salles interrompue (%s) pour %d séance(s)",
            solveur.status_name(code),
            len(seances),
        )
        return AffectationSalles(None, prouve=False)

    # Impossible : quelles séances ne peuvent pas toutes avoir une salle ?
    cp, _, hypotheses = _construire(preparation, horaires, avec_hypotheses=True)
    solveur = _solveur(graine, temps_limite_s)
    code = solveur.solve(cp)
    conflit = tuple(range(len(seances)))
    if code == cp_model.INFEASIBLE:
        par_variable = {hypothese.index: index for index, hypothese in enumerate(hypotheses)}
        coeur = sorted(
            par_variable[variable]
            for variable in solveur.sufficient_assumptions_for_infeasibility()
            if variable in par_variable
        )
        if coeur:
            conflit = tuple(coeur)
    journal.info(
        "Affectation des salles impossible à ces horaires : %d séance(s) en conflit (%s)",
        len(conflit),
        ", ".join(seances[index].seance.id for index in conflit[:12]),
    )
    return AffectationSalles(None, conflit=conflit)


def entorses_de_salle(
    preparation: Preparation, index: int, debut: int, salle: int
) -> tuple[Violation, ...]:
    """Exigences et règles souples que `salle` enfreint pour la séance `index` commençant à `debut`.

    Les exigences du besoin (`Regles.salles_compatibles`), l'indisponibilité
    de la salle (`DISPO_SALLE` souple) et les règles `SALLES` souples de la
    séance (`SeancePreparee.entorses_salles`).
    """
    seance = preparation.seances[index]
    violations = [
        Violation(code)
        for code in preparation.regles.salles_compatibles(seance.besoin).entorses.get(salle, ())
    ]
    if debut in seance.debuts_genants.get(salle, ()):
        violations.append(Violation(CodeContrainte.DISPO_SALLE))
    violations.extend(seance.entorses_salles.get(salle, ()))
    return tuple(violations)


def _penalite(preparation: Preparation, index: int, debut: int, salle: int) -> int:
    regles = preparation.regles
    return sum(
        regles.poids_violation(violation)
        for violation in entorses_de_salle(preparation, index, debut, salle)
    )


def _entorses(
    preparation: Preparation, horaires: Sequence[Option], salles: Sequence[int]
) -> tuple[dict[CodeContrainte, int], dict[int, int]]:
    """Entorses de salle pour les salles `salles` : par contrainte, puis par règle."""
    comptes: dict[CodeContrainte, int] = defaultdict(int)
    regles: dict[int, int] = defaultdict(int)
    for index, (horaire, salle) in enumerate(zip(horaires, salles, strict=True)):
        for violation in entorses_de_salle(preparation, index, horaire.debut, salle):
            if violation.regle_id is None:
                comptes[violation.code] += 1
            else:
                regles[violation.regle_id] += 1
    return dict(sorted(comptes.items())), dict(sorted(regles.items()))


def _preferee(preparation: Preparation, index: int, horaire: Option) -> int:
    """Salle la moins coûteuse ; à égalité la salle de l'indice, sinon la première admissible."""
    indice = preparation.seances[index].seance.indice
    ordre = list(horaire.salles)
    if indice is not None and indice.salle_id in ordre:
        ordre.remove(indice.salle_id)
        ordre.insert(0, indice.salle_id)
    return min(ordre, key=lambda salle: _penalite(preparation, index, horaire.debut, salle))


def _solveur(graine: int, temps_limite_s: float) -> cp_model.CpSolver:
    solveur = cp_model.CpSolver()
    solveur.parameters.max_time_in_seconds = max(0.001, temps_limite_s)
    solveur.parameters.num_workers = 1
    solveur.parameters.random_seed = graine % (2**31 - 1)
    solveur.parameters.permute_variable_randomly = True  # une autre graine, d'autres salles
    solveur.parameters.log_to_stdout = False
    return solveur


def _construire(
    preparation: Preparation, horaires: Sequence[Option], avec_hypotheses: bool
) -> tuple[cp_model.CpModel, list[dict[int, Litteral]], list[cp_model.IntVar]]:
    """Modèle d'affectation ; avec une hypothèse « cette séance a une salle » par séance si demandé.

    Sans hypothèses, le coût des exigences souples non satisfaites est
    minimisé (quand il peut être non nul).
    """
    cp = cp_model.CpModel()
    presences: list[dict[int, Litteral]] = []
    hypotheses: list[cp_model.IntVar] = []
    par_salle: dict[int, list[int]] = defaultdict(list)
    couts: list[tuple[int, cp_model.IntVar]] = []
    constante = 0
    for index, horaire in enumerate(horaires):
        presence: dict[int, Litteral]
        if avec_hypotheses:
            hypothese = cp.new_bool_var(f"h{index}")
            hypotheses.append(hypothese)
            if len(horaire.salles) == 1:
                presence = {horaire.salles[0]: hypothese}
            else:
                presence = {salle: cp.new_bool_var("") for salle in horaire.salles}
                cp.add_at_most_one(presence.values())
                cp.add_bool_or([*presence.values(), hypothese.negated()])
        elif len(horaire.salles) == 1:
            presence = {horaire.salles[0]: True}
            constante += _penalite(preparation, index, horaire.debut, horaire.salles[0])
        else:
            presence = {salle: cp.new_bool_var("") for salle in horaire.salles}
            cp.add_exactly_one(presence.values())
            preferee = _preferee(preparation, index, horaire)
            for salle, litteral in presence.items():
                cp.add_hint(litteral, salle == preferee)
                penalite = _penalite(preparation, index, horaire.debut, salle)
                if penalite:
                    couts.append((penalite, litteral))
        presences.append(presence)
        for salle in horaire.salles:
            par_salle[salle].append(index)
    if avec_hypotheses:
        cp.add_assumptions(hypotheses)
    elif couts:
        cp.minimize(sum(penalite * litteral for penalite, litteral in couts) + constante)

    vus: set[tuple[int, tuple[int, ...]]] = set()
    for salle in sorted(par_salle):
        for ensemble in preparation.ensembles_par_semaines(par_salle[salle]):
            if len(ensemble) < 2:
                continue
            plages = [
                (
                    horaires[index].debut,
                    horaires[index].debut + preparation.seances[index].duree,
                    presences[index][salle],
                )
                for index in ensemble
            ]
            limiter_par_instant(cp, plages, 1, vus)
    return cp, presences, hypotheses
