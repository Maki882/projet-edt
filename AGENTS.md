# Consignes pour les assistants (Claude Code, Codex, autres)

Ce fichier est la source unique des consignes. `CLAUDE.md` ne fait que l'importer.
Si vous modifiez une règle, modifiez-la ici.

## Le projet

Générateur automatique d'emplois du temps pour l'ISEP. Projet scolaire 2026-2027,
réalisé par une équipe de plusieurs étudiants, chacun avec son propre assistant.
La spécification fonctionnelle est dans `docs/spec.md` (version texte) et
`docs/Fonctionnement du projet.docx` (original). **C'est une ébauche : certains
détails peuvent encore changer.** En cas de doute, demander plutôt que supposer.

Deux principes non négociables issus de la spec :

1. L'application ne supprime jamais une contrainte pour réussir à produire un
   emploi du temps. Si aucune solution n'existe, elle le dit clairement.
2. Les contraintes obligatoires (dures) et les préférences (souples) seront
   distinguées. La première version ne traite que les contraintes dures.

## Cadre du cours (module PTS Informatique, ISEP)

Le détail est dans `docs/planning.md`. L'essentiel :

- **Attendus techniques du module** : interface graphique ou web, base de
  données, gestion de version GitHub, tests unitaires et d'intégration.
- **Notation** : documentation 3 pts, usage de Git/GitHub en équipe 3 pts,
  tests 3 pts, soutenance 3 pts, travail individuel en séance 8 pts.
  Conséquence : **chaque membre commit sous son propre compte GitHub**, avec
  des PR à son nom. Un assistant ne fait pas le travail de tout le monde
  depuis une seule machine.
- **Livrables documentaires** : spécifications fonctionnelles (description,
  fonctionnalités classées obligatoire / utile / optionnelle, diagramme de
  classes UML), spécifications techniques et organisation (stack, lots,
  planning, outils), documentation utilisateur, soutenance.
- **Jalons** : démarrage du code le 05/10/2026, tests unitaires le 12/10,
  tests d'intégration le 02/11, fin des développements le 16/11,
  documentation utilisateur le 23/11, soutenance le 14/12/2026.

## Langue

Code, commentaires, commits, issues et documentation en **français**. Les noms
d'identifiants dans le code peuvent être en anglais si c'est plus lisible
(`room`, `slot`), mais restez cohérents dans un même module.

## Stack (proposition, à confirmer par l'équipe)

- Moteur : Python 3.11+, solveur de contraintes (OR-Tools CP-SAT).
- Interface : application web lancée en local (serveur local + navigateur).
- Stockage : base de données SQLite (attendu « base de données » du module,
  sans serveur à installer).
- Conception orientée objet avec un diagramme de classes UML tenu à jour dans
  `docs/` : le code doit refléter le diagramme, et inversement.
- Tests : `pytest`, tests unitaires par classe/contrainte et tests
  d'intégration (données → génération → résultat).
- Le langage est libre (Python par défaut dans le module). Ne pas ajouter de
  dépendance lourde sans l'écrire dans une issue ou une PR.

## Workflow git

- `main` est protégée : on n'y pousse jamais directement.
- Une branche par tâche, nommée `type/description-courte`
  (ex. `feat/import-csv`, `fix/pause-dejeuner`, `docs/spec-groupes`).
- Une pull request par branche, relue par un autre membre avant fusion.
- Commits courts, à l'impératif, en français : `Ajoute la contrainte de capacité des salles`.
- Ne jamais committer de secrets, de fichiers `~$*.docx`, ni d'environnements virtuels.

## Conventions de code

- Formatage automatique : `ruff format` pour Python (config dans `pyproject.toml`
  une fois créé). Ne pas reformater des fichiers qu'on ne modifie pas.
- Tests dans `tests/`, lancés avec `pytest`. Toute contrainte du solveur a au
  moins un test qui vérifie qu'elle est respectée et un qui vérifie que
  l'échec est signalé quand elle est impossible.
- Pas de fonctionnalité sans test minimal.

## Structure prévue

```
docs/        spécification et documentation
src/edt/     moteur de génération (modèle, contraintes, solveur)
web/         interface web locale
tests/       tests automatisés
```

Cette structure sera ajustée quand la stack sera confirmée.

## Ce qu'un assistant ne fait pas seul

- Modifier la spec sur le fond (`docs/spec.md`) : proposer dans une issue ou une PR.
- Fusionner une PR.
- Changer la stack ou ajouter une dépendance majeure.
