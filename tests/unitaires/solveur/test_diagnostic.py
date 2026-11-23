"""Diagnostic d'un calcul impossible : isolement d'un ensemble irréductible de règles et de données.

Petits instantanés impossibles fabriqués en mémoire (`tests.outils.solveur.fabrique`), sans base.
Chaque cas mêle au noyau impossible des éléments sans rapport (un besoin, une indisponibilité,
une règle d'un autre enseignant ou d'un autre groupe) : le diagnostic doit rendre le noyau, et lui
seul. Les tests ne dépendent pas de l'ordre des essais ni de la solution trouvée par le moteur,
seulement du résultat promis par le contrat (PLAN P9, §3 et §4). Chaque ensemble attendu a été
vérifié irréductible au moteur (impossible avec ses seuls éléments, possible sans l'un d'eux) ;
quand deux ensembles irréductibles existent, le test accepte l'un ou l'autre.
"""

from __future__ import annotations

import math
import time

import pytest
from tests.outils.solveur.fabrique import (
    C1,
    LUNDI,
    MARDI,
    Fabrique,
    H,
    grille,
    options,
)

from edt.domaine.contraintes import CodeContrainte, Niveau, niveaux_par_defaut
from edt.domaine.enumerations import CibleIndisponibilite
from edt.domaine.instantane import Instantane
from edt.domaine.regles import TypeRegle
from edt.erreurs import ErreurDonneesInvalides
from edt.solveur import OptionsSolveur, ResultatSolveur, StatutSolveur, resoudre
from edt.solveur.diagnostic import StatutDiagnostic, isoler_impossibilite

BUDGET_S = 20.0
"""Budget des cas qui doivent conclure : large devant la durée réelle (quelques dixièmes)."""

OPTIONS_DIAGNOSTIC = options(niveaux=niveaux_par_defaut())
"""Niveaux par défaut de l'application : les contraintes souples gardent leur niveau dans les
essais et ne sont jamais candidates (décision D3)."""


def _ids(elements, nature: str) -> set[str]:
    return {e.identifiant for e in elements if e.nature == nature}


def _seances(instantane: Instantane, besoin_id: int) -> list[str]:
    return [s.id for s in instantane.seances if s.besoin.id == besoin_id]


# --------------------------------------------------------------------------- #
# Instantanés impossibles : un noyau connu et des éléments sans rapport
# --------------------------------------------------------------------------- #


def _enseignant_surcharge() -> tuple[Instantane, dict[str, int]]:
    """Mme Martin a 4 créneaux (le lundi) et deux besoins de 3 et 2 séances : 5 > 4.

    Chaque besoin seul tient. Sans rapport : un cours de M. Petit et son indisponibilité du lundi
    après-midi (une séance, deux créneaux du matin restent libres).
    """
    f = Fabrique(creneaux=grille([LUNDI], samedi=False))
    g1, g2, g3 = f.groupe("G1"), f.groupe("G2"), f.groupe("G3")
    martin, petit = f.enseignant("Mme Martin"), f.enseignant("M. Petit")
    for _ in range(3):
        f.salle()
    a = f.besoin([g1], [martin], nombre=3, libelle="TD Analyse - G1")
    b = f.besoin([g2], [martin], nombre=2, libelle="TD Algèbre - G2")
    c = f.besoin([g3], [petit], nombre=1, libelle="TD Anglais - G3")
    f.indisponibilite(CibleIndisponibilite.ENSEIGNANT, petit, jour=LUNDI, debut=13 * H)
    return f.instantane(), {"a": a, "b": b, "c": c}


def _verrous_en_conflit() -> tuple[Instantane, dict[str, object]]:
    """Deux séances de G1 verrouillées toutes deux le lundi à 8 h 30 : le groupe ne peut pas.

    Sans rapport : un troisième cours de G2, non verrouillé.
    """
    f = Fabrique(creneaux=grille([LUNDI, MARDI], samedi=False))
    g1, g2 = f.groupe("G1"), f.groupe("G2")
    martin, petit, roux = (f.enseignant(n) for n in ("Mme Martin", "M. Petit", "Mme Roux"))
    for _ in range(3):
        f.salle()
    a = f.besoin([g1], [martin], libelle="TD Analyse - G1")
    b = f.besoin([g1], [petit], libelle="TD Physique - G1")
    c = f.besoin([g2], [roux], libelle="TD Anglais - G2")
    brouillon = f.instantane()
    (seance_a,) = _seances(brouillon, a)
    (seance_b,) = _seances(brouillon, b)
    f.verrou(seance_a, LUNDI, C1, salle=1)
    f.verrou(seance_b, LUNDI, C1, salle=2)
    return f.instantane(), {"a": a, "b": b, "c": c, "seance_a": seance_a, "seance_b": seance_b}


