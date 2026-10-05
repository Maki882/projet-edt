"""Dépendances FastAPI partagées par les routes.

Session de base : déclarer le paramètre avec le type `SessionRequete`.

    from edt.web.dependances import SessionRequete

    @routeur.post("/salles")
    def creer(request: Request, session: SessionRequete):
        services.salles.creer(session, ...)      # le service ne valide pas
        return rediriger(request, "/salles", "Salle enregistrée.")

La transaction est **validée automatiquement** quand la route se termine sans
erreur, **avant** l'envoi de la réponse (la page suivante voit donc les
données), et **annulée** si la route lève une erreur. Une route n'appelle
jamais `session.commit()` elle-même.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from edt.db.moteur import fabrique_sessions


def session_requete() -> Iterator[Session]:
    """Ouvre une session pour la durée de la route : validée si tout va bien, annulée sinon."""
    session = fabrique_sessions()()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


SessionRequete = Annotated[Session, Depends(session_requete, scope="function")]
"""Type à utiliser pour le paramètre `session` d'une route.

`scope="function"` fait valider la transaction dès la fin de la fonction de
route, avant l'envoi de la réponse.
"""
