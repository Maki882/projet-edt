"""Limites et coûts : prix d'un appel, réservation, compteurs du jour en base et en mémoire."""

from __future__ import annotations

import datetime as dt
import json
import logging
from types import SimpleNamespace

import pytest

from edt.services.assistant import temps
from edt.services.assistant.configuration import MICRO, charger_configuration_assistant
from edt.services.assistant.limites import (
    FICHIER_CONSOMMATION,
    Budget,
    CompteurAdresses,
    Compteurs,
    Limite,
    LimiteAtteinte,
    cout_maximal_appel,
    cout_reel,
    dossier_consommation,
)

JOUR = dt.date(2026, 10, 5)


def _config(**variables):
    return charger_configuration_assistant(
        {"ANTHROPIC_API_KEY": "x", **variables}, version=lambda: "2.8.0"
    )


@pytest.fixture
def jour(monkeypatch):
    etat = {"jour": JOUR}
    monkeypatch.setattr(temps, "aujourdhui", lambda: etat["jour"])
    return etat


def _usage(entree=0, lus=0, ecrits=0, sortie=0, iterations=None):
    return SimpleNamespace(
        input_tokens=entree,
        cache_read_input_tokens=lus,
        cache_creation_input_tokens=ecrits,
        output_tokens=sortie,
        iterations=iterations,
    )


def _fichier(jour_):
    """(échanges, appels, coût) du fichier de consommation, s'il est du jour `jour_`."""
    chemin = dossier_consommation() / FICHIER_CONSOMMATION
    if not chemin.exists():
        return None
    donnees = json.loads(chemin.read_text(encoding="utf-8"))
    if donnees["jour"] != jour_.isoformat():
        return None
    return donnees["echanges"], donnees["appels"], donnees["cout"]


# --------------------------------------------------------------------------- #
# Coûts
# --------------------------------------------------------------------------- #


def test_cout_reel_d_un_appel_au_prix_du_modele():
    # Question type à cache chaud : 15 600 jetons lus, 1 300 écrits, 700 en sortie (Opus 5.5).
    cout = cout_reel(_usage(lus=15_600, ecrits=1_300, sortie=700), "claude-opus-5-5")
    assert cout.cout == round(15_600 * 0.20 + 1_300 * 5.00 + 700 * 20.00)
    assert (cout.appels, cout.jetons) == (1, 17_600)
    assert 0.02 < cout.cout / MICRO < 0.03


def test_cout_d_un_repli_compte_chaque_tentative_a_son_modele(caplog):
    iterations = [
        SimpleNamespace(
            type="message",
            model="claude-opus-5-5",
            input_tokens=1000,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
            output_tokens=0,
        ),
        SimpleNamespace(
            type="fallback_message",
            model="claude-opus-5",
            input_tokens=1000,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
            output_tokens=100,
        ),
    ]
    cout = cout_reel(_usage(iterations=iterations), "claude-opus-5")
    assert cout.cout == 1000 * 4 + 1000 * 5 + 100 * 25
    with caplog.at_level(logging.WARNING):
        inconnu = cout_reel(_usage(entree=1000), "claude-futur-9")
    assert inconnu.cout == 1000 * 5  # prix du plus cher de la table
    assert "absent de la table des prix" in caplog.text


def test_cout_maximal_d_un_appel_couvre_la_demande_et_son_repli():
    config = _config()
    debut = cout_maximal_appel(7_700, config)
    assert debut == round(7_700 * 5 + 4096 * 20 + 7_700 * 6.25 + 4096 * 25)
    assert 0.25 < debut / MICRO < 0.30
    assert 0.6 < cout_maximal_appel(40_000, config) / MICRO < 0.65


# --------------------------------------------------------------------------- #
# Budget et messages du site
# --------------------------------------------------------------------------- #


def test_reservation_qui_depasserait_le_budget_refusee(base, jour):
    config = _config(EDT_ASSISTANT_BUDGET_JOUR="0.50")
    budget = Budget()
    premiere = budget.reserver(300_000, config)
    with pytest.raises(LimiteAtteinte) as limite:
        budget.reserver(300_000, config)  # 0,30 + 0,30 > 0,50 : refusé avant l'appel
    assert limite.value.limite is Limite.SITE
    budget.solder(premiere, Compteurs(appels=1, cout=20_000))
    assert budget.consommation().cout == 20_000
    budget.reserver(300_000, config)  # la réservation soldée libère la place


def test_echanges_du_jour_comptes_et_limites(base, jour, caplog):
    config = _config(EDT_ASSISTANT_MESSAGES_PAR_JOUR="2")
    budget = Budget()
    budget.ouvrir_echange(config)
    budget.ouvrir_echange(config)
    with caplog.at_level(logging.WARNING), pytest.raises(LimiteAtteinte, match="limite du jour"):
        budget.ouvrir_echange(config)
    with pytest.raises(LimiteAtteinte):
        budget.ouvrir_echange(config)
    assert caplog.text.count("limite du jour atteinte") == 1  # une seule fois par jour
    assert _fichier(JOUR) == (2, 0, 0)


