"""Services de l'emploi du temps : générer, consulter, corriger, traiter les anomalies.

Ce paquet sert les trois onglets « Générer », « Emploi du temps » et « À
traiter » ainsi que la page d'accueil. Il ne dépend pas du web : chaque
fonction reçoit une session SQLAlchemy et renvoie des dataclasses figées,
faites de valeurs simples (sérialisables en JSON), que la ligne de commande
ou un futur pilotage par LLM peuvent réutiliser telles quelles. Aucune
fonction ne valide la transaction (`flush` seulement).

Modules :

- `raccords` : **seul** point d'appel des fonctions livrées par d'autres lots
  (pré-vérifications, vérificateur, instanciation datée) ; traduit leur
  absence en erreur explicite ;
- `presentation` : petites mises en forme françaises (durées, semaines,
  couleurs stables par matière) ;
- `disposition` : calcul pur de la position des blocs dans la grille ;
- `vues` : séances d'une vue (version, cible, couche, semaine ou jour) ;
- `modifications` : salles compatibles, déplacement, verrou, occurrences ;
- `versions` : semestres, historique, activation et suppression de versions ;
- `calculs` : préparation, lancement et lecture du résultat d'un calcul ;
- `anomalies` : la liste « À traiter » ;
- `tableau_de_bord` : l'état de l'établissement pour l'accueil.

Le détail (disposition des blocs, raccords en attente, ajout d'une vue) est
dans `docs/interface-emploi-du-temps.md`.
"""
