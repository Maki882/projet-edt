"""Vérificateur : fonctions pures qui listent les anomalies d'un emploi du temps.

Point d'entrée stable pour tout le projet :

    from edt.verification import Anomalie, verifier, verifier_occurrences

- `verifier(instantane, placements, contraintes_actives=None)` : semaine type ;
- `verifier_occurrences(instantane, occurrences, contraintes_actives=None)` :
  séances datées ;
- `calculer_indicateurs(instantane, placements, anomalies=None)` :
  indicateurs de qualité d'une semaine type.

Organisation du paquet :

- `contrat` : type `Anomalie` et signatures (contrat partagé, figé) ;
- `semaine_type`, `datee` : les deux points d'entrée (validation, volume,
  verrous, assemblage des règles, tri) ;
- `contexte` : vue uniforme des séances placées (`Element`) et outils communs ;
- `regles_seance`, `regles_paires`, `regles_enseignants` : les règles ;
- `indisponibilites` : projection des indisponibilités sur la semaine type ;
- `messages` : mise en forme des textes ;
- `indicateurs` : indicateurs de qualité.

Les règles sont décrites dans `docs/verification.md`.
"""

from edt.verification.contrat import Anomalie, FonctionVerification, FonctionVerificationDatee
from edt.verification.datee import verifier_occurrences
from edt.verification.indicateurs import Indicateurs, calculer_indicateurs
from edt.verification.semaine_type import verifier

__all__ = [
    "Anomalie",
    "FonctionVerification",
    "FonctionVerificationDatee",
    "Indicateurs",
    "calculer_indicateurs",
    "verifier",
    "verifier_occurrences",
]
