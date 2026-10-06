# Interface de l'emploi du temps

Ce document décrit les trois onglets **Générer**, **Emploi du temps** et
**À traiter**, l'enrichissement de la page d'accueil, le calcul de la
disposition des blocs dans la grille, les raccords vers les lots encore en
cours et la façon d'ajouter une vue. Les décisions qui le fondent sont dans
[`decisions.md`](decisions.md) (sections 1, 5 à 8 et 11) ; les conventions
communes (couches, `rendre`, `SessionRequete`, erreurs) dans
[`architecture.md`](architecture.md).

Principe : l'utilisateur est un responsable de scolarité **non
informaticien**. Aucun terme de solveur n'apparaît, aucun écran n'affiche
d'erreur brute, et une vérification qui n'a pas pu tourner est **dite**,
jamais remplacée par une liste vide.

## Fichiers

| Fichier | Rôle |
|---|---|
| `src/edt/services/emploi_du_temps/raccords.py` | seul point d'appel des lots en cours (pré-vérifications, vérificateur, instanciation) |
| `src/edt/services/emploi_du_temps/presentation.py` | durées, semaines, pourcentages, couleur stable d'une matière |
| `src/edt/services/emploi_du_temps/disposition.py` | position des blocs dans la grille (fonction pure) |
| `src/edt/services/emploi_du_temps/vues.py` | séances d'une vue (version, cible, couche, période) |
| `src/edt/services/emploi_du_temps/modifications.py` | salles compatibles, déplacement vérifié, verrou, séances datées |
| `src/edt/services/emploi_du_temps/versions.py` | semestres, versions, historique, activation, suppression |
| `src/edt/services/emploi_du_temps/calculs.py` | préparation, lancement et résultat d'un calcul |
| `src/edt/services/emploi_du_temps/anomalies.py` | la liste « À traiter » |
| `src/edt/services/emploi_du_temps/compteur.py` | compteur de l'onglet « À traiter » (somme des `ATraiter.total`) |
| `src/edt/services/emploi_du_temps/memoire.py` | mémoire des vérifications, pré-vérifications et récapitulatif tant que la base ne change pas |
| `src/edt/services/emploi_du_temps/prechauffage.py` | recalcul en arrière-plan de cette mémoire après un calcul ou une activation |
| `src/edt/services/emploi_du_temps/tableau_de_bord.py` | ce que l'accueil affiche : prochaine étape, version active, points à traiter, calcul sans solution |
| `src/edt/web/routes/calculs.py` | onglet « Générer » (`/calculs`, `/versions`) |
| `src/edt/web/routes/emploi_du_temps.py` | onglet « Emploi du temps » (`/emploi-du-temps`) |
| `src/edt/web/routes/a_traiter.py` | onglet « À traiter » (`/a-traiter`) |
| `src/edt/web/routes/accueil.py` | accueil (`/`), sans statistiques ; `/sante` (seule la clé `base.ecarts_schema` ajoutée) |
| `src/edt/web/templates/edt/` | gabarits des trois onglets ; `_ui.html` : macros partagées |
| `src/edt/web/static/css/edt.css`, `static/js/edt.js` | grille, panneau, impression ; recherche rapide, envoi automatique |
| `tests/web_edt/` | tests de ces écrans et services |

Les routes ne touchent pas l'ORM et n'appellent jamais `commit()` : elles
lisent les paramètres, appellent un service et choisissent la page. Les
services ne font que `flush()`.

## Onglet « Générer »

Section de menu `"Générer"` (ordre 250 et 260).

| Adresse | Rôle |
|---|---|
| `GET /calculs?semestre_id=` | semestre, récapitulatif, vérifications préalables, contraintes, options, lancement |
| `GET /calculs/verifications` | fragment des vérifications, recalculé quand on coche ou décoche une contrainte |
| `POST /calculs` | lance le calcul puis redirige vers son suivi |
| `GET /calculs/{id}` | suivi puis résultat |
| `GET /calculs/{id}/etat` | fragment interrogé **chaque seconde** par HTMX tant que le calcul tourne |
| `POST /calculs/{id}/arreter` | demande l'arrêt |
| `POST /calculs/{id}/relancer` | `longue=1` : relance avec `temps_limite_long_s` ; `autre_solution=1` : nouvelle graine |
| `GET /versions?semestre_id=` | historique des calculs et des versions |
| `POST /versions/{id}/activer`, `POST /versions/{id}/supprimer` | version active ; suppression (refusée pour la version active, confirmation dans la page) |

