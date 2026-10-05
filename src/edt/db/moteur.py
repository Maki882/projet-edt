"""Moteur SQLAlchemy et sessions.

Un moteur est créé par URL de base et réutilisé (les fils d'exécution du
serveur et du calcul partagent le même moteur, chacun avec sa session).
Pour SQLite, chaque connexion active les clés étrangères, le mode WAL et un
délai d'attente sur verrou.

Usage dans un service appelé hors requête web (CLI, fil de calcul) :

    with session_base() as session:
        ...            # validé à la sortie du bloc, annulé en cas d'erreur

Dans une route web, utiliser la dépendance `edt.web.dependances.session_requete`.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from edt.config import charger_configuration, url_affichable
from edt.erreurs import ErreurConfiguration

journal = logging.getLogger(__name__)

DELAI_VERROU_MS = 10_000

_verrou = threading.Lock()
_moteurs: dict[str, Engine] = {}


def _preparer_sqlite(connexion_dbapi, _enregistrement) -> None:
    """Réglages appliqués à chaque nouvelle connexion SQLite."""
    curseur = connexion_dbapi.cursor()
    try:
        curseur.execute("PRAGMA foreign_keys=ON")
        curseur.execute("PRAGMA journal_mode=WAL")
        curseur.execute("PRAGMA synchronous=NORMAL")
        curseur.execute(f"PRAGMA busy_timeout={DELAI_VERROU_MS}")
    finally:
        curseur.close()


def creer_moteur(url: str) -> Engine:
    """Crée un moteur pour `url` (sans le mettre en cache).

    Pour une base SQLite sur fichier, le dossier parent est créé s'il manque.
    """
    try:
        url_analysee = make_url(url)
    except Exception as erreur:
        raise ErreurConfiguration(f"EDT_DB_URL invalide ({url!r}) : {erreur}") from erreur

    arguments: dict = {}
    if url_analysee.get_backend_name() == "sqlite":
        chemin = url_analysee.database
        if chemin and chemin != ":memory:":
            try:
                Path(chemin).parent.mkdir(parents=True, exist_ok=True)
            except OSError as erreur:
                raise ErreurConfiguration(
                    f"Impossible de créer le dossier de la base « {Path(chemin).parent} » : {erreur}"
                ) from erreur
        arguments["connect_args"] = {"check_same_thread": False}

    moteur = create_engine(url_analysee, **arguments)
    if url_analysee.get_backend_name() == "sqlite":
        event.listen(moteur, "connect", _preparer_sqlite)
    return moteur


def obtenir_moteur(url: str | None = None) -> Engine:
    """Moteur partagé pour `url` (par défaut : `EDT_DB_URL`)."""
    url = url or charger_configuration().db_url
    with _verrou:
        moteur = _moteurs.get(url)
        if moteur is None:
            moteur = creer_moteur(url)
            _moteurs[url] = moteur
            journal.debug("Moteur de base créé pour %s", url_affichable(url))
        return moteur


def fabrique_sessions(url: str | None = None) -> sessionmaker[Session]:
    """Fabrique de sessions liée au moteur partagé.

    `expire_on_commit=False` : les objets restent lisibles après validation,
    ce qui évite des requêtes surprises dans les gabarits.
    """
    return sessionmaker(bind=obtenir_moteur(url), expire_on_commit=False)


@contextmanager
def session_base(url: str | None = None) -> Iterator[Session]:
    """Ouvre une session : validée à la sortie du bloc, annulée si une erreur survient."""
    session = fabrique_sessions(url)()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def liberer_moteurs() -> None:
    """Ferme toutes les connexions et oublie les moteurs (tests, arrêt propre)."""
    with _verrou:
        for moteur in _moteurs.values():
            moteur.dispose()
        _moteurs.clear()
