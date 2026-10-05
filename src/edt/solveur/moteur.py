"""Moteur de la semaine type : `resoudre(instantane, options, arret)`.

Déroulement d'un calcul
-----------------------
1. **Préparation** : domaines de débuts et de salles de chaque séance
   (`edt.solveur.preparation`).
2. **Pré-vérifications** (`edt.solveur.preverification`) : toute
   impossibilité certaine fait **refuser** le calcul, avec la liste des
   raisons ; les situations tendues deviennent des avertissements.
3. **Modèle** CP-SAT (`edt.solveur.modele`).
4. **Recherche**, bornée par `options.temps_limite_s` (construction du modèle
   comprise) et interruptible par `arret`.

Issues
------
- `SOLUTION` : toutes les séances sont placées ;
- `INFAISABLE` : impossibilité prouvée, par les pré-vérifications (calcul
  refusé, `statistiques["refuse"]` vaut vrai) ou par CP-SAT ;
- `TEMPS_DEPASSE` : rien trouvé dans le temps imparti, sans preuve ;
- `ARRETE` : arrêt demandé avant la fin.

Jamais de solution partielle : `placements` est vide hors `SOLUTION`.
"""

from __future__ import annotations

import logging
import os
import statistics
import threading
import time

from ortools.sat.python import cp_model

from edt.domaine.contraintes import CATALOGUE
from edt.domaine.instantane import Instantane
from edt.erreurs import ErreurEDT
from edt.journal import journal_calcul_courant
from edt.solveur.contrat import OptionsSolveur, ResultatSolveur, StatutSolveur
from edt.solveur.modele import Modele
from edt.solveur.preparation import Preparation
from edt.solveur.preverification import diagnostiquer

journal = logging.getLogger(__name__)

NOM_MOTEUR = "cp_sat"
NB_WORKERS_PAR_DEFAUT = 2
"""Fils de calcul quand `options.nb_workers` vaut 0 : le serveur cible a deux cœurs."""
INTERVALLE_SUIVI_S = 5.0
"""Intervalle entre deux lignes de progression dans le journal."""
PERIODE_SURVEILLANCE_S = 0.1
"""Fréquence à laquelle la demande d'arrêt est relue pendant la recherche."""
NB_RAISONS_DANS_LE_MESSAGE = 3


class _Interruption(Exception):
    """La construction du modèle doit s'arrêter (arrêt demandé ou temps dépassé)."""

    def __init__(self, statut: StatutSolveur) -> None:
        super().__init__(statut.value)
        self.statut = statut


