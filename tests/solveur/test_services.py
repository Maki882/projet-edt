"""Intégration sur le jeu minimal du socle : génération de bout en bout, instanciation, replacement.

Base SQLite temporaire (fixtures de `tests/conftest.py`). Le semestre du jeu
minimal contient des jours fériés et des vacances : des séances datées
tombent dessus et doivent être replacées.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import logging
import os
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.exc import OperationalError

import edt
from edt import cli, solveur
from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.calendrier import lundi_de
from edt.domaine.contraintes import niveaux_depuis_desactivees
from edt.domaine.enumerations import (
    EtatCalcul,
    Gravite,
    ModeVolume,
    OrigineOccurrence,
    OrigineVersion,
    StatutOccurrence,
)
from edt.domaine.instantane import Placement
from edt.echange.lecture import FichierEnvoye
from edt.erreurs import (
    ErreurCalculEnCours,
    ErreurDonneesInvalides,
    ErreurIntrouvable,
    ErreurRegleMetier,
)
from edt.journal import configurer_journalisation
from edt.services import echange, generation, jeux
from edt.services import regles as service_regles
from edt.services.echange import OptionsImport
from edt.services.instanciation import instancier_version, replacer_occurrences
from edt.services.instantane import charger_instantane
from edt.services.parametres import reinitialiser_parametres
from edt.services.verification import (
    verifier_occurrences_version,
    verifier_version,
    version_a_verifier,
)
from edt.solveur import OptionsSolveur, ResultatSolveur, StatutSolveur
from edt.solveur.moteur import MESSAGE_SOLUTION_CONSERVEE
from edt.web.app import creer_app

DELAI = 60
OPTIONS = OptionsSolveur(temps_limite_s=30, graine=1, nb_workers=1)


def generer(semestre_id: int, options: OptionsSolveur = OPTIONS, **kwargs):
    return generation.attendre_calcul(
        generation.lancer_calcul(semestre_id, options, **kwargs), DELAI
    )


def occurrences(version_id: int) -> list[m.Occurrence]:
    with session_base() as session:
        lignes = session.scalars(
            select(m.Occurrence)
            .where(m.Occurrence.version_id == version_id)
            .order_by(m.Occurrence.date, m.Occurrence.debut_min, m.Occurrence.id)
        ).all()
        session.expunge_all()
        return list(lignes)


# --------------------------------------------------------------------------- #
# Génération de bout en bout
# --------------------------------------------------------------------------- #


def test_generation_de_bout_en_bout(jeu_minimal):
    suivi = generer(jeu_minimal.semestre_id)
    assert suivi.etat is EtatCalcul.REUSSI, suivi.message
    stats = suivi.statistiques
    assert stats["moteur"] == "cp_sat" and stats["nb_seances"] == 17
    assert stats["raison_arret"] == "premiere_solution"  # la raison de l'arrêt suit le calcul

    # La semaine type est valide pour le vérificateur officiel, et ses indicateurs sont joints.
    with session_base() as session:
        rapport = verifier_version(session, suivi.version_id)
        assert rapport.valide, [a.message for a in rapport.anomalies if a.bloquante]
        instantane = charger_instantane(session, jeu_minimal.semestre_id, suivi.version_id)
    assert stats["indicateurs"]["nb_anomalies_bloquantes"] == 0
    assert stats["indicateurs"]["nb_seances_placees"] == 17

    # Les séances datées existent : une par séance hebdomadaire et par semaine active.
    bilan = stats["instanciation"]
    attendues = sum(len(seance.semaines) for seance in instantane.seances)
    lignes = occurrences(suivi.version_id)
    assert len(lignes) == bilan["nb_occurrences"] == attendues
    assert bilan["nb_planifiees"] + bilan["nb_replacees"] + bilan["nb_a_programmer"] == attendues
    assert f"{attendues} séance(s) datée(s)" in suivi.message

    # Aucune séance datée n'enfreint une contrainte (jours fermés compris).
    with session_base() as session:
        dates = verifier_occurrences_version(session, suivi.version_id)
    assert dates.valide, [a.message for a in dates.anomalies if a.bloquante]


def test_jour_ferie_replace_dans_la_meme_semaine(jeu_minimal):
    # Le 11 novembre 2026 est un mercredi férié. Le moteur ne donne pas la même première
    # solution d'une machine à l'autre : pour qu'une séance tombe ce jour-là quoi qu'il
    # arrive, le TD de mathématiques de la classe 2 est verrouillé le mercredi dans une
    # première version, puis l'emploi du temps est recalculé autour de ce verrou. (Imposer
    # le mercredi au besoin ne conviendrait pas : la séance datée devrait alors rester un
    # mercredi, et partirait dans la semaine voisine.)
    armistice = dt.date(2026, 11, 11)
    premiere = generer(jeu_minimal.semestre_id)
    assert premiere.etat is EtatCalcul.REUSSI, premiere.message
    with session_base() as session:
        td = session.scalar(
            select(m.SeanceHebdo).where(
                m.SeanceHebdo.version_id == premiere.version_id,
                m.SeanceHebdo.besoin.has(
                    m.Besoin.matiere.has(code="IM.1101")
                    & m.Besoin.type_seance.has(code="TD")
                    & m.Besoin.groupes.any(code="A1-G2")
                ),
            )
        )
        td.jour, td.verrou = armistice.weekday(), True  # 2 : mercredi
        besoin_td = td.besoin_id
    suivi = generer(jeu_minimal.semestre_id)
    assert suivi.etat is EtatCalcul.REUSSI, suivi.message
    with session_base() as session:
        instantane = charger_instantane(session, jeu_minimal.semestre_id, suivi.version_id)
        verrouillee = session.scalar(
            select(m.SeanceHebdo).where(
                m.SeanceHebdo.version_id == suivi.version_id,
                m.SeanceHebdo.besoin_id == besoin_td,
            )
        )
        assert (verrouillee.jour, verrouillee.verrou) == (armistice.weekday(), True)
    deplacees = [o for o in occurrences(suivi.version_id) if o.statut is StatutOccurrence.DEPLACEE]
    assert deplacees, "le calendrier 2026-2027 contient des jours fériés en semaine"
    assert suivi.statistiques["instanciation"]["nb_replacees"] == len(deplacees)
    for occurrence in deplacees:
        besoin = instantane.besoin(occurrence.besoin_id)
        assert occurrence.origine is OrigineOccurrence.REPLACEMENT
        assert occurrence.date_initiale is not None and occurrence.commentaire
        assert instantane.fermeture(occurrence.date, besoin) is None
    # Les séances du 11 novembre sont déplacées pour « jour fermé », et le TD verrouillé le
    # mercredi reste dans la même semaine.
    du_11 = [o for o in deplacees if o.date_initiale == armistice]
    assert all("jour fermé" in o.commentaire and o.date != armistice for o in du_11)
    (td_du_11,) = [o for o in du_11 if o.besoin_id == besoin_td]
    assert td_du_11.date.isocalendar()[:2] == armistice.isocalendar()[:2]


def test_instanciation_idempotente_et_saisies_manuelles_conservees(jeu_minimal):
    suivi = generer(jeu_minimal.semestre_id)
    avant = occurrences(suivi.version_id)
    with session_base() as session:
        une = session.scalar(
            select(m.SeanceHebdo).where(m.SeanceHebdo.version_id == suivi.version_id)
        )
        session.add(
            m.Occurrence(
                version_id=suivi.version_id,
                besoin_id=une.besoin_id,
                date=dt.date(2026, 10, 3),  # un samedi
                debut_min=14 * 60,
                fin_min=16 * 60,
                salle_id=une.salle_id,
                origine=OrigineOccurrence.MANUELLE,
                commentaire="Rattrapage saisi à la main",
            )
        )
    with session_base() as session:
        rapport = instancier_version(session, suivi.version_id)
    apres = occurrences(suivi.version_id)
    assert rapport.nb_occurrences == len(avant) and rapport.nb_conservees == 1
    assert len(apres) == len(avant) + 1
    manuelles = [o for o in apres if o.origine is OrigineOccurrence.MANUELLE]
    assert [o.commentaire for o in manuelles] == ["Rattrapage saisi à la main"]

    def cle(o):
        return o.besoin_id, o.date, o.debut_min, o.salle_id, o.statut, o.origine

    recalculees = [o for o in apres if o.origine is not OrigineOccurrence.MANUELLE]
    assert sorted(map(cle, recalculees)) == sorted(map(cle, avant))


def _semaine(occurrence: m.Occurrence) -> dt.date:
    """Lundi de la semaine d'origine d'une séance datée."""
    return lundi_de(occurrence.date_initiale or occurrence.date)


def test_une_retouche_reste_la_seule_seance_de_sa_semaine(lanceur, jeu_minimal):
    """Séance déplacée ou annulée à la main : la semaine type n'est pas recréée à côté."""
    suivi = generer(jeu_minimal.semestre_id)
    avant = occurrences(suivi.version_id)
    planifiees = [
        o
        for o in avant
        if o.statut is StatutOccurrence.PLANIFIEE and o.origine is OrigineOccurrence.INSTANCIATION
    ]
    deplacee = next(o for o in planifiees if dt.date(2026, 10, 5) <= o.date <= dt.date(2026, 10, 9))
    annulee = next(
        o
        for o in planifiees
        if o.seance_hebdo_id != deplacee.seance_hebdo_id
        and dt.date(2026, 10, 12) <= o.date <= dt.date(2026, 10, 16)
    )
    with session_base() as session:
        # Comme le fait l'interface : la séance part à la semaine suivante, l'autre est annulée.
        ligne = session.get(m.Occurrence, deplacee.id)
        ligne.origine, ligne.statut = OrigineOccurrence.MANUELLE, StatutOccurrence.DEPLACEE
        ligne.date_initiale, ligne.date = deplacee.date, deplacee.date + dt.timedelta(days=8)
        ligne = session.get(m.Occurrence, annulee.id)
        ligne.origine, ligne.statut = OrigineOccurrence.MANUELLE, StatutOccurrence.ANNULEE

    resultat = lanceur.invoke(cli.app, ["instancier", "--version", str(suivi.version_id)])
    assert resultat.exit_code == 0, resultat.output
    assert "2 séance(s) de la semaine type n'ont pas été recréées" in resultat.output

    apres = occurrences(suivi.version_id)
    assert len(apres) == len(avant)  # ni doublon, ni perte
    for retouche in (deplacee, annulee):
        de_la_seance = [o for o in apres if o.seance_hebdo_id == retouche.seance_hebdo_id]
        semaines = [_semaine(o) for o in de_la_seance]
        assert len(semaines) == len(set(semaines)), "une seule séance datée par semaine"
        (seule,) = [o for o in de_la_seance if _semaine(o) == lundi_de(retouche.date)]
        assert seule.id == retouche.id and seule.origine is OrigineOccurrence.MANUELLE
    assert {o.id: o.statut for o in apres}[annulee.id] is StatutOccurrence.ANNULEE

    # Relancer ne change plus rien.
    with session_base() as session:
        rapport = instancier_version(session, suivi.version_id)
    assert (rapport.nb_couvertes, rapport.nb_conservees) == (2, 2)
    assert len(occurrences(suivi.version_id)) == len(avant)


