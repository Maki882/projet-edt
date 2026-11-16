"""Fixtures partagées par les tests unitaires et d'intégration de l'espace « Données ».

Importées par leur nom dans le `conftest.py` de chaque côté qui s'en sert :

- `ids` : identifiants utiles du jeu minimal (campus, groupes, semestre...) ;
- `sans_prechauffage` (automatique) : aucun fil de préchauffage des écrans,
  qui survivrait à la base temporaire du test ;
- `generation_lente` : génération de jeu fictif remplacée par une attente
  (`entre` signale qu'elle tient le verrou, `liberer` la laisse finir).
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import select
from tests.outils.web_donnees.aides import Ids
from tests.outils.web_donnees.concurrence import DELAI_S, _rapport_factice

from edt.db import modeles as m
from edt.db.jeux import JeuMinimal
from edt.db.moteur import session_base
from edt.services import jeux
from edt.services.emploi_du_temps import prechauffage


@pytest.fixture
def ids(jeu_minimal: JeuMinimal) -> Ids:
    """Identifiants du jeu minimal, lus en base."""
    with session_base() as session:

        def ident(modele, colonne, valeur) -> int:
            return session.scalar(select(modele.id).where(colonne == valeur))

        return Ids(
            annee=jeu_minimal.annee_scolaire_id,
            semestre=jeu_minimal.semestre_id,
            ndc=ident(m.Campus, m.Campus.code, "NDC"),
            issy=ident(m.Campus, m.Campus.code, "ISSY"),
            salle_n016=ident(m.Salle, m.Salle.code, "N016"),
            type_cours=ident(m.TypeSalle, m.TypeSalle.code, "COURS"),
            promo=ident(m.Groupe, m.Groupe.code, "A1"),
            classe_g1=ident(m.Groupe, m.Groupe.code, "A1-G1"),
            enseignant_martin=ident(m.Enseignant, m.Enseignant.identifiant, "ENS001"),
            matiere_maths=ident(m.Matiere, m.Matiere.code, "IM.1101"),
            type_td=ident(m.TypeSeance, m.TypeSeance.code, "TD"),
        )


@pytest.fixture(autouse=True)
def sans_prechauffage(monkeypatch) -> None:
    """Pas de préchauffage des écrans après un calcul : le fil survivrait à la base du test."""
    monkeypatch.setattr(prechauffage, "ACTIF", False)


@pytest.fixture
def generation_lente(monkeypatch):
    """Remplace la génération par une attente : `entre` signale qu'elle tient le verrou."""
    entre, liberer = threading.Event(), threading.Event()

    def generer(session, configuration, graine, **options):
        entre.set()
        assert liberer.wait(DELAI_S), "la génération ralentie n'a pas été libérée"
        return _rapport_factice(graine)

    monkeypatch.setattr(jeux, "generer_jeu", generer)
    yield entre, liberer
    liberer.set()
