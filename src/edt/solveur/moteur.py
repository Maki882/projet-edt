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
4. **Amélioration** : l'objectif est ajouté au modèle et la recherche
   repart de la solution valide, dans le temps qui reste. L'objectif compte
   toujours les **entorses** aux contraintes souples (niveaux « important »
   et « facultatif », `edt.domaine.contraintes.POIDS_NIVEAU`) ; avec
   `options.optimiser` s'y ajoutent les termes de confort (trous, campus,
   samedi, créneaux extrêmes). Sans `optimiser`, l'amélioration n'a lieu que
   si la solution valide comporte des entorses, et s'arrête dès qu'elles
   sont nulles ou qu'elle ne progresse plus. Si elle ne trouve pas mieux, ou
   n'a plus de temps, c'est la solution valide qui est rendue. Elle s'arrête
   d'elle-même quand elle ne progresse plus (`options.arret_sur_stagnation`,
   voir `delai_de_stagnation`), sans attendre le temps limite.

Les entorses retenues sont dans `statistiques["entorses"]` (`{code: nombre}`)
et le message les dit en clair (« 2 entorses à des règles importantes (pause
entre deux séances : 2). ») ; rien n'est dit s'il n'y en a aucune.

Le tout est borné par `options.temps_limite_s` (construction du modèle
comprise) et interruptible par `arret`. Seule exception : si CP-SAT rend la
main en retard à la fin de l'amélioration, les meilleurs horaires trouvés
reçoivent quand même leurs salles (une seconde ou deux), plutôt que de
perdre l'amélioration.

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

