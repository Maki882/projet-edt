"""Configuration vue par la commande doctor : une adresse d'écoute introuvable est signalée."""

from __future__ import annotations

import pytest

from edt import cli


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
