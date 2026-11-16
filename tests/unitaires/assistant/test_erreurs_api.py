"""Matrice des erreurs du service : chaque cas, son message, son journal, sa pause.

Une ligne de la matrice de `docs/assistant.md` = un cas. Le faux client lève
de vraies exceptions du SDK ; rien n'est validé dans la conversation.
"""

from __future__ import annotations

import datetime as dt
import itertools
import logging

import anthropic
import pytest
from tests.outils.assistant.faux_client import FauxClient, delai_depasse, erreur_api, reseau_coupe

from edt.services.assistant import boucle, temps
from edt.services.assistant.boucle import Issue, derouler, message_du_tour
from edt.services.assistant.configuration import charger_configuration_assistant
from edt.services.assistant.limites import Budget
from edt.services.assistant.outils import ContexteOutil

AUJOURDHUI = dt.date(2026, 10, 5)

CAS = [
    # (exception, issue, extrait du message affiché, niveau du journal, pause en secondes)
    (
        lambda: erreur_api(anthropic.AuthenticationError, 401),
        Issue.CLE_REFUSEE,
        "momentanément indisponible",
        logging.ERROR,
        600,
    ),
    (
        lambda: erreur_api(anthropic.PermissionDeniedError, 403),
        Issue.CLE_REFUSEE,
        "momentanément indisponible",
        logging.ERROR,
        600,
    ),
    (
        lambda: erreur_api(anthropic.APIStatusError, 402),
        Issue.FACTURATION,
        "momentanément indisponible",
        logging.ERROR,
        3600,
    ),
    (
        lambda: erreur_api(anthropic.NotFoundError, 404),
        Issue.MODELE_INTROUVABLE,
        "momentanément indisponible",
        logging.ERROR,
        600,
    ),
    (
        lambda: erreur_api(anthropic.BadRequestError, 400),
        Issue.REQUETE_INVALIDE,
        "Erreur interne de l'assistant",
        logging.ERROR,
        None,
    ),
    (
        lambda: erreur_api(
            anthropic.BadRequestError,
            400,
            "Invalid `signature` in `thinking` block. The block is bound to a different conversation.",
        ),
        Issue.TROP_LONGUE,
        "commencez-en une nouvelle",
        logging.ERROR,
        None,
    ),
    (
        lambda: erreur_api(anthropic.ContentTooLargeError, 413),
        Issue.TROP_LONGUE,
        "Conversation trop longue",
        logging.WARNING,
        None,
    ),
    (
        lambda: erreur_api(anthropic.RateLimitError, 429),
        Issue.DEBIT,
        "très sollicité",
        logging.WARNING,
        None,
    ),
    (
        lambda: erreur_api(anthropic.InternalServerError, 500),
        Issue.SURCHARGE,
        "surchargé",
        logging.WARNING,
        None,
    ),
    (
        lambda: erreur_api(anthropic.OverloadedError, 529),
        Issue.SURCHARGE,
        "surchargé",
        logging.WARNING,
        None,
    ),
    (
        lambda: erreur_api(anthropic.UnprocessableEntityError, 422),
        Issue.INTERNE,
        "Erreur interne",
        logging.ERROR,
        None,
    ),
    (delai_depasse, Issue.DELAI, "prend trop de temps", logging.WARNING, None),
    (reseau_coupe, Issue.RESEAU, "Service injoignable", logging.WARNING, None),
    (
        lambda: anthropic.CredentialsError("aucun identifiant"),
        Issue.IDENTIFIANT,
        "configuration est à corriger",
        logging.ERROR,
        None,
    ),
]


@pytest.fixture(autouse=True)
def _pause_levee():
    boucle.lever_la_pause()
    yield
    boucle.lever_la_pause()


def _derouler(client):
    compteur = itertools.count(1)
    return derouler(
        historique=[],
        texte="Bonjour",
        message_systeme=message_du_tour(AUJOURDHUI, None, ()),
        client=client,
        config=charger_configuration_assistant({"ANTHROPIC_API_KEY": "x"}, version=lambda: "2.8.0"),
        contexte_outils=ContexteOutil("as-test", AUJOURDHUI, lambda: next(compteur)),
        budget=Budget(),
        horloge=lambda: 0.0,
    )


@pytest.mark.parametrize(("fabrique", "issue", "extrait", "niveau", "pause"), CAS)
def test_matrice_des_erreurs(base, caplog, monkeypatch, fabrique, issue, extrait, niveau, pause):
    instant = {"t": 1000.0}
    monkeypatch.setattr(temps, "maintenant", lambda: instant["t"])
    with caplog.at_level(logging.DEBUG, logger="edt.services.assistant"):
        resultat = _derouler(FauxClient([fabrique()]))
    assert resultat.issue is issue
    assert extrait in resultat.message and resultat.messages == []
    niveaux = [r.levelno for r in caplog.records if r.name == "edt.services.assistant.boucle"]
    assert niveau in niveaux, caplog.text
    if pause is None:
        assert boucle.en_pause() is None
    else:
        assert boucle.en_pause() is issue
        instant["t"] += pause + 1
        assert boucle.en_pause() is None


def test_identifiant_de_requete_journalise(base, caplog):
    with caplog.at_level(logging.ERROR):
        _derouler(FauxClient([erreur_api(anthropic.AuthenticationError, 401)]))
    assert "req_test" in caplog.text


def test_exception_qui_n_est_pas_du_sdk_remonte(base):
    with pytest.raises(ZeroDivisionError):
        _derouler(FauxClient([ZeroDivisionError("bogue")]))
