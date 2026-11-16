"""Scénario d : une règle personnalisée saisie par l'utilisateur est respectée par le calcul."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from tests.outils.parcours.aides import calculer

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.services import regles
from edt.services.verification import verifier_version

LUNDI = 0  # jours de la semaine type : 0 = lundi ... 5 = samedi


@pytest.mark.integration
def test_regle_jours_travailles_respectee_de_bout_en_bout(jeu_minimal):
    """Scénario : « la classe A1-G1 ne travaille pas le lundi » (règle nécessaire).

    Étapes :
    1. Créer la règle avec le service des règles (comme l'écran « Règles »).
    2. Lancer un vrai calcul sur le semestre.
    3. Faire vérifier la version produite par le vérificateur.
    4. Relire la base : aucune séance propre à A1-G1 n'est placée le lundi.

    Ce que cela prouve : une règle saisie est lue par le calcul, imposée par le solveur, puis
    contrôlée par le vérificateur, qui ne lui trouve aucune entorse.
    """
    with session_base() as session:
        annee = session.get(m.AnneeScolaire, jeu_minimal.annee_scolaire_id).libelle
        regle = regles.creer(
            session,
            {
                "type": "JOURS_TRAVAILLES",
                "annee": annee,
                "portee": {"nature": "groupe", "cible": "A1-G1"},
                "parametres": {"jours": ["mardi", "mercredi", "jeudi", "vendredi"]},
                "niveau": "necessaire",
            },
        )

    version = calculer(jeu_minimal.semestre_id).version_id

    with session_base() as session:
        rapport = verifier_version(session, version)
        jours_a1_g1 = session.scalars(
            select(m.SeanceHebdo.jour).where(
                m.SeanceHebdo.version_id == version,
                m.SeanceHebdo.besoin.has(m.Besoin.groupes.any(code="A1-G1")),
            )
        ).all()
    assert rapport.valide
    assert regle.id in rapport.niveaux_regles  # la règle a bien été vérifiée
    assert not [a for a in rapport.anomalies if a.regle_id == regle.id]
    assert jours_a1_g1 and LUNDI not in jours_a1_g1
