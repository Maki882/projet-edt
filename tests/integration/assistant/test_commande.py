"""Commande `python -m edt assistant` : état, essai et question, avec le faux client."""

from __future__ import annotations

import anthropic
import pytest
from tests.outils.assistant.faux_client import erreur_api, reponse_outil, reponse_texte
from tests.outils.assistant.fixtures import CLE_FACTICE
from typer.testing import CliRunner

from edt import cli


@pytest.fixture
def lanceur() -> CliRunner:
    return CliRunner()


def _lancer(lanceur, *arguments):
    resultat = lanceur.invoke(cli.app, ["assistant", *arguments])
    assert CLE_FACTICE not in resultat.output
    assert "Traceback" not in resultat.output
    return resultat


def test_etat_sans_cle_code_1(lanceur, base):
    resultat = _lancer(lanceur)
    assert resultat.exit_code == 1
    assert "Assistant : non configuré." in resultat.output
    assert "aucune variable ANTHROPIC_API_KEY" in resultat.output


def test_etat_configure_code_0_sans_afficher_la_cle(lanceur, base, cle_factice, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_LOG", "debug")
    resultat = _lancer(lanceur)
    assert resultat.exit_code == 0, resultat.output
    assert "Assistant : prêt." in resultat.output
    assert "variable ANTHROPIC_API_KEY présente (valeur jamais affichée)" in resultat.output
    assert "Modèle : claude-opus-5-5, effort low" in resultat.output
    assert "budget de 2,00 $" in resultat.output
    assert "ANTHROPIC_LOG est posée" in resultat.output


def test_etat_configuration_invalide(lanceur, base, cle_factice, monkeypatch):
    monkeypatch.setenv("EDT_ASSISTANT_BUDGET_JOUR", "beaucoup")
    resultat = _lancer(lanceur)
    assert resultat.exit_code == 1
    assert "Erreur de configuration : EDT_ASSISTANT_BUDGET_JOUR" in resultat.output


def test_essai_reussi_avec_le_faux_client(lanceur, base, faux_client, jour_fige):
    faux_client.reponses = [reponse_texte("prêt", input_tokens=7712, output_tokens=41)]
    resultat = _lancer(lanceur, "--essai")
    assert resultat.exit_code == 0, resultat.output
    assert "Essai réussi en" in resultat.output and "arrêt end_turn" in resultat.output
    assert "jetons d'entrée 7712" in resultat.output and "sortie 41" in resultat.output
    (arguments,) = faux_client.appels
    assert arguments["max_tokens"] == 256 and len(arguments["tools"]) == 12
    assert arguments["fallbacks"] == "default"
    consommation = _lancer(lanceur)
    assert "1 appel(s)" in consommation.output  # le coût de l'essai compte dans le jour


@pytest.mark.parametrize(
    ("erreur", "extrait"),
    [
        (lambda: erreur_api(anthropic.AuthenticationError, 401), "La clé est refusée"),
        (lambda: erreur_api(anthropic.NotFoundError, 404), "EDT_ASSISTANT_MODELE"),
        (lambda: erreur_api(anthropic.BadRequestError, 400), "en-tête bêta"),
    ],
)
def test_essai_en_echec_code_1_avec_le_remede(
    lanceur, base, faux_client, jour_fige, erreur, extrait
):
    faux_client.reponses = [erreur()]
    resultat = _lancer(lanceur, "--essai")
    assert resultat.exit_code == 1
    assert "Essai en échec" in resultat.output and extrait in resultat.output


def test_essai_sans_cle_code_1(lanceur, base):
    resultat = _lancer(lanceur, "--essai")
    assert resultat.exit_code == 1 and "Essai impossible" in resultat.output


def test_question_affiche_la_reponse_et_les_propositions(
    lanceur, jeu_minimal, faux_client, jour_fige
):
    faux_client.reponses = [
        reponse_outil("proposer_etat_regle", {"code": "R001", "etat": "inactive"}),
        reponse_texte("Je vous propose de désactiver R001."),
    ]
    resultat = _lancer(lanceur, "--question", "Désactive la règle R001.")
    assert resultat.exit_code == 0, resultat.output
    assert "Je vous propose de désactiver R001." in resultat.output
    assert "Proposition P1 (Désactiver R001)" in resultat.output
    assert "non confirmable en ligne de commande" in resultat.output


def test_essai_et_question_ensemble_refuses(lanceur, base):
    assert _lancer(lanceur, "--essai", "--question", "x").exit_code == 1
