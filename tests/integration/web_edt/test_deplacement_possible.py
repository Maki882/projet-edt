"""Déplacement d'une séance : une heure annoncée « possible » s'enregistre telle que présentée.

Le formulaire est rechargé à chaque changement du jour ou de l'heure ; HTMX renvoie alors tout
le formulaire, salle déjà sélectionnée comprise. Ces tests rejouent ce rechargement par le
client HTTP, avec le vrai vérificateur, sur la version produite par le moteur.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.services.emploi_du_temps import modifications

HX = {"HX-Request": "true"}


def _option_choisie(fragment: str, nom: str) -> str:
    """Valeur de l'option sélectionnée du `<select name=nom>` d'un fragment."""
    liste = re.search(rf'<select[^>]*name="{nom}"[^>]*>(.*?)</select>', fragment, re.S)
    assert liste is not None, nom
    choisie = re.search(r'<option value="([^"]*)" selected>', liste.group(1))
    assert choisie is not None, nom
    return choisie.group(1)


def _heure_possible_ou_la_salle_actuelle_est_occupee(session, version_id: int):
    """(séance, jour, début, salle actuelle) : à cette heure annoncée « possible », la salle
    actuelle de la séance est occupée par une autre séance de la version."""
    seances = session.scalars(
        select(m.SeanceHebdo)
        .where(m.SeanceHebdo.version_id == version_id, m.SeanceHebdo.salle_id.is_not(None))
        .order_by(m.SeanceHebdo.id)
    ).all()
    for seance in seances:
        for autre in seances:
            if autre.id == seance.id or autre.salle_id != seance.salle_id:
                continue
            if (autre.jour, autre.debut_min) == (seance.jour, seance.debut_min):
                continue
            formulaire = modifications.formulaire_seance(
                session, seance.id, jour=autre.jour, debut=autre.debut_min
            )
            annonces = dict(formulaire.debuts)
            occupee = any(s.id == seance.salle_id and not s.libre for s in formulaire.salles)
            if (
                formulaire.jour == autre.jour
                and "(possible" in annonces.get(autre.debut_min, "")
                and occupee
            ):
                return seance.id, autre.jour, autre.debut_min, seance.salle_id
    return None


@pytest.mark.cas_limite
def test_heure_possible_choisie_puis_enregistree_reussit(version_generee, client):
    """Cas : l'utilisateur choisit une heure « (possible) » ; HTMX recharge le formulaire en
    renvoyant la salle actuelle de la séance, occupée à cette heure par une autre séance.

    Risque : la salle occupée reste sélectionnée et « Vérifier et enregistrer » refuse le
    déplacement (« Une séance par salle »), alors que l'heure était annoncée possible.
    """
    with session_base() as session:
        cas = _heure_possible_ou_la_salle_actuelle_est_occupee(session, version_generee.version_id)
    assert cas is not None, "le jeu minimal doit offrir une salle occupée à une heure possible"
    seance_id, jour, debut, salle_actuelle = cas
    adresse = f"/emploi-du-temps/seances/{seance_id}"
    envoye = {
        "jour": jour,
        "debut": debut,
        "salle_id": salle_actuelle,
        "retour": "/emploi-du-temps",
    }

    fragment = client.get(f"{adresse}/formulaire", params=envoye, headers=HX).text
    salle = _option_choisie(fragment, "salle_id")
    assert salle != str(salle_actuelle)  # une salle libre à cette heure est présélectionnée
    assert _option_choisie(fragment, "debut") == str(debut)

    # Choix explicite de la salle occupée : refusé, avec sa raison.
    refus = client.post(f"{adresse}/modifier", data=envoye, headers=HX)
    assert refus.status_code == 200
    assert "n&#39;a pas été enregistrée" in refus.text and "Une séance par salle" in refus.text

    # Le formulaire tel qu'il se présente après le choix de l'heure : enregistré.
    reponse = client.post(f"{adresse}/modifier", data={**envoye, "salle_id": salle}, headers=HX)
    assert reponse.status_code == 204, reponse.text
    with session_base() as session:
        deplacee = session.get(m.SeanceHebdo, seance_id)
        assert (deplacee.jour, deplacee.debut_min, deplacee.salle_id) == (jour, debut, int(salle))


@pytest.mark.cas_limite
def test_chaque_heure_possible_du_jeu_minimal_s_enregistre(version_generee, client):
    """Cas : pour chaque séance de la version et chaque jour, la première heure annoncée
    « (possible) », choisie puis enregistrée telle que le formulaire se présente.

    Risque : une annonce « possible » démentie à l'enregistrement par le bandeau rouge.
    """
    with session_base() as session:
        seances = session.scalars(
            select(m.SeanceHebdo.id)
            .where(m.SeanceHebdo.version_id == version_generee.version_id)
            .order_by(m.SeanceHebdo.id)
        ).all()
    essais = []
    for seance_id in seances:
        adresse = f"/emploi-du-temps/seances/{seance_id}"
        panneau = client.get(f"{adresse}/panneau", headers=HX).text
        salle = _option_choisie(panneau, "salle_id")
        for jour in range(5):
            envoye = {"jour": jour, "salle_id": salle, "retour": "/emploi-du-temps"}
            fragment = client.get(f"{adresse}/formulaire", params=envoye, headers=HX).text
            heures = re.findall(r'<option value="(\d+)"[^>]*>[^<]*\(possible', fragment)
            if not heures:
                continue
            envoye["debut"] = heures[0]
            choix = client.get(f"{adresse}/formulaire", params=envoye, headers=HX).text
            envoye["salle_id"] = _option_choisie(choix, "salle_id")
            reponse = client.post(f"{adresse}/modifier", data=envoye, headers=HX)
            essais.append((seance_id, jour, heures[0], reponse.status_code))
            break  # la séance a bougé : un essai par séance
    assert essais, "aucune heure possible dans la version"
    refuses = [essai for essai in essais if essai[3] != 204]
    assert not refuses, refuses
