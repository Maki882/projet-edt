"""EDT : générateur automatique d'emplois du temps pour une école d'ingénieurs.

Le paquet est organisé en couches (voir `docs/architecture.md`) :

- `edt.web` et `edt.cli` : interfaces (routes, gabarits, commandes) ;
- `edt.services` : fonctions typées appelées par les interfaces ;
- `edt.domaine`, `edt.solveur`, `edt.verification` : logique pure, sans base ;
- `edt.db` : moteur SQLAlchemy, modèles ORM, données par défaut.
"""

__version__ = "1.0.0"
