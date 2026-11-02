"""Pré-vérifications : impossibilités certaines et situations tendues, dites en clair."""

from __future__ import annotations

import time

import pytest
from tests.solveur.fabrique import (
    C1,
    C2,
    LUNDI,
    MARDI,
    NIVEAUX_DURS,
    OPTIONS,
    Fabrique,
    H,
    grille,
    options,
    verifier_solution,
)
from tests.solveur.generateur import generer
from tests.solveur.test_niveaux import niveaux_avec

from edt.domaine.contraintes import CATALOGUE
from edt.domaine.contraintes import CodeContrainte as C
from edt.domaine.enumerations import CibleIndisponibilite, Gravite, ModeVolume
from edt.erreurs import ErreurDonneesInvalides
from edt.solveur import StatutSolveur, preverification, resoudre


def pre_verifier(instantane, contraintes_desactivees=(), **reglages):
    """Pré-vérifications avec toutes les contraintes nécessaires : ces tests parlent de règles dures."""
    return preverification.pre_verifier(
        instantane, contraintes_desactivees, niveaux=NIVEAUX_DURS, **reglages
    )


LUNDI_SEUL = grille(jours=[LUNDI], samedi=False)
"""Quatre créneaux : 8 h 30 de cours possibles (deux demi-journées de 4 h 15)."""


def erreurs(anomalies, code=None):
    return [a for a in anomalies if a.bloquante and (code is None or a.code is code)]


def avertissements(anomalies, code=None):
    return [
        a
        for a in anomalies
        if a.gravite is Gravite.AVERTISSEMENT and (code is None or a.code is code)
    ]


def test_un_probleme_sain_ne_releve_rien():
    f = Fabrique()
    f.salle()
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()], nombre=3)
    f.besoin([f.groupe("A2")], [f.enseignant()], nombre=3)
    assert pre_verifier(f.instantane()) == []


def test_charge_d_un_groupe():
    f = Fabrique(creneaux=LUNDI_SEUL)
    for _ in range(3):
        f.salle()
    classe = f.groupe("A2-G1")
    for _ in range(5):
        f.besoin([classe], [f.enseignant()])
    anomalies = pre_verifier(f.instantane())
    (erreur,) = erreurs(anomalies, C.GROUPE_UNIQUE)
    assert erreur.message == (
        "Le groupe A2-G1 demande 10 h de cours par semaine pour 8 h 30 disponibles : "
        "c'est impossible."
    )
    assert erreur.groupes == (classe,)


def test_charge_d_un_groupe_ne_vaut_que_si_la_grille_est_sure():
    """`HORAIRES` souple : les séances peuvent sortir de la grille, la borne n'est plus certaine."""
    f = Fabrique(creneaux=LUNDI_SEUL)
    for _ in range(3):
        f.salle()
    classe = f.groupe("A2-G1")
    for _ in range(5):
        f.besoin([classe], [f.enseignant()])
    instantane = f.instantane()
    assert erreurs(pre_verifier(instantane), C.GROUPE_UNIQUE)
    souple = preverification.pre_verifier(instantane, niveaux=niveaux_avec(HORAIRES="important"))
    assert not erreurs(souple), [a.message for a in erreurs(souple)]
    assert not erreurs(preverification.pre_verifier(instantane, [C.HORAIRES]))


def test_charge_d_un_groupe_tendue():
    f = Fabrique(creneaux=LUNDI_SEUL)
    for _ in range(3):
        f.salle()
    classe = f.groupe("A1")
    for _ in range(4):
        f.besoin([classe], [f.enseignant()])
    anomalies = pre_verifier(f.instantane())
    assert not erreurs(anomalies)
    (alerte,) = avertissements(anomalies, C.GROUPE_UNIQUE)
    assert "8 h de cours par semaine pour 8 h 30 disponibles" in alerte.message


def test_charge_d_un_groupe_compte_les_seances_de_sa_promotion_et_ses_indisponibilites():
    f = Fabrique(creneaux=LUNDI_SEUL)
    for _ in range(3):
        f.salle()
    promo = f.groupe("A1", 60)
    classe = f.groupe("A1-C1", 30, parent=promo)
    f.indisponibilite(CibleIndisponibilite.GROUPE, promo, jour=LUNDI, debut=13 * H)
    f.besoin([promo], [f.enseignant()], effectif=30)
    f.besoin([classe], [f.enseignant()], nombre=2)
    anomalies = pre_verifier(f.instantane())
    (erreur,) = erreurs(anomalies, C.GROUPE_UNIQUE)
    assert "Le groupe A1-C1 demande 6 h" in erreur.message
    assert "4 h 15 disponibles" in erreur.message


