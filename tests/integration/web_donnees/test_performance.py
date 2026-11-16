"""Banque des cas limites : temps et taille des pages sur l'établissement complet (jeu grand).

Le serveur sert une requête à la fois : une page de plusieurs secondes gèle
tout le monde, et un tableau non paginé peut peser plusieurs mégaoctets. Ces
seuils font voir une régression. Test long (chargement du jeu grand) : marqué
`perf`, exclu de la CI courante (`pytest -m "not perf"`).

Deux précautions rendent la mesure stable :

- le seuil porte sur le **temps processeur** de la requête
  (`time.process_time`), pas sur le temps écoulé : sur une machine chargée
  (autres tests, autres programmes), le temps écoulé d'une même page varie du
  simple au décuple, le temps processeur presque pas. Le temps écoulé reste
  borné largement : un blocage ou une attente de verrou se verraient là ;
- les pages sont mesurées dans un **interpréteur neuf** : en fin de suite
  complète, le processus des tests traîne l'état de mille tests et la page
  Générer y coûte jusqu'à 2,4 s de processeur, contre 1,65 s dans un
  interpréteur neuf (modules déjà chargés, pré-vérifications à froid).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import func, select

import edt
from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.services.jeux import recharger_jeu_fictif

pytestmark = [pytest.mark.perf, pytest.mark.cas_limite]

PROCESSEUR_PAGE_S = 2.0
"""Temps processeur maximal d'une page (référence : 0,1 à 0,6 s ; Générer à froid 1,65 s)."""
ECOULE_PAGE_S = 10.0
"""Temps écoulé maximal d'une page, même sur une machine très chargée."""
TAILLE_PAGE_MAX = 300_000
"""Octets (mesures de référence : 40 à 180 Ko)."""
PROCESSEUR_EXPORT_S = 10.0
"""Temps processeur maximal de l'export Excel complet (mesure de référence : 2 s)."""

MESURE = """
import json, sys, time
from fastapi.testclient import TestClient
from sqlalchemy import select
from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.services.jeux import recharger_jeu_fictif
from edt.web.app import creer_app

recharger_jeu_fictif("grand", 0, reinitialiser=True)
with session_base() as session:
    semestre, autre = session.scalars(select(m.Semestre.id).order_by(m.Semestre.date_debut)).all()[:2]
    nb_besoins = len(session.scalars(select(m.Besoin.id)).all())
client = TestClient(creer_app())
# Hors mesure : gabarits et modules chargés une fois pour toute la vie du serveur (le moteur
# des pré-vérifications, via Générer sur l'autre semestre ; ses résultats sont propres à
# chaque semestre, celui qui est mesuré reste à froid).
client.get("/")
client.get(f"/calculs?semestre_id={autre}")
mesures = {}
for adresse in sys.argv[1:]:
    processeur, ecoule = time.process_time(), time.perf_counter()
    reponse = client.get(adresse.replace("{semestre}", str(semestre)))
    mesures[adresse] = [
        reponse.status_code,
        round(time.process_time() - processeur, 2),
        round(time.perf_counter() - ecoule, 2),
        len(reponse.content),
    ]
print("BESOINS=" + str(nb_besoins))
print("MESURES=" + json.dumps(mesures))
"""
"""Script de mesure, lancé dans un interpréteur neuf sur la base du test."""


def test_pages_du_jeu_grand_sous_deux_secondes(base):
    """Cas : les pages les plus lourdes (besoins de tous les semestres, arbre des groupes,
    formulaires de règles avec toutes les salles, Générer à froid) sur l'établissement complet.
    Risque : une page de plusieurs secondes gèle le serveur pour tout le monde, un tableau non
    paginé pèse des mégaoctets ; sans seuil, la régression passe inaperçue."""
    adresses = (
        "/besoins?semestre=",  # tous les semestres
        "/groupes/arbre",
        "/regles/nouveau?type=SALLES",
        "/regles/nouveau?type=MAX_PAR_JOUR",
        "/calculs?semestre_id={semestre}",  # pré-vérifications à froid
    )
    source = str(Path(edt.__file__).resolve().parents[1])
    execution = subprocess.run(
        [sys.executable, "-c", MESURE, *adresses],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
        env={**os.environ, "PYTHONPATH": source, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )
    sorties = dict(
        ligne.split("=", 1)
        for ligne in execution.stdout.splitlines()
        if ligne.startswith(("BESOINS=", "MESURES="))
    )
    assert execution.returncode == 0 and "MESURES" in sorties, execution.stderr[-3000:]
    # Le vrai établissement complet, pas un jeu réduit : plus de mille besoins.
    assert int(sorties["BESOINS"]) > 1000
    mesures = json.loads(sorties["MESURES"])
    assert all(statut == 200 for statut, *_ in mesures.values()), mesures
    trop = [
        adresse
        for adresse, (_, processeur, ecoule, taille) in mesures.items()
        if processeur > PROCESSEUR_PAGE_S or ecoule > ECOULE_PAGE_S or taille > TAILLE_PAGE_MAX
    ]
    assert not trop, f"(statut, processeur s, écoulé s, octets) : {mesures}"


@pytest.fixture
def jeu_grand(client):
    """Le jeu grand chargé dans la base du test."""
    recharger_jeu_fictif("grand", 0, reinitialiser=True)
    client.get("/")  # premier appel : chargement des gabarits, hors mesure
    with session_base() as session:
        # Le vrai établissement complet, pas un jeu réduit : plus de mille besoins.
        assert session.scalar(select(func.count()).select_from(m.Besoin)) > 1000


def test_export_des_donnees_du_jeu_grand_sous_dix_secondes(client, jeu_grand):
    """Cas : l'export Excel de toutes les données de l'établissement complet. Risque : un
    export qui dépasse dix secondes bloque le serveur (une requête à la fois)."""
    processeur, ecoule = time.process_time(), time.perf_counter()
    reponse = client.get("/export/donnees.xlsx")
    processeur, ecoule = time.process_time() - processeur, time.perf_counter() - ecoule
    assert reponse.status_code == 200
    assert processeur < PROCESSEUR_EXPORT_S, (round(processeur, 2), round(ecoule, 2))
