# Tests d'intégration

Document du dossier de rendu (module PTS Informatique). Il présente la
stratégie de test du projet EDT et, en détail, les six parcours de bout en
bout du dossier `tests/integration/parcours/`. Le dossier `tests/integration/`
contient aussi les tests d'intégration propres à chaque composant (un
sous-dossier par composant). Résumé visuel pour une présentation orale :
[`tests/integration/README.md`](README.md) ; règle de classement et nombres
de tests : [`tests/README.md`](../README.md).

## 1. Objectifs

Les tests unitaires montrent que chaque pièce marche seule. Ils ne disent pas
si les pièces marchent **ensemble** : une route web peut appeler un service
avec un mauvais identifiant, un export peut oublier des séances que la base
contient, une règle peut être bien enregistrée mais jamais lue par le solveur.

Les tests d'intégration répondent à trois questions :

1. **Le parcours de l'utilisateur fonctionne-t-il de bout en bout ?** Du
   formulaire « Générer » jusqu'au fichier iCal téléchargé.
2. **Les composants se comprennent-ils ?** Ce que le solveur produit, le
   vérificateur l'accepte ; ce que l'export écrit, l'import le relit.
3. **Les retouches gardent-elles la cohérence ?** Une modification à la main
   ne doit pas casser l'emploi du temps.

## 2. Stratégie de test du projet

| Niveau | Où | Comment le lancer | Rôle |
|---|---|---|---|
| Unitaire | `tests/unitaires/<composant>/` (socle, solveur, verification, web_donnees, web_edt, echange, synthese, exploitation), marqueur `unitaire` | `python -m pytest tests/unitaires` | un module à la fois, par ses fonctions Python, comportement observable |
| Intégration | `tests/integration/<composant>/` et `tests/integration/parcours/`, marqueur `integration` | `python -m pytest tests/integration` | plusieurs couches réelles assemblées : requête HTTP, ligne de commande, autre processus, calcul complet ; parcours de bout en bout |
| Cas limites | dans les deux familles, marqueur `cas_limite` | `python -m pytest -m cas_limite` | pannes plausibles et saisies piégées relevées par les campagnes de relecture ([banque de tests](../../docs/banque-de-tests.md)) |
| Charge | dans les deux familles, marqueur `perf` | `python -m pytest -m perf` | gros jeux (moyen, grand), durées et mémoire ; exclus de la CI courante |

La règle qui sépare unitaire et intégration est donnée dans
[`tests/README.md`](../README.md) ; `tests/test_rangement.py` vérifie que chaque
test est dans le bon dossier.

La CI (GitHub Actions) enchaîne à chaque envoi trois étapes : contrôle du
rangement (`python -m pytest tests/test_rangement.py`), tests unitaires
(`python -m pytest tests/unitaires -m "not perf"`), tests d'intégration
(`python -m pytest tests/integration -m "not perf"`).

Règles communes à tous les tests (voir [banque de tests](../../docs/banque-de-tests.md),
« Conventions ») : noms en français, base SQLite dans un dossier temporaire
propre à chaque test, aucune dépendance à l'horloge ni à l'ordre des tests,
jamais de solution particulière du solveur attendue en dur.

## 3. Couches assemblées

```
 navigateur simulé (TestClient)       ou     ligne de commande (CliRunner)
            |  requête HTTP                         |  python -m edt ...
            v                                       v
   routes web (FastAPI + Jinja2)              commandes Typer
            \                                       /
             v                                     v
     services : génération, règles, import/export, retouches, jeux fictifs
                    |                         |
                    v                         v
          solveur OR-Tools CP-SAT      vérificateur indépendant
                    \                         /
                     v                       v
                 base SQLite temporaire (SQLAlchemy 2)
```

| Couche | Code | Scénarios qui la traversent |
|---|---|---|
| Web (routes, gabarits HTML) | `src/edt/web/` | a, c, e |
| Ligne de commande | `src/edt/cli.py`, `src/edt/commandes/` | b |
| Services | `src/edt/services/` | tous |
| Solveur CP-SAT | `src/edt/solveur/` | a, b, d, e, f |
| Vérificateur | `src/edt/verification/` | a, b, d, e, f |
| Import / export | `src/edt/echange/` | a, b, c |
| Générateur de données fictives | `src/edt/synthese/` | f |
| Base SQLite | `src/edt/db/` | tous |

**Rien n'est simulé** : aucun `monkeypatch` du solveur ni du vérificateur. La
seule chose coupée (fixture `sans_prechauffage` de `parcours/conftest.py`) est le
préchauffage des écrans, un fil d'arrière-plan qui calcule d'avance les pages
après un calcul et qui survivrait à la base temporaire du test.

