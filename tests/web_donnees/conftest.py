"""Fixtures et aides des tests de l'espace « Données » et de la mise en page commune.

Fixtures :

- `site` : client web sur une base chargée du jeu minimal ;
- `ids` : identifiants utiles du jeu minimal (campus, groupes, semestre...) ;
- `version_generee` : jeu minimal et une version produite par un vrai calcul
  (son identifiant) ;
- `calcul_retenu` : un calcul lancé sur le semestre du jeu minimal, retenu par
  un moteur qui attend d'être libéré (`liberer()`), puis qui calcule pour de
  vrai : de quoi agir « pendant le calcul » puis vérifier qu'il aboutit ;
- `sans_prechauffage` (automatique) : aucun fil de préchauffage des écrans,
  qui survivrait à la base temporaire du test.

Aides (à importer : `from .conftest import HX, depuis, ...`) :

- en-têtes : `HX` (requête HTMX), `depuis(adresse)` (requête HTMX partie
  d'une page, en-tête `HX-Current-URL`) ;
- base : `identifiant`, `existe`, `compter`, `seance_placee`,
  `occurrence_prevue` ;
- formulaires : `formulaire_de` (valeurs d'un élément existant, comme le
  navigateur les renverrait), `saisie_salle`, `saisie_besoin`, `saisie_regle` ;
- pages : `texte_visible` (texte sans balises, entités décodées) ;
- valeurs piégées : `NOMBRE_TROP_GRAND` (au-delà de ce que SQLite enregistre),
  `NOMBRE_DE_5000_CHIFFRES` (au-delà de ce que Python convertit) ;
- `LOT_WEB_EDT` : début de la raison d'un `xfail` qui attend une correction du
  lot web-edt (Générer, Emploi du temps, À traiter).

Les tests de la banque des cas limites portent le marqueur `cas_limite` et
commencent par « Cas : ... Risque : ... ».
"""

from __future__ import annotations

import html
import re
import threading
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from edt import solveur
from edt.db import modeles as m
from edt.db.jeux import JeuMinimal
from edt.db.moteur import session_base
from edt.domaine.enumerations import StatutOccurrence
from edt.services import generation
from edt.services.emploi_du_temps import prechauffage
from edt.services.referentiels import operations

DELAI_CALCUL_S = 20
"""Garde-fou : un calcul de test qui ne finit pas fait échouer le test au lieu de le bloquer."""

HX = {"HX-Request": "true"}
"""En-têtes d'une requête HTMX."""

NOMBRE_TROP_GRAND = "99999999999999999999"
"""Vingt chiffres : au-delà de ce que SQLite sait enregistrer (OverflowError si on le lui passe)."""

NOMBRE_DE_5000_CHIFFRES = "1" * 5000
"""Au-delà de 4 300 chiffres, `int()` lève ValueError (limite de Python)."""

LOT_WEB_EDT = "à corriger par le lot web-edt : "
"""Début de la raison d'un `xfail(strict=True)` qui attend une correction du lot web-edt."""


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


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
def version_generee(jeu_minimal: JeuMinimal) -> int:
    """Jeu minimal et une version active produite par le vrai moteur ; renvoie son identifiant."""
    calcul_id = generation.lancer_calcul(jeu_minimal.semestre_id)
    suivi = generation.attendre_calcul(calcul_id, DELAI_CALCUL_S)
    assert suivi.reussi, suivi.message
    return suivi.version_id


@dataclass(frozen=True)
class CalculRetenu:
    """Calcul en cours, retenu avant le moteur : `liberer()` le laisse calculer et finir."""

    id: int
    semestre_id: int
    _liberation: threading.Event

    def liberer(self) -> generation.SuiviCalcul:
        """Laisse le vrai moteur calculer, attend la fin du calcul et renvoie son suivi."""
        self._liberation.set()
        return generation.attendre_calcul(self.id, DELAI_CALCUL_S)


@pytest.fixture
def calcul_retenu(jeu_minimal: JeuMinimal, monkeypatch):
    """Calcul lancé sur le semestre du jeu minimal, retenu tant que le test ne le libère pas.

    Le moteur attend d'être libéré, puis appelle le vrai moteur sur l'instantané
    chargé au lancement : ce que le test a changé entre-temps ne s'y voit pas,
    mais l'enregistrement de la solution, lui, se fait dans la base telle
    qu'elle est devenue (c'est là que se voient les suppressions acceptées à tort).
    """
    demarre, liberation = threading.Event(), threading.Event()
    vrai_moteur = solveur.resoudre

    def moteur_retenu(instantane, options, arret=None):
        demarre.set()
        assert liberation.wait(DELAI_CALCUL_S), "le calcul retenu n'a pas été libéré"
        return vrai_moteur(instantane, options, arret)

    monkeypatch.setattr(solveur, "resoudre", moteur_retenu)
    calcul_id = generation.lancer_calcul(jeu_minimal.semestre_id)
    assert demarre.wait(DELAI_CALCUL_S), "le calcul n'a pas atteint le moteur"
    calcul = CalculRetenu(calcul_id, jeu_minimal.semestre_id, liberation)
    yield calcul
    # Le calcul doit être fini avant que la base temporaire ne disparaisse.
    liberation.set()
    generation.attendre_calcul(calcul_id, DELAI_CALCUL_S)


