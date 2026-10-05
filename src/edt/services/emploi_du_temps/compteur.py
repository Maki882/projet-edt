"""Compteur de l'onglet « À traiter » : le même nombre que la page et que l'accueil.

Le nombre est la somme des `ATraiter.total` des versions actives (voir
`anomalies`) : c'est la seule définition de « points à traiter » de
l'application. Il est demandé à chaque page (pastille du menu) et passe donc
par `memoire.memoriser` : calculé au plus une fois par changement de la base,
servi ensuite en quelques millisecondes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from edt.services.emploi_du_temps import anomalies, memoire

journal = logging.getLogger(__name__)


@dataclass(frozen=True)
class Compteur:
    """Nombre d'éléments à traiter ; `complet` est faux si une vérification n'a pas tourné."""

    total: int
    complet: bool


def compteur_a_traiter(session: Session) -> Compteur:
    """Le compteur, mémorisé tant que la base ne change pas."""
    return memoire.memoriser(session, "compteur_a_traiter", (), lambda: _calculer(session))


def _calculer(session: Session) -> Compteur:
    liste = anomalies.a_traiter(session)
    compteur = Compteur(
        total=sum(element.total for element in liste),
        complet=all(element.complet for element in liste),
    )
    journal.info(
        "Compteur « À traiter » recalculé : %d élément(s)%s",
        compteur.total,
        "" if compteur.complet else " (vérifications incomplètes)",
    )
    return compteur


def oublier() -> None:
    """Vide la mémoire du compteur et des vérifications (tests)."""
    memoire.oublier()
