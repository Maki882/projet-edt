"""Règles personnalisées dans le solveur (temps A) : poseurs, réglages par personne, niveaux.

Chaque poseur du temps A est éprouvé des deux côtés : la règle nécessaire
interdit (calcul refusé ou impossible, en nommant la règle), la règle
souple est comptée (`statistiques["entorses_regles"]`, message). Les
réglages par personne (déjeuner, pause) viennent de `Instantane.reglages` :
deux groupes peuvent déjeuner différemment. L'oracle `verifier_solution`
vérifie les règles aux niveaux retenus par le moteur.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest
from sqlalchemy import select
from tests.solveur.fabrique import (
    C1,
    C2,
    C3,
    C4,
    LUNDI,
    MARDI,
    MERCREDI,
    OPTIONS,
    RENTREE,
    SAMEDI,
    Fabrique,
    H,
    grille,
    options,
    verifier_solution,
)
from tests.solveur.test_couche_datee import SEMAINE, place
from typer.testing import CliRunner

from edt import cli
from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.contraintes import Niveau
from edt.domaine.enumerations import CibleIndisponibilite, DemiJournee, EtatCalcul
from edt.domaine.instantane import Creneau
from edt.domaine.regles import NaturePortee, TypeRegle, types_non_couverts
from edt.services import generation
from edt.services.regles import charger_regles_actives
from edt.solveur import OptionsSolveur, StatutSolveur, resoudre
from edt.solveur.instanciation import instancier
from edt.solveur.personnalisees import POSEURS
from edt.solveur.preverification import pre_verifier
from edt.solveur.replacement import OptionsReplacement, replacer

TEMPS_B = frozenset(
    {
        TypeRegle.MAX_PAR_JOUR,
        TypeRegle.JOURNEES_LIBRES,
        TypeRegle.SANS_TROU,
        TypeRegle.CAMPUS_PAR_JOUR,
        TypeRegle.MATIERES_PAR_JOUR,
        TypeRegle.ORDRE_SEMAINE,
        TypeRegle.REPARTITION_MATIERE,
    }
)

LUNDI_SEUL = grille(jours=[LUNDI], samedi=False)
"""Quatre créneaux le lundi : 8 h 30, 10 h 45, 13 h 45 et 16 h."""


def refuse(instantane, *mots: str):
    """Le calcul est impossible (prouvé ou refusé d'avance) et le message nomme `mots`."""
    resultat = resoudre(instantane, OPTIONS)
    assert resultat.statut is StatutSolveur.INFAISABLE, resultat.message
    texte = resultat.message + " ".join(resultat.avertissements)
    for mot in mots:
        assert mot in texte, f"« {mot} » absent de : {texte}"
    return resultat


def souple(instantane, regle_id: int, niveau: str = "important"):
    """Résout avec la règle `regle_id` au niveau souple `niveau`, vérifie et rend le résultat."""
    niveaux = {regle.id: regle.niveau for regle in instantane.regles}
    niveaux[regle_id] = Niveau(niveau)
    resultat = resoudre(instantane, options(niveaux_regles=niveaux))
    assert resultat.statut is StatutSolveur.SOLUTION, resultat.message
    verifier_solution(instantane, resultat)
    assert resultat.niveaux_regles == niveaux
    return resultat


# --------------------------------------------------------------------------- #
# Registre et options
# --------------------------------------------------------------------------- #


def test_les_types_du_temps_a_ont_un_poseur():
    assert types_non_couverts("solveur") == TEMPS_B
    assert set(POSEURS) == set(TypeRegle) - TEMPS_B


def test_une_regle_d_un_type_sans_poseur_fait_refuser_le_calcul():
    f = Fabrique(creneaux=LUNDI_SEUL)
    f.salle()
    classe = f.groupe("A1")
    f.besoin([classe], [f.enseignant()])
    f.regle(TypeRegle.MAX_PAR_JOUR, NaturePortee.GROUPE, classe, maximum=360)
    instantane = f.instantane()
    resultat = refuse(instantane, "R001", "ne sait pas encore poser", "Maximum par jour")
    assert resultat.statistiques["refuse"] is True
    (anomalie,) = [a for a in pre_verifier(instantane) if a.bloquante]
    assert (anomalie.regle_id, anomalie.regle_code) == (1, "R001")
    # Ignorée pour ce calcul, la règle ne gêne plus.
    ignoree = resoudre(instantane, options(niveaux_regles={}))
    assert ignoree.statut is StatutSolveur.SOLUTION, ignoree.message
    assert ignoree.niveaux_regles == {}


def test_options_niveaux_regles_aller_retour():
    reglages = OptionsSolveur(niveaux_regles={3: "facultatif", "7": Niveau.NECESSAIRE})
    assert reglages.niveaux_regles == {3: Niveau.FACULTATIF, 7: Niveau.NECESSAIRE}
    enregistre = reglages.vers_dict()
    assert enregistre["niveaux_regles"] == {"3": "facultatif", "7": "necessaire"}
    assert OptionsSolveur.lire_niveaux_regles(enregistre) == reglages.niveaux_regles
    assert OptionsSolveur().vers_dict()["niveaux_regles"] is None
    assert OptionsSolveur.lire_niveaux_regles({}) is None
    with pytest.raises(Exception, match="illisibles"):
        OptionsSolveur.lire_niveaux_regles({"niveaux_regles": ["3"]})


# --------------------------------------------------------------------------- #
# Jours travaillés
# --------------------------------------------------------------------------- #


def _lundi_ferme_a_la_classe() -> tuple:
    """Une classe qui ne travaille pas le lundi, sur une grille qui n'a que le lundi."""
    f = Fabrique(creneaux=LUNDI_SEUL)
    f.salle()
    classe = f.groupe("A1")
    f.besoin([classe], [f.enseignant()])
    regle = f.regle(
        TypeRegle.JOURS_TRAVAILLES,
        NaturePortee.GROUPE,
        classe,
        jours=["mardi", "mercredi", "jeudi", "vendredi"],
    )
    return f.instantane(), regle


def test_jours_travailles_ferme_un_jour():
    instantane, regle = _lundi_ferme_a_la_classe()
    assert instantane.regles[0].phrase == "La classe A1 ne travaille pas le lundi."
    refuse(instantane, "règle R001", "ne travaille pas le lundi", "aucun horaire possible")

    resultat = souple(instantane, regle)
    assert resultat.statistiques["entorses_regles"] == {"R001": 1}
    assert resultat.statistiques["entorses"] == {}
    assert "1 entorse à la règle R001 (La classe A1 ne travaille pas le lundi.)" in resultat.message

    # Ignorée pour ce calcul : le standard (lundi à vendredi) reprend sa place.
    ignoree = resoudre(instantane, options(niveaux_regles={}))
    assert ignoree.statut is StatutSolveur.SOLUTION, ignoree.message
    assert ignoree.statistiques["entorses_regles"] == {}


def test_jours_travailles_ouvre_le_samedi_a_un_groupe():
    """Le standard ferme le samedi ; une règle l'ouvre à la classe, son enseignant sans règle suit."""
    samedi = grille(jours=[], samedi=True)
    f = Fabrique(creneaux=samedi)
    f.salle()
    classe = f.groupe("A1")
    f.besoin([classe], [f.enseignant()])
    refuse(f.instantane(), "aucun jour de cours permis")

    f.regle(TypeRegle.JOURS_TRAVAILLES, NaturePortee.GROUPE, classe, jours=["samedi"])
    instantane = f.instantane()
    assert instantane.regles[0].phrase == "La classe A1 ne travaille que le samedi."
    resultat = resoudre(instantane, OPTIONS)
    places = verifier_solution(instantane, resultat)
    assert places["1-1"].jour == SAMEDI
    assert resultat.statistiques["entorses_regles"] == {}

    # Son enseignant ferme le samedi par une règle : les deux doivent être satisfaites.
    f.regle(TypeRegle.JOURS_TRAVAILLES, NaturePortee.ENSEIGNANT, 1, jours=["lundi", "mardi"])
    refuse(f.instantane(), "règle R002")


def test_jours_travailles_par_campus():
    """Le samedi n'est ouvert qu'à Issy : la séance s'y place ; sans salle à Issy, impossible."""
    samedi = grille(jours=[], samedi=True)
    f = Fabrique(creneaux=samedi)
    ndc, issy = f.campus("NDC"), f.campus("ISSY")
    f.salle("N101", campus=ndc)
    f.salle("I101", campus=issy)
    classe = f.groupe("A1")
    f.besoin([classe], [f.enseignant()])
    f.regle(TypeRegle.JOURS_TRAVAILLES, NaturePortee.CAMPUS, issy, jours=["samedi"])
    instantane = f.instantane()
    resultat = resoudre(instantane, OPTIONS)
    places = verifier_solution(instantane, resultat)
    assert (places["1-1"].jour, places["1-1"].salle_id) == (SAMEDI, 2)

    g = Fabrique(creneaux=samedi)
    ndc, issy = g.campus("NDC"), g.campus("ISSY")
    g.salle("N101", campus=ndc)
    classe = g.groupe("A1")
    g.besoin([classe], [g.enseignant()])
    g.regle(TypeRegle.JOURS_TRAVAILLES, NaturePortee.CAMPUS, issy, jours=["samedi"])
    refuse(g.instantane(), "aucun jour de cours permis")


# --------------------------------------------------------------------------- #
# Horaires et créneaux interdits
# --------------------------------------------------------------------------- #


def _quatre_seances_le_lundi() -> Fabrique:
    f = Fabrique(creneaux=LUNDI_SEUL)
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()], nombre=4)
    return f


