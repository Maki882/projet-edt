"""Écran des paramètres : construit depuis les métadonnées, bornes contrôlées, valeurs par défaut."""

from __future__ import annotations

from edt.db.moteur import session_base
from edt.domaine.parametres import definitions
from edt.services.parametres import lire_parametres


def _saisies(**changements):
    valeurs = {
        d.cle: (
            f"{d.defaut // 60:02d}:{d.defaut % 60:02d}" if d.unite == "heure" else str(d.defaut)
        )
        for d in definitions()
        if d.type is int
    }
    valeurs.update(changements)
    return valeurs


def test_formulaire_construit_depuis_les_metadonnees(client):
    page = client.get("/parametres").text
    for definition in definitions():
        assert definition.libelle.replace("'", "&#39;") in page
    assert 'name="ouverture_min" value="08:30"' in page  # une heure se saisit « 08:30 »
    assert "maximum 3600" in page and "secondes" in page


def test_bornes_refusees_et_signalees_sous_le_champ(client):
    reponse = client.post("/parametres", data=_saisies(temps_limite_s="5000", pause_min="-1"))
    assert reponse.status_code == 422
    assert "La valeur maximale est 3600 (lu : 5000)." in reponse.text
    assert "La valeur minimale est 0 (lu : -1)." in reponse.text
    assert 'value="5000"' in reponse.text  # la saisie est conservée
    with session_base() as session:
        assert lire_parametres(session).temps_limite_s == 60  # rien n'a été enregistré


def test_incoherence_d_ensemble_refusee(client):
    reponse = client.post("/parametres", data=_saisies(ouverture_min="19:00"))
    assert reponse.status_code == 422
    assert "L&#39;heure d&#39;ouverture doit précéder" in reponse.text


def test_enregistrement_puis_retour_aux_valeurs_par_defaut(client):
    reponse = client.post(
        "/parametres",
        data=_saisies(temps_limite_s="120", objectif_actif="1"),
        follow_redirects=True,
    )
    assert "Paramètres enregistrés." in reponse.text
    with session_base() as session:
        parametres = lire_parametres(session)
        assert parametres.temps_limite_s == 120 and parametres.objectif_actif is True

    reponse = client.post("/parametres/defaut", follow_redirects=True)
    assert "valeur par défaut" in reponse.text
    with session_base() as session:
        parametres = lire_parametres(session)
        assert parametres.temps_limite_s == 60 and parametres.objectif_actif is False
