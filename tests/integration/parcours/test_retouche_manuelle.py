"""Scénario e : une retouche manuelle après le calcul (annulation d'une séance datée)."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from tests.outils.parcours.aides import calculer, compter

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import StatutOccurrence
from edt.services.verification import verifier_occurrences_version


@pytest.mark.integration
def test_annuler_une_seance_datee_par_le_site(jeu_minimal, client):
    """Scénario : un cours est annulé (grève) après la publication de l'emploi du temps.

    Étapes :
    1. Calculer l'emploi du temps (vrai solveur) : des séances datées sont créées.
    2. Choisir une séance datée prévue et l'annuler par le site (bouton « Annuler »).
    3. Relire la base : la séance est annulée, avec son motif, et une seule a changé.
    4. Revérifier toutes les séances datées : aucune anomalie bloquante.

    Ce que cela prouve : une modification faite à la main passe par la route web, le service
    et la base sans casser la cohérence de l'emploi du temps vérifiée par le vérificateur.
    """
    version = calculer(jeu_minimal.semestre_id).version_id
    annulee = m.Occurrence.statut == StatutOccurrence.ANNULEE
    de_la_version = m.Occurrence.version_id == version
    with session_base() as session:
        cible = session.scalar(
            select(m.Occurrence.id).where(de_la_version, ~annulee).order_by(m.Occurrence.id)
        )
    avant = compter(m.Occurrence, de_la_version, annulee)

    reponse = client.post(
        f"/emploi-du-temps/occurrences/{cible}/annuler",
        data={"motif": "grève"},
        follow_redirects=False,
    )
    assert reponse.status_code == 303, reponse.text

    with session_base() as session:
        occurrence = session.get(m.Occurrence, cible)
        assert occurrence.statut == StatutOccurrence.ANNULEE
        assert "grève" in occurrence.commentaire
        assert verifier_occurrences_version(session, version).valide
    assert compter(m.Occurrence, de_la_version, annulee) == avant + 1
