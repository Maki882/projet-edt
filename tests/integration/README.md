# Tests d'intégration

Un test d'intégration fait tourner **plusieurs couches réelles de
l'application ensemble**, sur une vraie base SQLite temporaire. Il passe par
une interface (requête HTTP par le client de test, ligne de commande par
`CliRunner`, autre processus) ou enchaîne tout le calcul par le service de
génération (`lancer_calcul`, `attendre_calcul`).

Ce dossier contient (nombres de tests et détail par fichier en section 6) :

- **`parcours/`** : des scénarios de bout en bout, comme un utilisateur se
  servirait de l'application. C'est le point d'entrée pour un lecteur pressé
  (section 4) ;
- **un dossier par composant** (`assistant/`, `banc_tests/`, `echange/`,
  `exploitation/`, `socle/`, `solveur/`, `synthese/`, `verification/`,
  `web_donnees/`, `web_edt/`) : les
  écrans de chaque composant par HTTP, ses commandes par `CliRunner`, ses
  calculs complets (section 6).

Le document de recette de ces tests est le
[cahier de recette](../../docs/livrets/4-cahier-de-recette.md) (livret 4) :
il relie les tests d'intégration aux fonctionnalités et aux cas d'usage du
livret 1 et donne les résultats des campagnes.

La règle de classement, la vue d'ensemble et les tests unitaires sont décrits
dans [`tests/README.md`](../README.md) et
[`tests/unitaires/README.md`](../unitaires/README.md).

## 1. Pourquoi des tests d'intégration

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

## 2. La chaîne traversée

```mermaid
flowchart TD
    NAV["Navigateur simulé : TestClient"] -->|requête HTTP| ROUTES["Routes web FastAPI : src/edt/web/routes"]
    CLI["Ligne de commande : CliRunner"] -->|commande edt| CMD["Commandes Typer : src/edt/commandes"]
    ROUTES --> SERV["Services : génération, règles, import et export, retouches, jeux fictifs"]
    CMD --> SERV
    SERV --> SOL["Solveur OR-Tools CP-SAT : src/edt/solveur"]
    SERV --> VER["Vérificateur indépendant : src/edt/verification"]
    SERV --> BASE[("Base SQLite temporaire : SQLAlchemy 2")]
    SOL --> BASE
    VER --> BASE
```

| Couche | Code | Parcours qui la traversent |
|---|---|---|
| Web (routes, gabarits HTML) | `src/edt/web/` | a, c, e |
| Ligne de commande | `src/edt/cli.py`, `src/edt/commandes/` | b |
| Services | `src/edt/services/` | tous |
| Solveur CP-SAT | `src/edt/solveur/` | a, b, d, e, f |
| Vérificateur | `src/edt/verification/` | a, b, d, e, f |
| Import / export | `src/edt/echange/` | a, b, c |
| Générateur de données fictives | `src/edt/synthese/` | f |
| Base SQLite | `src/edt/db/` | tous |

Dans les parcours, **rien n'est simulé** : aucun `monkeypatch` du solveur ni
du vérificateur. La seule chose coupée (fixture `sans_prechauffage` de
`parcours/conftest.py`) est le préchauffage des écrans, un fil
d'arrière-plan qui calcule d'avance les pages après un calcul et qui
survivrait à la base temporaire du test.

## 3. Un parcours complet, pas à pas

Le parcours web ([`parcours/test_parcours_web.py`](parcours/test_parcours_web.py)),
tel que le code l'exécute :