def test_seances_datees_importees_jamais_recalculees(lanceur, jeu_minimal):
    """Une version dont les séances datées ont été importées n'est pas instanciée à côté."""
    suivi = generer(jeu_minimal.semestre_id)
    with session_base() as session:
        # Comme l'import en mode « référence » : des séances datées sans séance hebdomadaire.
        session.execute(delete(m.Occurrence).where(m.Occurrence.version_id == suivi.version_id))
        une = session.scalar(
            select(m.SeanceHebdo).where(m.SeanceHebdo.version_id == suivi.version_id)
        )
        session.add(
            m.Occurrence(
                version_id=suivi.version_id,
                besoin_id=une.besoin_id,
                date=dt.date(2026, 10, 6),
                debut_min=une.debut_min,
                fin_min=une.fin_min,
                salle_id=une.salle_id,
                origine=OrigineOccurrence.IMPORT,
            )
        )
    with session_base() as session, pytest.raises(ErreurRegleMetier, match="ont été importées"):
        instancier_version(session, suivi.version_id)
    resultat = lanceur.invoke(cli.app, ["instancier", "--version", str(suivi.version_id)])
    assert resultat.exit_code == 1
    assert "les recalculer depuis la semaine type les doublerait" in resultat.output
    (seule,) = occurrences(suivi.version_id)  # rien n'a été ajouté ni retiré
    assert seule.origine is OrigineOccurrence.IMPORT


def test_une_seance_de_la_semaine_type_ne_prend_pas_la_place_d_une_retouche(jeu_minimal):
    """La salle et l'heure d'une séance sont prises par une retouche : elle est replacée."""
    suivi = generer(jeu_minimal.semestre_id)
    semaine = [
        o
        for o in occurrences(suivi.version_id)
        if dt.date(2026, 10, 5) <= o.date <= dt.date(2026, 10, 9)
        and o.statut is StatutOccurrence.PLANIFIEE
    ]
    genee = semaine[0]
    retouche = next(
        o
        for o in semaine
        if o.seance_hebdo_id != genee.seance_hebdo_id
        and (o.date, o.debut_min) != (genee.date, genee.debut_min)
    )
    with session_base() as session:
        # Retouche à la main : la séance vient occuper la salle de l'autre, au même moment.
        ligne = session.get(m.Occurrence, retouche.id)
        ligne.origine, ligne.statut = OrigineOccurrence.MANUELLE, StatutOccurrence.DEPLACEE
        ligne.date_initiale, ligne.date = retouche.date, genee.date
        ligne.debut_min, ligne.fin_min = genee.debut_min, genee.debut_min + 60
        ligne.salle_id = genee.salle_id
    with session_base() as session:
        rapport = instancier_version(session, suivi.version_id)
    assert rapport.nb_conservees == 1

    apres = occurrences(suivi.version_id)
    en_place = next(o for o in apres if o.id == retouche.id)
    assert (en_place.date, en_place.debut_min, en_place.salle_id) == (
        genee.date,
        genee.debut_min,
        genee.salle_id,
    )
    # La séance de la semaine type n'est plus là : elle a été replacée, raison à l'appui.
    (deplacee,) = [
        o
        for o in apres
        if o.seance_hebdo_id == genee.seance_hebdo_id and (o.date_initiale or o.date) == genee.date
    ]
    assert deplacee.origine is OrigineOccurrence.REPLACEMENT
    assert "place prise par une séance saisie à la main" in deplacee.commentaire
    if deplacee.statut is StatutOccurrence.DEPLACEE:
        assert (deplacee.date, deplacee.debut_min, deplacee.salle_id) != (
            genee.date,
            genee.debut_min,
            genee.salle_id,
        )
    # Personne d'autre n'occupe la salle de la retouche à ce moment-là.
    dans_la_salle = [
        o
        for o in apres
        if o.date == en_place.date
        and o.salle_id == en_place.salle_id
        and o.statut.occupe_les_ressources
        and o.debut_min < en_place.fin_min
        and en_place.debut_min < o.fin_min
    ]
    assert [o.id for o in dans_la_salle] == [en_place.id]


def test_replacer_des_occurrences_designees_dans_une_fenetre(jeu_minimal):
    suivi = generer(jeu_minimal.semestre_id)
    cible = next(
        o
        for o in occurrences(suivi.version_id)
        if o.statut is StatutOccurrence.PLANIFIEE
        and dt.date(2026, 10, 5) <= o.date <= dt.date(2026, 10, 9)
    )
    fenetre = (dt.date(2026, 10, 12), dt.date(2026, 10, 16))
    with session_base() as session:
        rapport = replacer_occurrences(session, suivi.version_id, [cible.id], *fenetre)
    assert (rapport.nb_occurrences, rapport.nb_replacees, rapport.nb_a_programmer) == (1, 1, 0)
    with session_base() as session:
        ligne = session.get(m.Occurrence, cible.id)
        assert fenetre[0] <= ligne.date <= fenetre[1]
        assert ligne.statut is StatutOccurrence.DEPLACEE
        assert ligne.origine is OrigineOccurrence.REPLACEMENT
        assert ligne.date_initiale == cible.date
        dates = verifier_occurrences_version(session, suivi.version_id)
        assert dates.valide, [a.message for a in dates.anomalies if a.bloquante]

    with session_base() as session, pytest.raises(ErreurIntrouvable):
        replacer_occurrences(session, suivi.version_id, [999_999])


