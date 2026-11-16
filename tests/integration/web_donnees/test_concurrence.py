"""Un seul chargement de jeu fictif ou une seule remise à zéro à la fois (revue n° 14).

Le premier chargement est ralenti (génération remplacée par une attente) ;
pendant qu'il tient le verrou, toute autre demande est refusée tout de suite,
proprement, au lieu de supprimer et recréer les tables en même temps que lui.
"""

from __future__ import annotations

import sqlite3
import sys
import types

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from tests.outils.web_donnees.concurrence import DELAI_S, _en_arriere_plan

from edt.services import jeux
from edt.web import app as module_app


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
