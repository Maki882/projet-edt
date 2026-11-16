"""Configuration de l'assistant : défauts, variables invalides, clé absente ou en double."""

from __future__ import annotations

import logging

import pytest

from edt.services import assistant
from edt.services.assistant.configuration import (
    MICRO,
    PRIX_PAR_MODELE,
    Raison,
    charger_configuration_assistant,
    dollars,
)

AVEC_CLE = {"ANTHROPIC_API_KEY": "factice"}


def _config(**variables: str):
    return charger_configuration_assistant(variables, version=lambda: "2.8.0")


def test_defauts_de_la_conception():
    config = _config(**AVEC_CLE)
    assert config.disponible and config.raison is Raison.DISPONIBLE
    assert (config.modele, config.effort, config.jetons_sortie_max) == (
        "claude-opus-5-5",
        "low",
        4096,
    )
    assert (config.appels_max, config.delai_s, config.delai_appel_s) == (5, 90, 40)
    assert (config.longueur_max, config.messages_par_conversation, config.contexte_max) == (
        800,
        10,
        40_000,
    )
    assert (config.messages_par_adresse, config.messages_par_jour) == (20, 100)
    assert config.budget_jour_micro == 2 * MICRO and config.budget_jour_texte == "2,00 $"
    assert (config.simultanes, config.conversations_max, config.expiration_min) == (2, 200, 30)
    assert not config.journal_contenu and config.variable_cle == "ANTHROPIC_API_KEY"
    assert config.modele in PRIX_PAR_MODELE


def test_sans_cle_l_assistant_n_est_pas_configure():
    config = _config()
    assert not config.disponible and config.raison is Raison.NON_CONFIGURE
    assert config.raison_texte == "L'assistant n'est pas configuré sur ce serveur."
    assert _config(ANTHROPIC_API_KEY="   ").raison is Raison.NON_CONFIGURE


def test_le_jeton_d_authentification_suffit():
    config = _config(ANTHROPIC_AUTH_TOKEN="factice")
    assert config.disponible and config.variable_cle == "ANTHROPIC_AUTH_TOKEN"


def test_desactive_par_l_administrateur_meme_avec_une_cle():
    config = _config(EDT_ASSISTANT_ACTIF="non", **AVEC_CLE)
    assert config.raison is Raison.DESACTIVE and not config.disponible
    assert "désactivé par l'administrateur" in config.raison_texte


@pytest.mark.parametrize(
    ("variable", "valeur"),
    [
        ("EDT_ASSISTANT_ACTIF", "peut-être"),
        ("EDT_ASSISTANT_MODELE", "claude-inconnu-9"),
        ("EDT_ASSISTANT_EFFORT", "extreme"),
        ("EDT_ASSISTANT_JETONS_SORTIE_MAX", "100"),
        ("EDT_ASSISTANT_APPELS_MAX", "0"),
        ("EDT_ASSISTANT_DELAI_S", "300"),
        ("EDT_ASSISTANT_DELAI_APPEL_S", "dix"),
        ("EDT_ASSISTANT_LONGUEUR_MAX", "-5"),
        ("EDT_ASSISTANT_MESSAGES_PAR_CONVERSATION", "1e3"),
        ("EDT_ASSISTANT_CONTEXTE_MAX", "²"),
        ("EDT_ASSISTANT_MESSAGES_PAR_ADRESSE", "0"),
        ("EDT_ASSISTANT_MESSAGES_PAR_JOUR", "100001"),
        ("EDT_ASSISTANT_BUDGET_JOUR", "beaucoup"),
        ("EDT_ASSISTANT_BUDGET_JOUR", "-1"),
        ("EDT_ASSISTANT_BUDGET_JOUR", "NaN"),
        ("EDT_ASSISTANT_SIMULTANES", "21"),
        ("EDT_ASSISTANT_CONVERSATIONS_MAX", ""),
        ("EDT_ASSISTANT_EXPIRATION_MIN", "1441"),
        ("EDT_ASSISTANT_JOURNAL_CONTENU", "toujours"),
    ],
)
def test_une_variable_invalide_rend_l_assistant_indisponible_sans_lever(variable, valeur):
    config = _config(**{variable: valeur}, **AVEC_CLE)
    if valeur == "":
        assert config.disponible  # vide : valeur par défaut
        return
    assert config.raison is Raison.CONFIGURATION and not config.disponible
    assert len(config.erreurs) == 1
    assert variable in config.erreurs[0] and repr(valeur) in config.erreurs[0]
    assert "configuration est à corriger" in config.raison_texte


def test_valeurs_valides_lues():
    config = _config(
        EDT_ASSISTANT_MODELE="claude-sonnet-5-5",
        EDT_ASSISTANT_EFFORT="MEDIUM",
        EDT_ASSISTANT_BUDGET_JOUR="0,50",
        EDT_ASSISTANT_MESSAGES_PAR_JOUR="0",
        EDT_ASSISTANT_JOURNAL_CONTENU="oui",
        **AVEC_CLE,
    )
    assert config.disponible
    assert (config.modele, config.effort, config.messages_par_jour) == (
        "claude-sonnet-5-5",
        "medium",
        0,
    )
    assert config.budget_jour_micro == 500_000 and config.journal_contenu


def test_delai_d_appel_plus_long_que_l_echange_refuse():
    config = _config(EDT_ASSISTANT_DELAI_S="30", EDT_ASSISTANT_DELAI_APPEL_S="60", **AVEC_CLE)
    assert config.raison is Raison.CONFIGURATION
    assert "EDT_ASSISTANT_DELAI_APPEL_S" in config.erreurs[0]


@pytest.mark.parametrize("version", [None, "1.17.0"])
def test_sdk_absent_ou_trop_ancien(version):
    config = charger_configuration_assistant(AVEC_CLE, version=lambda: version)
    assert config.raison is Raison.CONFIGURATION
    assert "anthropic" in config.erreurs[0]


def test_deux_variables_d_identifiant_signalees(caplog):
    config = _config(ANTHROPIC_API_KEY="a", ANTHROPIC_AUTH_TOKEN="b")
    assert config.deux_cles and config.variable_cle == "ANTHROPIC_API_KEY"
    from edt.services.assistant.configuration import annoncer

    with caplog.at_level(logging.INFO, logger="edt.services.assistant"):
        annoncer(config)
    assert "ANTHROPIC_API_KEY et ANTHROPIC_AUTH_TOKEN" in caplog.text


def test_etat_journalise_une_fois_par_changement(monkeypatch, caplog):
    with caplog.at_level(logging.INFO, logger="edt.services.assistant"):
        assert assistant.etat().raison is Raison.NON_CONFIGURE
        assistant.etat()
        monkeypatch.setenv("EDT_ASSISTANT_EFFORT", "infini")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "factice")
        etat = assistant.etat()
    assert etat.raison is Raison.CONFIGURATION
    assert caplog.text.count("Assistant non configuré") == 1
    erreurs = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(erreurs) == 1 and "EDT_ASSISTANT_EFFORT" in erreurs[0].getMessage()
    assert "factice" not in caplog.text


def test_montants_en_dollars():
    assert dollars(2 * MICRO) == "2,00 $"
    assert dollars(24_000) == "0,024 $"
    assert dollars(0) == "0,00 $"
