"""Une contrainte, un test : chaque contrainte est déterminante sur un petit problème.

Pour chaque code du catalogue, un problème minimal où la contrainte change
l'issue : impossible quand elle est active, résolu quand l'utilisateur la
désactive (ou résolu autrement). Chaque solution passe par l'oracle
(`verifier_solution`) et porte en plus une assertion directe sur la règle.
"""

from __future__ import annotations

import pytest
from tests.outils.solveur.aides import MATINEE, UN_CRENEAU
from tests.outils.solveur.fabrique import (
    C1,
    C2,
    C3,
    C4,
    JEUDI,
    LUNDI,
    MARDI,
    NIVEAUX_DURS,
    OPTIONS,
    RENTREE,
    SAMEDI,
    VENDREDI,
    Fabrique,
    H,
    grille,
    options,
    verifier_solution,
)

from edt.domaine.contraintes import CodeContrainte as C
from edt.domaine.enumerations import (
    Accessibilite,
    CibleIndisponibilite,
    DemiJournee,
    ModeVolume,
    NatureGroupe,
)
from edt.domaine.heures import ecart, se_chevauchent
from edt.domaine.instantane import Creneau
from edt.solveur import StatutSolveur, resoudre
from edt.solveur.regles import Regles


def sans(*codes: C):
    """Options de test avec des contraintes désactivées."""
    return options(contraintes_desactivees=frozenset(codes))


def resoudre_sans(instantane, *codes: C):
    """Résout avec les contraintes `codes` désactivées, et vérifie le rappel dans le résultat."""
    resultat = resoudre(instantane, sans(*codes))
    assert resultat.contraintes_desactivees == frozenset(codes)
    return resultat


def impossible(instantane, *mots: str) -> None:
    """Le calcul est impossible, et le message parle de `mots`."""
    resultat = resoudre(instantane, OPTIONS)
    assert resultat.statut is StatutSolveur.INFAISABLE, resultat.message
    assert resultat.placements == ()
    texte = resultat.message + " ".join(resultat.avertissements)
    for mot in mots:
        assert mot in texte, f"« {mot} » absent de : {texte}"


def chevauchent(a, b) -> bool:
    return a.jour == b.jour and se_chevauchent(a.debut, a.fin, b.debut, b.fin)


# --------------------------------------------------------------------------- #
# Unicité des ressources
# --------------------------------------------------------------------------- #


def test_salle_unique():
    f = Fabrique(creneaux=UN_CRENEAU)
    f.salle("N101")
    f.besoin([f.groupe("A1")], [f.enseignant()])
    f.besoin([f.groupe("A2")], [f.enseignant()])
    instantane = f.instantane()
    impossible(instantane, "Une séance par salle")

    places = verifier_solution(
        instantane, resoudre_sans(instantane, C.SALLE_UNIQUE), [C.SALLE_UNIQUE]
    )
    assert places["1-1"].salle_id == places["2-1"].salle_id
    assert chevauchent(places["1-1"], places["2-1"])


def test_salle_unique_deux_seances_ne_partagent_pas_la_salle():
    f = Fabrique(creneaux=MATINEE)
    f.salle("N101")
    f.besoin([f.groupe("A1")], [f.enseignant()])
    f.besoin([f.groupe("A2")], [f.enseignant()])
    instantane = f.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert not chevauchent(places["1-1"], places["2-1"])


def test_enseignant_unique():
    f = Fabrique(creneaux=UN_CRENEAU)
    f.salle()
    f.salle()
    martin = f.enseignant("Mme Martin")
    f.besoin([f.groupe("A1")], [martin])
    f.besoin([f.groupe("A2")], [martin])
    instantane = f.instantane()
    impossible(instantane, "Une séance par enseignant")

    places = verifier_solution(
        instantane, resoudre_sans(instantane, C.ENSEIGNANT_UNIQUE), [C.ENSEIGNANT_UNIQUE]
    )
    assert chevauchent(places["1-1"], places["2-1"])