- **Récapitulatif** (`calculs.recapitulatif`) : besoins, séances de la
  semaine type après dépliage, groupes et enseignants concernés (fictifs
  exclus), salles ; il lit l'instantané du semestre comme le calcul.
- **Vérifications préalables** (`calculs.pre_verifier`) : les anomalies de
  `pre_verifier_semestre` (charges des groupes et des enseignants, salles,
  séances sans place possible, verrous contradictoires, besoins impossibles
  à déplier), groupées par gravité et chiffrées. Une **erreur** empêche de
  lancer (`ErreurPreVerification`, la page est réaffichée en 409 avec les
  choix de l'utilisateur) ; les avertissements (situations tendues) sont
  comptés et dépliables. Les pré-vérifications sont relancées au lancement
  avec les contraintes réellement cochées.
- **Contraintes** : le catalogue (`CATALOGUE`), libellé et description,
  tout coché par défaut ; ce qui est décoché est rappelé dans le suivi, le
  résultat et l'historique.
- **Options** : « rechercher jusqu'à 5 minutes » (`temps_limite_s`, 300 s
  par défaut) ou « 15 minutes » (`temps_limite_long_s`, 900 s) : les durées
  affichées sont lues dans les paramètres, jamais écrites dans l'écran ni
  dans les tests ; « chercher un emploi du temps plus confortable »
  (`OptionsSolveur.optimiser`, coché si `objectif_actif`, vrai par défaut ;
  le jeu de test minimal l'éteint, un test qui veut l'amélioration la
  demande avec `optimiser=1`),
  « proposer une autre solution » : graine tirée au hasard **et**
  `utiliser_indices=False`, sinon le moteur, guidé par les placements de la
  version active, peut redonner le même emploi du temps. Avec deux fils (le
  réglage par défaut), deux graines donnent deux emplois du temps
  différents ; avec un seul fil (`nb_workers=1`), CP-SAT trouve la même
  première solution quelle que soit la graine.
- **Un seul calcul à la fois** : si un calcul tourne, `/calculs` montre son
  suivi et ne propose pas le formulaire ; un `POST /calculs` concurrent mène
  au suivi du calcul en cours.
- **Résultat** (`calculs.resultat_calcul`), d'après `SuiviCalcul` et ses
  statistiques :
  - **succès** : séances placées (`nb_seances`), bilan des séances datées
    (`statistiques["instanciation"]` : total, replacées, à programmer avec
    besoin, date, horaire et raison), lien vers l'emploi du temps et vers
    « À traiter », quatre indicateurs, bouton « autre solution ». La ligne
    **« Qualité : »** reprend du message du moteur les seules phrases de
    qualité (`calculs.qualite_du_moteur` : « Bilan sur l'ensemble du
    semestre : x créneau(x) libre(s)… et y séance(s) le samedi », « on ne
    peut pas faire mieux », amélioration arrêtée au temps limite) ; ce que
    les compteurs disent déjà (séances placées, séances datées) n'est pas
    répété. Le moteur ne fait ce bilan que si l'amélioration était demandée ;
  - **arrêté avec une solution** (`statistiques["arrete_par_utilisateur"]`,
    état réussi) : « Calcul arrêté : meilleure solution conservée », la
    phrase « Calcul arrêté à votre demande : la meilleure solution trouvée a
    été conservée » et la version enregistrée, comme un succès. Le bouton
    d'arrêt l'annonce pendant le calcul (« si un emploi du temps a déjà été
    trouvé, le meilleur est conservé ») ;
  - **refusé** (`etat` impossible et `statistiques["refuse"]`) : « Calcul
    non lancé : impossibilités relevées avant la recherche », avec la liste
    `statistiques["impossibilites"]` ;
  - **impossible avec ces contraintes** : prouvé par la recherche ; la
    phrase est celle du moteur ;
  - **pas trouvé dans le temps imparti**, avec le bouton « relancer en
    cherchant jusqu'à 15 minutes » (`temps_limite_long_s`) ; **arrêté** avant
    toute solution : aucune version enregistrée ;
  - **erreur** : numéro du calcul et nom du fichier journal à communiquer,
    jamais de trace ni de nom d'exception (un défaut du moteur relevé par le
    vérificateur arrive ici, avec ses raisons) ;
  - dans tous les cas terminés, les **avertissements** du calcul
    (`SuiviCalcul.avertissements`), comptés et dépliables.

