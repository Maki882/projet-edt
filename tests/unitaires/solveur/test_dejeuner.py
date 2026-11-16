"""Pause déjeuner à trois modes : fixe (plage réservée), libre bornée (fenêtre), aucun.

Le mode fixe est éprouvé par `test_contraintes` ; ici, le mode « aucun » et
surtout le mode libre : pour chaque groupe et chaque enseignant, chaque jour
où il a une séance dans la fenêtre `[X, Y[`, il lui reste N minutes libres
d'un seul tenant dans la fenêtre. L'oracle `verifier_solution` ne connaît que
la plage fixe : la pause libre est vérifiée ici explicitement
(`pause_tenue`). Les créneaux des scénarios sont espacés d'un quart d'heure
pour que la règle `PAUSE` (15 min entre deux séances) ne se mêle pas du jeu.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from tests.outils.solveur.aides import SEMAINE, instancier_et_replacer, niveaux_avec, place
from tests.outils.solveur.fabrique import (
    LUNDI,
    MARDI,
    NIVEAUX_DURS,
    OPTIONS,
    RENTREE,
    Fabrique,
    H,
    options,
    verifier_solution,
)

from edt.domaine.contraintes import Niveau
from edt.domaine.enumerations import DemiJournee
from edt.domaine.instantane import Creneau, Instantane
from edt.solveur import StatutSolveur, resoudre
from edt.solveur.regles import pause_dejeuner_tenue
from edt.solveur.replacement import OptionsReplacement

MIDI = (Creneau(LUNDI, 12 * H, 14 * H, DemiJournee.MATIN),)
"""Un seul créneau, de 12 h à 14 h : il recoupe la pause déjeuner par défaut (12 h 45-13 h 45)."""

LIBRE = dict(
    dejeuner_mode="libre", dejeuner_debut_min=12 * H, dejeuner_fin_min=14 * H, dejeuner_duree_min=60
)
"""Pause libre d'une heure entre 12 h et 14 h."""

SOUPLE = options(niveaux=niveaux_avec(DEJEUNER=Niveau.IMPORTANT))
"""Toutes les règles dures, sauf la pause déjeuner, souple."""

H11, H13_15, H15_30 = 11 * H, 13 * H + 15, 15 * H + 30
"""Débuts des créneaux de 2 h des scénarios : 11 h-13 h, 13 h 15-15 h 15, 15 h 30-17 h 30.

Une séance à 11 h laisse 13 h-14 h ; une à 13 h 15 laisse 12 h-13 h 15 ; les
deux ensemble ne laissent que 13 h-13 h 15 : pas de pause.
"""


def creneaux(*jours: int, debuts: tuple[int, ...] = (H11, H13_15, H15_30), duree: int = 2 * H):
    """Créneaux de `duree` commençant aux minutes `debuts` (après-midi dès 13 h), chaque jour."""
    return tuple(
        Creneau(
            jour,
            debut,
            debut + duree,
            DemiJournee.MATIN if debut < 13 * H else DemiJournee.APRES_MIDI,
        )
        for jour in jours
        for debut in debuts
    )


def probleme(grille=MIDI, **parametres) -> Instantane:
    """Une séance de 2 h d'un groupe et d'un enseignant, sur les créneaux donnés."""
    f = Fabrique(creneaux=grille, **parametres)
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()])
    return f.instantane()


def pause_tenue(instantane: Instantane, resultat) -> None:
    """Chaque groupe et chaque enseignant garde sa pause libre chaque jour (définition de la règle)."""
    fenetre = instantane.parametres.fenetre_dejeuner
    assert fenetre is not None
    journees: dict[tuple[str, int, int], list[tuple[int, int]]] = defaultdict(list)
    for placement in resultat.placements:
        besoin = instantane.besoin(int(placement.seance_id.split("-")[0]))
        plage = (placement.debut, placement.fin)
        for enseignant in besoin.enseignants:
            journees[("enseignant", enseignant, placement.jour)].append(plage)
        for groupe in besoin.groupes:
            for lie in instantane.groupes_lies(groupe):
                journees[("groupe", lie, placement.jour)].append(plage)
    for (nature, identifiant, jour), plages in journees.items():
        assert pause_dejeuner_tenue(plages, fenetre), (nature, identifiant, jour, sorted(plages))