def test_plage_horaire():
    f = _quatre_seances_le_lundi()
    regle = f.regle(TypeRegle.PLAGE_HORAIRE, NaturePortee.GROUPE, 1, debut="10:00")
    instantane = f.instantane()
    assert "commence" in instantane.regles[0].phrase or "10 h" in instantane.regles[0].phrase
    refuse(instantane)  # quatre séances pour trois créneaux après 10 h : impossible, prouvé

    resultat = souple(instantane, regle, "facultatif")
    assert resultat.statistiques["entorses_regles"] == {"R001": 1}
    places = verifier_solution(instantane, resultat)
    assert sorted(p.debut for p in places.values()) == [C1, C2, C3, C4]

    # Une seule séance : la règle écarte simplement le créneau de 8 h 30.
    g = Fabrique(creneaux=LUNDI_SEUL)
    g.salle()
    g.besoin([g.groupe("A1")], [g.enseignant()])
    g.regle(TypeRegle.PLAGE_HORAIRE, NaturePortee.GROUPE, 1, debut="10:00", fin="13:00")
    instantane = g.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert places["1-1"].debut == C2


def test_creneaux_interdits_sur_un_type_de_seance():
    f = _quatre_seances_le_lundi()
    regle = f.regle(
        TypeRegle.CRENEAUX_INTERDITS,
        NaturePortee.TYPE_SEANCE,
        1,
        jours=["lundi"],
        debut="13:00",
        fin="18:00",
    )
    instantane = f.instantane()
    refuse(instantane)  # impossible, prouvé par le modèle

    resultat = souple(instantane, regle)
    assert resultat.statistiques["entorses_regles"] == {
        "R001": 2
    }  # les deux séances de l'après-midi
    assert "2 entorses à la règle R001" in resultat.message


