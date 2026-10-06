"""Pause déjeuner : trois modes, validation, propriétés, lecture d'une ancienne base."""

from __future__ import annotations

import logging
import re

import pytest

from edt.db.modeles import Parametre
from edt.domaine.parametres import (
    DEJEUNER_AUCUN,
    DEJEUNER_FIXE,
    DEJEUNER_LIBRE,
    TYPE_CHOIX,
    Parametres,
    convertir_texte,
    defauts_dejeuner,
    definition,
    valider,
)
from edt.erreurs import ErreurDonneesInvalides
from edt.services.parametres import ecrire_parametre, lire_parametres

H = 60


def test_par_defaut_horaires_determines():
    parametres = Parametres()
    assert parametres.dejeuner_mode == DEJEUNER_FIXE
    assert parametres.dejeuner_actif is True
    assert parametres.plage_dejeuner == (12 * H + 45, 13 * H + 45)
    assert parametres.fenetre_dejeuner is None
    assert parametres.dejeuner_duree_min == 60


def test_horaires_determines_exigent_une_plage_non_vide_dans_l_ouverture():
    parametres = Parametres()
    for changements, message in (
        ({"dejeuner_fin_min": 12 * H}, "La fin du déjeuner doit venir après son début (12 h 45)."),
        ({"dejeuner_fin_min": 12 * H + 45}, "La fin du déjeuner doit venir après son début"),
        ({"dejeuner_debut_min": 8 * H}, "ne peut pas commencer avant l'ouverture (8 h 30)."),
        ({"dejeuner_fin_min": 19 * H}, "ne peut pas finir après la fermeture (18 h 00)."),
    ):
        with pytest.raises(ErreurDonneesInvalides, match=re.escape(message)):
            parametres.avec(**changements)
    # La durée minimale ne joue pas en horaires déterminés.
    assert parametres.avec(dejeuner_duree_min=180).plage_dejeuner == (765, 825)


def test_pause_libre_bornee_fenetre_et_duree():
    parametres = Parametres().avec(
        dejeuner_mode=DEJEUNER_LIBRE,
        dejeuner_debut_min=12 * H,
        dejeuner_fin_min=14 * H,
        dejeuner_duree_min=45,
    )
    assert parametres.dejeuner_actif is True
    assert parametres.plage_dejeuner is None
    assert parametres.fenetre_dejeuner == (12 * H, 14 * H, 45)
    # Une pause qui remplit exactement la fenêtre est permise.
    assert parametres.avec(dejeuner_duree_min=120).fenetre_dejeuner == (720, 840, 120)

    for changements, message in (
        (
            {"dejeuner_duree_min": 150},
            "Une pause de 150 min ne tient pas dans la fenêtre du déjeuner (12 h 00 - 14 h 00, "
            "soit 120 min).",
        ),
        ({"dejeuner_debut_min": 8 * H}, "ne peut pas commencer avant l'ouverture (8 h 30)."),
        ({"dejeuner_fin_min": 19 * H}, "ne peut pas finir après la fermeture (18 h 00)."),
        ({"dejeuner_fin_min": 11 * H}, "La fin du déjeuner doit venir après son début (12 h 00)."),
    ):
        with pytest.raises(ErreurDonneesInvalides, match=re.escape(message)):
            parametres.avec(**changements)
    for hors_bornes in (14, 181):
        with pytest.raises(ErreurDonneesInvalides, match="Durée minimale de la pause libre"):
            parametres.avec(dejeuner_duree_min=hors_bornes)


def test_sans_pause_les_heures_sont_gardees_mais_ignorees():
    parametres = Parametres().avec(
        dejeuner_mode=DEJEUNER_AUCUN,
        dejeuner_debut_min=13 * H,
        dejeuner_fin_min=12 * H,
        dejeuner_duree_min=180,
    )
    assert parametres.dejeuner_actif is False
    assert parametres.plage_dejeuner is None
    assert parametres.fenetre_dejeuner is None
    assert (parametres.dejeuner_debut_min, parametres.dejeuner_fin_min) == (780, 720)


def test_defauts_par_champ_selon_le_mode():
    assert defauts_dejeuner(765, 825, 510, 1080) == {}
    assert defauts_dejeuner(765, 825, 510, 1080, mode=DEJEUNER_LIBRE, duree=60) == {}
    assert set(defauts_dejeuner(765, 825, 510, 1080, mode=DEJEUNER_LIBRE, duree=90)) == {
        "dejeuner_duree_min"
    }
    assert set(defauts_dejeuner(500, 1100, 510, 1080)) == {"dejeuner_debut_min", "dejeuner_fin_min"}
    assert defauts_dejeuner(800, 700, 510, 1080, mode=DEJEUNER_LIBRE, duree=60) == {
        "dejeuner_fin_min": "La fin du déjeuner doit venir après son début (13 h 20)."
    }
    assert defauts_dejeuner(800, 700, 510, 1080, mode=DEJEUNER_AUCUN, duree=999) == {}


