"""Services du solveur sans calcul complet : pré-vérifications, graines, identifiants, clôture.

Base SQLite temporaire (fixtures de `tests/conftest.py`) ; le moteur est appelé directement sur
l'instantané chargé. Les tests de génération de bout en bout sont dans
`tests/integration/solveur/test_services.py`.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import time

import pytest
from sqlalchemy import select, update
from tests.outils.solveur.en_base import OPTIONS
from tests.outils.solveur.en_base import calcul_fictif as _calcul_fictif
from tests.outils.solveur.en_base import etat as _etat
from tests.outils.solveur.en_base import journalise as _journalise
from tests.outils.solveur.en_base import rendre_impossible as _rendre_impossible

from edt.db import modeles as m
from edt.db.moteur import session_base, transaction_immediate
from edt.domaine.enumerations import EtatCalcul, Gravite, OrigineVersion
from edt.erreurs import ErreurIntrouvable
from edt.services import generation
from edt.services import regles as service_regles
from edt.services.instantane import charger_instantane
from edt.services.verification import version_a_verifier


@pytest.fixture(autouse=True)
def _journal_capte_des_info(caplog: pytest.LogCaptureFixture) -> None:
    """Fait parvenir à `caplog` les messages INFO du journal, pour chaque test du module.

    Dans l'ancien `tests/solveur/test_services.py`,
    `test_calcul_en_cours_ne_s_auto_bloque_pas_sur_un_calcul_abandonne` ne voyait le message
    INFO « reportée » que parce que des tests de commande lancés avant lui avaient configuré la
    journalisation au niveau INFO (lancé seul, il échouait déjà). Séparé de ces tests, il reçoit
    ici ce niveau explicitement ; `caplog` le rétablit à la fin de chaque test.
    """
    caplog.set_level(logging.INFO)


# --------------------------------------------------------------------------- #
# Pré-vérifications
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Cas limites (banque de tests : `pytest -m cas_limite`)
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
def test_version_a_verifier_identifiant_hors_bornes_est_introuvable(jeu_minimal):
    """Cas : `version_a_verifier(session, version_id=2**70)`.

    Risque : OverflowError de SQLite au lieu d'ErreurIntrouvable : page 500 au lieu de 404.
    """
    with session_base() as session, pytest.raises(ErreurIntrouvable):
        version_a_verifier(session, version_id=2**70)


@pytest.mark.cas_limite
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
def test_calcul_en_cours_ne_s_auto_bloque_pas_sur_un_calcul_abandonne(jeu_minimal, caplog):
    """Cas : une requête d'écriture tient le verrou d'écriture (transaction immédiate et une
    écriture) dans le fil courant, un calcul « en cours » abandonné traîne en base, et la requête
    consulte `calcul_en_cours()` (import, remise à zéro, paramètres).

    Risque : la clôture du calcul abandonné attend, dans le même fil, le verrou que la requête
    tient elle-même : dix secondes puis « base occupée » (auto-blocage), alors qu'il suffit de
    la reporter et de répondre d'après ce qu'on a lu.
    """
    abandonne = _calcul_fictif(jeu_minimal.semestre_id)  # sans numéro de processus : abandonné
    with session_base() as requete:
        transaction_immediate(requete)
        requete.execute(
            update(m.Semestre)
            .where(m.Semestre.id == jeu_minimal.semestre_id)
            .values(libelle=m.Semestre.libelle)
        )
        debut = time.perf_counter()
        assert generation.calcul_en_cours() is None
        assert time.perf_counter() - debut < 1, "la clôture a attendu le verrou"
        assert _journalise(caplog, "reportée", logging.INFO)
        assert _etat(abandonne) is EtatCalcul.EN_COURS  # clôture reportée, rien d'écrit
    # Verrou relâché : l'appel suivant clôt le calcul.
    assert generation.calcul_en_cours() is None
    assert _etat(abandonne) is EtatCalcul.ERREUR
