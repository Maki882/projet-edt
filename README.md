# Projet EDT — générateur d'emplois du temps ISEP

Application qui construit automatiquement les emplois du temps de l'ISEP à partir
des cours, classes, enseignants, salles et contraintes fournis, en garantissant
qu'aucune contrainte obligatoire n'est violée.

Projet scolaire 2026-2027, réalisé en équipe. Toutes les données sont fictives.

## Documentation

- [Spécification fonctionnelle](docs/spec.md) (ébauche d'origine)
- [Décisions de cadrage de la V1](docs/decisions.md) : fait autorité pour le code
- [Architecture](docs/architecture.md) : couches, extension, contrats partagés
- [Modèle de données](docs/modele-donnees.md) : diagramme de classes et tables
- [Consignes de travail et conventions](AGENTS.md)

## Installer

Il faut Python 3.11 ou plus récent. Depuis la racine du dépôt :

```sh
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"      # Linux et macOS : .venv/bin/python
.venv/Scripts/python -m edt init-db
```

La troisième commande crée la base `data/edt.db` avec son schéma et ses données
par défaut (grille horaire, types de séance, calendrier de l'année). Les
exemples suivants supposent l'environnement virtuel activé
(`.venv\Scripts\activate` sous Windows, `source .venv/bin/activate` ailleurs).

## Lancer

```sh
python -m edt jeu-minimal      # facultatif : charge un petit établissement fictif
python -m edt serve            # puis ouvrir http://127.0.0.1:8000
```

Toute fonctionnalité est aussi une commande :

| Commande | Rôle |
|---|---|
| `python -m edt --help` | liste des commandes |
| `python -m edt init-db [--reset]` | crée la base ; `--reset` efface tout d'abord |
| `python -m edt jeu-minimal` | charge un tout petit jeu de données cohérent |
| `python -m edt serve [--port 8000] [--reload]` | lance le serveur web |
| `python -m edt doctor` | vérifie l'installation (Python, OR-Tools, base, journaux, port) |
| `python -m edt version` | affiche la version |

Réglages par variables d'environnement, toutes facultatives : `EDT_DB_URL`
(`sqlite:///data/edt.db`), `EDT_LOG_DIR` (`logs`), `EDT_HOST` (`127.0.0.1`),
`EDT_PORT` (`8000`), `EDT_ENV` (`dev` ou `prod`), `EDT_LOG_LEVEL` (`INFO`).
Les journaux sont dans `logs/edt.log`, et un fichier par calcul dans
`logs/generations/`. En production, le serveur se lance avec
`uvicorn edt.web.asgi:app`.

## Tester

```sh
python -m pytest                    # tous les tests
python -m pytest -m "not perf"      # sans les tests de charge (ce que lance la CI)
python -m ruff check .              # lint
python -m ruff format --check .     # format
```

Les tests créent leur base et leurs journaux dans des dossiers temporaires :
ils ne touchent jamais `data/` ni `logs/`.

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
python -m unittest discover -s tests -p "test_conflits.py" -v
```

Le modèle de ce module est décrit dans [le diagramme de classes](docs/verification-conflits.md).

Ce script lit une base au format du premier essai (table `cours`), dont le
fichier d'exemple `emploi_du_temps.db` n'est plus dans le dépôt. Il est conservé
comme point de départ du vérificateur complet (`edt.verification`), qui
travaille sur le modèle de données actuel.

## Contribuer au code

1. Créer une branche depuis `main` : `feat/ma-fonctionnalite`.
2. Committer en français, à l'impératif.
3. Ouvrir une pull request et demander une relecture à un autre membre.

Avant d'écrire du code, lire [`docs/architecture.md`](docs/architecture.md) :
une commande s'ajoute dans `src/edt/commandes/`, un écran dans
`src/edt/web/routes/`, sans modifier les fichiers des autres.
