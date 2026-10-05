"""Moteur de la semaine type : `resoudre(instantane, options, arret)`.

Déroulement d'un calcul
-----------------------
1. **Préparation** : domaines de débuts et de salles de chaque séance
   (`edt.solveur.preparation`).
2. **Pré-vérifications** (`edt.solveur.preverification`) : toute
   impossibilité certaine fait **refuser** le calcul, avec la liste des
   raisons ; les situations tendues deviennent des avertissements.
3. **Une solution valide** : le modèle des horaires (`edt.solveur.modele`)
   choisit le début et le campus de chaque séance, puis chaque séance reçoit
   une salle (`edt.solveur.salles`). Si les salles ne suivent pas, les
   séances en cause sont signalées au modèle des horaires, qui écarte cette
   combinaison et cherche d'autres horaires.
4. **Amélioration** (seulement avec `options.optimiser`) : l'objectif de
   qualité est ajouté au modèle et la recherche repart de la solution valide,
   dans le temps qui reste. Si elle ne trouve pas mieux, ou n'a plus de
   temps, c'est la solution valide qui est rendue.

Le tout est borné par `options.temps_limite_s` (construction du modèle
comprise) et interruptible par `arret`.

Une solution trouvée n'est jamais perdue
----------------------------------------
Dès que l'étape 3 a abouti, le calcul rend une solution, quoi qu'il arrive
ensuite : temps limite atteint pendant l'amélioration, ou arrêt demandé par
l'utilisateur (la meilleure solution trouvée est alors conservée,
`statistiques["arrete_par_utilisateur"]` vaut vrai et un avertissement le dit).

Issues
------
- `SOLUTION` : toutes les séances sont placées ;
- `INFAISABLE` : impossibilité prouvée, par les pré-vérifications (calcul
  refusé, `statistiques["refuse"]` vaut vrai) ou par CP-SAT ;
- `TEMPS_DEPASSE` : aucune solution dans le temps imparti, sans preuve ;
- `ARRETE` : arrêt demandé avant qu'une solution existe.

Jamais de solution partielle : `placements` est vide hors `SOLUTION`.

Graine
------
`options.graine` fixe l'ordre dans lequel CP-SAT examine les variables : une
même graine redonne la même solution (avec un seul fil de calcul), une autre
graine en donne une autre, même sur un petit problème.
"""

from __future__ import annotations

import logging
import os
import statistics
import threading
import time
from dataclasses import dataclass

from ortools.sat.python import cp_model

from edt.domaine.contraintes import CATALOGUE
from edt.domaine.instantane import Instantane, Placement
from edt.erreurs import ErreurEDT
from edt.journal import journal_calcul_courant
from edt.solveur.contrat import OptionsSolveur, ResultatSolveur, StatutSolveur
from edt.solveur.modele import Bilan, ModeleHoraires, Option
from edt.solveur.preparation import Preparation
from edt.solveur.preverification import diagnostiquer
from edt.solveur.salles import affecter_salles

journal = logging.getLogger(__name__)

NOM_MOTEUR = "cp_sat"
NB_WORKERS_PAR_DEFAUT = 2
"""Fils de calcul quand `options.nb_workers` vaut 0 : le serveur cible a deux cœurs."""
INTERVALLE_SUIVI_S = 5.0
"""Intervalle entre deux lignes de progression dans le journal."""
INTERVALLE_SOLUTIONS_S = 2.0
"""Intervalle minimal entre deux solutions journalisées pendant une amélioration."""
PERIODE_SURVEILLANCE_S = 0.1
"""Fréquence à laquelle la demande d'arrêt est relue pendant la recherche."""
NB_RAISONS_DANS_LE_MESSAGE = 3
PART_RESERVEE_AUX_SALLES = 0.05
"""Part du temps limite gardée pour la phase des salles (au plus `RESERVE_MAX_S`)."""
RESERVE_MAX_S = 3.0
TEMPS_MIN_AMELIORATION_S = 1.0
"""En dessous de ce temps restant, l'amélioration n'est pas tentée."""
NB_SOLUTIONS_GARDEES = 20
"""Dernières solutions d'horaires gardées pour la phase des salles (amélioration)."""

MESSAGE_SOLUTION_CONSERVEE = (
    "Calcul arrêté à votre demande : la meilleure solution trouvée a été conservée."
)


class _Interruption(Exception):
    """La construction du modèle doit s'arrêter (arrêt demandé ou temps dépassé)."""

    def __init__(self, statut: StatutSolveur) -> None:
        super().__init__(statut.value)
        self.statut = statut