def test_replacement_impossible_passe_a_programmer(jeu_minimal):
    suivi = generer(jeu_minimal.semestre_id)
    cible = occurrences(suivi.version_id)[0]
    dimanche = dt.date(2026, 10, 11)  # aucun créneau de la grille ce jour-là
    with session_base() as session:
        rapport = replacer_occurrences(session, suivi.version_id, [cible.id], dimanche, dimanche)
    assert (rapport.nb_replacees, rapport.nb_a_programmer) == (0, 1)
    (entree,) = rapport.a_programmer
    assert entree.occurrence_id == cible.id and entree.date_origine == cible.date
    assert entree.besoin and "aucun créneau" in entree.raison
    with session_base() as session:
        ligne = session.get(m.Occurrence, cible.id)
        assert ligne.statut is StatutOccurrence.A_PROGRAMMER and ligne.date == cible.date
        assert "À programmer" in ligne.commentaire


# --------------------------------------------------------------------------- #
# Pré-vérifications et contrôle du résultat
# --------------------------------------------------------------------------- #


def _rendre_impossible(semestre_id: int) -> None:
    """Demande 60 séances par semaine à un groupe : aucune semaine ne peut les contenir."""
    with session_base() as session:
        besoin = session.scalar(
            select(m.Besoin).where(m.Besoin.mode_volume == ModeVolume.PAR_SEMAINE)
        )
        besoin.nombre = 60


def test_pre_verifier_semestre(jeu_minimal):
    with session_base() as session:
        assert not [
            a
            for a in generation.pre_verifier_semestre(session, jeu_minimal.semestre_id)
            if a.gravite is Gravite.ERREUR
        ]
    _rendre_impossible(jeu_minimal.semestre_id)
    with session_base() as session:
        anomalies = generation.pre_verifier_semestre(session, jeu_minimal.semestre_id)
    erreurs = [a for a in anomalies if a.gravite is Gravite.ERREUR]
    assert erreurs and any("c'est impossible" in a.message for a in erreurs)


def test_calcul_refuse_par_les_pre_verifications(jeu_minimal):
    _rendre_impossible(jeu_minimal.semestre_id)
    appels = []

    def moteur_espion(instantane, options, arret=None):
        appels.append(instantane)
        return ResultatSolveur(StatutSolveur.SOLUTION)

    suivi = generer(jeu_minimal.semestre_id, moteur=moteur_espion)
    assert suivi.etat is EtatCalcul.INFAISABLE
    assert appels == []  # le moteur n'a pas été appelé
    assert "Le calcul n'a pas été lancé" in suivi.message
    assert suivi.statistiques["refuse"] is True and suivi.statistiques["impossibilites"]
    assert suivi.version_id is None

    # Le vrai moteur refuse de la même façon s'il est appelé directement.
    suivi = generer(jeu_minimal.semestre_id)
    assert suivi.etat is EtatCalcul.INFAISABLE and suivi.version_id is None


def test_defaut_du_moteur_releve_par_le_verificateur(jeu_minimal):
    def moteur_fautif(instantane, options, arret=None):
        """Toutes les séances au même moment, dans la même salle."""
        salle = instantane.salles[0].id
        return ResultatSolveur(
            StatutSolveur.SOLUTION,
            placements=tuple(
                Placement(seance.id, 0, 510, 510 + seance.duree, salle)
                for seance in instantane.seances
            ),
        )

    suivi = generer(jeu_minimal.semestre_id, moteur=moteur_fautif)
    assert suivi.etat is EtatCalcul.ERREUR
    assert "défaut du moteur" in suivi.message
    assert suivi.version_id is None
    with session_base() as session:
        assert session.scalar(select(func.count()).select_from(m.VersionEdt)) == 0
    journal = open(suivi.fichier_journal, encoding="utf-8").read()
    assert "Défaut du moteur, solution refusée [SALLE_UNIQUE]" in journal


def test_arret_avec_une_solution_la_version_est_enregistree(jeu_minimal):
    """Le moteur rend la solution trouvée malgré l'arrêt : le calcul réussit et l'enregistre."""
    demarre = threading.Event()

    def moteur_qui_conserve(instantane, options, arret=None):
        demarre.set()
        assert arret.wait(DELAI)  # l'utilisateur demande l'arrêt pendant l'amélioration
        resultat = generation.solveur.resoudre(instantane, options, arret=None)
        return dataclasses.replace(
            resultat,
            message=f"{resultat.message} {MESSAGE_SOLUTION_CONSERVEE}",
            avertissements=(*resultat.avertissements, MESSAGE_SOLUTION_CONSERVEE),
            statistiques={**resultat.statistiques, "arrete_par_utilisateur": True},
        )

    identifiant = generation.lancer_calcul(
        jeu_minimal.semestre_id, OPTIONS, moteur=moteur_qui_conserve
    )
    assert demarre.wait(DELAI)
    assert generation.demander_arret(identifiant).arret_demande
    suivi = generation.attendre_calcul(identifiant, DELAI)

    assert suivi.etat is EtatCalcul.REUSSI and suivi.arret_demande
    assert suivi.version_id is not None
    assert suivi.statistiques["arrete_par_utilisateur"] is True
    assert MESSAGE_SOLUTION_CONSERVEE in suivi.avertissements
    assert MESSAGE_SOLUTION_CONSERVEE in suivi.message
    with session_base() as session:
        version = session.get(m.VersionEdt, suivi.version_id)
        assert version.active and len(version.seances) == 17
    assert occurrences(suivi.version_id)  # les séances datées sont produites aussi


def test_graines_differentes_solutions_differentes_sur_le_jeu_minimal(jeu_minimal):
    """« Proposer une autre solution » : une autre graine suffit, même avec un seul fil."""
    with session_base() as session:
        instantane = charger_instantane(session, jeu_minimal.semestre_id)
    solutions = [
        generation.solveur.resoudre(
            instantane, dataclasses.replace(OPTIONS, graine=graine, utiliser_indices=False)
        ).placements
        for graine in (1, 2, 3, 4, 5, 6, 1)
    ]
    assert all(solutions)
    assert len(set(solutions[:6])) >= 5
    assert solutions[0] == solutions[6]  # même graine, même solution


def test_verrou_decoche_le_verrou_ne_suit_pas_la_seance_deplacee(jeu_minimal):
    """« Séances verrouillées » décochée : une séance déplacée n'est plus marquée verrouillée."""
    premiere = generer(jeu_minimal.semestre_id)
    with session_base() as session:
        avant = {}
        for seance in session.scalars(
            select(m.SeanceHebdo).where(m.SeanceHebdo.version_id == premiere.version_id)
        ):
            seance.verrou = True
            avant[(seance.besoin_id, seance.rang)] = (
                seance.jour,
                seance.debut_min,
                seance.fin_min,
                seance.salle_id,
            )
    libre = dataclasses.replace(
        OPTIONS, contraintes_desactivees=frozenset({"VERROU"}), utiliser_indices=False, graine=7
    )
    seconde = generer(jeu_minimal.semestre_id, libre)
    assert seconde.etat is EtatCalcul.REUSSI, seconde.message
    with session_base() as session:
        apres = list(
            session.scalars(
                select(m.SeanceHebdo).where(m.SeanceHebdo.version_id == seconde.version_id)
            )
        )
        deplacees = 0
        for seance in apres:
            place = (seance.jour, seance.debut_min, seance.fin_min, seance.salle_id)
            restee = place == avant[(seance.besoin_id, seance.rang)]
            deplacees += not restee
            assert seance.verrou is restee
    assert deplacees > 0, "le moteur devait déplacer au moins une séance"

    # Verrous actifs : tout reste en place, et tout reste verrouillé.
    with session_base() as session:
        session.get(m.VersionEdt, seconde.version_id).active = False
        session.get(m.VersionEdt, premiere.version_id).active = True
    troisieme = generer(jeu_minimal.semestre_id)
    with session_base() as session:
        for seance in session.scalars(
            select(m.SeanceHebdo).where(m.SeanceHebdo.version_id == troisieme.version_id)
        ):
            place = (seance.jour, seance.debut_min, seance.fin_min, seance.salle_id)
            assert seance.verrou and place == avant[(seance.besoin_id, seance.rang)]


