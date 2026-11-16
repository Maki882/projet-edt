"""Banque des cas limites : saisies aberrantes dans l'espace « Données ».

Jamais d'erreur 500, toujours un message clair sous le champ ou sur la page
(revue n° 18 et 19 ; inventaire des cas limites n° 7 et 8). Un cas qui relève
d'un autre lot reste marqué `xfail(strict=True)` avec le fichier à corriger.
"""

from __future__ import annotations

import re

import pytest
from tests.outils.web_donnees.aides import (
    NOMBRE_DE_5000_CHIFFRES,
    NOMBRE_TROP_GRAND,
    compter,
    existe,
    occurrence_prevue,
    saisie_besoin,
    saisie_regle,
    saisie_salle,
)

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.services.referentiels.lieux import cle_trajet

pytestmark = pytest.mark.cas_limite


# --------------------------------------------------------------------------- #
# Entiers trop grands ou exotiques (revue n° 18)
# --------------------------------------------------------------------------- #


def test_capacite_trop_grande(site, ids):
    """Cas : une capacité de vingt chiffres. Risque : OverflowError de SQLite (erreur 500) ou
    capacité absurde enregistrée."""
    reponse = site.post("/salles", data=saisie_salle(ids, capacite=NOMBRE_TROP_GRAND))
    assert reponse.status_code == 422
    assert "La valeur maximale est 100000." in reponse.text
    assert f'name="capacite" value="{NOMBRE_TROP_GRAND}"' in reponse.text  # saisie conservée


def test_effectif_trop_grand(site, ids):
    """Cas : un effectif de groupe de vingt chiffres. Risque : erreur 500 ou effectif absurde
    qui fausse le contrôle des capacités."""
    reponse = site.post(
        "/groupes",
        data={
            "annee_scolaire_id": ids.annee,
            "code": "ZZ",
            "libelle": "Essai",
            "nature": "transverse",
            "effectif": NOMBRE_TROP_GRAND,
        },
    )
    assert reponse.status_code == 422 and "La valeur maximale est 100000." in reponse.text


def test_identifiant_trop_grand_dans_un_choix(site, ids):
    """Cas : une liste de choix (campus) renvoyée avec un identifiant de vingt chiffres. Risque :
    OverflowError à la recherche de l'élément choisi."""
    reponse = site.post("/salles", data=saisie_salle(ids, campus_id=NOMBRE_TROP_GRAND))
    assert reponse.status_code == 422 and "Nombre beaucoup trop grand." in reponse.text


@pytest.mark.parametrize("valeur", ["²", "٣", "1e3", "2**70"])
def test_chiffres_exotiques_refuses(site, ids, valeur):
    """Cas : une capacité « ² », « ٣ » (chiffre arabe), « 1e3 » ou « 2**70 ». Risque : Python
    les lit comme des chiffres (`isdigit`) et une valeur inattendue est enregistrée."""
    reponse = site.post("/salles", data=saisie_salle(ids, capacite=valeur))
    assert reponse.status_code == 422 and "Nombre entier attendu." in reponse.text


def test_annee_de_debut_exotique(site):
    """Cas : « Ajouter l'année » avec une année de début « ² ». Risque : int() lève ValueError,
    erreur 500 sur le calendrier."""
    reponse = site.post(
        "/calendrier/annees/par-defaut", data={"annee_debut": "²"}, follow_redirects=False
    )
    assert reponse.status_code == 303
    page = site.get(reponse.headers["location"]).text
    assert "Année de début invalide" in page


def test_graine_exotique(client):
    """Cas : la graine du jeu fictif saisie « ² ». Risque : int() lève ValueError, erreur 500."""
    reponse = client.post("/jeux/charger", data={"taille": "petit", "graine": "²"})
    assert reponse.status_code == 422 and "Nombre entier positif attendu" in reponse.text


