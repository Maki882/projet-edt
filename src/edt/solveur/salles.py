"""Affectation des salles : la seconde phase du calcul.

Les horaires sont fixés par la première phase (`edt.solveur.modele`) : chaque
séance a un début et la liste des salles possibles à ce début (`Option`). Il
reste à donner à chacune une salle sans jamais mettre dans la même salle deux
séances qui se chevauchent (`SALLE_UNIQUE`).

Modèle : un booléen par (séance, salle possible), exactement une salle par
séance, et pour chaque salle au plus une séance en cours à chaque instant,
parmi les séances actives une même semaine. Les horaires étant connus, ce
modèle est petit et se résout en une fraction de seconde.

Si l'affectation est impossible, on cherche un **conflit** : un sous-ensemble
de séances qui, à ces horaires, ne peuvent pas toutes recevoir une salle. La
première phase écarte alors cette combinaison d'horaires et recommence
(`ModeleHoraires.interdire`). Le résultat global reste exact : une solution
respecte toutes les contraintes, et une impossibilité n'est déclarée que si
elle est prouvée.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from ortools.sat.python import cp_model

from edt.domaine.contraintes import CodeContrainte
from edt.solveur.modele import Litteral, Option, limiter_par_instant
from edt.solveur.preparation import Preparation

journal = logging.getLogger(__name__)


@dataclass(frozen=True)
class AffectationSalles:
    """Résultat de la seconde phase.

    - `salles` : salle de chaque séance, dans l'ordre des séances ; `None` si
      aucune affectation n'a été trouvée ;
    - `conflit` : indices de séances qui ne peuvent pas toutes recevoir une
      salle à ces horaires (vide si `salles` est renseigné ou si rien n'est
      prouvé) ;
    - `prouve` : faux si la recherche a été interrompue par le temps limite.
    """

    salles: tuple[int, ...] | None
    conflit: tuple[int, ...] = ()
    prouve: bool = True


def affecter_salles(
    preparation: Preparation,
    horaires: Sequence[Option],
    graine: int = 0,
    temps_limite_s: float = 60.0,
) -> AffectationSalles:
    """Donne une salle à chaque séance, les horaires `horaires` étant fixés.

    `horaires[i]` est le choix retenu pour la séance d'indice `i`. La salle
    de l'indice (`SeanceAPlacer.indice`) est proposée comme point de départ.
    """
    seances = preparation.seances
    if not preparation.regles.active(CodeContrainte.SALLE_UNIQUE):
        # Sans unicité des salles, toute salle admissible convient.
        return AffectationSalles(
            tuple(_preferee(preparation, i, h) for i, h in enumerate(horaires))
        )

    cp, presences, _ = _construire(preparation, horaires, avec_hypotheses=False)
    solveur = _solveur(graine, temps_limite_s)
    code = solveur.solve(cp)
    if code in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return AffectationSalles(
            tuple(
                next(
                    salle
                    for salle, litteral in presence.items()
                    if litteral is True or solveur.boolean_value(litteral)
                )
                for presence in presences
            )
        )
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


def _preferee(preparation: Preparation, index: int, horaire: Option) -> int:
    """Salle de l'indice si elle est possible, sinon la première salle admissible."""
    indice = preparation.seances[index].seance.indice
    if indice is not None and indice.salle_id in horaire.salles:
        return indice.salle_id
    return horaire.salles[0]


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
    """Modèle d'affectation ; avec une hypothèse « cette séance a une salle » par séance si demandé."""
    cp = cp_model.CpModel()
    presences: list[dict[int, Litteral]] = []
    hypotheses: list[cp_model.IntVar] = []
    par_salle: dict[int, list[int]] = defaultdict(list)
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
        else:
            presence = {salle: cp.new_bool_var("") for salle in horaire.salles}
            cp.add_exactly_one(presence.values())
            preferee = _preferee(preparation, index, horaire)
            for salle, litteral in presence.items():
                cp.add_hint(litteral, salle == preferee)
        presences.append(presence)
        for salle in horaire.salles:
            par_salle[salle].append(index)
    if avec_hypotheses:
        cp.add_assumptions(hypotheses)

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
