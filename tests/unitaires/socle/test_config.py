"""Configuration : variables EDT_* invalides refusées, valeurs vides au défaut, doctor."""

from __future__ import annotations

import pytest

from edt.config import DEFAUTS, charger_configuration
from edt.erreurs import ErreurConfiguration


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("variable", "valeur"),
    [
        ("EDT_PORT", "²"),  # isdigit() vrai, int() impossible
        ("EDT_PORT", "0"),
        ("EDT_PORT", "65536"),
        ("EDT_PORT", "abc"),
        ("EDT_PORT", "-1"),
        ("EDT_PORT", "80 80"),
        ("EDT_HOST", "adresse invalide"),
        ("EDT_HOST", "hote/chemin"),
        ("EDT_LOG_LEVEL", "verbose"),
        ("EDT_ENV", "production"),
    ],
)
def test_variables_d_environnement_invalides_levent_une_erreur_de_configuration(variable, valeur):
    """Cas : une variable EDT_* porte une valeur hors domaine (port « ² », hôte avec espace,
    niveau de journal ou environnement inconnus).

    Risque : `ValueError` brute au démarrage (code 2, trace) ou repli silencieux sur le
    défaut ; le message doit nommer la variable et la valeur lue.
    """
    with pytest.raises(ErreurConfiguration) as capture:
        charger_configuration({**DEFAUTS, variable: valeur})
    message = str(capture.value)
    assert variable in message and valeur.strip().lower() in message.lower()


@pytest.mark.cas_limite
def test_valeurs_vides_ou_entourees_d_espaces():
    """Cas : variables présentes mais vides, ou avec espaces et casse libres.

    Risque : une variable vide refusée au lieu de prendre son défaut, ou « PROD » non reconnu.
    """
    config = charger_configuration(
        {"EDT_PORT": " ", "EDT_HOST": "", "EDT_ENV": " PROD ", "EDT_LOG_LEVEL": " debug "}
    )
    assert (config.port, config.host, config.env, config.log_level) == (
        8000,
        "127.0.0.1",
        "prod",
        "DEBUG",
    )
    assert config.est_prod and config.db_url == DEFAUTS["EDT_DB_URL"]
