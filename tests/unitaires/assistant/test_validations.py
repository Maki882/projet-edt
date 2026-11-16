"""Validations sans écriture utilisées par les propositions de l'assistant.

`regles.valider_modification` (même fusion que `modifier`) et
`referentiels.operations.valider_creation` (mêmes contrôles que `creer`).
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from edt.db import modeles as m
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable
from edt.services import regles
from edt.services.referentiels import operations
from edt.services.referentiels.operations import ErreurSaisie


def _nombre(session, modele) -> int:
    return session.scalar(select(func.count()).select_from(modele))


def _enseignant(session, identifiant: str) -> int:
    return session.scalar(select(m.Enseignant.id).where(m.Enseignant.identifiant == identifiant))


# --------------------------------------------------------------------------- #
# valider_modification
# --------------------------------------------------------------------------- #


def test_modification_valide_donne_la_phrase_sans_rien_ecrire(jeu_minimal, session):
    avant = regles.lire(session, jeu_minimal.annee_scolaire_id, "R002")
    validation = regles.valider_modification(
        session, jeu_minimal.annee_scolaire_id, "R002", {"parametres": {"duree": 90}}
    )
    assert validation.valide and validation.erreurs == {}
    assert validation.regle.parametres["duree"] == 90
    assert validation.regle.parametres["mode"] == avant.regle.parametres["mode"]  # fusion
    assert validation.phrase != avant.phrase and "1 h 30" in validation.phrase
    session.expire_all()
    apres = regles.lire(session, jeu_minimal.annee_scolaire_id, "R002")
    assert apres.regle.parametres == avant.regle.parametres and apres.phrase == avant.phrase


def test_modification_invalide_rend_les_erreurs_par_champ(jeu_minimal, session):
    validation = regles.valider_modification(
        session,
        jeu_minimal.annee_scolaire_id,
        "R002",
        {"parametres": {"duree": 5000}, "niveau": "capital"},
    )
    assert not validation.valide and validation.regle is None
    assert set(validation.erreurs) == {"parametres.duree", "niveau"}


@pytest.mark.cas_limite
def test_modification_qui_change_l_annee_ou_le_code_refusee(jeu_minimal, session):
    """Cas : le modèle propose de changer l'année ou le code d'une règle, ou envoie autre
    chose qu'un objet.

    Risque : une règle déplacée d'année en silence, ou une erreur Python au lieu d'un refus.
    """
    validation = regles.valider_modification(
        session, jeu_minimal.annee_scolaire_id, "R002", {"code": "R099", "annee": "2030-2031"}
    )
    assert not validation.valide and set(validation.erreurs) == {"code", "annee"}
    validation = regles.valider_modification(session, jeu_minimal.annee_scolaire_id, "R002", [1])
    assert not validation.valide and "changements" in validation.erreurs


def test_modification_d_une_regle_inconnue(jeu_minimal, session):
    with pytest.raises(ErreurIntrouvable, match="R404"):
        regles.valider_modification(session, jeu_minimal.annee_scolaire_id, "R404", {})


@pytest.mark.cas_limite
def test_activation_qui_ferait_doublon_refusee_par_la_validation(jeu_minimal, session):
    """Cas : l'assistant propose d'activer une pause déjeuner inactive pour la classe qui en a
    déjà une active sur le même semestre.

    Risque : deux réglages contradictoires actifs ; le calcul ne saurait lequel appliquer.
    """
    regle = regles.creer(
        session,
        {
            "type": "PAUSE_DEJEUNER",
            "portee": {"nature": "groupe", "cible": "A1-G1"},
            "parametres": {"mode": "fixe", "debut": "12:00", "fin": "13:00"},
            "annee": "2026-2027",
            "periode": {"semestre": "S1"},
        },
        etat="inactive",
    )
    validation = regles.valider_modification(
        session, jeu_minimal.annee_scolaire_id, regle.code, {"etat": "active"}
    )
    assert not validation.valide and "R002" in validation.erreurs["portee"]
    sans_doublon = regles.valider_modification(
        session, jeu_minimal.annee_scolaire_id, "R003", {"etat": "active"}
    )
    assert sans_doublon.valide and sans_doublon.regle.etat.value == "active"


def test_modifier_garde_le_meme_comportement(jeu_minimal, session):
    detail = regles.modifier(
        session, jeu_minimal.annee_scolaire_id, "R002", {"parametres": {"duree": 90}}
    )
    assert detail.regle.parametres["duree"] == 90 and detail.regle.parametres["mode"] == "libre"
    with pytest.raises(regles.ErreurSaisie):
        regles.modifier(session, jeu_minimal.annee_scolaire_id, "R002", {"code": "R099"})


# --------------------------------------------------------------------------- #
# valider_creation
# --------------------------------------------------------------------------- #


def test_creation_valide_rend_les_valeurs_sans_rien_ecrire(jeu_minimal, session):
    avant = _nombre(session, m.Indisponibilite)
    valeurs = operations.valider_creation(
        session,
        "indisponibilite",
        {
            "cible": "enseignant",
            "enseignant_id": _enseignant(session, "ENS002"),
            "forme": "recurrente",
            "jour": "2",
            "debut_min": "08:30",
            "fin_min": "12:45",
        },
    )
    assert valeurs["jour"] == 2 and (valeurs["debut_min"], valeurs["fin_min"]) == (510, 765)
    assert _nombre(session, m.Indisponibilite) == avant


def test_creation_invalide_rend_les_champs_fautifs(jeu_minimal, session):
    with pytest.raises(ErreurSaisie) as erreur:
        operations.valider_creation(
            session, "indisponibilite", {"cible": "enseignant", "forme": "datee"}
        )
    assert "enseignant_id" in erreur.value.champs and "date_debut" in erreur.value.champs
    with pytest.raises(ErreurDonneesInvalides, match="Champ\\(s\\) inconnu\\(s\\).*couleur"):
        operations.valider_creation(session, "indisponibilite", {"couleur": "rouge"})


def test_creation_d_un_besoin_controle_les_references(jeu_minimal, session):
    with pytest.raises(ErreurSaisie) as erreur:
        operations.valider_creation(
            session, "besoin", {"semestre_id": jeu_minimal.semestre_id, "matiere_id": 999_999}
        )
    assert "matiere_id" in erreur.value.champs