@pytest.mark.parametrize(
    ("adresse", "statut"),
    [
        ("/salles?page=²", 422),
        (f"/salles/{NOMBRE_TROP_GRAND}", 404),
        (f"/salles/{NOMBRE_TROP_GRAND}/supprimer", 404),
        (f"/salles?campus={NOMBRE_TROP_GRAND}", 422),
        ("/besoins?semestre=abc", 422),
    ],
)
def test_adresses_aberrantes(site, adresse, statut):
    """Cas : une adresse retouchée à la main (page « ² », identifiant ou filtre de vingt
    chiffres). Risque : erreur 500 au lieu de « introuvable » ou « valeur invalide »."""
    reponse = site.get(adresse)
    assert reponse.status_code == statut
    assert "Une erreur inattendue" not in reponse.text


def test_jours_feries_d_une_annee_inexistante(site):
    """Cas : « Ajouter les fériés » envoyé pour une année de vingt chiffres. Risque : erreur 500
    au lieu de « introuvable »."""
    reponse = site.post(f"/calendrier/annees/{NOMBRE_TROP_GRAND}/feries")
    assert reponse.status_code == 404


# --------------------------------------------------------------------------- #
# Listes vides (revue n° 19)
# --------------------------------------------------------------------------- #


def test_liste_vide_sans_filtre_pose(client):
    """Cas : une liste vide alors que seul le filtre par défaut (l'année en cours) s'applique.
    Risque : le message « rien ne correspond à vos filtres » fait chercher un filtre inexistant
    au lieu de proposer d'ajouter un élément."""
    groupes = client.get("/groupes").text  # base sans groupe ; l'année en cours est le défaut
    assert "correspondant à la recherche" not in groupes
    assert "Aucun élément ne correspond" not in groupes
    assert "0 groupe (Année : 2026-2027)." in groupes
    assert "Aucun élément pour l'instant (Année : 2026-2027)" in groupes
    assert 'href="/groupes/nouveau?annee_scolaire_id=' in groupes

    salles = client.get("/salles").text
    assert 'Aucun élément pour l\'instant : « <a href="/salles/nouveau">' in salles


def test_liste_vide_avec_filtre_pose(site, ids):
    """Cas : une recherche qui ne trouve rien. Risque : liste vide sans moyen visible de revenir
    à la liste complète."""
    page = site.get("/groupes", params={"q": "inexistant"}).text
    assert "Aucun élément ne correspond à la recherche ou aux filtres." in page
    assert '<a href="/groupes">Effacer la recherche et les filtres</a>' in page


# --------------------------------------------------------------------------- #
# Nombres de cinq mille chiffres (inventaire n° 7)
# --------------------------------------------------------------------------- #

CINQ_MILLE = NOMBRE_DE_5000_CHIFFRES


def _envois_de_cinq_mille_chiffres(ids) -> list:
    trajet = cle_trajet(ids.ndc, ids.issy)
    return [
        ("get", f"/salles?page={CINQ_MILLE}", None, 422, "Numéro de page invalide"),
        ("get", f"/regles?page={CINQ_MILLE}", None, 422, "Numéro de page invalide"),
        ("get", f"/regles?annee={CINQ_MILLE}", None, 404, "Année scolaire introuvable"),
        ("get", f"/salles/{CINQ_MILLE}", None, 404, "Page introuvable"),
        ("get", f"/regles/{CINQ_MILLE}/modifier", None, 404, "Page introuvable"),
        ("get", f"/groupes/arbre?annee={CINQ_MILLE}", None, 404, "Année scolaire introuvable"),
        ("post", "/salles", saisie_salle(ids, capacite=CINQ_MILLE), 422, "beaucoup trop grand"),
        ("post", "/jeux/charger", {"taille": "petit", "graine": CINQ_MILLE}, 422, "entier positif"),
        (
            "post",
            f"/salles/{ids.salle_n016}/supprimer",
            {"_regles": CINQ_MILLE},
            422,
            "Règles confirmées illisibles",
        ),
        ("post", "/regles/nouveau", saisie_regle(ids, cible_groupe=CINQ_MILLE), 422, "inconnu"),
        ("post", "/campus/trajets", {trajet: CINQ_MILLE}, 422, "Nombre de minutes attendu."),
    ]


