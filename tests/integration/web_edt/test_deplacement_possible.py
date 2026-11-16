"""Déplacement d'une séance : une heure annoncée « possible » s'enregistre telle que présentée.

Le formulaire est rechargé à chaque changement du jour ou de l'heure ; HTMX renvoie alors tout
le formulaire, salle déjà sélectionnée comprise. Ces tests rejouent ce rechargement par le
client HTTP, avec le vrai vérificateur. Le premier part de la version saisie à la main du jeu
minimal, la même sur toutes les machines : une version produite par le moteur varie d'un
système à l'autre et n'offre pas toujours le cas cherché. Le second balaie la version produite
par le moteur, quelle qu'elle soit.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select
from tests.outils.verification.versions import enregistrer_version

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import CibleIndisponibilite
from edt.services.emploi_du_temps import modifications

HX = {"HX-Request": "true"}


@pytest.fixture
def version_saisie(jeu_minimal) -> int:
    """Version active du jeu minimal saisie à la main (`EMPLOI_DU_TEMPS`) : déterministe."""
    with session_base() as session:
        return enregistrer_version(session, jeu_minimal.semestre_id)


def _option_choisie(fragment: str, nom: str) -> str:
    """Valeur de l'option sélectionnée du `<select name=nom>` d'un fragment."""
    liste = re.search(rf'<select[^>]*name="{nom}"[^>]*>(.*?)</select>', fragment, re.S)
    assert liste is not None, nom
    choisie = re.search(r'<option value="([^"]*)" selected>', liste.group(1))
    assert choisie is not None, nom
    return choisie.group(1)


def _annonce(session, seance_id: int, jour: int, debut: int) -> tuple[bool, bool]:
    """(heure annoncée « possible », salle actuelle occupée) pour la séance à ce jour et cette
    heure."""
    seance = session.get(m.SeanceHebdo, seance_id)
    formulaire = modifications.formulaire_seance(session, seance_id, jour=jour, debut=debut)
    possible = formulaire.jour == jour and "(possible" in dict(formulaire.debuts).get(debut, "")
    occupee = any(s.id == seance.salle_id and not s.libre for s in formulaire.salles)
    return possible, occupee


def _occuper_la_salle_actuelle_a_une_heure_possible(session, version_id: int):
    """Construit le cas dans la version saisie : (séance, jour, début, salle actuelle).

    Cherche une séance et une autre séance, placée dans une autre salle à une heure où la
    première est annoncée « possible », puis loge la seconde dans la salle de la première. À
    cette heure, la salle actuelle de la première est alors occupée, et l'heure reste
    « possible » avec une autre salle. Rien ne dépend du moteur : le cas est le même partout.
    """
    seances = session.scalars(
        select(m.SeanceHebdo)
        .where(m.SeanceHebdo.version_id == version_id, m.SeanceHebdo.salle_id.is_not(None))
        .order_by(m.SeanceHebdo.id)
    ).all()
    for seance in seances:
        for autre in seances:
            if autre.id == seance.id or autre.salle_id == seance.salle_id:
                continue
            if (autre.jour, autre.debut_min) == (seance.jour, seance.debut_min):
                continue
            if not _annonce(session, seance.id, autre.jour, autre.debut_min)[0]:
                continue
            salle_d_origine = autre.salle_id
            autre.salle_id = seance.salle_id
            session.flush()
            if _annonce(session, seance.id, autre.jour, autre.debut_min) == (True, True):
                session.commit()
                return seance.id, autre.jour, autre.debut_min, seance.salle_id
            autre.salle_id = salle_d_origine
            session.flush()
    return None


@pytest.mark.cas_limite
def test_heure_possible_choisie_puis_enregistree_reussit(version_saisie, client):
    """Cas : l'utilisateur choisit une heure « (possible) » ; HTMX recharge le formulaire en
    renvoyant la salle actuelle de la séance, occupée à cette heure par une autre séance.

    Risque : la salle occupée reste sélectionnée et « Vérifier et enregistrer » refuse le
    déplacement (« Une séance par salle »), alors que l'heure était annoncée possible.
    """
    with session_base() as session:
        cas = _occuper_la_salle_actuelle_a_une_heure_possible(session, version_saisie)
    assert cas is not None, "la version saisie doit permettre de construire le cas"
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


def _parcours_changer_le_jour_puis_l_heure(client, session, seance_id: int) -> list[tuple]:
    """Rejoue le navigateur sur chaque jour : ouverture du panneau, changement du jour (heure et
    salle affichées renvoyées), puis chaque heure annoncée « (possible… » choisie en renvoyant
    la salle présélectionnée. Renvoie les heures dont la vérification, avec la salle du
    formulaire rechargé, refuse le déplacement (rien n'est enregistré)."""
    from edt.domaine.instantane import id_seance
    from edt.services.emploi_du_temps import raccords

    seance = session.get(m.SeanceHebdo, seance_id)
    identifiant, duree = id_seance(seance.besoin_id, seance.rang), seance.fin_min - seance.debut_min
    adresse = f"/emploi-du-temps/seances/{seance_id}/formulaire"
    panneau = client.get(f"/emploi-du-temps/seances/{seance_id}/panneau", headers=HX).text
    affiche = {
        "debut": _option_choisie(panneau, "debut"),
        "salle_id": _option_choisie(panneau, "salle_id"),
    }
    refusees = []
    for jour in range(5):
        jour_change = client.get(adresse, params={**affiche, "jour": jour}, headers=HX).text
        if _option_choisie(jour_change, "jour") != str(jour):
            continue  # jour non proposé : le formulaire garde le jour actuel
        salle = _option_choisie(jour_change, "salle_id")
        for debut in re.findall(r'<option value="(\d+)"[^>]*>[^<]*\(possible', jour_change):
            choix = client.get(
                adresse, params={"jour": jour, "debut": debut, "salle_id": salle}, headers=HX
            ).text
            salle_finale = _option_choisie(choix, "salle_id")
            verification = raccords.verifier_modification(
                session,
                seance.version_id,
                identifiant,
                jour,
                int(debut),
                int(debut) + duree,
                int(salle_finale) if salle_finale else None,
            )
            if not verification.valide:
                refusees.append((seance_id, jour, debut, salle, salle_finale))
    return refusees


def _salle_presentee_indisponible_a_une_heure(session, version_id: int):
    """Construit le cas dans la version saisie : (séance, jour, début, salle actuelle, heure).

    Une autre séance est logée dans la salle actuelle R de la séance, un autre jour J, à l'heure
    actuelle D de la séance : en passant au jour J, le formulaire reçoit R (occupée) et
    présélectionne une autre salle X. X est ensuite rendue indisponible le jour J à une heure H :
    à cette heure, le déplacement passe avec R mais pas avec X, la salle que le navigateur
    renverra. Rien ne dépend du moteur : le cas est le même partout.
    """
    seances = session.scalars(
        select(m.SeanceHebdo)
        .where(m.SeanceHebdo.version_id == version_id, m.SeanceHebdo.salle_id.is_not(None))
        .order_by(m.SeanceHebdo.id)
    ).all()
    for seance in seances:
        duree = seance.fin_min - seance.debut_min
        for jour in (j for j in range(5) if j != seance.jour):
            for autre in seances:
                if autre.id == seance.id or not set(autre.semaines or ()) & set(
                    seance.semaines or ()
                ):
                    continue
                avant = (autre.jour, autre.debut_min, autre.fin_min, autre.salle_id)
                autre.jour, autre.salle_id = jour, seance.salle_id
                autre.debut_min = seance.debut_min
                autre.fin_min = seance.debut_min + (avant[2] - avant[1])
                session.flush()
                formulaire = modifications.formulaire_seance(
                    session, seance.id, jour=jour, debut=seance.debut_min, salle_id=seance.salle_id
                )
                heures = [
                    d
                    for d, texte in formulaire.debuts
                    if d != seance.debut_min and "(possible" in texte
                ]
                if (
                    formulaire.jour == jour
                    and formulaire.salle_id not in (None, seance.salle_id)
                    and heures
                ):
                    session.add(
                        m.Indisponibilite(
                            cible=CibleIndisponibilite.SALLE,
                            salle_id=formulaire.salle_id,
                            jour=jour,
                            debut_min=heures[0],
                            fin_min=heures[0] + duree,
                            motif="Salle réservée",
                        )
                    )
                    session.commit()
                    return seance.id, jour, seance.debut_min, seance.salle_id, heures[0]
                autre.jour, autre.debut_min, autre.fin_min, autre.salle_id = avant
                session.flush()
    return None


@pytest.mark.cas_limite
def test_changer_le_jour_puis_choisir_une_heure_possible(version_saisie, client):
    """Cas : l'utilisateur change le jour ; le navigateur renvoie l'heure et la salle affichées,
    occupée ce jour-là, et le formulaire présélectionne une autre salle. Il choisit ensuite une
    heure « (possible) » en renvoyant cette autre salle.

    Risque : l'heure a été annoncée avec la salle reçue, pas avec la salle présélectionnée que
    le navigateur renverra : l'enregistrement est refusé (trajet entre campus, salle
    indisponible).
    """
    with session_base() as session:
        cas = _salle_presentee_indisponible_a_une_heure(session, version_saisie)
    assert cas is not None, "la version saisie doit offrir le cas"
    seance_id, jour, debut, salle_actuelle, heure = cas
    adresse = f"/emploi-du-temps/seances/{seance_id}/formulaire"
    jour_change = client.get(
        adresse, params={"jour": jour, "debut": debut, "salle_id": salle_actuelle}, headers=HX
    ).text
    assert _option_choisie(jour_change, "salle_id") != str(salle_actuelle)
    annonce = re.search(rf'<option value="{heure}"[^>]*>([^<]*)</option>', jour_change)
    assert annonce is not None and "(refusé)" in annonce.group(1), annonce
    with session_base() as session:
        refusees = _parcours_changer_le_jour_puis_l_heure(client, session, seance_id)
    assert not refusees, refusees


def test_balayage_changer_le_jour_puis_l_heure_de_la_version_du_moteur(version_generee, client):
    """Même parcours du navigateur sur la version produite par le moteur, quelle qu'elle soit."""
    with session_base() as session:
        seances = session.scalars(
            select(m.SeanceHebdo.id).where(m.SeanceHebdo.version_id == version_generee.version_id)
        ).all()
        refusees = [
            r for s in seances for r in _parcours_changer_le_jour_puis_l_heure(client, session, s)
        ]
    assert not refusees, refusees