@dataclass(frozen=True)
class _Solution:
    """Une solution complète : le choix de chaque séance, ses horaires et sa salle."""

    choix: tuple[int, ...]
    horaires: list[Option]
    salles: tuple[int, ...]


class _Suivi(cp_model.CpSolverSolutionCallback):
    """Garde les solutions d'horaires et journalise la progression de l'amélioration."""

    def __init__(self, modele: ModeleHoraires, journal_calcul: logging.Logger, debut: float):
        super().__init__()
        self._modele = modele
        self._journal = journal_calcul
        self._debut = debut
        self._dernier_message = float("-inf")
        self.nb_solutions = 0
        self.solutions: list[tuple[int | None, tuple[int, ...]]] = []

    def on_solution_callback(self) -> None:
        self.nb_solutions += 1
        ecoule = time.perf_counter() - self._debut
        objectif = round(self.objective_value) if self._modele.objectif_actif else None
        self.solutions.append((objectif, self._modele.choix(self.boolean_value)))
        del self.solutions[:-NB_SOLUTIONS_GARDEES]
        if objectif is None:
            self._journal.info("Horaires trouvés après %.1f s", ecoule)
        elif ecoule - self._dernier_message >= INTERVALLE_SOLUTIONS_S:
            # Les améliorations se suivent de près : on n'en journalise qu'une partie.
            self._dernier_message = ecoule
            self._journal.info(
                "Amélioration n° %d après %.1f s : objectif %d (borne %d)",
                self.nb_solutions,
                ecoule,
                objectif,
                round(self.best_objective_bound),
            )

    @property
    def objectif(self) -> int | None:
        """Objectif de la meilleure solution d'horaires connue."""
        return self.solutions[-1][0] if self.solutions else None


