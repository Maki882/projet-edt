"""Transactions d'un import : simulation sans écriture, ligne isolée dans un point de sauvegarde.

Un import s'exécute toujours de la même façon, qu'il soit simulé ou appliqué :
la simulation fait exactement le même travail dans un point de sauvegarde
(SAVEPOINT) qu'elle annule à la fin. Le bilan affiché par la simulation est
donc celui que donnera l'application, et rien n'est écrit.

Chaque ligne importée est elle-même isolée dans un point de sauvegarde : si
la base la refuse malgré la validation (contrainte non prévue), seule cette
ligne est annulée et le rapport le dit ; les autres lignes entrent.

La transaction de l'import réserve la base en écriture dès son ouverture :
`simulation_possible` la commence par `edt.db.moteur.transaction_immediate`
(le moteur émet alors BEGIN IMMEDIATE). Deux imports simultanés se suivent
ainsi, le second attendant le premier dans la limite du délai d'attente de
la base. Une transaction différée qui a lu puis veut écrire n'attend pas, elle :
elle échoue aussitôt « database is locked » si une autre a validé entre-temps.
La session ne doit avoir servi à rien avant ; le moteur refuse sinon
(`ErreurConfiguration`), et le service lit à part ce dont il a besoin.

Les points de sauvegarde restent annulables par la transaction qui les
contient : le moteur gère lui-même BEGIN et COMMIT (voir `edt.db.moteur`).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from edt.db.moteur import transaction_immediate

journal = logging.getLogger(__name__)


@contextmanager
def simulation_possible(session: Session, simuler: bool) -> Iterator[None]:
    """Exécute le bloc dans une transaction qui réserve la base en écriture dès son début.

    Si `simuler`, tout ce que le bloc a écrit est annulé, même en cas de succès.
    En cas d'erreur, le point de sauvegarde est annulé et l'erreur remonte. La
    session ne doit avoir servi à rien dans sa transaction en cours.
    """
    transaction_immediate(session)
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
