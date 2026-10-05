"""Le moteur dans son ensemble : issues, semaines actives, reproductibilité, objectif, arrêt."""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time

from tests.solveur.fabrique import (
    C1,
    LUNDI,
    MARDI,
    OPTIONS,
    RENTREE,
    Fabrique,
    H,
    grille,
    options,
    verifier_solution,
)
from tests.solveur.generateur import generer
from tests.solveur.test_contraintes import MATINEE, UN_CRENEAU

from edt.domaine.enumerations import ModeVolume
from edt.solveur import ResultatSolveur, StatutSolveur, resoudre
from edt.verification import calculer_indicateurs


def _deux_moities(fin_premiere: int, debut_seconde: int):
    """Deux matières courtes du même groupe et du même enseignant, une seule salle, un seul créneau.

    La première occupe les semaines 1 à `fin_premiere`, la seconde les
    semaines `debut_seconde` à 4.
    """
    f = Fabrique(nb_semaines=4, creneaux=UN_CRENEAU)
    f.salle("N101")
    classe, martin = f.groupe("A1"), f.enseignant("Mme Martin")
    for premiere, derniere in ((1, fin_premiere), (debut_seconde, 4)):
        f.besoin(
            [classe],
            [martin],
            nombre=derniere - premiere + 1,
            mode_volume=ModeVolume.TOTAL,
            max_par_semaine=1,
            date_debut=RENTREE + dt.timedelta(days=7 * (premiere - 1)),
            date_fin=RENTREE + dt.timedelta(days=7 * derniere - 3),
        )
    return f.instantane()


def test_semaines_disjointes_partagent_creneau_salle_enseignant_et_groupe():
    instantane = _deux_moities(fin_premiere=2, debut_seconde=3)
    assert [sorted(s.semaines) for s in instantane.seances] == [[1, 2], [3, 4]]
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    a, b = places["1-1"], places["2-1"]
    assert (a.jour, a.debut, a.salle_id) == (b.jour, b.debut, b.salle_id) == (LUNDI, C1, 1)


def test_semaines_qui_se_croisent_ne_partagent_rien():
    instantane = _deux_moities(fin_premiere=3, debut_seconde=3)  # la semaine 3 est commune
    resultat = resoudre(instantane, OPTIONS)
    assert resultat.statut is StatutSolveur.INFAISABLE
    assert resultat.placements == ()


def test_aucune_seance():
    f = Fabrique()
    f.salle()
    resultat = resoudre(f.instantane(), OPTIONS)
    assert resultat.statut is StatutSolveur.SOLUTION
    assert resultat.placements == ()


def test_statistiques_et_journal(caplog):
    instantane, _ = generer(40, graine=3)
    with caplog.at_level(logging.INFO, logger="edt.solveur"):
        resultat = resoudre(instantane, OPTIONS)
    verifier_solution(instantane, resultat)
    stats = resultat.statistiques
    assert stats["moteur"] == "cp_sat"
    assert stats["nb_seances"] == len(instantane.seances)
    assert stats["nb_variables"] > 0 and stats["nb_contraintes"] > 0
    assert stats["statut_cp_sat"] in ("OPTIMAL", "FEASIBLE")
    assert {"duree_s", "duree_construction_s", "duree_resolution_s", "nb_workers"} <= set(stats)
    journal = caplog.text
    assert "Domaines : débuts par séance min" in journal
    assert "Modèle des horaires construit" in journal
    assert "Fin du calcul : solution" in journal


def test_mode_reproductible():
    instantane, _ = generer(80, graine=5)
    reglages = options(graine=11, nb_workers=1)
    premier = resoudre(instantane, reglages)
    second = resoudre(instantane, reglages)
    verifier_solution(instantane, premier)
    assert premier.placements == second.placements


def test_verrous_et_indices_du_generateur():
    instantane, cache = generer(80, graine=2, part_verrouillee=0.4, indices=True)
    verrouillees = [seance for seance in instantane.seances if seance.verrou is not None]
    assert verrouillees and all(seance.indice is not None for seance in instantane.seances)
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    for seance in verrouillees:
        assert places[seance.id] == cache[seance.id]