def test_enseignant_fictif_hors_contrainte():
    f = Fabrique(creneaux=UN_CRENEAU)
    f.salle()
    f.salle()
    a_recruter = f.enseignant("À recruter", fictif=True)
    f.besoin([f.groupe("A1")], [a_recruter])
    f.besoin([f.groupe("A2")], [a_recruter])
    instantane = f.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert chevauchent(places["1-1"], places["2-1"])


def test_groupe_unique():
    f = Fabrique(creneaux=UN_CRENEAU)
    f.salle()
    f.salle()
    promo = f.groupe("A1", 60, nature=NatureGroupe.PROMOTION)
    classe = f.groupe("A1-C1", 30, parent=promo)
    f.besoin([promo], [f.enseignant()], effectif=30)
    f.besoin([classe], [f.enseignant()])
    instantane = f.instantane()
    impossible(instantane, "Une séance par groupe")

    places = verifier_solution(
        instantane, resoudre_sans(instantane, C.GROUPE_UNIQUE), [C.GROUPE_UNIQUE]
    )
    assert chevauchent(places["1-1"], places["2-1"])


def test_groupe_unique_deux_classes_soeurs_en_parallele():
    f = Fabrique(creneaux=UN_CRENEAU)
    f.salle()
    f.salle()
    promo = f.groupe("A1", 60, nature=NatureGroupe.PROMOTION)
    f.besoin([f.groupe("A1-C1", 30, parent=promo)], [f.enseignant()])
    f.besoin([f.groupe("A1-C2", 30, parent=promo)], [f.enseignant()])
    instantane = f.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert chevauchent(places["1-1"], places["2-1"])


def test_infaisabilite_prouvee_par_la_recherche():
    """Trois séances qui se gênent deux à deux pour deux créneaux : seule la recherche le voit."""
    f = Fabrique(creneaux=MATINEE)
    for _ in range(3):
        f.salle()
    a, b = f.groupe("A"), f.groupe("B")
    x, y = f.enseignant(), f.enseignant()
    f.besoin([a], [x])  # gêne la deuxième par l'enseignant x
    f.besoin([b], [x, y])  # gêne la troisième par l'enseignant y
    f.besoin([a], [y])  # gêne la première par le groupe A
    instantane = f.instantane()
    resultat = resoudre(instantane, OPTIONS)
    assert resultat.statut is StatutSolveur.INFAISABLE
    assert resultat.statistiques["refuse"] is False
    assert resultat.statistiques["statut_cp_sat"] == "INFEASIBLE"
    assert "prouvé" in resultat.message
    assert resultat.placements == ()


# --------------------------------------------------------------------------- #
# Pause et trajets
# --------------------------------------------------------------------------- #

SANS_PAUSE = (
    Creneau(LUNDI, 8 * H, 10 * H, DemiJournee.MATIN),
    Creneau(LUNDI, 10 * H, 12 * H, DemiJournee.MATIN),
)


def test_pause():
    f = Fabrique(creneaux=SANS_PAUSE, ouverture_min=8 * H)
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()], nombre=2)
    instantane = f.instantane()
    resultat = resoudre(instantane, OPTIONS)
    assert resultat.statut is StatutSolveur.INFAISABLE

    places = verifier_solution(instantane, resoudre_sans(instantane, C.PAUSE), [C.PAUSE])
    assert {places["1-1"].debut, places["1-2"].debut} == {8 * H, 10 * H}


def test_pause_respectee_quand_elle_est_possible():
    creneaux = (*SANS_PAUSE, Creneau(LUNDI, 14 * H, 16 * H, DemiJournee.APRES_MIDI))
    f = Fabrique(creneaux=creneaux, ouverture_min=8 * H)
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()], nombre=2)
    instantane = f.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    a, b = places["1-1"], places["1-2"]
    assert ecart(a.debut, a.fin, b.debut, b.fin) >= instantane.parametres.pause_min