```mermaid
sequenceDiagram
    participant T as Test pytest
    participant C as Client HTTP de test
    participant R as Routes web
    participant G as Service de génération
    participant F as Fil de calcul
    participant S as Solveur CP-SAT
    participant V as Vérificateur
    participant B as Base SQLite temporaire
    T->>C: POST /calculs avec le semestre
    C->>R: requête HTTP
    R->>G: demarrer_calcul puis lancer_calcul
    G->>B: ligne de calcul enregistrée
    G->>F: démarrage du fil de calcul
    R-->>C: redirection 303 vers la page du calcul
    C-->>T: réponse 303 et numéro du calcul
    F->>B: chargement de l'instantané du semestre
    F->>S: pré-vérifications puis résolution
    S-->>F: placements des séances
    F->>V: contrôle de la solution
    F->>B: version enregistrée et activée, séances datées
    T->>G: attendre_calcul
    G-->>T: suivi réussi et numéro de version
    T->>C: GET /versions puis GET /emploi-du-temps
    C->>R: requêtes HTTP
    R->>B: lecture de la version
    R-->>C: pages HTML
    C-->>T: version Active et blocs dans la grille
    T->>V: verifier_version et verifier_occurrences_version
    V-->>T: aucune anomalie bloquante
    T->>C: GET des exports CSV et iCal
    C->>R: requêtes HTTP
    R->>B: lecture des séances de la version
    R-->>C: fichiers CSV et iCal
    C-->>T: une ligne et un événement par séance en base
```

## 4. Les parcours : point d'entrée

Chaque parcours porte un docstring en trois parties : **Scénario**,
**Étapes**, **Ce que cela prouve**. Données : le jeu minimal (2 campus,
6 salles, 5 enseignants, 9 groupes, 6 matières, 16 besoins, 17 séances par
semaine) ou le jeu fictif « petit ».

| Fichier | Scénario | Couches traversées | Ce qu'il prouve | Durée mesurée |
|---|---|---|---|---|
| [`parcours/test_parcours_web.py`](parcours/test_parcours_web.py) | a. calcul lancé depuis le site, consulté, exporté | web, services, solveur, vérificateur, export, base | une version active, affichée, valide et exportée en entier | 0,5 s |
| [`parcours/test_parcours_ligne_de_commande.py`](parcours/test_parcours_ligne_de_commande.py) | b. `init-db`, `jeu-minimal`, `generer`, `verifier`, `exporter-edt` | ligne de commande, services, solveur, vérificateur, export, base | les commandes s'enchaînent sur la même base sans le site | 0,3 s |
| [`parcours/test_aller_retour_donnees.py`](parcours/test_aller_retour_donnees.py) | c. classeur exporté puis importé dans une base neuve | web, import et export, services, base | le format d'échange est réimportable sans perte | 0,6 s (plus 0,7 s de préparation) |
| [`parcours/test_regle_personnalisee.py`](parcours/test_regle_personnalisee.py) | d. « A1-G1 ne travaille pas le lundi » | services, solveur, vérificateur, base | une règle saisie est imposée puis contrôlée | moins de 0,3 s |
| [`parcours/test_retouche_manuelle.py`](parcours/test_retouche_manuelle.py) | e. séance datée annulée par le site | services, solveur, web, vérificateur, base | une retouche laisse l'emploi du temps cohérent | 0,3 s |
| [`parcours/test_jeu_fictif_realiste.py`](parcours/test_jeu_fictif_realiste.py) | f. jeu fictif « petit » calculé et vérifié | générateur de données, solveur, vérificateur, base | un établissement plus riche est calculé sans anomalie bloquante | 0,4 s |

Durées de l'appel du test, mesurées le 16 novembre 2026 (Windows 11,
Python 3.11.9) par la commande de la section 4.7. Les parcours tournent
en moins de 4 s au total, hors premier chargement des bibliothèques (quelques
secondes de plus). Aucun ne dépasse 30 s : aucun n'est marqué `perf`, tous
tournent dans la CI.

Les tests ne vérifient jamais *où* le solveur a placé un cours (cela peut
changer d'une machine à l'autre) mais des **propriétés** : une version active
existe, zéro anomalie bloquante, les comptes sont égaux, la règle est respectée.

### 4.1 a. Parcours web complet

1. `POST /calculs` (formulaire « Générer ») ; le vrai solveur calcule.
2. `GET /versions` : une version porte l'étiquette « Active ».
3. `GET /emploi-du-temps` : la grille contient des séances.
4. Vérificateur : semaine type et séances datées sans anomalie bloquante.
5. `GET /export/emploi-du-temps.csv` et `.ics` : autant de lignes et
   d'événements que de séances en base.

Prouve que la chaîne web → services → solveur → vérificateur → base → export
fonctionne d'un bout à l'autre, et que l'export est fidèle à la base.

