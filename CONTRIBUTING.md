# Contribuer

Ce guide dit comment proposer une modification du projet : préparer son
poste, travailler avec Git et GitHub, respecter les conventions de code et
d'écriture, tester. Il s'adresse aux membres de l'équipe et à toute personne
qui reprend le code.

## 1. Préparer son poste

Il faut Python 3.11 ou plus récent et Git. Depuis la racine du dépôt :

```sh
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]" -c contraintes.txt   # Linux et macOS : .venv/bin/python
```

`contraintes.txt` fixe la version exacte de chaque paquet, celle que le
contrôle automatique teste ; `pyproject.toml` n'en donne que les bornes. Pour
changer une version : [docs/deploiement.md](docs/deploiement.md), section
« Versions des dépendances ».

Après activation de l'environnement (`.venv\Scripts\activate` sous Windows,
`source .venv/bin/activate` ailleurs) :

```sh
python -m edt init-db
python -m edt jeu --taille moyen
python -m edt doctor
```

Les dépendances sont déclarées dans `pyproject.toml`. Une dépendance nouvelle
se discute d'abord avec l'équipe, dans une tâche ou une pull request.

## 2. Travailler avec Git et GitHub

Aucune protection de branche n'est activée sur ce dépôt : GitHub
n'empêche pas techniquement de pousser sur `main`. Les règles suivantes sont
donc des règles d'équipe :

- **Aucun envoi direct sur `main`.** Tout passe par une pull
  request.
- **Une branche par tâche**, créée depuis `main` à jour, nommée
  `type/description-courte` : `feat/import-csv`, `fix/pause-dejeuner`,
  `docs/guide-utilisateur`, `test/cas-limites-import`.
- **Une tâche GitHub par travail** (gabarit « Tâche »), avec son classement
  obligatoire, utile ou optionnelle. La pull request la ferme par
  « Closes #n » : GitHub ne reconnaît que les mots-clés anglais, et ne ferme
  l'issue qu'à la fusion dans `main`.
- **Des commits petits et par sujet**, en français, à l'impératif :
  `Ajouter la contrainte de capacité des salles`.
- **Chaque membre commite et ouvre ses pull requests sous son propre
  compte.** Un commit n'est attribué qu'à la personne qui l'a écrit.
- **Une pull request est relue par un autre membre** avant sa fusion. Le
  relecteur lance les vérifications de la section « Comment vérifier » du
  gabarit, commente ou approuve, puis fusionne. Une pull request reste courte
  (une tâche, quelques centaines de lignes) pour rester relisible.
- **L'intégration continue doit être verte** avant la fusion. Elle tourne
  sur chaque pull request et sur chaque envoi vers `main`
  (`.github/workflows/ci.yml`), sous Python 3.11, 3.13 et 3.14 : lint, format,
  contrôle du rangement des tests, tests unitaires, tests d'intégration (hors
  tests de charge).
- Jamais de secret (clé, mot de passe, jeton, adresse de serveur), de base de
  données, de journal, d'environnement virtuel ni de fichier `~$*` dans un
  commit : `.gitignore` les écarte.

Ce qui a été appliqué dans ce projet est décrit dans le
[livret 2](docs/livrets/2-specifications-techniques-et-organisation.md),
sections 2.5 et 2.6. En résumé : après les premiers commits de mise en place, les
changements sont passés par une branche et une pull request vérifiée par
l'intégration continue, à l'exception de deux commits déposés directement sur `main` le
23/11/2026 ; les anomalies ouvertes sont suivies par des tâches GitHub
depuis la recette de la version 1.0.0, comme les issues ouvertes par l'équipe
d'agents ([docs/organisation-agents.md](docs/organisation-agents.md)) ; aucun
jalon n'est ouvert, aucune approbation de pull request n'est enregistrée, et presque
toutes les pull requests ont été ouvertes et fusionnées par le même compte.
Les règles de relecture, de tâches et de commits sous le compte de chacun
restent celles à suivre pour la suite.

## 3. Conventions de code

L'organisation du code est décrite dans
[docs/architecture.md](docs/architecture.md). Les règles à connaître :

- **Couches** : interfaces (web, ligne de commande) → services → logique pure
  (domaine, solveur, vérification) → base. Une fonctionnalité est d'abord une
  **fonction de service typée**, puis une route web et une commande.
- Le vérificateur et le solveur travaillent sur l'instantané
  (`edt.domaine.instantane`), jamais sur l'ORM ; les codes des contraintes
  viennent du catalogue (`edt.domaine.contraintes`).
- **Rien n'échoue en silence** : pas de `except: pass`, pas de valeur de
  repli muette ; les services lèvent des `ErreurEDT` dont le message nomme la
  cause et le remède ; chaque module journalise avec
  `logging.getLogger(__name__)`.
- **Écritures en base** : une transaction qui lit puis écrit s'ouvre par
  `edt.db.moteur.transaction_immediate(session)` avant sa première requête.
  Les requêtes web d'écriture le font déjà (`edt.web.dependances`). Un
  identifiant venu de l'extérieur passe par
  `edt.db.moteur.identifiant_valide`.
- **Simplicité** : pas d'abstraction inutile, pas de duplication, pas de code
  mort (il se supprime, il ne se commente pas).
- **Format** : `ruff format` et `ruff check` (configuration dans
  `pyproject.toml`, lignes de 100 caractères). Ne reformatez pas un fichier
  que vous ne modifiez pas.