def test_avertissements_joints_au_suivi(jeu_minimal):
    def moteur_bavard(instantane, options, arret=None):
        return ResultatSolveur(StatutSolveur.TEMPS_DEPASSE, avertissements=("Remarque du moteur.",))

    suivi = generer(jeu_minimal.semestre_id, moteur=moteur_bavard)
    assert suivi.etat is EtatCalcul.TEMPS_DEPASSE
    assert "Remarque du moteur." in suivi.avertissements


# --------------------------------------------------------------------------- #
# Calcul abandonné par son processus
# --------------------------------------------------------------------------- #


def _calcul_fictif(semestre_id: int, **champs) -> int:
    """Inscrit en base une ligne de calcul telle quelle (suivi ancien, abandonné ou corrompu).

    Par défaut : « en cours », lancé à l'instant avec les options des tests,
    sans statistiques ; `champs` remplace n'importe quelle colonne.
    """
    valeurs = {
        "semestre_id": semestre_id,
        "etat": EtatCalcul.EN_COURS,
        "demarre_le": dt.datetime.now(),
        "parametres": OPTIONS.vers_dict(),
        "contraintes_desactivees": [],
        "statistiques": {},
        "message": "",
        **champs,
    }
    with session_base() as session:
        calcul = m.Calcul(**valeurs)
        session.add(calcul)
        session.flush()
        return calcul.id


def _calcul_en_cours_d_un_autre_processus(semestre_id: int, pid: int, **champs) -> int:
    """Inscrit en base un calcul « en cours » que le processus `pid` est censé exécuter."""
    demarre_le = champs.pop("demarre_le", dt.datetime.now())
    statistiques = {
        generation.CLE_PROCESSUS: pid,
        generation.CLE_DEMARRAGE: demarre_le.timestamp(),
    }
    return _calcul_fictif(semestre_id, demarre_le=demarre_le, statistiques=statistiques, **champs)


def _etat(calcul_id: int) -> EtatCalcul:
    with session_base() as session:
        return EtatCalcul(session.get(m.Calcul, calcul_id).etat)


def test_calcul_abandonne_par_un_processus_mort_ne_bloque_plus(jeu_minimal):
    """Commande interrompue ou processus tué : le calcul est clos au prochain lancement."""
    mort = subprocess.Popen([sys.executable, "-c", "pass"])
    mort.wait()
    abandonne = _calcul_en_cours_d_un_autre_processus(jeu_minimal.semestre_id, mort.pid)

    suivi = generer(jeu_minimal.semestre_id)  # n'est pas refusé
    assert suivi.etat is EtatCalcul.REUSSI, suivi.message
    with session_base() as session:
        ancien = session.get(m.Calcul, abandonne)
        assert EtatCalcul(ancien.etat) is EtatCalcul.ERREUR
        assert ancien.message == generation.MESSAGE_INTERROMPU and ancien.termine_le is not None

    # Même chose par les autres portes : l'état lu et le bouton « Arrêter ».
    autre = _calcul_en_cours_d_un_autre_processus(jeu_minimal.semestre_id, mort.pid)
    assert generation.calcul_en_cours() is None and _etat(autre) is EtatCalcul.ERREUR
    dernier = _calcul_en_cours_d_un_autre_processus(jeu_minimal.semestre_id, mort.pid)
    assert generation.demander_arret(dernier).etat is EtatCalcul.ERREUR


def test_calcul_vivant_d_un_autre_processus_n_est_pas_clos(jeu_minimal):
    """Une commande lancée pendant que le service calcule ne clôt pas le calcul du service."""
    vivant = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        calcul_id = _calcul_en_cours_d_un_autre_processus(jeu_minimal.semestre_id, vivant.pid)
        assert generation.cloturer_calculs_interrompus() == 0
        assert generation.calcul_en_cours().id == calcul_id
        with pytest.raises(ErreurCalculEnCours):
            generation.lancer_calcul(jeu_minimal.semestre_id, OPTIONS)
        assert generation.demander_arret(calcul_id).etat is EtatCalcul.EN_COURS
    finally:
        vivant.kill()
        vivant.wait()
    # Le processus a disparu : le calcul est clos, plus rien ne bloque.
    assert generation.cloturer_calculs_interrompus() == 1
    assert _etat(calcul_id) is EtatCalcul.ERREUR


def test_calcul_en_cours_bien_au_dela_de_son_temps_limite_est_clos(jeu_minimal):
    """Numéro de processus réattribué : passé le délai de grâce, le calcul ne peut plus vivre."""
    il_y_a_longtemps = dt.datetime.now() - dt.timedelta(
        seconds=OPTIONS.temps_limite_s + generation.DELAI_DE_GRACE_S + 60
    )
    vivant = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        calcul_id = _calcul_en_cours_d_un_autre_processus(
            jeu_minimal.semestre_id, vivant.pid, demarre_le=il_y_a_longtemps
        )
        assert generation.cloturer_calculs_interrompus() == 1
        assert _etat(calcul_id) is EtatCalcul.ERREUR
    finally:
        vivant.kill()
        vivant.wait()


def test_commande_generer_interrompue_au_clavier(lanceur, jeu_minimal, monkeypatch):
    """Ctrl+C pendant l'attente : l'arrêt est demandé, le calcul se termine, rien ne reste en cours."""

    def moteur_qui_attend_l_arret(instantane, options, arret=None):
        arret.wait(DELAI)
        return ResultatSolveur(StatutSolveur.ARRETE)

    attendre = generation.attendre_calcul
    appels = []

    def attendre_puis_ctrl_c(calcul_id, delai_s=None):
        appels.append(calcul_id)
        if len(appels) == 1:
            raise KeyboardInterrupt
        return attendre(calcul_id, delai_s)

    monkeypatch.setattr(solveur, "resoudre", moteur_qui_attend_l_arret)
    monkeypatch.setattr(generation, "attendre_calcul", attendre_puis_ctrl_c)
    debut = time.perf_counter()
    resultat = lanceur.invoke(cli.app, ["generer", "--semestre", str(jeu_minimal.semestre_id)])
    assert time.perf_counter() - debut < DELAI / 2, "l'arrêt n'a pas atteint le moteur"
    assert resultat.exit_code == 1, resultat.output
    assert "Interruption demandée" in resultat.output
    assert len(appels) == 2
    assert generation.calcul_en_cours() is None
    with session_base() as session:
        (calcul,) = session.scalars(select(m.Calcul)).all()
        assert calcul.arret_demande and EtatCalcul(calcul.etat) is EtatCalcul.ARRETE


def test_attente_d_un_calcul_bornee_par_son_delai(jeu_minimal):
    """L'attente par tranches rend la main au délai demandé, le calcul encore en cours."""
    liberer = threading.Event()

    def moteur_lent(instantane, options, arret=None):
        liberer.wait(DELAI)
        return ResultatSolveur(StatutSolveur.TEMPS_DEPASSE)

    calcul_id = generation.lancer_calcul(jeu_minimal.semestre_id, OPTIONS, moteur=moteur_lent)
    try:
        debut = time.perf_counter()
        assert generation.attendre_calcul(calcul_id, 0.7).en_cours
        assert 0.6 < time.perf_counter() - debut < 5
    finally:
        liberer.set()
    assert generation.attendre_calcul(calcul_id, DELAI).etat is EtatCalcul.TEMPS_DEPASSE