### 4.2 b. Parcours en ligne de commande

`init-db` → `jeu-minimal` → `generer --sans-optimiser` → `verifier` (code de
sortie 0) → `exporter-edt --sortie edt.csv` (fichier écrit, non vide). Le
semestre et la version sont relus en base, jamais écrits en dur. Prouve qu'un
administrateur peut produire un emploi du temps sans le site.

### 4.3 c. Aller-retour des données

Classeur téléchargé par `GET /export/donnees.xlsx`, base neuve initialisée à
côté, import par le service de l'écran « Import », puis comparaison des
comptes de salles, enseignants, groupes, matières et besoins. Prouve que le
format d'échange est réellement réimportable, sans rejet ni perte.

### 4.4 d. Règle personnalisée de bout en bout

Règle créée par le service des règles : « la classe A1-G1 ne travaille pas le
lundi » (type `JOURS_TRAVAILLES`, niveau nécessaire). Après un vrai calcul :
le rapport du vérificateur est valide, la règle figure parmi les règles
vérifiées, aucune anomalie ne la cite, et aucune séance de A1-G1 n'est un
lundi. Prouve qu'une règle saisie est lue, imposée puis contrôlée.

Contre-épreuve : en déplaçant une séance de A1-G1 au lundi dans la base après
le calcul, le vérificateur rend un rapport invalide dont l'anomalie cite la
règle (« Règle R005 (La classe A1-G1 ne travaille pas le lundi) : … »). Le
test sait donc échouer.

### 4.5 e. Retouche manuelle

Après un calcul, une séance datée est annulée par la route web
`POST /emploi-du-temps/occurrences/{id}/annuler` (motif « grève »). La base
montre la séance annulée avec son motif, exactement une annulation de plus,
et le vérificateur des séances datées ne trouve aucune anomalie bloquante.
L'annulation a été préférée au déplacement : un déplacement peut être refusé
selon la place que le solveur a choisie, ce qui rendrait le test dépendant de
la solution. Le déplacement est couvert par
[`web_edt/test_emploi_du_temps.py`](web_edt/test_emploi_du_temps.py).

### 4.6 f. Données fictives réalistes

Chargement du jeu « petit » (une promotion de 1re année, deux classes, deux
sites, salles spécialisées, vacataires, une absence d'enseignant, partiels ;
23 besoins) par le service de l'écran « Établissement fictif », calcul de
chacun de ses semestres, vérification de la semaine type et des séances
datées (283 séances datées) sans anomalie bloquante. L'optimisation est
coupée (paramètre `objectif_actif`) : un emploi du temps valide est attendu,
pas le meilleur.

### 4.7 Sortie réelle de pytest

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

## 5. Un exemple expliqué pas à pas

Fichier [`parcours/test_regle_personnalisee.py`](parcours/test_regle_personnalisee.py),
corps du test (docstring omis) :

```python
with session_base() as session:
    annee = session.get(m.AnneeScolaire, jeu_minimal.annee_scolaire_id).libelle
    regle = regles.creer(
        session,
        {
            "type": "JOURS_TRAVAILLES",
            "annee": annee,
            "portee": {"nature": "groupe", "cible": "A1-G1"},
            "parametres": {"jours": ["mardi", "mercredi", "jeudi", "vendredi"]},
            "niveau": "necessaire",
        },
    )

version = calculer(jeu_minimal.semestre_id).version_id

with session_base() as session:
    rapport = verifier_version(session, version)
    jours_a1_g1 = session.scalars(
        select(m.SeanceHebdo.jour).where(
            m.SeanceHebdo.version_id == version,
            m.SeanceHebdo.besoin.has(m.Besoin.groupes.any(code="A1-G1")),
        )
    ).all()
assert rapport.valide
assert regle.id in rapport.niveaux_regles  # la règle a bien été vérifiée
assert not [a for a in rapport.anomalies if a.regle_id == regle.id]
assert jours_a1_g1 and LUNDI not in jours_a1_g1
```

