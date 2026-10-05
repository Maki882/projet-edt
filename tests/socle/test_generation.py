"""Orchestration d'un calcul : succès, calcul déjà en cours, arrêt, échecs."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from sqlalchemy import func, select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import EtatCalcul, ModeVolume
from edt.erreurs import ErreurCalculEnCours, ErreurInfaisable, ErreurIntrouvable
from edt.services import generation
from edt.services.instantane import charger_instantane
from edt.solveur import OptionsSolveur, ResultatSolveur, StatutSolveur

DELAI = 20  # secondes : garde-fou pour qu'un test bloqué échoue au lieu d'attendre sans fin


def _nb_versions() -> int:
    with session_base() as session:
        return session.scalar(select(func.count()).select_from(m.VersionEdt))


def test_calcul_reussi_enregistre_une_version_active(jeu_minimal):
    identifiant = generation.lancer_calcul(jeu_minimal.semestre_id)
    suivi = generation.attendre_calcul(identifiant, DELAI)

    assert suivi.etat is EtatCalcul.REUSSI
    assert suivi.reussi and not suivi.en_cours
    assert suivi.version_id is not None
    assert suivi.statistiques["nb_seances"] == 17
    assert generation.exiger_reussite(suivi) is suivi

    with session_base() as session:
        version = session.get(m.VersionEdt, suivi.version_id)
        assert version.active and version.numero == 1 and version.calcul_id == identifiant
        assert len(version.seances) == 17
        une = version.seances[0]
        assert une.salle_id is not None and une.semaines  # semaines actives enregistrées

    # Le journal dédié du calcul existe et raconte le calcul.
    journal_dedie = Path(suivi.fichier_journal)
    assert journal_dedie.parent.name == "generations"
    contenu = journal_dedie.read_text(encoding="utf-8")
    assert f"calcul={identifiant}" in contenu
    assert "Fin du calcul" in contenu


def test_une_nouvelle_version_remplace_la_version_active(jeu_minimal):
    for _ in range(2):
        generation.attendre_calcul(generation.lancer_calcul(jeu_minimal.semestre_id), DELAI)

    with session_base() as session:
        versions = session.scalars(select(m.VersionEdt).order_by(m.VersionEdt.numero)).all()
        assert [(v.numero, v.active) for v in versions] == [(1, False), (2, True)]


def test_un_seul_calcul_a_la_fois(jeu_minimal):
    demarre, liberer = threading.Event(), threading.Event()

    def moteur_lent(instantane, options, arret=None):
        demarre.set()
        liberer.wait(DELAI)
        return ResultatSolveur(StatutSolveur.TEMPS_DEPASSE)

    premier = generation.lancer_calcul(jeu_minimal.semestre_id, moteur=moteur_lent)
    try:
        assert demarre.wait(DELAI)
        assert generation.etat_calcul(premier).en_cours
        assert generation.calcul_en_cours().id == premier
        with pytest.raises(ErreurCalculEnCours):
            generation.lancer_calcul(jeu_minimal.semestre_id)
    finally:
        liberer.set()
    assert generation.attendre_calcul(premier, DELAI).etat is EtatCalcul.TEMPS_DEPASSE
    assert generation.calcul_en_cours() is None

    # Une fois le premier terminé, un nouveau calcul peut partir.
    second = generation.lancer_calcul(jeu_minimal.semestre_id)
    assert generation.attendre_calcul(second, DELAI).reussi


def test_arret_demande_par_l_utilisateur(jeu_minimal):
    demarre = threading.Event()

    def moteur_interruptible(instantane, options, arret=None):
        demarre.set()
        arret.wait(DELAI)
        return ResultatSolveur(StatutSolveur.ARRETE if arret.is_set() else StatutSolveur.SOLUTION)

    identifiant = generation.lancer_calcul(jeu_minimal.semestre_id, moteur=moteur_interruptible)
    assert demarre.wait(DELAI)
    assert generation.demander_arret(identifiant).arret_demande
    suivi = generation.attendre_calcul(identifiant, DELAI)

    assert suivi.etat is EtatCalcul.ARRETE
    assert suivi.version_id is None
    assert _nb_versions() == 0


def test_infaisable_ne_cree_aucune_version(jeu_minimal):
    def moteur_infaisable(instantane, options, arret=None):
        return ResultatSolveur(StatutSolveur.INFAISABLE, message="Trop de cours le lundi.")

    suivi = generation.attendre_calcul(
        generation.lancer_calcul(jeu_minimal.semestre_id, moteur=moteur_infaisable), DELAI
    )

    assert suivi.etat is EtatCalcul.INFAISABLE
    assert suivi.message == "Trop de cours le lundi."
    assert _nb_versions() == 0
    with pytest.raises(ErreurInfaisable):
        generation.exiger_reussite(suivi)


def test_erreur_du_moteur_journalisee_sans_version(jeu_minimal):
    def moteur_casse(instantane, options, arret=None):
        raise RuntimeError("panne interne du moteur")

    suivi = generation.attendre_calcul(
        generation.lancer_calcul(jeu_minimal.semestre_id, moteur=moteur_casse), DELAI
    )

    assert suivi.etat is EtatCalcul.ERREUR
    assert "panne interne" not in suivi.message  # l'utilisateur ne voit pas le détail technique
    assert _nb_versions() == 0
    contenu = Path(suivi.fichier_journal).read_text(encoding="utf-8")
    assert "RuntimeError: panne interne du moteur" in contenu  # la trace est dans le journal


def test_solution_incomplete_refusee(jeu_minimal):
    def moteur_partiel(instantane, options, arret=None):
        complet = generation.solveur.resoudre(instantane, options, arret)
        return ResultatSolveur(StatutSolveur.SOLUTION, placements=complet.placements[:-1])

    suivi = generation.attendre_calcul(
        generation.lancer_calcul(jeu_minimal.semestre_id, moteur=moteur_partiel), DELAI
    )

    assert suivi.etat is EtatCalcul.ERREUR
    assert "incomplète" in suivi.message
    assert _nb_versions() == 0


def test_besoin_impossible_a_deplier_refuse_le_calcul(jeu_minimal):
    with session_base() as session:
        besoin = session.scalar(select(m.Besoin).where(m.Besoin.mode_volume == ModeVolume.TOTAL))
        besoin.nombre = 500
        besoin_id = besoin.id
    with session_base() as session:
        libelle = charger_instantane(session, jeu_minimal.semestre_id).besoin(besoin_id).libelle
    appels = []

    def moteur_espion(instantane, options, arret=None):
        appels.append(instantane)
        return ResultatSolveur(StatutSolveur.INFAISABLE)

    suivi = generation.attendre_calcul(
        generation.lancer_calcul(jeu_minimal.semestre_id, moteur=moteur_espion), DELAI
    )
    # Refus par les pré-vérifications : état « impossible », message qui nomme le besoin.
    assert suivi.etat is EtatCalcul.INFAISABLE
    assert suivi.statistiques["refuse"] is True
    assert "Le calcul n'a pas été lancé" in suivi.message
    assert f"{libelle} : 500 séances demandées" in suivi.message
    assert any("500 séances demandées" in raison for raison in suivi.statistiques["impossibilites"])
    assert suivi.version_id is None
    assert appels == []  # le moteur n'a même pas été appelé

    # En désactivant explicitement la contrainte de volume, le calcul est accepté.
    options = OptionsSolveur(contraintes_desactivees=frozenset({"VOLUME"}))
    suivi = generation.attendre_calcul(
        generation.lancer_calcul(jeu_minimal.semestre_id, options, moteur=moteur_espion), DELAI
    )
    assert len(appels) == 1
    assert suivi.contraintes_desactivees == ("VOLUME",)


def test_semestre_inconnu(base):
    with pytest.raises(ErreurIntrouvable):
        generation.lancer_calcul(9999)


def _calcul_reste_en_cours(semestre_id: int) -> int:
    """Calcul « en cours » que plus aucun processus n'exécute (application arrêtée en route)."""
    import datetime as dt

    with session_base() as session:
        calcul = m.Calcul(
            semestre_id=semestre_id,
            etat=EtatCalcul.EN_COURS,
            demarre_le=dt.datetime(2026, 10, 5, 9, 0),
        )
        session.add(calcul)
        session.flush()
        return calcul.id


def test_calcul_interrompu_cloture_au_demarrage(jeu_minimal):
    abandonne = _calcul_reste_en_cours(jeu_minimal.semestre_id)

    assert generation.cloturer_calculs_interrompus() == 1
    suivi = generation.etat_calcul(abandonne)
    assert suivi.etat is EtatCalcul.ERREUR and suivi.message == generation.MESSAGE_INTERROMPU
    assert generation.cloturer_calculs_interrompus() == 0
    assert generation.attendre_calcul(
        generation.lancer_calcul(jeu_minimal.semestre_id), DELAI
    ).reussi


def test_calcul_interrompu_ne_bloque_pas_le_suivant(jeu_minimal):
    """Sans redémarrage du serveur : le calcul abandonné est clos par le lancement suivant."""
    abandonne = _calcul_reste_en_cours(jeu_minimal.semestre_id)

    suivant = generation.attendre_calcul(generation.lancer_calcul(jeu_minimal.semestre_id), DELAI)
    assert suivant.reussi
    assert generation.etat_calcul(abandonne).etat is EtatCalcul.ERREUR
