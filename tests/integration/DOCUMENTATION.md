# Tests d'intégration

Document du dossier de rendu (module PTS Informatique). Il présente la
stratégie de test du projet EDT et, en détail, les tests d'intégration du
dossier `tests/integration/`. Résumé visuel pour une présentation orale :
[`tests/integration/README.md`](README.md).

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
| Unitaire | `tests/<lot>/` (socle, solveur, verification, web_donnees, web_edt, echange, synthese, exploitation) | `python -m pytest tests/solveur` | une pièce à la fois, comportement observable |
| Cas limites | dans les mêmes dossiers, marqueur `cas_limite` | `python -m pytest -m cas_limite` | pannes plausibles et saisies piégées relevées par les campagnes de relecture ([banque de tests](banque-de-tests.md)) |
| Intégration | `tests/integration/`, marqueur `integration` | `python -m pytest -m integration` | plusieurs composants réels assemblés, parcours complets |
| Charge | marqueur `perf` | `python -m pytest -m perf` | gros jeux (moyen, grand), durées et mémoire ; exclus de la CI courante |

La CI (GitHub Actions) lance `python -m pytest -m "not perf"` à chaque envoi :
les tests d'intégration en font partie.

Règles communes à tous les tests (voir [banque de tests](banque-de-tests.md),
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
seule chose coupée (fixture `sans_prechauffage` du `conftest.py`) est le
préchauffage des écrans, un fil d'arrière-plan qui calcule d'avance les pages
après un calcul et qui survivrait à la base temporaire du test.

## 4. Les scénarios

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

`python -m pytest tests/integration -v -p no:cacheprovider --durations=0`, le
9 novembre 2026 (Windows 11, Python 3.11.9) :

```
collecting ... collected 6 items

tests/integration/test_aller_retour_donnees.py::test_export_puis_import_dans_une_base_neuve PASSED [ 16%]
tests/integration/test_jeu_fictif_realiste.py::test_jeu_petit_calcule_et_verifie PASSED [ 33%]
tests/integration/test_parcours_ligne_de_commande.py::test_parcours_ligne_de_commande PASSED [ 50%]
tests/integration/test_parcours_web.py::test_parcours_web_du_calcul_a_l_export PASSED [ 66%]
tests/integration/test_regle_personnalisee.py::test_regle_jours_travailles_respectee_de_bout_en_bout PASSED [ 83%]
tests/integration/test_retouche_manuelle.py::test_annuler_une_seance_datee_par_le_site PASSED [100%]

============================== slowest durations ==============================
1.86s call     tests/integration/test_parcours_web.py::test_parcours_web_du_calcul_a_l_export
1.03s call     tests/integration/test_retouche_manuelle.py::test_annuler_une_seance_datee_par_le_site
1.03s call     tests/integration/test_parcours_ligne_de_commande.py::test_parcours_ligne_de_commande
0.49s call     tests/integration/test_regle_personnalisee.py::test_regle_jours_travailles_respectee_de_bout_en_bout
0.43s call     tests/integration/test_aller_retour_donnees.py::test_export_puis_import_dans_une_base_neuve
0.41s call     tests/integration/test_jeu_fictif_realiste.py::test_jeu_petit_calcule_et_verifie
======================== 6 passed, 1 warning in 9.19s =========================
```

Au premier lancement d'une session, le premier test paie le chargement des
bibliothèques (2 à 3 s de plus) ; total observé : 9 à 13 s. Aucun test ne
dépasse 30 secondes, aucun n'est donc marqué `perf`. L'avertissement est une
dépréciation de la bibliothèque `starlette` (client de test), sans rapport
avec le projet.

## 5. Ce que couvre chaque niveau

Chiffres relevés le 9 novembre 2026 par `python -m pytest --collect-only -q`
(avec `-m integration`, `-m cas_limite`, `-m perf`) :

| Niveau | Nombre de tests | Ce qu'il couvre | Ce qu'il ne couvre pas |
|---|---|---|---|
| Unitaire (sans marqueur) | 1 126 | chaque fonction, règle, écran ou commande pris isolément | l'enchaînement complet des composants |
| Cas limites (`cas_limite`) | 548 (dont 10 aussi `perf`) | saisies extrêmes, concurrence, pannes de fichiers ou de base, identifiants hors bornes | le parcours normal d'un utilisateur |
| Intégration (`integration`) | 6 | parcours complets web et ligne de commande, accord solveur/vérificateur, aller-retour des données | les cas rares (laissés aux cas limites) |
| Charge (`perf`) | 27 | jeux moyen et grand, durées et mémoire | exclus de la CI courante |
| **Total** | **1 697** | | |

La CI (`-m "not perf"`) lance 1 670 tests, dont les 6 d'intégration.

## 6. Limites connues

- **Pas de vrai navigateur** : `TestClient` envoie de vraies requêtes HTTP à
  l'application et lit le HTML rendu, mais n'exécute ni JavaScript ni HTMX et
  ne juge pas l'apparence. Un test de bout en bout dans un navigateur
  (Playwright, par exemple) serait l'étape suivante.
- **Pas de site déployé** : rien ne touche le VPS ni nginx ; le déploiement est
  testé à part (`tests/exploitation/` : scripts, unité systemd, nginx).
- **Petits jeux seulement** : jeu minimal et jeu « petit », pour rester sous
  quelques secondes ; les gros jeux sont dans les tests de charge.
- **Optimisation coupée** : les scénarios cherchent un emploi du temps valide,
  pas le meilleur ; la qualité de l'optimisation est mesurée par les tests du
  solveur et de charge.
- **Propriétés, pas solutions** : les tests n'attendent jamais un placement
  précis ; une régression qui garderait un emploi du temps valide mais moins
  bon ne serait pas vue ici.
- **Retouche limitée à l'annulation** : le déplacement d'une séance est couvert
  par les tests de `tests/web_edt/test_emploi_du_temps.py`, pas par un scénario
  d'intégration (voir le scénario e).
