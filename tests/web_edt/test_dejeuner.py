"""Onglet « Générer » : bloc « Pause déjeuner » prérempli, enregistré au lancement, refus expliqué."""

from __future__ import annotations

import logging

from sqlalchemy import func, select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.contraintes import CATALOGUE
from edt.domaine.parametres import Parametres
from edt.services import generation
from edt.services.parametres import lire_parametres

from .conftest import DELAI

COCHEES = [str(code) for code in CATALOGUE if str(code) != "DEJEUNER"]


def _envoyer(client, semestre_id: int, **dejeuner: str):
    donnees = {
        "semestre_id": semestre_id,
        "contraintes": COCHEES,
        "contraintes_envoyees": "1",
        "duree": "court",
        **dejeuner,
    }
    return client.post("/calculs", data=donnees, follow_redirects=False)


def _attendre(reponse) -> generation.SuiviCalcul:
    assert reponse.status_code == 303, reponse.text
    calcul_id = int(reponse.headers["location"].rsplit("/", 1)[1])
    return generation.attendre_calcul(calcul_id, DELAI)


def _parametres() -> Parametres:
    with session_base() as session:
        return lire_parametres(session)


def test_bloc_prerempli_depuis_les_parametres_a_la_place_de_la_case(jeu_minimal, client):
    page = client.get(f"/calculs?semestre_id={jeu_minimal.semestre_id}").text
    assert "<legend>Pause déjeuner</legend>" in page
    assert 'name="dejeuner" value="determines" checked' in page
    assert 'name="dejeuner" value="aucun">' in page
    assert 'name="dejeuner_debut" value="12:45"' in page
    assert 'name="dejeuner_fin" value="13:45"' in page
    assert 'data-visible-si="dejeuner:determines"' in page  # heures masquées avec « aucun »
    assert 'value="DEJEUNER"' not in page  # plus de case « Pause déjeuner » à décocher


def test_pas_d_horaires_determines_enregistre_puis_lance(jeu_minimal, client, caplog):
    with caplog.at_level(logging.INFO, logger="edt.services.parametres"):
        suivi = _attendre(_envoyer(client, jeu_minimal.semestre_id, dejeuner="aucun"))
    assert "Paramètre « dejeuner_horaires_determines » réglé sur False" in caplog.text
    assert suivi.reussi, suivi.message
    assert suivi.contraintes_desactivees == ()  # rien n'est décoché : la règle est inapplicable
    parametres = _parametres()
    assert parametres.plage_dejeuner is None
    assert (parametres.dejeuner_debut_min, parametres.dejeuner_fin_min) == (765, 825)  # gardées

    page = client.get(f"/calculs?semestre_id={jeu_minimal.semestre_id}").text
    assert 'name="dejeuner" value="aucun" checked' in page


def test_nouvelles_heures_enregistrees_avant_le_calcul(jeu_minimal, client):
    reponse = _envoyer(
        client,
        jeu_minimal.semestre_id,
        dejeuner="determines",
        dejeuner_debut="12:30",
        dejeuner_fin="13:30",
    )
    assert _parametres().plage_dejeuner == (750, 810)
    assert _attendre(reponse).reussi


def test_saisie_invalide_reaffichee_sans_lancer_ni_enregistrer(jeu_minimal, client):
    for debut, fin, message in (
        ("13:45", "12:45", "La fin du déjeuner doit venir après son début (13 h 45)."),
        ("07:00", "08:00", "commencer avant l&#39;ouverture (8 h 30)."),
        ("midi", "13:45", "Heure de début du déjeuner illisible (« midi »)"),
        ("12:45", "", "Indiquer l&#39;heure de fin du déjeuner"),
    ):
        reponse = _envoyer(
            client,
            jeu_minimal.semestre_id,
            dejeuner="determines",
            dejeuner_debut=debut,
            dejeuner_fin=fin,
        )
        assert reponse.status_code == 422, (debut, fin)
        assert 'class="erreur-champ"' in reponse.text and message in reponse.text
        assert f'name="dejeuner_debut" value="{debut}"' in reponse.text  # saisie conservée
        assert "Traceback" not in reponse.text
    with session_base() as session:
        assert session.scalar(select(func.count()).select_from(m.Calcul)) == 0
    assert _parametres().plage_dejeuner == (765, 825)  # rien n'a été enregistré
