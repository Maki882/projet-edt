"""État général de l'application : base initialisée ou non, schéma à jour, écriture, compteurs.

Sert à la page d'accueil, à la route `/sante` et aux commandes `doctor` et
`verifier-schema`.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass, field

from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from edt.config import url_affichable
from edt.db import modeles as m
from edt.db.initialisation import ecarts_schema, tables_manquantes
from edt.db.moteur import base_verrouillee, expliquer_erreur_base, obtenir_moteur

journal = logging.getLogger(__name__)

_verrou_schema = threading.Lock()
_ecarts_connus: dict[tuple[str, int], tuple[str, ...]] = {}
"""Écarts de schéma déjà calculés, par base et version du schéma SQLite (`_ecarts_du_schema`)."""

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
"""Entités comptées par le diagnostic (`EtatBase.compteurs`), avec leur libellé."""


@dataclass(frozen=True)
class EtatBase:
    """Diagnostic de la base de données.

    - `accessible` : la connexion fonctionne ;
    - `initialisee` : la base est prête à servir, c'est-à-dire que toutes les
      tables du modèle existent **et** qu'aucune table existante ne diffère du
      modèle. Une base au schéma périmé n'est donc pas initialisée ;
    - `tables_manquantes` : tables du modèle absentes de la base ;
    - `ecarts_schema` : écarts entre les tables existantes et le modèle, en
      phrases lisibles (voir `edt.db.initialisation.ecarts_schema`) ;
    - `mode_journal` : mode de journalisation SQLite (attendu : `wal`) ;
    - `inscriptible` : une écriture d'essai (annulée) passe ; `False` pour une
      base en lecture seule ou un disque plein (`ecriture_impossible` dit
      pourquoi, avec le remède), `None` si elle n'a pas pu être tentée (base
      occupée par une autre écriture, ou inaccessible) ;
    - `erreur` : message si la base est inaccessible ;
    - `compteurs` : nombre de lignes par entité, seulement si la base est initialisée.
    """

    url: str
    accessible: bool
    initialisee: bool
    tables_manquantes: tuple[str, ...] = ()
    mode_journal: str | None = None
    erreur: str | None = None
    compteurs: dict[str, int] = field(default_factory=dict)
    ecarts_schema: tuple[str, ...] = ()
    inscriptible: bool | None = None
    ecriture_impossible: str | None = None

    @property
    def schema_perime(self) -> bool:
        """Vrai si une table existante n'a plus les colonnes du modèle (base à recréer)."""
        return bool(self.ecarts_schema)

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


def verifier_ecriture(moteur: Engine) -> tuple[bool | None, str | None]:
    """Écriture d'essai annulée : `(inscriptible, explication si elle est refusée)`.

    En mode WAL, prendre le verrou d'écriture (BEGIN IMMEDIATE) réussit même
    sur un fichier en lecture seule : seule une écriture réelle le révèle. La
    version utilisateur est donc réécrite à l'identique, puis ROLLBACK : rien
    n'est enregistré. Sans attente sur verrou (`/sante` est appelée souvent) :
    une base occupée par une autre écriture donne `None` (non vérifié), pas une
    panne. Ne lève jamais : un refus est journalisé et décrit.
    """
    try:
        brute = moteur.raw_connection()
        try:
            attente = brute.execute("PRAGMA busy_timeout").fetchone()[0]
            brute.execute("PRAGMA busy_timeout = 0")
            try:
                brute.execute("BEGIN IMMEDIATE")
                version = brute.execute("PRAGMA user_version").fetchone()[0]
                brute.execute(f"PRAGMA user_version = {int(version)}")
            finally:
                brute.rollback()
                brute.execute(f"PRAGMA busy_timeout = {int(attente)}")
        finally:
            brute.close()
    except (sqlite3.Error, SQLAlchemyError) as erreur:
        if base_verrouillee(erreur):
            journal.info("Écriture d'essai non tentée : base occupée (%s)", erreur)
            return None, None
        explication = expliquer_erreur_base(erreur)
        journal.warning("Écriture d'essai refusée par la base : %s", explication)
        return False, explication
    return True, None


def _ecarts_du_schema(moteur: Engine, version_schema: int | None) -> tuple[str, ...]:
    """Écarts entre les tables et le modèle, recalculés seulement quand le schéma change.

    Comparer chaque table au modèle (`ecarts_schema`) coûte des dizaines de
    millisecondes, et l'accueil comme `/sante` le redemandent à chaque
    affichage. SQLite incrémente `PRAGMA schema_version` à chaque création,
    suppression ou modification de table (`init-db`, remise à zéro, `ALTER
    TABLE` d'un autre processus) : le résultat est donc gardé par base et par
    version du schéma. Limite : une base remplacée par un autre fichier au
    même emplacement et au même numéro de version du schéma garderait les
    écarts de l'ancienne jusqu'au redémarrage du serveur.
    """
    if version_schema is None:
        return tuple(ecarts_schema(moteur))
    cle = (str(moteur.url), version_schema)
    with _verrou_schema:
        connus = _ecarts_connus.get(cle)
    if connus is None:
        connus = tuple(ecarts_schema(moteur))
        with _verrou_schema:
            if len(_ecarts_connus) >= 32:
                _ecarts_connus.clear()
            _ecarts_connus[cle] = connus
    return connus


def diagnostiquer_base(moteur: Engine | None = None) -> EtatBase:
    """Examine la base configurée sans jamais lever : toute panne est décrite dans le résultat.

    L'écriture d'essai (`verifier_ecriture`, moins d'une milliseconde) est
    refaite à chaque appel : c'est elle qui découvre une base devenue en
    lecture seule ou un disque plein. La comparaison du schéma au modèle,
    elle, n'est refaite que si le schéma a changé (`_ecarts_du_schema`).
    """
    moteur = moteur or obtenir_moteur()
    url = url_affichable(moteur.url)
    try:
        with moteur.connect() as connexion:
            connexion.execute(text("SELECT 1"))
            mode_journal = None
            version_schema = None
            if moteur.dialect.name == "sqlite":
                mode_journal = connexion.execute(text("PRAGMA journal_mode")).scalar()
                version_schema = int(connexion.execute(text("PRAGMA schema_version")).scalar())
        manquantes = tuple(tables_manquantes(moteur))
        ecarts = _ecarts_du_schema(moteur, version_schema)
        initialisee = not manquantes and not ecarts
        inscriptible, ecriture_impossible = (
            verifier_ecriture(moteur) if moteur.dialect.name == "sqlite" else (None, None)
        )
        compteurs: dict[str, int] = {}
        if initialisee:
            with Session(moteur) as session:
                compteurs = compter_entites(session)
    except (SQLAlchemyError, OSError) as erreur:
        journal.exception("Base inaccessible (%s)", url)
        # Le message du pilote de base (« file is not a database ») suffit à l'utilisateur ;
        # la trace complète est dans le journal.
        cause = getattr(erreur, "orig", None) or erreur
        return EtatBase(url=url, accessible=False, initialisee=False, erreur=str(cause))
    if ecarts:
        journal.warning(
            "Schéma périmé (%s) : %d écart(s) avec le modèle : %s",
            url,
            len(ecarts),
            " ; ".join(ecarts),
        )
    return EtatBase(
        url=url,
        accessible=True,
        initialisee=initialisee,
        tables_manquantes=manquantes,
        mode_journal=mode_journal,
        compteurs=compteurs,
        ecarts_schema=ecarts,
        inscriptible=inscriptible,
        ecriture_impossible=ecriture_impossible,
    )
