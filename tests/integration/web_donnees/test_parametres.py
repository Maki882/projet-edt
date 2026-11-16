"""Écran des paramètres : construit depuis les métadonnées, bornes contrôlées, valeurs par défaut."""

from __future__ import annotations

import re

import pytest

from edt.db.modeles import Parametre
from edt.db.moteur import session_base
from edt.domaine.parametres import (
    DEJEUNER_FIXE,
    DEJEUNER_LIBRE,
    JOURS_TRAVAILLES_PAR_DEFAUT,
    MODES_DEJEUNER,
    Parametres,
    definitions,
)
from edt.services.parametres import anomalies_parametres, lire_parametres

MAXIMUM_TEMPS_LIMITE = next(d.maximum for d in definitions() if d.cle == "temps_limite_s")


def _saisies(**changements):
    valeurs = {
        d.cle: (
            f"{d.defaut // 60:02d}:{d.defaut % 60:02d}" if d.unite == "heure" else str(d.defaut)
        )
        for d in definitions()
        if d.type is int
    }
    # Comme un navigateur : les cases cochées des jours travaillés (lundi à vendredi).
    valeurs["jours_travailles"] = [str(jour) for jour in JOURS_TRAVAILLES_PAR_DEFAUT]
    valeurs.update(changements)
    return valeurs


def test_formulaire_construit_depuis_les_metadonnees(client):
    page = client.get("/parametres").text
    for definition in definitions():
        assert definition.libelle.replace("'", "&#39;") in page
    assert 'name="ouverture_min" value="08:30"' in page  # une heure se saisit « 08:30 »
    assert f"maximum {MAXIMUM_TEMPS_LIMITE}" in page and "secondes" in page


def test_pause_dejeuner_liste_deroulante_et_champs_selon_le_mode(client):
    page = client.get("/parametres").text
    assert '<select id="champ-dejeuner_mode" name="dejeuner_mode" required' in page
    for valeur, libelle in MODES_DEJEUNER:
        selection = " selected" if valeur == DEJEUNER_FIXE else ""
        assert f'<option value="{valeur}"{selection}>{libelle}</option>' in page
    assert "Par défaut : Horaires déterminés." in page
    debut = re.search(r'<div class="champ"[^>]*>\s*<label for="champ-dejeuner_debut_min"', page)
    assert 'data-visible-si="dejeuner_mode:fixe,libre"' in debut.group(0)
    duree = re.search(r'<div class="champ"[^>]*>\s*<label for="champ-dejeuner_duree_min"', page)
    assert 'data-visible-si="dejeuner_mode:libre"' in duree.group(0)


def test_pause_libre_enregistree_et_valeur_hors_choix_refusee(client):
    reponse = client.post(
        "/parametres",
        data=_saisies(dejeuner_mode="libre", dejeuner_fin_min="14:00", dejeuner_duree_min="75"),
        follow_redirects=True,
    )
    assert "Paramètres enregistrés." in reponse.text
    with session_base() as session:
        parametres = lire_parametres(session)
    assert parametres.dejeuner_mode == DEJEUNER_LIBRE and parametres.dejeuner_duree_min == 75
    assert parametres.fenetre_dejeuner == (12 * 60 + 45, 14 * 60, 75)
    page = client.get("/parametres").text
    assert '<option value="libre" selected>Pause libre bornée</option>' in page

    refus = client.post("/parametres", data=_saisies(dejeuner_mode="midi"))
    assert refus.status_code == 422
    assert re.search(r'id="erreur-dejeuner_mode">Valeur &#39;midi&#39; inconnue', refus.text)
    with session_base() as session:
        assert lire_parametres(session).dejeuner_mode == DEJEUNER_LIBRE  # rien n'a changé


def test_pause_libre_plus_longue_que_sa_fenetre_signalee_sous_le_champ(client):
    reponse = client.post(
        "/parametres", data=_saisies(dejeuner_mode="libre", dejeuner_duree_min="90")
    )
    assert reponse.status_code == 422
    message = re.search(r'id="erreur-dejeuner_duree_min">([^<]+)<', reponse.text).group(1)
    assert message.startswith("Une pause de 90 min ne tient pas dans la fenêtre du déjeuner")
    with session_base() as session:
        assert lire_parametres(session) == Parametres()