@pytest.mark.cas_limite
def test_consommation_du_jour_survit_a_un_redemarrage_et_change_avec_le_jour(base, jour):
    """Cas : le service redémarre (déploiement) après avoir dépensé une partie du budget ; puis
    minuit passe.

    Risque : le budget remis à zéro à chaque redémarrage, ou jamais remis à zéro.
    """
    config = _config(EDT_ASSISTANT_BUDGET_JOUR="0.10")
    Budget().solder(0, Compteurs(appels=1, cout=90_000))
    neuf = Budget()  # processus redémarré : mémoire vide, base intacte
    assert neuf.consommation().cout == 90_000
    with pytest.raises(LimiteAtteinte):
        neuf.reserver(20_000, config)
    jour["jour"] = JOUR + dt.timedelta(days=1)
    assert neuf.consommation().cout == 0
    neuf.reserver(20_000, config)


@pytest.mark.cas_limite
def test_consommation_survit_a_une_remise_a_zero_puis_a_un_redemarrage(base, jour):
    """Cas : 1,90 $ dépensés ; un visiteur charge un établissement fictif (remise à zéro de la
    base) ; le service redémarre le même jour.

    Risque : le budget et le compteur du jour repartent de zéro : un second budget complet.
    """
    from edt.db.initialisation import initialiser_base

    config = _config()
    budget = Budget()
    budget.ouvrir_echange(config)
    budget.solder(0, Compteurs(appels=1, cout=1_900_000))
    initialiser_base(reset=True, aujourdhui=JOUR)
    redemarre = Budget()
    assert redemarre.consommation().cout == 1_900_000
    assert redemarre.consommation().echanges == 1
    with pytest.raises(LimiteAtteinte):
        redemarre.reserver(200_000, config)


@pytest.mark.cas_limite
def test_fichier_de_consommation_illisible(base, jour, caplog):
    """Cas : le fichier de consommation est abîmé (disque, copie à la main).

    Risque : une exception à chaque message, ou un compte faux sans que personne le sache.
    """
    budget = Budget()
    budget.solder(0, Compteurs(appels=1, cout=50_000))
    (dossier_consommation() / FICHIER_CONSOMMATION).write_text("{abîmé", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert budget.consommation().cout == 50_000  # la mémoire compte encore
    assert "illisible" in caplog.text
    budget.solder(0, Compteurs(appels=1, cout=1_000))
    assert _fichier(JOUR) == (0, 1, 1_000)  # réécrit au premier appel suivant


@pytest.mark.cas_limite
def test_consommation_impossible_a_ecrire_est_reportee(base, jour, caplog):
    """Cas : le fichier de consommation ne peut pas être écrit (dossier en lecture seule,
    disque plein ; simulé par un dossier à la place du fichier).

    Risque : appel déjà payé mais non compté, ou échange qui échoue pour une simple écriture.
    """
    budget = Budget()
    chemin = dossier_consommation() / FICHIER_CONSOMMATION
    chemin.mkdir(parents=True)
    with caplog.at_level(logging.ERROR):
        budget.solder(0, Compteurs(appels=1, cout=7_000))
    assert "gardée en mémoire" in caplog.text
    assert budget.consommation().cout == 7_000  # la mémoire compte
    chemin.rmdir()
    budget.solder(0, Compteurs(appels=1, cout=3_000))
    assert _fichier(JOUR) == (0, 2, 10_000)  # le report est ajouté à l'écriture suivante


# --------------------------------------------------------------------------- #
# Messages par adresse
# --------------------------------------------------------------------------- #


def test_limite_par_adresse_et_par_jour(jour):
    config = _config(EDT_ASSISTANT_MESSAGES_PAR_ADRESSE="2")
    compteur = CompteurAdresses()
    compteur.compter("adresse-a", config)
    compteur.compter("adresse-a", config)
    with pytest.raises(LimiteAtteinte) as limite:
        compteur.compter("adresse-a", config)
    assert limite.value.limite is Limite.ADRESSE and "limite de 2 messages" in limite.value.message
    compteur.compter("adresse-b", config)  # une autre adresse n'est pas touchée
    compteur.rendre("adresse-a")
    compteur.compter("adresse-a", config)
    jour["jour"] = JOUR + dt.timedelta(days=1)
    compteur.compter("adresse-a", config)


def test_le_fichier_de_consommation_n_a_que_des_compteurs(base, jour):
    Budget().solder(0, Compteurs(appels=1, entree=10, sortie=5, cout=90))
    donnees = json.loads((dossier_consommation() / FICHIER_CONSOMMATION).read_text("utf-8"))
    assert set(donnees) == {
        "jour",
        "echanges",
        "appels",
        "entree",
        "cache_lus",
        "cache_ecrits",
        "sortie",
        "cout",
    }
    assert donnees["jour"] == JOUR.isoformat()