# --------------------------------------------------------------------------- #
# Ligne de commande
# --------------------------------------------------------------------------- #


def _lignes_de_bilan(sortie: str) -> int:
    """Nombre de lignes que la commande consacre elle-même au bilan de qualité."""
    return sum(
        1 for ligne in sortie.splitlines() if ligne.startswith("Bilan sur l'ensemble du semestre")
    )


def test_commande_generer_puis_instancier(lanceur, jeu_minimal):
    semestre = str(jeu_minimal.semestre_id)
    resultat = lanceur.invoke(cli.app, ["pre-verifier", "--semestre", semestre])
    assert resultat.exit_code == 0, resultat.output
    assert "rien n'empêche de lancer le calcul" in resultat.output

    resultat = lanceur.invoke(
        cli.app,
        ["generer", "--semestre", semestre, "--sans-optimiser", "--temps-limite", "30"]
        + ["--graine", "2", "--fils", "1"],
    )
    assert resultat.exit_code == 0, resultat.output
    assert "sans optimisation" in resultat.output
    assert "Réussi" in resultat.output and "17 séance(s) placée(s)" in resultat.output
    message = next(ligne for ligne in resultat.output.splitlines() if ligne.startswith("Emploi"))
    # Sans amélioration, le message du calcul ne porte pas de bilan : la commande le donne.
    assert "Bilan sur l'ensemble du semestre :" not in message
    assert _lignes_de_bilan(resultat.output) == 1
    assert "Version enregistrée : n° 1." in resultat.output

    resultat = lanceur.invoke(cli.app, ["instancier", "--semestre", semestre])
    assert resultat.exit_code == 0, resultat.output
    assert "Version n° 1 :" in resultat.output and "séance(s) datée(s)" in resultat.output


def test_commande_generer_avec_optimisation_et_contrainte_desactivee(lanceur, jeu_minimal):
    resultat = lanceur.invoke(
        cli.app,
        [
            "generer",
            "--semestre",
            str(jeu_minimal.semestre_id),
            "--optimiser",
            "--temps-limite",
            "5",
            "-d",
            "PAUSE",
        ],
    )
    assert resultat.exit_code == 0, resultat.output
    assert "avec optimisation" in resultat.output
    assert "Contraintes ignorées à votre demande : Pause entre deux séances" in resultat.output
    # Le bilan est dans le message du calcul : la commande ne le répète pas sur sa propre ligne.
    message = next(ligne for ligne in resultat.output.splitlines() if ligne.startswith("Emploi"))
    assert "Bilan sur l'ensemble du semestre :" in message and "le samedi" in message
    assert _lignes_de_bilan(resultat.output) == 0


def test_commande_generer_ameliore_par_defaut(lanceur, jeu_minimal):
    """Sans option, la commande suit le réglage « objectif_actif », actif par défaut."""
    with session_base() as session:
        reinitialiser_parametres(session)  # le jeu minimal des tests coupe l'amélioration
    resultat = lanceur.invoke(
        cli.app, ["generer", "--semestre", str(jeu_minimal.semestre_id), "--temps-limite", "20"]
    )
    assert resultat.exit_code == 0, resultat.output
    assert "avec optimisation" in resultat.output
    message = next(ligne for ligne in resultat.output.splitlines() if ligne.startswith("Emploi"))
    assert "Bilan sur l'ensemble du semestre :" in message
    assert _lignes_de_bilan(resultat.output) == 0
    # Un si petit problème va d'ordinaire jusqu'à l'optimum ; sur une machine chargée, l'arrêt
    # sur stagnation (actif par défaut) peut couper avant la preuve : les deux issues disent
    # pourquoi l'amélioration s'est arrêtée.
    with session_base() as session:
        (calcul,) = session.scalars(select(m.Calcul)).all()
        raison = calcul.statistiques["raison_arret"]
        assert calcul.parametres["arret_sur_stagnation"] is True
    assert raison in ("optimum_prouve", "stagnation"), raison
    attendu = {
        "optimum_prouve": "On ne peut pas faire mieux avec les préférences actuelles.",
        "stagnation": "L'amélioration s'est arrêtée d'elle-même",
    }[raison]
    assert attendu in message


def test_commande_generer_echec_code_1(lanceur, jeu_minimal):
    _rendre_impossible(jeu_minimal.semestre_id)
    semestre = str(jeu_minimal.semestre_id)
    resultat = lanceur.invoke(cli.app, ["pre-verifier", "--semestre", semestre])
    assert resultat.exit_code == 1 and "le calcul serait refusé" in resultat.output
    resultat = lanceur.invoke(cli.app, ["generer", "--semestre", semestre])
    assert resultat.exit_code == 1, resultat.output
    assert "Impossible (prouvé)" in resultat.output


# --------------------------------------------------------------------------- #
# Cas limites (banque de tests : `pytest -m cas_limite`)
# --------------------------------------------------------------------------- #


def _nb_lignes(table) -> int:
    with session_base() as session:
        return session.scalar(select(func.count()).select_from(table))


def _journalise(caplog, texte: str, niveau: int = logging.WARNING) -> list[logging.LogRecord]:
    """Enregistrements du journal de ce niveau dont le message contient `texte`."""
    return [r for r in caplog.records if r.levelno == niveau and texte in r.getMessage()]


def _sortie_complete(resultat) -> str:
    """Sortie standard et sortie d'erreur d'une commande, quelle que soit la version de Click."""
    texte = resultat.output
    try:
        texte += resultat.stderr
    except ValueError:  # Click < 8.2 : la sortie d'erreur est déjà mêlée à la sortie standard
        pass
    return texte


@pytest.mark.cas_limite
def test_options_hors_bornes_refusees_par_la_commande(lanceur, jeu_minimal):
    """Cas : `generer --temps-limite nan`, `--temps-limite inf` ou `--fils 1000` (Typer accepte
    nan et inf comme flottants).

    Risque : « Aucun emploi du temps trouvé en nan s » après quelques millisecondes, un calcul
    sans fin dont l'échéance d'abandon déborde, ou mille fils qui épuisent la mémoire du serveur.
    """
    semestre = str(jeu_minimal.semestre_id)
    for arguments, attendu in (
        (["--temps-limite", "nan"], "temps limite"),
        (["--temps-limite", "inf"], "temps limite"),
        (["--fils", "1000"], "fils de calcul"),
    ):
        resultat = lanceur.invoke(cli.app, ["generer", "--semestre", semestre, *arguments])
        assert resultat.exit_code == 1, resultat.output
        assert attendu in _sortie_complete(resultat)
    assert _nb_lignes(m.Calcul) == 0


@pytest.mark.cas_limite
def test_temps_limite_enregistre_illisible_ne_bloque_pas_le_suivi(jeu_minimal, processus_vivant):
    """Cas : calcul « en cours » d'un autre processus vivant dont le temps limite enregistré vaut
    l'infini ou un texte (ligne ancienne ou corrompue).

    Risque : `timedelta(seconds=inf)` lève OverflowError et `float("abc")` ValueError dans le
    contrôle des calculs abandonnés : `calcul_en_cours()` plante, et toutes les pages avec lui.
    """
    for temps in (float("inf"), "abc"):
        calcul_id = _calcul_en_cours_d_un_autre_processus(
            jeu_minimal.semestre_id,
            processus_vivant.pid,
            parametres={**OPTIONS.vers_dict(), "temps_limite_s": temps},
        )
        assert generation.calcul_en_cours().id == calcul_id
        assert generation.cloturer_calculs_interrompus() == 0
        with session_base() as session:
            session.get(m.Calcul, calcul_id).etat = EtatCalcul.ARRETE