## Onglet « Emploi du temps »

Section de menu `SECTION_EMPLOI_DU_TEMPS` (ordre 300).

Paramètres de `GET /emploi-du-temps` : `semestre_id`, `version_id` (la
version active par défaut), au plus un de `groupe_id`, `enseignant_id`,
`salle_id` (ou `cible=groupe:12`, valeur du sélecteur), `couche` (`hebdo`
ou `date`), `affichage` (`semaine` ou `jour`), `date` (couche datée), `jour`
(semaine type, affichage jour), `seance` (`h<id>` séance hebdomadaire,
`o<id>` occurrence) pour mettre une séance en évidence et ouvrir son
panneau, `etendu` (`1` ou `0`) pour la vue d'un groupe (voir plus bas).

- **Ouverture** : sans cible dans l'adresse, la page s'ouvre sur le
  dernier groupe, enseignant ou salle consulté (cookie du navigateur
  `edt_derniere_cible`, rappelé sous le titre), sinon sur la **première
  classe** de l'année (`vues.cible_par_defaut` : classe, puis demi-groupe,
  puis promotion). Une cible retenue qui n'existe plus ici est oubliée sans
  bruit ; un lien explicite vers un groupe d'une autre année affiche la
  première classe avec un message, pas une erreur.

- **Sélecteurs** : semestre, version, « pour qui » (groupes par nature,
  enseignants, salles par campus) avec une **recherche rapide** qui filtre
  la liste sans tenir compte des accents (`edt.js`).
- **Trois affichages** : semaine type ; semaine datée (flèches, saut à une
  date, numéro de semaine du semestre et de l'année, jours fermés et
  vacances grisés avec leur libellé, séances annulées barrées, déplacées en
  pointillés, séances « à programmer » listées sous la grille) ; jour
  (semaine type ou date).
- **Vue d'un groupe** (`vues.Perimetre`) :
  - **restreinte** : les séances du groupe et de ses ascendants, que suivent
    tous ses étudiants. C'est la vue par défaut d'une promotion, d'une
    classe, d'un groupe transverse ou tiers-temps : sur l'établissement
    complet, la vue étendue d'une promotion empile plus de mille blocs ;
  - **étendue** (`etendu=1`) : en plus, les séances de ses descendants
    (classes, demi-groupes) et des groupes qui partagent une partie de ses
    étudiants (`conflits_groupes` : langues, options, tiers-temps),
    hachurées « une partie du groupe ». C'est la vue par défaut d'un
    demi-groupe : tout ce que suivent ses étudiants ;
  - un **bandeau** au-dessus de la grille dit ce qui est affiché, ce qui ne
    l'est pas (groupes laissés de côté et nombre de séances, par deux
    comptages SQL) et propose la bascule ; la navigation garde le choix.
  - Pour un enseignant ou une salle, le bandeau rappelle que toutes leurs
    séances sont affichées.
- **Bloc** : horaire, matière (et précision), type, groupes, enseignants,
  salle ; couleur stable par matière (CRC32 du code, 12 teintes) et liseré
  de la couleur du type de séance ; cadenas si verrouillée ; marque « ! »
  colorée selon la gravité de ses anomalies ; « certaines semaines » ou
  « le jj/mm/aaaa seulement » pour une séance qui n'a pas lieu chaque
  semaine (mode `total`, fenêtre de dates, séance ponctuelle).