class _Suivi(cp_model.CpSolverSolutionCallback):
    """Compte les solutions et journalise chaque amélioration de l'objectif."""

    def __init__(self, journal_calcul: logging.Logger, debut: float, avec_objectif: bool) -> None:
        super().__init__()
        self._journal = journal_calcul
        self._debut = debut
        self._avec_objectif = avec_objectif
        self.nb_solutions = 0
        self.objectif: float | None = None

    def on_solution_callback(self) -> None:
        self.nb_solutions += 1
        ecoule = time.perf_counter() - self._debut
        if self._avec_objectif:
            self.objectif = self.objective_value
            self._journal.info(
                "Solution n° %d après %.1f s : pénalité %d (borne %d)",
                self.nb_solutions,
                ecoule,
                round(self.objective_value),
                round(self.best_objective_bound),
            )
        else:
            self._journal.info("Solution trouvée après %.1f s", ecoule)


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
        reste = len(erreurs) - NB_RAISONS_DANS_LE_MESSAGE
        suite = f" (et {reste} autre(s) raison(s), voir la liste complète)" if reste > 0 else ""
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

    # 3. Modèle.
    def point_de_controle() -> None:
        if arret_demande():
            raise _Interruption(StatutSolveur.ARRETE)
        if time.perf_counter() - debut >= options.temps_limite_s:
            raise _Interruption(StatutSolveur.TEMPS_DEPASSE)

    message_arret = (
        "Calcul arrêté à votre demande avant la fin de la recherche : aucun emploi du temps "
        "n'a été enregistré."
    )
    message_temps = (
        f"Aucun emploi du temps n'a été trouvé en {options.temps_limite_s:g} s. Ce n'est pas "
        "une preuve d'impossibilité : relancer avec plus de temps peut aboutir."
    )
    debut_modele = time.perf_counter()
    try:
        point_de_controle()
        modele = Modele(preparation, options, point_de_controle)
    except _Interruption as interruption:
        if interruption.statut is StatutSolveur.ARRETE:
            return conclure(StatutSolveur.ARRETE, message_arret)
        return conclure(StatutSolveur.TEMPS_DEPASSE, message_temps)
    stats["nb_variables"] = modele.nb_variables
    stats["nb_contraintes"] = modele.nb_contraintes
    stats["duree_construction_s"] = round(time.perf_counter() - debut_modele, 3)
    journal.info(
        "Modèle construit en %.3f s : %d variable(s), %d contrainte(s), objectif %s",
        stats["duree_construction_s"],
        modele.nb_variables,
        modele.nb_contraintes,
        "actif" if modele.objectif_actif else "inactif",
    )

    # 4. Recherche.
    solveur = cp_model.CpSolver()
    nb_workers = options.nb_workers or min(NB_WORKERS_PAR_DEFAUT, os.cpu_count() or 1)
    solveur.parameters.max_time_in_seconds = max(
        0.001, options.temps_limite_s - (time.perf_counter() - debut)
    )
    solveur.parameters.num_workers = nb_workers
    solveur.parameters.random_seed = options.graine % (2**31 - 1)
    solveur.parameters.log_to_stdout = False
    if journal_calcul.isEnabledFor(logging.DEBUG):
        solveur.parameters.log_search_progress = True
        solveur.log_callback = lambda ligne: journal_calcul.debug("CP-SAT : %s", ligne)
    stats["nb_workers"] = nb_workers

    suivi = _Suivi(journal_calcul, debut, modele.objectif_actif)
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
    statut_cp = solveur.status_name(code)
    stats.update(
        statut_cp_sat=statut_cp,
        duree_resolution_s=round(time.perf_counter() - debut_recherche, 3),
        nb_conflits=solveur.num_conflicts,
        nb_branches=solveur.num_branches,
        nb_solutions=suivi.nb_solutions,
    )
    journal.info(
        "Recherche terminée en %.3f s : statut CP-SAT %s, %d conflit(s), %d branche(s)",
        stats["duree_resolution_s"],
        statut_cp,
        solveur.num_conflicts,
        solveur.num_branches,
    )

    if code == cp_model.MODEL_INVALID:
        raise ErreurEDT(
            "Le moteur a construit un modèle invalide : c'est une erreur interne, à signaler "
            "avec le journal du calcul.",
            [modele.cp.validate()],
        )
    if arret_demande():
        return conclure(StatutSolveur.ARRETE, message_arret)
    if code == cp_model.INFEASIBLE:
        return conclure(
            StatutSolveur.INFAISABLE,
            "Aucun emploi du temps ne peut respecter toutes les contraintes actives : c'est "
            "prouvé, relancer le calcul ne changera rien. Il faut alléger les données "
            "(salles, indisponibilités, verrous, volumes) ou désactiver une contrainte ; les "
            "avertissements des pré-vérifications indiquent où chercher en premier.",
        )
    if code not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return conclure(StatutSolveur.TEMPS_DEPASSE, message_temps)

    placements = modele.placements(solveur)
    message = f"Emploi du temps trouvé : {nb_seances} séance(s) placée(s)."
    if modele.objectif_actif:
        stats["objectif"] = round(solveur.objective_value)
        stats["borne_objectif"] = round(solveur.best_objective_bound)
        stats["optimal"] = code == cp_model.OPTIMAL
        qualite = (
            "c'est le meilleur résultat possible"
            if code == cp_model.OPTIMAL
            else "meilleure solution trouvée dans le temps imparti"
        )
        message += f" Pénalité de qualité : {stats['objectif']} ({qualite})."
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
    prochain_point = INTERVALLE_SUIVI_S
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
                etat = f"{suivi.nb_solutions} solution(s), meilleure pénalité {suivi.objectif:g}"
            else:
                etat = "aucune solution pour l'instant"
            journal_calcul.info("Recherche en cours depuis %.0f s : %s", ecoule, etat)
