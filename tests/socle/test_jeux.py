"""Jeux fictifs et remise à zéro : panne après remise à zéro, schéma périmé, marqueur."""

from __future__ import annotations

import datetime as dt
import logging
import os
import time

import pytest

from edt.db import modeles as m
from edt.db.initialisation import ErreurSchemaPerime, base_initialisee
from edt.db.moteur import obtenir_moteur, session_base
from edt.erreurs import ErreurDonneesInvalides, ErreurRegleMetier
from edt.services import jeux
from edt.services.jeux import (
    charger_jeu_fictif,
    fichier_marqueur,
    operation_en_cours,
    recharger_jeu_fictif,
    remettre_a_zero,
)

AUJOURDHUI = dt.date(2026, 10, 5)


@pytest.mark.cas_limite
def test_panne_de_generation_apres_remise_a_zero_est_dite(
    jeu_minimal, monkeypatch, compter, caplog
):
    """Cas : `recharger_jeu_fictif(..., reinitialiser=True)` et la génération du jeu plante
    (bogue, disque plein) après que la remise à zéro a été validée.

    Risque : `RuntimeError` brute (500 / code 2) sans dire que la base a été effacée ni
    qu'aucun jeu n'est chargé ; la docstring promettait « rien n'est écrit sinon ».
    """

    def generation_en_panne(*_args, **_options):
        raise RuntimeError("panne simulée")

    monkeypatch.setattr(jeux, "generer_jeu", generation_en_panne)
    with caplog.at_level(logging.ERROR, logger="edt.services.jeux"):
        with pytest.raises(ErreurRegleMetier) as capture:
            recharger_jeu_fictif("petit", 1, reinitialiser=True, aujourdhui=AUJOURDHUI)

    message = capture.value.message
    assert "remise à zéro" in message and "aucun jeu n'est chargé" in message
    assert "panne simulée" in message and "journal" in message
    assert "Traceback" in caplog.text  # la trace est au journal, pas à l'écran
    assert compter(m.Salle) == 0 and base_initialisee(obtenir_moteur())  # vidée mais prête
    assert not operation_en_cours()


@pytest.mark.cas_limite
def test_jeu_fictif_sur_schema_perime_indique_le_reset(base, compter):
    """Cas : une table a une colonne en trop (base d'une autre version) ; on charge un jeu
    sans remise à zéro, par le service puis par le chemin direct.

    Risque : « lancer init-db » conseillé alors qu'init-db refusera ; le chemin direct
    continuait jusqu'à une `OperationalError` (code 2). Le bon remède est « --reset ».
    """
    with obtenir_moteur().begin() as connexion:
        connexion.exec_driver_sql("ALTER TABLE besoin ADD COLUMN ancien_champ VARCHAR(10)")

    with pytest.raises(ErreurSchemaPerime) as capture:
        recharger_jeu_fictif("petit", 1, aujourdhui=AUJOURDHUI)
    assert "--reset" in str(capture.value) and "init-db »" not in capture.value.message
    with session_base() as session, pytest.raises(ErreurSchemaPerime):
        charger_jeu_fictif(session, "petit", 1)
    assert compter(m.Salle) == 0  # rien d'écrit


@pytest.mark.cas_limite
def test_une_operation_d_un_autre_processus_est_vue_par_son_marqueur(
    base, environnement, monkeypatch
):
    """Cas : `edt jeu --reset` ou `init-db --reset` tourne dans un autre processus pendant que le
    serveur sert des pages.

    Risque : le verrou étant propre au processus, le serveur répondait « base non
    initialisée : lancer init-db » (faux) pendant la recréation des tables ; le marqueur posé
    à côté de la base doit être vu, et un marqueur périmé ignoré.
    """
    marqueur = fichier_marqueur()
    assert marqueur == environnement / "edt.db.operation" and not operation_en_cours()

    marqueur.write_text("4242 remise à zéro", encoding="utf-8")
    assert operation_en_cours()
    perime = time.time() - 3600
    os.utime(marqueur, (perime, perime))
    assert not operation_en_cours()  # un processus mort n'immobilise pas le serveur
    marqueur.unlink()

    # Pendant une remise à zéro, le marqueur est posé ; il est retiré ensuite.
    vu_pendant = []
    reelle = jeux.initialiser_base

    def initialisation_observee(*args, **options):
        vu_pendant.append(marqueur.exists() and operation_en_cours())
        return reelle(*args, **options)

    monkeypatch.setattr(jeux, "initialiser_base", initialisation_observee)
    rapport = remettre_a_zero(aujourdhui=AUJOURDHUI)
    assert rapport.reinitialisee and vu_pendant == [True]
    assert not marqueur.exists() and not operation_en_cours()


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("graine", "annee_debut"), [(-1, None), (0, 1900)], ids=["graine négative", "année 1900"]
)
def test_une_graine_ou_une_annee_invalides_sont_refusees_avant_la_remise_a_zero(
    jeu_minimal, compter, graine, annee_debut
):
    """Cas : l'écran (ou l'assistant) demande un rechargement avec remise à zéro et une graine
    ou une année de début hors bornes.

    Risque : la base était remise à zéro, puis la génération refusait la demande : toutes les
    données perdues pour une faute de saisie (cas transmis par le lot synthèse).
    """
    with pytest.raises(ErreurDonneesInvalides):
        recharger_jeu_fictif(
            "petit", graine, reinitialiser=True, annee_debut=annee_debut, aujourdhui=AUJOURDHUI
        )
    assert compter(m.Salle) == jeu_minimal.nb_salles  # rien n'a été effacé
    assert compter(m.Besoin) == jeu_minimal.nb_besoins
    assert not operation_en_cours()