def test_mode_optimise_fait_baisser_l_objectif_du_verificateur():
    instantane, _ = generer(60, graine=4)
    faisable = resoudre(instantane, OPTIONS)
    optimise = resoudre(instantane, options(optimiser=True, temps_limite_s=8, nb_workers=2))
    verifier_solution(instantane, faisable)
    verifier_solution(instantane, optimise)
    avant = calculer_indicateurs(instantane, faisable.placements)
    apres = calculer_indicateurs(instantane, optimise.placements)
    assert apres.valeur_objectif < avant.valeur_objectif
    assert apres.trous_creneaux <= avant.trous_creneaux
    assert "objectif" not in faisable.statistiques
    stats = optimise.statistiques
    assert stats["objectif"] >= stats["borne_objectif"]
    # L'amélioration part de la première solution valide et ne fait jamais moins bien.
    assert stats["objectif"] <= stats["objectif_premiere_solution"]
    assert stats["objectif"] == apres.valeur_objectif
    assert (stats["trous"], stats["samedis"]) == (apres.trous_creneaux, apres.seances_samedi)
    assert 0 < stats["duree_premiere_solution_s"] <= stats["duree_s"]
    # Le message parle au responsable de planning : des trous et des samedis, pas de pénalité.
    assert (
        f"Bilan sur l'ensemble du semestre : {apres.trous_creneaux} créneau(x) libre(s) entre "
        f"deux cours dans les journées des groupes et {apres.seances_samedi} séance(s) le samedi."
    ) in optimise.message
    assert "pénalité" not in optimise.message.lower()
    assert "objectif" not in optimise.message.lower()


def test_l_amelioration_part_de_la_solution_valide():
    """La solution valide, donnée comme point de départ complet, est la première du second calcul."""
    from ortools.sat.python import cp_model

    from edt.solveur.modele import ModeleHoraires
    from edt.solveur.preparation import Preparation

    instantane, _ = generer(60, graine=4)
    modele = ModeleHoraires(Preparation(instantane), OPTIONS)
    solveur = cp_model.CpSolver()
    solveur.parameters.num_workers = 1
    assert solveur.solve(modele.cp) in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    choix = modele.choix(solveur.boolean_value)
    assert modele.poser_objectif()
    depart = modele.bilan(choix)
    modele.partir_de(list(solveur.response_proto.solution))

    class Premiere(cp_model.CpSolverSolutionCallback):
        """Retient l'objectif de la première solution, puis arrête la recherche."""

        def __init__(self) -> None:
            super().__init__()
            self.objectif: int | None = None

        def on_solution_callback(self) -> None:
            if self.objectif is None:
                self.objectif = round(self.objective_value)
            self.stop_search()

    suite = cp_model.CpSolver()
    suite.parameters.num_workers = 1
    suite.parameters.log_search_progress = True
    suite.parameters.log_to_stdout = False
    lignes: list[str] = []
    suite.log_callback = lignes.append
    premiere = Premiere()
    suite.solve(modele.cp, premiere)
    assert any("solution hint is complete and is feasible" in ligne for ligne in lignes)
    assert premiere.objectif is not None and premiere.objectif <= depart.objectif
    assert depart.objectif > 0 and depart.trous is not None and depart.samedis is not None


def test_mode_optimise_rend_la_solution_valide_quand_le_temps_manque(monkeypatch):
    """Temps limite atteint juste après la première solution : elle est rendue, pas perdue."""
    from edt.solveur import moteur

    instantane, _ = generer(60, graine=4)
    monkeypatch.setattr(moteur, "TEMPS_MIN_AMELIORATION_S", 10_000.0)  # jamais assez de temps
    resultat = resoudre(instantane, options(optimiser=True))
    verifier_solution(instantane, resultat)
    assert "objectif" not in resultat.statistiques
    assert "Le temps a manqué pour l'améliorer" in resultat.message


def test_objectif_prefere_eviter_le_trou_et_le_samedi():
    """Deux séances d'un groupe sur une journée : l'optimum les met côte à côte, hors créneaux extrêmes."""
    f = Fabrique(creneaux=grille(jours=[LUNDI], samedi=True))
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()], nombre=2, samedi_autorise=True)
    instantane = f.instantane()
    resultat = resoudre(instantane, options(optimiser=True))
    places = verifier_solution(instantane, resultat)
    assert resultat.statistiques["optimal"] is True
    indicateurs = calculer_indicateurs(instantane, resultat.placements)
    assert indicateurs.trous_creneaux == 0 and indicateurs.seances_samedi == 0
    assert {p.jour for p in places.values()} == {LUNDI}
    assert resultat.statistiques["objectif"] == indicateurs.valeur_objectif


