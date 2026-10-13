"""Niveaux de règles, temps 1 : la plomberie, des options du calcul jusqu'au suivi et à la commande.

Le moteur pose encore toutes les contraintes présentes en dur ; seule la
circulation des niveaux est vérifiée ici (options, enregistrement, suivi,
pré-vérifications, ligne de commande).
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
    LUNDI,
    MARDI,
    NIVEAUX_DURS,
    OPTIONS,
    RENTREE,
    Fabrique,
    H,
    grille,
    options,
    verifier_solution,
)
from tests.solveur.test_contraintes import MATINEE, UN_CRENEAU
from tests.solveur.test_couche_datee import SEMAINE, place
from typer.testing import CliRunner

from edt import cli
from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.contraintes import (
    CATALOGUE,
    CodeContrainte,
    Niveau,
    niveaux_depuis_desactivees,
    niveaux_par_defaut,
    normaliser_niveaux,
)
from edt.domaine.enumerations import (
    CibleIndisponibilite,
    DemiJournee,
    EtatCalcul,
    Gravite,
    ModeVolume,
)
from edt.domaine.heures import ecart
from edt.domaine.instantane import Creneau
from edt.erreurs import ErreurDonneesInvalides
from edt.services import generation
from edt.services.niveaux import niveaux_du_calcul
from edt.solveur import OptionsSolveur, StatutSolveur, resoudre
from edt.solveur.instanciation import instancier
from edt.solveur.preverification import pre_verifier
from edt.solveur.replacement import OptionsReplacement, replacer

C = CodeContrainte
DELAI = 60


def _generer(semestre_id: int, reglages: OptionsSolveur) -> generation.SuiviCalcul:
    return generation.attendre_calcul(generation.lancer_calcul(semestre_id, reglages), DELAI)


# --------------------------------------------------------------------------- #
# Options du calcul
# --------------------------------------------------------------------------- #


def test_options_sans_rien_dire_tous_les_niveaux_par_defaut():
    reglages = OptionsSolveur()
    assert reglages.niveaux == niveaux_par_defaut()
    assert set(reglages.niveaux) == set(CATALOGUE)
    assert reglages.contraintes_desactivees == frozenset()
    assert reglages.niveaux[C.PAUSE] is Niveau.IMPORTANT
    assert reglages.niveaux[C.SALLE_UNIQUE] is Niveau.NECESSAIRE


def test_contraintes_desactivees_seules_construisent_les_niveaux():
    """Les appelants d'avant les niveaux : une contrainte désactivée est une clé absente."""
    reglages = OptionsSolveur(contraintes_desactivees={"PAUSE", "EQUIPEMENT"})
    assert C.PAUSE not in reglages.niveaux and C.EQUIPEMENT not in reglages.niveaux
    assert reglages.niveaux == niveaux_depuis_desactivees({"PAUSE", "EQUIPEMENT"})
    assert reglages.contraintes_desactivees == {C.PAUSE, C.EQUIPEMENT}
    assert reglages.vers_dict()["niveaux"].keys() == {
        code.value for code in CATALOGUE if code not in (C.PAUSE, C.EQUIPEMENT)
    }


def test_les_niveaux_sont_la_seule_verite():
    """`contraintes_desactivees` est déduit des clés absentes ; donné avec `niveaux`, il en retire."""
    choisis = normaliser_niveaux({"PAUSE": "facultatif", "MAX_ENSEIGNANT": "necessaire"})
    del choisis[C.EQUIPEMENT]  # ignorée
    reglages = OptionsSolveur(niveaux=choisis)
    assert reglages.niveaux[C.PAUSE] is Niveau.FACULTATIF
    assert reglages.niveaux[C.MAX_ENSEIGNANT] is Niveau.NECESSAIRE
    assert reglages.contraintes_desactivees == {C.EQUIPEMENT}
    enregistre = reglages.vers_dict()["niveaux"]
    assert enregistre["PAUSE"] == "facultatif" and "EQUIPEMENT" not in enregistre
    # Les niveaux passent par les textes des formulaires et survivent à `replace`.
    assert OptionsSolveur(niveaux={"PAUSE": "important"}).niveaux == {C.PAUSE: Niveau.IMPORTANT}
    assert dataclasses.replace(reglages, optimiser=True).niveaux == reglages.niveaux
    les_deux = OptionsSolveur(contraintes_desactivees={"PAUSE"}, niveaux=choisis)
    assert C.PAUSE not in les_deux.niveaux
    assert les_deux.contraintes_desactivees == {C.PAUSE, C.EQUIPEMENT}