## 4. Les scénarios

Les six fichiers sont dans `tests/integration/parcours/` ; leurs aides
(`DELAI`, `calculer`, `compter`) sont dans `tests/outils/parcours/aides.py`.
Chaque test porte le marqueur `@pytest.mark.integration` et un docstring en
trois parties : **Scénario** (l'histoire), **Étapes**, **Ce que cela prouve**.
Données : le jeu minimal (2 campus, 6 salles, 5 enseignants, 9 groupes,
6 matières, 16 besoins, 17 séances par semaine) ou le jeu fictif « petit ».

### a. Parcours web complet — `test_parcours_web.py`

1. `POST /calculs` (formulaire « Générer ») ; le vrai solveur calcule.
2. `GET /versions` : une version porte l'étiquette « Active ».
3. `GET /emploi-du-temps` : la grille contient des séances.
4. Vérificateur : semaine type et séances datées sans anomalie bloquante.
5. `GET /export/emploi-du-temps.csv` et `.ics` : autant de lignes et
   d'événements que de séances en base.

Prouve que la chaîne web → services → solveur → vérificateur → base → export
fonctionne d'un bout à l'autre, et que l'export est fidèle à la base.

### b. Parcours en ligne de commande — `test_parcours_ligne_de_commande.py`

`init-db` → `jeu-minimal` → `generer --sans-optimiser` → `verifier` (code de
sortie 0) → `exporter-edt --sortie edt.csv` (fichier écrit, non vide). Le
semestre et la version sont relus en base, jamais écrits en dur. Prouve qu'un
administrateur peut produire un emploi du temps sans le site.

### c. Aller-retour des données — `test_aller_retour_donnees.py`

Classeur téléchargé par `GET /export/donnees.xlsx`, base neuve initialisée à
côté, import par le service de l'écran « Import », puis comparaison des
comptes de salles, enseignants, groupes, matières et besoins. Prouve que le
format d'échange est réellement réimportable, sans rejet ni perte.

### d. Règle personnalisée de bout en bout — `test_regle_personnalisee.py`

Règle créée par le service des règles : « la classe A1-G1 ne travaille pas le
lundi » (type `JOURS_TRAVAILLES`, niveau nécessaire). Après un vrai calcul :
le rapport du vérificateur est valide, la règle figure parmi les règles
vérifiées, aucune anomalie ne la cite, et aucune séance de A1-G1 n'est un
lundi. Prouve qu'une règle saisie est lue, imposée puis contrôlée.

*Contre-épreuve faite à la main pendant la mise au point* : en déplaçant une
séance de A1-G1 au lundi dans la base après le calcul, le vérificateur rend
un rapport invalide dont l'anomalie cite la règle (« Règle R005 (La classe
A1-G1 ne travaille pas le lundi) : … »). Le test sait donc échouer.

### e. Retouche manuelle — `test_retouche_manuelle.py`

Après un calcul, une séance datée est annulée par la route web
`POST /emploi-du-temps/occurrences/{id}/annuler` (motif « grève »). La base
montre la séance annulée avec son motif, exactement une annulation de plus,
et le vérificateur des séances datées ne trouve aucune anomalie bloquante.
L'annulation a été préférée au déplacement : un déplacement peut être refusé
selon la place que le solveur a choisie, ce qui rendrait le test dépendant de
la solution.

### f. Données fictives réalistes — `test_jeu_fictif_realiste.py`

Chargement du jeu « petit » (une promotion de 1re année, deux classes, deux
sites, salles spécialisées, vacataires, une absence d'enseignant, partiels ;
23 besoins) par le service de l'écran « Jeux de
démonstration », calcul de chacun de ses semestres, vérification de la
semaine type et des séances datées (283 séances datées) sans anomalie
bloquante. L'optimisation est coupée (paramètre `objectif_actif`) : on veut un
emploi du temps valide, pas le meilleur.

### Sortie réelle de pytest

`python -m pytest tests/integration/parcours -v -p no:cacheprovider --durations=6`,
le 16 novembre 2026 (Windows 11, Python 3.11.9) :

