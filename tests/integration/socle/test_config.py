"""Configuration vue par la commande doctor : adresse d'écoute introuvable, port occupé."""

from __future__ import annotations

import socket

import pytest

from edt import cli
from edt.commandes import base as module_base


@pytest.mark.cas_limite
def test_doctor_signale_une_adresse_d_ecoute_introuvable(base, monkeypatch, lanceur):
    """Cas : EDT_HOST désigne un nom qui ne se résout pas.

    Risque : `serve` mourrait sur `gaierror` avec une trace, et doctor conclurait à tort
    « le serveur tourne peut-être déjà » (code 0) au lieu d'un échec qui nomme EDT_HOST.
    La résolution de nom est simulée : le test ne dépend pas du réseau du poste.
    """

    def resolution_impossible(*_arguments, **_options):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(module_base.socket, "getaddrinfo", resolution_impossible)
    monkeypatch.setenv("EDT_HOST", "adresse-invalide.invalid")
    resultat = lanceur.invoke(cli.app, ["doctor"])
    assert resultat.exit_code == 1, resultat.output
    assert "ECHEC" in resultat.output and "introuvable" in resultat.output
    assert "EDT_HOST" in resultat.output and "tourne peut-être déjà" not in resultat.output


@pytest.mark.cas_limite
def test_doctor_signale_un_port_deja_occupe_sans_echouer(base, monkeypatch, lanceur):
    """Cas : le port EDT_PORT est déjà pris, en général par le serveur EDT lancé plus tôt.

    Risque : doctor en échec (code 1) pour un serveur qui tourne, ou silence ; il faut un
    avertissement qui dit quoi faire, et le code 0.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as prise:
        prise.bind(("127.0.0.1", 0))
        prise.listen()
        monkeypatch.setenv("EDT_HOST", "127.0.0.1")
        monkeypatch.setenv("EDT_PORT", str(prise.getsockname()[1]))
        resultat = lanceur.invoke(cli.app, ["doctor"])

    assert resultat.exit_code == 0, resultat.output
    (ligne,) = [x for x in resultat.output.splitlines() if x.startswith("[") and "Port" in x]
    assert "ATTENTION" in ligne and "tourne peut-être déjà" in ligne and "EDT_PORT" in ligne
