"""Saisies aberrantes : jamais d'erreur 500, toujours un message clair (revue n° 18 et 19)."""

from __future__ import annotations

import pytest

from edt.db.moteur import session_base
from edt.services.referentiels import operations
from edt.services.referentiels.operations import ErreurSaisie

TROP_GRAND = "99999999999999999999"  # au-delà de ce que SQLite sait enregistrer


def _salle(ids, **champs):
    return {
        "code": "ZZ7",
        "campus_id": str(ids.ndc),
        "type_salle_id": str(ids.type_cours),
        "capacite": "30",
        "accessibilite": "oui",
        **champs,
    }


def test_capacite_trop_grande(site, ids):
    reponse = site.post("/salles", data=_salle(ids, capacite=TROP_GRAND))
    assert reponse.status_code == 422
    assert "La valeur maximale est 100000." in reponse.text
    assert 'name="capacite" value="99999999999999999999"' in reponse.text  # saisie conservée


def test_effectif_trop_grand(site, ids):
    reponse = site.post(
        "/groupes",
        data={
            "annee_scolaire_id": ids.annee,
            "code": "ZZ",
            "libelle": "Essai",
            "nature": "transverse",
            "effectif": TROP_GRAND,
        },
    )
    assert reponse.status_code == 422 and "La valeur maximale est 100000." in reponse.text


def test_identifiant_trop_grand_dans_un_choix(site, ids):
    reponse = site.post("/salles", data=_salle(ids, campus_id=TROP_GRAND))
    assert reponse.status_code == 422 and "Nombre beaucoup trop grand." in reponse.text


@pytest.mark.parametrize("valeur", ["²", "٣", "1e3", "2**70"])
def test_chiffres_exotiques_refuses(site, ids, valeur):
    reponse = site.post("/salles", data=_salle(ids, capacite=valeur))
    assert reponse.status_code == 422 and "Nombre entier attendu." in reponse.text


def test_service_borne_les_entiers(ids):
    with session_base() as session, pytest.raises(ErreurSaisie) as erreur:
        operations.creer(session, "salle", _salle(ids, capacite=2**70))
    assert "capacite" in erreur.value.champs


def test_annee_de_debut_exotique(site):
    reponse = site.post(
        "/calendrier/annees/par-defaut", data={"annee_debut": "²"}, follow_redirects=False
    )
    assert reponse.status_code == 303
    page = site.get(reponse.headers["location"]).text
    assert "Année de début invalide" in page


def test_graine_exotique(client):
    reponse = client.post("/jeux/charger", data={"taille": "petit", "graine": "²"})
    assert reponse.status_code == 422 and "Nombre entier positif attendu" in reponse.text


@pytest.mark.parametrize(
    ("adresse", "statut"),
    [
        ("/salles?page=²", 422),
        (f"/salles/{TROP_GRAND}", 404),
        (f"/salles/{TROP_GRAND}/supprimer", 404),
        (f"/salles?campus={TROP_GRAND}", 422),
        ("/besoins?semestre=abc", 422),
    ],
)
def test_adresses_aberrantes(site, adresse, statut):
    reponse = site.get(adresse)
    assert reponse.status_code == statut
    assert "Une erreur inattendue" not in reponse.text


def test_jours_feries_d_une_annee_inexistante(site):
    reponse = site.post(f"/calendrier/annees/{TROP_GRAND}/feries")
    assert reponse.status_code == 404


def test_liste_vide_sans_filtre_pose(client):
    groupes = client.get("/groupes").text  # base sans groupe ; l'année en cours est le défaut
    assert "correspondant à la recherche" not in groupes
    assert "Aucun élément ne correspond" not in groupes
    assert "0 groupe (Année : 2026-2027)." in groupes
    assert "Aucun élément pour l'instant (Année : 2026-2027)" in groupes
    assert 'href="/groupes/nouveau?annee_scolaire_id=' in groupes

    salles = client.get("/salles").text
    assert 'Aucun élément pour l\'instant : « <a href="/salles/nouveau">' in salles


def test_liste_vide_avec_filtre_pose(site, ids):
    page = site.get("/groupes", params={"q": "inexistant"}).text
    assert "Aucun élément ne correspond à la recherche ou aux filtres." in page
    assert '<a href="/groupes">Effacer la recherche et les filtres</a>' in page
