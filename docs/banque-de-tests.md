# Banque de tests

Ce document décrit la banque de tests du projet : comment elle est organisée,
comment la lancer en entier ou par lot, les conventions à respecter pour y
ajouter un test, et l'inventaire des cas limites couverts. Il est destiné aux
développeurs de l'équipe ; il se complète à chaque campagne.

## Lancer les tests

```bash
python -m pytest                          # toute la banque (quelques minutes)
python -m pytest -m "not perf"            # sans les essais de charge : ce que lance la CI
python -m pytest -m cas_limite            # la banque des cas limites seule
python -m pytest -m perf                  # les essais de charge (jeux moyen et grand)
python -m pytest tests/solveur            # un lot
python -m pytest tests/socle/test_regles.py -k samedi   # un fichier, un mot du nom
```

Un test porte le marqueur `cas_limite` quand il protège contre un cas limite
ou une panne plausible relevés par une campagne ; sa docstring commence par
« Cas : … Risque : … » et dit ce qui casserait sans lui.

## Organisation

| Dossier | Lot | Ce qu'il couvre |
|---|---|---|
| `tests/socle/` | socle | domaine (règles, paramètres, dépliage, calendrier), base, services transverses, ligne de commande, cycle de vie du serveur |
| `tests/solveur/` | solveur | préparation, modèle CP-SAT, salles, moteur, instanciation et replacement des séances datées, service de génération |
| `tests/verification/` | vérificateur | règles du catalogue et règles personnalisées, couche datée, indicateurs, services de vérification |
| `tests/web_donnees/` | écrans de données | écrans génériques, règles personnalisées, paramètres, jeux, suppression, concurrence |
| `tests/web_edt/` | page Générer et emploi du temps | calculs, suivi, grille, retouches, À traiter, accueil |
| `tests/echange/` | import/export | gabarit, import XLSX et CSV, rapport, exports, feuille des règles |
| `tests/synthese/` | générateur de données | jeux petit, moyen, grand, témoin, règles d'exemple |
| `tests/exploitation/` | exploitation | scripts de déploiement, unité systemd, nginx, commandes d'administration |

## Conventions

- Noms en français, qui disent le cas (`test_une_regle_hors_des_heures_d_ouverture_est_refusee`).
- Un test vérifie un comportement observable, jamais une implémentation.
- Déterministe : pas d'horloge, pas de dépendance à l'ordre des tests ni à une
  solution du moteur qui n'est pas forcée (la fabrique de `tests/solveur/fabrique.py`
  permet de forcer un placement).
- Rapide : moins de deux secondes, sinon marqué `perf`.
- Base dans un dossier temporaire par test (fixtures `environnement`, `base`,
  `jeu_minimal` de `tests/conftest.py`) ; jamais la base de travail.
- Un défaut trouvé par un test se corrige dans le même lot ; s'il appartient à
  un autre lot, le test reste en `xfail(strict=True)` avec la raison, jusqu'à la
  correction.

## Inventaire des cas limites

À compléter à la fin de la campagne du 2 novembre 2026 : un tableau par lot
(cas, risque, test qui le couvre, état : couvert, corrigé, manuel, écarté).