def test_nombres_de_cinq_mille_chiffres_refuses(site, ids):
    """Cas : un nombre de 5 000 chiffres dans une adresse (page, année, identifiant) ou un
    formulaire (capacité, graine, règles confirmées, cible, trajet). Risque : Python refuse de
    convertir plus de 4 300 chiffres, ValueError non rattrapée, erreur 500."""
    for methode, adresse, donnees, statut, message in _envois_de_cinq_mille_chiffres(ids):
        reponse = getattr(site, methode)(adresse, **({"data": donnees} if donnees else {}))
        assert reponse.status_code == statut, (adresse[:60], reponse.status_code)
        assert message in reponse.text.replace("&#39;", "'"), adresse[:60]
        assert "Une erreur inattendue" not in reponse.text, adresse[:60]
    assert existe(m.Salle, ids.salle_n016)  # la suppression n'a pas eu lieu

    annee = site.post("/calendrier/annees/par-defaut", data={"annee_debut": CINQ_MILLE})
    assert annee.status_code == 200 and "Année de début invalide" in annee.text


def test_seance_de_cinq_mille_chiffres_dans_l_emploi_du_temps(site, version_generee):
    """Cas : l'adresse de l'emploi du temps avec « seance=h » suivi de 5 000 chiffres (lien
    abîmé). Risque : erreur 500 au lieu d'ignorer la séance mise en évidence."""
    reponse = site.get(f"/emploi-du-temps?seance=h{CINQ_MILLE}")
    assert reponse.status_code in (200, 404, 422)


def test_niveau_de_regle_de_cinq_mille_chiffres_au_lancement(site, ids):
    """Cas : le formulaire « Lancer le calcul » avec un champ niveau_regle_<5 000 chiffres>.
    Risque : erreur 500 au lancement au lieu d'un refus clair."""
    reponse = site.post(
        "/calculs",
        data={"semestre_id": str(ids.semestre), f"niveau_regle_{CINQ_MILLE}": "important"},
    )
    assert reponse.status_code in (303, 404, 422)


# --------------------------------------------------------------------------- #
# Textes libres bornés côté serveur (inventaire n° 8)
# --------------------------------------------------------------------------- #


def test_note_de_besoin_bornee_cote_serveur(site, ids):
    """Cas : la note d'un besoin envoyée avec 5 001 caractères ou un mégaoctet (le navigateur
    borne, pas un envoi fabriqué). Risque : tout est enregistré, les pages et la base
    s'alourdissent."""
    avant = compter(m.Besoin)
    for taille in (5001, 1_000_000):
        refus = site.post("/besoins", data=saisie_besoin(ids, commentaire="x" * taille))
        assert refus.status_code == 422
        erreur = re.search(r'id="erreur-commentaire">([^<]+)<', refus.text).group(1)
        assert erreur == "5000 caractères au plus."
    assert compter(m.Besoin) == avant
    accepte = site.post(
        "/besoins", data=saisie_besoin(ids, commentaire="x" * 5000), follow_redirects=False
    )
    assert accepte.status_code == 303 and compter(m.Besoin) == avant + 1  # la limite passe


def test_commentaire_de_regle_borne_cote_serveur(site, ids):
    """Cas : le commentaire d'une règle envoyé avec 200 000 caractères (maxlength du navigateur
    contourné). Risque : enregistré tel quel et recopié dans l'infobulle de chaque ligne de la
    liste des règles (des centaines de kilo-octets par page)."""
    reponse = site.post("/regles/nouveau", data=saisie_regle(ids, commentaire="y" * 200_000))
    assert reponse.status_code == 422
    erreur = re.search(r'id="erreur-commentaire">([^<]+)<', reponse.text).group(1)
    assert erreur == "2000 caractères au plus (lu : 200000)."
    assert compter(m.Regle, m.Regle.commentaire.like("yyy%")) == 0


def test_motif_d_annulation_borne_cote_serveur(site, version_generee):
    """Cas : le motif d'annulation d'une séance datée envoyé avec 200 000 caractères. Risque :
    enregistré dans la note de la séance et réaffiché dans son panneau (page de 200 Ko)."""
    occurrence = occurrence_prevue(version_generee)
    reponse = site.post(
        f"/emploi-du-temps/occurrences/{occurrence}/annuler", data={"motif": "z" * 200_000}
    )
    with session_base() as session:
        stocke = session.get(m.Occurrence, occurrence).commentaire or ""
    assert reponse.status_code == 422 and len(stocke) <= 600