def test_pause_sans_unicite_autorise_le_chevauchement_mais_pas_l_enchainement():
    """Unicité de l'enseignant désactivée : ses deux séances se chevauchent ou respectent la pause."""
    f = Fabrique(creneaux=SANS_PAUSE, ouverture_min=8 * H)
    f.salle()
    f.salle()
    martin = f.enseignant()
    f.besoin([f.groupe("A1")], [martin])
    f.besoin([f.groupe("A2")], [martin])
    instantane = f.instantane()
    places = verifier_solution(
        instantane, resoudre_sans(instantane, C.ENSEIGNANT_UNIQUE), [C.ENSEIGNANT_UNIQUE]
    )
    assert places["1-1"].debut == places["2-1"].debut


def deux_campus(creneaux):
    f = Fabrique(creneaux=creneaux)
    nord, sud = f.campus("N"), f.campus("S")
    f.trajet(nord, sud, 60)
    salle_nord = f.salle("N1", campus=nord)
    salle_sud = f.salle("S1", campus=sud)
    classe = f.groupe("A1")
    f.besoin([classe], [f.enseignant()], salle_imposee_id=salle_nord)
    f.besoin([classe], [f.enseignant()], salle_imposee_id=salle_sud)
    return f.instantane()


def test_campus_trajet():
    instantane = deux_campus(MATINEE)  # 15 minutes entre les deux créneaux, 60 de trajet
    resultat = resoudre(instantane, OPTIONS)
    assert resultat.statut is StatutSolveur.INFAISABLE

    places = verifier_solution(
        instantane, resoudre_sans(instantane, C.CAMPUS_TRAJET), [C.CAMPUS_TRAJET]
    )
    assert {places["1-1"].debut, places["2-1"].debut} == {C1, C2}


def test_campus_trajet_ne_concerne_pas_deux_seances_qui_se_chevauchent():
    """Unicité du groupe décochée : deux cours en même temps sur deux campus, comme l'oracle."""
    instantane = deux_campus(UN_CRENEAU)
    assert resoudre(instantane, OPTIONS).statut is StatutSolveur.INFAISABLE
    places = verifier_solution(
        instantane, resoudre_sans(instantane, C.GROUPE_UNIQUE), [C.GROUPE_UNIQUE]
    )
    assert chevauchent(places["1-1"], places["2-1"])
    assert places["1-1"].salle_id != places["2-1"].salle_id


def test_campus_trajet_respecte_quand_il_est_possible():
    instantane = deux_campus(grille(jours=[LUNDI], samedi=False))
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    a, b = places["1-1"], places["2-1"]
    assert ecart(a.debut, a.fin, b.debut, b.fin) >= 60


# --------------------------------------------------------------------------- #
# Grille horaire
# --------------------------------------------------------------------------- #


def test_dejeuner():
    midi = (Creneau(LUNDI, 12 * H, 14 * H, DemiJournee.MATIN),)
    f = Fabrique(creneaux=midi)
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()])
    instantane = f.instantane()
    impossible(instantane, "pause déjeuner")

    places = verifier_solution(instantane, resoudre_sans(instantane, C.DEJEUNER), [C.DEJEUNER])
    assert places["1-1"].debut == 12 * H


def test_horaires_la_seance_tient_dans_sa_demi_journee():
    f = Fabrique(creneaux=grille(jours=[LUNDI], samedi=False))
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()], duree=3 * H)
    instantane = f.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert places["1-1"].debut in (C1, C3)  # 10 h 45 et 16 h débordent de la demi-journée
    assert places["1-1"].fin - places["1-1"].debut == 3 * H


def test_horaires():
    f = Fabrique(creneaux=grille(jours=[LUNDI], samedi=False))
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()], duree=5 * H)
    instantane = f.instantane()
    impossible(instantane, "5 h", "demi-journée")

    resultat = resoudre_sans(instantane, C.HORAIRES, C.DEJEUNER)
    places = verifier_solution(instantane, resultat, [C.HORAIRES, C.DEJEUNER])
    assert places["1-1"].jour == LUNDI
    assert places["1-1"].fin - places["1-1"].debut == 5 * H


