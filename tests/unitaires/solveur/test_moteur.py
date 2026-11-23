"""Le moteur dans son ensemble : issues, semaines actives, reproductibilité, objectif, arrêt."""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import threading
import time

import pytest
from tests.outils.solveur.aides import MATINEE, UN_CRENEAU
from tests.outils.solveur.fabrique import (
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
from tests.outils.solveur.generateur import generer

from edt.domaine.contraintes import CodeContrainte, niveaux_par_defaut
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
    instantane, _ = generer(40, graine=5)
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
        f"Bilan sur l'ensemble du semestre : {apres.trous_creneaux} "
        f"{'créneau libre' if apres.trous_creneaux <= 1 else 'créneaux libres'} entre deux cours "
        f"dans les journées des groupes et {apres.seances_samedi} "
        f"{'séance' if apres.seances_samedi <= 1 else 'séances'} le samedi."
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
    # comme il arrive sur une machine chargée avec un gros modèle. La première solution de
    # cette instance arrive en une fraction de seconde, même machine chargée : il reste du
    # temps pour améliorer, sans que l'optimum soit prouvé avant la fin.
    monkeypatch.setattr(moteur, "RESERVE_MAX_S", -1.5)
    instantane, _ = generer(60, graine=4)
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
    # Garde-fou contre une construction qui exploserait (quadratique) : la construction seule
    # prend jusqu'à 3 s sur une machine chargée, d'où une borne large.
    assert time.perf_counter() - debut < 20
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
    """Lance un calcul optimisé de 120 séances, attend que l'amélioration ait commencé, demande
    l'arrêt ; rend `(instantané, résultat, délai en secondes entre la demande et le retour)`.

    L'amélioration est encore en cours au moment de la demande : elle ne s'arrête d'elle-même
    qu'après `STAGNATION_MIN_S` (10 s) sans progrès, et ne prouve pas l'optimum à cette taille.

    Toute attente est une attente d'événement du moteur (première solution annoncée, départ de
    l'amélioration), jamais un délai fixe : le déroulement ne dépend pas de la charge de la
    machine, seule la durée change.
    """
    instantane, _ = generer(120, graine=1)
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
    assert "Impossible de faire mieux" not in resultat.message


class _FauxSolveur:
    """Ce que le fil de surveillance demande à CP-SAT : arrêter la recherche."""

    def __init__(self) -> None:
        self.arrets = 0
        self.arrete = threading.Event()

    def stop_search(self) -> None:
        self.arrets += 1
        self.arrete.set()


class _FauxSuivi:
    """Une recherche qui a trouvé une solution au lancement, puis plus rien."""

    nb_solutions = 1
    objectif = 100
    dernier_progres: float | None = 0.0
    stagnation_s: float | None = None


def _surveiller(
    monkeypatch, attente_s: float, **reglages
) -> tuple[_FauxSolveur, _FauxSuivi, float]:
    """Fait tourner le fil de surveillance jusqu'à sa première demande d'arrêt, au plus
    `attente_s` secondes ; rend le faux solveur, le faux suivi et le temps écoulé."""
    from edt.solveur import moteur

    monkeypatch.setattr(moteur, "STAGNATION_MIN_S", 0.3)
    solveur, suivi, fini = _FauxSolveur(), _FauxSuivi(), threading.Event()
    debut = time.perf_counter()
    fil = threading.Thread(
        target=moteur._surveiller,
        args=(solveur, None, fini, logging.getLogger("edt.calcul"), debut, suivi),
        kwargs=reglages,
    )
    fil.start()
    solveur.arrete.wait(attente_s)
    ecoule = time.perf_counter() - debut
    fini.set()
    fil.join(timeout=5)
    assert not fil.is_alive()
    return solveur, suivi, ecoule


def test_regle_de_stagnation(monkeypatch):
    """Patience : 10 s au moins, et un tiers du temps déjà écoulé depuis le lancement."""
    from edt.solveur import moteur

    delais = [moteur.delai_de_stagnation(t) for t in (5, 30, 90, 300)]
    assert delais == pytest.approx([10, 10, 30, 100])

    # Le fil de surveillance arrête la recherche restée sans progrès plus longtemps que cela
    # (la dernière solution date du lancement : `_FauxSuivi.dernier_progres` vaut 0)...
    solveur, suivi, ecoule = _surveiller(monkeypatch, 10, stagnation=True)
    assert solveur.arrets >= 1
    assert suivi.stagnation_s is not None and 0.3 <= suivi.stagnation_s <= ecoule

    # ... mais pas si le critère est désactivé (`OptionsSolveur.arret_sur_stagnation` à faux),
    # ce qu'une attente de deux fois le délai suffit à constater,
    solveur, suivi, _ = _surveiller(monkeypatch, 0.6, stagnation=False)
    assert solveur.arrets == 0 and suivi.stagnation_s is None

    # ni tant que cette recherche n'a pas trouvé sa première solution (chargement du modèle).
    monkeypatch.setattr(_FauxSuivi, "dernier_progres", None)
    solveur, suivi, _ = _surveiller(monkeypatch, 0.6, stagnation=True)
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
        {"nb_workers": True},
        {"graine": 1.5},
        {"graine": True},
    ],
    ids=str,
)
def test_options_invalides_refusees(champs):
    """Cas : options du solveur hors bornes : temps limite NaN, infini, démesuré (1e9 s), nul,
    négatif, en texte ou booléen ; nombre de fils négatif, mille fils, fils ou graine non entiers
    ou booléens.

    Risque : « Aucun emploi du temps trouvé en nan s », calcul sans fin que l'échéance d'abandon
    ne sait pas calculer (OverflowError), mémoire du serveur épuisée par les fils.
    """
    with pytest.raises(ErreurDonneesInvalides):
        options(**champs)


