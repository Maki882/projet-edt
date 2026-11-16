"""Aides partagées des tests de l'espace « Données » et de la mise en page commune.

- en-têtes : `HX` (requête HTMX), `depuis(adresse)` (requête HTMX partie
  d'une page, en-tête `HX-Current-URL`) ;
- identifiants du jeu minimal : `Ids` (rendu par la fixture `ids`) ;
- base : `identifiant`, `existe`, `compter`, `seance_placee`,
  `occurrence_prevue` ;
- formulaires : `formulaire_de` (valeurs d'un élément existant, comme le
  navigateur les renverrait), `saisie_salle`, `saisie_besoin`, `saisie_regle` ;
- pages : `texte_visible` (texte sans balises, entités décodées) ;
- valeurs piégées : `NOMBRE_TROP_GRAND` (au-delà de ce que SQLite enregistre),
  `NOMBRE_DE_5000_CHIFFRES` (au-delà de ce que Python convertit) ;
- `LOT_WEB_EDT` : début de la raison d'un `xfail` qui attend une correction du
  lot web-edt (Générer, Emploi du temps, À traiter).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import StatutOccurrence
from edt.services.referentiels import operations

HX = {"HX-Request": "true"}
"""En-têtes d'une requête HTMX."""

NOMBRE_TROP_GRAND = "99999999999999999999"
"""Vingt chiffres : au-delà de ce que SQLite sait enregistrer (OverflowError si on le lui passe)."""

NOMBRE_DE_5000_CHIFFRES = "1" * 5000
"""Au-delà de 4 300 chiffres, `int()` lève ValueError (limite de Python)."""

LOT_WEB_EDT = "à corriger par le lot web-edt : "
"""Début de la raison d'un `xfail(strict=True)` qui attend une correction du lot web-edt."""


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