def test_charge_d_un_enseignant():
    f = Fabrique(creneaux=LUNDI_SEUL)
    for _ in range(3):
        f.salle()
    martin = f.enseignant("Mme Martin")
    f.indisponibilite(CibleIndisponibilite.ENSEIGNANT, martin, jour=LUNDI, fin=13 * H)
    for numero in range(3):
        f.besoin([f.groupe(f"A{numero}")], [martin])
    anomalies = pre_verifier(f.instantane())
    (erreur,) = erreurs(anomalies, C.ENSEIGNANT_UNIQUE)
    assert erreur.message == (
        "Mme Martin doit assurer 6 h de cours par semaine pour 4 h 15 disponibles (compte "
        "tenu de ses indisponibilités) : c'est impossible."
    )
    assert erreur.enseignants == (martin,)
    assert not erreurs(pre_verifier(f.instantane(), [C.DISPO_ENSEIGNANT]))


def test_maximum_hebdomadaire_d_un_enseignant():
    f = Fabrique()
    f.salle()
    martin = f.enseignant("Mme Martin", max_semaine=4 * H)
    f.besoin([f.groupe("A1")], [martin], nombre=3)
    (erreur,) = erreurs(pre_verifier(f.instantane()), C.MAX_ENSEIGNANT)
    assert erreur.message == (
        "Mme Martin doit assurer 6 h de cours par semaine pour un maximum de 4 h par semaine : "
        "c'est impossible."
    )
    assert pre_verifier(f.instantane(), [C.MAX_ENSEIGNANT]) == []


def test_seances_a_horaire_fixe_au_dela_du_maximum_journalier():
    f = Fabrique()
    f.salle()
    f.salle()
    martin = f.enseignant("Mme Martin", max_jour=2 * H)
    f.besoin([f.groupe("A1")], [martin], jour_impose=LUNDI, debut_impose=C1)
    f.besoin([f.groupe("A2")], [martin], jour_impose=LUNDI, debut_impose=14 * H)
    (erreur,) = erreurs(pre_verifier(f.instantane()), C.MAX_ENSEIGNANT)
    assert "Mme Martin a déjà 4 h de séances à horaire fixe" in erreur.message
    assert "le lundi, pour un maximum de 2 h par jour" in erreur.message


def test_demande_de_salles_rapportee_a_l_offre():
    f = Fabrique(creneaux=LUNDI_SEUL)
    f.salle("Labo", type_salle=2)
    f.salle("N101")
    f.salle("N102")
    for numero in range(5):
        f.besoin([f.groupe(f"A{numero}")], [f.enseignant()], types_salle_acceptes=frozenset({2}))
    anomalies = pre_verifier(f.instantane())
    (erreur,) = erreurs(anomalies, C.SALLE_UNIQUE)
    assert erreur.message == (
        "Les 5 séance(s) qui ne peuvent avoir lieu que dans la salle Labo demandent 10 h de "
        "cours par semaine pour 8 h 30 disponibles dans ces salles : c'est impossible."
    )
    assert not erreurs(pre_verifier(f.instantane(), [C.TYPE_SALLE]))


def test_seance_sans_salle_compatible_dit_quelle_exigence_elimine_les_salles():
    f = Fabrique()
    f.salle("N101", capacite=20, equipements=[1])
    f.salle("N102", capacite=60)
    f.besoin(
        [f.groupe("A1", 45)], [f.enseignant()], equipements_requis=frozenset({1}), libelle="TP"
    )
    (erreur,) = erreurs(pre_verifier(f.instantane()))
    assert erreur.code in (C.CAPACITE, C.EQUIPEMENT)
    assert "« TP » (séance 1-1) : aucune salle ne convient" in erreur.message
    assert "Sur 2 salle(s) : 1 d'au moins 45 places" in erreur.message
    assert "1 équipées de tout le matériel requis" in erreur.message
    assert "aucune ne réunit toutes ces exigences" in erreur.message
    assert erreur.seances == ("1-1",)