@pytest.mark.cas_limite
def test_options_aux_bornes_acceptees():
    """Cas : options aux bornes mêmes (24 h, 64 fils, 0 fil, 0,5 s) et graines négative ou 2^40.

    Risque : des bornes trop serrées refuseraient des réglages légitimes ; une graine hors
    [0, 2^31[ est gardée telle quelle (le moteur la ramène dans les bornes de CP-SAT, voir
    `test_parametres_extremes_restent_valides`).
    """
    for champs in (
        {"temps_limite_s": TEMPS_LIMITE_MAX_S},
        {"temps_limite_s": 0.5},
        {"nb_workers": NB_WORKERS_MAX},
        {"nb_workers": 0},
        {"graine": -5},
        {"graine": 2**40},
    ):
        ((cle, valeur),) = champs.items()
        assert getattr(options(**champs), cle) == valeur


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


def _arreter_pendant_la_premiere_recherche(guetter_journal) -> tuple[ResultatSolveur, float]:
    """Lance un calcul de 300 séances, demande l'arrêt dès que le modèle est construit ; rend
    `(résultat, délai en secondes entre la demande et le retour)`."""
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
    delai = time.perf_counter() - demande
    assert not fil.is_alive(), "le moteur n'a pas rendu la main en 30 s"
    (resultat,) = resultats
    return resultat, delai


@pytest.mark.cas_limite
def test_arret_pendant_la_premiere_recherche_est_suivi_d_effet(guetter_journal):
    """Cas : instantané de 300 séances, arrêt demandé dès que le modèle est construit, pendant la
    première recherche, avant toute solution (la première solution demande ici plus d'une
    minute).

    Risque : le moteur ignore l'arrêt tant qu'aucune solution n'existe : bouton « Arrêter » sans
    effet avant la première solution. Borne large sur le délai, machine chargée ou non ; la
    réactivité fine est mesurée à part
    (`test_arret_pendant_la_premiere_recherche_repond_en_moins_de_deux_secondes`, `perf`).
    """
    resultat, delai = _arreter_pendant_la_premiere_recherche(guetter_journal)
    assert delai < 30
    assert resultat.statut is StatutSolveur.ARRETE and resultat.placements == ()
    assert "avant qu'un emploi du temps soit trouvé" in resultat.message


@pytest.mark.perf
def test_arret_pendant_la_premiere_recherche_repond_en_moins_de_deux_secondes(guetter_journal):
    """Machine au repos : avant toute solution aussi, le moteur rend la main moins de deux
    secondes après la demande d'arrêt."""
    _, delai = _arreter_pendant_la_premiere_recherche(guetter_journal)
    assert delai < 2


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