@pytest.mark.cas_limite
def test_identifiant_hors_bornes_est_introuvable(jeu_minimal):
    """Cas : identifiant 2^70 (hors bornes des entiers SQLite) donné au suivi, à l'arrêt, au
    lancement, aux pré-vérifications, à l'instanciation ou au replacement.

    Risque : `session.get` lève OverflowError au lieu de répondre « introuvable » : page 500
    « erreur inattendue » avec trace, alors que -1 est correctement refusé.
    """
    enorme = 2**70
    for appel in (
        lambda: generation.etat_calcul(enorme),
        lambda: generation.demander_arret(enorme),
        lambda: generation.attendre_calcul(enorme, 0.1),
        lambda: generation.lancer_calcul(enorme, OPTIONS),
    ):
        with pytest.raises(ErreurIntrouvable):
            appel()
    assert generation.dernier_calcul(enorme) is None
    with session_base() as session:
        for appel in (
            lambda: instancier_version(session, enorme),
            lambda: replacer_occurrences(session, enorme, [1]),
            lambda: generation.pre_verifier_semestre(session, enorme),
        ):
            with pytest.raises(ErreurIntrouvable):
                appel()
    assert _nb_lignes(m.Calcul) == 0


@pytest.mark.cas_limite
@pytest.mark.xfail(
    strict=True,
    reason="à corriger par le lot vérificateur : src/edt/services/verification.py "
    "(version_a_verifier lève OverflowError pour un identifiant hors bornes SQLite)",
)
def test_version_a_verifier_identifiant_hors_bornes_est_introuvable(jeu_minimal):
    """Cas : `version_a_verifier(session, version_id=2**70)`.

    Risque : OverflowError de SQLite au lieu d'ErreurIntrouvable : page 500 au lieu de 404.
    """
    with session_base() as session, pytest.raises(ErreurIntrouvable):
        version_a_verifier(session, version_id=2**70)


@pytest.mark.cas_limite
@pytest.mark.xfail(
    strict=True,
    reason="à corriger par le lot règles personnalisées : src/edt/services/regles.py "
    "(_resoudre_cible lève OverflowError pour une cible numérique hors bornes SQLite)",
)
def test_regle_dont_la_cible_est_hors_bornes_est_invalide(jeu_minimal):
    """Cas : brouillon de règle dont la cible est l'identifiant numérique 10^20.

    Risque : OverflowError de SQLite au lieu d'une validation « portee.cible » invalide : page
    500 sur l'écran des règles.
    """
    brouillon = {
        "type": "JOURS_TRAVAILLES",
        "portee": {"nature": "groupe", "cible": 10**20},
        "parametres": {"jours": ["lundi"]},
    }
    with session_base() as session:
        validation = service_regles.valider(session, brouillon)
    assert not validation.valide and "portee.cible" in validation.erreurs


@pytest.mark.cas_limite
def test_donnees_modifiees_pendant_le_calcul_sont_dites(jeu_minimal, caplog):
    """Cas : un besoin du semestre est supprimé pendant que le moteur calcule (les écrans Données
    ne consultent pas le calcul en cours).

    Risque : IntegrityError à l'enregistrement de la version, état « erreur inattendue » avec
    trace, minutes de calcul perdues sans que la cause soit dite.
    """
    demarre, liberer = threading.Event(), threading.Event()

    def moteur_bloque(instantane, options, arret=None):
        demarre.set()
        assert liberer.wait(DELAI)
        return generation.solveur.resoudre(instantane, options, arret)

    calcul_id = generation.lancer_calcul(jeu_minimal.semestre_id, OPTIONS, moteur=moteur_bloque)
    assert demarre.wait(DELAI)
    with session_base() as session:
        besoin_id = session.scalar(
            select(m.Besoin.id).where(m.Besoin.semestre_id == jeu_minimal.semestre_id)
        )
        session.execute(delete(m.Besoin).where(m.Besoin.id == besoin_id))
    liberer.set()
    suivi = generation.attendre_calcul(calcul_id, DELAI)
    assert suivi.etat is EtatCalcul.ERREUR, suivi.message
    assert "ont changé pendant le calcul" in suivi.message
    assert suivi.version_id is None and _nb_lignes(m.VersionEdt) == 0
    assert not _journalise(caplog, "inattendue", logging.ERROR)


@pytest.mark.cas_limite
def test_semestre_sans_besoin_ne_cree_pas_de_version_vide(jeu_minimal):
    """Cas : Générer sur un semestre sans aucun besoin, qui a une version de référence active.

    Risque : « Aucune séance à placer » pris pour un succès, version vide enregistrée et activée,
    l'ancienne désactivée : l'emploi du temps affiché se vide sans confirmation.
    """
    with session_base() as session:
        session.execute(delete(m.Besoin).where(m.Besoin.semestre_id == jeu_minimal.semestre_id))
        session.add(
            m.VersionEdt(
                semestre_id=jeu_minimal.semestre_id,
                numero=1,
                libelle="Référence importée",
                origine=OrigineVersion.REFERENCE,
                active=True,
            )
        )
    suivi = generer(jeu_minimal.semestre_id)
    assert suivi.etat is EtatCalcul.ERREUR and suivi.version_id is None
    assert "aucune séance à placer" in suivi.message
    with session_base() as session:
        (version,) = session.scalars(select(m.VersionEdt)).all()
        assert version.libelle == "Référence importée" and version.active


@pytest.mark.cas_limite
def test_journal_de_calcul_impossible_donne_la_vraie_cause(jeu_minimal, environnement, caplog):
    """Cas : `<EDT_LOG_DIR>/generations` est un fichier (ou un dossier non inscriptible).

    Risque : tout calcul échouerait « Le détail est dans le journal du calcul » avec un journal
    inexistant ; plus aucun emploi du temps productible tant que personne ne lit edt.log.
    """
    configurer_journalisation()
    (environnement / "logs" / "generations").write_text("pas un dossier", encoding="utf-8")
    suivi = generer(jeu_minimal.semestre_id)
    assert suivi.etat is EtatCalcul.REUSSI, suivi.message
    assert suivi.fichier_journal is None and suivi.version_id is not None
    (alerte,) = [a for a in suivi.avertissements if "Journal dédié du calcul indisponible" in a]
    assert "EDT_LOG_DIR" in alerte
    assert _journalise(caplog, "indisponible")


@pytest.mark.cas_limite
@pytest.mark.xfail(
    strict=True,
    reason="à corriger par le lot socle : src/edt/commandes/base.py (doctor ne teste que le "
    "dossier racine des journaux, pas generations/)",
)
def test_doctor_signale_le_dossier_des_journaux_de_calcul_inutilisable(
    lanceur, base, environnement
):
    """Cas : `doctor` alors que `<EDT_LOG_DIR>/generations` est un fichier.

    Risque : « Journaux accessibles » et « Tout est prêt » alors qu'aucun calcul n'aura de journal.
    """
    configurer_journalisation()
    (environnement / "logs" / "generations").write_text("pas un dossier", encoding="utf-8")
    resultat = lanceur.invoke(cli.app, ["doctor"])
    lignes = [ligne for ligne in resultat.output.splitlines() if "generations" in ligne]
    assert resultat.exit_code == 1 and lignes and all("ECHEC" in ligne for ligne in lignes)