def test_seance_accessible_sans_salle_accessible():
    from edt.domaine.enumerations import Accessibilite

    f = Fabrique()
    f.salle("N101", accessibilite=Accessibilite.NON)
    f.besoin([f.groupe("A1", accessibilite=True)], [f.enseignant()])
    (erreur,) = erreurs(pre_verifier(f.instantane()))
    assert erreur.code is C.ACCESSIBILITE
    assert "requiert une salle accessible et aucune salle ne l'est" in erreur.message


def test_conflit_entre_verrous():
    f = Fabrique()
    salle = f.salle("N101")
    f.salle("N102")
    classe = f.groupe("A1")
    f.besoin([classe], [f.enseignant()], libelle="Maths")
    f.besoin([classe], [f.enseignant()], libelle="Physique")
    f.verrou("1-1", MARDI, C1, salle)
    f.verrou("2-1", MARDI, C1, salle)
    instantane = f.instantane()
    (erreur,) = erreurs(pre_verifier(instantane))
    assert erreur.seances == ("1-1", "2-1") and erreur.jour == MARDI
    assert "Les séances verrouillées « Maths »" in erreur.message
    for code in (C.SALLE_UNIQUE, C.GROUPE_UNIQUE):
        assert CATALOGUE[code].libelle in erreur.message

    toleres = pre_verifier(instantane, tolerer_violations_heritees=True)
    assert not erreurs(toleres)
    assert "tolérée" in avertissements(toleres)[0].message


def test_verrous_trop_rapproches_la_pause_compte():
    f = Fabrique()
    f.salle("N101")
    f.salle("N102")
    classe = f.groupe("A1")
    f.besoin([classe], [f.enseignant()], duree=2 * H + 10)
    f.besoin([classe], [f.enseignant()])
    f.verrou("1-1", MARDI, C1, 1)  # finit à 10 h 40, cinq minutes avant la suivante
    f.verrou("2-1", MARDI, C2, 2)
    (erreur,) = erreurs(pre_verifier(f.instantane()))
    assert erreur.code is C.PAUSE


def test_probleme_de_depliage():
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
    (erreur,) = erreurs(pre_verifier(instantane), C.VOLUME)
    assert "Projet" in erreur.message and "Corriger le besoin" in erreur.message
    assert not erreurs(pre_verifier(instantane, [C.VOLUME]))


def test_rapide_sur_un_etablissement_complet():
    """Un établissement complet (800 séances) : moins d'une seconde machine au repos (0,7 s) ;
    borne large pour une machine chargée, qui arrête quand même une explosion quadratique."""
    instantane, _ = generer(800, graine=1)
    debut = time.perf_counter()
    anomalies = pre_verifier(instantane)
    assert time.perf_counter() - debut < 5
    assert not erreurs(anomalies)  # le générateur produit des problèmes satisfiables


# --------------------------------------------------------------------------- #
# Cas limites (banque de tests : `pytest -m cas_limite`)
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
def test_une_seance_sans_horaire_ne_produit_pas_de_fausse_charge():
    """Cas : grille du lundi, une salle, l'enseignante indisponible tout le lundi, un besoin.

    Risque : trois impossibilités au lieu d'une, dont deux charges parasites « ... pour 0 min
    disponibles : c'est impossible » qui envoient corriger la charge du groupe ou de l'enseignant.
    """
    f = Fabrique(creneaux=LUNDI_SEUL)
    f.salle()
    martin = f.enseignant("Mme Martin")
    f.indisponibilite(CibleIndisponibilite.ENSEIGNANT, martin, jour=LUNDI)
    f.besoin([f.groupe("A1")], [martin])
    anomalies = pre_verifier(f.instantane())
    (erreur,) = erreurs(anomalies)
    assert erreur.code is C.DISPO_ENSEIGNANT and "indisponibilité de Mme Martin" in erreur.message
    assert not avertissements(anomalies)
    assert not any("0 min" in a.message for a in anomalies)


def _une_seance_verrouillee(jour=LUNDI, salle=1, duree=2 * H, duree_verrou=None):
    """Grille du lundi, une salle, une séance verrouillée à 8 h 30 telle quelle (sans validation)."""
    f = Fabrique(creneaux=LUNDI_SEUL)
    f.salle("N101")
    f.besoin([f.groupe("A1")], [f.enseignant("Mme Martin")], duree=duree)
    f.verrou("1-1", jour, C1, salle, fin=None if duree_verrou is None else C1 + duree_verrou)
    return f.instantane()