def test_bornes_refusees_et_signalees_sous_le_champ(client):
    reponse = client.post("/parametres", data=_saisies(temps_limite_s="5000", pause_min="-1"))
    assert reponse.status_code == 422
    assert f"La valeur maximale est {MAXIMUM_TEMPS_LIMITE} (lu : 5000)." in reponse.text
    assert "La valeur minimale est 0 (lu : -1)." in reponse.text
    assert 'value="5000"' in reponse.text  # la saisie est conservée
    with session_base() as session:
        assert lire_parametres(session) == Parametres()  # rien n'a été enregistré


def test_incoherence_d_ensemble_refusee(client):
    reponse = client.post("/parametres", data=_saisies(ouverture_min="19:00"))
    assert reponse.status_code == 422
    assert "L&#39;heure d&#39;ouverture doit précéder" in reponse.text


def test_enregistrement_puis_retour_aux_valeurs_par_defaut(client):
    reponse = client.post(
        "/parametres",
        data=_saisies(temps_limite_s="120"),  # case « objectif » décochée : absente de l'envoi
        follow_redirects=True,
    )
    assert "Paramètres enregistrés." in reponse.text
    with session_base() as session:
        parametres = lire_parametres(session)
        assert parametres.temps_limite_s == 120 and parametres.objectif_actif is False

    reponse = client.post("/parametres/defaut", follow_redirects=True)
    assert "valeur par défaut" in reponse.text
    with session_base() as session:
        parametres = lire_parametres(session)
        assert parametres == Parametres()
        assert parametres.temps_limite_s != 120 and parametres.objectif_actif is True


@pytest.mark.cas_limite
def test_parametres_refuses_pendant_un_calcul(site, calcul_retenu):
    """Cas : pendant un calcul, on enregistre les paramètres ou on les remet à leur valeur par
    défaut. Risque : le calcul, qui relit les paramètres pour contrôler et dater sa solution,
    produit une version vérifiée avec d'autres réglages (anomalies inexpliquées dans À traiter),
    alors que Générer refuse déjà de changer la pause déjeuner pendant un calcul."""
    with session_base() as session:
        avant = lire_parametres(session)
    page = site.get("/parametres")
    assert f"calcul n° {calcul_retenu.id}" in page.text
    assert f'href="/calculs/{calcul_retenu.id}"' in page.text

    remise = site.post("/parametres/defaut")
    assert remise.status_code == 409 and "Rien n&#39;a été enregistré" in remise.text
    envoi = site.post("/parametres", data=_saisies(temps_limite_s="120"))
    assert envoi.status_code == 409 and "calcul d&#39;emploi du temps est en cours" in envoi.text
    assert 'name="temps_limite_s" value="120"' in envoi.text  # la saisie est conservée
    with session_base() as session:
        assert lire_parametres(session) == avant

    assert calcul_retenu.liberer().reussi
    apres = site.post("/parametres", data=_saisies(temps_limite_s="120"), follow_redirects=True)
    assert "Paramètres enregistrés." in apres.text
    with session_base() as session:
        assert lire_parametres(session).temps_limite_s == 120


@pytest.mark.cas_limite
def test_valeur_corrompue_signalee_puis_reparee_depuis_l_ecran(site):
    """Cas : une valeur de paramètre corrompue en base (« quinze » pour la pause entre deux
    séances), puis l'utilisateur ouvre /parametres et enregistre. Risque : l'écran, seul moyen
    de la corriger, ne s'ouvre plus (422), ne dit pas quelle valeur est en cause, ou refuse
    l'enregistrement qui la répare."""
    with session_base() as session:
        session.get(Parametre, "pause_min").valeur = "quinze"
    page = site.get("/parametres")
    assert page.status_code == 200
    erreur = re.search(r'id="erreur-pause_min">([^<]+)<', page.text).group(1)
    assert erreur.startswith("Valeur enregistrée invalide (un nombre entier est attendu")
    assert "1 paramètre enregistré est invalide" in page.text
    defaut = next(d.defaut for d in definitions() if d.cle == "pause_min")
    assert f'name="pause_min" value="{defaut}"' in page.text  # la valeur par défaut est proposée

    repare = site.post("/parametres", data=_saisies(), follow_redirects=True)
    assert "Paramètres enregistrés." in repare.text
    with session_base() as session:
        assert anomalies_parametres(session) == {}
        assert lire_parametres(session).pause_min == defaut
    assert 'id="erreur-pause_min"' not in site.get("/parametres").text
