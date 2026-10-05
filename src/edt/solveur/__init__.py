"""Solveur : moteur qui place les séances d'un instantané.

Point d'entrée stable pour tout le projet :

    from edt.solveur import OptionsSolveur, ResultatSolveur, StatutSolveur, resoudre

Le contrat (types et signature) est dans `edt.solveur.contrat`. Le moteur
exporté ici est le moteur CP-SAT (`edt.solveur.moteur`) ; l'ancien moteur
provisoire reste disponible dans `edt.solveur.provisoire`. Les appelants
passent par `edt.services.generation`, jamais par le moteur directement.

Autres fonctions du paquet (voir `docs/solveur.md`) :

- `edt.solveur.preverification.pre_verifier` : contrôles avant calcul ;
- `edt.solveur.instanciation.instancier` : séances datées d'une semaine type ;
- `edt.solveur.replacement.replacer` : replacement d'occurrences datées.
"""

from edt.solveur.contrat import Moteur, OptionsSolveur, ResultatSolveur, StatutSolveur
from edt.solveur.moteur import resoudre

__all__ = ["Moteur", "OptionsSolveur", "ResultatSolveur", "StatutSolveur", "resoudre"]
