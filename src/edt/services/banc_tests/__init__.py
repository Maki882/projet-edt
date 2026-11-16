"""Banc de tests : lancer les tests du projet depuis l'application et garder leurs résultats.

Les deux onglets « Tests unitaires » et « Tests d'intégration » de l'interface
web, et la commande `python -m edt tests`, passent par ce paquet :

- `catalogue` : liste des tests, lue dans les fichiers de `tests/` sans les
  exécuter (module `ast`), et cibles lançables (un test, un fichier,
  un composant, une famille) ;
- `execution` : une exécution à la fois, dans un sous-processus pytest à
  l'environnement assaini, suivie en direct, arrêtable, bornée en durée ;
- `resultats` : résultat et date de la dernière exécution de chaque test,
  dans un fichier JSON de `<EDT_LOG_DIR>/tests/`, hors de la base (charger un
  établissement fictif remet la base à zéro, l'historique des tests survit) ;
- `greffon` : greffon pytest chargé dans le sous-processus, qui écrit une
  ligne JSON par compte rendu de test.

Ce module ne charge rien : le greffon l'importe dans chaque sous-processus,
qui doit démarrer vite.
"""
