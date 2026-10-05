"""Accès à la base de données (SQLite via SQLAlchemy 2).

- `base` : classe déclarative et convention de nommage des contraintes ;
- `moteur` : moteur, fabrique de sessions, `session_base()` ;
- `modeles` : modèles ORM (voir `docs/modele-donnees.md`) ;
- `initialisation` : création du schéma et des données par défaut ;
- `jeux` : petit jeu de données cohérent pour les tests et la découverte.

Il n'y a pas de migrations en V1 : la base se recrée et se repeuple par
commande (`python -m edt init-db --reset`).
"""
