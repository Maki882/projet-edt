"""Moteur de base vu par la ligne de commande : URL de base refusées avec une erreur de configuration."""

from __future__ import annotations

import pytest

from edt import cli
from edt.db.moteur import (
    creer_moteur,
    liberer_moteurs,
    obtenir_moteur,
)
from edt.erreurs import ErreurConfiguration


@pytest.mark.cas_limite
@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:"])
def test_une_url_sqlite_sans_fichier_est_refusee(url, monkeypatch, lanceur):
    """Cas : EDT_DB_URL désigne une base SQLite en mémoire (chemin oublié dans edt.env).

    Risque : chaque fil d'exécution (serveur, calcul, commande) verrait sa propre base vide ;
    init-db dirait « 59 lignes » et le calcul échouerait « no such table ».
    """
    with pytest.raises(ErreurConfiguration, match="EDT_DB_URL") as capture:
        creer_moteur(url)
    assert "fichier est requis" in str(capture.value)

    monkeypatch.setenv("EDT_DB_URL", url)
    liberer_moteurs()
    with pytest.raises(ErreurConfiguration, match="fichier est requis"):
        obtenir_moteur()
    resultat = lanceur.invoke(cli.app, ["init-db"])
    assert resultat.exit_code == cli.CODE_ERREUR_METIER
    assert "EDT_DB_URL" in resultat.output and "Traceback" not in resultat.output


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    "url", ["postgresql://utilisateur:secret@localhost/edt", "nimporte://quoi", "sqlit:///x.db"]
)
def test_une_url_sans_pilote_ou_de_schema_inconnu_est_une_erreur_de_configuration(
    url, monkeypatch, lanceur
):
    """Cas : EDT_DB_URL vise un autre moteur (pilote absent) ou porte une faute de frappe.

    Risque : exception brute de SQLAlchemy (code 2, trace), service qui ne démarre pas, au
    lieu d'un message de configuration ; le mot de passe de l'URL ne doit pas s'afficher.
    """
    with pytest.raises(ErreurConfiguration, match="seul SQLite") as capture:
        creer_moteur(url)
    message = str(capture.value)
    assert "EDT_DB_URL" in message and "secret" not in message

    monkeypatch.setenv("EDT_DB_URL", "postgresql://x/y")
    liberer_moteurs()
    resultat = lanceur.invoke(cli.app, ["doctor"])
    assert resultat.exit_code == 1
    assert "ECHEC" in resultat.output and "seul SQLite" in resultat.output
    assert "Traceback" not in resultat.output
