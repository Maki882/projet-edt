"""Section « Diagnostic » de la page d'un calcul : présence, bouton, fragment rendu, erreurs.

Requêtes HTTP au serveur de test, sur le jeu minimal rendu impossible (`semestre_bloque` :
l'enseignante d'anglais absente tous les jours ; `calcul_impossible` : un calcul « impossible »
inscrit tel quel). Le diagnostic tourne pour de vrai, avec le budget de la
page. Les textes comparés sont ceux du contrat entre le calcul et les écrans (PLAN P9, §1 et §4).
"""

from __future__ import annotations

import datetime as dt
import html

import pytest
from sqlalchemy import delete
from tests.outils.solveur.en_base import (
    MOTIF_BLOCAGE,
    bloquer_un_enseignant,
    calcul_fictif,
    calcul_impossible,
)
from tests.outils.web_edt.aides import HX

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import EtatCalcul
from edt.services.diagnostic import AVERTISSEMENT_DONNEES_ACTUELLES, lire_diagnostic

BOUTON = "Chercher les règles et les données en cause"
TOUJOURS_GARDEES = "Les règles qui définissent le problème sont toujours gardées"
LIBELLES_STATUT = {
    "minimal": "Ensemble minimal trouvé",
    "partiel": "Ensemble trouvé, peut-être pas le plus petit",
    "possible": "Le calcul n'est plus impossible",
    "indetermine": "Diagnostic sans conclusion",
}


@pytest.fixture
def semestre_bloque(jeu_minimal) -> int:
    """Semestre du jeu minimal rendu impossible : l'enseignante d'anglais absente tous les jours."""
    bloquer_un_enseignant(jeu_minimal.semestre_id)
    return jeu_minimal.semestre_id


def _texte(reponse) -> str:
    """Texte de la réponse, entités HTML décodées (« n&#39;est » devient « n'est »)."""
    return html.unescape(reponse.text)


def _section_presente(page: str) -> bool:
    return 'id="diagnostic"' in page


# --------------------------------------------------------------------------- #
# Présence de la section
# --------------------------------------------------------------------------- #


def test_section_presente_sur_un_calcul_impossible(semestre_bloque, client):
    calcul_id = calcul_impossible(semestre_bloque)

    reponse = client.get(f"/calculs/{calcul_id}")

    assert reponse.status_code == 200
    page = _texte(reponse)
    assert _section_presente(page)
    assert "<h2>Diagnostic</h2>" in page
    assert BOUTON in page
    assert f"/calculs/{calcul_id}/diagnostic" in page
    assert 'id="diagnostic-resultat"' in page
    # Aucun diagnostic encore : aucun statut affiché.
    assert not any(libelle in page for libelle in LIBELLES_STATUT.values())


def test_section_absente_sur_un_calcul_reussi(version_generee, client):
    page = _texte(client.get(f"/calculs/{version_generee.calcul_id}"))

    assert not _section_presente(page)
    assert BOUTON not in page


@pytest.mark.parametrize("etat", [EtatCalcul.TEMPS_DEPASSE, EtatCalcul.ARRETE, EtatCalcul.ERREUR])
def test_section_absente_sur_un_calcul_ni_impossible_ni_reussi(jeu_minimal, client, etat):
    calcul_id = calcul_fictif(
        jeu_minimal.semestre_id, etat=etat, termine_le=dt.datetime.now(), statistiques={}
    )

    reponse = client.get(f"/calculs/{calcul_id}")

    assert reponse.status_code == 200
    assert not _section_presente(_texte(reponse))


# --------------------------------------------------------------------------- #
# Lancement depuis la page
# --------------------------------------------------------------------------- #


def test_bouton_rend_le_fragment_du_diagnostic(semestre_bloque, client):
    calcul_id = calcul_impossible(semestre_bloque)

    reponse = client.post(f"/calculs/{calcul_id}/diagnostic", headers=HX)

    assert reponse.status_code == 200, reponse.text
    fragment = _texte(reponse)
    assert fragment.lstrip().startswith('<div id="diagnostic-resultat"')
    assert "<html" not in fragment  # un fragment, pas une page entière
    diagnostic = lire_diagnostic(calcul_id)
    assert diagnostic is not None
    assert LIBELLES_STATUT[diagnostic.statut.value] in fragment
    assert diagnostic.message in fragment
    assert "Données en cause" in fragment and "Règles en cause" in fragment
    assert all(e.libelle in fragment for e in diagnostic.regles + diagnostic.donnees)
    assert TOUJOURS_GARDEES in fragment
    assert AVERTISSEMENT_DONNEES_ACTUELLES in fragment
    assert "essai" in fragment and "au plus 30 s" in fragment
    if diagnostic.statut.value == "partiel":
        assert "peut-être pas le plus petit" in fragment and "--temps 600" in fragment