@pytest.mark.cas_limite
def test_un_calcul_illisible_est_clos_sans_bloquer_l_application(jeu_minimal, caplog):
    """Cas : lignes de calcul aux JSON mal typés : « en cours » avec statistiques en texte et temps
    limite « abc » ; « réussi » avec avertissements en texte, niveaux « pause », niveaux de règles
    et règles illisibles, contraintes désactivées en texte.

    Risque : AttributeError ou ValueError dans le suivi : accueil et écran Calculs en 500,
    lancement impossible jusqu'à une réparation SQL ; avertissements affichés lettre à lettre.
    """
    semestre_id = jeu_minimal.semestre_id
    illisible = _calcul_fictif(
        semestre_id, parametres={"temps_limite_s": "abc"}, statistiques="pas un dict"
    )
    corrompu = _calcul_fictif(
        semestre_id,
        etat=EtatCalcul.REUSSI,
        termine_le=dt.datetime.now(),
        duree_s=1.5,
        parametres={"niveaux": "pause", "niveaux_regles": {"abc": "x"}, "regles": "aucune"},
        contraintes_desactivees="PAUSE",
        statistiques={"avertissements": "Un seul avertissement"},
        message="Réussi malgré tout.",
    )
    assert generation.calcul_en_cours() is None  # la ligne illisible est close, sans plantage
    clos = generation.etat_calcul(illisible)
    assert clos.etat is EtatCalcul.ERREUR and "illisible" in clos.message
    assert _journalise(caplog, "illisible")
    suivi = generation.etat_calcul(corrompu)
    assert suivi.avertissements == ("Un seul avertissement",)
    assert suivi.niveaux == niveaux_depuis_desactivees(["PAUSE"])
    assert suivi.niveaux_regles is None and suivi.regles == ()
    assert generation.dernier_calcul(semestre_id).id == corrompu
    client = TestClient(creer_app())
    for chemin in ("/", "/calculs"):
        assert client.get(chemin).status_code == 200, chemin
    assert generer(semestre_id).etat is EtatCalcul.REUSSI


@pytest.mark.cas_limite
def test_version_enregistree_sans_instanciation_dit_de_relancer_l_instanciation(
    jeu_minimal, monkeypatch, lanceur
):
    """Cas : l'instanciation échoue (exception) ou est interrompue (KeyboardInterrupt) juste après
    l'enregistrement de la version.

    Risque : version active sans aucune séance datée et personne de prévenu (« erreur inattendue »
    ou succès muet) : l'utilisateur regarde un calendrier vide.
    """

    def instanciation_en_panne(session, version_id, *args, **kwargs):
        raise RuntimeError("panne simulée")

    monkeypatch.setattr(generation, "instancier_version", instanciation_en_panne)
    suivi = generer(jeu_minimal.semestre_id)
    assert suivi.etat is EtatCalcul.REUSSI and suivi.version_id is not None
    assert "relancer l'instanciation" in suivi.message
    assert suivi.statistiques["instanciation"] == {"erreur": True}
    assert occurrences(suivi.version_id) == []

    def instanciation_interrompue(session, version_id, *args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(generation, "instancier_version", instanciation_interrompue)
    suivi = generer(jeu_minimal.semestre_id)
    assert suivi.etat is EtatCalcul.ERREUR and suivi.version_id is not None
    assert "relancer l'instanciation" in suivi.message
    assert f"--version {suivi.version_id}" in suivi.message
    # L'instanciation relancée à la main produit les séances datées.
    resultat = lanceur.invoke(cli.app, ["instancier", "--version", str(suivi.version_id)])
    assert resultat.exit_code == 0, resultat.output
    assert occurrences(suivi.version_id)


@pytest.mark.cas_limite
def test_cloture_renseigne_la_duree_et_nomme_la_version_sans_dates(jeu_minimal):
    """Cas : calculs « en cours » abandonnés il y a sept minutes (processus tué), l'un juste après
    l'enregistrement de sa version, clos par `cloturer_calculs_interrompus`.

    Risque : « erreur en 0 s » dans l'historique pour un calcul qui a tourné des minutes, et une
    version active sans séances datées dont personne ne sait qu'il faut relancer l'instanciation.
    """
    il_y_a_7_min = dt.datetime.now() - dt.timedelta(minutes=7)
    sans_version = _calcul_fictif(jeu_minimal.semestre_id, demarre_le=il_y_a_7_min)
    avec_version = _calcul_fictif(
        jeu_minimal.semestre_id,
        demarre_le=il_y_a_7_min,
        statistiques={generation.CLE_DEMARRAGE: il_y_a_7_min.timestamp()},
    )
    with session_base() as session:
        session.add(
            m.VersionEdt(
                semestre_id=jeu_minimal.semestre_id,
                numero=1,
                libelle="Version 1",
                origine=OrigineVersion.GENEREE,
                active=True,
                calcul_id=avec_version,
            )
        )
    assert generation.cloturer_calculs_interrompus() == 2
    for calcul_id in (sans_version, avec_version):
        suivi = generation.etat_calcul(calcul_id)
        assert suivi.etat is EtatCalcul.ERREUR
        assert 415 <= suivi.temps_ecoule_s <= 425, suivi.temps_ecoule_s
    with session_base() as session:
        assert all(c.duree_s is not None for c in session.scalars(select(m.Calcul)))
    suivi = generation.etat_calcul(avec_version)
    assert suivi.version_id is not None and "relancer l'instanciation" in suivi.message
    assert "relancer l'instanciation" not in generation.etat_calcul(sans_version).message


@pytest.mark.cas_limite
def test_lancer_un_calcul_pendant_un_chargement_est_refuse(jeu_minimal):
    """Cas : Générer pendant qu'un chargement de jeu fictif ou une remise à zéro tient le verrou
    de la base (tables supprimées puis recréées).

    Risque : calcul sur des tables en recréation (« no such table », erreur inattendue) ou version
    qui référence des besoins disparus ; l'inverse est refusé et l'asymétrie surprend.
    """
    assert jeux._verrou_base.acquire(blocking=False)
    try:
        with pytest.raises(ErreurRegleMetier, match="chargement de jeu de données"):
            generation.lancer_calcul(jeu_minimal.semestre_id, OPTIONS)
        assert _nb_lignes(m.Calcul) == 0
    finally:
        jeux._verrou_base.release()
    assert generer(jeu_minimal.semestre_id).etat is EtatCalcul.REUSSI


@pytest.mark.cas_limite
@pytest.mark.xfail(
    strict=True,
    reason="à corriger par le lot import et export : src/edt/services/echange.py (aucun marqueur "
    "« import en cours » que lancer_calcul puisse consulter, alors que l'import refuse, lui, un "
    "calcul en cours)",
)
def test_lancer_un_calcul_pendant_un_import_est_refuse(jeu_minimal, monkeypatch):
    """Cas : import de données en cours (fichier en cours de lecture), Générer depuis un autre
    onglet.

    Risque : calcul sur des données en train de changer, version qui référence des besoins
    remplacés ; l'inverse (import pendant un calcul) est refusé, l'asymétrie surprend.
    """
    commence, liberer = threading.Event(), threading.Event()

    def lecture_bloquee(fichier, limites):
        commence.set()
        assert liberer.wait(DELAI)
        raise ErreurDonneesInvalides("fin de l'import simulé")

    def importer():
        with session_base() as session, contextlib.suppress(ErreurDonneesInvalides):
            echange.appliquer_import(
                session, [FichierEnvoye("donnees.xlsx", b"x" * 100)], OptionsImport()
            )

    def moteur_vide(instantane, options, arret=None):
        return ResultatSolveur(StatutSolveur.ARRETE)

    monkeypatch.setattr(echange, "lire_fichier", lecture_bloquee)
    fil = threading.Thread(target=importer)
    fil.start()
    calcul_id = None
    try:
        assert commence.wait(10), "l'import simulé n'a pas commencé"
        with pytest.raises(ErreurRegleMetier, match="import"):
            calcul_id = generation.lancer_calcul(
                jeu_minimal.semestre_id, OPTIONS, moteur=moteur_vide
            )
    finally:
        liberer.set()
        fil.join(DELAI)
        if calcul_id is not None:
            generation.attendre_calcul(calcul_id, DELAI)


_LANCEMENT_CONCURRENT = """
import sys, time
from pathlib import Path
from edt.erreurs import ErreurCalculEnCours
from edt.services import generation
from edt.solveur import OptionsSolveur, ResultatSolveur, StatutSolveur

temoin, pret, semestre = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])

def moteur_lent(instantane, options, arret=None):
    time.sleep(2)
    return ResultatSolveur(StatutSolveur.TEMPS_DEPASSE)

pret.touch()
while not temoin.exists():
    time.sleep(0.001)
try:
    identifiant = generation.lancer_calcul(
        semestre, OptionsSolveur(temps_limite_s=5, nb_workers=1), moteur=moteur_lent
    )
except ErreurCalculEnCours:
    print("REFUSE")
else:
    generation.attendre_calcul(identifiant, 30)
    print("LANCE", identifiant)
"""


@pytest.mark.perf
@pytest.mark.cas_limite
def test_deux_lancements_simultanes_dans_deux_processus(jeu_minimal, tmp_path):
    """Cas : la commande `generer` et le bouton Générer lancent un calcul au même instant depuis
    deux processus (le verrou de `generation` est local à un processus).

    Risque : deux calculs parallèles sur les deux cœurs du serveur, deux versions activées l'une
    après l'autre : la règle « un seul calcul à la fois » ne tiendrait qu'au sein d'un processus.
    """
    temoin = tmp_path / "depart"
    prets = [tmp_path / f"pret-{n}" for n in range(2)]
    environnement = {
        **os.environ,
        "PYTHONPATH": str(Path(edt.__file__).resolve().parents[1]),
        "PYTHONIOENCODING": "utf-8",
    }
    processus = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                _LANCEMENT_CONCURRENT,
                str(temoin),
                str(pret),
                str(jeu_minimal.semestre_id),
            ],
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=environnement,
        )
        for pret in prets
    ]
    echeance = time.monotonic() + DELAI
    while not all(pret.exists() for pret in prets):
        assert time.monotonic() < echeance, "les processus n'ont pas démarré"
        assert all(p.poll() is None for p in processus), "un processus s'est arrêté avant le départ"
        time.sleep(0.01)
    temoin.write_text("partez", encoding="utf-8")
    sorties = [p.communicate(timeout=DELAI)[0].split() for p in processus]
    assert sorted(sortie[0] for sortie in sorties) == ["LANCE", "REFUSE"], sorties
    with session_base() as session:
        calculs = session.scalars(select(m.Calcul)).all()
    assert len(calculs) == 1 and EtatCalcul(calculs[0].etat) is EtatCalcul.TEMPS_DEPASSE