def test_une_contrainte_qui_definit_le_probleme_reste_necessaire():
    with pytest.raises(ErreurDonneesInvalides, match="définit le problème"):
        OptionsSolveur(niveaux=normaliser_niveaux({"SALLE_UNIQUE": "important"}))
    with pytest.raises(ErreurDonneesInvalides, match="Niveau inconnu"):
        OptionsSolveur(niveaux={"PAUSE": "souhaitable"})
    with pytest.raises(ErreurDonneesInvalides, match="Contrainte inconnue"):
        OptionsSolveur(niveaux={"INCONNUE": "important"})


def test_le_moteur_pose_en_dur_les_contraintes_presentes_quel_que_soit_leur_niveau():
    """Temps 1 : une contrainte importante ou facultative est encore respectée à la lettre."""
    f = Fabrique()
    f.salle()
    classe, martin = f.groupe("A1"), f.enseignant()
    f.besoin([classe], [martin], nombre=2)
    instantane = f.instantane()
    souples = normaliser_niveaux({"PAUSE": "facultatif"})
    places = verifier_solution(instantane, resoudre(instantane, options(niveaux=souples)))
    a, b = places["1-1"], places["1-2"]
    assert (
        a.jour != b.jour
        or a.fin + instantane.parametres.pause_min <= b.debut
        or (b.fin + instantane.parametres.pause_min <= a.debut)
    )
    # Clé absente : contrainte ignorée, comme avec `contraintes_desactivees`.
    sans = dict(niveaux_par_defaut())
    del sans[C.PAUSE]
    assert resoudre(instantane, options(niveaux=sans)).contraintes_desactivees == {C.PAUSE}


# --------------------------------------------------------------------------- #
# Enregistrement et suivi
# --------------------------------------------------------------------------- #


def test_les_niveaux_sont_enregistres_avec_le_calcul_et_rendus_au_suivi(jeu_minimal):
    choisis = normaliser_niveaux({"PAUSE": "facultatif"})
    del choisis[C.EQUIPEMENT]
    suivi = _generer(jeu_minimal.semestre_id, dataclasses.replace(OPTIONS, niveaux=choisis))
    assert suivi.etat is EtatCalcul.REUSSI, suivi.message
    assert suivi.niveaux == choisis
    assert suivi.contraintes_desactivees == ("EQUIPEMENT",)
    with session_base() as session:
        calcul = session.get(m.Calcul, suivi.id)
        assert calcul.parametres["niveaux"]["PAUSE"] == "facultatif"
        assert "EQUIPEMENT" not in calcul.parametres["niveaux"]
        assert calcul.contraintes_desactivees == ["EQUIPEMENT"]


def test_un_calcul_d_avant_les_niveaux_est_lu_avec_ses_contraintes_desactivees(jeu_minimal):
    suivi = _generer(
        jeu_minimal.semestre_id, dataclasses.replace(OPTIONS, contraintes_desactivees={"PAUSE"})
    )
    with session_base() as session:
        calcul = session.get(m.Calcul, suivi.id)
        parametres = dict(calcul.parametres)
        del parametres["niveaux"]  # comme un calcul enregistré avant les niveaux
        calcul.parametres = parametres
    relu = generation.etat_calcul(suivi.id)
    assert relu.niveaux == niveaux_depuis_desactivees({"PAUSE"})
    assert C.PAUSE not in relu.niveaux and relu.contraintes_desactivees == ("PAUSE",)


# --------------------------------------------------------------------------- #
# Pré-vérifications
# --------------------------------------------------------------------------- #


def test_pre_verifier_semestre_ne_regarde_que_les_contraintes_presentes(jeu_minimal):
    with session_base() as session:
        besoin = session.scalar(
            select(m.Besoin).where(m.Besoin.mode_volume == ModeVolume.PAR_SEMAINE)
        )
        besoin.nombre = 60  # aucune semaine ne peut contenir 60 séances
    with session_base() as session:
        erreurs = [
            a
            for a in generation.pre_verifier_semestre(session, jeu_minimal.semestre_id)
            if a.gravite is Gravite.ERREUR
        ]
        assert erreurs
        code = erreurs[0].code
        # Le niveau ne change rien à la pré-vérification : seule la présence compte.
        importante = {**niveaux_par_defaut(), code: Niveau.FACULTATIF}
        avec = generation.pre_verifier_semestre(
            session, jeu_minimal.semestre_id, niveaux=importante
        )
        assert [a.message for a in avec] == [a.message for a in erreurs] or any(
            a.code is code for a in avec
        )
        sans = {c: n for c, n in niveaux_par_defaut().items() if c is not code}
        ignoree = generation.pre_verifier_semestre(session, jeu_minimal.semestre_id, niveaux=sans)
        assert not any(a.code is code for a in ignoree)
        # `niveaux` prime sur `contraintes_desactivees`.
        prime = generation.pre_verifier_semestre(
            session, jeu_minimal.semestre_id, [code.value], niveaux=niveaux_par_defaut()
        )
        assert any(a.code is code for a in prime)


