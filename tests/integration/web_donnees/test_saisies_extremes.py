"""Saisies aberrantes : jamais d'erreur 500, toujours un message.

Jamais d'erreur 500, toujours un message clair sous le champ ou sur la page :
entiers trop grands ou exotiques, listes vides, nombres de cinq mille
chiffres, textes libres trop longs. Les mêmes saisies sur les écrans Générer
et Emploi du temps sont dans `tests/integration/web_edt/test_cas_limites.py`.
"""

from __future__ import annotations

import pytest
from tests.outils.web_donnees.aides import (
    NOMBRE_DE_5000_CHIFFRES,
    NOMBRE_TROP_GRAND,
    compter,
    erreur_sous_le_champ,
    existe,
    saisie_besoin,
    saisie_regle,
    saisie_salle,
)

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.regles import ErreurSaisie
from edt.services import regles as service_regles
from edt.services.referentiels.lieux import cle_trajet

pytestmark = pytest.mark.cas_limite


# --------------------------------------------------------------------------- #
# Entiers trop grands ou exotiques
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
# Listes vides
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
# Nombres de cinq mille chiffres
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
        (
            "post",
            f"/salles/{ids.salle_n016}/supprimer",
            {"_regles": "0"},
            422,
            "Règles confirmées illisibles : « 0 » n'est pas un identifiant",
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


# --------------------------------------------------------------------------- #
# Textes libres bornés côté serveur
# --------------------------------------------------------------------------- #


def test_note_de_besoin_bornee_cote_serveur(site, ids):
    """Cas : la note d'un besoin envoyée avec 5 001 caractères ou un mégaoctet (le navigateur
    borne, pas un envoi fabriqué). Risque : tout est enregistré, les pages et la base
    s'alourdissent."""
    avant = compter(m.Besoin)
    for taille in (5001, 1_000_000):
        refus = site.post("/besoins", data=saisie_besoin(ids, commentaire="x" * taille))
        assert refus.status_code == 422
        erreur = erreur_sous_le_champ(refus, "commentaire")
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
    erreur = erreur_sous_le_champ(reponse, "commentaire")
    assert erreur == "2000 caractères au plus (lu : 200000)."
    assert compter(m.Regle, m.Regle.commentaire.like("yyy%")) == 0
    # La borne est celle du service : un import ou un appel direct est refusé de même.
    brouillon = {
        "type": "JOURS_TRAVAILLES",
        "annee": ids.annee,
        "portee": {"nature": "groupe", "cible": ids.classe_g1},
        "parametres": {"jours": ["lundi"]},
        "commentaire": "y" * (service_regles.LONGUEUR_COMMENTAIRE + 1),
    }
    with session_base() as session, pytest.raises(ErreurSaisie) as refus:
        service_regles.creer(session, brouillon)
    assert refus.value.erreurs == {"commentaire": "2000 caractères au plus (lu : 2001)."}
    limite = saisie_regle(ids, commentaire="y" * service_regles.LONGUEUR_COMMENTAIRE)
    accepte = site.post("/regles/nouveau", data=limite, follow_redirects=False)
    assert accepte.status_code == 303  # la limite exacte passe
