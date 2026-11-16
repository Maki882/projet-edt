# Tests unitaires

Un test unitaire exerce un seul module par ses fonctions Python. Il n'envoie
aucune requête HTTP, ne lance aucune commande par `CliRunner`, aucun autre
processus, et n'enchaîne pas le calcul complet par le service de génération.
Il peut utiliser une base SQLite temporaire. Le solveur appelé directement sur
des données en mémoire reste unitaire. La règle complète et les nombres de
tests sont dans [`tests/README.md`](../README.md).

Un dossier par composant : `socle`, `solveur`, `verification`, `web_donnees`,
`web_edt`, `echange`, `synthese`, `exploitation`. Les aides partagées avec
les tests d'intégration sont dans `tests/outils/<composant>/`.

## Lancer

```sh
python -m pytest tests/unitaires                  # tous les tests unitaires
python -m pytest -m unitaire                      # idem, par marqueur
python -m pytest tests/unitaires -m "not perf"    # sans les tests de charge (comme la CI)
python -m pytest tests/unitaires/solveur          # un composant
```

## Exemple

`tests/unitaires/solveur/test_contraintes.py` : un petit problème construit
en mémoire par la fabrique (`tests/outils/solveur/fabrique.py`), résolu par
le solveur appelé directement, puis contrôlé par l'oracle.

```python
def test_salle_unique_deux_seances_ne_partagent_pas_la_salle():
    f = Fabrique(creneaux=MATINEE)
    f.salle("N101")
    f.besoin([f.groupe("A1")], [f.enseignant()])
    f.besoin([f.groupe("A2")], [f.enseignant()])
    instantane = f.instantane()
    places = verifier_solution(instantane, resoudre(instantane, OPTIONS))
    assert not chevauchent(places["1-1"], places["2-1"])
```