def _indisponibilite_bloquante() -> tuple[Instantane, dict[str, int]]:
    """Mme Martin est absente le lundi matin et n'a que le lundi : 2 créneaux pour 3 séances.

    Sans rapport : M. Petit, absent le lundi après-midi, qui n'a qu'une séance.
    """
    f = Fabrique(creneaux=grille([LUNDI], samedi=False))
    g1, g2 = f.groupe("G1"), f.groupe("G2")
    martin, petit = f.enseignant("Mme Martin"), f.enseignant("M. Petit")
    for _ in range(2):
        f.salle()
    a = f.besoin([g1], [martin], nombre=3, libelle="TD Analyse - G1")
    c = f.besoin([g2], [petit], nombre=1, libelle="TD Anglais - G2")
    f.indisponibilite(CibleIndisponibilite.ENSEIGNANT, martin, jour=LUNDI, debut=0, fin=13 * H)
    f.indisponibilite(CibleIndisponibilite.ENSEIGNANT, petit, jour=LUNDI, debut=13 * H)
    return f.instantane(), {"a": a, "c": c, "indispo_martin": 1, "indispo_petit": 2}


def _regle_necessaire() -> tuple[Instantane, dict[str, int]]:
    """La grille n'a que le lundi ; une règle nécessaire fait travailler G1 le mardi seulement.

    Sans rapport : une règle qui fait travailler G2 le lundi (satisfaite) et le cours de G2.
    """
    f = Fabrique(creneaux=grille([LUNDI], samedi=False))
    g1, g2 = f.groupe("G1"), f.groupe("G2")
    martin, petit = f.enseignant("Mme Martin"), f.enseignant("M. Petit")
    for _ in range(2):
        f.salle()
    a = f.besoin([g1], [martin], libelle="TD Analyse - G1")
    c = f.besoin([g2], [petit], libelle="TD Anglais - G2")
    regle_g1 = f.regle(
        TypeRegle.JOURS_TRAVAILLES, "groupe", g1, niveau="necessaire", jours=["mardi"]
    )
    regle_g2 = f.regle(
        TypeRegle.JOURS_TRAVAILLES, "groupe", g2, niveau="necessaire", jours=["lundi"]
    )
    return f.instantane(), {"a": a, "c": c, "regle_g1": regle_g1, "regle_g2": regle_g2}


def _possible() -> Instantane:
    f = Fabrique(creneaux=grille([LUNDI], samedi=False))
    g1 = f.groupe("G1")
    martin = f.enseignant("Mme Martin")
    f.salle()
    f.besoin([g1], [martin], nombre=2)
    return f.instantane()


# --------------------------------------------------------------------------- #
# Noyau exact, statut MINIMAL
# --------------------------------------------------------------------------- #


def test_noyau_minimal_de_deux_besoins_sur_un_meme_enseignant():
    instantane, ids = _enseignant_surcharge()
    assert resoudre(instantane, OPTIONS_DIAGNOSTIC).statut is StatutSolveur.INFAISABLE

    resultat = isoler_impossibilite(instantane, OPTIONS_DIAGNOSTIC, BUDGET_S)

    assert resultat.statut is StatutDiagnostic.MINIMAL, resultat.message
    # Sans la contrainte des horaires de la grille, une séance se placerait entre deux créneaux :
    # elle fait partie de l'ensemble, comme les deux besoins.
    assert [(e.nature, e.identifiant) for e in resultat.regles] == [("contrainte", "HORAIRES")]
    assert [(e.nature, e.identifiant) for e in resultat.donnees] == [
        ("besoin", str(ids["a"])),
        ("besoin", str(ids["b"])),
    ]
    libelles = " ".join(e.libelle for e in resultat.donnees)
    assert "TD Analyse - G1" in libelles and "TD Algèbre - G2" in libelles
    assert resultat.nb_essais >= 3  # essai complet, puis au moins un retrait par élément gardé
    assert 0 < resultat.duree_s <= BUDGET_S
    assert resultat.message