def resoudre(
    instantane: Instantane,
    options: OptionsSolveur,
    arret: threading.Event | None = None,
) -> ResultatSolveur:
    """Place toutes les séances de l'instantané, ou explique pourquoi c'est impossible.

    Voir `edt.solveur.contrat` pour le contrat et l'en-tête de ce module pour
    le déroulement. `options.nb_workers == 0` utilise deux fils de calcul au
    plus ; un seul fil avec une graine fixe donne un résultat reproductible.
    """
    debut = time.perf_counter()
    journal_calcul = journal_calcul_courant()
    nb_seances = len(instantane.seances)
    stats: dict[str, int | float | str | bool] = {
        "moteur": NOM_MOTEUR,
        "nb_seances": nb_seances,
        "nb_salles": len(instantane.salles),
        "graine": options.graine,
        "optimiser": options.optimiser,
        "statut_cp_sat": "NON_LANCE",
        "refuse": False,
    }
    avertissements: list[str] = []

    def conclure(statut: StatutSolveur, message: str, placements=()) -> ResultatSolveur:
        stats["duree_s"] = round(time.perf_counter() - debut, 3)
        journal.info("Fin du calcul : %s - %s (%.3f s)", statut.value, message, stats["duree_s"])
        return ResultatSolveur(
            statut=statut,
            placements=tuple(placements),
            contraintes_desactivees=options.contraintes_desactivees,
            message=message,
            avertissements=tuple(avertissements),
            statistiques=stats,
        )

    def arret_demande() -> bool:
        return arret is not None and arret.is_set()

    def reste() -> float:
        return options.temps_limite_s - (time.perf_counter() - debut)

    journal.info(
        "Début du calcul : %d séance(s), %d salle(s), %d enseignant(s), %d groupe(s) ; options %s",
        nb_seances,
        len(instantane.salles),
        len(instantane.enseignants),
        len(instantane.groupes),
        options.vers_dict(),
    )
    if options.contraintes_desactivees:
        journal.warning(
            "Contraintes désactivées à la demande de l'utilisateur : %s",
            ", ".join(sorted(options.contraintes_desactivees)),
        )

    # 1 et 2. Préparation et pré-vérifications.
    preparation = Preparation(
        instantane, options.contraintes_desactivees, options.tolerer_violations_heritees
    )
    anomalies = diagnostiquer(preparation)
    erreurs = [anomalie for anomalie in anomalies if anomalie.bloquante]
    for anomalie in anomalies:
        niveau = logging.ERROR if anomalie.bloquante else logging.WARNING
        journal.log(niveau, "Pré-vérification [%s] : %s", anomalie.code.value, anomalie.message)
    avertissements.extend(anomalie.message for anomalie in erreurs)
    avertissements.extend(anomalie.message for anomalie in anomalies if not anomalie.bloquante)
    stats["duree_preparation_s"] = round(time.perf_counter() - debut, 3)
    if erreurs:
        stats["refuse"] = True
        raisons = " ".join(a.message for a in erreurs[:NB_RAISONS_DANS_LE_MESSAGE])
        autres = len(erreurs) - NB_RAISONS_DANS_LE_MESSAGE
        suite = f" (et {autres} autre(s) raison(s), voir la liste complète)" if autres > 0 else ""
        conseil = ""
        if preparation.conflits_verrous and not options.tolerer_violations_heritees:
            conseil = (
                " Pour garder des séances verrouillées qui se contredisent, cocher « tolérer "
                "les violations héritées »."
            )
        return conclure(
            StatutSolveur.INFAISABLE,
            f"Le calcul n'a pas été lancé : {len(erreurs)} impossibilité(s) certaine(s) "
            f"relevée(s) avant la recherche. {raisons}{suite}{conseil}",
        )
    if nb_seances == 0:
        return conclure(StatutSolveur.SOLUTION, "Aucune séance à placer pour ce semestre.")
    _journaliser_domaines(preparation)

    # Modèle des horaires, sans objectif : il s'agit d'abord de trouver une solution valide.
    def point_de_controle() -> None:
        if arret_demande():
            raise _Interruption(StatutSolveur.ARRETE)
        if reste() <= 0:
            raise _Interruption(StatutSolveur.TEMPS_DEPASSE)

    message_arret = (
        "Calcul arrêté à votre demande avant qu'un emploi du temps soit trouvé : rien n'a "
        "été enregistré."
    )
    message_temps = (
        f"Aucun emploi du temps n'a été trouvé en {options.temps_limite_s:g} s. Ce n'est pas "
        "une preuve d'impossibilité : relancer avec plus de temps peut aboutir."
    )
    message_infaisable = (
        "Aucun emploi du temps ne peut respecter toutes les contraintes actives : c'est "
        "prouvé, relancer le calcul ne changera rien. Il faut alléger les données (salles, "
        "indisponibilités, verrous, volumes) ou désactiver une contrainte ; les "
        "avertissements des pré-vérifications indiquent où chercher en premier."
    )
    debut_modele = time.perf_counter()
    try:
        point_de_controle()
        modele = ModeleHoraires(preparation, options, point_de_controle)
    except _Interruption as interruption:
        if interruption.statut is StatutSolveur.ARRETE:
            return conclure(StatutSolveur.ARRETE, message_arret)
        return conclure(StatutSolveur.TEMPS_DEPASSE, message_temps)
    stats["nb_variables"] = modele.nb_variables
    stats["nb_contraintes"] = modele.nb_contraintes
    stats["duree_construction_s"] = round(time.perf_counter() - debut_modele, 3)
    journal.info(
        "Modèle des horaires construit en %.3f s : %d variable(s), %d contrainte(s)",
        stats["duree_construction_s"],
        modele.nb_variables,
        modele.nb_contraintes,
    )

    nb_workers = options.nb_workers or min(NB_WORKERS_PAR_DEFAUT, os.cpu_count() or 1)
    reserve = min(RESERVE_MAX_S, PART_RESERVEE_AUX_SALLES * options.temps_limite_s)
    stats.update(nb_workers=nb_workers, duree_resolution_s=0.0, duree_salles_s=0.0, nb_coupes=0)
    stats.update(nb_conflits=0, nb_branches=0, nb_solutions=0)

    def chercher() -> tuple[int, cp_model.CpSolver, _Suivi]:
        """Une recherche CP-SAT sur le modèle des horaires, dans le temps qui reste."""
        solveur = cp_model.CpSolver()
        solveur.parameters.max_time_in_seconds = max(0.001, reste() - reserve)
        solveur.parameters.num_workers = nb_workers
        solveur.parameters.random_seed = options.graine % (2**31 - 1)
        # La graine décide de l'ordre des variables : sans cela, un petit problème
        # résolu sans tirage aléatoire donnerait la même solution à toutes les graines.
        solveur.parameters.permute_variable_randomly = True
        solveur.parameters.log_to_stdout = False
        if journal_calcul.isEnabledFor(logging.DEBUG):
            solveur.parameters.log_search_progress = True
            solveur.log_callback = lambda ligne: journal_calcul.debug("CP-SAT : %s", ligne)
        suivi = _Suivi(modele, journal_calcul, debut)
        fini = threading.Event()
        surveillant = threading.Thread(
            target=_surveiller,
            args=(solveur, arret, fini, journal_calcul, debut, suivi),
            name="edt-surveillance-calcul",
            daemon=True,
        )
        debut_recherche = time.perf_counter()
        surveillant.start()
        try:
            code = solveur.solve(modele.cp, suivi)
        finally:
            fini.set()
            surveillant.join()
        duree = time.perf_counter() - debut_recherche
        stats["statut_cp_sat"] = solveur.status_name(code)
        stats["duree_resolution_s"] = round(stats["duree_resolution_s"] + duree, 3)
        stats["nb_conflits"] += solveur.num_conflicts
        stats["nb_branches"] += solveur.num_branches
        stats["nb_solutions"] += suivi.nb_solutions
        journal.info(
            "Recherche des horaires terminée en %.3f s : statut CP-SAT %s, %d solution(s), "
            "%d conflit(s), %d branche(s)",
            duree,
            solveur.status_name(code),
            suivi.nb_solutions,
            solveur.num_conflicts,
            solveur.num_branches,
        )
        if code == cp_model.MODEL_INVALID:
            raise ErreurEDT(
                "Le moteur a construit un modèle invalide : c'est une erreur interne, à "
                "signaler avec le journal du calcul.",
                [modele.cp.validate()],
            )
        return code, solveur, suivi

    def donner_des_salles(choix: tuple[int, ...]) -> _Solution | bool:
        """La solution complète pour ces horaires ; sinon vrai si l'échec est prouvé, faux sinon.

        Un échec prouvé écarte ces horaires du modèle (coupe).
        """
        debut_salles = time.perf_counter()
        horaires = modele.horaires(choix)
        affectation = affecter_salles(preparation, horaires, options.graine, max(1.0, reste()))
        stats["duree_salles_s"] = round(
            stats["duree_salles_s"] + time.perf_counter() - debut_salles, 3
        )
        if affectation.salles is not None:
            return _Solution(choix, horaires, affectation.salles)
        if affectation.conflit:
            modele.interdire(choix, affectation.conflit)
            stats["nb_coupes"] = modele.nb_coupes
        return bool(affectation.conflit)

    # 3. Une solution valide : horaires, puis salles ; d'autres horaires si les salles ne suivent pas.
    while True:
        code, solveur, suivi = chercher()
        if not suivi.solutions:
            if arret_demande():
                return conclure(StatutSolveur.ARRETE, message_arret)
            if code == cp_model.INFEASIBLE:
                return conclure(StatutSolveur.INFAISABLE, message_infaisable)
            return conclure(StatutSolveur.TEMPS_DEPASSE, message_temps)
        essai = donner_des_salles(suivi.solutions[-1][1])
        if isinstance(essai, _Solution):
            solution = essai
            break
        if arret_demande():
            return conclure(StatutSolveur.ARRETE, message_arret)
        if reste() <= 0 or not essai:
            return conclure(StatutSolveur.TEMPS_DEPASSE, message_temps)
        journal.info(
            "Les salles ne suivent pas : %d combinaison(s) d'horaires écartée(s), nouvelle "
            "recherche des horaires",
            modele.nb_coupes,
        )
    valeurs = list(solveur.response_proto.solution)
    # Il n'y a qu'un modèle : l'amélioration lui ajoute l'objectif. La première recherche,
    # elle, est libérée avant que la seconde commence.
    del solveur, suivi
    stats["duree_premiere_solution_s"] = round(time.perf_counter() - debut, 3)
    journal.info("Emploi du temps valide trouvé après %.1f s", stats["duree_premiere_solution_s"])

    # 4. Amélioration : on repart de la solution valide, dans le temps qui reste.
    bilan: Bilan | None = None
    optimal = amelioration_lancee = temps_manquant = False
    if options.optimiser and not arret_demande():
        if reste() - reserve < TEMPS_MIN_AMELIORATION_S:
            temps_manquant = True
            journal.warning(
                "Plus de temps pour améliorer la solution : elle est rendue telle quelle."
            )
        elif modele.poser_objectif():
            bilan = modele.bilan(solution.choix)
            modele.partir_de(valeurs)
            stats["nb_variables"] = modele.nb_variables
            stats["nb_contraintes"] = modele.nb_contraintes
            stats["objectif_premiere_solution"] = bilan.objectif
            journal.info(
                "Amélioration : départ à l'objectif %d (%d variable(s), %d contrainte(s))",
                bilan.objectif,
                modele.nb_variables,
                modele.nb_contraintes,
            )
            amelioration_lancee = True
            code, solveur, suivi = chercher()
            for _, choix in reversed(suivi.solutions):
                candidat = modele.bilan(choix)
                if candidat.objectif >= bilan.objectif:
                    break  # les solutions gardées vont de la moins bonne à la meilleure
                essai = donner_des_salles(choix)
                if isinstance(essai, _Solution):
                    solution, bilan = essai, candidat
                    break
            if suivi.solutions:
                stats["borne_objectif"] = round(solveur.best_objective_bound)
                optimal = code == cp_model.OPTIMAL and bilan.objectif <= round(
                    solveur.objective_value
                )
            else:
                journal.info(
                    "L'amélioration n'a pas eu le temps d'aboutir (statut %s) : la première "
                    "solution valide est conservée.",
                    solveur.status_name(code),
                )

    placements = []
    for seance, horaire, salle in zip(
        preparation.seances, solution.horaires, solution.salles, strict=True
    ):
        jour, minute = preparation.decoder(horaire.debut)
        placements.append(Placement(seance.seance.id, jour, minute, minute + seance.duree, salle))

    message = f"Emploi du temps trouvé : {nb_seances} séance(s) placée(s)."
    if bilan is not None:
        stats.update(objectif=bilan.objectif, optimal=optimal)
        mesures = []
        if bilan.trous is not None:
            stats["trous"] = bilan.trous
            mesures.append(
                f"{bilan.trous} créneau(x) libre(s) entre deux cours dans les journées des groupes"
            )
        if bilan.samedis is not None:
            stats["samedis"] = bilan.samedis
            mesures.append(f"{bilan.samedis} séance(s) le samedi")
        if mesures:
            message += f" Bilan sur l'ensemble du semestre : {' et '.join(mesures)}."
    if arret_demande():
        stats["arrete_par_utilisateur"] = True
        avertissements.append(MESSAGE_SOLUTION_CONSERVEE)
        message += f" {MESSAGE_SOLUTION_CONSERVEE}"
    elif optimal:
        message += " On ne peut pas faire mieux avec les préférences actuelles."
    elif amelioration_lancee:
        message += (
            " L'amélioration s'est arrêtée au temps limite : relancer plus longtemps peut "
            "encore l'affiner."
        )
    elif temps_manquant:
        message += (
            " Le temps a manqué pour l'améliorer : relancer avec plus de temps donnera un "
            "emploi du temps plus confortable."
        )
    if options.contraintes_desactivees:
        libelles = sorted(CATALOGUE[code_].libelle for code_ in options.contraintes_desactivees)
        message += " Contraintes ignorées à votre demande : " + ", ".join(libelles) + "."
    return conclure(StatutSolveur.SOLUTION, message, placements)