def test_horaires_creneau_impose_hors_grille():
    f = Fabrique()
    f.salle()
    f.besoin([f.groupe("A2")], [f.enseignant()], duree=4 * H, jour_impose=LUNDI, debut_impose=8 * H)
    instantane = f.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert (places["1-1"].jour, places["1-1"].debut, places["1-1"].fin) == (LUNDI, 8 * H, 12 * H)


def test_jours():
    f = Fabrique(creneaux=grille(jours=[], samedi=True))  # la grille n'a que le samedi matin
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()])
    instantane = f.instantane()
    impossible(instantane, "aucun jour de cours permis")

    places = verifier_solution(instantane, resoudre_sans(instantane, C.JOURS), [C.JOURS])
    assert places["1-1"].jour == SAMEDI


def test_jours_samedi_reserve_aux_seances_qui_l_autorisent():
    f = Fabrique(creneaux=grille(jours=[], samedi=True))
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()], samedi_autorise=True)
    instantane = f.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert places["1-1"].jour == SAMEDI


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("ignorees", "attendus"),
    [
        ((), [C.HORAIRES]),
        ((C.JOURS,), [C.HORAIRES]),
        ((C.HORAIRES,), [C.JOURS]),
        ((C.HORAIRES, C.JOURS), []),
    ],
)
def test_jour_travaille_sans_creneau_une_seule_cause(ignorees, attendus):
    """Cas : le mardi est un jour travaillé, mais la grille n'a de créneaux que le lundi.

    Risque : la même cause portée par `JOURS` dans le moteur et par `HORAIRES`
    dans le vérificateur, ou plus dite du tout quand l'une des deux est ignorée.
    """
    f = Fabrique(creneaux=grille(jours=[LUNDI], samedi=False))
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()])
    regles = Regles(f.instantane(), ignorees, niveaux=NIVEAUX_DURS)
    violations = regles.violations_de_jour("1-1", MARDI)
    assert [v.code for v in violations] == attendus
    assert all("aucun créneau le mardi" in v.raison for v in violations)


# --------------------------------------------------------------------------- #
# Exigences de salle
# --------------------------------------------------------------------------- #


def test_capacite():
    f = Fabrique(creneaux=UN_CRENEAU)
    petite = f.salle("N101", capacite=20)
    f.besoin([f.groupe("A1", 30)], [f.enseignant()])
    instantane = f.instantane()
    impossible(instantane, "30 places")

    places = verifier_solution(instantane, resoudre_sans(instantane, C.CAPACITE), [C.CAPACITE])
    assert places["1-1"].salle_id == petite


def test_capacite_choisit_la_salle_assez_grande():
    f = Fabrique(creneaux=UN_CRENEAU)
    f.salle("N101", capacite=20)
    grande = f.salle("N102", capacite=40)
    f.besoin([f.groupe("A1", 30)], [f.enseignant()])
    instantane = f.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert places["1-1"].salle_id == grande


def test_type_salle():
    f = Fabrique(creneaux=UN_CRENEAU)
    amphi = f.salle("Amphi", type_salle=1)
    labo = f.salle("Labo", type_salle=2)
    f.besoin([f.groupe("A1")], [f.enseignant()], types_salle_acceptes=frozenset({2}))
    f.besoin([f.groupe("A2")], [f.enseignant()], types_salle_acceptes=frozenset({2}))
    instantane = f.instantane()
    impossible(instantane, "Labo")

    places = verifier_solution(instantane, resoudre_sans(instantane, C.TYPE_SALLE), [C.TYPE_SALLE])
    assert {places["1-1"].salle_id, places["2-1"].salle_id} == {amphi, labo}


def test_type_salle_salle_et_campus_imposes():
    f = Fabrique(creneaux=UN_CRENEAU)
    nord, sud = f.campus("N"), f.campus("S")
    f.salle("N1", campus=nord)
    imposee = f.salle("N2", campus=nord)
    au_sud = f.salle("S1", campus=sud)
    f.besoin([f.groupe("A1")], [f.enseignant()], salle_imposee_id=imposee)
    f.besoin([f.groupe("A2")], [f.enseignant()], campus_impose_id=sud)
    instantane = f.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert places["1-1"].salle_id == imposee
    assert places["2-1"].salle_id == au_sud