def test_sur_premiere_solution_appele_une_fois_avec_la_duree_ecoulee():
    """Le rappel est appelé une seule fois, amélioration comprise, avec une durée positive."""
    instantane, _ = generer(40, graine=3)
    appels: list[float] = []
    resultat = resoudre(
        instantane, options(optimiser=True, temps_limite_s=5), sur_premiere_solution=appels.append
    )
    verifier_solution(instantane, resultat)
    assert len(appels) == 1
    assert 0 < appels[0] <= resultat.statistiques["duree_s"]


def test_sur_premiere_solution_pas_appele_sans_solution():
    """Horaires trouvés mais salles impossibles (impossibilité prouvée), ou calcul refusé avant
    la recherche : aucun emploi du temps complet, aucun appel."""
    appels: list[float] = []
    resultat = resoudre(_salles_en_quinconce([LUNDI]), OPTIONS, sur_premiere_solution=appels.append)
    assert resultat.statut is StatutSolveur.INFAISABLE
    assert resultat.statistiques["nb_coupes"] >= 1  # des horaires avaient été trouvés
    f = Fabrique(creneaux=UN_CRENEAU)
    f.salle("N101")
    f.besoin([f.groupe("A1")], [f.enseignant()], nombre=2)
    resultat = resoudre(f.instantane(), OPTIONS, sur_premiere_solution=appels.append)
    assert resultat.statut is StatutSolveur.INFAISABLE
    assert appels == []


def test_sur_premiere_solution_qui_echoue_n_empeche_pas_le_resultat(caplog):
    """Un rappel qui lève : erreur journalisée avec sa trace, solution rendue quand même."""
    instantane, _ = generer(40, graine=3)

    def rappel(duree: float) -> None:
        raise RuntimeError("interface indisponible")

    with caplog.at_level(logging.ERROR, logger="edt.solveur.moteur"):
        resultat = resoudre(instantane, OPTIONS, sur_premiere_solution=rappel)
    verifier_solution(instantane, resultat)
    assert resultat.statut is StatutSolveur.SOLUTION
    (erreur,) = [r for r in caplog.records if "rappel de première solution" in r.getMessage()]
    assert erreur.exc_info is not None and "interface indisponible" in str(erreur.exc_info[1])


@pytest.mark.cas_limite
def test_salles_interrompues_le_message_donne_la_duree_reelle(monkeypatch):
    """Cas : des horaires sont trouvés, puis l'attribution des salles s'interrompt sur son propre
    temps maximal sans rien prouver (machine très chargée), bien avant le temps limite de 300 s.

    Risque : le calcul s'arrête « pas trouvé » après une minute, mais son message affirme
    « Aucun emploi du temps n'a été trouvé en 300 s » : deux durées différentes à l'écran.
    """
    from edt.solveur import moteur
    from edt.solveur.salles import AffectationSalles

    monkeypatch.setattr(
        moteur, "affecter_salles", lambda *args, **kwargs: AffectationSalles(None, prouve=False)
    )
    instantane, _ = generer(20, graine=1)
    resultat = resoudre(instantane, options(temps_limite_s=300))
    assert resultat.statut is StatutSolveur.TEMPS_DEPASSE
    assert "300 s" not in resultat.message, resultat.message
    duree = resultat.statistiques["duree_s"]
    attendu = "moins d'une seconde" if duree < 1 else f"en {round(duree)} s"
    assert attendu in resultat.message, resultat.message
    assert "salles" in resultat.message


def _construction_longue():
    """Un groupe à 32 séances sur deux campus, horaires de la grille ignorés.

    Sans la contrainte des horaires, chaque séance peut commencer à toute heure du jour : l'étape
    des pauses et des trajets examine des milliers de couples de débuts. Sur le moteur qui ne
    s'interrompait qu'entre deux étapes, la construction seule dure une quinzaine de secondes.
    """
    f = Fabrique()
    nord, sud = f.campus(), f.campus()
    f.trajet(nord, sud, 60)
    classe = f.groupe("A1")
    for campus in (nord, nord, sud):
        f.salle(campus=campus)
    f.besoin([classe], [f.enseignant()], nombre=30)
    f.besoin([classe], [f.enseignant()], nombre=2)
    sans_horaires = {
        code: niveau
        for code, niveau in niveaux_par_defaut().items()
        if code is not CodeContrainte.HORAIRES
    }
    return f.instantane(), sans_horaires


