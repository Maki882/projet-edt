"""Le moteur dans son ensemble : issues, semaines actives, reproductibilité, objectif, arrêt."""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import threading
import time

import pytest
from tests.solveur.fabrique import (
    C1,
    C2,
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
from edt.erreurs import ErreurDonneesInvalides
from edt.solveur import ResultatSolveur, StatutSolveur, resoudre
from edt.solveur.contrat import NB_WORKERS_MAX, TEMPS_LIMITE_MAX_S
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


def test_jamais_plus_de_fils_que_de_coeurs():
    """Un réglage démesuré (le formulaire des paramètres est public) est ramené aux cœurs."""
    instantane, _ = generer(40, graine=3)
    resultat = resoudre(instantane, options(nb_workers=64))
    verifier_solution(instantane, resultat)
    assert resultat.statistiques["nb_workers"] == min(64, os.cpu_count())
    defaut = resoudre(instantane, options(nb_workers=0))
    assert defaut.statistiques["nb_workers"] == min(2, os.cpu_count())


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
    assert stats["raison_arret"] == "premiere_solution"  # pas d'amélioration demandée
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
    assert stats["raison_arret"] == "temps_limite"
    assert "L'amélioration s'est arrêtée au temps limite" in optimise.message
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
    assert resultat.statistiques["raison_arret"] == "temps_manquant"
    assert "Le temps a manqué pour l'améliorer" in resultat.message


def test_recherche_rendue_en_retard_l_amelioration_n_est_pas_perdue(monkeypatch):
    """CP-SAT rend la main après le temps limite : les horaires améliorés gardent leurs salles."""
    from edt.solveur import moteur

    # Réserve négative : la recherche se termine une seconde et demie APRÈS le temps limite,
    # comme il arrive sur une machine chargée avec un gros modèle. Le temps limite laisse une
    # marge large à la première solution (quelques secondes, même machine chargée), et
    # l'instance est assez grande pour que l'amélioration ne prouve pas l'optimum avant la fin.
    monkeypatch.setattr(moteur, "RESERVE_MAX_S", -1.5)
    instantane, _ = generer(120, graine=4)
    resultat = resoudre(instantane, options(optimiser=True, temps_limite_s=6, nb_workers=2))
    verifier_solution(instantane, resultat)
    stats = resultat.statistiques
    assert stats["duree_s"] > 6  # le temps limite était bien dépassé au retour de la recherche
    assert stats["raison_arret"] == "temps_limite"
    assert stats["objectif"] < stats["objectif_premiere_solution"]
    indicateurs = calculer_indicateurs(instantane, resultat.placements)
    assert stats["objectif"] == indicateurs.valeur_objectif


def test_objectif_prefere_eviter_le_trou_et_le_samedi():
    """Deux séances d'un groupe sur une journée : l'optimum les met côte à côte, hors créneaux extrêmes."""
    f = Fabrique(creneaux=grille(jours=[LUNDI], samedi=True))
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()], nombre=2, samedi_autorise=True)
    instantane = f.instantane()
    resultat = resoudre(instantane, options(optimiser=True))
    places = verifier_solution(instantane, resultat)
    assert resultat.statistiques["optimal"] is True
    assert resultat.statistiques["raison_arret"] == "optimum_prouve"
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


