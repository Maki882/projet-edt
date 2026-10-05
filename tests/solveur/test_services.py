"""Intégration sur le jeu minimal du socle : génération de bout en bout, instanciation, replacement.

Base SQLite temporaire (fixtures de `tests/conftest.py`). Le semestre du jeu
minimal contient des jours fériés et des vacances : des séances datées
tombent dessus et doivent être replacées.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import subprocess
import sys
import threading
import time

import pytest
from sqlalchemy import delete, func, select
from typer.testing import CliRunner

from edt import cli, solveur
from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.calendrier import lundi_de
from edt.domaine.enumerations import (
    EtatCalcul,
    Gravite,
    ModeVolume,
    OrigineOccurrence,
    StatutOccurrence,
)
from edt.domaine.instantane import Placement
from edt.erreurs import ErreurCalculEnCours, ErreurIntrouvable, ErreurRegleMetier
from edt.services import generation
from edt.services.instanciation import instancier_version, replacer_occurrences
from edt.services.instantane import charger_instantane
from edt.services.parametres import reinitialiser_parametres
from edt.services.verification import verifier_occurrences_version, verifier_version
from edt.solveur import OptionsSolveur, ResultatSolveur, StatutSolveur
from edt.solveur.moteur import MESSAGE_SOLUTION_CONSERVEE

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
    suivi = generer(jeu_minimal.semestre_id)
    with session_base() as session:
        instantane = charger_instantane(session, jeu_minimal.semestre_id, suivi.version_id)
    deplacees = [o for o in occurrences(suivi.version_id) if o.statut is StatutOccurrence.DEPLACEE]
    assert deplacees, "le calendrier 2026-2027 contient des jours fériés en semaine"
    assert suivi.statistiques["instanciation"]["nb_replacees"] == len(deplacees)
    for occurrence in deplacees:
        besoin = instantane.besoin(occurrence.besoin_id)
        assert occurrence.origine is OrigineOccurrence.REPLACEMENT
        assert occurrence.date_initiale is not None and occurrence.commentaire
        assert instantane.fermeture(occurrence.date, besoin) is None
    # Le 11 novembre 2026 est un mercredi férié : ses séances restent dans la même semaine.
    armistice = dt.date(2026, 11, 11)
    du_11 = [o for o in deplacees if o.date_initiale == armistice]
    assert du_11 and all("jour fermé" in o.commentaire for o in du_11)
    for occurrence in du_11:
        assert occurrence.date != armistice
        assert occurrence.date.isocalendar()[:2] == armistice.isocalendar()[:2]


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


def _calcul_en_cours_d_un_autre_processus(semestre_id: int, pid: int, **champs) -> int:
    """Inscrit en base un calcul « en cours » que le processus `pid` est censé exécuter."""
    with session_base() as session:
        calcul = m.Calcul(
            semestre_id=semestre_id,
            etat=EtatCalcul.EN_COURS,
            demarre_le=champs.get("demarre_le", dt.datetime.now()),
            parametres=OPTIONS.vers_dict(),
            contraintes_desactivees=[],
            statistiques={generation.CLE_PROCESSUS: pid},
        )
        session.add(calcul)
        session.flush()
        return calcul.id


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


@pytest.fixture
def lanceur() -> CliRunner:
    return CliRunner()


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
    # Un si petit problème va jusqu'à l'optimum : ni temps limite, ni arrêt sur stagnation.
    assert "On ne peut pas faire mieux avec les préférences actuelles." in message
    with session_base() as session:
        (calcul,) = session.scalars(select(m.Calcul)).all()
        assert calcul.statistiques["raison_arret"] == "optimum_prouve"
        assert calcul.parametres["arret_sur_stagnation"] is True


def test_commande_generer_echec_code_1(lanceur, jeu_minimal):
    _rendre_impossible(jeu_minimal.semestre_id)
    semestre = str(jeu_minimal.semestre_id)
    resultat = lanceur.invoke(cli.app, ["pre-verifier", "--semestre", semestre])
    assert resultat.exit_code == 1 and "le calcul serait refusé" in resultat.output
    resultat = lanceur.invoke(cli.app, ["generer", "--semestre", semestre])
    assert resultat.exit_code == 1, resultat.output
    assert "Impossible (prouvé)" in resultat.output