def test_equipement():
    f = Fabrique(creneaux=UN_CRENEAU)
    nue = f.salle("N101")
    f.besoin([f.groupe("A1")], [f.enseignant()], equipements_requis=frozenset({7}))
    instantane = f.instantane()
    impossible(instantane, "matériel requis")

    places = verifier_solution(instantane, resoudre_sans(instantane, C.EQUIPEMENT), [C.EQUIPEMENT])
    assert places["1-1"].salle_id == nue


def test_equipement_choisit_la_salle_equipee():
    f = Fabrique(creneaux=UN_CRENEAU)
    f.salle("N101")
    equipee = f.salle("N102", equipements=[7, 8])
    f.besoin([f.groupe("A1")], [f.enseignant()], equipements_requis=frozenset({7}))
    instantane = f.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert places["1-1"].salle_id == equipee


def test_accessibilite():
    f = Fabrique(creneaux=UN_CRENEAU)
    inaccessible = f.salle("N101", accessibilite=Accessibilite.NON)
    f.besoin([f.groupe("A1", accessibilite=True)], [f.enseignant()])
    instantane = f.instantane()
    impossible(instantane, "salle accessible")

    places = verifier_solution(
        instantane, resoudre_sans(instantane, C.ACCESSIBILITE), [C.ACCESSIBILITE]
    )
    assert places["1-1"].salle_id == inaccessible


def test_accessibilite_inconnue_vaut_non_sauf_reglage():
    def instantane(**parametres):
        f = Fabrique(creneaux=UN_CRENEAU, **parametres)
        f.salle("N101", accessibilite=Accessibilite.INCONNU)
        f.besoin([f.groupe("A1", accessibilite=True)], [f.enseignant()])
        return f.instantane()

    assert resoudre(instantane(), OPTIONS).statut is StatutSolveur.INFAISABLE
    permissif = instantane(accessibilite_inconnue_acceptee=True)
    verifier_solution(permissif, resoudre(permissif, OPTIONS))


def test_accessibilite_heritee_d_un_groupe_lie():
    """La classe d'un demi-groupe qui requiert l'accessibilité va, elle aussi, en salle accessible."""
    f = Fabrique(creneaux=UN_CRENEAU)
    f.salle("N101", accessibilite=Accessibilite.NON)
    accessible = f.salle("N102", accessibilite=Accessibilite.OUI)
    classe = f.groupe("A1")
    f.groupe("A1-G1", 15, parent=classe, nature=NatureGroupe.DEMI_GROUPE, accessibilite=True)
    f.besoin([classe], [f.enseignant()])
    instantane = f.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert places["1-1"].salle_id == accessible


# --------------------------------------------------------------------------- #
# Disponibilités
# --------------------------------------------------------------------------- #


def test_dispo_enseignant():
    f = Fabrique(creneaux=grille(jours=[LUNDI, MARDI], samedi=False))
    f.salle()
    martin = f.enseignant("Mme Martin")
    f.indisponibilite(CibleIndisponibilite.ENSEIGNANT, martin, jour=LUNDI)
    f.indisponibilite(CibleIndisponibilite.ENSEIGNANT, martin, jour=MARDI, fin=13 * H)
    f.besoin([f.groupe("A1")], [martin])
    instantane = f.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert places["1-1"].jour == MARDI and places["1-1"].debut >= 13 * H

    f.indisponibilite(CibleIndisponibilite.ENSEIGNANT, martin, jour=MARDI, debut=13 * H)
    bloque = f.instantane()
    impossible(bloque, "Mme Martin", "indisponibilité")
    verifier_solution(bloque, resoudre_sans(bloque, C.DISPO_ENSEIGNANT), [C.DISPO_ENSEIGNANT])