# --------------------------------------------------------------------------- #
# Campus et salles
# --------------------------------------------------------------------------- #


def _deux_campus() -> Fabrique:
    f = Fabrique(creneaux=LUNDI_SEUL[:1])
    ndc, issy = f.campus("NDC"), f.campus("ISSY")
    f.salle("N101", campus=ndc)
    f.salle("I101", campus=issy)
    f.besoin([f.groupe("A1")], [f.enseignant()])
    return f


def test_campus_impose_ou_interdit():
    f = _deux_campus()
    f.regle(TypeRegle.CAMPUS, NaturePortee.GROUPE, 1, mode="imposer", campus=["ISSY"])
    instantane = f.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert places["1-1"].salle_id == 2

    g = _deux_campus()
    g.regle(TypeRegle.CAMPUS, NaturePortee.GROUPE, 1, mode="interdire", campus=["ISSY"])
    instantane = g.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert places["1-1"].salle_id == 1

    # Campus imposé sans salle : refus en dur qui nomme la règle, entorse en souple.
    h = Fabrique(creneaux=LUNDI_SEUL[:1])
    h.campus("NDC")
    issy = h.campus("ISSY")
    h.salle("N101", campus=1)
    h.besoin([h.groupe("A1")], [h.enseignant()])
    regle = h.regle(TypeRegle.CAMPUS, NaturePortee.GROUPE, 1, mode="imposer", campus=["ISSY"])
    assert issy == 2
    instantane = h.instantane()
    refuse(instantane, "règle R001", "aucune salle ne convient")
    resultat = souple(instantane, regle)
    assert resultat.statistiques["entorses_regles"] == {"R001": 1}