- **Modèle de données** : le diagramme de classes de
  [docs/modele-donnees.md](docs/modele-donnees.md) suit le code ; une table
  ou une colonne ajoutée y est décrite dans la même pull request.

### Ajouter une commande

Créez un module dans `src/edt/commandes/` qui expose `commandes`, un
`typer.Typer` ; ses commandes s'ajoutent à `python -m edt` sans modifier
`cli.py`. La commande appelle un service et affiche son résultat. Exemple
complet : [docs/architecture.md](docs/architecture.md#ajouter-une-commande).

### Ajouter un écran

Créez un module dans `src/edt/web/routes/` qui expose `routeur` (un
`APIRouter`) et `MENU` (ses entrées de menu), et ses gabarits dans
`src/edt/web/templates/<écran>/` ; l'écran est découvert sans modifier
`web/app.py`. Une entité de l'établissement se décrit une fois dans
`edt.services.referentiels` et reçoit ses écrans génériques. Exemples et
règles : [docs/architecture.md](docs/architecture.md#ajouter-un-écran) et
[docs/interface.md](docs/interface.md).

## 4. Tests

```sh
python -m pytest -m "not perf"     # avant chaque pull request
python -m ruff check .
python -m ruff format --check .
```

- **Où ranger un test.** `tests/unitaires/<composant>/` pour un test qui
  exerce un module seul, par ses fonctions Python (au besoin sur une base
  temporaire) ; `tests/integration/<composant>/` pour un test qui passe par
  une requête HTTP, la ligne de commande, un autre processus ou un calcul
  complet (`lancer_calcul`). Les aides partagées vont dans
  `tests/outils/<composant>/`. `tests/test_rangement.py` vérifie ce
  classement ; la règle complète est dans [tests/README.md](tests/README.md).
  Ajoutez de préférence un test à un fichier existant.
  Un nouveau fichier de tests commence par une docstring de module dont la
  première phrase dit ce qu'il vérifie. Après l'ajout ou le retrait d'un
  test, `python -m tests.outils.classement --ecrire` met à jour les nombres
  et les tableaux de `tests/README.md`, `tests/unitaires/README.md` et
  `tests/integration/README.md` ; `tests/test_rangement.py` échoue tant
  qu'ils ne sont pas à jour.
- **Un test appelle le vrai code et doit pouvoir échouer** : avant de le
  garder, cassez volontairement le code qu'il protège et constatez qu'il
  passe au rouge.
- Toute règle d'un emploi du temps a au moins un test qui vérifie qu'elle est
  respectée et un test qui vérifie que son impossibilité est signalée.
- **Déterminisme** : aucun test ne dépend de l'heure réelle, du hasard, de
  l'ordre des tests, de la charge de la machine ni de la solution exacte du
  solveur.
- **Durée** : moins de 2 s par test, sinon le marqueur `perf` (test de
  charge, exclu de l'intégration continue).
- **Cas limites** : marqueur `@pytest.mark.cas_limite` et docstring
  « Cas : <scénario>. Risque : <ce qui casserait sans le test>. ». La banque
  est décrite dans [docs/banque-de-tests.md](docs/banque-de-tests.md).
- Un test ne se supprime pas, ne s'affaiblit pas et ne se saute pas pour
  faire passer la suite : un test faux se corrige, et la pull request dit
  pourquoi.

## 5. Écrire

Ces règles valent pour les commentaires, les docstrings, les textes de
l'interface et la documentation.

- **Tout texte décrit le logiciel tel qu'il est.** Un commentaire dit ce que
  fait le code et pourquoi (la contrainte, le piège à éviter) ; il ne raconte
  pas l'historique des modifications : Git le garde. Pas de TODO ni de
  « provisoire » : ce qui manque s'écrit comme une limite (« Limite : … »).
- **Ton** : présent, phrases courtes, impersonnel dans la documentation
  technique ; vouvoiement et impératif dans le guide de l'utilisateur.
- **Aucun nom de personne** (hors la liste de l'équipe dans le README, les
  pages de garde des livrets et la partie « Organisation » du livret 2),
  aucun numéro de pull request ou de commit ni nom de compte (hors le
  livret 2, section 2), aucun chemin de poste personnel, aucune adresse de
  serveur, aucun nom de clé. Dans un exemple : `edt.example.org`,
  `203.0.113.10`, `/chemin/vers/le/depot`.
- **Interface** : vocabulaire de la scolarité, pas de jargon technique, un
  mot par notion ; un message d'erreur dit ce qui s'est passé et quoi faire.
- **Documentation** : un document de `docs/` par sous-système, un seul titre
  de niveau 1, liens relatifs, commandes exécutables telles quelles depuis la
  racine, schémas en Mermaid. La carte est dans [docs/README.md](docs/README.md).
- **Textes contrôlés par des tests** : la section « Feuilles » de
  `docs/import-export.md` est générée et comparée au code ; la description
  des tables de `docs/modele-donnees.md` et les procédures de
  `docs/deploiement.md` sont vérifiées par des tests. Modifiez-les avec le
  code correspondant.

## 6. Ce qui se décide en équipe

- Un changement de fond sur ce que fait l'application (livret 1,
  [docs/decisions.md](docs/decisions.md)) : proposé dans une tâche ou une
  pull request, avant le code.
- Un changement de la pile technique ou une dépendance nouvelle.
- Une fusion, une publication ou un déploiement sur le serveur de
  démonstration ([docs/deploiement.md](docs/deploiement.md)).
