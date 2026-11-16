"""Page des jeux de données : établissement fictif, refus pendant un calcul, remise à zéro."""

from __future__ import annotations

import html
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest
from sqlalchemy import func, select

from edt.db.modeles import Besoin, Campus
from edt.db.moteur import session_base
from edt.services.emploi_du_temps import prechauffage
from edt.synthese.configuration import GRAINE_MAX


def _compter(modele) -> int:
    with session_base() as session:
        return session.scalar(select(func.count()).select_from(modele))


def test_page_decrit_les_tailles(client):
    page = client.get("/jeux").text
    for taille in ("Petit", "Moyen", "Grand"):
        assert f"<strong>{taille}</strong>" in page
    assert 'name="graine" value="0"' in page
    assert "data-attente=" in page  # bouton désactivé pendant l'envoi


def test_chargement_du_jeu_petit(client, monkeypatch):
    prechauffages = []
    monkeypatch.setattr(prechauffage, "lancer", prechauffages.append)
    reponse = client.post("/jeux/charger", data={"taille": "petit", "graine": "0"})

    assert reponse.status_code == 200
    assert "Établissement fictif « petit » chargé" in reponse.text
    assert "Jeu fictif « petit » (graine 0)" in reponse.text  # résumé du rapport
    assert 'href="/besoins"' in reponse.text
    assert _compter(Campus) == 2 and _compter(Besoin) > 0
    assert len(prechauffages) == 1  # écrans suivants préparés pendant la lecture du résumé


@pytest.mark.cas_limite
def test_graine_au_dela_de_la_borne_du_generateur_refusee_par_l_ecran(client):
    """Cas : la graine saisie à l'écran vaut un de plus que la plus grande graine admise par
    le générateur (et par la commande `python -m edt jeu`).

    Risque : l'écran accepte la graine, la génération la refuse ensuite ; ou l'écran refuse
    une graine que la commande accepte. Le message donne la borne exacte.
    """
    reponse = client.post("/jeux/charger", data={"taille": "petit", "graine": str(GRAINE_MAX + 1)})
    assert reponse.status_code == 422
    assert f"de 0 à {GRAINE_MAX}" in reponse.text
    assert _compter(Campus) == 0


def test_rechargement_exige_une_confirmation_d_effacement(jeu_minimal, client):
    page = client.get("/jeux").text
    assert "Seront effacés" in page and "Salles : 6" in page

    refus = client.post("/jeux/charger", data={"taille": "petit", "graine": "0"})
    assert refus.status_code == 422 and "Cochez la case" in refus.text
    assert _compter(Besoin) == 16  # rien n'a changé

    ok = client.post("/jeux/charger", data={"taille": "petit", "graine": "1", "confirmation": "1"})
    assert ok.status_code == 200 and "(graine 1)" in ok.text


def test_refus_clair_pendant_un_calcul(jeu_minimal, client, monkeypatch):
    monkeypatch.setattr(
        "edt.services.jeux.calcul_en_cours", lambda: SimpleNamespace(id=42, semestre_id=1)
    )
    charge = client.post(
        "/jeux/charger", data={"taille": "petit", "graine": "0", "confirmation": "1"}
    )
    assert charge.status_code == 409
    assert "un calcul d&#39;emploi du temps est en cours" in charge.text
    assert (
        "Une erreur inattendue" not in charge.text
        and 'class="message message-attention"' in charge.text
    )

    zero = client.post("/jeux/remise-a-zero", data={"confirmation_zero": "1"})
    assert zero.status_code == 409 and "calcul n°42" in zero.text
    assert _compter(Besoin) == 16


def test_remise_a_zero_confirmee(jeu_minimal, client):
    sans_confirmation = client.post("/jeux/remise-a-zero", data={})
    assert sans_confirmation.status_code == 422
    assert _compter(Campus) == 2

    reponse = client.post(
        "/jeux/remise-a-zero", data={"confirmation_zero": "1"}, follow_redirects=True
    )
    assert "La base a été remise à zéro" in reponse.text
    assert _compter(Campus) == 0 and _compter(Besoin) == 0
    assert client.get("/salles").status_code == 200


def _port_libre() -> int:
    with socket.socket() as sonde:
        sonde.bind(("127.0.0.1", 0))
        return sonde.getsockname()[1]


def _attendre_calcul(client: httpx2.Client, reponse: httpx2.Response) -> int:
    calcul_id = int(re.search(r"/calculs/(\d+)", str(reponse.url)).group(1))
    for _ in range(600):
        etat = client.get(f"/calculs/{calcul_id}/etat", headers={"HX-Request": "true"})
        if "every 1s" not in etat.text:
            return calcul_id
        time.sleep(0.5)
    raise AssertionError(f"le calcul {calcul_id} ne finit pas")


@pytest.mark.perf
@pytest.mark.cas_limite
def test_recharger_apres_une_autre_solution_sur_un_vrai_serveur(tmp_path):
    """Cas : sur le serveur réel (uvicorn, autre processus), charger le jeu moyen, calculer,
    « Proposer une autre solution », puis recharger un jeu avec confirmation.

    Risque : la base est vidée, puis le chargement refusé « Le schéma de la base ne
    correspond plus au modèle... Rien n'a été modifié » ; chaque essai suivant échoue de
    même jusqu'au redémarrage. Défaut intermittent, propre au processus du serveur (le
    client de test ne le reproduit pas) : ce test rejoue le parcours de la démonstration.
    """
    port = _port_libre()
    environnement = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[3] / "src"),
        "EDT_DB_URL": f"sqlite:///{(tmp_path / 'edt.db').as_posix()}",
        "EDT_LOG_DIR": str(tmp_path / "journaux"),
    }
    serveur = subprocess.Popen(
        [sys.executable, "-m", "edt", "serve", "--port", str(port)],
        env=environnement,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with httpx2.Client(
            base_url=f"http://127.0.0.1:{port}", timeout=120, follow_redirects=True
        ) as client:
            for _ in range(100):
                try:
                    client.get("/sante")
                    break
                except httpx2.TransportError:
                    time.sleep(0.2)
            charge = {"taille": "moyen", "graine": "0", "confirmation": "1"}
            assert client.post("/jeux/charger", data=charge).status_code == 200
            semestre = re.search(
                r'name="semestre_id"[^>]*>\s*<option value="(\d+)"', client.get("/calculs").text
            )
            lancement = {"semestre_id": semestre.group(1), "duree": "court", "optimiser": "1"}
            calcul = _attendre_calcul(client, client.post("/calculs", data=lancement))
            client.get(f"/calculs/{calcul}")
            relance = client.post(f"/calculs/{calcul}/relancer", data={"autre_solution": "1"})
            client.get(f"/calculs/{_attendre_calcul(client, relance)}")

            reponse = client.post("/jeux/charger", data={**charge, "taille": "petit"})

            assert reponse.status_code == 200, reponse.text[:2000]
            assert "« petit » chargé : 14 salles" in html.unescape(reponse.text)
    finally:
        serveur.terminate()
        serveur.wait(10)
