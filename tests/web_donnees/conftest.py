"""Fixtures des tests de l'espace « Données » et de la mise en page commune.

- `site` : client web sur une base chargée du jeu minimal ;
- `ids` : identifiants utiles du jeu minimal (campus, groupes, semestre...).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from edt.db.jeux import JeuMinimal
from edt.db.modeles import (
    Campus,
    Enseignant,
    Groupe,
    Matiere,
    Salle,
    TypeSalle,
    TypeSeance,
)
from edt.db.moteur import session_base


@dataclass(frozen=True)
class Ids:
    """Identifiants du jeu minimal utilisés par les tests."""

    annee: int
    semestre: int
    ndc: int
    issy: int
    salle_n016: int
    type_cours: int
    promo: int
    classe_g1: int
    enseignant_martin: int
    matiere_maths: int
    type_td: int


@pytest.fixture
def site(jeu_minimal: JeuMinimal, client: TestClient) -> TestClient:
    """Client web sur une base chargée du jeu minimal."""
    return client


@pytest.fixture
def ids(jeu_minimal: JeuMinimal) -> Ids:
    """Identifiants du jeu minimal, lus en base."""
    with session_base() as session:

        def ident(modele, colonne, valeur) -> int:
            return session.scalar(select(modele.id).where(colonne == valeur))

        return Ids(
            annee=jeu_minimal.annee_scolaire_id,
            semestre=jeu_minimal.semestre_id,
            ndc=ident(Campus, Campus.code, "NDC"),
            issy=ident(Campus, Campus.code, "ISSY"),
            salle_n016=ident(Salle, Salle.code, "N016"),
            type_cours=ident(TypeSalle, TypeSalle.code, "COURS"),
            promo=ident(Groupe, Groupe.code, "A1"),
            classe_g1=ident(Groupe, Groupe.code, "A1-G1"),
            enseignant_martin=ident(Enseignant, Enseignant.identifiant, "ENS001"),
            matiere_maths=ident(Matiere, Matiere.code, "IM.1101"),
            type_td=ident(TypeSeance, TypeSeance.code, "TD"),
        )