def test_diagnostic_reaffiche_a_l_ouverture_de_la_page(semestre_bloque, client):
    calcul_id = calcul_impossible(semestre_bloque)

    reponse = client.post(f"/calculs/{calcul_id}/diagnostic", follow_redirects=False)

    assert reponse.status_code == 303
    assert reponse.headers["location"].endswith(f"/calculs/{calcul_id}")
    diagnostic = lire_diagnostic(calcul_id)
    page = _texte(client.get(f"/calculs/{calcul_id}"))
    assert LIBELLES_STATUT[diagnostic.statut.value] in page
    assert all(e.libelle in page for e in diagnostic.donnees)
    assert AVERTISSEMENT_DONNEES_ACTUELLES in page


@pytest.mark.cas_limite
def test_donnees_corrigees_depuis_le_calcul_le_fragment_le_dit(semestre_bloque, client):
    """Cas : la donnée fautive est corrigée après le calcul ; diagnostic lancé depuis la page.

    Risque : la page montrerait des listes vides comme un ensemble trouvé, ou des causes qui
    n'existent plus, au lieu de dire que le calcul n'est plus impossible.
    """
    calcul_id = calcul_impossible(semestre_bloque)
    with session_base() as session:
        session.execute(delete(m.Indisponibilite).where(m.Indisponibilite.motif == MOTIF_BLOCAGE))

    fragment = _texte(client.post(f"/calculs/{calcul_id}/diagnostic", headers=HX))

    assert LIBELLES_STATUT["possible"] in fragment
    assert "Données en cause" not in fragment and "Règles en cause" not in fragment
    assert TOUJOURS_GARDEES not in fragment
    assert AVERTISSEMENT_DONNEES_ACTUELLES in fragment


# --------------------------------------------------------------------------- #
# Erreurs affichées proprement
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
@pytest.mark.parametrize("entetes", [HX, {}], ids=["htmx", "page"])
def test_diagnostic_d_un_calcul_reussi_refuse_proprement(version_generee, client, entetes):
    """Cas : POST du diagnostic sur un calcul réussi (adresse forgée, onglet resté ouvert).

    Risque : une erreur technique (500, trace) ou un diagnostic enregistré sur un calcul qui
    n'est pas impossible.
    """
    calcul_id = version_generee.calcul_id

    reponse = client.post(
        f"/calculs/{calcul_id}/diagnostic", headers=entetes, follow_redirects=False
    )

    assert reponse.status_code == 409, reponse.text
    texte = _texte(reponse)
    assert "Traceback" not in texte and "ErreurRegleMetier" not in texte
    assert lire_diagnostic(calcul_id) is None
    if entetes:
        assert "<html" not in texte  # fragment d'erreur, la page reste en place


@pytest.mark.cas_limite
@pytest.mark.parametrize("adresse", ["/calculs/999999/diagnostic", "/calculs/0/diagnostic"])
@pytest.mark.parametrize("entetes", [HX, {}], ids=["htmx", "page"])
def test_diagnostic_d_un_calcul_introuvable(jeu_minimal, client, adresse, entetes):
    """Cas : POST du diagnostic sur un calcul inexistant ou d'identifiant nul.

    Risque : une erreur technique (500, trace) au lieu d'un « introuvable » lisible.
    """
    reponse = client.post(adresse, headers=entetes, follow_redirects=False)

    assert reponse.status_code == 404, reponse.text
    texte = _texte(reponse)
    assert "Traceback" not in texte and "ErreurIntrouvable" not in texte


@pytest.mark.cas_limite
def test_diagnostic_d_un_identifiant_non_entier(jeu_minimal, client):
    """Cas : POST sur /calculs/abc/diagnostic.

    Risque : une erreur de validation brute (JSON 422) au lieu d'une page « introuvable ».
    """
    reponse = client.post("/calculs/abc/diagnostic", follow_redirects=False)

    assert reponse.status_code == 404