```
collecting ... collected 6 items

tests/integration/parcours/test_aller_retour_donnees.py::test_export_puis_import_dans_une_base_neuve PASSED [ 16%]
tests/integration/parcours/test_jeu_fictif_realiste.py::test_jeu_petit_calcule_et_verifie PASSED [ 33%]
tests/integration/parcours/test_parcours_ligne_de_commande.py::test_parcours_ligne_de_commande PASSED [ 50%]
tests/integration/parcours/test_parcours_web.py::test_parcours_web_du_calcul_a_l_export PASSED [ 66%]
tests/integration/parcours/test_regle_personnalisee.py::test_regle_jours_travailles_respectee_de_bout_en_bout PASSED [ 83%]
tests/integration/parcours/test_retouche_manuelle.py::test_annuler_une_seance_datee_par_le_site PASSED [100%]

============================= slowest 6 durations =============================
0.73s setup    tests/integration/parcours/test_aller_retour_donnees.py::test_export_puis_import_dans_une_base_neuve
0.63s call     tests/integration/parcours/test_aller_retour_donnees.py::test_export_puis_import_dans_une_base_neuve
0.53s call     tests/integration/parcours/test_parcours_web.py::test_parcours_web_du_calcul_a_l_export
0.43s call     tests/integration/parcours/test_jeu_fictif_realiste.py::test_jeu_petit_calcule_et_verifie
0.32s call     tests/integration/parcours/test_retouche_manuelle.py::test_annuler_une_seance_datee_par_le_site
0.27s call     tests/integration/parcours/test_parcours_ligne_de_commande.py::test_parcours_ligne_de_commande
============================== 6 passed in 3.61s ==============================
```

Au premier lancement d'une session, le premier test paie le chargement des
bibliothèques (quelques secondes de plus) ; total observé : 3 à 13 s selon la
machine et le cache. Aucun parcours ne dépasse 30 secondes, aucun n'est donc
marqué `perf`.

## 5. Ce que couvre chaque niveau

Chiffres relevés le 16 novembre 2026 par `python -m pytest --collect-only -q`
(avec `tests/unitaires`, `tests/integration`, `-m cas_limite`, `-m perf`) ;
le détail par composant est dans [`tests/README.md`](../README.md) :

| Niveau | Nombre de tests | Ce qu'il couvre | Ce qu'il ne couvre pas |
|---|---|---|---|
| Unitaire (`tests/unitaires/`) | 1 180 (dont 24 `perf`) | chaque fonction, règle ou service pris isolément, par ses fonctions Python | l'enchaînement des couches (HTTP, commandes, calcul complet) |
| Intégration (`tests/integration/`) | 518 (dont 3 `perf`), dont les 6 parcours | écrans par HTTP, commandes, autres processus, calculs complets ; parcours de bout en bout web et ligne de commande, accord solveur/vérificateur, aller-retour des données | l'apparence et le JavaScript (pas de vrai navigateur) |
| Contrôle du rangement (`tests/test_rangement.py`) | 12 | chaque test est dans le dossier de sa famille ; aucun fichier de tests égaré ; aucun import entre modules de tests | le contenu des tests |
| **Total** | **1 710** | | |
| dont cas limites (`cas_limite`) | 549 (347 unitaires, 202 d'intégration ; 10 aussi `perf`) | saisies extrêmes, concurrence, pannes de fichiers ou de base, identifiants hors bornes | le parcours normal d'un utilisateur |
| dont charge (`perf`) | 27 | jeux moyen et grand, durées et mémoire | exclus de la CI courante |

La CI (`-m "not perf"`) lance 1 683 tests : 12 de contrôle, 1 156 unitaires
et 515 d'intégration, dont les 6 parcours.

## 6. Limites connues

- **Pas de vrai navigateur** : `TestClient` envoie de vraies requêtes HTTP à
  l'application et lit le HTML rendu, mais n'exécute ni JavaScript ni HTMX et
  ne juge pas l'apparence. Un test de bout en bout dans un navigateur
  (Playwright, par exemple) serait l'étape suivante.
- **Pas de site déployé** : rien ne touche le VPS ni nginx ; le déploiement est
  testé à part (`tests/unitaires/exploitation/` et
  `tests/integration/exploitation/` : scripts, unité systemd, nginx).
- **Petits jeux seulement** : jeu minimal et jeu « petit », pour rester sous
  quelques secondes ; les gros jeux sont dans les tests de charge.
- **Optimisation coupée** : les scénarios cherchent un emploi du temps valide,
  pas le meilleur ; la qualité de l'optimisation est mesurée par les tests du
  solveur et de charge.
- **Propriétés, pas solutions** : les tests n'attendent jamais un placement
  précis ; une régression qui garderait un emploi du temps valide mais moins
  bon ne serait pas vue ici.
- **Retouche limitée à l'annulation** : le déplacement d'une séance est couvert
  par les tests de `tests/integration/web_edt/test_emploi_du_temps.py`, pas par
  un parcours de bout en bout (voir le scénario e).
