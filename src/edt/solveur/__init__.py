"""Solveur : moteur qui place les séances d'un instantané.

Point d'entrée stable pour tout le projet :

    from edt.solveur import OptionsSolveur, ResultatSolveur, StatutSolveur, resoudre

Le contrat (types et signature) est dans `edt.solveur.contrat`. Le moteur
exporté ici est pour l'instant le moteur **provisoire** (`edt.solveur.provisoire`),
volontairement naïf. Le lot « solveur » écrit le vrai moteur CP-SAT dans ce
paquet puis remplace l'import de `resoudre` ci-dessous, sans changer la
signature. Les appelants passent par `edt.services.generation`, jamais par le
moteur directement.
"""

from edt.solveur.contrat import Moteur, OptionsSolveur, ResultatSolveur, StatutSolveur
from edt.solveur.provisoire import resoudre

__all__ = ["Moteur", "OptionsSolveur", "ResultatSolveur", "StatutSolveur", "resoudre"]