def test_dispo_groupe_vaut_pour_les_groupes_lies():
    f = Fabrique(creneaux=grille(jours=[LUNDI], samedi=False))
    f.salle()
    promo = f.groupe("A1", 60, nature=NatureGroupe.PROMOTION)
    classe = f.groupe("A1-C1", 30, parent=promo)
    f.indisponibilite(CibleIndisponibilite.GROUPE, promo, jour=LUNDI, fin=13 * H)
    f.besoin([classe], [f.enseignant()])
    instantane = f.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert places["1-1"].debut >= 13 * H

    f.indisponibilite(CibleIndisponibilite.GROUPE, promo, jour=LUNDI, debut=13 * H)
    bloque = f.instantane()
    impossible(bloque, "le groupe A1")
    verifier_solution(bloque, resoudre_sans(bloque, C.DISPO_GROUPE), [C.DISPO_GROUPE])


def test_dispo_salle():
    f = Fabrique(creneaux=MATINEE)
    travaux = f.salle("N101")
    libre = f.salle("N102")
    f.indisponibilite(CibleIndisponibilite.SALLE, travaux, jour=LUNDI, fin=10 * H + 40)
    f.besoin([f.groupe("A1")], [f.enseignant()])
    f.besoin([f.groupe("A2")], [f.enseignant()])
    f.besoin([f.groupe("A3")], [f.enseignant()])
    instantane = f.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    en_travaux = [p for p in places.values() if p.salle_id == travaux]
    assert [p.debut for p in en_travaux] == [C2]  # N101 n'est utilisable qu'à 10 h 45
    assert sum(1 for p in places.values() if p.salle_id == libre) == 2

    f.indisponibilite(CibleIndisponibilite.SALLE, travaux, jour=LUNDI, debut=10 * H + 40)
    bloque = f.instantane()
    assert resoudre(bloque, OPTIONS).statut is StatutSolveur.INFAISABLE
    verifier_solution(bloque, resoudre_sans(bloque, C.DISPO_SALLE), [C.DISPO_SALLE])


def test_indisponibilites_datees_et_souples_ne_restreignent_pas_la_semaine_type():
    f = Fabrique(creneaux=UN_CRENEAU)
    salle = f.salle()
    martin = f.enseignant()
    f.indisponibilite(CibleIndisponibilite.ENSEIGNANT, martin, date_debut=RENTREE)
    f.indisponibilite(CibleIndisponibilite.SALLE, salle, date_debut=RENTREE)
    f.indisponibilite(CibleIndisponibilite.ENSEIGNANT, martin, jour=LUNDI, dure=False)
    f.besoin([f.groupe("A1")], [martin])
    instantane = f.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert (places["1-1"].jour, places["1-1"].debut) == (LUNDI, C1)


# --------------------------------------------------------------------------- #
# Maxima des enseignants
# --------------------------------------------------------------------------- #


def test_max_enseignant_par_jour():
    f = Fabrique(creneaux=grille(jours=[LUNDI], samedi=False))
    f.salle()
    martin = f.enseignant("Mme Martin", max_jour=2 * H)
    f.besoin([f.groupe("A1")], [martin], nombre=2)
    instantane = f.instantane()
    impossible(instantane, "Mme Martin")

    places = verifier_solution(
        instantane, resoudre_sans(instantane, C.MAX_ENSEIGNANT), [C.MAX_ENSEIGNANT]
    )
    assert places["1-1"].jour == places["1-2"].jour == LUNDI


def test_max_enseignant_par_jour_repartit_sur_deux_jours():
    f = Fabrique(creneaux=grille(jours=[LUNDI, MARDI], samedi=False))
    f.salle()
    martin = f.enseignant("Mme Martin", max_jour=4 * H)
    f.besoin([f.groupe("A1")], [martin], nombre=2)
    f.besoin([f.groupe("A2")], [martin], nombre=2)
    instantane = f.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    par_jour = [sum(1 for p in places.values() if p.jour == jour) for jour in (LUNDI, MARDI)]
    assert par_jour == [2, 2]