1. **Préparation.** La fixture `jeu_minimal` charge un petit
   établissement dans la base temporaire du test. Le service des règles crée
   la règle « A1-G1 ne travaille que du mardi au vendredi », au niveau
   nécessaire, comme le ferait l'écran « Règles personnalisées ».
2. **Appel.** `calculer` (`tests/outils/parcours/aides.py`) lance
   un vrai calcul par `lancer_calcul` et l'attend par `attendre_calcul` :
   chargement de l'instantané, solveur, contrôle, enregistrement de la
   version. C'est ce calcul complet qui fait de ce test un test
   d'intégration.
3. **Vérification.** Le vérificateur juge la version valide, a bien
   contrôlé la règle et ne lui trouve aucune entorse ; la base, relue
   directement, ne contient aucune séance de A1-G1 un lundi.
4. **Pourquoi le test échouerait si le défaut revenait.** Si la règle
   n'était plus transmise au solveur, ou plus posée par lui, des séances de
   A1-G1 pourraient tomber un lundi : la dernière assertion échouerait. Si le
   vérificateur ne la contrôlait plus, `regle.id` manquerait dans
   `rapport.niveaux_regles`.

## 6. Ce que vérifie chaque fichier, par composant

Nombres de fonctions de test (`test_*`), comptés dans le code : une fonction
paramétrée compte une fois. La description d'un fichier est la première
phrase de la docstring de son module. Cette section est produite par
`python -m tests.outils.classement --ecrire` (voir [`tests/README.md`](../README.md), section 3) ; le nombre
de cas collectés par pytest s'obtient par
`python -m pytest --collect-only -q tests/integration`.

<!-- tests-integration : début de la partie générée par `python -m tests.outils.classement --ecrire`, ne pas modifier à la main -->

Ce dossier compte **559 fonctions de test** dans 11 composants, dont 221 marquées `cas_limite` et 4 marquées `perf`.

### `assistant/` : Assistant en langage courant par le site et la ligne de commande (31 fonctions)

| Fichier | Ce qu'il vérifie | Fonctions |
|---|---|---:|
| [`assistant/test_commande.py`](assistant/test_commande.py) | Commande `python -m edt assistant` : état, essai et question, avec le faux client | 8 |
| [`assistant/test_panneau.py`](assistant/test_panneau.py) | Panneau Assistant sur toutes les pages : non configuré, désactivé, prêt à charger | 5 |
| [`assistant/test_web.py`](assistant/test_web.py) | Panneau Assistant par HTTP, avec le faux client : question, proposition, limites, gardes | 18 |

### `banc_tests/` : Tests d'intégration du banc de tests : vrai sous-processus pytest, écrans, commande (24 fonctions)

| Fichier | Ce qu'il vérifie | Fonctions |
|---|---|---:|
| [`banc_tests/test_commande.py`](banc_tests/test_commande.py) | Commande `python -m edt tests` sur le dépôt factice : code de sortie, échecs affichés, résultats enregistrés | 4 |
| [`banc_tests/test_ecrans.py`](banc_tests/test_ecrans.py) | Écrans « Tests unitaires » et « Tests d'intégration » par HTTP : onglets, schéma, lancement, suivi en direct, confettis, refus | 13 |
| [`banc_tests/test_execution.py`](banc_tests/test_execution.py) | Exécution réelle des tests d'un dépôt factice par le service, dans un sous-processus pytest : résultats, arrêt, durée maximale | 7 |

### `echange/` : Import et export par le site et la ligne de commande (41 fonctions)

| Fichier | Ce qu'il vérifie | Fonctions |
|---|---|---:|
| [`echange/test_bornes.py`](echange/test_bornes.py) | Fichiers hostiles envoyés au site, pages bornées | 6 |
| [`echange/test_commandes.py`](echange/test_commandes.py) | Commandes gabarit, importer, exporter-donnees et exporter-edt | 8 |
| [`echange/test_export_edt.py`](echange/test_export_edt.py) | Export d'un emploi du temps : CSV et iCal, par ressource, relu par la bibliothèque icalendar | 6 |
| [`echange/test_recette_echanges.py`](echange/test_recette_echanges.py) | Recette de l'import, de l'export et de l'impression, par les écrans de l'application | 5 |
| [`echange/test_web.py`](echange/test_web.py) | Écrans d'import et d'export : dépôt, simulation, application, téléchargements | 16 |

