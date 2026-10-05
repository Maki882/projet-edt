"""État général de l'application : base initialisée ou non, compteurs des entités.

Sert à la page d'accueil, à la route `/sante` et à la commande `doctor`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from edt.config import url_affichable
from edt.db import modeles as m
from edt.db.initialisation import tables_manquantes
from edt.db.moteur import obtenir_moteur

journal = logging.getLogger(__name__)

ENTITES_COMPTEES: tuple[tuple[str, type], ...] = (
    ("Campus", m.Campus),
    ("Salles", m.Salle),
    ("Enseignants", m.Enseignant),
    ("Groupes", m.Groupe),
    ("Étudiants", m.Etudiant),
    ("Matières", m.Matiere),
    ("Besoins", m.Besoin),
    ("Semestres", m.Semestre),
    ("Jours fermés", m.JourFerme),
    ("Indisponibilités", m.Indisponibilite),
    ("Versions d'emploi du temps", m.VersionEdt),
    ("Calculs", m.Calcul),
)
"""Entités affichées sur la page d'accueil, avec leur libellé."""


@dataclass(frozen=True)
class EtatBase:
    """Diagnostic de la base de données.

    - `accessible` : la connexion fonctionne ;
    - `initialisee` : toutes les tables du modèle existent ;
    - `tables_manquantes` : tables absentes (vide si la base est initialisée) ;
    - `mode_journal` : mode de journalisation SQLite (attendu : `wal`) ;
    - `erreur` : message si la base est inaccessible.
    """

    url: str
    accessible: bool
    initialisee: bool
    tables_manquantes: tuple[str, ...] = ()
    mode_journal: str | None = None
    erreur: str | None = None
    compteurs: dict[str, int] = field(default_factory=dict)

    @property
    def vide(self) -> bool:
        """Vrai si aucun référentiel n'a encore été saisi ni chargé."""
        return not any(self.compteurs.get(nom) for nom in ("Campus", "Salles", "Groupes"))


def compter_entites(session: Session) -> dict[str, int]:
    """Nombre de lignes de chaque entité principale, par libellé."""
    return {
        libelle: session.scalar(select(func.count()).select_from(modele)) or 0
        for libelle, modele in ENTITES_COMPTEES
    }


def diagnostiquer_base(moteur: Engine | None = None) -> EtatBase:
    """Examine la base configurée sans jamais lever : toute panne est décrite dans le résultat."""
    moteur = moteur or obtenir_moteur()
    url = url_affichable(moteur.url)
    try:
        with moteur.connect() as connexion:
            connexion.execute(text("SELECT 1"))
            mode_journal = None
            if moteur.dialect.name == "sqlite":
                mode_journal = connexion.execute(text("PRAGMA journal_mode")).scalar()
        manquantes = tuple(tables_manquantes(moteur))
        compteurs: dict[str, int] = {}
        if not manquantes:
            with Session(moteur) as session:
                compteurs = compter_entites(session)
    except (SQLAlchemyError, OSError) as erreur:
        journal.exception("Base inaccessible (%s)", url)
        return EtatBase(url=url, accessible=False, initialisee=False, erreur=str(erreur))
    return EtatBase(
        url=url,
        accessible=True,
        initialisee=not manquantes,
        tables_manquantes=manquantes,
        mode_journal=mode_journal,
        compteurs=compteurs,
    )