def test_max_enseignant_par_semaine():
    f = Fabrique()
    f.salle()
    martin = f.enseignant("Mme Martin", max_semaine=4 * H)
    f.besoin([f.groupe("A1")], [martin], nombre=3)
    instantane = f.instantane()
    impossible(instantane, "Mme Martin", "6 h", "maximum de 4 h")
    verifier_solution(instantane, resoudre_sans(instantane, C.MAX_ENSEIGNANT), [C.MAX_ENSEIGNANT])


# --------------------------------------------------------------------------- #
# Volume
# --------------------------------------------------------------------------- #


def test_volume_besoin_non_depliable():
    f = Fabrique(nb_semaines=2)
    f.salle()
    f.besoin(
        [f.groupe("A1")],
        [f.enseignant()],
        nombre=5,
        mode_volume=ModeVolume.TOTAL,
        max_par_semaine=1,
        libelle="Projet",
    )
    instantane = f.instantane()
    assert instantane.problemes_depliage
    impossible(instantane, "Projet", "5 séances demandées")

    resultat = resoudre_sans(instantane, C.VOLUME)
    places = verifier_solution(instantane, resultat, [C.VOLUME])
    assert len(places) == len(instantane.seances) == 1


@pytest.mark.parametrize("nombre", [1, 4])
def test_volume_tout_est_place(nombre):
    f = Fabrique(creneaux=grille(jours=[LUNDI], samedi=False))
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()], nombre=nombre)
    instantane = f.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert len(places) == nombre
    assert len({(p.jour, p.debut) for p in places.values()}) == nombre


def test_volume_un_creneau_impose_a_plusieurs_seances_est_refuse():
    """Un besoin de deux séances par semaine au même jour et à la même heure imposés."""
    f = Fabrique()
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()], nombre=2, jour_impose=JEUDI, debut_impose=C3)
    impossible(f.instantane(), "le même jour et la même heure", "séances n° 1 et 2")


# --------------------------------------------------------------------------- #
# Verrous
# --------------------------------------------------------------------------- #


def test_verrou():
    f = Fabrique()
    f.salle("N101")
    preferee = f.salle("N102")
    f.besoin([f.groupe("A1")], [f.enseignant()], nombre=3)
    f.verrou("1-2", VENDREDI, C4, preferee)
    instantane = f.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    verrouillee = places["1-2"]
    assert (verrouillee.jour, verrouillee.debut, verrouillee.salle_id) == (VENDREDI, C4, preferee)


def test_verrou_desactive_la_seance_redevient_libre():
    f = Fabrique(creneaux=UN_CRENEAU)
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()])
    f.verrou("1-1", MARDI, C2)  # hors de la grille réduite au lundi 8 h 30
    instantane = f.instantane()
    verrouille = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert (verrouille["1-1"].jour, verrouille["1-1"].debut) == (MARDI, C2)

    libre = verifier_solution(instantane, resoudre_sans(instantane, C.VERROU), [C.VERROU])
    assert (libre["1-1"].jour, libre["1-1"].debut) == (LUNDI, C1)


def test_verrou_sans_salle_le_moteur_choisit_la_salle():
    f = Fabrique()
    f.salle("N101", capacite=10)
    grande = f.salle("N102", capacite=40)
    f.besoin([f.groupe("A1", 30)], [f.enseignant()])
    f.verrou("1-1", MARDI, C2)
    instantane = f.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert (places["1-1"].jour, places["1-1"].debut, places["1-1"].salle_id) == (MARDI, C2, grande)


def test_verrou_les_autres_seances_s_organisent_autour():
    f = Fabrique(creneaux=MATINEE)
    salle = f.salle()
    classe = f.groupe("A1")
    f.besoin([classe], [f.enseignant()])
    f.besoin([classe], [f.enseignant()])
    f.verrou("2-1", LUNDI, C1, salle)
    instantane = f.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert places["2-1"].debut == C1 and places["1-1"].debut == C2


