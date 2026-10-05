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
- [Solveur](docs/solveur.md) : formulation, objectif, séances datées, mesures, limites
- [Consignes de travail et conventions](AGENTS.md)

## Installer

Il faut Python 3.11 ou plus récent. Depuis la racine du dépôt :

```sh
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"      # Linux et macOS : .venv/bin/python
.venv/Scripts/python -m edt init-db
```

La troisième commande crée la base `data/edt.db` avec son schéma et ses données
par défaut (grille horaire, types de séance, calendrier de l'année). Il n'y a
pas de migrations : si une mise à jour a changé les colonnes d'une table déjà
créée, `init-db` s'arrête sans rien modifier, liste les écarts, et il faut
relancer `python -m edt init-db --reset`, qui recrée la base et efface les
données. Les exemples suivants supposent l'environnement virtuel activé
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
| `python -m edt verifier-schema` | compare la base au modèle : code 0 à jour, 1 périmée (écarts listés), 2 inaccessible |
| `python -m edt jeu-minimal` | charge un tout petit jeu de données cohérent |
| `python -m edt pre-verifier [--semestre 1]` | dit, avant tout calcul, ce qui rend un emploi du temps impossible ou difficile (code 1 si le calcul serait refusé) |
| `python -m edt generer [--semestre 1] [--temps-limite 120] [--optimiser] [-d PAUSE]` | génère l'emploi du temps d'un semestre, le contrôle, l'enregistre et produit ses séances datées (code 1 si le calcul n'aboutit pas) |
| `python -m edt instancier [--version 4]` | refait les séances datées d'une version et replace celles qui tombent un jour fermé |
| `python -m edt serve [--port 8000] [--reload]` | lance le serveur web |
| `python -m edt doctor` | vérifie l'installation (Python, OR-Tools, base, schéma, journaux, port) |
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

## Vérifier un emploi du temps

Le vérificateur liste les anomalies d'un emploi du temps enregistré : salles,
enseignants ou groupes occupés deux fois, pause, trajet entre campus,
déjeuner, grille horaire, jours de cours, salle adaptée, disponibilités,
maxima horaires, volume, verrous et jours fermés. Il ne modifie rien.

```sh
python -m edt verifier                     # semaine type de la version active du semestre en cours
python -m edt verifier --version 4 --datees --du 2026-11-02 --au 2026-11-08
python -m edt verifier --semestre 1 -c SALLE_UNIQUE --json
```

Codes de sortie : `0` aucune erreur, `1` au moins une erreur, `2` vérification
impossible (version introuvable, code inconnu, rien à vérifier...). Chaque
anomalie a un code, une gravité (erreur, avertissement, information) et un
message en clair.

Ce module généralise le premier vérificateur de chevauchements écrit par
l'équipe. Tests : `python -m pytest tests/verification` (charge : `-m perf`).
Règles, indicateurs et diagramme de classes : [docs/verification.md](docs/verification.md).

## Contribuer au code

1. Créer une branche depuis `main` : `feat/ma-fonctionnalite`.
2. Committer en français, à l'impératif.
3. Ouvrir une pull request et demander une relecture à un autre membre.

Avant d'écrire du code, lire [`docs/architecture.md`](docs/architecture.md) :
une commande s'ajoute dans `src/edt/commandes/`, un écran dans
`src/edt/web/routes/`, sans modifier les fichiers des autres.