def _arreter_pendant_l_amelioration(guetter_journal):
    """Lance un calcul optimisé de 300 séances, attend que l'amélioration ait commencé, demande
    l'arrêt ; rend `(instantané, résultat, délai en secondes entre la demande et le retour)`.

    Toute attente est une attente d'événement du moteur (première solution annoncée, départ de
    l'amélioration), jamais un délai fixe : le déroulement ne dépend pas de la charge de la
    machine, seule la durée change.
    """
    instantane, _ = generer(300, graine=1)
    solution = guetter_journal("Emploi du temps valide trouvé")
    amelioration = guetter_journal("Amélioration : départ à l'objectif")
    arret = threading.Event()
    resultats: list[ResultatSolveur] = []
    calcul = threading.Thread(
        target=lambda: resultats.append(
            resoudre(instantane, options(optimiser=True, temps_limite_s=120, nb_workers=2), arret)
        )
    )
    calcul.start()
    assert solution.wait(90), "aucune solution valide en 90 s"
    assert amelioration.wait(30), "l'amélioration n'a pas commencé en 30 s"
    assert calcul.is_alive(), "le calcul devait encore améliorer la solution"
    demande = time.perf_counter()
    arret.set()
    calcul.join(timeout=60)
    delai = time.perf_counter() - demande
    assert not calcul.is_alive(), "le moteur n'a pas rendu la main en 60 s"
    (resultat,) = resultats
    return instantane, resultat, delai


def test_arret_pendant_l_amelioration_la_solution_trouvee_est_conservee(guetter_journal):
    """Arrêt demandé alors qu'une solution existe : elle est rendue, pas perdue.

    Borne large sur le délai de retour : l'arrêt est suivi d'effet bien avant le temps limite
    (120 s), quelle que soit la charge de la machine ; la réactivité fine est mesurée à part
    (`test_arret_pendant_l_amelioration_repond_en_moins_de_deux_secondes`, `perf`).
    """
    instantane, resultat, delai = _arreter_pendant_l_amelioration(guetter_journal)
    assert delai < 60
    verifier_solution(instantane, resultat)
    assert resultat.statistiques["arrete_par_utilisateur"] is True
    assert resultat.statistiques["raison_arret"] == "arret_utilisateur"
    conservee = "Calcul arrêté à votre demande : la meilleure solution trouvée a été conservée."
    assert conservee in resultat.avertissements and conservee in resultat.message


@pytest.mark.perf
def test_arret_pendant_l_amelioration_repond_en_moins_de_deux_secondes(guetter_journal):
    """Machine au repos : le moteur rend la main moins de deux secondes après la demande d'arrêt
    (engagement de `resoudre`, voir docs/solveur.md « Réglages et suivi »)."""
    _, _, delai = _arreter_pendant_l_amelioration(guetter_journal)
    assert delai < 2


def test_l_amelioration_s_arrete_d_elle_meme_quand_elle_ne_progresse_plus(monkeypatch):
    """Plus de progrès : le calcul rend la main bien avant le temps limite, et le dit."""
    from edt.solveur import moteur

    monkeypatch.setattr(moteur, "STAGNATION_MIN_S", 1.0)  # 10 s en vrai : trop long pour un test
    instantane, _ = generer(60, graine=4)
    debut = time.perf_counter()
    resultat = resoudre(
        instantane,
        options(optimiser=True, temps_limite_s=120, nb_workers=2, arret_sur_stagnation=True),
    )
    duree = time.perf_counter() - debut
    verifier_solution(instantane, resultat)
    stats = resultat.statistiques
    assert stats["raison_arret"] == "stagnation", resultat.message
    assert duree < 60, f"arrêt après {duree:.1f} s pour un temps limite de 120 s"
    assert stats["duree_sans_progres_s"] >= 1.0
    assert stats["optimal"] is False
    assert stats["objectif"] <= stats["objectif_premiere_solution"]
    # Le message distingue cet arrêt du temps limite et de l'optimum prouvé, en clair.
    assert re.search(
        r"L'amélioration s'est arrêtée d'elle-même : plus de progrès depuis \d+ s\.",
        resultat.message,
    )
    assert "temps limite" not in resultat.message
    assert "On ne peut pas faire mieux" not in resultat.message


class _FauxSolveur:
    """Ce que le fil de surveillance demande à CP-SAT : arrêter la recherche."""

    def __init__(self) -> None:
        self.arrets = 0

    def stop_search(self) -> None:
        self.arrets += 1


class _FauxSuivi:
    """Une recherche qui a trouvé une solution au lancement, puis plus rien."""

    nb_solutions = 1
    objectif = 100
    dernier_progres: float | None = 0.0
    stagnation_s: float | None = None