def test_salles_imposees_ou_interdites():
    f = Fabrique(creneaux=LUNDI_SEUL[:1])
    f.salle("N101")
    f.salle("N102")
    f.besoin([f.groupe("A1")], [f.enseignant()])
    f.regle(TypeRegle.SALLES, NaturePortee.GROUPE, 1, mode="imposer", salles=["N102"])
    instantane = f.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert places["1-1"].salle_id == 2

    # La salle imposée est indisponible : impossible en dur, une entorse (phase des salles) en souple.
    g = Fabrique(creneaux=LUNDI_SEUL[:1])
    g.salle("N101")
    g.salle("N102")
    g.besoin([g.groupe("A1")], [g.enseignant()])
    g.indisponibilite(CibleIndisponibilite.SALLE, 2, jour=LUNDI)
    regle = g.regle(TypeRegle.SALLES, NaturePortee.GROUPE, 1, mode="imposer", salles=["N102"])
    instantane = g.instantane()
    refuse(instantane, "aucune salle n'est libre")
    resultat = souple(instantane, regle)
    assert resultat.statistiques["entorses_regles"] == {"R001": 1}
    assert verifier_solution(instantane, resultat)["1-1"].salle_id == 1


# --------------------------------------------------------------------------- #
# Réglages par personne : déjeuner et pause
# --------------------------------------------------------------------------- #

AUTOUR_DE_MIDI = (
    Creneau(LUNDI, 11 * H, 13 * H, DemiJournee.MATIN),
    Creneau(LUNDI, 13 * H + 45, 15 * H + 45, DemiJournee.APRES_MIDI),
)