def test_le_mode_est_un_parametre_a_choix():
    entree = definition("dejeuner_mode")
    assert entree.type == TYPE_CHOIX
    assert [valeur for valeur, _ in entree.choix] == ["fixe", "libre", "aucun"]
    assert [libelle for _, libelle in entree.choix] == [
        "Horaires déterminés",
        "Pause libre bornée",
        "Pas de pause",
    ]
    assert definition("pause_min").type is int and definition("pause_min").choix == ()

    assert valider("dejeuner_mode", "libre") == "libre"
    assert convertir_texte("dejeuner_mode", " libre ") == "libre"
    for mauvais in ("midi", "", "Fixe", 1, True):
        with pytest.raises(ErreurDonneesInvalides, match="choisir parmi"):
            valider("dejeuner_mode", mauvais)
    with pytest.raises(ErreurDonneesInvalides, match=re.escape("Pas de pause (« aucun »)")):
        convertir_texte("dejeuner_mode", "souple")
    with pytest.raises(ErreurDonneesInvalides, match="choisir parmi"):
        Parametres().avec(dejeuner_mode="midi")


def test_le_mode_est_enregistre_et_relu_en_base(session):
    ecrire_parametre(session, "dejeuner_mode", DEJEUNER_LIBRE)
    ecrire_parametre(session, "dejeuner_duree_min", 45)
    session.commit()

    assert session.get(Parametre, "dejeuner_mode").valeur == "libre"  # une chaîne en JSON
    relu = lire_parametres(session)
    assert relu.fenetre_dejeuner == (765, 825, 45) and relu.plage_dejeuner is None

    with pytest.raises(ErreurDonneesInvalides):  # incohérence : la pause déborde de la fenêtre
        ecrire_parametre(session, "dejeuner_duree_min", 90)


def _ancienne_base(session, horaires_determines: bool | None) -> None:
    """Base d'avant `dejeuner_mode` : l'ancien booléen, ou rien du tout."""
    session.delete(session.get(Parametre, "dejeuner_mode"))
    session.delete(session.get(Parametre, "dejeuner_duree_min"))
    if horaires_determines is not None:
        session.add(Parametre(cle="dejeuner_horaires_determines", valeur=horaires_determines))
    session.flush()


@pytest.mark.parametrize(
    ("ancien", "mode_attendu"), [(True, DEJEUNER_FIXE), (False, DEJEUNER_AUCUN)]
)
def test_ancien_booleen_lu_comme_mode_sans_modifier_la_base(session, caplog, ancien, mode_attendu):
    _ancienne_base(session, ancien)
    with caplog.at_level(logging.INFO, logger="edt.services.parametres"):
        parametres = lire_parametres(session)

    assert parametres.dejeuner_mode == mode_attendu
    assert parametres.dejeuner_actif is ancien
    assert parametres.dejeuner_duree_min == 60  # défaut du paramètre absent
    assert f"lu comme mode de déjeuner « {mode_attendu} »" in caplog.text
    assert session.get(Parametre, "dejeuner_mode") is None  # la lecture n'écrit rien


def test_base_sans_aucun_reglage_de_mode_lue_avec_le_defaut(session, caplog):
    _ancienne_base(session, None)
    with caplog.at_level(logging.INFO, logger="edt.services.parametres"):
        parametres = lire_parametres(session)
    assert parametres.dejeuner_mode == DEJEUNER_FIXE
    assert "« dejeuner_mode » absent de la base" in caplog.text
    assert "lu comme mode de déjeuner" not in caplog.text


def test_ancien_booleen_ignore_quand_le_mode_est_enregistre(session, caplog):
    session.add(Parametre(cle="dejeuner_horaires_determines", valeur=False))
    session.get(Parametre, "dejeuner_mode").valeur = DEJEUNER_LIBRE
    session.flush()
    with caplog.at_level(logging.INFO, logger="edt.services.parametres"):
        assert lire_parametres(session).dejeuner_mode == DEJEUNER_LIBRE
    assert "lu comme mode de déjeuner" not in caplog.text


def test_ancienne_plage_vide_lue_comme_pas_de_pause(session, caplog):
    """Base d'avant tout réglage, où une plage vide voulait dire « pas de déjeuner » : lisible."""
    session.get(Parametre, "dejeuner_fin_min").valeur = 12 * H + 45
    _ancienne_base(session, None)
    with caplog.at_level(logging.WARNING, logger="edt.services.parametres"):
        parametres = lire_parametres(session)
    assert parametres.dejeuner_mode == DEJEUNER_AUCUN
    assert parametres.plage_dejeuner is None
    assert "lue comme « pas de pause »" in caplog.text
    assert session.get(Parametre, "dejeuner_fin_min").valeur == 12 * H + 45


def test_pause_libre_incoherente_en_base_lue_comme_pas_de_pause(session, caplog):
    session.get(Parametre, "dejeuner_mode").valeur = DEJEUNER_LIBRE
    session.get(Parametre, "dejeuner_duree_min").valeur = 120  # fenêtre de 60 min
    session.flush()
    with caplog.at_level(logging.WARNING, logger="edt.services.parametres"):
        parametres = lire_parametres(session)
    assert parametres.dejeuner_mode == DEJEUNER_AUCUN
    assert parametres.fenetre_dejeuner is None
    assert "ne tient pas dans la fenêtre" in caplog.text