def _journaliser_domaines(preparation: Preparation) -> None:
    """Taille du problème : domaines de débuts et de salles (minimum, médiane)."""
    debuts = [len(seance.debuts) for seance in preparation.seances]
    salles = [len(seance.salles) for seance in preparation.seances]
    journal.info(
        "Domaines : débuts par séance min %d / médiane %d ; salles par séance min %d / "
        "médiane %d ; %d séance(s) à horaire fixe ; %d verrou(s)",
        min(debuts),
        statistics.median(debuts),
        min(salles),
        statistics.median(salles),
        sum(1 for nombre in debuts if nombre == 1),
        sum(1 for seance in preparation.seances if seance.verrouillee),
    )


def _surveiller(
    solveur: cp_model.CpSolver,
    arret: threading.Event | None,
    fini: threading.Event,
    journal_calcul: logging.Logger,
    debut: float,
    suivi: _Suivi,
) -> None:
    """Fil de surveillance : relaie la demande d'arrêt et journalise la progression."""
    prochain_point = (time.perf_counter() - debut) + INTERVALLE_SUIVI_S
    annonce = False
    while not fini.wait(PERIODE_SURVEILLANCE_S):
        if arret is not None and arret.is_set():
            if not annonce:
                journal_calcul.info("Arrêt demandé : interruption de la recherche.")
                annonce = True
            # Répété à chaque tour : sans effet tant que la recherche n'a pas démarré.
            solveur.stop_search()
            continue
        ecoule = time.perf_counter() - debut
        if ecoule >= prochain_point:
            prochain_point += INTERVALLE_SUIVI_S
            if suivi.objectif is not None:
                etat = f"{suivi.nb_solutions} amélioration(s), objectif {suivi.objectif}"
            elif suivi.nb_solutions:
                etat = "horaires trouvés"
            else:
                etat = "aucune solution pour l'instant"
            journal_calcul.info("Recherche en cours depuis %.0f s : %s", ecoule, etat)
