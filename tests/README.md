# Tests

Les tests du projet sont rangés en deux familles : **tests unitaires**
(`tests/unitaires/`) et **tests d'intégration** (`tests/integration/`). Chaque
famille est découpée par composant de l'application. Le classement suit une
règle unique, contrôlée automatiquement.

## 1. Unitaire ou intégration : la règle

La règle ci-dessous fait foi. Elle est appliquée par
`tests/outils/classement.py` et vérifiée par `tests/test_rangement.py`.

- Test d'INTÉGRATION : il traverse plusieurs couches de l'application. Soit il
  passe par une interface (requête HTTP par le client de test : `TestClient`,
  `AsyncClient`, `ASGITransport`, y compris par les fixtures `client` ou
  `site` ; ligne de commande par `CliRunner`, y compris par une fixture
  `lanceur` ; autre processus : `subprocess`, `multiprocessing`), soit il
  enchaîne tout le calcul par le service de génération (`lancer_calcul`,
  `attendre_calcul`, y compris par une aide ou une fixture).
- Test UNITAIRE : tout le reste. Il exerce un seul module par ses fonctions
  Python, au besoin sur une base SQLite temporaire ; le solveur appelé
  directement sur des données en mémoire reste unitaire.

Exemple de test unitaire (`tests/unitaires/socle/test_calendrier.py`) : une
fonction du calendrier, appelée directement.

```python
def test_feries_d_une_periode_a_cheval_sur_deux_annees():
    feries = jours_feries_entre(dt.date(2026, 12, 1), dt.date(2027, 1, 31))
    assert [jour for jour, _ in feries] == [dt.date(2026, 12, 25), dt.date(2027, 1, 1)]
```

Exemple de test d'intégration (`tests/integration/socle/test_web.py`) : une
vraie requête HTTP traverse l'application web, ses routes et la base.

```python
def test_accueil_reste_lisible_sans_base_initialisee(environnement):
    client = TestClient(module_app.creer_app())

    reponse = client.get("/")
    assert reponse.status_code == 200
    assert "pas encore initialisée" in reponse.text
```

Un même fichier peut exister des deux côtés (par exemple
`tests/unitaires/socle/test_web.py` et `tests/integration/socle/test_web.py`) :
le premier appelle des fonctions du module web, le second passe par HTTP.

## 2. Arborescence

```
tests/
├── conftest.py            fixtures communes (base temporaire, jeu minimal, client web)
│                          et pose des marqueurs « unitaire » / « integration »
├── test_rangement.py      contrôle automatique du rangement (voir section 6)
├── README.md              ce document
├── unitaires/             un module exercé seul, par ses fonctions Python
│   ├── README.md
│   ├── socle/   solveur/   verification/   web_donnees/
│   └── web_edt/ echange/   synthese/       exploitation/
├── integration/           plusieurs couches assemblées (HTTP, commande, processus, calcul complet)
│   ├── README.md          présentation courte, point d'entrée : les six parcours
│   ├── DOCUMENTATION.md   stratégie de test et détail des parcours
│   ├── parcours/          six scénarios de bout en bout
│   ├── socle/   solveur/   verification/   web_donnees/
│   └── web_edt/ echange/   synthese/       exploitation/
└── outils/                aides et fixtures partagées (aucun test)
    ├── classement.py      classeur unitaire / intégration
    ├── parcours/          aides des six parcours
    └── socle/ solveur/ verification/ web_donnees/ web_edt/ echange/ synthese/ exploitation/
```

Chaque dossier de composant peut avoir son `conftest.py` (fixtures propres à
ce côté). Les dossiers contiennent un `__init__.py`.

## 3. Composants et nombres de tests

Nombres mesurés sur le dépôt. « Fonctions » compte les fonctions `test_*` ;
« cas » compte les tests collectés par pytest (une fonction paramétrée donne
plusieurs cas).

| Composant | Ce qu'il vérifie | Unitaires (fonctions / cas) | Intégration (fonctions / cas) |
|---|---|---|---|
| `socle` | domaine (règles, paramètres, dépliage, calendrier), base et schéma, configuration, journaux, services transverses, ligne de commande, cycle de vie du serveur | 183 / 316 | 64 / 80 |
| `solveur` | préparation du problème, modèle CP-SAT, salles, moteur, séances datées, pré-vérification, service de génération | 188 / 207 | 47 / 51 |
| `verification` | vérificateur indépendant : règles du catalogue et règles personnalisées, semaine type et séances datées, indicateurs, services de vérification | 231 / 271 | 6 / 10 |
| `web_donnees` | écrans de l'espace « Données » : formulaires génériques, besoins, règles, paramètres, jeux, suppression, concurrence | 11 / 11 | 116 / 142 |
| `web_edt` | page Générer et emploi du temps : calculs, suivi, grille, retouches, À traiter, accueil | 19 / 19 | 119 / 145 |
| `echange` | import et export : gabarit, lecture XLSX et CSV, rapport d'import, exports CSV et iCal, feuille des règles | 139 / 206 | 32 / 38 |
| `synthese` | générateur de données fictives : jeux petit, moyen et grand, emploi du temps témoin, règles d'exemple | 59 / 93 | 6 / 8 |
| `exploitation` | scripts de déploiement, unité systemd, configuration nginx, commandes d'administration (sauvegarde, diagnostic) | 30 / 57 | 23 / 38 |
| `parcours` | six scénarios de bout en bout : web, ligne de commande, aller-retour des données, règle personnalisée, retouche, jeu fictif | — | 6 / 6 |
| **Total** | | **860 / 1 180** | **419 / 518** |

