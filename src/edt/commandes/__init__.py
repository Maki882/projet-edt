"""Commandes de la ligne de commande, découvertes automatiquement par `edt.cli`.

Chaque module de ce paquet expose un objet `commandes` (un `typer.Typer`)
dont les commandes sont ajoutées à `python -m edt`. Une nouvelle commande
prend place dans un module de ce paquet, sans modifier `edt/cli.py`. Les
modules dont le nom commence par `_` ne sont pas chargés.

Une commande appelle un service (`edt.services`) et affiche le résultat ;
elle ne contient pas de logique métier.
"""