def _surveiller_une_seconde(monkeypatch, **reglages) -> tuple[_FauxSolveur, _FauxSuivi]:
    from edt.solveur import moteur

    monkeypatch.setattr(moteur, "STAGNATION_MIN_S", 0.3)
    solveur, suivi, fini = _FauxSolveur(), _FauxSuivi(), threading.Event()
    fil = threading.Thread(
        target=moteur._surveiller,
        args=(solveur, None, fini, logging.getLogger("edt.calcul"), time.perf_counter(), suivi),
        kwargs=reglages,
    )
    fil.start()
    time.sleep(1.0)
    fini.set()
    fil.join(timeout=5)
    assert not fil.is_alive()
    return solveur, suivi


def test_regle_de_stagnation(monkeypatch):
    """Patience : 10 s au moins, et un tiers du temps déjà écoulé depuis le lancement."""
    from edt.solveur import moteur

    delais = [moteur.delai_de_stagnation(t) for t in (5, 30, 90, 300)]
    assert delais == pytest.approx([10, 10, 30, 100])

    # Le fil de surveillance arrête la recherche restée sans progrès plus longtemps que cela...
    solveur, suivi = _surveiller_une_seconde(monkeypatch, stagnation=True)
    assert solveur.arrets >= 1
    assert suivi.stagnation_s is not None and 0.3 <= suivi.stagnation_s < 1.0

    # ... mais pas si le critère est désactivé (`OptionsSolveur.arret_sur_stagnation` à faux),
    solveur, suivi = _surveiller_une_seconde(monkeypatch, stagnation=False)
    assert solveur.arrets == 0 and suivi.stagnation_s is None

    # ni tant que cette recherche n'a pas trouvé sa première solution (chargement du modèle).
    monkeypatch.setattr(_FauxSuivi, "dernier_progres", None)
    solveur, suivi = _surveiller_une_seconde(monkeypatch, stagnation=True)
    assert solveur.arrets == 0 and suivi.stagnation_s is None


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


# --------------------------------------------------------------------------- #
# Cas limites (banque de tests : `pytest -m cas_limite`)
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    "champs",
    [
        {"temps_limite_s": float("nan")},
        {"temps_limite_s": float("inf")},
        {"temps_limite_s": 1e9},
        {"temps_limite_s": 0},
        {"temps_limite_s": -3},
        {"temps_limite_s": "60"},
        {"temps_limite_s": True},
        {"nb_workers": -1},
        {"nb_workers": 1000},
        {"nb_workers": 2.5},
        {"graine": 1.5},
    ],
    ids=str,
)
def test_options_invalides_refusees(champs):
    """Cas : options du solveur hors bornes : temps limite NaN, infini, démesuré (1e9 s), nul,
    négatif, en texte ou booléen ; nombre de fils négatif, mille fils, fils ou graine non entiers.

    Risque : « Aucun emploi du temps trouvé en nan s », calcul sans fin que l'échéance d'abandon
    ne sait pas calculer (OverflowError), mémoire du serveur épuisée par les fils.
    """
    with pytest.raises(ErreurDonneesInvalides):
        options(**champs)


@pytest.mark.cas_limite
def test_options_aux_bornes_acceptees():
    """Cas : options aux bornes mêmes (24 h, 64 fils, 0 fil, 0,5 s) et graines négative ou 2^40.

    Risque : des bornes trop serrées refuseraient des réglages légitimes ; une graine hors
    [0, 2^31[ est ramenée dans les bornes de CP-SAT par le moteur, pas refusée.
    """
    for champs in (
        {"temps_limite_s": TEMPS_LIMITE_MAX_S},
        {"temps_limite_s": 0.5},
        {"nb_workers": NB_WORKERS_MAX},
        {"nb_workers": 0},
        {"graine": -5},
        {"graine": 2**40},
    ):
        assert options(**champs).vers_dict()