S'y ajoute le contrôle du rangement (`tests/test_rangement.py`, 6 fonctions,
12 cas). Total collecté : 1 710 cas, dont 27 tests de charge (`perf`) : 24
unitaires et 3 d'intégration.

Recalculer les nombres de fonctions par côté et par composant :

```sh
python -m tests.outils.classement
```

Recalculer les nombres de cas collectés :

```sh
python -m pytest --collect-only -q tests/unitaires
python -m pytest --collect-only -q tests/integration
```

## 4. Lancer les tests

Depuis la racine du dépôt :

```sh
python -m pytest                              # tout (tests de charge compris)
python -m pytest -m "not perf"                # tout sauf les tests de charge
python -m pytest tests/unitaires              # les tests unitaires
python -m pytest tests/integration            # les tests d'intégration
python -m pytest -m unitaire                  # idem par marqueur
python -m pytest -m integration               # idem par marqueur
python -m pytest tests/integration/parcours   # les six parcours de bout en bout
python -m pytest tests/unitaires/solveur      # un composant, un côté
python -m pytest -m cas_limite                # les cas limites (docs/banque-de-tests.md)
python -m pytest -m perf                      # les tests de charge (longs)
python -m pytest tests/test_rangement.py      # le contrôle du rangement
```

Les marqueurs `unitaire` et `integration` sont posés d'après le dossier par
`tests/conftest.py` ; un test n'a pas à les porter lui-même. Ils se combinent
avec les autres : `-m "integration and cas_limite"`, `-m "unitaire and not perf"`.

L'intégration continue (`.github/workflows/ci.yml`) enchaîne trois étapes :
contrôle du rangement, tests unitaires, tests d'intégration ; les tests de
charge en sont exclus.

## 5. Où ranger un nouveau test

1. Appliquer la règle de la section 1. Si le test utilise, directement ou par
   une fixture ou une aide, un client HTTP, `CliRunner`, un autre processus ou
   `lancer_calcul` / `attendre_calcul`, il va dans `tests/integration/`.
   Sinon, il va dans `tests/unitaires/`.
2. Choisir le dossier du composant concerné (`socle`, `solveur`...). Un
   scénario de bout en bout qui traverse plusieurs composants va dans
   `tests/integration/parcours/`.
3. Nommer le fichier `test_<sujet>.py` et la fonction en français, d'après le
   comportement vérifié.

Aucun fichier `test_*.py` n'est accepté hors de `tests/unitaires/` et
`tests/integration/` (sauf `tests/test_rangement.py`).

## 6. Aides partagées

Les fixtures et fonctions d'aide utilisées par plusieurs fichiers vivent dans
`tests/outils/<composant>/`. Un `conftest.py` les importe par leur nom pour
les rendre disponibles comme fixtures. Un module de tests n'importe jamais un
autre module de tests (`test_*.py`) ni un `conftest.py` : il importe depuis
`tests/outils/`.

Fixtures communes à tous les tests (`tests/conftest.py`) :

- `environnement` (automatique) : base SQLite et journaux dans un dossier
  temporaire propre au test ;
- `base`, `session` : base initialisée et session ouverte dessus ;
- `jeu_minimal` : base chargée du petit jeu de démonstration ;
- `client` : client HTTP de l'application web (fait d'un test un test
  d'intégration).

## 7. Contrôle automatique du classement

`tests/test_rangement.py` vérifie à chaque exécution que :

- chaque test de `tests/unitaires/` est unitaire selon la règle, et chaque
  test de `tests/integration/` est d'intégration ; en cas d'erreur, le message
  nomme le test, la preuve trouvée et le dossier où le déplacer ;
- aucun fichier de tests n'existe hors de ces deux dossiers ;
- aucun module de tests n'importe un autre module de tests ;
- le classeur reconnaît bien chaque forme de preuve (usage direct, par une
  fixture, par une aide du module, par une aide importée) et le cas unitaire.

Le classeur (`tests/outils/classement.py`) lit le code sans l'exécuter. Pour
chaque test, il suit les fixtures (module, `conftest.py` du dossier et des
dossiers parents), les fonctions d'aide du module et les aides importées de
`tests/`. Les noms qui font preuve sont listés dans sa constante `PREUVES`.
