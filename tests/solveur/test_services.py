"""Intégration sur le jeu minimal du socle : génération de bout en bout, instanciation, replacement.

Base SQLite temporaire (fixtures de `tests/conftest.py`). Le semestre du jeu
minimal contient des jours fériés et des vacances : des séances datées
tombent dessus et doivent être replacées.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select
from typer.testing import CliRunner

from edt import cli
from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import (
    EtatCalcul,
    Gravite,
    ModeVolume,
    OrigineOccurrence,
    StatutOccurrence,
)
from edt.domaine.instantane import Placement
from edt.erreurs import ErreurIntrouvable
from edt.services import generation
from edt.services.instanciation import instancier_version, replacer_occurrences
from edt.services.instantane import charger_instantane
from edt.services.verification import verifier_occurrences_version, verifier_version
from edt.solveur import OptionsSolveur, ResultatSolveur, StatutSolveur

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


def test_avertissements_joints_au_suivi(jeu_minimal):
    def moteur_bavard(instantane, options, arret=None):
        return ResultatSolveur(StatutSolveur.TEMPS_DEPASSE, avertissements=("Remarque du moteur.",))

    suivi = generer(jeu_minimal.semestre_id, moteur=moteur_bavard)
    assert suivi.etat is EtatCalcul.TEMPS_DEPASSE
    assert "Remarque du moteur." in suivi.avertissements


# --------------------------------------------------------------------------- #
# Ligne de commande
# --------------------------------------------------------------------------- #


@pytest.fixture
def lanceur() -> CliRunner:
    return CliRunner()


def test_commande_generer_puis_instancier(lanceur, jeu_minimal):
    semestre = str(jeu_minimal.semestre_id)
    resultat = lanceur.invoke(cli.app, ["pre-verifier", "--semestre", semestre])
    assert resultat.exit_code == 0, resultat.output
    assert "rien n'empêche de lancer le calcul" in resultat.output

    resultat = lanceur.invoke(
        cli.app,
        ["generer", "--semestre", semestre, "--temps-limite", "30", "--graine", "2", "--fils", "1"],
    )
    assert resultat.exit_code == 0, resultat.output
    assert "Réussi" in resultat.output and "17 séance(s) placée(s)" in resultat.output
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
    assert "Qualité : objectif" in resultat.output


def test_commande_generer_echec_code_1(lanceur, jeu_minimal):
    _rendre_impossible(jeu_minimal.semestre_id)
    semestre = str(jeu_minimal.semestre_id)
    resultat = lanceur.invoke(cli.app, ["pre-verifier", "--semestre", semestre])
    assert resultat.exit_code == 1 and "le calcul serait refusé" in resultat.output
    resultat = lanceur.invoke(cli.app, ["generer", "--semestre", semestre])
    assert resultat.exit_code == 1, resultat.output
    assert "Impossible (prouvé)" in resultat.output