def test_temps_depasse_sans_preuve():
    instantane, _ = generer(300, graine=1)
    debut = time.perf_counter()
    resultat = resoudre(instantane, options(temps_limite_s=0.05))
    assert time.perf_counter() - debut < 5
    assert resultat.statut is StatutSolveur.TEMPS_DEPASSE
    assert resultat.placements == ()
    assert "pas une preuve" in resultat.message


def test_arret_demande_avant_le_calcul():
    instantane, _ = generer(40, graine=1)
    arret = threading.Event()
    arret.set()
    resultat = resoudre(instantane, OPTIONS, arret)
    assert resultat.statut is StatutSolveur.ARRETE
    assert resultat.placements == ()
    assert "arrêté" in resultat.message


def test_arret_pendant_l_amelioration_la_solution_trouvee_est_conservee():
    """Arrêt demandé alors qu'une solution existe : rendue en moins de deux secondes, pas perdue."""
    instantane, _ = generer(300, graine=1)
    arret = threading.Event()
    resultats: list[ResultatSolveur] = []
    calcul = threading.Thread(
        target=lambda: resultats.append(
            resoudre(instantane, options(optimiser=True, temps_limite_s=120, nb_workers=2), arret)
        )
    )
    calcul.start()
    time.sleep(8)  # la première solution est trouvée en deux secondes ; l'amélioration tourne
    assert calcul.is_alive(), "le calcul devait encore améliorer la solution"
    demande = time.perf_counter()
    arret.set()
    calcul.join(timeout=10)
    assert not calcul.is_alive()
    assert time.perf_counter() - demande < 2
    resultat = resultats[0]
    verifier_solution(instantane, resultat)
    assert resultat.statistiques["arrete_par_utilisateur"] is True
    conservee = "Calcul arrêté à votre demande : la meilleure solution trouvée a été conservée."
    assert conservee in resultat.avertissements and conservee in resultat.message


def test_graines_differentes_solutions_differentes_avec_un_seul_fil():
    """Même graine : même solution. Autre graine : autre solution, même sur un petit problème."""
    f = Fabrique()
    for _ in range(3):
        f.salle()
    for numero in range(4):
        f.besoin([f.groupe(f"A{numero}")], [f.enseignant()], nombre=3)
    instantane = f.instantane()
    solutions = {}
    for graine in (1, 2, 3, 4, 5, 6):
        resultat = resoudre(instantane, options(graine=graine, nb_workers=1))
        verifier_solution(instantane, resultat)
        solutions[graine] = resultat.placements
    assert len(set(solutions.values())) >= 5
    assert resoudre(instantane, options(graine=3, nb_workers=1)).placements == solutions[3]


def _salles_en_quinconce(jours):
    """Une séance de 4 h et deux de 2 h dont les salles imposées se croisent.

    X (4 h) accepte les salles a et b ; P n'accepte que a, Q que b. À chaque
    instant deux salles suffisent pour deux séances, mais si P et Q occupent
    chacune une moitié de la matinée de X, X n'a aucune salle pour ses 4 h :
    la phase des salles le détecte et écarte ces horaires.
    """
    f = Fabrique(creneaux=grille(jours=jours, samedi=False)[: 2 * len(jours)])
    a, b = f.salle("a"), f.salle("b")
    f.besoin([f.groupe("X")], [f.enseignant()], duree=4 * H)
    f.besoin([f.groupe("P")], [f.enseignant()], salle_imposee_id=a)
    f.besoin([f.groupe("Q")], [f.enseignant()], salle_imposee_id=b)
    return f.instantane()


def test_conflit_de_salles_prouve_l_impossibilite():
    instantane = _salles_en_quinconce([LUNDI])
    assert [c.debut for c in instantane.creneaux] == [c.debut for c in MATINEE]
    resultat = resoudre(instantane, OPTIONS)
    assert resultat.statut is StatutSolveur.INFAISABLE
    assert resultat.statistiques["nb_coupes"] >= 1  # la phase des salles a écarté des horaires


def test_conflit_de_salles_contourne_quand_une_autre_matinee_existe():
    instantane = _salles_en_quinconce([LUNDI, MARDI])
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert places["1-1"].fin - places["1-1"].debut == 4 * H
