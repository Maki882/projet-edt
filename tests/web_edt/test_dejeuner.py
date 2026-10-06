"""Onglet « Générer » : pause déjeuner à trois modes, préremplie, enregistrée au lancement.

Horaires déterminés (plage réservée), pause libre bornée (au moins N minutes
d'un seul tenant dans une fenêtre) ou pas de pause. Le moteur et le
vérificateur du mode libre sont livrés à part : ici, un calcul en mode libre
doit seulement réussir, la pause elle-même n'est pas contrôlée.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.parametres import DEJEUNER_AUCUN, DEJEUNER_FIXE, DEJEUNER_LIBRE, Parametres
from edt.services import generation
from edt.services.parametres import ecrire_parametres, lire_parametres

from .conftest import DELAI

H = 60
PLAGE_PAR_DEFAUT = (12 * H + 45, 13 * H + 45)


def _envoyer(client, semestre_id: int, **dejeuner: str):
    donnees = {"semestre_id": semestre_id, "duree": "court", **dejeuner}
    return client.post("/calculs", data=donnees, follow_redirects=False)


def _attendre(reponse) -> generation.SuiviCalcul:
    assert reponse.status_code == 303, reponse.text
    calcul_id = int(reponse.headers["location"].rsplit("/", 1)[1])
    return generation.attendre_calcul(calcul_id, DELAI)


def _parametres() -> Parametres:
    with session_base() as session:
        return lire_parametres(session)


def _regler(**valeurs) -> None:
    with session_base() as session:
        ecrire_parametres(session, valeurs)


def _nb_calculs() -> int:
    with session_base() as session:
        return session.scalar(select(func.count()).select_from(m.Calcul))


def test_trois_choix_preremplis_depuis_les_parametres(jeu_minimal, client):
    adresse = f"/calculs?semestre_id={jeu_minimal.semestre_id}"
    page = client.get(adresse).text

    assert "<legend>Pause déjeuner</legend>" in page
    assert 'name="dejeuner" value="fixe" checked> Horaires déterminés</label>' in page
    assert 'name="dejeuner" value="libre"> Pause libre bornée</label>' in page
    assert 'name="dejeuner" value="aucun"> Pas de pause</label>' in page
    assert 'name="dejeuner_debut" value="12:45"' in page
    assert 'name="dejeuner_fin" value="13:45"' in page
    assert 'name="dejeuner_duree" value="60"' in page
    # Heures visibles en horaires déterminés et en pause libre, durée en pause libre seulement.
    assert 'data-visible-si="dejeuner:fixe,libre"' in page
    assert 'data-visible-si="dejeuner:libre"' in page
    # La règle garde son niveau, avec la note adaptée aux trois modes.
    assert 'name="niveau_DEJEUNER"' in page
    assert "Ne s&#39;applique pas sans pause déjeuner" in page

    _regler(dejeuner_mode=DEJEUNER_LIBRE, dejeuner_debut_min=12 * H, dejeuner_fin_min=14 * H)
    _regler(dejeuner_duree_min=45)
    libre = client.get(adresse).text
    assert 'name="dejeuner" value="libre" checked' in libre
    assert 'name="dejeuner_debut" value="12:00"' in libre
    assert 'name="dejeuner_duree" value="45"' in libre

    _regler(dejeuner_mode=DEJEUNER_AUCUN)
    assert 'name="dejeuner" value="aucun" checked' in client.get(adresse).text


def test_pause_libre_enregistree_avant_le_calcul(jeu_minimal, client):
    reponse = _envoyer(
        client,
        jeu_minimal.semestre_id,
        dejeuner="libre",
        dejeuner_debut="12:00",
        dejeuner_fin="14:00",
        dejeuner_duree="75",
    )

    parametres = _parametres()
    assert parametres.dejeuner_mode == DEJEUNER_LIBRE
    assert parametres.fenetre_dejeuner == (12 * H, 14 * H, 75)
    assert parametres.plage_dejeuner is None
    # Le calcul réussit ; la pause libre elle-même relève du moteur et du vérificateur.
    assert _attendre(reponse).reussi


def test_pause_libre_mal_saisie_refusee_sous_le_champ(jeu_minimal, client):
    for debut, fin, duree, champ, message in (
        (
            "12:30",
            "13:30",
            "75",
            "dejeuner_duree",
            "Une pause de 75 min ne tient pas dans la fenêtre du déjeuner",
        ),
        ("12:00", "14:00", "", "dejeuner_duree", "Indiquer la durée minimale de la pause"),
        ("12:00", "14:00", "une heure", "dejeuner_duree", "Durée minimale illisible"),
        ("12:00", "14:00", "10", "dejeuner_duree", "Durée minimale de 10 min hors bornes"),
        ("14:00", "12:00", "60", "dejeuner_fin", "La fin du déjeuner doit venir après son début"),
    ):
        reponse = _envoyer(
            client,
            jeu_minimal.semestre_id,
            dejeuner="libre",
            dejeuner_debut=debut,
            dejeuner_fin=fin,
            dejeuner_duree=duree,
        )
        assert reponse.status_code == 422, (debut, fin, duree)
        assert f'<p class="erreur-champ" id="erreur-{champ}">' in reponse.text
        assert message in reponse.text
        assert 'name="dejeuner" value="libre" checked' in reponse.text  # saisie conservée
        assert f'name="dejeuner_duree" value="{duree}"' in reponse.text
        assert "Traceback" not in reponse.text
    assert _nb_calculs() == 0
    parametres = _parametres()  # rien n'a été enregistré
    assert parametres.dejeuner_mode == DEJEUNER_FIXE
    assert parametres.plage_dejeuner == PLAGE_PAR_DEFAUT


def test_pas_de_pause_enregistre_puis_lance(jeu_minimal, client, caplog):
    with caplog.at_level(logging.INFO, logger="edt.services.parametres"):
        suivi = _attendre(_envoyer(client, jeu_minimal.semestre_id, dejeuner="aucun"))
    assert "Paramètre « dejeuner_mode » réglé sur 'aucun'" in caplog.text
    assert suivi.reussi, suivi.message
    assert suivi.contraintes_desactivees == ()  # rien n'est ignoré : la règle est inapplicable
    parametres = _parametres()
    assert not parametres.dejeuner_actif and parametres.plage_dejeuner is None
    assert (parametres.dejeuner_debut_min, parametres.dejeuner_fin_min) == PLAGE_PAR_DEFAUT

    page = client.get(f"/calculs?semestre_id={jeu_minimal.semestre_id}").text
    assert 'name="dejeuner" value="aucun" checked' in page


def test_horaires_determines_enregistres_avant_le_calcul(jeu_minimal, client):
    reponse = _envoyer(
        client,
        jeu_minimal.semestre_id,
        dejeuner="fixe",
        dejeuner_debut="12:30",
        dejeuner_fin="13:30",
    )
    assert _parametres().plage_dejeuner == (750, 810)
    assert _parametres().dejeuner_duree_min == 60  # durée gardée, sans objet ici
    assert _attendre(reponse).reussi


def test_horaires_determines_mal_saisis_reaffiches_sans_lancer_ni_enregistrer(jeu_minimal, client):
    for debut, fin, message in (
        ("13:45", "12:45", "La fin du déjeuner doit venir après son début (13 h 45)."),
        ("07:00", "08:00", "commencer avant l&#39;ouverture (8 h 30)."),
        ("midi", "13:45", "Heure de début du déjeuner illisible (« midi »)"),
        ("12:45", "", "Indiquer l&#39;heure de fin du déjeuner"),
    ):
        reponse = _envoyer(
            client,
            jeu_minimal.semestre_id,
            dejeuner="fixe",
            dejeuner_debut=debut,
            dejeuner_fin=fin,
        )
        assert reponse.status_code == 422, (debut, fin)
        assert 'class="erreur-champ"' in reponse.text and message in reponse.text
        assert f'name="dejeuner_debut" value="{debut}"' in reponse.text  # saisie conservée
        assert "Traceback" not in reponse.text
    assert _nb_calculs() == 0
    assert _parametres().plage_dejeuner == PLAGE_PAR_DEFAUT  # rien n'a été enregistré


def test_mode_inconnu_refuse(jeu_minimal, client):
    reponse = _envoyer(client, jeu_minimal.semestre_id, dejeuner="determines")
    assert reponse.status_code == 422
    assert "Choisir « Horaires déterminés » ou « Pause libre bornée » ou « Pas de pause »" in (
        reponse.text
    )
    assert _nb_calculs() == 0


def test_la_grille_dessine_la_bande_selon_le_mode(version_generee, client):
    adresse = f"/emploi-du-temps?version_id={version_generee.version_id}"

    fixe = client.get(adresse).text
    assert '<div class="edt-dejeuner" style=' in fixe
    assert 'title="Déjeuner : 12 h 45 - 13 h 45, libre pour tous"><span>Déjeuner</span>' in fixe

    _regler(dejeuner_mode=DEJEUNER_LIBRE, dejeuner_debut_min=12 * H, dejeuner_fin_min=14 * H)
    libre = client.get(adresse).text
    assert '<div class="edt-dejeuner edt-dejeuner-libre" style=' in libre
    assert 'title="Pause libre : au moins 60 min dans cette fenêtre"' in libre

    _regler(dejeuner_mode=DEJEUNER_AUCUN)
    assert 'class="edt-dejeuner' not in client.get(adresse).text
