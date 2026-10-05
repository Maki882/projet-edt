"""Un seul chargement de jeu fictif ou une seule remise à zéro à la fois (revue n° 14).

Le premier chargement est ralenti (génération remplacée par une attente) ;
pendant qu'il tient le verrou, toute autre demande est refusée tout de suite,
proprement, au lieu de supprimer et recréer les tables en même temps que lui.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
import types
from types import SimpleNamespace

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from edt.services import jeux
from edt.web import app as module_app

DELAI_S = 10


def _rapport_factice(graine: int) -> SimpleNamespace:
    """Ce que la page de résultat lit d'un `RapportJeu`."""
    return SimpleNamespace(
        graine=graine,
        duree_s=0.0,
        compteurs={"salle": 0, "enseignant": 0},
        nb_besoins=0,
        resume=lambda: f"Jeu fictif (graine {graine})",
    )


@pytest.fixture
def generation_lente(monkeypatch):
    """Remplace la génération par une attente : `entre` signale qu'elle tient le verrou."""
    entre, liberer = threading.Event(), threading.Event()

    def generer(session, configuration, graine, **options):
        entre.set()
        assert liberer.wait(DELAI_S), "la génération ralentie n'a pas été libérée"
        return _rapport_factice(graine)

    monkeypatch.setattr(jeux, "generer_jeu", generer)
    yield entre, liberer
    liberer.set()


def _en_arriere_plan(fonction) -> tuple[threading.Thread, dict]:
    resultat: dict = {}

    def executer():
        try:
            resultat["valeur"] = fonction()
        except BaseException as erreur:  # transmis au test, qui le relance
            resultat["erreur"] = erreur

    fil = threading.Thread(target=executer)
    fil.start()
    return fil, resultat


def test_service_refuse_un_second_chargement_ou_une_remise_a_zero(base, generation_lente):
    entre, liberer = generation_lente
    fil, premier = _en_arriere_plan(
        lambda: jeux.recharger_jeu_fictif("petit", 3, reinitialiser=True)
    )
    assert entre.wait(DELAI_S)
    assert jeux.operation_en_cours()

    with pytest.raises(jeux.ErreurChargementEnCours, match="déjà en cours"):
        jeux.recharger_jeu_fictif("petit", 4, reinitialiser=True)
    with pytest.raises(jeux.ErreurChargementEnCours, match="remettre la base à zéro"):
        jeux.remettre_a_zero()

    liberer.set()
    fil.join(DELAI_S)
    assert "erreur" not in premier, premier.get("erreur")
    assert premier["valeur"].graine == 3
    assert not jeux.operation_en_cours()
    jeux.remettre_a_zero()  # le verrou est rendu : la demande suivante passe


def test_deux_chargements_simultanes_depuis_la_page(client, generation_lente):
    entre, liberer = generation_lente
    fil, premier = _en_arriere_plan(
        lambda: client.post("/jeux/charger", data={"taille": "petit", "graine": "1"})
    )
    assert entre.wait(DELAI_S)

    second = client.post(
        "/jeux/charger", data={"taille": "moyen", "graine": "2", "confirmation": "1"}
    )
    assert second.status_code == 409
    assert "un chargement de jeu de données ou une remise à zéro est déjà en cours" in second.text
    assert "Base de données" not in second.text and "init-db" not in second.text

    zero = client.post("/jeux/remise-a-zero", data={"confirmation_zero": "1"})
    assert zero.status_code == 409 and "déjà en cours" in zero.text

    liberer.set()
    fil.join(DELAI_S)
    assert premier["valeur"].status_code == 200
    assert "Établissement fictif « petit » chargé" in premier["valeur"].text


NOM_MODULE_ESSAI = "edt.web.routes.essai_base_occupee"


@pytest.fixture
def client_pannes(base, monkeypatch):
    """Application avec des routes d'essai qui lèvent les pannes de base de SQLite."""
    routeur = APIRouter(prefix="/essai")

    def panne(message: str):
        raise OperationalError("SELECT 1", {}, sqlite3.OperationalError(message))

    routeur.add_api_route("/verrou", lambda: panne("database is locked"))
    routeur.add_api_route("/tables", lambda: panne("no such table: salle"))
    routeur.add_api_route("/disque", lambda: panne("disk I/O error"))
    module = types.ModuleType(NOM_MODULE_ESSAI)
    module.routeur = routeur
    monkeypatch.setitem(sys.modules, NOM_MODULE_ESSAI, module)
    reels = module_app.modules_de_routes()
    monkeypatch.setattr(module_app, "modules_de_routes", lambda: [*reels, NOM_MODULE_ESSAI])
    return TestClient(module_app.creer_app())


def test_base_occupee_n_est_pas_presentee_comme_non_initialisee(client_pannes):
    occupee = client_pannes.get("/essai/verrou")
    assert occupee.status_code == 503
    assert "Base de données occupée" in occupee.text and "réessayer" in occupee.text
    assert "init-db" not in occupee.text

    absente = client_pannes.get("/essai/tables")
    assert "Base de données indisponible" in absente.text and "init-db" in absente.text

    inaccessible = client_pannes.get("/essai/disque")
    assert "pas accessible" in inaccessible.text and "init-db" not in inaccessible.text


def test_tables_absentes_pendant_un_chargement_donnent_base_occupee(client_pannes):
    assert jeux._verrou_base.acquire(blocking=False)  # un chargement « en cours »
    try:
        reponse = client_pannes.get("/essai/tables")
    finally:
        jeux._verrou_base.release()
    assert reponse.status_code == 503
    assert "Base de données occupée" in reponse.text and "init-db" not in reponse.text
