"""Transactions d'un import : simulation sans écriture, ligne isolée dans un point de sauvegarde.

Un import s'exécute toujours de la même façon, qu'il soit simulé ou appliqué :
la simulation fait exactement le même travail dans un point de sauvegarde
(SAVEPOINT) qu'elle annule à la fin. Le bilan affiché par la simulation est
donc celui que donnera l'application, et rien n'est écrit.

Chaque ligne importée est elle-même isolée dans un point de sauvegarde : si
la base la refuse malgré la validation (contrainte non prévue), seule cette
ligne est annulée et le rapport le dit ; les autres lignes entrent.

Particularité du pilote `sqlite3` de Python : il n'ouvre une transaction
qu'avant un INSERT, UPDATE ou DELETE. Un SAVEPOINT émis avant toute écriture
ouvre alors lui-même la transaction, et le RELEASE de ce point de sauvegarde
la **valide aussitôt** (constaté et couvert par un test). `ouvrir_transaction`
émet donc un BEGIN explicite avant le premier point de sauvegarde ; la
transaction reste ensuite entre les mains de l'appelant (validée à la fin de
la requête web ou du bloc `session_base`).

Ce BEGIN est IMMEDIATE : l'import prend le verrou d'écriture dès le départ.
Deux imports simultanés se suivent ainsi (le second attend le premier, dans
la limite du délai d'attente de la base) au lieu que le second échoue.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

journal = logging.getLogger(__name__)


def ouvrir_transaction(session: Session) -> None:
    """Garantit qu'une vraie transaction est ouverte avant d'utiliser des points de sauvegarde."""
    connexion = session.connection()
    if connexion.dialect.name != "sqlite":
        return
    brute = connexion.connection.dbapi_connection
    if not brute.in_transaction:
        # IMMEDIATE : le verrou d'écriture est pris dès maintenant, en attendant au besoin
        # (busy_timeout) qu'un autre import ou une autre écriture se termine. Un BEGIN
        # différé lirait d'abord, puis échouerait aussitôt (« database is locked ») si une
        # autre transaction avait validé entre-temps : SQLite n'attend pas dans ce cas.
        connexion.exec_driver_sql("BEGIN IMMEDIATE")
        journal.debug("Transaction SQLite ouverte (BEGIN IMMEDIATE) avant les points de sauvegarde")


@contextmanager
def simulation_possible(session: Session, simuler: bool) -> Iterator[None]:
    """Exécute le bloc ; si `simuler`, annule tout ce qu'il a écrit (même en cas de succès).

    En cas d'erreur, le point de sauvegarde est annulé et l'erreur remonte.
    """
    ouvrir_transaction(session)
    point = session.begin_nested()
    try:
        yield
        session.flush()
    except BaseException:
        point.rollback()
        raise
    if simuler:
        point.rollback()
        session.expire_all()
        journal.info("Simulation terminée : toutes les écritures sont annulées")
    else:
        point.commit()
