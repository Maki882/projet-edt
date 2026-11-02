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

Verrou d'écriture : une requête d'écriture (POST, PUT, PATCH, DELETE) ouvre
sa transaction par `BEGIN IMMEDIATE` (`edt.db.moteur.transaction_immediate`),
avant toute lecture. SQLite refuse aussitôt, sans attendre, qu'une
transaction qui a déjà lu se mette à écrire pendant qu'une autre connexion
écrit (sa règle contre les interblocages) : une modification faite pendant
un import ou un chargement de jeu, dans un autre onglet, échouait
« database is locked ». En prenant le verrou d'emblée, la requête attend
son tour (`DELAI_VERROU_MS`, 10 s), puis la page « base occupée » (503) le
dit. GET et HEAD restent différés : ils sont servis pendant qu'un verrou
d'écriture est tenu.

Exception : les routes de `ROUTES_ECRITURE_DELEGUEE` (lancer, relancer,
arrêter un calcul) ne font que lire dans la session de la requête ; leurs
écritures passent par les propres transactions du service de génération,
qui attendraient en vain, jusqu'au délai, un verrou que la requête
tiendrait dans le même fil.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from edt.db.moteur import fabrique_sessions, transaction_immediate

METHODES_ECRITURE = frozenset({"POST", "PUT", "PATCH", "DELETE"})
"""Méthodes dont la transaction prend le verrou d'écriture d'emblée (voir l'en-tête)."""

ROUTES_ECRITURE_DELEGUEE = frozenset({"lancer_calcul", "relancer_calcul", "arreter_calcul"})
"""Noms des routes d'écriture dont la session reste différée : elle ne fait que lire, le
service de génération écrit dans ses propres transactions. `creer_app` vérifie que chaque nom
désigne une route existante (un renommage ne passe pas inaperçu)."""


def verrou_d_emblee(request: Request) -> bool:
    """Vrai si la transaction de cette requête prend le verrou d'écriture dès son ouverture."""
    route = request.scope.get("route")
    return (
        request.method in METHODES_ECRITURE
        and getattr(route, "name", None) not in ROUTES_ECRITURE_DELEGUEE
    )


def session_requete(request: Request) -> Iterator[Session]:
    """Ouvre une session pour la durée de la route : validée si tout va bien, annulée sinon.

    Requête d'écriture : verrou d'écriture pris avant toute requête (voir l'en-tête).
    """
    session = fabrique_sessions()()
    try:
        if verrou_d_emblee(request):
            # Avant toute lecture : une transaction qui a déjà lu ne peut plus attendre son
            # tour pour écrire (règle anti-interblocage de SQLite) ; celle-ci l'attend.
            transaction_immediate(session)
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