# --------------------------------------------------------------------------- #
# Modes fixe et aucun
# --------------------------------------------------------------------------- #


def test_mode_aucun_une_seance_peut_chevaucher_midi():
    fixe = resoudre(probleme(), OPTIONS)
    assert fixe.statut is StatutSolveur.INFAISABLE and "pause déjeuner" in fixe.message

    instantane = probleme(dejeuner_mode="aucun")
    assert instantane.parametres.plage_dejeuner is None
    assert instantane.parametres.fenetre_dejeuner is None
    resultat = resoudre(instantane, OPTIONS)
    assert resultat.contraintes_desactivees == frozenset()  # DEJEUNER reste active
    places = verifier_solution(instantane, resultat)  # l'oracle ne relève rien non plus
    assert places["1-1"].debut == 12 * H
    assert resultat.statistiques.get("entorses", {}) == {}


# --------------------------------------------------------------------------- #
# Mode libre : la règle
# --------------------------------------------------------------------------- #


def test_regle_de_la_pause_libre():
    fenetre = (12 * H, 14 * H, 60)
    assert pause_dejeuner_tenue([], fenetre)
    assert pause_dejeuner_tenue(
        [(8 * H, 12 * H), (14 * H, 16 * H)], fenetre
    )  # rien dans la fenêtre
    assert pause_dejeuner_tenue([(11 * H, 13 * H)], fenetre)  # 13 h-14 h
    assert pause_dejeuner_tenue([(13 * H, 15 * H)], fenetre)  # 12 h-13 h
    assert pause_dejeuner_tenue([(12 * H + 30, 13 * H)], fenetre)  # 13 h-14 h
    assert not pause_dejeuner_tenue([(12 * H + 30, 13 * H + 15)], fenetre)  # 30 min + 45 min
    assert not pause_dejeuner_tenue([(11 * H, 13 * H), (13 * H, 15 * H)], fenetre)
    assert not pause_dejeuner_tenue([(12 * H, 14 * H)], fenetre)
    assert pause_dejeuner_tenue([(11 * H, 13 * H), (13 * H, 15 * H)], (12 * H, 14 * H, 0))


def test_mode_libre_une_seance_seule_qui_couvre_la_fenetre():
    """La séance de 12 h à 14 h ne laisse pas une heure : refusée dès la pré-vérification."""
    instantane = probleme(**LIBRE)
    assert instantane.parametres.fenetre_dejeuner == (12 * H, 14 * H, 60)
    dur = resoudre(instantane, OPTIONS)
    assert dur.statut is StatutSolveur.INFAISABLE and "pause déjeuner" in dur.message

    souple = resoudre(instantane, SOUPLE)
    assert souple.statut is StatutSolveur.SOLUTION, souple.message
    assert verifier_solution(instantane, souple)["1-1"].debut == 12 * H
    # Une entorse pour le groupe, une pour l'enseignant : la journée de chacun, comptée une fois.
    assert souple.statistiques["entorses"] == {"DEJEUNER": 2}
    assert "pause déjeuner" in souple.message


def test_mode_libre_la_pause_peut_glisser():
    """Deux séances du même groupe un lundi : 11 h et 13 h 15 ensemble sont interdites."""
    f = Fabrique(creneaux=creneaux(LUNDI), **LIBRE)
    f.salle()
    groupe = f.groupe("A1")
    f.besoin([groupe], [f.enseignant()])
    f.besoin([groupe], [f.enseignant()])
    instantane = f.instantane()
    resultat = resoudre(instantane, OPTIONS)
    assert resultat.statut is StatutSolveur.SOLUTION, resultat.message
    places = verifier_solution(instantane, resultat)
    pause_tenue(instantane, resultat)
    assert {places["1-1"].debut, places["2-1"].debut} != {H11, H13_15}
    assert resultat.statistiques.get("entorses", {}) == {}


