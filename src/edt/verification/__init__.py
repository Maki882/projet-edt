"""Vérificateur : fonctions pures qui listent les anomalies d'un emploi du temps.

Point d'entrée stable pour tout le projet :

    from edt.verification import Anomalie, verifier, verifier_occurrences

Le contrat (types et signatures) est dans `edt.verification.contrat`. Tant
que le lot « vérificateur » n'est pas livré, `verifier` et
`verifier_occurrences` lèvent `ErreurNonLivre`. Le lot vérificateur écrit son
implémentation dans ce paquet puis remplace les deux imports ci-dessous, sans
changer les signatures.
"""

from edt.verification.contrat import (
    Anomalie,
    FonctionVerification,
    FonctionVerificationDatee,
    verifier,
    verifier_occurrences,
)

__all__ = [
    "Anomalie",
    "FonctionVerification",
    "FonctionVerificationDatee",
    "verifier",
    "verifier_occurrences",
]