def test_pause_dejeuner_reglee_par_groupe():
    """A1 a une pause libre d'une heure entre 12 h et 14 h ; A2 suit l'établissement (aucune)."""
    f = Fabrique(creneaux=AUTOUR_DE_MIDI, dejeuner_mode="aucun")
    f.salle()
    f.salle()
    a1, a2 = f.groupe("A1"), f.groupe("A2")
    f.besoin([a1], [f.enseignant()], nombre=2)
    f.besoin([a2], [f.enseignant()], nombre=2)
    regle = f.regle(
        TypeRegle.PAUSE_DEJEUNER,
        NaturePortee.GROUPE,
        a1,
        mode="libre",
        debut="12:00",
        fin="14:00",
        duree=60,
        niveau="necessaire",
    )
    instantane = f.instantane()
    assert instantane.regles[0].phrase == (
        "La classe A1 a une pause déjeuner libre d'au moins 1 h entre 12 h 00 et 14 h 00."
    )
    refuse(instantane)  # A1 : 11 h-13 h et 13 h 45-15 h 45 ne laissent que 45 min

    resultat = souple(instantane, regle)
    assert resultat.statistiques["entorses_regles"] == {"R001": 1}  # A1, pas A2 ni les enseignants
    assert resultat.statistiques["entorses"] == {}
    assert "1 entorse à la règle R001" in resultat.message

    # Ignorée pour ce calcul : A1 revient au réglage de l'établissement (aucune pause).
    ignoree = resoudre(instantane, options(niveaux_regles={}))
    assert ignoree.statut is StatutSolveur.SOLUTION
    assert ignoree.statistiques["entorses_regles"] == {}


def test_pause_dejeuner_fixe_par_enseignant():
    """M. Petit déjeune de 12 h à 13 h (règle) : sa séance ne peut pas être à 11 h."""
    f = Fabrique(creneaux=AUTOUR_DE_MIDI, dejeuner_mode="aucun")
    f.salle()
    petit = f.enseignant("M. Petit")
    f.besoin([f.groupe("A1")], [petit])
    regle = f.regle(
        TypeRegle.PAUSE_DEJEUNER,
        NaturePortee.ENSEIGNANT,
        petit,
        niveau="necessaire",
        mode="fixe",
        debut="12:00",
        fin="13:00",
    )
    instantane = f.instantane()
    assert instantane.regles[0].phrase == "M. Petit déjeune de 12 h 00 à 13 h 00."
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert places["1-1"].debut == 13 * H + 45

    f.indisponibilite(CibleIndisponibilite.ENSEIGNANT, petit, jour=LUNDI, debut=13 * H)
    instantane = f.instantane()
    refuse(instantane, "règle R001")
    resultat = souple(instantane, regle)
    assert resultat.statistiques["entorses_regles"] == {"R001": 1}


def test_pause_minimale_reglee_par_groupe():
    """A1 veut 30 min entre deux séances ; les créneaux sont à 15 min ; A2 se contente de 15."""
    f = Fabrique(creneaux=LUNDI_SEUL[:2])
    f.salle()
    f.salle()
    a1, a2 = f.groupe("A1"), f.groupe("A2")
    f.besoin([a1], [f.enseignant()], nombre=2)
    f.besoin([a2], [f.enseignant()], nombre=2)
    regle = f.regle(TypeRegle.PAUSE_MINIMALE, NaturePortee.GROUPE, a1, "necessaire", duree=30)
    instantane = f.instantane()
    assert instantane.regles[0].phrase == "La classe A1 a au moins 30 min entre deux séances."
    refuse(instantane)

    resultat = souple(instantane, regle)
    assert resultat.statistiques["entorses_regles"] == {"R001": 1}
    assert resultat.statistiques["entorses"] == {}
    # Le standard (PAUSE souple) et la règle se cumulent : chacun est imputé à sa source.
    niveaux = {regle: Niveau.IMPORTANT}
    reglages = options(niveaux_regles=niveaux, niveaux={**OPTIONS.niveaux, "PAUSE": "important"})
    f.parametres = dataclasses.replace(f.parametres, pause_min=30)
    serre = f.instantane()
    resultat = resoudre(serre, reglages)
    assert resultat.statut is StatutSolveur.SOLUTION, resultat.message
    assert resultat.statistiques["entorses_regles"] == {"R001": 1}
    # A2 et l'enseignant d'A1, tous deux au standard de 30 min (une entorse par paire et par source).
    assert resultat.statistiques["entorses"] == {"PAUSE": 2}


