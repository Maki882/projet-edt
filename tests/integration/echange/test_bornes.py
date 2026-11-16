"""Bornes contre les fichiers hostiles, vérifiées par l'interface web et sur une version générée.

Partie intégration de `tests/unitaires/echange/test_bornes.py` : archive bombe et
fichier trop long envoyés au site, page bornée, boucle d'événements qui répond
pendant un import, export CSV d'une version calculée et présélection de la
version active.
"""

from __future__ import annotations

import asyncio
import csv
import io
import threading
import time

import httpx2
import pytest
from sqlalchemy import select
from tests.outils.echange.fabrique import (
    archive_zip,
)

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import OrigineVersion
from edt.echange import lecture
from edt.services import echange as service

pytestmark = pytest.mark.cas_limite

HTMX = {"HX-Request": "true"}


def test_import_web_d_une_archive_bombe(base, client):
    """Cas : une archive bombe envoyée par le formulaire d'import du site.

    Risque : page d'erreur brute ou serveur bloqué au lieu d'un refus lisible dans une page
    légère.
    """
    bombe = archive_zip({"salles.csv": b"code;campus\n" + b"\n" * (21 * 1024 * 1024)})
    reponse = client.post(
        "/import/simuler",
        headers=HTMX,
        files=[("fichiers", ("bombe.zip", bombe, "application/zip"))],
        data={"mode": "donnees"},
    )
    assert reponse.status_code == 200
    assert "Archive ZIP refusé" in reponse.text
    assert len(reponse.content) < 200 * 1024


def test_page_bornee_et_rapport_complet_telechargeable(jeu_minimal, client):
    """Cas : un CSV de 1 500 lignes fautives (3 000 erreurs) simulé par le site.

    Risque : page de plusieurs mégaoctets illisible ; ou rapport téléchargeable incomplet.
    """
    lignes = "".join(f"{i};pas une date;y\n" for i in range(1500))
    envoi = {
        "files": [
            (
                "fichiers",
                ("annees.csv", ("libelle;date_debut;date_fin\n" + lignes).encode(), "text/csv"),
            )
        ],
        "data": {"mode": "donnees"},
    }

    page = client.post("/import/simuler", headers=HTMX, **envoi)

    assert page.status_code == 200
    assert "3000 ligne(s), les 200 premières affichées" in page.text
    assert page.text.count("<tr>") < 260  # 200 signalements, le bilan et l'aperçu
    assert "data:text/csv" not in page.text

    rapport = client.post("/import/rapport.csv", **envoi)
    assert rapport.status_code == 200 and rapport.headers["content-type"].startswith("text/csv")
    lignes_rapport = list(
        csv.reader(io.StringIO(rapport.content.decode("utf-8-sig")), delimiter=";")
    )
    assert sum(1 for ligne in lignes_rapport if ligne[0] == "erreur") == 3000


def test_fichier_de_19_mo_refuse_vite_par_le_web(base, client):
    """Cas : un fichier annees.csv de 19,5 Mo, 1 200 000 lignes invalides, envoyé au site.

    Risque : le serveur analyse chaque ligne et renvoie une page géante au lieu de refuser le
    tableau.
    """
    contenu = b"libelle;date_debut;date_fin\n" + b"x;pas une date;y\n" * 1_200_000
    assert len(contenu) < service.TAILLE_MAX_OCTETS
    debut = time.perf_counter()
    reponse = client.post(
        "/import/simuler",
        headers=HTMX,
        files=[("fichiers", ("annees.csv", contenu, "text/csv"))],
        data={"mode": "donnees"},
    )
    assert time.perf_counter() - debut < 15
    assert reponse.status_code == 200 and len(reponse.content) < 200 * 1024
    assert f"plus de {lecture.MAX_LIGNES_TABLEAU} lignes de données" in reponse.text


def test_un_import_ne_gele_pas_les_autres_requetes(jeu_minimal, monkeypatch):
    """Cas : un import dont le rendu de la page prend du temps.

    Risque : l'import tourne dans la boucle d'événements : tout le site ne répond plus pendant
    ce temps.
    """
    from edt.web.app import creer_app
    from edt.web.routes import echange as routes

    entre, libere = threading.Event(), threading.Event()
    rendre = routes.rendre

    def rendu_lent(*arguments, **options):
        entre.set()
        assert libere.wait(10)
        return rendre(*arguments, **options)

    monkeypatch.setattr(routes, "rendre", rendu_lent)
    app = creer_app()

    async def scenario() -> tuple[int, int]:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://essai") as client:
            envoi = client.post(
                "/import/simuler",
                files=[("fichiers", ("campus.csv", b"code;nom\nNDC;Notre-Dame\n", "text/csv"))],
                data={"mode": "donnees"},
            )
            tache = asyncio.ensure_future(envoi)
            assert await asyncio.to_thread(entre.wait, 10)
            sante = await asyncio.wait_for(client.get("/sante"), 5)
            assert not tache.done()  # le rendu attend encore, le site répond quand même
            libere.set()
            return sante.status_code, (await tache).status_code

    assert asyncio.run(scenario()) == (200, 200)


def test_csv_d_emploi_du_temps_neutralise(version_generee):
    """Cas : un libellé de besoin commençant par « + » dans l'export CSV d'un emploi du temps.

    Risque : le tableur interprète le libellé comme une formule à l'ouverture.
    """
    with session_base() as session:
        besoin = session.get(m.SeanceHebdo, 1).besoin
        besoin.libelle = "+33 SUM(A1)"
    with session_base() as session:
        fichier = service.exporter_emploi_du_temps(session, version_generee, "csv")
    assert " +33 SUM(A1)" in fichier.contenu.decode("utf-8-sig")


def test_export_preselectionne_la_version_active(version_generee, client, aujourdhui):
    """Cas : une version plus récente que la version active existe (référence d'un autre semestre).

    Risque : l'écran d'export présélectionne la mauvaise version : export d'un emploi du temps
    qui n'est pas celui en vigueur.
    """
    with session_base() as session:
        s2 = session.scalar(select(m.Semestre).where(m.Semestre.code == "S2"))
        session.add(
            m.VersionEdt(
                semestre_id=s2.id,
                numero=1,
                libelle="Témoin du second semestre",
                origine=OrigineVersion.REFERENCE,
                active=False,
            )
        )
    with session_base() as session:
        choix = service.choix_export(session, aujourdhui)
    assert choix.versions[0][1].endswith("Témoin du second semestre")  # en tête de liste...
    assert choix.version_par_defaut == version_generee  # ...mais l'active est présélectionnée

    page = client.get("/export").text
    assert f'<option value="{version_generee}" selected>' in page
