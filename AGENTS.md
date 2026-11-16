# Consignes pour les assistants de programmation

Ce fichier est la source unique des consignes données aux assistants de
programmation ; `CLAUDE.md` l'importe. Les conventions du projet (Git, code,
tests, écriture) sont dans [CONTRIBUTING.md](CONTRIBUTING.md) et s'appliquent
en entier : ce fichier n'ajoute que ce qui est propre au travail avec un
assistant.

## Le projet en bref

Générateur automatique d'emplois du temps pour une école d'ingénieurs.
Python 3.11 ou plus, FastAPI, Jinja2 et HTMX (aucun outil Node),
SQLAlchemy 2 et SQLite, OR-Tools CP-SAT, Typer, pytest, ruff. Tout est en
français : code, commentaires, messages, documentation, commits.

- Ce que fait l'application et ses choix : [docs/decisions.md](docs/decisions.md)
  et le [livret 1](docs/livrets/1-specifications-fonctionnelles.md).
- Organisation du code : [docs/architecture.md](docs/architecture.md).
- Carte de la documentation : [docs/README.md](docs/README.md).

Deux principes non négociables :

1. L'application ne supprime jamais une règle pour réussir à produire un
   emploi du temps. Si aucune solution n'existe, elle le dit clairement.
2. Toutes les données sont fictives. Aucune donnée réelle n'entre dans le
   dépôt.

## Méthode de travail

- **Secrets** : ne jamais lire ni écrire un secret (clé, mot de passe,
  jeton, fichier `.env`, `deploy/serveur.env`), ni le recopier dans un
  message, un journal ou un rapport.
- **Suppressions** : ne supprimer que ce que l'on a créé soi-même, par son
  chemin exact ; jamais de suppression par motif ni de nettoyage large.
- **Relire avant de refaire** : lire le code et la documentation existants
  avant d'écrire ; réutiliser plutôt que dupliquer.
- **Changements limités** : modifier les lignes nécessaires, pas réécrire un
  fichier ; ne glisser aucun changement de comportement dans un nettoyage.
- **Vérifications proportionnées au risque**, complètes pour ce qui touche
  aux données et à ce que voient les utilisateurs (pages, messages,
  exports).
- **Mesurer avant d'affirmer** : un nombre, une durée ou un résultat de test
  cité vient d'une commande exécutée ; le constaté se distingue du supposé.
- **Tests** : un test appelle le vrai code et doit pouvoir échouer ; le
  casser volontairement une fois pour le constater.
- **Rien n'échoue en silence**, ni dans le code ni dans le compte rendu : un
  échec, une étape sautée ou un doute se disent.
- **Environnement propre** : pas de processus laissé en marche, pas de code
  mort, pas de fichier temporaire dans le dépôt ; une base d'essai et ses
  journaux vont dans un dossier hors du dépôt (`EDT_DB_URL`, `EDT_LOG_DIR`).
- **Décisions de l'équipe** : un assistant ne fusionne pas, ne pousse pas sur
  `main` et ne déploie pas sans la décision d'un membre de l'équipe. Il ne
  change ni la pile technique ni le fond de la spécification de lui-même.

## Travail délégué à plusieurs assistants

- **Un brief autonome par tâche** : chemins exacts, fichiers autorisés,
  interdits, décisions déjà prises, preuve de réussite attendue, forme du
  compte rendu.
- **Un fichier n'est modifié que par un assistant à la fois** ; chacun
  travaille dans sa copie de travail et sur sa branche.
- **Vérifier le travail réel, pas le compte rendu** : relire le diff, relancer
  les tests.

## Compte rendu

Court, en phrases simples : ce qui est fait, ce qui ne l'est pas et pourquoi,
les commandes de vérification et leur résultat, ce qui reste à décider.