# --------------------------------------------------------------------------- #
# Ligne de commande
# --------------------------------------------------------------------------- #


def test_lire_niveaux_complete_les_codes_absents_et_exige_un_dictionnaire():
    """Même lecture que `edt.services.niveaux.niveaux_du_calcul`."""
    lus = OptionsSolveur.lire_niveaux({"niveaux": {"PAUSE": "facultatif"}}, ["EQUIPEMENT"])
    attendus = niveaux_par_defaut()
    attendus[C.PAUSE] = Niveau.FACULTATIF
    del attendus[C.EQUIPEMENT]
    assert lus == attendus
    assert lus == niveaux_du_calcul({"niveaux": {"PAUSE": "facultatif"}}, ["EQUIPEMENT"])
    with pytest.raises(ErreurDonneesInvalides, match="dictionnaire"):
        OptionsSolveur.lire_niveaux({"niveaux": ["PAUSE"]})


def test_commande_pre_verifier_niveau(jeu_minimal):
    lanceur = CliRunner()
    semestre = str(jeu_minimal.semestre_id)
    resultat = lanceur.invoke(
        cli.app,
        [
            "pre-verifier",
            "--semestre",
            semestre,
            "--niveau",
            "PAUSE=facultatif",
            "-d",
            "EQUIPEMENT",
        ],
    )
    assert resultat.exit_code == 0, resultat.output
    assert "Pause entre deux séances : facultatif" in resultat.output
    assert "ignorée" in resultat.output and "Pré-vérifications du semestre" in resultat.output
    refuse = lanceur.invoke(
        cli.app, ["pre-verifier", "--semestre", semestre, "--niveau", "PAUSE=souhaitable"]
    )
    assert refuse.exit_code != 0 and "Niveau inconnu" in refuse.output


def test_commande_generer_niveau(jeu_minimal):
    lanceur = CliRunner()
    semestre = str(jeu_minimal.semestre_id)
    resultat = lanceur.invoke(
        cli.app,
        ["generer", "--semestre", semestre, "--sans-optimiser", "--temps-limite", "30"]
        + [
            "--niveau",
            "PAUSE=facultatif",
            "--niveau",
            "MAX_ENSEIGNANT=necessaire",
            "-d",
            "EQUIPEMENT",
        ],
    )
    assert resultat.exit_code == 0, resultat.output
    assert "Règles : " in resultat.output
    assert "Pause entre deux séances : facultatif" in resultat.output
    assert "ignorée" in resultat.output
    assert "Maxima horaires des enseignants : nécessaire" in resultat.output
    with session_base() as session:
        (calcul,) = session.scalars(select(m.Calcul)).all()
        assert calcul.parametres["niveaux"]["PAUSE"] == "facultatif"
        assert calcul.parametres["niveaux"]["MAX_ENSEIGNANT"] == "necessaire"
        assert "EQUIPEMENT" not in calcul.parametres["niveaux"]

    # Sans option : rien de particulier à dire sur les règles.
    resultat = lanceur.invoke(
        cli.app, ["generer", "--semestre", semestre, "--sans-optimiser", "--temps-limite", "30"]
    )
    assert resultat.exit_code == 0 and "Règles : " not in resultat.output


@pytest.mark.parametrize(
    ("arguments", "attendu"),
    [
        (["--niveau", "PAUSE"], "CODE=niveau"),
        (["--niveau", "PAUSE=souhaitable"], "Niveau inconnu"),
        (["--niveau", "SALLE_UNIQUE=important"], "définit le problème"),
        (["--niveau", "PAUSE=facultatif", "-d", "PAUSE"], "à la fois ignorée"),
        (["--niveau", "INCONNUE=important"], "Contrainte inconnue"),
    ],
)
def test_commande_generer_niveau_refuse(jeu_minimal, arguments, attendu):
    resultat = CliRunner().invoke(
        cli.app, ["generer", "--semestre", str(jeu_minimal.semestre_id), *arguments]
    )
    assert resultat.exit_code == 1, resultat.output
    assert attendu in resultat.output
    with session_base() as session:
        assert session.scalars(select(m.Calcul)).all() == []  # rien n'a été lancé


# --------------------------------------------------------------------------- #
# Temps 2 : contraintes souples (une entorse coûte, une contrainte nécessaire interdit)
# --------------------------------------------------------------------------- #