def test_reproductible_avec_des_regles_souples():
    instantane, regle = _lundi_ferme_a_la_classe()
    premier = souple(instantane, regle)
    second = souple(instantane, regle)
    assert premier.placements == second.placements
    assert premier.statistiques["entorses_regles"] == second.statistiques["entorses_regles"]


# --------------------------------------------------------------------------- #
# Couche datée
# --------------------------------------------------------------------------- #


def test_replacement_respecte_les_jours_de_la_regle():
    """Le lundi de la 2e semaine est fermé ; A1 ne travaille pas le mardi : replacée au mercredi."""
    f = Fabrique(nb_semaines=3)
    f.salle("N101")
    classe = f.groupe("A1")
    f.besoin([classe], [f.enseignant("Mme Martin")])
    f.jour_ferme(RENTREE + SEMAINE)
    regle = f.regle(
        TypeRegle.JOURS_TRAVAILLES,
        NaturePortee.GROUPE,
        classe,
        jours=["lundi", "mercredi", "jeudi", "vendredi"],
    )
    instantane = f.instantane()
    placements = [place("1-1", LUNDI, C1, 1)]
    instancie = instancier(instantane, placements)
    resultat = replacer(
        instantane,
        instancie.occurrences,
        [revoir.occurrence for revoir in instancie.a_replacer],
    )
    (replacement,) = resultat.replacements
    assert replacement.replacee, replacement.raison
    assert replacement.occurrence.date.weekday() == MERCREDI

    # Règle souple : le mardi reste possible, au prix d'une entorse ; le mercredi coûte moins.
    resultat = replacer(
        instantane,
        instancie.occurrences,
        [revoir.occurrence for revoir in instancie.a_replacer],
        OptionsReplacement(niveaux_regles={regle: Niveau.FACULTATIF}),
    )
    (replacement,) = resultat.replacements
    assert replacement.occurrence.date.weekday() == MERCREDI
    assert replacement.cout == 20

    # Règle datée : hors de sa période, le mardi redevient permis.
    g = Fabrique(nb_semaines=3)
    g.salle("N101")
    classe = g.groupe("A1")
    g.besoin([classe], [g.enseignant("Mme Martin")])
    g.jour_ferme(RENTREE + SEMAINE)
    g.regle(
        TypeRegle.JOURS_TRAVAILLES,
        NaturePortee.GROUPE,
        classe,
        jours=["lundi", "mercredi", "jeudi", "vendredi"],
        date_debut=RENTREE + 2 * SEMAINE,
        date_fin=RENTREE + 2 * SEMAINE + dt.timedelta(days=4),
    )
    instantane = g.instantane()
    instancie = instancier(instantane, placements)
    resultat = replacer(
        instantane, instancie.occurrences, [r.occurrence for r in instancie.a_replacer]
    )
    (replacement,) = resultat.replacements
    assert replacement.occurrence.date.weekday() == MARDI


# --------------------------------------------------------------------------- #
# Services et ligne de commande (jeu minimal : R001 nécessaire, R002 importante)
# --------------------------------------------------------------------------- #


def _regles_du_jeu(jeu_minimal) -> dict[str, int]:
    """Identifiants des règles actives du jeu minimal, par code."""
    with session_base() as session:
        return {
            regle.code: regle.id
            for regle in charger_regles_actives(session, jeu_minimal.annee_scolaire_id)
        }


