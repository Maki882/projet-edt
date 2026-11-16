"""Formulaire de déplacement d'une séance : heures de début annoncées « possible » ou « refusé »."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from tests.outils.verification.versions import enregistrer_version

from edt.db import modeles as m
from edt.domaine.enumerations import Gravite
from edt.services.emploi_du_temps import modifications
from edt.verification.contrat import Anomalie


@pytest.fixture
def seance(jeu_minimal, session) -> m.SeanceHebdo:
    """Le CM de mathématiques de la version saisie à la main (lundi 8 h 30)."""
    version_id = enregistrer_version(session, jeu_minimal.semestre_id)
    return session.scalar(
        select(m.SeanceHebdo)
        .where(m.SeanceHebdo.version_id == version_id)
        .order_by(m.SeanceHebdo.jour, m.SeanceHebdo.debut_min)
        .limit(1)
    )


def mentions(formulaire) -> list[str]:
    return [texte.rpartition(" (")[2].rstrip(")") for _, texte in formulaire.debuts]


def test_chaque_heure_annoncee_sans_changer_les_valeurs(seance, session, faux_verificateur):
    formulaire = modifications.formulaire_seance(session, seance.id)
    valeurs = [debut for debut, _ in formulaire.debuts]
    assert len(valeurs) > 1
    attendu = ["actuelle" if v == seance.debut_min else "possible" for v in valeurs]
    assert mentions(formulaire) == attendu
    verifiees = [a for a in faux_verificateur.appels if a[0] == "modification"]
    assert len(verifiees) == len(valeurs) - 1  # une vérification par heure, sauf l'actuelle
    assert all(appel[-1] is None for appel in verifiees)  # sans salle

    faux_verificateur.modification = [Anomalie("GROUPE_UNIQUE", Gravite.ERREUR, "Conflit.")]
    refusees = modifications.formulaire_seance(session, seance.id)
    assert [d for d, _ in refusees.debuts] == valeurs
    assert mentions(refusees) == ["actuelle" if m_ == "actuelle" else "refusé" for m_ in attendu]


@pytest.mark.cas_limite
def test_budget_depasse_ou_verificateur_absent(seance, session, faux_verificateur, monkeypatch):
    """Cas : la vérification des heures dépasse son budget, ou le vérificateur est absent.

    Risque : un formulaire qui met des secondes à se recharger à chaque changement de jour,
    ou une erreur qui empêche d'ouvrir le panneau de la séance.
    """
    monkeypatch.setattr(modifications, "BUDGET_ANNOTATION_S", -1.0)
    formulaire = modifications.formulaire_seance(session, seance.id)
    assert not [a for a in faux_verificateur.appels if a[0] == "modification"]
    assert "possible" not in mentions(formulaire) and formulaire.remarque_debuts is None

    monkeypatch.setattr(modifications, "BUDGET_ANNOTATION_S", 1.5)
    monkeypatch.setitem(__import__("sys").modules, "edt.services.verification", None)
    sans = modifications.formulaire_seance(session, seance.id)
    assert "possible" not in mentions(sans)
    assert sans.remarque_debuts.startswith("Les heures possibles n'ont pas pu être vérifiées")