def verrous_contradictoires():
    f = Fabrique(creneaux=MATINEE)
    salle = f.salle("N101")
    f.salle("N102")
    f.besoin([f.groupe("A1")], [f.enseignant()], libelle="Maths")
    f.besoin([f.groupe("A2")], [f.enseignant()], libelle="Physique")
    f.besoin([f.groupe("A3")], [f.enseignant()], libelle="Chimie")
    f.verrou("1-1", LUNDI, C1, salle)
    f.verrou("2-1", LUNDI, C1, salle)
    return f.instantane(), salle


def test_verrous_contradictoires_calcul_refuse_avec_la_liste():
    instantane, _ = verrous_contradictoires()
    resultat = resoudre(instantane, OPTIONS)
    assert resultat.statut is StatutSolveur.INFAISABLE
    assert resultat.statistiques["refuse"] is True
    assert "Maths" in resultat.message and "Physique" in resultat.message
    # Le conseil vient avec le conflit, une seule fois.
    assert resultat.message.count("tolérer les violations héritées") == 1


def test_verrous_contradictoires_toleres():
    instantane, salle = verrous_contradictoires()
    resultat = resoudre(instantane, options(tolerer_violations_heritees=True))
    assert resultat.statut is StatutSolveur.SOLUTION, resultat.message
    places = {p.seance_id: p for p in resultat.placements}
    assert places["1-1"].salle_id == places["2-1"].salle_id == salle
    assert places["1-1"].debut == places["2-1"].debut == C1
    # La séance libre, elle, ne partage la salle d'aucun verrou.
    libre = places["3-1"]
    assert not (libre.salle_id == salle and libre.debut == C1)
    assert any("tolérée" in avertissement for avertissement in resultat.avertissements)


def test_verrou_intenable_seul_est_refuse_nommement():
    f = Fabrique()
    petite = f.salle("N101", capacite=10)
    f.besoin([f.groupe("A1", 30)], [f.enseignant()], libelle="Maths")
    f.verrou("1-1", LUNDI, C1, petite)
    impossible(f.instantane(), "Maths", "N101", "30 places")


# --------------------------------------------------------------------------- #
# Cas limites (banque de tests : `pytest -m cas_limite`)
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
def test_deux_besoins_sans_groupe_sont_refuses_en_les_nommant():
    """Cas : deux besoins sans aucun groupe inscrit (données hors interface), d'enseignants
    différents, un seul créneau et deux salles.

    Risque : une donnée incomplète tolérée en silence, ou une exception brute. Le dépliage ne
    produit aucune séance pour un tel besoin et la pré-vérification refuse le calcul en nommant
    chaque besoin, au lieu de réunir les séances sans groupe en une unité fictive « aucun
    groupe » et de prouver une infaisabilité fausse.
    """
    f = Fabrique(creneaux=UN_CRENEAU)
    f.salle("N101")
    f.salle("N102")
    f.besoin([], [f.enseignant("Mme Martin")], effectif=10)
    f.besoin([], [f.enseignant("M. Durand")], effectif=10)
    instantane = f.instantane()
    assert instantane.seances == ()
    assert {probleme.besoin_id for probleme in instantane.problemes_depliage} == {1, 2}
    impossible(instantane, "Cours 1", "Cours 2", "aucun groupe n'est inscrit")


@pytest.mark.cas_limite
def test_seance_finissant_apres_minuit_refusee_meme_sans_horaires():
    """Cas : besoin de 25 h, ou besoin de 2 h au début imposé 23 h, la grille active puis ignorée.

    Risque : placement dont la fin dépasse minuit (occurrence débordant sur le lendemain, refusée
    ensuite par la base), ou refus muet qui ne dit pas pourquoi.
    """
    for champs in ({"duree": 25 * H}, {"jour_impose": LUNDI, "debut_impose": 23 * H}):
        f = Fabrique()
        f.salle()
        f.besoin([f.groupe("A1")], [f.enseignant()], **champs)
        instantane = f.instantane()
        impossible(instantane, "finirait après minuit")
        resultat = resoudre_sans(instantane, C.HORAIRES)
        assert resultat.statut is StatutSolveur.INFAISABLE, resultat.message
        assert resultat.placements == ()
        assert "finirait après minuit" in resultat.message