def test_les_regles_sont_copiees_avec_le_calcul_et_rendues_au_suivi(jeu_minimal):
    ids = _regles_du_jeu(jeu_minimal)
    assert {"R001", "R002"} <= set(ids)
    choisis = {ids["R001"]: Niveau.FACULTATIF}  # R002 ignorée pour ce calcul
    suivi = generation.attendre_calcul(
        generation.lancer_calcul(
            jeu_minimal.semestre_id, dataclasses.replace(OPTIONS, niveaux_regles=choisis)
        ),
        60,
    )
    assert suivi.etat is EtatCalcul.REUSSI, suivi.message
    assert suivi.niveaux_regles == choisis
    par_code = {regle["code"]: regle for regle in suivi.regles}
    assert par_code["R001"]["niveau"] == "facultatif"
    assert par_code["R002"]["niveau"] is None  # ignorée : copiée quand même, sans niveau
    assert par_code["R001"]["type"] == "JOURS_TRAVAILLES"
    assert par_code["R001"]["portee"]["nature"] == "groupe"
    assert par_code["R001"]["parametres"]["jours"] == [
        "lundi",
        "mardi",
        "mercredi",
        "jeudi",
        "vendredi",
    ]
    assert par_code["R001"]["phrase"]
    with session_base() as session:
        calcul = session.get(m.Calcul, suivi.id)
        assert calcul.parametres["niveaux_regles"] == {str(ids["R001"]): "facultatif"}
        assert [r["code"] for r in calcul.parametres["regles"]] == sorted(par_code)

    # Sans choix : chaque règle active à son niveau, rien d'ignoré.
    suivi = generation.attendre_calcul(
        generation.lancer_calcul(jeu_minimal.semestre_id, OPTIONS), 60
    )
    assert suivi.etat is EtatCalcul.REUSSI, suivi.message
    assert suivi.niveaux_regles is None
    niveaux = {regle["code"]: regle["niveau"] for regle in suivi.regles}
    assert niveaux["R001"] == "necessaire" and niveaux["R002"] == "important"


def test_pre_verifier_semestre_avec_des_niveaux_de_regles(jeu_minimal):
    ids = _regles_du_jeu(jeu_minimal)
    with session_base() as session:
        anomalies = generation.pre_verifier_semestre(
            session, jeu_minimal.semestre_id, niveaux_regles={ids["R001"]: Niveau.NECESSAIRE}
        )
    assert not [a for a in anomalies if a.bloquante]


def test_commande_generer_regle(jeu_minimal):
    lanceur = CliRunner()
    semestre = str(jeu_minimal.semestre_id)
    base = ["generer", "--semestre", semestre, "--sans-optimiser", "--temps-limite", "30"]
    resultat = lanceur.invoke(
        cli.app, [*base, "--regle", "R001=facultatif", "--sans-regle", "R002"]
    )
    assert resultat.exit_code == 0, resultat.output
    assert "Règles personnalisées : R001 : facultatif ; R002 : ignorée." in resultat.output
    ids = _regles_du_jeu(jeu_minimal)
    with session_base() as session:
        (calcul,) = session.scalars(select(m.Calcul)).all()
        assert calcul.parametres["niveaux_regles"] == {str(ids["R001"]): "facultatif"}
        niveaux = {r["code"]: r["niveau"] for r in calcul.parametres["regles"]}
        assert niveaux["R001"] == "facultatif" and niveaux["R002"] is None

    for arguments, attendu in (
        (["--regle", "R001"], "CODE=niveau"),
        (["--regle", "R999=important"], "inconnue ou inactive"),
        (["--regle", "R001=facultatif", "--sans-regle", "R001"], "à la fois ignorée"),
        (["--regle", "R001=souhaitable"], "Niveau inconnu"),
    ):
        refuse = lanceur.invoke(cli.app, [*base, *arguments])
        assert refuse.exit_code == 1, refuse.output
        assert attendu in refuse.output

    # Sans option, rien à dire sur les règles.
    resultat = lanceur.invoke(cli.app, base)
    assert resultat.exit_code == 0 and "Règles personnalisées : " not in resultat.output


def test_commande_pre_verifier_regle(jeu_minimal):
    lanceur = CliRunner()
    resultat = lanceur.invoke(
        cli.app,
        ["pre-verifier", "--semestre", str(jeu_minimal.semestre_id), "--sans-regle", "R001"],
    )
    assert resultat.exit_code == 0, resultat.output
    assert "Règles personnalisées : R001 : ignorée." in resultat.output