def test_mode_libre_infaisable_quand_la_fenetre_ne_peut_pas_contenir_la_pause():
    """Sans le créneau de 15 h 30, les deux séances encadrent midi : impossible en dur, prouvé."""
    f = Fabrique(creneaux=creneaux(LUNDI, debuts=(H11, H13_15)), **LIBRE)
    f.salle()
    groupe = f.groupe("A1")
    f.besoin([groupe], [f.enseignant()])
    f.besoin([groupe], [f.enseignant()])
    instantane = f.instantane()
    dur = resoudre(instantane, OPTIONS)
    assert dur.statut is StatutSolveur.INFAISABLE, dur.message
    assert "prouvé" in dur.message

    souple = resoudre(instantane, SOUPLE)
    assert souple.statut is StatutSolveur.SOLUTION, souple.message
    verifier_solution(instantane, souple)
    assert souple.statistiques["entorses"] == {"DEJEUNER": 1}  # le groupe A1, ce lundi
    assert "1 entorse" in souple.message and "pause déjeuner" in souple.message


def test_mode_libre_l_enseignant_a_aussi_sa_pause():
    """Un enseignant devant deux groupes sans lien : ses séances ne doivent pas encadrer midi."""
    f = Fabrique(creneaux=creneaux(LUNDI, debuts=(H11, H13_15)), **LIBRE)
    f.salle()
    f.salle()
    enseignant = f.enseignant("M. Dupont")
    f.besoin([f.groupe("A1")], [enseignant])
    f.besoin([f.groupe("A2")], [enseignant])
    instantane = f.instantane()
    assert resoudre(instantane, OPTIONS).statut is StatutSolveur.INFAISABLE
    souple = resoudre(instantane, SOUPLE)
    assert souple.statut is StatutSolveur.SOLUTION, souple.message
    assert souple.statistiques["entorses"] == {"DEJEUNER": 1}  # M. Dupont, ce lundi

    # Sans enseignant commun, les deux groupes sont indépendants : aucune contrainte.
    f = Fabrique(creneaux=creneaux(LUNDI, debuts=(H11, H13_15)), **LIBRE)
    f.salle()
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()])
    f.besoin([f.groupe("A2")], [f.enseignant()])
    instantane = f.instantane()
    resultat = resoudre(instantane, OPTIONS)
    assert resultat.statut is StatutSolveur.SOLUTION, resultat.message
    pause_tenue(instantane, resultat)


def test_mode_libre_jour_sans_seance_dans_la_fenetre_non_contraint():
    """Trois séances hors de la fenêtre (8 h-9 h 30, 10 h-11 h 30, 14 h-15 h 30) : rien à dire."""
    grille = creneaux(LUNDI, debuts=(8 * H, 10 * H, 14 * H), duree=90)
    f = Fabrique(creneaux=grille, ouverture_min=8 * H, **LIBRE)
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()], duree=90, nombre=3)
    instantane = f.instantane()
    resultat = resoudre(instantane, OPTIONS)
    assert resultat.statut is StatutSolveur.SOLUTION, resultat.message
    places = verifier_solution(instantane, resultat)
    assert sorted(p.debut for p in places.values()) == [8 * H, 10 * H, 14 * H]
    assert resultat.statistiques.get("entorses", {}) == {}


def test_mode_libre_les_sous_groupes_dejeunent_chacun_a_leur_heure():
    """Les TD des deux demi-groupes se relaient sur midi : chacun garde une heure, la classe aussi."""
    grille = creneaux(LUNDI, debuts=(12 * H, 13 * H + 15), duree=H)
    f = Fabrique(creneaux=grille, **LIBRE)
    f.salle()
    f.salle()
    classe = f.groupe("A", effectif=30)
    demi_a = f.groupe("A-1", effectif=15, parent=classe)
    demi_b = f.groupe("A-2", effectif=15, parent=classe)
    f.besoin([demi_a], [f.enseignant()], duree=H)
    f.besoin([demi_b], [f.enseignant()], duree=H)
    instantane = f.instantane()
    resultat = resoudre(instantane, OPTIONS)
    assert resultat.statut is StatutSolveur.SOLUTION, resultat.message
    verifier_solution(instantane, resultat)
    assert resultat.statistiques.get("entorses", {}) == {}

    # Un cours de la classe entière occupe les deux demi-groupes : A-1 n'a plus sa pause.
    f = Fabrique(creneaux=grille, **LIBRE)
    f.salle()
    f.salle()
    classe = f.groupe("A", effectif=30)
    demi_a = f.groupe("A-1", effectif=15, parent=classe)
    f.groupe("A-2", effectif=15, parent=classe)
    f.besoin([classe], [f.enseignant()], duree=H)
    f.besoin([demi_a], [f.enseignant()], duree=H)
    instantane = f.instantane()
    assert resoudre(instantane, OPTIONS).statut is StatutSolveur.INFAISABLE
    souple = resoudre(instantane, SOUPLE)
    assert souple.statut is StatutSolveur.SOLUTION, souple.message
    assert souple.statistiques["entorses"] == {"DEJEUNER": 1}  # A-1 seulement : A et A-2 déjeunent