def test_noyau_minimal_de_deux_verrous_en_conflit():
    instantane, ids = _verrous_en_conflit()

    resultat = isoler_impossibilite(instantane, OPTIONS_DIAGNOSTIC, BUDGET_S)

    assert resultat.statut is StatutDiagnostic.MINIMAL, resultat.message
    assert resultat.regles == ()
    # Ordre du contrat : besoins puis verrous, chacun par identifiant.
    assert [(e.nature, e.identifiant) for e in resultat.donnees] == [
        ("besoin", str(ids["a"])),
        ("besoin", str(ids["b"])),
        ("verrou", ids["seance_a"]),
        ("verrou", ids["seance_b"]),
    ]


def test_noyau_minimal_avec_une_indisponibilite_et_sa_contrainte():
    instantane, ids = _indisponibilite_bloquante()

    resultat = isoler_impossibilite(instantane, OPTIONS_DIAGNOSTIC, BUDGET_S)

    assert resultat.statut is StatutDiagnostic.MINIMAL, resultat.message
    # Ordre du catalogue : les horaires de la grille, puis la disponibilité des enseignants.
    assert [(e.nature, e.identifiant) for e in resultat.regles] == [
        ("contrainte", "HORAIRES"),
        ("contrainte", "DISPO_ENSEIGNANT"),
    ]
    assert [(e.nature, e.identifiant) for e in resultat.donnees] == [
        ("besoin", str(ids["a"])),
        ("indisponibilite", str(ids["indispo_martin"])),
    ]


def test_noyau_minimal_avec_une_regle_personnalisee_necessaire():
    instantane, ids = _regle_necessaire()

    resultat = isoler_impossibilite(instantane, OPTIONS_DIAGNOSTIC, BUDGET_S)

    assert resultat.statut is StatutDiagnostic.MINIMAL, resultat.message
    # Deux ensembles irréductibles existent (vérifiés au moteur) : sans les horaires de la grille
    # ni les jours de cours, des débuts s'ouvrent le mardi et la règle se satisfait. La règle et
    # l'une des deux contraintes, au choix de l'ordre des essais, forment l'ensemble.
    regles = [(e.nature, e.identifiant) for e in resultat.regles]
    assert regles in (
        [("contrainte", "HORAIRES"), ("regle", str(ids["regle_g1"]))],
        [("contrainte", "JOURS"), ("regle", str(ids["regle_g1"]))],
    )
    assert [(e.nature, e.identifiant) for e in resultat.donnees] == [("besoin", str(ids["a"]))]


# --------------------------------------------------------------------------- #
# Instance possible, budget épuisé, essai sans conclusion
# --------------------------------------------------------------------------- #


def test_instance_possible_donne_possible_sans_element():
    resultat = isoler_impossibilite(_possible(), OPTIONS_DIAGNOSTIC, BUDGET_S)

    assert resultat.statut is StatutDiagnostic.POSSIBLE
    assert resultat.regles == () and resultat.donnees == ()
    assert resultat.nb_essais == 1
    assert resultat.message


@pytest.mark.cas_limite
def test_budget_minuscule_rend_indetermine_sans_depasser_le_budget():
    """Cas : budget de 0,01 s, trop court pour un seul essai utile.

    Risque : un diagnostic qui lance quand même un essai dépasse son budget (la page attend au-delà
    du délai du serveur frontal) ou prétend un résultat sans l'avoir établi.
    """
    instantane, _ = _enseignant_surcharge()
    debut = time.perf_counter()

    resultat = isoler_impossibilite(instantane, OPTIONS_DIAGNOSTIC, 0.01)

    assert time.perf_counter() - debut < 1.0
    assert resultat.statut in (StatutDiagnostic.INDETERMINE, StatutDiagnostic.PARTIEL)
    assert resultat.duree_s <= 0.01
    if resultat.statut is StatutDiagnostic.INDETERMINE:
        assert resultat.regles == () and resultat.donnees == ()