### `exploitation/` : Commandes d'exploitation et scripts de déploiement (32 fonctions)

| Fichier | Ce qu'il vérifie | Fonctions |
|---|---|---:|
| [`exploitation/test_commandes_exploitation.py`](exploitation/test_commandes_exploitation.py) | Commandes et service : `init-db` relançable, codes de sortie, sauvegardes | 19 |
| [`exploitation/test_scripts_deploiement.py`](exploitation/test_scripts_deploiement.py) | Scripts de déploiement exécutés hors du serveur : bash -n, fonctions extraites, awk, Python | 13 |

### `parcours/` : Scénarios de bout en bout (6 fonctions)

| Fichier | Ce qu'il vérifie | Fonctions |
|---|---|---:|
| [`parcours/test_aller_retour_donnees.py`](parcours/test_aller_retour_donnees.py) | Parcours REC-BOUT-3 : exporter les données d'un établissement puis les réimporter dans une base neuve | 1 |
| [`parcours/test_jeu_fictif_realiste.py`](parcours/test_jeu_fictif_realiste.py) | Parcours REC-BOUT-6 : un établissement fictif réaliste (jeu « petit ») est calculé et vérifié | 1 |
| [`parcours/test_parcours_ligne_de_commande.py`](parcours/test_parcours_ligne_de_commande.py) | Parcours REC-BOUT-2 : parcours complet en ligne de commande (`python -m edt ...`) | 1 |
| [`parcours/test_parcours_web.py`](parcours/test_parcours_web.py) | Parcours REC-BOUT-1 : parcours complet d'un utilisateur dans l'application web | 1 |
| [`parcours/test_regle_personnalisee.py`](parcours/test_regle_personnalisee.py) | Parcours REC-BOUT-4 : une règle personnalisée saisie par l'utilisateur est respectée par le calcul | 1 |
| [`parcours/test_retouche_manuelle.py`](parcours/test_retouche_manuelle.py) | Parcours REC-BOUT-5 : une retouche manuelle après le calcul (annulation d'une séance datée) | 1 |

### `socle/` : Commandes et application web du socle (80 fonctions)

| Fichier | Ce qu'il vérifie | Fonctions |
|---|---|---:|
| [`socle/test_cli.py`](socle/test_cli.py) | Ligne de commande : commandes générales et mécanisme d'extension | 22 |
| [`socle/test_config.py`](socle/test_config.py) | Configuration vue par la commande doctor : adresse d'écoute introuvable, port occupé | 2 |
| [`socle/test_generation.py`](socle/test_generation.py) | Orchestration d'un calcul : succès, calcul déjà en cours, arrêt, échecs | 12 |
| [`socle/test_instantane.py`](socle/test_instantane.py) | Instantané : identifiants hors des entiers SQLite, côté commandes (pre-verifier, verifier) | 2 |
| [`socle/test_journal.py`](socle/test_journal.py) | Journalisation vue par la ligne de commande : fichier journal non ouvrable, signalé par doctor | 1 |
| [`socle/test_moteur.py`](socle/test_moteur.py) | Moteur de base vu par la ligne de commande : URL de base refusées avec une erreur de configuration | 2 |
| [`socle/test_parametres.py`](socle/test_parametres.py) | Paramètres globaux vus par l'application web : l'écran reste ouvrable avec une valeur corrompue | 1 |
| [`socle/test_recette_petit_ecran.py`](socle/test_recette_petit_ecran.py) | Recette de la consultation sur téléphone : ce qu'un test par HTTP peut prouver | 2 |
| [`socle/test_schema.py`](socle/test_schema.py) | Schéma périmé vu par les interfaces : init-db, verifier-schema, doctor, jeu-minimal et /sante | 9 |
| [`socle/test_web.py`](socle/test_web.py) | Application web : accueil, santé, démarrage, erreurs lisibles, mécanisme d'extension | 27 |

### `solveur/` : Calculs complets et commandes du solveur (54 fonctions)

| Fichier | Ce qu'il vérifie | Fonctions |
|---|---|---:|
| [`solveur/test_niveaux.py`](solveur/test_niveaux.py) | Niveaux de règles, de bout en bout : enregistrement avec le calcul, suivi et ligne de commande | 6 |
| [`solveur/test_regles.py`](solveur/test_regles.py) | Règles personnalisées, de bout en bout : copie avec le calcul, suivi et ligne de commande | 3 |
| [`solveur/test_services.py`](solveur/test_services.py) | Intégration sur le jeu minimal du socle : génération de bout en bout, instanciation, replacement | 45 |

### `synthese/` : Commande des jeux fictifs (6 fonctions)

| Fichier | Ce qu'il vérifie | Fonctions |
|---|---|---:|
| [`synthese/test_cas_limites.py`](synthese/test_cas_limites.py) | Une graine ou une année refusée ne vide jamais la base | 1 |
| [`synthese/test_commande.py`](synthese/test_commande.py) | Commande `python -m edt jeu` (service `edt.services.jeux` par la ligne de commande) | 5 |

### `verification/` : Commande du vérificateur (6 fonctions)

| Fichier | Ce qu'il vérifie | Fonctions |
|---|---|---:|
| [`verification/test_commande.py`](verification/test_commande.py) | Commande `verifier` lancée par `CliRunner` : codes de sortie, options | 6 |

### `web_donnees/` : Écrans de l'espace « Données », par HTTP (127 fonctions)

| Fichier | Ce qu'il vérifie | Fonctions |
|---|---|---:|
| [`web_donnees/test_besoins.py`](web_donnees/test_besoins.py) | Écran des besoins : trois modes de volume, filtres, duplication, charge hebdomadaire | 9 |
| [`web_donnees/test_cas_limites.py`](web_donnees/test_cas_limites.py) | Calcul en cours, dates aberrantes, adresses et en-têtes fabriqués | 12 |
| [`web_donnees/test_concurrence.py`](web_donnees/test_concurrence.py) | Un seul chargement de jeu fictif ou une seule remise à zéro à la fois | 3 |
| [`web_donnees/test_entites.py`](web_donnees/test_entites.py) | Un test rapide par entité : la liste répond, le formulaire s'ouvre, une création valide passe | 7 |
| [`web_donnees/test_generique.py`](web_donnees/test_generique.py) | Mécanisme générique : liste (tri, recherche, filtres, pagination), formulaire, suppression | 9 |
| [`web_donnees/test_jeux.py`](web_donnees/test_jeux.py) | Page des jeux de données : établissement fictif, refus pendant un calcul, remise à zéro | 9 |
| [`web_donnees/test_mise_en_page.py`](web_donnees/test_mise_en_page.py) | Mise en page commune : six onglets, sous-navigation, onglet vide masqué, panneau de l'assistant, aucune annonce | 6 |
| [`web_donnees/test_parametres.py`](web_donnees/test_parametres.py) | Écran des paramètres : construit depuis les métadonnées, bornes contrôlées, valeurs par défaut | 11 |
| [`web_donnees/test_performance.py`](web_donnees/test_performance.py) | Banque des cas limites : temps et taille des pages sur l'établissement complet (jeu grand) | 2 |
| [`web_donnees/test_regles.py`](web_donnees/test_regles.py) | Règles personnalisées : liste et filtres, ajout guidé, modification, états, suppressions, paramètre des jours travaillés, propositions à la création d'une année, parcours réel | 30 |
| [`web_donnees/test_saisies_extremes.py`](web_donnees/test_saisies_extremes.py) | Saisies aberrantes : jamais d'erreur 500, toujours un message | 14 |
| [`web_donnees/test_sans_trace.py`](web_donnees/test_sans_trace.py) | Aucune page de l'espace « Données » ne plante ni n'affiche de trace technique | 2 |
| [`web_donnees/test_suppression.py`](web_donnees/test_suppression.py) | Suppression : retour conservé, confirmation sur place, refus avec liens, conséquences expliquées | 13 |

### `web_edt/` : Écrans Générer et emploi du temps, par HTTP (152 fonctions)

| Fichier | Ce qu'il vérifie | Fonctions |
|---|---|---:|
| [`web_edt/test_a_traiter.py`](web_edt/test_a_traiter.py) | Page « Points à traiter » : avec et sans vérificateur, liens vers les séances, séances à programmer | 6 |
| [`web_edt/test_acces.py`](web_edt/test_acces.py) | Accès au site par mot de passe : connexion, droits de la consultation, site ouvert par défaut | 6 |
| [`web_edt/test_accueil.py`](web_edt/test_accueil.py) | Accueil : prochaine étape, version active, points à traiter, calcul sans solution, base | 11 |
| [`web_edt/test_calculs.py`](web_edt/test_calculs.py) | Onglet « Générer » : lancement, suivi, messages de fin, vérifications préalables, versions | 26 |
| [`web_edt/test_cas_limites.py`](web_edt/test_cas_limites.py) | Adresses mal copiées, liens périmés, cookies forgés : jamais d'erreur 500 | 25 |
| [`web_edt/test_dejeuner.py`](web_edt/test_dejeuner.py) | Onglet « Générer » : pause déjeuner à trois modes, préremplie, enregistrée au lancement | 8 |
| [`web_edt/test_deplacement_possible.py`](web_edt/test_deplacement_possible.py) | Déplacement d'une séance : une heure annoncée « possible » s'enregistre telle que présentée | 4 |
| [`web_edt/test_emploi_du_temps.py`](web_edt/test_emploi_du_temps.py) | Grille, groupes liés, déplacements et annulations vérifiés | 20 |
| [`web_edt/test_niveaux_web.py`](web_edt/test_niveaux_web.py) | Niveaux de règles sur l'écran « Générer », le suivi, l'historique et « Points à traiter » | 11 |
| [`web_edt/test_raccords.py`](web_edt/test_raccords.py) | Raccords : vraies pré-vérifications et instanciation d'une version produite par un calcul complet | 1 |
| [`web_edt/test_recette_affichage.py`](web_edt/test_recette_affichage.py) | Adaptation des écrans à un petit écran et à l'impression, vérifiable sans navigateur | 4 |
| [`web_edt/test_recette_deplacement.py`](web_edt/test_recette_deplacement.py) | Formulaire « Déplacer la séance » par le site, avec le vrai vérificateur : heures annoncées « possible, avec avertissement », et jour sans aucune heure possible | 2 |
| [`web_edt/test_recette_resultat.py`](web_edt/test_recette_resultat.py) | Résultat d'un calcul : les indicateurs de qualité affichés concordent avec le vérificateur | 1 |
| [`web_edt/test_recette_verrous.py`](web_edt/test_recette_verrous.py) | Verrous posés et levés depuis le panneau d'une séance, puis tenus par le calcul suivant | 2 |
| [`web_edt/test_regles_web.py`](web_edt/test_regles_web.py) | Règles personnalisées sur les écrans de l'emploi du temps : Générer, suivi, grille, anomalies | 13 |
| [`web_edt/test_revue.py`](web_edt/test_revue.py) | Écrans de l'emploi du temps : séances datées importées, points à traiter, règles décochées, mémoire des écrans | 12 |

<!-- tests-integration : fin de la partie générée -->

## 7. Stratégie de test du projet

| Niveau | Où | Comment le lancer | Rôle |
|---|---|---|---|
| Unitaire | `tests/unitaires/<composant>/`, marqueur `unitaire` | `python -m pytest tests/unitaires` | un module à la fois, par ses fonctions Python |
| Intégration | `tests/integration/<composant>/` et `tests/integration/parcours/`, marqueur `integration` | `python -m pytest tests/integration` | plusieurs couches réelles assemblées ; parcours de bout en bout |
| Cas limites | dans les deux familles, marqueur `cas_limite` | `python -m pytest -m cas_limite` | pannes plausibles et saisies piégées ([banque de tests](../../docs/banque-de-tests.md)) |
| Charge | dans les deux familles, marqueur `perf` | `python -m pytest -m perf` | gros jeux (moyen, grand), durées et mémoire ; exclus de la CI courante |

| Niveau | Ce qu'il couvre | Ce qu'il ne couvre pas |
|---|---|---|
| Unitaire (`tests/unitaires/`) | chaque fonction, règle ou service pris isolément | l'enchaînement des couches |
| Intégration (`tests/integration/`), parcours compris | écrans par HTTP, commandes, autres processus, calculs complets ; accord solveur et vérificateur ; aller-retour des données | l'apparence et le JavaScript |
| Contrôle du rangement (`tests/test_rangement.py`) | classement de chaque test, fichiers égarés, imports entre modules de tests, nombres et tableaux générés de ces documents | le contenu des tests |
| Cas limites (`cas_limite`) | saisies extrêmes, concurrence, pannes de fichiers ou de base, identifiants hors bornes | le parcours normal d'un utilisateur |
| Charge (`perf`) | jeux moyen et grand, durées et mémoire | exclus de la CI courante |

Les nombres de tests de chaque niveau sont dans la vue d'ensemble de
[`tests/README.md`](../README.md) (section 3). La CI
(`.github/workflows/ci.yml`) enchaîne, sous Python 3.11, 3.13 et 3.14 :
`ruff check`, `ruff format --check`, contrôle du rangement, tests unitaires et
tests d'intégration, hors tests de charge.

Règles communes à tous les tests (voir [banque de tests](../../docs/banque-de-tests.md),
« Conventions ») : noms en français, base SQLite dans un dossier temporaire
propre à chaque test, aucune dépendance à l'horloge ni à l'ordre des tests,
jamais de solution particulière du solveur attendue en dur.

## 8. Ce que ces tests ne couvrent pas

- **Pas de vrai navigateur** : `TestClient` envoie de vraies requêtes HTTP à
  l'application et lit le HTML rendu, mais n'exécute ni JavaScript ni HTMX et
  ne juge pas l'apparence. Un test dans un navigateur (Playwright, par
  exemple) serait l'étape suivante.