- **Panneau latéral** (clic sur un bloc, fragment HTMX ; sans JavaScript le
  lien ouvre la même page avec `seance=`). L'adresse de retour vient de
  l'en-tête `HX-Current-URL` envoyé par HTMX (limitée à cet onglet) : détail, exigences de salle,
  anomalies de la séance, puis formulaire. Jour et heure de début parmi les
  débuts de la grille ce jour-là (plus l'heure actuelle ou imposée, pour un
  DST hors grille) ; salle parmi les **salles compatibles** (capacité,
  types acceptés, équipements, accessibilité, salle ou campus imposés,
  mêmes règles que l'instantané), libres d'abord, les occupées signalées
  avec la séance qui les occupe. Changer le jour ou l'heure recharge le
  formulaire.
- **Enregistrement** : `verifier_modification` est appelé avant toute
  écriture. Une erreur refuse et s'affiche en phrases dans le panneau ;
  avertissements et informations accompagnent le message de succès. **Sans
  vérificateur, aucune séance n'est déplacée** (message clair) : une
  modification n'est jamais enregistrée sans contrôle. Après un
  déplacement, les séances datées de la version, s'il y en a, sont
  recalculées par `instancier_version`.
- **Verrou** : verrouiller / déverrouiller (aucune vérification).
- **Séance datée** : déplacer **cette occurrence seulement** (statut
  « déplacée », date initiale conservée) après vérification de son jour par
  `verifier_occurrences_version` (la modification est remise en l'état si
  elle est refusée) ; annuler avec un motif facultatif ; programmer une
  séance « à programmer ». Un déplacement ou une annulation est une
  **retouche** : son origine devient « saisie manuelle ».
- **Séances datées** : produites par l'orchestration à la fin d'un calcul
  réussi. En semaine datée, « Recalculer les séances datées »
  (`modifications.recalculer_seances_datees`) réapplique la semaine type :
  `instancier_version` remplace les séances d'origine « semaine type » ou
  « replacement », **conserve** les saisies manuelles (`nb_conservees`,
  annoncées dans le message « vos N retouche(s) à la main… sont
  conservées ») et ne recrée pas la séance de la semaine type dans une
  semaine qu'une retouche couvre déjà (`nb_couvertes`) : une retouche reste
  la seule séance de sa semaine, sans dédoublonnage côté écran.
  L'utilisateur en est prévenu avant d'agir.
  Une version sans séances datées (version importée ou de référence)
  propose « produire les séances datées ».
- **Impression** : bouton « Imprimer » ; la feuille d'impression masque
  menus, sélecteurs et panneau, imprime la grille en A4 paysage avec ses
  couleurs et un titre (cible, semestre, version, période).
- **Exports** (servis par le lot échange) : `/export/emploi-du-temps.csv`
  et `/export/emploi-du-temps.ics` avec `version_id`, `couche` et la cible.

## Onglet « À traiter »

Section de menu `"À traiter"` (ordre 350). `GET /a-traiter` (filtre
`semestre_id` facultatif) : pour la version active de chaque semestre, les
anomalies de la semaine type puis des séances datées, regroupées par
gravité puis par contrainte (libellé et description du catalogue, 100
lignes au plus par contrainte, le total restant affiché). Chaque ligne mène
à l'emploi du temps du premier groupe, enseignant ou salle nommé par
l'anomalie (à défaut, le premier groupe de la séance), avec la séance mise
en évidence. Puis les séances « à programmer » avec leur raison (le
commentaire de l'occurrence, sinon une phrase par défaut).

**Nombre de points à traiter** : une seule définition, `ATraiter.total`,
reprise par la page, la pastille de l'onglet (`GET /a-traiter/compteur`,
somme des versions actives) et l'accueil (version active du semestre du
jour). Il compte les erreurs et avertissements des contraintes actives, sur
la semaine type et sur les séances datées, et chaque séance « à programmer »
**une fois** (l'avertissement « reste à programmer » que le vérificateur
émet pour elle n'est pas ajouté).

**Contraintes décochées lors du calcul** de la version : la vérification
porte sur toutes les règles, mais les écarts aux règles décochées sont
listés à part (« Écarts aux règles décochées lors du calcul ») et ne
comptent ni comme conflits ni comme points à traiter. Une retouche applique
les mêmes règles que le calcul (`raccords.contraintes_actives_de`, passées
au vérificateur), comme l'instanciation des séances datées.

**Séances datées importées** (origine « import », version de référence) :
elles ne se recalculent jamais depuis la semaine type, ce qui les
doublerait. Le bouton « recalculer » est remplacé par une explication, le
recalcul est refusé avant toute écriture (`ErreurRegleMetier`), et un
déplacement dans la semaine type d'une telle version est enregistré avec la
remarque que les séances datées n'ont pas été recalculées. Si
`instancier_version` refuse elle-même (même `ErreurRegleMetier`), l'écran
le dit de la même façon : jamais d'erreur 500.

## Accueil

`GET /` ne montre que ce dont l'équipe pédagogique a besoin pour avancer,
**sans aucune statistique** : ni compteurs d'entités, ni indicateurs, ni
nombres de séances ou de besoins. Quatre blocs, pour le semestre du jour :

1. **Prochaine étape** : une phrase et son écran (tableau ci-dessous).
2. **Emploi du temps du semestre** : le calcul en cours et « Suivre le
   calcul » ; la version active, son libellé et « Consulter » ; les points
   à traiter avec « Voir ce qui reste à traiter » (une action, pas une
   statistique ; « Aucun point à traiter » quand il n'y en a plus, le
   nombre de séances à programmer quand les vérifications ne sont pas
   disponibles) ; sinon « Aucun emploi du temps n'a encore été généré pour
   ce semestre ». Les quatre indicateurs de l'équipe restent sur le
   résultat d'un calcul (`edt/_suivi_calcul.html`).
3. **Dernier calcul**, seulement s'il s'est terminé **sans emploi du
   temps** (`tableau_de_bord.CalculSansSolution` : impossible, refusé par
   les vérifications préalables, pas trouvé dans le temps imparti, arrêté,
   erreur) : étiquette, date, message et « Voir le détail ». Un calcul
   réussi n'y figure pas (la version active le dit), un calcul en cours est
   dans le bloc 2, l'historique complet est sur `/versions`.
4. **État de la base de données**, seulement en cas de problème (base
   inaccessible, schéma périmé, base non initialisée) ou de base vide
   (invitation à charger un établissement fictif) :
   `routes.accueil.base_a_signaler`. Quand tout va bien, le bloc disparaît ;
   emplacement, mode du journal et environnement sont dans `/sante` et
   `python -m edt doctor`.

Étapes et écrans :

| Situation | Écran principal | Autres chemins |
|---|---|---|
| base vide | `/jeux` « Charger un établissement fictif » | `/import`, `/salles` |
| salles ou groupes manquants | `/salles` ou `/groupes` | `/import` |
| calcul en cours | `/calculs/{id}` | |
| aucun besoin pour le semestre | `/besoins` | `/import` |
| pas de version, dernier calcul en échec | `/calculs/{id}` | |
| pas de version | `/calculs?semestre_id=` | |
| anomalies ou séances à programmer | `/a-traiter?semestre_id=` | |
| sinon | `/emploi-du-temps?version_id=` | |

Une base au schéma périmé affiche « Le schéma de la base ne correspond
plus à l'application », la liste des écarts et la marche à suivre
(`python -m edt init-db --reset`). La route `/sante` n'a reçu qu'une clé :
`base.ecarts_schema`.

## Temps de réponse

Mesurés sur le jeu « grand » (`python -m edt jeu --taille grand --graine 0
--reference` : 99 salles, 136 enseignants, 229 groupes, 595 séances au
premier semestre), version de référence active, serveur local :

| Page | Temps |
|---|---|
| `/emploi-du-temps` (ouverture : classe A1-G1, 12 blocs) | 0,9 à 1,0 s |
| classe en semaine type / en jour / vue étendue (36 blocs) | 0,91 / 0,97 / 0,92 s |
| demi-groupe en semaine type / en jour | 0,59 / 1,0 s |
| enseignant en semaine type / en jour | 1,0 / 0,91 s |
| salle en semaine type / en jour | 0,81 / 0,74 s |
| `/a-traiter` | 0,90 s |
| `/versions` | 0,02 s |
| accueil | 0,68 à 0,97 s |
| `/a-traiter/compteur` (mémorisé / recalcul après une modification) | 0,005 / 0,59 s |

L'essentiel est la vérification de la semaine type (environ 0,5 s, dont le
chargement de l'instantané par le socle). Sans vérification, une grille se
construit en 5 à 40 ms.

### Mémoire et préchauffage

Les écrans de consultation passent par `memoire.memoriser` : vérification de
la semaine type et des séances datées d'une version, pré-vérifications et
récapitulatif d'un semestre sont gardés avec une **empreinte** de la base
(taille et date du fichier SQLite et de son journal, versions actives,
agrégats SQL sur leurs séances et occurrences). Toute écriture validée
l'invalide ; le résultat est recalculé une fois, puis servi depuis la
mémoire. Une semaine datée prend ses anomalies dans la vérification de tout
le semestre (une seule vérification datée par version). Les modifications
vérifient toujours sans mémoire, dans leur transaction.

Après un calcul réussi (dès que son écran l'affiche) ou l'activation d'une
version, `prechauffage.lancer` refait ces calculs dans un fil d'arrière-plan
unique : les écrans suivants sont servis tout de suite.

Mesures sur le jeu grand après un calcul en ligne de commande (base de
départ identique, serveur local) :

| Page | Avant | Après, avec préchauffage | Après, 1er affichage sans préchauffage | Après, affichages suivants |
|---|---|---|---|---|
| accueil | 0,62 à 1,12 s | 0,06 s | 2,09 s | 0,04 s |
| « Générer » | 1,60 à 2,18 s | 0,04 s | 1,60 s | 0,02 s |
| « À traiter » | 2,55 à 2,62 s | 0,05 s | 0,05 s | 0,03 s |
| pastille « À traiter » | 1,96 s puis 0,006 s | 0,009 s | 0,03 s | 0,008 s |
| emploi du temps d'une classe | 0,74 à 1,02 s | 0,18 s | 0,16 s | 0,08 s |
| emploi du temps d'un enseignant / d'une salle | 0,58 à 1,05 s | 0,04 à 0,08 s | 0,04 à 0,08 s | 0,02 à 0,04 s |
| semaine datée d'une classe | 0,79 à 1,19 s | 0,06 s | 0,07 s | 0,06 s |

Le premier affichage après une écriture faite hors de ces écrans (calcul en
ligne de commande, import, écrans de données) paie encore le calcul : deux
chargements de l'instantané, dont l'essentiel est le dépliage du socle
(`domaine.depliage.semaines_ouvertes`, environ 1,5 million d'appels sur
l'établissement complet), et la vérification datée du semestre.

## Disposition des blocs

`disposition.disposer(plages, ouverture, fermeture)` reçoit des `Plage(cle,
colonne, debut, fin)` (minutes depuis minuit, colonne = jour) et renvoie
l'axe et une `Position` par clé, en pourcentages :

1. **Axe** : de `min(ouverture, débuts)` à `max(fermeture, fins)`, arrondi
   à l'heure pleine, une graduation par heure. Un DST de 8 h à 12 h avec
   une grille qui ouvre à 8 h 30 élargit l'axe à 8 h et s'affiche à la
   minute près.
2. **Grappes** : dans une colonne, les plages triées par début (puis fin
   décroissante, puis clé) sont regroupées tant qu'elles recoupent la
   grappe en cours (plages semi-ouvertes : 10 h 30 ne recoupe pas 10 h 30).
3. **Voies** : chaque plage d'une grappe prend la première voie libre (dont
   la dernière plage est finie), sinon une nouvelle voie. Toute la grappe
   est découpée en `nb_voies` colonnes de même largeur.
4. **Élargissement** : un bloc s'étend vers la droite sur les voies
   voisines libres pendant toute sa durée.
5. `haut = (debut - axe.debut) / durée de l'axe`, `hauteur = (fin - debut)
   / durée`, `gauche = voie / nb_voies`, `largeur = étendue / nb_voies`.

Deux demi-groupes en TP au même moment occupent chacun la moitié de la
colonne. `Disposition.voies` donne, par jour, le plus grand nombre de
séances côte à côte : chaque colonne est au moins large de ce nombre de
fois `--edt-largeur-voie` (l'emploi du temps d'une promotion de
l'établissement complet en a jusqu'à 18 ; la grille défile), et un bloc
étroit ne garde que matière, groupes et salle (requête de conteneur CSS). Le gabarit place les blocs en `top`/`height` (%) et en variables
CSS `--edt-gauche`/`--edt-largeur` ; la hauteur d'une colonne vaut
`durée de l'axe × --edt-minute`. Le calcul est testé seul
(`tests/web_edt/test_disposition.py`).

## Raccords

`raccords.py` importe chaque fonction **au moment de l'appel**. Les trois
lots sont livrés et branchés (vérificateur, pré-vérifications du solveur,
instanciation) : aucun encart « pas encore disponible » n'apparaît. Le
mécanisme reste comme garde-fou.

| Raccord | Fonction attendue | Utilisée par |
|---|---|---|
| `pre_verifier_semestre` | `edt.services.generation.pre_verifier_semestre(session, semestre_id, contraintes_desactivees=(), *, version_base_id=None, tolerer_violations_heritees=False) -> list[Anomalie]` | Générer |
| `verifier_version` | `edt.services.verification.verifier_version(session, version_id, contraintes_actives=None) -> RapportVerification` | grille, panneau, À traiter, résultat, accueil |
| `verifier_occurrences_version` | `edt.services.verification.verifier_occurrences_version(session, version_id, date_debut=None, date_fin=None, contraintes_actives=None)` | semaine datée, déplacement d'une occurrence, À traiter |
| `verifier_modification` | `edt.services.verification.verifier_modification(session, version_id, seance_id, jour, debut, fin, salle_id)` | déplacement dans la semaine type |
| `instancier_version` | `edt.services.instanciation.instancier_version(session, version_id, contraintes_desactivees=None) -> RapportInstanciation` | « recalculer les séances datées », après un déplacement dans la semaine type |

- Module ou fonction absent, ou `ErreurNonLivre` levée : le raccord lève
  `ErreurRaccordIndisponible` (sous-classe d'`ErreurNonLivre`), journalisée
  en avertissement, dont le message s'affiche dans un encart (« Les
  vérifications automatiques ne sont pas encore disponibles dans cette
  version »). Un `ModuleNotFoundError` venant de *l'intérieur* d'un module
  livré n'est pas maquillé.
- `raccords.controler(...)` sert aux écrans de consultation : indisponible →
  encart « attention » ; `ErreurEDT` → encart « erreur » ; toute autre
  exception → trace dans le journal et encart « erreur inattendue », la
  page reste utilisable.
- `RapportVerification` est lu une seule fois (`lire_rapport`) : attributs
  `anomalies` (des `Anomalie`) et `indicateurs`. Les indicateurs sont lus
  par `vers_dict()` (ou les champs de la dataclass) avec les noms
  `pourcentage_seances_placees`, `pourcentage_utilisation_salles`,
  `changements_campus_groupes`, `changements_campus_enseignants`,
  `trous_creneaux`, `nb_semaines` ; un nom absent est journalisé et
  l'indicateur affiché « - ». Les comptes par gravité sont recalculés à
  partir des anomalies.
- Une anomalie sans séance (séance non placée, besoin impossible à
  déplier) est rattachée par `Anomalie.besoins` : lien vers le premier
  groupe du besoin dans « À traiter », liste des anomalies de la vue.
- `pre_verifier_semestre` couvre les besoins impossibles à déplier : ceux
  du récapitulatif ne s'affichent que si le raccord manquait.
- **Tests** : le cas nominal passe par les vraies fonctions (le jeu minimal
  est calculé par le vrai moteur). Un faux vérificateur
  (`faux_verificateur`) sert à provoquer une anomalie précise ; l'absence
  d'une fonction n'est testée que simulée (`sans_verificateur`, attribut ou
  module retiré le temps du test). Aucun test ne suppose l'emploi du temps
  particulier d'un moteur : jours et dates sont lus dans la version produite.

## Ajouter une vue

Exemple : une vue « par campus ».

1. **Service** : ajouter `CAMPUS = "campus"` à `vues.NatureCible`, sa clause
   dans `vues._filtre_cible` (par exemple `colonne_salle.in_(select(Salle.id)
   .where(Salle.campus_id == cible.id))`), son titre dans `_titre_cible` et
   ses options dans `options_cibles`. `construire_vue` fait le reste :
   lecture limitée à la période, disposition, anomalies.
2. **Route** : accepter `campus_id` dans `emploi_du_temps.page` et
   `Cible.depuis`, et l'ajouter aux paramètres d'export si le lot échange
   le prend en charge.
3. **Test** : un cas de plus dans
   `test_trois_affichages_pour_un_groupe_un_enseignant_une_salle`.

Pour un nouvel *affichage* (un mois, par exemple), ajouter un membre à
`Affichage`, calculer la période et les colonnes dans `construire_vue`, et
réutiliser `disposer` : il ne connaît que des colonnes et des minutes.

## Mise en page commune

Chaque gabarit de ces écrans charge `edt.css` dans le bloc `styles` et
`edt.js` (en `defer`) dans le bloc `scripts` de `base.html`, par les macros
`edt.ressources()` et `edt.script()` de `templates/edt/_ui.html`. Le badge
de l'onglet « À traiter » s'obtient en ajoutant dans `base.html`, après le
titre de l'onglet :
`{% if onglet.titre == "À traiter" %}<span hx-get="/a-traiter/compteur"
hx-trigger="load" hx-swap="outerHTML"></span>{% endif %}`.