SANS_PAUSE = (
    Creneau(LUNDI, 8 * H, 10 * H, DemiJournee.MATIN),
    Creneau(LUNDI, 10 * H, 12 * H, DemiJournee.MATIN),
)
"""Deux créneaux qui se touchent : deux séances d'un même groupe ne peuvent pas respecter la pause."""


def niveaux_avec(**choix: str) -> dict[C, Niveau]:
    """Toutes les contraintes nécessaires, sauf celles de `choix` (code=niveau)."""
    return {**NIVEAUX_DURS, **{C(code): Niveau(valeur) for code, valeur in choix.items()}}


def _deux_seances_sans_pause() -> Fabrique:
    f = Fabrique(creneaux=SANS_PAUSE, ouverture_min=8 * H)
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()], nombre=2)
    return f


@pytest.mark.parametrize("niveau", ["important", "facultatif"])
def test_pause_souple_une_entorse_comptee_et_dite(niveau):
    """Deux séances d'un groupe sur deux créneaux qui se touchent : une entorse à la pause."""
    instantane = _deux_seances_sans_pause().instantane()
    resultat = resoudre(instantane, options(niveaux=niveaux_avec(PAUSE=niveau)))
    verifier_solution(instantane, resultat)
    assert resultat.statistiques["entorses"] == {"PAUSE": 1}
    adjectif = "importante" if niveau == "important" else "facultative"
    assert f"1 entorse à une règle {adjectif} (pause entre deux séances : 1)." in resultat.message
    # Nécessaire : la même situation est impossible.
    refus = resoudre(instantane, options(niveaux=niveaux_avec(PAUSE="necessaire")))
    assert refus.statut is StatutSolveur.INFAISABLE


def test_pas_d_entorse_quand_elle_est_evitable():
    """Un troisième créneau éloigné : la pause souple est respectée, rien n'est dit."""
    creneaux = (*SANS_PAUSE, Creneau(LUNDI, 14 * H, 16 * H, DemiJournee.APRES_MIDI))
    f = Fabrique(creneaux=creneaux, ouverture_min=8 * H)
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()], nombre=2)
    instantane = f.instantane()
    for optimiser in (False, True):
        resultat = resoudre(
            instantane, options(niveaux=niveaux_avec(PAUSE="facultatif"), optimiser=optimiser)
        )
        places = verifier_solution(instantane, resultat)
        a, b = places["1-1"], places["1-2"]
        assert ecart(a.debut, a.fin, b.debut, b.fin) >= instantane.parametres.pause_min
        assert resultat.statistiques["entorses"] == {}
        assert "entorse" not in resultat.message


def test_entorses_minimisees_meme_sans_optimiser():
    """Sans « optimiser », l'amélioration ne porte que sur les entorses et va jusqu'au bout."""
    creneaux = (*SANS_PAUSE, Creneau(LUNDI, 14 * H, 16 * H, DemiJournee.APRES_MIDI))
    f = Fabrique(creneaux=creneaux, ouverture_min=8 * H)
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()], nombre=3)  # trois séances, trois créneaux
    instantane = f.instantane()
    resultat = resoudre(instantane, options(niveaux=niveaux_avec(PAUSE="facultatif")))
    verifier_solution(instantane, resultat)
    assert resultat.statistiques["entorses"] == {"PAUSE": 1}
    assert resultat.statistiques["raison_arret"] == "optimum_prouve"
    assert "trous" not in resultat.statistiques  # pas de bilan de confort sans « optimiser »
    assert "On ne peut pas faire mieux" not in resultat.message


def test_dejeuner_souple():
    midi = (Creneau(LUNDI, 12 * H, 14 * H, DemiJournee.MATIN),)
    f = Fabrique(creneaux=midi)
    f.salle()
    f.besoin([f.groupe("A1")], [f.enseignant()])
    instantane = f.instantane()
    assert resoudre(instantane, OPTIONS).statut is StatutSolveur.INFAISABLE
    resultat = resoudre(instantane, options(niveaux=niveaux_avec(DEJEUNER="important")))
    verifier_solution(instantane, resultat)
    assert resultat.statistiques["entorses"] == {"DEJEUNER": 1}
    assert "pause déjeuner : 1" in resultat.message


def test_equipement_souple_se_regle_dans_la_phase_des_salles():
    f = Fabrique(creneaux=UN_CRENEAU)
    nue = f.salle("N101")
    f.besoin([f.groupe("A1")], [f.enseignant()], equipements_requis=frozenset({7}))
    instantane = f.instantane()
    assert resoudre(instantane, OPTIONS).statut is StatutSolveur.INFAISABLE
    resultat = resoudre(instantane, options(niveaux=niveaux_avec(EQUIPEMENT="important")))
    places = verifier_solution(instantane, resultat)
    assert places["1-1"].salle_id == nue
    assert resultat.statistiques["entorses"] == {"EQUIPEMENT": 1}

    # Une salle équipée existe : elle est choisie, aucune entorse.
    equipee = f.salle("N102", equipements=[7])
    instantane = f.instantane()
    resultat = resoudre(instantane, options(niveaux=niveaux_avec(EQUIPEMENT="important")))
    places = verifier_solution(instantane, resultat)
    assert places["1-1"].salle_id == equipee
    assert resultat.statistiques["entorses"] == {}