# --------------------------------------------------------------------------- #
# Aides : en-têtes et pages
# --------------------------------------------------------------------------- #


def depuis(adresse: str) -> dict[str, str]:
    """En-têtes d'une requête HTMX partie de la page `adresse` (en-tête `HX-Current-URL`)."""
    return {**HX, "HX-Current-URL": f"http://testserver{adresse}"}


def texte_visible(reponse) -> str:
    """Texte visible d'une page : balises retirées, entités décodées, espaces réduits."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", reponse.text)))


# --------------------------------------------------------------------------- #
# Aides : base
# --------------------------------------------------------------------------- #


def identifiant(modele, colonne, valeur) -> int | None:
    """Identifiant de l'élément de `modele` dont `colonne` vaut `valeur` (None s'il n'y en a pas)."""
    with session_base() as session:
        return session.scalar(select(modele.id).where(colonne == valeur))


def existe(modele, ident: int) -> bool:
    """Vrai si l'élément `ident` de `modele` est en base."""
    with session_base() as session:
        return session.get(modele, ident) is not None


def compter(modele, *conditions) -> int:
    """Nombre d'éléments de `modele` qui vérifient les conditions."""
    requete = select(func.count()).select_from(modele)
    if conditions:
        requete = requete.where(*conditions)
    with session_base() as session:
        return session.scalar(requete)


@dataclass(frozen=True)
class SeancePlacee:
    """Une séance hebdomadaire d'une version, placée dans une salle."""

    id: int
    salle_id: int
    besoin_id: int
    groupe_id: int


def seance_placee(version_id: int) -> SeancePlacee:
    """La première séance de la version qui a une salle, avec un de ses groupes."""
    with session_base() as session:
        seance = session.scalar(
            select(m.SeanceHebdo)
            .where(m.SeanceHebdo.version_id == version_id, m.SeanceHebdo.salle_id.is_not(None))
            .order_by(m.SeanceHebdo.id)
            .limit(1)
        )
        groupe = session.scalar(
            select(m.besoin_groupe.c.groupe_id)
            .where(m.besoin_groupe.c.besoin_id == seance.besoin_id)
            .limit(1)
        )
        return SeancePlacee(seance.id, seance.salle_id, seance.besoin_id, groupe)


def occurrence_prevue(version_id: int) -> int:
    """Une séance datée de la version qui n'est pas annulée."""
    with session_base() as session:
        return session.scalar(
            select(m.Occurrence.id)
            .where(
                m.Occurrence.version_id == version_id,
                m.Occurrence.statut != StatutOccurrence.ANNULEE,
            )
            .order_by(m.Occurrence.id)
            .limit(1)
        )


# --------------------------------------------------------------------------- #
# Aides : formulaires
# --------------------------------------------------------------------------- #


def formulaire_de(code_entite: str, ident: int) -> dict[str, Any]:
    """Valeurs du formulaire de modification d'un élément, comme le navigateur les renverrait."""
    with session_base() as session:
        valeurs = operations.lire(session, code_entite, ident)
    donnees: dict[str, Any] = {}
    for champ in operations.entite(code_entite).champs:
        valeur = valeurs.get(champ.nom)
        if isinstance(valeur, bool):
            donnees[champ.nom] = "oui" if valeur else "non"
        elif isinstance(valeur, list):
            donnees[champ.nom] = [str(v) for v in valeur]
        else:
            donnees[champ.nom] = "" if valeur is None else str(valeur)
    return donnees


def saisie_salle(ids: Ids, **champs) -> dict[str, Any]:
    """Formulaire d'ajout d'une salle valide (« ZZ7 », campus NDC), modifiable champ par champ."""
    return {
        "code": "ZZ7",
        "campus_id": str(ids.ndc),
        "type_salle_id": str(ids.type_cours),
        "capacite": "30",
        "accessibilite": "oui",
        **champs,
    }


def saisie_besoin(ids: Ids, **champs) -> dict[str, Any]:
    """Formulaire d'ajout d'un besoin valide (TD de maths, A1-G1, une séance par semaine)."""
    return {
        "semestre_id": ids.semestre,
        "matiere_id": ids.matiere_maths,
        "type_seance_id": ids.type_td,
        "groupes": [ids.classe_g1],
        "enseignants": [ids.enseignant_martin],
        "duree_min": "1 h 30",
        "mode_volume": "par_semaine",
        "seances_par_semaine": "1",
        **champs,
    }


def saisie_regle(ids: Ids, **champs) -> dict[str, Any]:
    """Formulaire d'ajout d'une règle valide (A1-G1 ne travaille que le lundi), modifiable."""
    return {
        "type": "JOURS_TRAVAILLES",
        "annee": str(ids.annee),
        "portee": "groupe",
        "cible_groupe": str(ids.classe_g1),
        "p_jours": ["lundi"],
        "niveau": "important",
        "periode": "annee",
        **champs,
    }
