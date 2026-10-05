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
| `src/edt/services/emploi_du_temps/tableau_de_bord.py` | l'état de l'établissement pour l'accueil |
| `src/edt/web/routes/calculs.py` | onglet « Générer » (`/calculs`, `/versions`) |
| `src/edt/web/routes/emploi_du_temps.py` | onglet « Emploi du temps » (`/emploi-du-temps`) |
| `src/edt/web/routes/a_traiter.py` | onglet « À traiter » (`/a-traiter`) |
| `src/edt/web/routes/accueil.py` | accueil enrichi (`/`) ; `/sante` inchangée |
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
| `POST /calculs/{id}/relancer` | `longue=1` : relance « 5 minutes » ; `autre_solution=1` : nouvelle graine |
| `GET /versions?semestre_id=` | historique des calculs et des versions |
| `POST /versions/{id}/activer`, `POST /versions/{id}/supprimer` | version active ; suppression (refusée pour la version active, confirmation dans la page) |

- **Récapitulatif** (`calculs.recapitulatif`) : besoins, séances de la
  semaine type après dépliage, groupes et enseignants concernés (fictifs
  exclus), salles ; il lit l'instantané du semestre comme le calcul.
- **Vérifications préalables** (`calculs.pre_verifier`) : les besoins
  impossibles à déplier dont la contrainte est active (toujours connus) et
  les anomalies de `pre_verifier_semestre`, groupées par gravité. Une
  **erreur** empêche de lancer (`ErreurPreVerification`, la page est
  réaffichée en 409 avec les choix de l'utilisateur), un avertissement non.
  Les pré-vérifications sont relancées au lancement avec les contraintes
  réellement cochées.
- **Contraintes** : le catalogue (`CATALOGUE`), libellé et description,
  tout coché par défaut ; ce qui est décoché est rappelé dans le suivi, le
  résultat et l'historique.
- **Options** : « rechercher jusqu'à 1 minute » (`temps_limite_s`) ou
  « 5 minutes » (`temps_limite_long_s`), « chercher un emploi du temps plus
  confortable » (`OptionsSolveur.optimiser`, coché si `objectif_actif`),
  « proposer une autre solution » (graine tirée au hasard).
- **Un seul calcul à la fois** : si un calcul tourne, `/calculs` montre son
  suivi et ne propose pas le formulaire ; un `POST /calculs` concurrent mène
  au suivi du calcul en cours.
- **Résultat** (`calculs.resultat_calcul`) : succès (lien vers l'emploi du
  temps, quatre indicateurs, séances à programmer, bouton « autre
  solution ») ; **impossible avec ces contraintes** (prouvé) ; **pas trouvé
  dans le temps imparti** avec le bouton « relancer en cherchant jusqu'à
  5 minutes » ; arrêté ; erreur : numéro du calcul et nom du fichier journal
  à communiquer, jamais de trace ni de nom d'exception.

## Onglet « Emploi du temps »

Section de menu `SECTION_EMPLOI_DU_TEMPS` (ordre 300).

Paramètres de `GET /emploi-du-temps` : `semestre_id`, `version_id` (la
version active par défaut), au plus un de `groupe_id`, `enseignant_id`,
`salle_id` (ou `cible=groupe:12`, valeur du sélecteur), `couche` (`hebdo`
ou `date`), `affichage` (`semaine` ou `jour`), `date` (couche datée), `jour`
(semaine type, affichage jour), `seance` (`h<id>` séance hebdomadaire,
`o<id>` occurrence) pour mettre une séance en évidence et ouvrir son
panneau. Un lien vers un groupe d'une autre année affiche le groupe par
défaut avec un message, pas une erreur.

- **Sélecteurs** : semestre, version, « pour qui » (groupes par nature,
  enseignants, salles par campus) avec une **recherche rapide** qui filtre
  la liste sans tenir compte des accents (`edt.js`).
- **Trois affichages** : semaine type ; semaine datée (flèches, saut à une
  date, numéro de semaine du semestre et de l'année, jours fermés et
  vacances grisés avec leur libellé, séances annulées barrées, déplacées en
  pointillés, séances « à programmer » listées sous la grille) ; jour
  (semaine type ou date).
- **Groupes liés** : l'emploi du temps d'un groupe montre les séances des
  groupes en conflit avec lui (`conflits_groupes`). Celles de ses
  ascendants le concernent en entier ; celles de ses descendants et des
  groupes chevauchants sont marquées « une partie du groupe ».
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
  séance « à programmer ».
- **Séances datées absentes** : la page le dit et propose « produire les
  séances datées » (`instancier_version`).
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

`GET /a-traiter/compteur` rend le nombre d'erreurs et d'avertissements
plus les séances à programmer, pour un badge dans le menu (voir « Mise en
page commune »).

## Accueil

`GET /` garde l'état de la base et les compteurs, et ajoute pour le
semestre du jour : la **prochaine étape** en une phrase avec son lien
(saisir les données, suivre le calcul en cours, vérifier puis générer,
relire un calcul qui a échoué, traiter les anomalies, consulter), la
version active, les quatre indicateurs (pourcentage de séances placées,
nombre de conflits, taux d'utilisation des salles, changements de campus)
et le nombre de points à traiter. Pour l'étape « saisir », le lien est le
premier écran de saisie présent dans le menu (`/besoins`, `/salles`...).
Une base au schéma périmé affiche « Le schéma de la base ne correspond
plus à l'application », la liste des écarts et la marche à suivre
(`python -m edt init-db --reset`). La route `/sante` n'a reçu qu'une clé :
`base.ecarts_schema`.

## Temps de réponse

Mesurés sur le jeu « grand » (`python -m edt jeu --taille grand --graine 0
--reference` : 99 salles, 136 enseignants, 229 groupes, 595 séances au
premier semestre) : chaque page de grille, « À traiter » et l'accueil
répondent en moins d'une seconde ; l'essentiel est la vérification de la
semaine type (environ 0,5 s, dont le chargement de l'instantané). Sans
vérification, une grille se construit en 5 à 40 ms. La semaine datée ne
vérifie que la semaine affichée.

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

## Raccords en attente

`raccords.py` importe chaque fonction **au moment de l'appel**. Le
vérificateur (`edt.services.verification`) est livré et branché ; les
pré-vérifications et l'instanciation arrivent avec le solveur : leurs encarts
restent affichés jusque-là.

| Raccord | Fonction attendue | Utilisée par |
|---|---|---|
| `pre_verifier_semestre` | `edt.services.generation.pre_verifier_semestre(session, semestre_id, contraintes_desactivees=()) -> list[Anomalie]` | Générer |
| `verifier_version` | `edt.services.verification.verifier_version(session, version_id, contraintes_actives=None) -> RapportVerification` | grille, panneau, À traiter, résultat, accueil |
| `verifier_occurrences_version` | `edt.services.verification.verifier_occurrences_version(session, version_id, date_debut=None, date_fin=None, contraintes_actives=None)` | semaine datée, déplacement d'une occurrence, À traiter |
| `verifier_modification` | `edt.services.verification.verifier_modification(session, version_id, seance_id, jour, debut, fin, salle_id)` | déplacement dans la semaine type |
| `instancier_version` | `edt.services.instanciation.instancier_version(session, version_id) -> RapportInstanciation` | « produire les séances datées », après un déplacement |

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
- **Branchement** : rien à modifier ici. Dès qu'un module existe, ses
  encarts disparaissent. À vérifier à l'arrivée du solveur : que
  `instancier_version` remplace les occurrences issues de l'instanciation
  (les déplacements et annulations manuels d'une version sont alors
  recalculés après un déplacement dans la semaine type), et que
  `pre_verifier_semestre` ne répète pas les besoins impossibles à déplier
  (déjà affichés à partir de l'instantané).
- **Tests** : `tests/web_edt/conftest.py` installe de faux modules aux mêmes
  noms (`faux_verificateur`, `fausse_instanciation`) ou simule leur absence
  (`sans_verificateur`) ; sans ces fixtures, c'est le vrai vérificateur qui
  tourne. Chaque écran est testé avec, sans, et avec le vrai.

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

En attendant la nouvelle mise en page (blocs `styles` et `scripts`), chaque
gabarit de ces écrans charge `edt.css` en tête de son bloc `contenu` et
`edt.js` (en `defer`) à la fin, par les macros `edt.ressources()` et
`edt.script()` de `templates/edt/_ui.html` : il suffira de déplacer ces deux
appels dans les blocs dédiés.