def test_max_enseignant_souple_compte_les_heures_au_dela():
    f = Fabrique(creneaux=grille(jours=[LUNDI], samedi=False))
    f.salle()
    martin = f.enseignant("Mme Martin", max_jour=2 * H)
    f.besoin([f.groupe("A1")], [martin], nombre=2)
    instantane = f.instantane()
    assert resoudre(instantane, OPTIONS).statut is StatutSolveur.INFAISABLE
    resultat = resoudre(instantane, options(niveaux=niveaux_avec(MAX_ENSEIGNANT="facultatif")))
    verifier_solution(instantane, resultat)
    assert resultat.statistiques["entorses"] == {"MAX_ENSEIGNANT": 2}  # 4 h pour 2 h : 2 h au-delà
    assert "maxima horaires des enseignants : 2" in resultat.message


def test_max_hebdomadaire_souple_est_un_avertissement_et_une_entorse_certaine():
    f = Fabrique(nb_semaines=2, creneaux=grille(jours=[LUNDI, MARDI], samedi=False))
    f.salle()
    martin = f.enseignant("Mme Martin", max_semaine=2 * H)
    f.besoin([f.groupe("A1")], [martin], nombre=2)  # 4 h par semaine pour 2 h
    instantane = f.instantane()
    (erreur,) = [a for a in pre_verifier(instantane, niveaux=NIVEAUX_DURS) if a.bloquante]
    assert erreur.code is C.MAX_ENSEIGNANT and "c'est impossible" in erreur.message
    souples = niveaux_avec(MAX_ENSEIGNANT="important")
    anomalies = pre_verifier(instantane, niveaux=souples)
    assert not [a for a in anomalies if a.bloquante]
    assert any("dépassé de 2 h" in a.message for a in anomalies)
    resultat = resoudre(instantane, options(niveaux=souples))
    verifier_solution(instantane, resultat)
    # Deux heures au-delà, chacune des deux semaines.
    assert resultat.statistiques["entorses"] == {"MAX_ENSEIGNANT": 4}


def test_dispo_enseignant_souple_et_verrou_pendant_une_indisponibilite():
    f = Fabrique(creneaux=UN_CRENEAU)
    f.salle()
    martin = f.enseignant("Mme Martin")
    f.indisponibilite(CibleIndisponibilite.ENSEIGNANT, martin, jour=LUNDI)
    f.besoin([f.groupe("A1")], [martin])
    instantane = f.instantane()
    assert resoudre(instantane, OPTIONS).statut is StatutSolveur.INFAISABLE
    souples = niveaux_avec(DISPO_ENSEIGNANT="important")
    resultat = resoudre(instantane, options(niveaux=souples))
    verifier_solution(instantane, resultat)
    assert resultat.statistiques["entorses"] == {"DISPO_ENSEIGNANT": 1}

    # Une séance verrouillée pendant l'indisponibilité souple tient, et l'entorse est comptée.
    f.verrou("1-1", LUNDI, C1, 1)
    verrouille = resoudre(f.instantane(), options(niveaux=souples))
    assert verrouille.statut is StatutSolveur.SOLUTION
    assert verrouille.statistiques["entorses"] == {"DISPO_ENSEIGNANT": 1}


def _deux_horaires_fixes_colles(verrous: bool) -> Fabrique:
    """Deux séances de la même classe le mardi, 8 h 30-10 h 40 puis 10 h 45 : cinq minutes de pause.

    Horaires fixés par des verrous, ou imposés par les besoins.
    """
    f = Fabrique()
    f.salle("N101")
    f.salle("N102")
    classe = f.groupe("A1")
    imposes = {} if verrous else {"jour_impose": MARDI}
    f.besoin([classe], [f.enseignant()], duree=2 * H + 10, **({"debut_impose": C1} | imposes))
    f.besoin([classe], [f.enseignant()], **({"debut_impose": C2} | imposes))
    if verrous:
        f.verrou("1-1", MARDI, C1, 1)
        f.verrou("2-1", MARDI, C2, 2)
    return f