Pour une solution, `statistiques["raison_arret"]` dit pourquoi la recherche
s'est arrêtée là : `premiere_solution` (pas d'amélioration demandée),
`optimum_prouve`, `stagnation` (plus de progrès, durée dans
`duree_sans_progres_s`), `temps_limite`, `temps_manquant` (pas le temps
d'améliorer) ou `arret_utilisateur`.

Graine
------
`options.graine` fixe l'ordre dans lequel CP-SAT examine les variables : une
même graine redonne la même solution (avec un seul fil de calcul), une autre
graine en donne une autre, même sur un petit problème. Cela vaut tant que la
recherche n'est pas coupée par l'horloge : temps limite ou arrêt sur
stagnation (que `options.arret_sur_stagnation` permet de désactiver).
"""

from __future__ import annotations

import logging
import os
import statistics
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from ortools.sat.python import cp_model

from edt.domaine.contraintes import CATALOGUE, CodeContrainte, Niveau
from edt.domaine.instantane import Instantane, Placement
from edt.erreurs import ErreurEDT
from edt.journal import journal_calcul_courant
from edt.solveur.contrat import OptionsSolveur, ResultatSolveur, StatutSolveur
from edt.solveur.modele import Bilan, ModeleHoraires, Option
from edt.solveur.preparation import Preparation
from edt.solveur.preverification import diagnostiquer
from edt.solveur.regles import Regles
from edt.solveur.salles import affecter_salles

journal = logging.getLogger(__name__)

NOM_MOTEUR = "cp_sat"
NB_WORKERS_PAR_DEFAUT = 2
"""Fils de calcul quand `options.nb_workers` vaut 0 : deux, ce que supporte un serveur de
deux cœurs (mesures dans docs/solveur.md)."""
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
TEMPS_SALLES_GARANTI_S = 10.0
"""Temps laissé aux salles des meilleurs horaires améliorés, même temps limite atteint."""
TEMPS_SALLES_MAX_S = 10.0
"""Temps maximal d'une affectation des salles : avec des exigences souples, elle a un objectif
à minimiser et ne doit pas consommer le temps de la recherche des horaires."""
TEMPS_MIN_AMELIORATION_S = 1.0
"""En dessous de ce temps restant, l'amélioration n'est pas tentée."""
NB_SOLUTIONS_GARDEES = 20
"""Dernières solutions d'horaires gardées pour la phase des salles (amélioration)."""
STAGNATION_MIN_S = 10.0
"""Durée sans progrès au bout de laquelle l'amélioration s'arrête d'elle-même, au moins."""
STAGNATION_PART = 1 / 3
"""... et au moins cette part du temps écoulé depuis le lancement du calcul."""

MESSAGE_SOLUTION_CONSERVEE = (
    "Calcul arrêté à votre demande : la meilleure solution trouvée a été conservée."
)


def delai_de_stagnation(ecoule_s: float) -> float:
    """Durée sans progrès qui arrête l'amélioration, `ecoule_s` secondes après le lancement.

    Un petit problème atteint vite son palier : `STAGNATION_MIN_S` suffit à le
    constater. Un gros progresse par à-coups de plus en plus espacés : la
    patience grandit avec le temps déjà passé (`STAGNATION_PART`), pour ne pas
    couper une recherche qui avance encore. Mesures dans docs/solveur.md.
    """
    return max(STAGNATION_MIN_S, STAGNATION_PART * ecoule_s)


class _Interruption(Exception):
    """La construction du modèle doit s'arrêter (arrêt demandé ou temps dépassé)."""

    def __init__(self, statut: StatutSolveur) -> None:
        super().__init__(statut.value)
        self.statut = statut


@dataclass(frozen=True)
class _Solution:
    """Une solution complète : le choix de chaque séance, ses horaires, sa salle.

    `entorses` : exigences de salle souples non satisfaites, par contrainte ;
    `entorses_regles` : règles `SALLES` souples enfreintes, par règle.
    """

    choix: tuple[int, ...]
    horaires: list[Option]
    salles: tuple[int, ...]
    entorses: dict[CodeContrainte, int]
    entorses_regles: dict[int, int]


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
        self.dernier_progres: float | None = None
        """Instant (secondes depuis le lancement) de la dernière solution trouvée."""
        self.stagnation_s: float | None = None
        """Durée sans progrès constatée quand la recherche a été arrêtée pour stagnation."""

    def on_solution_callback(self) -> None:
        self.nb_solutions += 1
        ecoule = time.perf_counter() - self._debut
        self.dernier_progres = ecoule
        objectif = round(self.objective_value) if self._modele.objectif_actif else None
        self.solutions.append((objectif, self._modele.choix(self.boolean_value)))
        del self.solutions[:-NB_SOLUTIONS_GARDEES]
        if objectif is None:
            self._journal.info("Horaires trouvés après %.1f s", ecoule)
        elif ecoule - self._dernier_message >= INTERVALLE_SOLUTIONS_S:
            # Les améliorations se suivent de près : seule une partie est journalisée.
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
    sur_premiere_solution: Callable[[float], None] | None = None,
) -> ResultatSolveur:
    """Place toutes les séances de l'instantané, ou explique pourquoi c'est impossible.

    Voir `edt.solveur.contrat` pour le contrat et l'en-tête de ce module pour
    le déroulement. `options.nb_workers == 0` utilise deux fils de calcul au
    plus ; un seul fil avec une graine fixe donne un résultat reproductible.

    `sur_premiere_solution`, s'il est donné, est appelé une seule fois, dès
    qu'un emploi du temps complet existe (horaires et salles) : à partir de
    cet instant, un arrêt demandé garde une solution. Il reçoit la durée
    écoulée depuis le lancement, en secondes. Il n'est pas appelé si aucune
    solution n'est trouvée. Une exception qu'il lève est journalisée avec sa
    trace et n'interrompt pas le calcul.
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
    niveaux_regles: dict[int, Niveau] = {}

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
            niveaux=dict(options.niveaux),
            niveaux_regles=dict(niveaux_regles),
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
        instantane,
        options.contraintes_desactivees,
        options.tolerer_violations_heritees,
        options.niveaux,
        options.niveaux_regles,
    )
    niveaux_regles.update(preparation.regles.niveaux_regles)
    inconnues = sorted(set(options.niveaux_regles or ()) - {r.id for r in instantane.regles})
    if inconnues:
        # Identifiant périmé (règle supprimée, désactivée, d'une autre année) : l'utilisateur
        # croirait avoir réglé une règle ; le résultat n'en garde que les règles connues.
        avertissement = (
            f"Niveau demandé pour {len(inconnues)} règle(s) personnalisée(s) inconnue(s) de ce "
            f"semestre (n° {', '.join(map(str, inconnues))} : supprimée(s), inactive(s) ou "
            "d'une autre année) : ignoré."
        )
        journal.warning(avertissement)
        avertissements.append(avertissement)
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
        # Le conseil « tolérer les violations héritées » est dans le message de chaque
        # conflit de verrous (`diagnostiquer`) : il n'est pas répété ici.
        return conclure(
            StatutSolveur.INFAISABLE,
            f"Le calcul n'a pas été lancé : {len(erreurs)} impossibilité(s) certaine(s) "
            f"relevée(s) avant la recherche. {raisons}{suite}",
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
        "Aucun emploi du temps ne peut respecter toutes les règles retenues pour ce calcul : "
        "c'est prouvé, relancer le calcul ne changera rien. Alléger les données (salles, "
        "indisponibilités, verrous, volumes) ou abaisser le niveau d'une règle (important ou "
        "facultatif) ; les avertissements des vérifications avant calcul indiquent où chercher "
        "en premier."
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

    # Jamais plus de fils que de cœurs : chaque fil de CP-SAT a sa copie du modèle, et un
    # réglage trop grand multiplierait la mémoire du calcul sans rien accélérer.
    nb_workers = min(options.nb_workers or NB_WORKERS_PAR_DEFAUT, os.cpu_count() or 1)
    reserve = min(RESERVE_MAX_S, PART_RESERVEE_AUX_SALLES * options.temps_limite_s)
    stats.update(nb_workers=nb_workers, duree_resolution_s=0.0, duree_salles_s=0.0, nb_coupes=0)
    stats.update(nb_conflits=0, nb_branches=0, nb_solutions=0)

    def chercher(amelioration: bool = False) -> tuple[int, cp_model.CpSolver, _Suivi]:
        """Une recherche CP-SAT sur le modèle des horaires, dans le temps qui reste."""
        solveur = cp_model.CpSolver()
        if amelioration:
            # Une solution valide existe déjà : le temps sert mieux à l'améliorer qu'à
            # préparer longuement le modèle (prétraitement allégé, voir docs/solveur.md).
            solveur.parameters.cp_model_probing_level = 0
            solveur.parameters.symmetry_level = 0
            solveur.parameters.max_presolve_iterations = 1
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
            kwargs={"stagnation": amelioration and options.arret_sur_stagnation},
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
                "Erreur interne du générateur : le calcul n'a pas pu être lancé. La signaler "
                "avec le journal du calcul.",
                [modele.cp.validate()],
            )
        return code, solveur, suivi

    def donner_des_salles(choix: tuple[int, ...], temps_min_s: float = 1.0) -> _Solution | bool:
        """La solution complète pour ces horaires ; sinon vrai si l'échec est prouvé, faux sinon.

        Un échec prouvé écarte ces horaires du modèle (coupe). La recherche des
        salles dispose du temps qui reste, et d'au moins `temps_min_s`.
        """
        debut_salles = time.perf_counter()
        horaires = modele.horaires(choix)
        affectation = affecter_salles(
            preparation,
            horaires,
            options.graine,
            max(temps_min_s, min(reste(), TEMPS_SALLES_MAX_S)),
        )
        stats["duree_salles_s"] = round(
            stats["duree_salles_s"] + time.perf_counter() - debut_salles, 3
        )
        if affectation.salles is not None:
            return _Solution(
                choix,
                horaires,
                affectation.salles,
                affectation.entorses,
                affectation.entorses_regles,
            )
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
    if sur_premiere_solution is not None:
        try:
            sur_premiere_solution(time.perf_counter() - debut)
        except Exception:
            # Le rappel informe l'interface ; son échec ne doit pas coûter la solution.
            journal.exception("Le rappel de première solution a échoué : le calcul continue.")

    def cout_salles(essai: _Solution) -> int:
        """Pénalité des entorses de la phase des salles, au poids de l'objectif."""
        regles = preparation.regles
        return sum(regles.poids(code) * nombre for code, nombre in essai.entorses.items()) + sum(
            regles.poids_imputation(regle_id) * nombre
            for regle_id, nombre in essai.entorses_regles.items()
        )

    def toutes_les_entorses(essai: _Solution, bilan: Bilan) -> dict:
        """Entorses des deux phases et entorses fixes, par contrainte puis par règle."""
        return _cumul(
            bilan.entorses,
            essai.entorses,
            preparation.entorses_fixes,
            bilan.entorses_regles,
            essai.entorses_regles,
        )

    bilan = modele.bilan(solution.choix)
    # Coût complet de la solution retenue : objectif et entorses des horaires, entorses des salles.
    cout = bilan.total + cout_salles(solution)
    journal.info(
        "Emploi du temps valide trouvé après %.1f s%s",
        stats["duree_premiere_solution_s"],
        _texte_entorses(toutes_les_entorses(solution, bilan)),
    )

    # 4. Amélioration : la recherche repart de la solution valide, dans le temps qui reste.
    # Sans « optimiser », elle ne porte que sur les entorses et n'a lieu que s'il y en a (phase des
    # horaires ou des salles).
    optimal = optimum_horaires = amelioration_lancee = temps_manquant = False
    stagnation_s: float | None = None
    if (options.optimiser or cout > 0) and not arret_demande():
        if reste() - reserve < TEMPS_MIN_AMELIORATION_S:
            temps_manquant = True
            journal.warning(
                "Plus de temps pour améliorer la solution : elle est rendue telle quelle."
            )
        elif modele.poser_objectif(confort=options.optimiser):
            bilan = modele.bilan(solution.choix)
            cout = bilan.total + cout_salles(solution)
            modele.partir_de(valeurs)
            stats["nb_variables"] = modele.nb_variables
            stats["nb_contraintes"] = modele.nb_contraintes
            stats["objectif_premiere_solution"] = bilan.objectif
            journal.info(
                "Amélioration : départ à l'objectif %d (dont %d d'entorses ; %d variable(s), "
                "%d contrainte(s))",
                bilan.total,
                bilan.penalite,
                modele.nb_variables,
                modele.nb_contraintes,
            )
            amelioration_lancee = True
            code, solveur, suivi = chercher(amelioration=True)
            if reste() <= 0:
                journal.warning(
                    "La recherche a rendu la main %.1f s après le temps limite.", -reste()
                )
            # Les horaires gardés vont de la moins bonne à la meilleure note : chacun reçoit ses
            # salles tant qu'il peut battre le coût complet de la solution retenue (sa note seule
            # est une borne inférieure), et le premier dont les salles ne coûtent rien conclut.
            for rang, (_, choix) in enumerate(reversed(suivi.solutions)):
                candidat = modele.bilan(choix)
                if candidat.total >= cout:
                    break
                if rang and reste() <= 0:
                    journal.warning(
                        "Temps limite atteint : les horaires améliorés précédents ne sont pas "
                        "essayés."
                    )
                    break
                # Les meilleurs horaires reçoivent toujours leurs salles, même si la recherche
                # a rendu la main en retard : une seconde de dépassement vaut mieux que des
                # minutes d'amélioration perdues.
                essai = donner_des_salles(choix, TEMPS_SALLES_GARANTI_S if rang == 0 else 1.0)
                if not isinstance(essai, _Solution):
                    journal.info(
                        "Horaires améliorés (objectif %d) écartés : les salles ne suivent pas",
                        candidat.total,
                    )
                    continue
                cout_essai = candidat.total + cout_salles(essai)
                if cout_essai < cout:
                    solution, bilan, cout = essai, candidat, cout_essai
                if not essai.entorses and not essai.entorses_regles:
                    break
                journal.info(
                    "Horaires améliorés (objectif %d) : leurs salles coûtent %d d'entorses (%s), "
                    "les horaires précédents sont essayés",
                    candidat.total,
                    cout_salles(essai),
                    ", ".join(
                        f"{c.value if isinstance(c, CodeContrainte) else c} {n}"
                        for c, n in {**essai.entorses, **essai.entorses_regles}.items()
                    ),
                )
            stagnation_s = suivi.stagnation_s
            if suivi.solutions:
                stats["borne_objectif"] = round(solveur.best_objective_bound)
                # Optimal : les horaires retenus sont les mieux notés possibles (prouvé) et leurs
                # salles ne coûtent aucune entorse ; la phase des horaires ignore le coût des salles.
                optimum_horaires = code == cp_model.OPTIMAL
                optimal = (
                    optimum_horaires
                    and bilan.total <= round(solveur.objective_value)
                    and not solution.entorses
                    and not solution.entorses_regles
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

    cumul = toutes_les_entorses(solution, bilan)
    entorses = {c: n for c, n in cumul.items() if isinstance(c, CodeContrainte)}
    entorses_regles = {c: n for c, n in cumul.items() if isinstance(c, int)}
    stats["entorses"] = {code.value: nombre for code, nombre in entorses.items()}
    stats["entorses_regles"] = {
        preparation.regles.regle(regle_id).code: nombre
        for regle_id, nombre in entorses_regles.items()
    }
    message = f"Emploi du temps trouvé : {nb_seances} séance(s) placée(s)."
    if entorses:
        message += " " + _phrase_entorses(entorses, preparation.regles.niveaux)
    if entorses_regles:
        message += " " + _phrase_entorses_regles(entorses_regles, preparation.regles)
    if options.optimiser and amelioration_lancee:
        stats.update(objectif=bilan.objectif, optimal=optimal)
        mesures = []
        if bilan.trous is not None:
            stats["trous"] = bilan.trous
            creneaux = "créneau libre" if bilan.trous <= 1 else "créneaux libres"
            mesures.append(
                f"{bilan.trous} {creneaux} entre deux cours dans les journées des groupes"
            )
        if bilan.samedis is not None:
            stats["samedis"] = bilan.samedis
            seances = "séance" if bilan.samedis <= 1 else "séances"
            mesures.append(f"{bilan.samedis} {seances} le samedi")
        if mesures:
            message += f" Bilan sur l'ensemble du semestre : {' et '.join(mesures)}."
    if arret_demande():
        stats["raison_arret"] = "arret_utilisateur"
        stats["arrete_par_utilisateur"] = True
        avertissements.append(MESSAGE_SOLUTION_CONSERVEE)
        message += f" {MESSAGE_SOLUTION_CONSERVEE}"
    elif optimum_horaires:
        stats["raison_arret"] = "optimum_prouve"
        if not options.optimiser:
            pass
        elif optimal:
            message += " Impossible de faire mieux avec les préférences actuelles."
        elif solution.entorses or solution.entorses_regles:
            message += (
                " Les horaires ne peuvent pas être mieux notés ; les entorses qui restent viennent "
                "des salles."
            )
        else:
            message += (
                " Des horaires mieux notés existent, mais leurs salles coûteraient des entorses : "
                "ceux-ci sont gardés."
            )
    elif stagnation_s is not None:
        stats["raison_arret"] = "stagnation"
        stats["duree_sans_progres_s"] = round(stagnation_s, 1)
        if options.optimiser:
            message += (
                " L'amélioration s'est arrêtée d'elle-même : plus de progrès depuis "
                f"{stagnation_s:.0f} s."
            )
    elif amelioration_lancee:
        stats["raison_arret"] = "temps_limite"
        if options.optimiser:
            message += (
                " L'amélioration s'est arrêtée au temps limite : relancer plus longtemps peut "
                "encore l'affiner."
            )
    elif temps_manquant:
        stats["raison_arret"] = "temps_manquant"
        if options.optimiser:
            message += (
                " Le temps a manqué pour l'améliorer : relancer avec plus de temps donnera un "
                "emploi du temps plus confortable."
            )
    else:
        stats["raison_arret"] = "premiere_solution"
    if options.contraintes_desactivees:
        libelles = sorted(CATALOGUE[code_].libelle for code_ in options.contraintes_desactivees)
        message += " Règles ignorées à votre demande : " + ", ".join(libelles) + "."
    return conclure(StatutSolveur.SOLUTION, message, placements)


def _cumul(*comptes: dict) -> dict:
    """Somme de plusieurs décomptes d'entorses (par contrainte ou par règle), zéros omis."""
    total: dict = {}
    for compte in comptes:
        for cle, nombre in compte.items():
            if nombre:
                total[cle] = total.get(cle, 0) + nombre
    return dict(sorted(total.items(), key=lambda item: (isinstance(item[0], int), str(item[0]))))


def _texte_entorses(entorses: dict) -> str:
    """Complément de journal : « , 2 entorse(s) : PAUSE 2, règle 7 1 » ou rien."""
    if not entorses:
        return ""
    detail = ", ".join(
        f"{cle.value if isinstance(cle, CodeContrainte) else f'règle {cle}'} {nombre}"
        for cle, nombre in entorses.items()
    )
    return f", {sum(entorses.values())} entorse(s) : {detail}"


def _phrase_entorses_regles(entorses: dict[int, int], regles: Regles) -> str:
    """Phrase du message pour les entorses aux règles personnalisées.

    « 2 entorses à la règle R007 (Le groupe A1 ne travaille pas le samedi),
    1 entorse à la règle R009 (...). »
    """
    morceaux = []
    for regle_id, nombre in entorses.items():
        regle = regles.regle(regle_id)
        morceaux.append(
            f"{nombre} entorse{'s' if nombre > 1 else ''} à la règle {regle.code} ({regle.phrase})"
        )
    return ", ".join(morceaux) + "."


def _phrase_entorses(entorses: dict[CodeContrainte, int], niveaux: dict) -> str:
    """Phrase du message pour les entorses retenues, par niveau de règle.

    « 2 entorses à des règles importantes (pause entre deux séances : 2). 1
    entorse à une règle facultative (jours de cours : 1). »
    """
    phrases = []
    for niveau, adjectif in ((Niveau.IMPORTANT, "importante"), (Niveau.FACULTATIF, "facultative")):
        du_niveau = {c: n for c, n in entorses.items() if niveaux.get(c) is niveau}
        if not du_niveau:
            continue
        total = sum(du_niveau.values())
        details = ", ".join(
            f"{CATALOGUE[code].libelle.lower()} : {nombre}" for code, nombre in du_niveau.items()
        )
        regles = f"des règles {adjectif}s" if total > 1 else f"une règle {adjectif}"
        phrases.append(f"{total} entorse{'s' if total > 1 else ''} à {regles} ({details}).")
    return " ".join(phrases)


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
    *,
    stagnation: bool = False,
) -> None:
    """Fil de surveillance : relaie la demande d'arrêt et journalise la progression.

    Avec `stagnation`, il arrête aussi la recherche quand aucune solution
    n'est venue depuis `delai_de_stagnation` (le compte part de la première
    solution de cette recherche, pas du chargement du modèle).
    """
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
        if stagnation and suivi.dernier_progres is not None:
            sans_progres = ecoule - suivi.dernier_progres
            if suivi.stagnation_s is not None or sans_progres >= delai_de_stagnation(ecoule):
                if suivi.stagnation_s is None:
                    suivi.stagnation_s = sans_progres
                    journal_calcul.info(
                        "Plus de progrès depuis %.0f s : l'amélioration s'arrête d'elle-même "
                        "(objectif %s).",
                        sans_progres,
                        suivi.objectif,
                    )
                solveur.stop_search()
                continue
        if ecoule >= prochain_point:
            prochain_point += INTERVALLE_SUIVI_S
            if suivi.objectif is not None:
                etat = f"{suivi.nb_solutions} amélioration(s), objectif {suivi.objectif}"
            elif suivi.nb_solutions:
                etat = "horaires trouvés"
            else:
                etat = "aucune solution pour l'instant"
            journal_calcul.info("Recherche en cours depuis %.0f s : %s", ecoule, etat)