@pytest.mark.cas_limite
def test_solution_sans_salle_ou_a_salle_inconnue_du_moteur_est_refusee(jeu_minimal):
    """Cas : un moteur de remplacement rend des placements sans salle, ou avec une salle inconnue
    de l'instantané (le placement manquant est couvert par `test_solution_incomplete_refusee`).

    Risque : version partielle ou incohérente enregistrée (contraire au tout-ou-rien), ou « erreur
    inattendue » à l'enregistrement.
    """

    def moteur_factice(salle):
        def resoudre_factice(instantane, options, arret=None):
            return ResultatSolveur(
                StatutSolveur.SOLUTION,
                placements=tuple(
                    Placement(s.id, 0, 510, 510 + s.duree, salle) for s in instantane.seances
                ),
            )

        return resoudre_factice

    for salle, attendu in ((None, "sans salle"), (999, "Salle 999")):
        suivi = generer(jeu_minimal.semestre_id, moteur=moteur_factice(salle))
        assert suivi.etat is EtatCalcul.ERREUR and attendu in suivi.message, suivi.message
        assert suivi.version_id is None
    assert _nb_lignes(m.VersionEdt) == 0


@pytest.mark.cas_limite
def test_verrous_contradictoires_toleres_de_bout_en_bout(jeu_minimal):
    """Cas : deux séances verrouillées sur le même créneau et la même salle dans la version active ;
    nouveau calcul sans l'option, puis avec « tolérer les violations héritées ».

    Risque : refus sans le conseil de tolérer, ou solution refusée par le vérificateur (« défaut du
    moteur ») alors que l'utilisateur a demandé de tolérer ces verrous.
    """
    premiere = generer(jeu_minimal.semestre_id)
    assert premiere.etat is EtatCalcul.REUSSI, premiere.message
    with session_base() as session:
        seances = session.scalars(
            select(m.SeanceHebdo)
            .where(m.SeanceHebdo.version_id == premiere.version_id)
            .order_by(m.SeanceHebdo.besoin_id, m.SeanceHebdo.rang)
        ).all()
        # Deux séances d'un même besoin : le créneau de la première convient à la seconde
        # (mêmes personnes), seules l'unicité et la salle sont en cause.
        a, b = next(
            (x, y) for x, y in zip(seances, seances[1:], strict=False) if x.besoin_id == y.besoin_id
        )
        duree = b.fin_min - b.debut_min
        b.jour, b.debut_min, b.fin_min, b.salle_id = (
            a.jour,
            a.debut_min,
            a.debut_min + duree,
            a.salle_id,
        )
        a.verrou = b.verrou = True
        cles = {(a.besoin_id, a.rang), (b.besoin_id, b.rang)}
        place = (a.jour, a.debut_min, a.salle_id)
    refuse = generer(jeu_minimal.semestre_id)
    assert refuse.etat is EtatCalcul.INFAISABLE and "tolérer" in refuse.message, refuse.message
    tolere = generer(
        jeu_minimal.semestre_id, dataclasses.replace(OPTIONS, tolerer_violations_heritees=True)
    )
    assert tolere.etat is EtatCalcul.REUSSI, tolere.message
    assert any("tolérée" in avertissement for avertissement in tolere.avertissements)
    with session_base() as session:
        seances = {
            (s.besoin_id, s.rang): s
            for s in session.scalars(
                select(m.SeanceHebdo).where(m.SeanceHebdo.version_id == tolere.version_id)
            )
        }
    for cle in cles:
        seance = seances[cle]
        assert seance.verrou and (seance.jour, seance.debut_min, seance.salle_id) == place
    assert occurrences(tolere.version_id)


@pytest.mark.cas_limite
def test_echec_de_l_ecriture_finale_est_retente(jeu_minimal, monkeypatch, caplog):
    """Cas : la base est verrouillée au moment où le fil du calcul écrit son état final (longue
    écriture d'un import ou d'une remise à zéro au-delà du délai d'attente).

    Risque : état final perdu, calcul « en cours » puis clos « interrompu par l'arrêt de
    l'application » alors que l'application tourne ; message et statistiques perdus.
    """
    original = generation._enregistrer_fin
    appels: list[int] = []

    def en_panne_une_fois(*args, **kwargs):
        appels.append(1)
        if len(appels) == 1:
            raise OperationalError("UPDATE calcul", {}, Exception("database is locked"))
        return original(*args, **kwargs)

    monkeypatch.setattr(generation, "_enregistrer_fin", en_panne_une_fois)
    monkeypatch.setattr(generation, "DELAI_ENTRE_ESSAIS_S", 0.01)
    suivi = generer(jeu_minimal.semestre_id)
    assert suivi.etat is EtatCalcul.REUSSI and suivi.version_id is not None
    assert len(appels) == 2
    assert _journalise(caplog, "nouvelle tentative")


@pytest.mark.cas_limite
def test_un_saut_d_horloge_ne_clot_pas_un_calcul_vivant(jeu_minimal, processus_vivant, monkeypatch):
    """Cas : l'heure locale avance d'une heure (passage à l'heure d'été, correction NTP) pendant
    qu'un calcul d'un autre processus tourne depuis trente secondes.

    Risque : échéance « temps limite + grâce » atteinte une heure trop tôt, calcul vivant clos
    « interrompu » à tort ; temps écoulé affiché majoré d'une heure.
    """
    depart = dt.datetime.now() - dt.timedelta(seconds=30)
    calcul_id = _calcul_en_cours_d_un_autre_processus(
        jeu_minimal.semestre_id,
        processus_vivant.pid,
        demarre_le=depart,
        parametres={**OPTIONS.vers_dict(), "temps_limite_s": 60},
    )

    class HorlogeAvancee(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime.now(tz) + dt.timedelta(hours=1)

    monkeypatch.setattr(
        generation, "dt", types.SimpleNamespace(datetime=HorlogeAvancee, timedelta=dt.timedelta)
    )
    assert generation.cloturer_calculs_interrompus() == 0
    suivi = generation.etat_calcul(calcul_id)
    assert suivi.etat is EtatCalcul.EN_COURS
    assert 25 <= suivi.temps_ecoule_s <= 45, suivi.temps_ecoule_s