@pytest.mark.parametrize("verrous", [True, False], ids=["verrous", "heures imposées"])
def test_deux_horaires_fixes_qui_n_enfreignent_qu_une_regle_souple_ne_refusent_pas_le_calcul(
    verrous,
):
    """La pré-vérification ne retient que les règles dures entre horaires fixes ; souple, l'entorse
    est comptée par le modèle."""
    instantane = _deux_horaires_fixes_colles(verrous).instantane()
    refuse = resoudre(instantane, OPTIONS)
    assert (
        refuse.statut is StatutSolveur.INFAISABLE and "Pause entre deux séances" in refuse.message
    )
    for niveau in ("facultatif", "important"):
        resultat = resoudre(instantane, options(niveaux=niveaux_avec(PAUSE=niveau)))
        assert resultat.statut is StatutSolveur.SOLUTION, resultat.message
        places = verifier_solution(instantane, resultat)
        assert (places["1-1"].debut, places["2-1"].debut) == (C1, C2)
        assert resultat.statistiques["entorses"] == {"PAUSE": 1}


def test_deux_verrous_sur_deux_campus_et_trajet_souple():
    """Même chose pour le trajet : deux verrous à un quart d'heure sur deux campus distants."""
    f = Fabrique(creneaux=MATINEE)
    nord, sud = f.campus("N"), f.campus("S")
    f.trajet(nord, sud, 60)
    f.salle("N101", campus=nord)
    f.salle("S101", campus=sud)
    martin = f.enseignant("Mme Martin")
    f.besoin([f.groupe("A1")], [martin])
    f.besoin([f.groupe("A2")], [martin])
    f.verrou("1-1", LUNDI, C1, 1)
    f.verrou("2-1", LUNDI, C2, 2)
    instantane = f.instantane()
    refuse = resoudre(instantane, OPTIONS)
    assert refuse.statut is StatutSolveur.INFAISABLE and "campus" in refuse.message.lower()
    resultat = resoudre(instantane, options(niveaux=niveaux_avec(CAMPUS_TRAJET="facultatif")))
    assert resultat.statut is StatutSolveur.SOLUTION, resultat.message
    verifier_solution(instantane, resultat)
    assert resultat.statistiques["entorses"] == {"CAMPUS_TRAJET": 1}


@pytest.mark.parametrize(
    ("cible", "code"),
    [
        (CibleIndisponibilite.ENSEIGNANT, "DISPO_ENSEIGNANT"),
        (CibleIndisponibilite.GROUPE, "DISPO_GROUPE"),
    ],
)
def test_indisponibilite_datee_souple_fait_replacer_l_occurrence(cible, code):
    """Souple, une indisponibilité récurrente n'empêche rien (la semaine type l'a arbitrée) ; une
    indisponibilité datée, que la semaine type ignore, envoie l'occurrence au replacement, qui
    préfère un autre jour à une entorse."""
    f = Fabrique(nb_semaines=3)
    f.salle("N101")
    f.besoin([f.groupe("A1")], [f.enseignant("Mme Martin")])
    f.indisponibilite(cible, 1, jour=LUNDI)  # récurrente : arbitrée par la semaine type
    f.indisponibilite(cible, 1, date_debut=RENTREE + SEMAINE)  # datée : le lundi de la 2e semaine
    f.indisponibilite(cible, 1, jour=MARDI)  # récurrente : le mardi coûtera une entorse
    instantane = f.instantane()
    niveaux = niveaux_avec(**{code: "important"})
    placements = [place("1-1", LUNDI, C1, 1)]
    instancie = instancier(instantane, placements, niveaux=niveaux)
    assert [(r.occurrence.date, r.code.value) for r in instancie.a_replacer] == [
        (RENTREE + SEMAINE, code)
    ]
    assert "est indisponible le 14/09/2026" in instancie.a_replacer[0].raison
    assert [o.date for o in instancie.occurrences] == [RENTREE, RENTREE + 2 * SEMAINE]
    resultat = replacer(
        instantane,
        instancie.occurrences,
        [revoir.occurrence for revoir in instancie.a_replacer],
        OptionsReplacement(niveaux=niveaux),
    )
    (replacement,) = resultat.replacements
    assert replacement.replacee, replacement.raison
    # Rester le lundi (datée) ou aller au mardi (récurrente) coûterait une entorse : le mercredi.
    assert replacement.occurrence.date == RENTREE + SEMAINE + dt.timedelta(days=2)
    assert replacement.cout == 20
    # Règle nécessaire : l'indisponibilité récurrente aussi fait replacer, dès l'instanciation.
    assert len(instancier(instantane, placements, niveaux=NIVEAUX_DURS).a_replacer) == 3