- **Pas de site déployé** : tout tourne en mémoire, sans serveur, sans réseau,
  sans le site déployé ni nginx ; les fichiers de
  déploiement sont contrôlés à part (`exploitation/` ici et dans
  `tests/unitaires/exploitation/` : scripts, unité systemd, nginx).
- **Petits jeux seulement** dans les parcours : jeu minimal et jeu « petit »,
  pour rester sous quelques secondes ; les gros jeux sont dans les tests de
  charge (`-m perf`).
- **Optimisation coupée** : les parcours cherchent un emploi du temps valide,
  pas le meilleur ; la qualité de l'optimisation est mesurée par les tests du
  solveur et de charge.
- **Propriétés, pas solutions** : les tests n'attendent jamais un placement
  précis ; une régression qui garderait un emploi du temps valide mais moins
  bon ne serait pas vue ici.

## 9. Lancer et ajouter

```sh
python -m pytest tests/integration/parcours -v    # les parcours seulement
python -m pytest tests/integration                # tous les tests d'intégration
python -m pytest -m integration                   # idem, par marqueur
python -m pytest tests/integration -m "not perf"  # sans les tests de charge (comme la CI)
python -m pytest tests/integration/web_edt        # un composant
```

Ajouter un parcours en cinq lignes :

```python
@pytest.mark.integration
def test_mon_scenario(jeu_minimal, client):
    """Scénario : ...  Étapes : 1. ... 2. ...  Ce que cela prouve : ..."""
    version = calculer(jeu_minimal.semestre_id).version_id  # vrai calcul
    assert client.get(f"/emploi-du-temps?version_id={version}").status_code == 200
```

Créer `test_<nom>.py` dans `tests/integration/parcours/`, importer `pytest` et
`from tests.outils.parcours.aides import calculer`, garder le docstring en
trois parties, et n'asserter que des propriétés. Un test d'intégration propre
à un composant va dans le dossier de ce composant.

Un nouveau fichier `test_*.py` commence par une docstring de module dont la
première phrase dit ce qu'il vérifie ; `python -m tests.outils.classement --ecrire` l'ajoute
ensuite aux tableaux de la section 6. `tests/test_rangement.py` échoue tant que
ces tableaux ne sont pas à jour.