def test_mode_libre_reproductible():
    """Un enseignant saturé deux jours durant : deux entorses, les mêmes à chaque calcul."""
    f = Fabrique(creneaux=creneaux(LUNDI, MARDI), **LIBRE)
    f.salle()
    f.salle()
    enseignant = f.enseignant()
    for code in ("A1", "A2", "A3"):
        f.besoin([f.groupe(code)], [enseignant], nombre=2)
    instantane = f.instantane()
    premier = resoudre(instantane, SOUPLE)
    second = resoudre(instantane, SOUPLE)
    assert premier.statut is StatutSolveur.SOLUTION, premier.message
    assert premier.statistiques["entorses"] == {"DEJEUNER": 2}  # l'enseignant, lundi et mardi
    assert premier.placements == second.placements
    assert premier.statistiques["entorses"] == second.statistiques["entorses"]


# --------------------------------------------------------------------------- #
# Mode libre : replacement des séances datées
# --------------------------------------------------------------------------- #


def une_classe_autour_de_midi(debuts=(H11, H13_15)) -> Fabrique:
    """Une classe, deux séances par semaine ; le lundi de la 2e semaine est fermé."""
    f = Fabrique(nb_semaines=3, creneaux=creneaux(LUNDI, MARDI, debuts=debuts), **LIBRE)
    f.salle("N101")
    f.besoin([f.groupe("A1")], [f.enseignant("Mme Martin")], nombre=2)
    f.jour_ferme(RENTREE + SEMAINE)
    return f


PLACEMENTS = [place("1-1", LUNDI, H11, 1), place("1-2", MARDI, H13_15, 1)]
"""Lundi 11 h-13 h et mardi 13 h 15-15 h 15 : la séance du lundi fermé doit être replacée."""


def test_replacement_ne_supprime_pas_la_pause_libre():
    """Le mardi 11 h est le seul créneau libre : il encadrerait midi avec 13 h 15, refusé en dur."""
    _, _, resultat = instancier_et_replacer(
        une_classe_autour_de_midi(), PLACEMENTS, options=OptionsReplacement(niveaux=NIVEAUX_DURS)
    )
    (replacement,) = resultat.replacements
    assert not replacement.replacee
    assert "pause déjeuner" in replacement.raison

    # Souple (le niveau par défaut) : replacée au prix d'une entorse pour le groupe et une
    # pour l'enseignante.
    _, _, resultat = instancier_et_replacer(une_classe_autour_de_midi(), PLACEMENTS)
    (replacement,) = resultat.replacements
    assert replacement.replacee, replacement.raison
    assert replacement.occurrence.date == RENTREE + SEMAINE + dt.timedelta(days=1)
    assert replacement.occurrence.debut == H11
    assert replacement.cout >= 2 * 1000


def test_replacement_choisit_un_creneau_qui_garde_la_pause():
    """Avec le créneau de 15 h 30, la séance replacée le mardi s'y met plutôt qu'à 11 h."""
    f = une_classe_autour_de_midi(debuts=(H11, H13_15, H15_30))
    _, _, resultat = instancier_et_replacer(f, PLACEMENTS)  # règle souple : 15 h 30 coûte moins
    (replacement,) = resultat.replacements
    assert replacement.replacee, replacement.raison
    assert replacement.occurrence.date == RENTREE + SEMAINE + dt.timedelta(days=1)
    assert replacement.occurrence.debut == H15_30
    assert replacement.cout < 1000