def test_replacement_paie_une_indisponibilite_souple():
    """La place de la même semaine tombe pendant une indisponibilité récurrente de l'enseignante :
    prise si la règle est facultative (20 < une autre semaine), pas si elle est importante."""
    f = Fabrique(nb_semaines=3, creneaux=grille(jours=[LUNDI, MARDI], samedi=False))
    f.salle("N101")
    martin = f.enseignant("Mme Martin")
    f.besoin([f.groupe("A1")], [martin])
    f.indisponibilite(CibleIndisponibilite.ENSEIGNANT, martin, jour=MARDI)
    lundi_ferme = RENTREE + SEMAINE
    f.jour_ferme(lundi_ferme)
    instantane = f.instantane()
    placements = [place("1-1", LUNDI, C1, 1)]
    for niveau, meme_semaine in (("facultatif", True), ("important", False), ("necessaire", False)):
        niveaux = niveaux_avec(DISPO_ENSEIGNANT=niveau)
        instancie = instancier(instantane, placements, niveaux=niveaux)
        resultat = replacer(
            instantane,
            instancie.occurrences,
            [revoir.occurrence for revoir in instancie.a_replacer],
            OptionsReplacement(niveaux=niveaux),
        )
        (replacement,) = resultat.replacements
        assert replacement.replacee, replacement.raison
        nouvelle = replacement.occurrence
        if meme_semaine:
            assert (nouvelle.date, nouvelle.debut) == (lundi_ferme + dt.timedelta(days=1), C1)
            assert replacement.cout == 10 + 20  # un jour, une entorse
        else:
            assert nouvelle.date.isocalendar()[1] != lundi_ferme.isocalendar()[1]


def test_optimal_n_est_pas_affirme_tant_qu_il_reste_une_entorse_de_salle():
    """Deux cours qui exigent le même matériel, forcés à la même heure : une salle nue pour l'un.
    Les horaires sont les mieux notés possibles (prouvé), pas l'emploi du temps."""
    f = Fabrique(creneaux=grille(jours=[LUNDI], samedi=False))
    f.salle("N101", equipements=[7])
    f.salle("N102")
    f.salle("N103")
    for code in ("A1", "A2"):
        enseignant = f.enseignant()
        # Libre le matin seulement : la séance ne peut être qu'au premier créneau.
        f.indisponibilite(CibleIndisponibilite.ENSEIGNANT, enseignant, jour=LUNDI, debut=C1 + 2 * H)
        f.besoin([f.groupe(code)], [enseignant], equipements_requis=frozenset({7}))
    f.besoin([f.groupe("A3")], [f.enseignant()], nombre=2)  # de quoi noter (trous, extrémités)
    instantane = f.instantane()
    reglages = options(niveaux=niveaux_avec(EQUIPEMENT="important"), optimiser=True)
    resultat = resoudre(instantane, reglages)
    assert resultat.statut is StatutSolveur.SOLUTION, resultat.message
    verifier_solution(instantane, resultat)
    assert resultat.statistiques["entorses"] == {"EQUIPEMENT": 1}
    assert resultat.statistiques["raison_arret"] == "optimum_prouve"
    assert resultat.statistiques["optimal"] is False
    assert "On ne peut pas faire mieux" not in resultat.message
    assert "viennent des salles" in resultat.message


def test_des_horaires_mieux_notes_sont_ecartes_si_leurs_salles_coutent_une_entorse():
    """Sans séance aux extrémités de la journée, la note serait parfaite, mais il faudrait deux
    cours à matériel à la même heure pour une seule salle équipée : l'entorse coûte plus que la
    note, les horaires de départ (donnés en indices) sont gardés."""
    f = Fabrique(
        creneaux=grille(jours=[LUNDI], samedi=False), poids_trous=10, poids_creneaux_extremes=5
    )
    f.salle("N101", equipements=[7])
    f.salle("N102")
    f.besoin([f.groupe("A1")], [f.enseignant()], nombre=2, equipements_requis=frozenset({7}))
    f.besoin([f.groupe("A2")], [f.enseignant()], equipements_requis=frozenset({7}))
    f.indice("1-1", LUNDI, C1, 1)
    f.indice("1-2", LUNDI, C2, 1)
    f.indice("2-1", LUNDI, C3, 1)
    instantane = f.instantane()
    reglages = options(niveaux=niveaux_avec(EQUIPEMENT="important"), optimiser=True)
    resultat = resoudre(instantane, reglages)
    places = verifier_solution(instantane, resultat)
    assert resultat.statistiques["entorses"] == {}
    assert len({places["1-1"].debut, places["1-2"].debut, places["2-1"].debut}) == 3
    assert resultat.statistiques["raison_arret"] == "optimum_prouve"
    assert resultat.statistiques["optimal"] is False
    assert "coûteraient des entorses" in resultat.message


