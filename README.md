# Projet EDT — générateur d'emplois du temps ISEP

Application qui construit automatiquement les emplois du temps de l'ISEP à partir
des cours, classes, enseignants, salles et contraintes fournis, en garantissant
qu'aucune contrainte obligatoire n'est violée.

Projet scolaire 2026-2027, réalisé en équipe.

## Documentation

- [Spécification fonctionnelle](docs/spec.md) (ébauche, susceptible d'évoluer)
- [Consignes de travail et conventions](AGENTS.md)

## Lancer le projet

À venir : la stack n'est pas encore figée. Voir `AGENTS.md` pour la proposition en cours.

## Vérifier les conflits d'un planning

Un premier contrôle est disponible sans dépendance supplémentaire :

```sh
python verifier_conflits.py emploi_du_temps.db
```

Il lit la table `cours` en lecture seule et signale les cours qui se chevauchent
pour une même salle, un même professeur ou une même classe. Les horaires doivent
être au format `HH:MM`, avec une fin strictement après le début. Utiliser une
représentation cohérente des jours (noms de jours ou dates ISO) dans la base.

Codes de sortie : `0` aucun chevauchement, `1` conflits détectés, `2` données
invalides ou base illisible. Une base absente n'est jamais créée.

Ce contrôle ne certifie pas le respect de toutes les contraintes : les pauses,
déplacements, disponibilités, groupes communs et besoins d'accessibilité ne sont
pas encore vérifiés. Deux cours contigus ne se chevauchent pas, mais peuvent
enfreindre la règle de pause. Aucun cours n'est déplacé ni supprimé.

Tests ciblés (compatibles avec pytest) :

```sh
python -m pytest tests/test_conflits.py
```

Sans pytest installé, les mêmes tests se lancent avec la bibliothèque standard :

```sh
python -m unittest discover -s tests -v
```

Le modèle de ce module est décrit dans [le diagramme de classes](docs/verification-conflits.md).

## Contribuer au code

1. Créer une branche depuis `main` : `feat/ma-fonctionnalite`.
2. Committer en français, à l'impératif.
3. Ouvrir une pull request et demander une relecture à un autre membre.
