"""Configuration : variables EDT_* invalides refusées, valeurs vides au défaut, doctor."""

from __future__ import annotations

import pytest

from edt import cli
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


@pytest.mark.cas_limite
def test_doctor_signale_une_adresse_d_ecoute_introuvable(base, monkeypatch, lanceur):
    """Cas : EDT_HOST désigne un nom qui ne se résout pas.

    Risque : `serve` mourrait sur `gaierror` avec une trace, et doctor concluait à tort
    « le serveur tourne peut-être déjà » (code 0) au lieu d'un échec qui nomme EDT_HOST.
    """
    monkeypatch.setenv("EDT_HOST", "adresse-invalide-xyz.invalid")
    resultat = lanceur.invoke(cli.app, ["doctor"])
    assert resultat.exit_code == 1, resultat.output
    assert "ECHEC" in resultat.output and "introuvable" in resultat.output
    assert "EDT_HOST" in resultat.output and "tourne peut-être déjà" not in resultat.output