def test_reproductible_a_un_fil_avec_des_entorses():
    instantane = _deux_seances_sans_pause().instantane()
    reglages = options(niveaux=niveaux_avec(PAUSE="important"), optimiser=True)
    premier, second = resoudre(instantane, reglages), resoudre(instantane, reglages)
    assert premier.placements == second.placements
    assert premier.statistiques["entorses"] == second.statistiques["entorses"] == {"PAUSE": 1}


def test_replacement_paie_les_entorses_souples():
    """Une place dans la même semaine mais sans pause : prise si la pause est facultative, pas
    si elle est importante (une semaine d'éloignement coûte moins qu'une entorse importante)."""
    creneaux = (*SANS_PAUSE, Creneau(MARDI, 8 * H, 10 * H, DemiJournee.MATIN))
    f = Fabrique(nb_semaines=3, creneaux=creneaux, ouverture_min=8 * H)
    f.salle("N101")
    classe, martin = f.groupe("A1"), f.enseignant("Mme Martin")
    # 1-1 : le lundi 8 h, la semaine 2 seulement ; 2-1 : le mardi 8 h chaque semaine, et le
    # mardi de la semaine 2 est fermé. Dans la semaine 2, seule reste la place du lundi 10 h,
    # collée à 1-1 ; les autres semaines offrent le lundi 10 h sans voisin.
    f.besoin(
        [classe],
        [martin],
        mode_volume=ModeVolume.TOTAL,
        nombre=1,
        date_debut=RENTREE + SEMAINE,
        date_fin=RENTREE + SEMAINE + dt.timedelta(days=4),
    )
    f.besoin([classe], [martin])
    mardi_ferme = RENTREE + SEMAINE + dt.timedelta(days=1)
    f.jour_ferme(mardi_ferme)
    instantane = f.instantane()
    placements = [place("1-1", LUNDI, 8 * H, 1), place("2-1", MARDI, 8 * H, 1)]
    for niveau, meme_semaine in (("facultatif", True), ("important", False), ("necessaire", False)):
        niveaux = niveaux_avec(PAUSE=niveau)
        instancie = instancier(instantane, placements, niveaux=niveaux)
        resultat = replacer(
            instantane,
            instancie.occurrences,
            [revoir.occurrence for revoir in instancie.a_replacer],
            OptionsReplacement(niveaux=niveaux),
        )
        (replacement,) = resultat.replacements
        assert replacement.replacee, replacement.raison
        nouvelle = replacement.occurrence
        if meme_semaine:
            assert (nouvelle.date, nouvelle.debut) == (mardi_ferme - dt.timedelta(days=1), 10 * H)
            assert replacement.cout == 10 + 1 + 20  # un jour, une autre heure, une entorse
        else:
            assert nouvelle.date.isocalendar()[1] != mardi_ferme.isocalendar()[1]


def test_calcul_reussi_malgre_une_indisponibilite_impossible_a_respecter(jeu_minimal):
    """Jeu minimal, Mme Martin indisponible toute la semaine : refusé si la règle est nécessaire ;
    emploi du temps trouvé avec ses entorses si elle est importante, sans que la couche datée
    déplace les séances concernées."""
    with session_base() as session:
        martin = session.scalar(select(m.Enseignant).where(m.Enseignant.nom == "Martin"))
        for jour in range(6):
            session.add(
                m.Indisponibilite(
                    cible=CibleIndisponibilite.ENSEIGNANT,
                    enseignant=martin,
                    jour=jour,
                    motif="Absente toute la semaine",
                )
            )
    refus = _generer(jeu_minimal.semestre_id, dataclasses.replace(OPTIONS, niveaux=NIVEAUX_DURS))
    assert refus.etat is EtatCalcul.INFAISABLE
    souples = niveaux_avec(DISPO_ENSEIGNANT="important")
    suivi = _generer(jeu_minimal.semestre_id, dataclasses.replace(OPTIONS, niveaux=souples))
    assert suivi.etat is EtatCalcul.REUSSI, suivi.message
    entorses = suivi.statistiques["entorses"]
    assert set(entorses) == {"DISPO_ENSEIGNANT"} and entorses["DISPO_ENSEIGNANT"] >= 2
    assert "à des règles importantes (disponibilité des enseignants" in suivi.message
    # Les séances datées de Mme Martin ne sont pas replacées pour cette indisponibilité souple.
    with session_base() as session:
        occurrences = session.scalars(
            select(m.Occurrence).where(m.Occurrence.version_id == suivi.version_id)
        ).all()
        assert not any(
            "indisponible" in (o.commentaire or "") and "Martin" in (o.commentaire or "")
            for o in occurrences
        )
