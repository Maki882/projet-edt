"""Données fictives : générateur d'un établissement d'ingénieurs complet, réaliste et faisable.

L'école n'a fourni aucune donnée : ce paquet fabrique un établissement fictif
(sites, salles, groupes, étudiants, enseignants, matières, calendrier,
besoins, indisponibilités) qui alimente les tests, le solveur et la
démonstration. Tout est fictif : aucun nom réel, aucune donnée de santé.

- `configuration` : les trois tailles nommées (petit, moyen, grand) et la
  dataclass des paramètres d'un jeu sur mesure ;
- `catalogue` : le contenu fixe de l'établissement (sites, genres de salle,
  programme des matières, parcours, langues) ;
- `temoin` : le placement glouton qui construit, en même temps que les
  besoins, un emploi du temps témoin valide (la preuve de faisabilité) ;
- `generateur` : `generer_jeu`, la fonction de service qui écrit le jeu en base ;
- `rapport` : `RapportJeu`, ce que la génération a produit (compteurs, taux
  de charge, besoins écartés, témoin).

Voir `docs/donnees-fictives.md`.
"""

from edt.synthese.configuration import (
    CONFIGURATIONS,
    GRAND,
    MOYEN,
    PETIT,
    ConfigPromotion,
    ConfigurationJeu,
    SallesSite,
    configuration_nommee,
)

__all__ = [
    "CONFIGURATIONS",
    "GRAND",
    "MOYEN",
    "PETIT",
    "ConfigPromotion",
    "ConfigurationJeu",
    "SallesSite",
    "configuration_nommee",
]
