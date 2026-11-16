## Quoi

<!-- Une ou deux phrases : ce que change cette pull request. -->

## Pourquoi

<!-- Le besoin et la tâche liée (par exemple « Ferme #12 »). -->

## Comment vérifier

<!-- Commandes à lancer ou étapes à suivre dans l'application. -->

## Liste de contrôle

- [ ] `python -m pytest -m "not perf"`, `python -m ruff check .` et `python -m ruff format --check .` passent en local
- [ ] Les tests ajoutés sont rangés selon `tests/README.md` et échouent sans la modification
- [ ] Aucun fichier généré ni secret n'est commité
- [ ] La documentation concernée est à jour (`docs/README.md` dit laquelle)