@pytest.mark.cas_limite
def test_essais_lents_arretes_au_budget_marge_comprise():
    """Cas : chaque essai de retrait consomme tout le temps qu'on lui donne sans conclure.

    Risque : sans arrêt au budget (marge comprise, décision D1), la requête de la page dépasse le
    délai du serveur frontal et l'utilisateur reçoit une erreur au lieu d'un résultat partiel.
    """
    instantane, _ = _enseignant_surcharge()
    budget = 1.5
    temps_donnes: list[float] = []

    def essai_lent(filtre: Instantane, opts: OptionsSolveur) -> ResultatSolveur:
        temps_donnes.append(opts.temps_limite_s)
        if len(filtre.seances) == len(instantane.seances) and not temps_donnes[1:]:
            return resoudre(filtre, opts)  # l'essai complet conclut : impossible
        time.sleep(opts.temps_limite_s)
        return ResultatSolveur(StatutSolveur.TEMPS_DEPASSE)

    debut = time.perf_counter()
    resultat = isoler_impossibilite(instantane, OPTIONS_DIAGNOSTIC, budget, essai=essai_lent)
    ecoule = time.perf_counter() - debut

    assert resultat.statut is StatutDiagnostic.PARTIEL, resultat.message
    assert resultat.duree_s <= budget
    assert ecoule <= budget + 0.5  # le temps hors essais (filtrage, journal) reste petit
    assert all(0 < t <= budget for t in temps_donnes)
    assert sum(temps_donnes[1:]) <= budget
    # Rien n'a pu être retiré : l'ensemble reste celui de départ, toujours impossible.
    assert resultat.donnees


@pytest.mark.cas_limite
def test_essai_sans_conclusion_garde_l_element_et_rend_partiel():
    """Cas : le retrait d'un élément sans rapport (le cours de G3) ne conclut pas dans son temps.

    Risque : un essai sans conclusion pris pour « pas nécessaire » ou « nécessaire » donnerait un
    ensemble faux, ou annoncé MINIMAL sans preuve.
    """
    instantane, ids = _enseignant_surcharge()
    seance_c = set(_seances(instantane, ids["c"]))

    def essai(filtre: Instantane, opts: OptionsSolveur) -> ResultatSolveur:
        if not seance_c & {s.id for s in filtre.seances}:
            return ResultatSolveur(StatutSolveur.TEMPS_DEPASSE)
        return resoudre(filtre, opts)

    resultat = isoler_impossibilite(instantane, OPTIONS_DIAGNOSTIC, BUDGET_S, essai=essai)

    assert resultat.statut is StatutDiagnostic.PARTIEL, resultat.message
    besoins = _ids(resultat.donnees, "besoin")
    # Le noyau reste, et l'élément dont le retrait n'a pas conclu est gardé.
    assert {str(ids["a"]), str(ids["b"]), str(ids["c"])} <= besoins


@pytest.mark.cas_limite
def test_premier_essai_sans_conclusion_rend_indetermine():
    """Cas : l'essai complet lui-même ne conclut pas (grand établissement, budget court).

    Risque : un diagnostic qui poursuit sans preuve d'impossibilité rendrait des « causes »
    inventées.
    """
    instantane, _ = _enseignant_surcharge()

    resultat = isoler_impossibilite(
        instantane,
        OPTIONS_DIAGNOSTIC,
        BUDGET_S,
        essai=lambda filtre, opts: ResultatSolveur(StatutSolveur.TEMPS_DEPASSE),
    )

    assert resultat.statut is StatutDiagnostic.INDETERMINE
    assert resultat.regles == () and resultat.donnees == ()
    assert resultat.nb_essais == 1
    assert resultat.message


@pytest.mark.cas_limite
def test_contrainte_souple_gardee_a_son_niveau_dans_les_essais():
    """Cas : horaires de la grille au niveau souple ; le calcul reste impossible (décision D3).

    Risque : des essais qui retirent les contraintes souples au lieu de les garder à leur niveau
    rendraient POSSIBLE un calcul vraiment impossible, ou citeraient une règle souple en cause.
    """
    instantane, ids = _enseignant_surcharge()
    souple = options(niveaux={**niveaux_par_defaut(), CodeContrainte.HORAIRES: Niveau.IMPORTANT})
    assert resoudre(instantane, souple).statut is StatutSolveur.INFAISABLE

    resultat = isoler_impossibilite(instantane, souple, BUDGET_S)

    assert resultat.statut is StatutDiagnostic.MINIMAL, resultat.message
    assert resultat.regles == ()
    assert _ids(resultat.donnees, "besoin") == {str(ids["a"]), str(ids["b"])}


@pytest.mark.cas_limite
@pytest.mark.parametrize("budget", [0, -1, math.nan, math.inf])
def test_budget_non_fini_ou_negatif_refuse(budget):
    """Cas : budget nul, négatif, NaN ou infini passé à la fonction du solveur.

    Risque : un budget infini ou NaN supprime la borne de temps ; un budget négatif rendrait un
    résultat sans aucun essai présenté comme un diagnostic.
    """
    instantane, _ = _enseignant_surcharge()
    with pytest.raises(ErreurDonneesInvalides):
        isoler_impossibilite(instantane, OPTIONS_DIAGNOSTIC, budget)
