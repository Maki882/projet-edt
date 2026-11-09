# Tests d'intégration

Six scénarios courts qui font tourner **l'application entière**, comme un
utilisateur s'en servirait, sur une vraie base SQLite temporaire. Version
complète (stratégie de test, sorties réelles, limites) :
[`DOCUMENTATION.md`](DOCUMENTATION.md).

## 1. Unitaire ou intégration : quelle différence ?

Un **test unitaire** vérifie une seule pièce, isolée : « le calcul des jours
fériés donne bien le 11 novembre ». Un **test d'intégration** assemble plusieurs
pièces réelles et vérifie qu'elles fonctionnent *ensemble* : « un calcul lancé
depuis le site produit un emploi du temps que le vérificateur accepte et que
l'export restitue en entier ».

```
              /\
             /  \        intégration : 6 scénarios (ce dossier)
            /----\       quelques secondes chacun, toute la chaîne
           /      \
          /--------\     cas limites : 548 tests (-m cas_limite)
         /          \    pannes plausibles, saisies piégées
        /------------\
       /              \  unitaires : plus de 1 100 tests (tests/<lot>/)
      /________________\ une pièce à la fois, très rapides
```

La chaîne traversée par un scénario d'intégration :

```
 navigateur simulé (TestClient)       ou     ligne de commande (CliRunner)
            |  requête HTTP                         |  python -m edt ...
            v                                       v
   routes web (FastAPI, src/edt/web/routes)   commandes (src/edt/cli.py)
            \                                       /
             v                                     v
                services (src/edt/services) : génération, règles,
                import/export, retouches, jeux fictifs
                    |                         |
                    v                         v
          solveur OR-Tools CP-SAT      vérificateur indépendant
           (src/edt/solveur)            (src/edt/verification)
                    \                         /
                     v                       v
                 base SQLite temporaire (SQLAlchemy 2)
```

Rien n'est simulé : ni le solveur, ni le vérificateur, ni la base. La seule
chose coupée est le « préchauffage » des écrans (un fil d'arrière-plan qui
survivrait à la base temporaire du test, voir `conftest.py`).

## 2. Lancer les tests

```sh
python -m pytest tests/integration -v      # ce dossier seulement
python -m pytest -m integration -v         # tous les tests marqués « integration »
python -m pytest -m "not perf"             # toute la banque, comme la CI (intégration comprise)
```

## 3. Les six scénarios

Durées mesurées le 9 novembre 2026 sur le PC de développement (Windows 11,
Python 3.11) : 6 tests en 9 à 13 secondes au total, chargement compris.

| Fichier | Ce qu'il assemble | Ce qu'il prouve | Durée |
|---|---|---|---|
| `test_parcours_web.py` | site web, génération, solveur, vérificateur, exports CSV et iCal | un calcul lancé depuis le site aboutit à une version active, affichée, valide et exportée en entier | 1,9 à 2,0 s |
| `test_parcours_ligne_de_commande.py` | commandes `init-db`, `jeu-minimal`, `generer`, `verifier`, `exporter-edt` | les commandes s'enchaînent sur la même base sans le site | 1,0 s |
| `test_aller_retour_donnees.py` | export web du classeur, lecture, import dans une base neuve | le format d'échange est réimportable sans perte (salles, enseignants, groupes, matières, besoins) | 0,4 à 2,0 s |
| `test_regle_personnalisee.py` | service des règles, solveur, vérificateur | une règle saisie (« A1-G1 ne travaille pas le lundi ») est imposée puis contrôlée | 0,5 s |
| `test_retouche_manuelle.py` | calcul, route web d'annulation, base, vérificateur | une séance annulée à la main laisse l'emploi du temps cohérent | 1,0 s |
| `test_jeu_fictif_realiste.py` | générateur de données fictives, solveur, vérificateur | un établissement fictif plus riche (jeu « petit ») est calculé sans anomalie bloquante | 0,4 à 1,7 s |

Aucun test ne dépasse 30 secondes : aucun n'est marqué `perf`, tous tournent
dans la CI.

Les tests ne vérifient jamais *où* le solveur a placé un cours (cela peut
changer d'une machine à l'autre) mais des **propriétés** : une version active
existe, zéro anomalie bloquante, les comptes sont égaux, la règle est respectée.

## 4. Ce que ces tests ne font pas

- Pas de vrai navigateur : le HTML est produit et lu, mais ni le JavaScript
  ni HTMX ne sont exécutés, et l'apparence n'est pas contrôlée.
- Pas de site déployé : tout tourne en mémoire, sans serveur, sans réseau,
  sans le VPS (`https://pts.jorund.xyz`).
- Pas de recherche de la meilleure solution : l'optimisation est coupée pour
  rester rapide (on veut un emploi du temps valide, pas le meilleur).
- Pas de gros jeux : les jeux « moyen » et « grand » sont dans les tests de
  charge (`-m perf`).

## 5. Ajouter un scénario en cinq lignes

```python
@pytest.mark.integration
def test_mon_scenario(jeu_minimal, client):
    """Scénario : ...  Étapes : 1. ... 2. ...  Ce que cela prouve : ..."""
    version = calculer(jeu_minimal.semestre_id).version_id  # vrai calcul
    assert client.get(f"/emploi-du-temps?version_id={version}").status_code == 200
```

Créer `test_<nom>.py` dans ce dossier, importer `pytest` et
`from .conftest import calculer`, garder le docstring en trois parties, et
n'asserter que des propriétés.