@pytest.mark.cas_limite
def test_verrou_incoherent_ou_contredisant_son_besoin_est_nomme():
    """Cas : séance verrouillée au jour 7, verrou sur une salle absente, verrou de 2 h sur un besoin
    passé à 1 h 30 (version importée ou besoin modifié après coup), avec et sans la grille.

    Risque : refus sans nommer la cause, charges parasites en prime, ou verrou appliqué en silence
    avec la mauvaise durée quand la grille (HORAIRES) est ignorée.
    """
    attendus = [
        (_une_seance_verrouillee(jour=7), "est incohérent"),
        (_une_seance_verrouillee(salle=99), "désigne une salle (n° 99) qui n'existe pas"),
        (
            _une_seance_verrouillee(duree=90, duree_verrou=120),
            "dure 2 h au lieu des 1 h 30 demandées",
        ),
    ]
    for instantane, attendu in attendus:
        anomalies = pre_verifier(instantane)
        (erreur,) = erreurs(anomalies)
        assert erreur.code is C.VERROU and attendu in erreur.message, erreur.message
        assert erreur.seances == ("1-1",)
        assert not avertissements(anomalies)
        resultat = resoudre(instantane, OPTIONS)
        assert resultat.statut is StatutSolveur.INFAISABLE and attendu in resultat.message
    # La grille ignorée, la durée du verrou reste opposée à celle du besoin : jamais de verrou
    # appliqué en silence avec une autre durée.
    instantane, attendu = attendus[2]
    (erreur,) = erreurs(pre_verifier(instantane, contraintes_desactivees={C.HORAIRES}))
    assert attendu in erreur.message
    resultat = resoudre(instantane, options(contraintes_desactivees=frozenset({C.HORAIRES})))
    assert resultat.statut is StatutSolveur.INFAISABLE and attendu in resultat.message


@pytest.mark.cas_limite
def test_indisponibilite_incoherente_refuse_le_calcul():
    """Cas : indisponibilité récurrente d'enseignant à début ≥ fin (13 h - 9 h) ou au jour 7
    (import ou saisie ancienne).

    Risque : indisponibilité ignorée en silence (jamais de recoupement) ou plantage ; le calcul
    doit être refusé en la nommant, en pré-vérification comme au calcul.
    """
    for champs in ({"jour": LUNDI, "debut": 13 * H, "fin": 9 * H}, {"jour": 7}):
        f = Fabrique()
        f.salle()
        martin = f.enseignant("Mme Martin")
        f.indisponibilite(CibleIndisponibilite.ENSEIGNANT, martin, **champs)
        f.besoin([f.groupe("A1")], [martin])
        instantane = f.instantane()
        attendu = "L'indisponibilité n° 1 est incohérente"
        with pytest.raises(ErreurDonneesInvalides, match=attendu):
            pre_verifier(instantane)
        with pytest.raises(ErreurDonneesInvalides, match=attendu):
            resoudre(instantane, OPTIONS)


@pytest.mark.cas_limite
def test_grille_vide_refusee_en_clair():
    """Cas : aucun créneau enregistré et un besoin à placer, la grille active puis ignorée.

    Risque : refus incompréhensible ou plantage (minimum d'une liste vide) ; la grille ignorée,
    une séance posée un jour sans créneau, que le vérificateur refuse (« défaut du moteur »).
    """
    f = Fabrique(creneaux=())
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()])
    instantane = f.instantane()
    (erreur,) = erreurs(pre_verifier(instantane))
    assert erreur.code is C.JOURS
    assert "la grille horaire n'offre aucun jour de cours permis" in erreur.message
    resultat = resoudre(instantane, OPTIONS)
    assert resultat.statut is StatutSolveur.INFAISABLE
    assert "la grille horaire n'offre aucun jour de cours permis" in resultat.message
    # La grille ignorée et les jours de cours (JOURS) toujours exigés : aucun jour n'a de
    # créneau, le refus demeure ; JOURS ignorée aussi, une solution existe et passe l'oracle.
    sans_grille = options(contraintes_desactivees=frozenset({C.HORAIRES}))
    assert resoudre(instantane, sans_grille).statut is StatutSolveur.INFAISABLE
    libre = options(contraintes_desactivees=frozenset({C.HORAIRES, C.JOURS}))
    verifier_solution(instantane, resoudre(instantane, libre), {C.HORAIRES, C.JOURS})