@pytest.mark.cas_limite
def test_toutes_les_seances_verrouillees():
    """Cas : chaque séance porte un verrou avec sa salle : le modèle n'a ni variable ni choix.

    Risque : plantage sur un modèle vide (objectif sans terme, statistiques) ou solution non
    rendue, en mode faisable comme en mode optimisé.
    """
    f = Fabrique(creneaux=grille(jours=[LUNDI], samedi=False))
    f.salle("N101")
    f.besoin([f.groupe("A1")], [f.enseignant()], nombre=2)
    f.verrou("1-1", LUNDI, C1, 1)
    f.verrou("1-2", LUNDI, C2, 1)
    instantane = f.instantane()
    for reglages in ({}, {"optimiser": True}):
        resultat = resoudre(instantane, options(**reglages))
        places = verifier_solution(instantane, resultat)
        assert [(p.jour, p.debut, p.salle_id) for p in (places["1-1"], places["1-2"])] == [
            (LUNDI, C1, 1),
            (LUNDI, C2, 1),
        ]
        assert resultat.statistiques["nb_variables"] == 0
        assert resultat.statistiques["raison_arret"] == "premiere_solution"


@pytest.mark.cas_limite
def test_arret_pendant_la_premiere_recherche_rend_la_main_vite(guetter_journal):
    """Cas : instantané de 300 séances, arrêt demandé dès que le modèle est construit, pendant la
    première recherche, avant toute solution (la première solution demande ici plus d'une
    minute).

    Risque : le moteur ignore l'arrêt tant qu'aucune solution n'existe, ou met des secondes à
    rendre la main : bouton « Arrêter » sans effet avant la première solution.
    """
    instantane, _ = generer(300, graine=1)
    modele_construit = guetter_journal("Modèle des horaires construit")
    arret = threading.Event()
    resultats = []
    fil = threading.Thread(
        target=lambda: resultats.append(resoudre(instantane, options(temps_limite_s=120), arret))
    )
    fil.start()
    assert modele_construit.wait(60), "le modèle n'a pas été construit en 60 s"
    demande = time.perf_counter()
    arret.set()
    fil.join(30)
    assert not fil.is_alive(), "le moteur n'a pas rendu la main en 30 s"
    # Borne large : l'arrêt est suivi d'effet bien avant le temps limite, machine chargée ou non.
    assert time.perf_counter() - demande < 30
    (resultat,) = resultats
    assert resultat.statut is StatutSolveur.ARRETE and resultat.placements == ()
    assert "avant qu'un emploi du temps soit trouvé" in resultat.message


@pytest.mark.cas_limite
def test_parametres_extremes_restent_valides():
    """Cas : pause de 120 min, trajet de 100 000 min entre deux campus, poids 1000, graines -5 et
    2^40, mode optimisé.

    Risque : coefficients CP-SAT qui débordent (MODEL_INVALID), graine refusée, codage
    jour × pas des débuts faux quand le pas dépasse 100 000.
    """
    f = Fabrique(pause_min=120, poids_trous=1000, poids_samedi=1000)
    nord, sud = f.campus("N"), f.campus("S")
    f.trajet(nord, sud, 100_000)
    n101, s101 = f.salle("N101", campus=nord), f.salle("S101", campus=sud)
    classe, prof = f.groupe("A1"), f.enseignant()
    f.besoin([classe], [prof], salle_imposee_id=n101)
    f.besoin([classe], [prof], salle_imposee_id=s101)
    instantane = f.instantane()
    for graine in (-5, 2**40):
        resultat = resoudre(instantane, options(optimiser=True, graine=graine))
        places = verifier_solution(instantane, resultat)
        assert resultat.statistiques["graine"] == graine
        assert resultat.statistiques["statut_cp_sat"] != "MODEL_INVALID"
        # 100 000 min de trajet : jamais le même jour.
        assert places["1-1"].jour != places["2-1"].jour