MARGE_INTERRUPTION_S = 3.0
"""Temps toléré entre l'échéance (temps limite ou arrêt) et le retour du moteur."""


@pytest.mark.cas_limite
def test_temps_limite_tenu_pendant_une_construction_longue():
    """Cas : temps limite d'1 s sur un problème dont la construction du modèle dure bien plus.

    Risque : le moteur ne regarde l'horloge qu'entre deux étapes de construction ; il rend la main
    des dizaines de secondes après son temps limite, et le diagnostic d'un calcul impossible, qui
    enchaîne des essais courts, dépasse son budget (décision D4).
    """
    instantane, niveaux = _construction_longue()
    debut = time.perf_counter()

    resultat = resoudre(instantane, options(temps_limite_s=1, niveaux=niveaux))

    ecoule = time.perf_counter() - debut
    assert resultat.statut is StatutSolveur.TEMPS_DEPASSE, resultat.message
    assert ecoule <= 1 + MARGE_INTERRUPTION_S, (
        f"rendu en {ecoule:.1f} s pour un temps limite de 1 s"
    )


class _EtapeVue(logging.Handler):
    """Lève un événement quand la construction du modèle journalise la fin d'une étape."""

    def __init__(self, etape: str) -> None:
        super().__init__(logging.DEBUG)
        self.etape = etape
        self.vue = threading.Event()

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage().startswith(f"Étape {self.etape} :"):
            self.vue.set()


@pytest.mark.cas_limite
def test_arret_tenu_pendant_une_construction_longue(caplog):
    """Cas : arrêt demandé pendant la construction d'un modèle long (temps limite de 60 s).

    Risque : le bouton « Arrêter » ou la fin d'un diagnostic n'agit qu'après la construction ;
    le calcul reste « en cours » des dizaines de secondes de plus (décision D4).
    """
    instantane, niveaux = _construction_longue()
    # L'étape longue (pauses et trajets) suit la séparation des personnes : l'arrêt est demandé
    # une fois celle-ci journalisée, pendant l'étape longue et non entre deux étapes.
    caplog.set_level(logging.DEBUG, logger="edt.solveur.modele")
    guetteur = _EtapeVue("separer_les_personnes")
    logging.getLogger("edt.solveur.modele").addHandler(guetteur)
    arret = threading.Event()
    resultats: list[ResultatSolveur] = []
    fil = threading.Thread(
        target=lambda: resultats.append(
            resoudre(instantane, options(temps_limite_s=60, niveaux=niveaux), arret=arret)
        ),
        daemon=True,
    )
    fil.start()
    try:
        assert guetteur.vue.wait(20), "la construction du modèle n'a pas atteint l'étape longue"
        time.sleep(0.5)  # l'étape longue est en route (elle dure bien plus sans interruption)
        arret.set()
        demande = time.perf_counter()
        fil.join(60)
    finally:
        arret.set()
        logging.getLogger("edt.solveur.modele").removeHandler(guetteur)

    ecoule = time.perf_counter() - demande
    assert not fil.is_alive(), "le moteur n'a pas rendu la main en 60 s"
    assert resultats[0].statut is StatutSolveur.ARRETE, resultats[0].message
    assert ecoule <= MARGE_INTERRUPTION_S, f"arrêt pris en compte {ecoule:.1f} s après la demande"


@pytest.mark.cas_limite
def test_contrainte_desactivee_par_l_utilisateur_reste_signalee_au_journal(caplog):
    """Cas : calcul ordinaire avec une contrainte désactivée (pause entre deux séances).

    Risque : en faisant taire le journal des essais du diagnostic, on ferait taire aussi
    l'avertissement d'un vrai calcul, où l'utilisateur doit voir qu'une règle a été ignorée.
    """
    f = Fabrique()
    f.salle()
    f.besoin([f.groupe()], [f.enseignant()], nombre=2)

    with caplog.at_level(logging.WARNING, logger="edt.solveur.moteur"):
        resultat = resoudre(f.instantane(), options(contraintes_desactivees={"PAUSE"}))

    assert resultat.statut is StatutSolveur.SOLUTION, resultat.message
    (avertissement,) = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "à la demande de l'utilisateur" in r.getMessage()
    ]
    assert "PAUSE" in avertissement.getMessage()
