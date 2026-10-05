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

## Stack

Décidée dans `docs/decisions.md` (section 2), qui fait autorité.

- Langage : Python, `requires-python >= 3.11` (3.11 en local, 3.14 sur le serveur).
- Solveur : OR-Tools CP-SAT (9.15 ou plus).
- Base : SQLite via SQLAlchemy 2 (clés étrangères actives, mode WAL). Pas de
  migrations en V1 : `python -m edt init-db --reset` recrée la base.
- Web : FastAPI, gabarits Jinja2, HTMX embarqué dans le dépôt. Aucun outil Node.
- Ligne de commande : Typer, `python -m edt <commande>`. Toute fonctionnalité
  est aussi une commande.
- Tests : `pytest` ; format et lint : `ruff` ; intégration continue GitHub
  Actions sur chaque PR (`.github/workflows/ci.yml`).
- Déploiement : service systemd (uvicorn `edt.web.asgi:app`) derrière nginx.
- Dépendances déclarées dans `pyproject.toml`. Ne pas en ajouter une lourde
  sans l'écrire dans une issue ou une PR.
- Conception orientée objet avec un diagramme de classes UML tenu à jour dans
  `docs/modele-donnees.md` : le code doit refléter le diagramme, et inversement.

Installation : `python -m venv .venv`, puis
`.venv/Scripts/python -m pip install -e ".[dev]"`, puis `python -m edt init-db`.

## Workflow git

- `main` est protégée : on n'y pousse jamais directement.
- Une branche par tâche, nommée `type/description-courte`
  (ex. `feat/import-csv`, `fix/pause-dejeuner`, `docs/spec-groupes`).
- Une pull request par branche, relue par un autre membre avant fusion.
- Commits courts, à l'impératif, en français : `Ajoute la contrainte de capacité des salles`.
- Ne jamais committer de secrets, de fichiers `~$*.docx`, ni d'environnements virtuels.

## Conventions de code

- Formatage automatique : `ruff format` pour Python (config dans `pyproject.toml`).
  Ne pas reformater des fichiers qu'on ne modifie pas.
- Tests dans `tests/`, lancés avec `pytest`. Toute contrainte du solveur a au
  moins un test qui vérifie qu'elle est respectée et un qui vérifie que
  l'échec est signalé quand elle est impossible.
- Pas de fonctionnalité sans test minimal.

## Structure

Le détail est dans `docs/architecture.md`. Architecture en couches, sans
exception : interfaces (web, ligne de commande) → services → logique pure
(domaine, solveur, vérification) → base.

```
docs/                 décisions, architecture, modèle de données, spécification
src/edt/
  config.py           configuration par variables d'environnement EDT_*
  journal.py          configuration unique des journaux
  erreurs.py          hiérarchie des erreurs métier (ErreurEDT)
  cli.py              ligne de commande (découvre edt/commandes/)
  commandes/          un module de commandes par lot
  db/                 moteur SQLite, modèles ORM, données par défaut, jeu minimal
  domaine/            énumérations, heures, calendrier, catalogue des contraintes,
                      paramètres, instantané d'un semestre, dépliage des besoins
  services/           fonctions typées appelées par le web et la ligne de commande
  verification/       vérificateur : liste les anomalies d'un emploi du temps
  solveur/            solveur CP-SAT (contrat partagé, horaires puis salles,
                      séances datées ; ancien moteur provisoire dans provisoire.py)
  synthese/           générateur d'établissements fictifs (petit, moyen, grand)
  echange/            import et export (XLSX, CSV, iCal)
  web/                application FastAPI (découvre web/routes/), gabarits, CSS, HTMX
tests/                tests automatisés : conftest.py et un dossier par lot
deploy/               installation et déploiement sur le serveur (nginx, systemd)
```

Règles à connaître avant d'écrire du code :

- une fonctionnalité existe d'abord comme **fonction de service typée**, puis
  comme route web et comme commande ;
- une commande s'ajoute dans **son** module de `src/edt/commandes/`, un écran
  dans **son** module de `src/edt/web/routes/` : on ne modifie ni `cli.py` ni
  `web/app.py` ;
- le vérificateur et le solveur travaillent sur l'instantané
  (`edt.domaine.instantane`), jamais sur l'ORM ; les codes des contraintes
  viennent du catalogue (`edt.domaine.contraintes`) ;
- rien n'échoue en silence : pas de `except: pass`, pas de valeur de repli
  silencieuse ; les services lèvent des `ErreurEDT`, chaque module journalise
  avec `logging.getLogger(__name__)` ;
- aucun fichier généré dans le dépôt : `data/` et `logs/` sont ignorés, les
  tests écrivent dans des dossiers temporaires.

## Ce qu'un assistant ne fait pas seul

- Modifier la spec sur le fond (`docs/spec.md`) : proposer dans une issue ou une PR.
- Fusionner une PR.
- Changer la stack ou ajouter une dépendance majeure.
