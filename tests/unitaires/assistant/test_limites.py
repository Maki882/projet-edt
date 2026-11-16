"""Limites et coûts : prix d'un appel, réservation, compteurs du jour en base et en mémoire."""

from __future__ import annotations

import datetime as dt
import logging
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.services.assistant import temps
from edt.services.assistant.configuration import MICRO, charger_configuration_assistant
from edt.services.assistant.limites import (
    Budget,
    CompteurAdresses,
    Compteurs,
    Limite,
    LimiteAtteinte,
    cout_maximal_appel,
    cout_reel,
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


def _ligne(jour_):
    with session_base() as session:
        ligne = session.get(m.AssistantConsommation, jour_)
        return None if ligne is None else (ligne.echanges, ligne.appels, ligne.cout_microdollars)


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
    assert _ligne(JOUR) == (2, 0, 0)


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
def test_consommation_gardee_en_memoire_si_la_base_est_videe(base, jour):
    """Cas : un visiteur charge un établissement fictif, qui recrée toutes les tables.

    Risque : le budget du jour remis à zéro par n'importe quel visiteur.
    """
    budget = Budget()
    budget.solder(0, Compteurs(appels=1, cout=50_000))
    with session_base() as session:
        session.execute(delete(m.AssistantConsommation))
    assert budget.consommation().cout == 50_000


@pytest.mark.cas_limite
def test_base_occupee_la_consommation_est_reportee(
    base, jour, delai_verrou_court, verrou_ecriture, caplog
):
    """Cas : une écriture de consommation tombe pendant qu'un autre processus tient la base.

    Risque : appel déjà payé mais non compté, ou échange qui échoue pour une simple écriture.
    """
    budget = Budget()
    with verrou_ecriture(), caplog.at_level(logging.ERROR):
        budget.solder(0, Compteurs(appels=1, cout=7_000))
    assert "gardée en mémoire" in caplog.text
    assert budget.consommation().cout == 7_000  # la mémoire compte
    budget.solder(0, Compteurs(appels=1, cout=3_000))
    assert _ligne(JOUR) == (0, 2, 10_000)  # le report est ajouté à l'écriture suivante


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


def test_la_table_de_consommation_n_a_aucun_texte_ni_adresse(base):
    colonnes = set(m.AssistantConsommation.__table__.columns.keys())
    assert colonnes == {
        "jour",
        "echanges",
        "appels",
        "jetons_entree",
        "jetons_cache_lus",
        "jetons_cache_ecrits",
        "jetons_sortie",
        "cout_microdollars",
        "modifie_le",
    }
    with session_base() as session:
        assert session.scalars(select(m.AssistantConsommation)).all() == []
