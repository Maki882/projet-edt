"""Formulaire « Déplacer la séance » par le site, avec le vrai vérificateur : heures annoncées
« possible, avec avertissement », et jour sans aucune heure possible.

La version est la version saisie à la main du jeu minimal (`EMPLOI_DU_TEMPS`) : la même sur
toutes les machines, sans dépendre de la solution du moteur.
"""

from __future__ import annotations

import html
import re

import pytest
from tests.outils.verification.versions import enregistrer_version
from tests.outils.web_edt.aides import HX, seance_du_besoin

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.services.parametres import ecrire_parametre

JEUDI, MARDI = 3, 1
DIX_HEURES_QUARANTE_CINQ = 10 * 60 + 45


@pytest.fixture
def version_saisie(jeu_minimal) -> int:
    """Version active du jeu minimal saisie à la main : déterministe."""
    with session_base() as session:
        return enregistrer_version(session, jeu_minimal.semestre_id)


def _cm_de_mathematiques(version_id: int) -> int:
    """Le CM de mathématiques de la promotion A1 (lundi 8 h 30 dans la version saisie)."""
    with session_base() as session:
        return seance_du_besoin(session, version_id, "Mathématiques", "CM", "A1").id


def _options(fragment: str, nom: str) -> dict[str, str]:
    """Valeur → texte des options du `<select name=nom>` d'un fragment."""
    liste = re.search(rf'<select[^>]*name="{nom}"[^>]*>(.*?)</select>', fragment, re.S)
    assert liste is not None, nom
    return {
        valeur: html.unescape(texte).strip()
        for valeur, texte in re.findall(
            r'<option value="([^"]*)"[^>]*>([^<]*)</option>', liste.group(1)
        )
    }


def _salle_choisie(fragment: str) -> str:
    liste = re.search(r'<select[^>]*name="salle_id"[^>]*>(.*?)</select>', fragment, re.S)
    choisie = re.search(r'<option value="([^"]*)" selected>', liste.group(1))
    assert choisie is not None
    return choisie.group(1)


def test_heure_acceptee_avec_un_avertissement_annoncee_et_enregistree(version_saisie, client):
    """L'utilisateur, qui a réglé la pause entre deux séances à une heure, passe le CM de
    mathématiques au jeudi : 10 h 45, juste après le CM d'électronique, est annoncée
    « possible, avec avertissement » ; choisie puis enregistrée, elle est acceptée et le
    message dit l'entorse à une règle importante."""
    with session_base() as session:
        ecrire_parametre(session, "pause_min", 60)
        session.commit()
    seance_id = _cm_de_mathematiques(version_saisie)
    adresse = f"/emploi-du-temps/seances/{seance_id}"
    panneau = client.get(f"{adresse}/panneau", headers=HX).text
    jeudi = client.get(
        f"{adresse}/formulaire",
        params={"jour": JEUDI, "debut": 510, "salle_id": _salle_choisie(panneau)},
        headers=HX,
    ).text
    annonce = _options(jeudi, "debut")[str(DIX_HEURES_QUARANTE_CINQ)]
    assert annonce == "10 h 45 (possible, avec avertissement)"
    assert "« possible, avec avertissement » : accepté, avec une entorse" in html.unescape(jeudi)

    envoye = {"jour": JEUDI, "debut": DIX_HEURES_QUARANTE_CINQ, "salle_id": _salle_choisie(jeudi)}
    choix = client.get(f"{adresse}/formulaire", params=envoye, headers=HX).text
    envoye["salle_id"] = _salle_choisie(choix)
    reponse = client.post(
        f"{adresse}/modifier", data={**envoye, "retour": "/emploi-du-temps"}, headers=HX
    )
    assert reponse.status_code == 204, reponse.text
    page = html.unescape(client.get(reponse.headers["HX-Redirect"]).text)
    assert 'class="message message-attention"' in page
    assert "Séance déplacée au jeudi à 10 h 45" in page
    assert "Entorse à une règle importante" in page
    with session_base() as session:
        deplacee = session.get(m.SeanceHebdo, seance_id)
        assert (deplacee.jour, deplacee.debut_min) == (JEUDI, DIX_HEURES_QUARANTE_CINQ)


def test_jour_sans_aucune_heure_possible_dit_quoi_faire(version_saisie, client):
    """L'utilisateur choisit le mardi pour le CM de mathématiques de la promotion : toutes les
    heures sont refusées, et le formulaire dit de choisir un autre jour en nommant la règle la
    plus souvent en cause ; enregistrer l'une d'elles est refusé pour cette raison."""
    seance_id = _cm_de_mathematiques(version_saisie)
    adresse = f"/emploi-du-temps/seances/{seance_id}"
    panneau = client.get(f"{adresse}/panneau", headers=HX).text
    mardi = client.get(
        f"{adresse}/formulaire",
        params={"jour": MARDI, "debut": 510, "salle_id": _salle_choisie(panneau)},
        headers=HX,
    ).text
    annonces = _options(mardi, "debut")
    assert annonces and all(texte.endswith("(refusé)") for texte in annonces.values())
    assert (
        "Aucune heure possible le mardi avec les salles proposées : essayer un autre jour "
        "(règle la plus souvent en cause : Une séance par groupe)."
    ) in html.unescape(mardi)

    debut = next(iter(annonces))
    refus = client.post(
        f"{adresse}/modifier",
        data={"jour": MARDI, "debut": debut, "salle_id": _salle_choisie(mardi), "retour": "/"},
        headers=HX,
    )
    assert refus.status_code == 200
    assert "n&#39;a pas été enregistrée" in refus.text and "Une séance par groupe" in refus.text
