# Interface web

Ce document décrit la mise en page commune à tous les écrans, la façon de
ranger un écran dans la navigation, le mécanisme générique qui donne ses
écrans à chaque entité de l'espace « Données », et le panneau Assistant.
Le socle (routes découvertes, `rendre`, `rediriger`, `SessionRequete`,
erreurs) est décrit dans [`architecture.md`](architecture.md).

Public visé : un responsable de scolarité **non informaticien**. Règles
suivies partout : vocabulaire métier, une phrase d'explication en tête de
chaque liste, aucune trace technique à l'écran, erreurs de saisie affichées
sous le champ concerné, saisie conservée après une erreur.

## 1. Navigation

La barre du haut porte **cinq onglets**, dans un ordre fixe. Chaque écran se
range dans un onglet par la `section` de son entrée de menu
(`edt/web/menu.py`, `ONGLETS`) :

| Onglet          | Sections                                              | Ordre des entrées |
|-----------------|-------------------------------------------------------|-------------------|
| Accueil         | (aucune section)                                      | 0 - 99            |
| Données         | `Référentiels`, `Planification`, `Configuration`, `Données` | 100 - 599   |
| Générer         | `Générer` (`SECTION_GENERER`)                         | 600 - 699         |
| Emploi du temps | `Emploi du temps` (`SECTION_EMPLOI_DU_TEMPS`)         | 300 - 399         |
| À traiter       | `À traiter` (`SECTION_A_TRAITER`)                     | 700 - 799         |

- Un onglet **sans entrée n'est pas affiché**. Son adresse est celle de sa
  première entrée.
- Un onglet de plusieurs entrées a une **sous-navigation** à gauche (une
  bande défilante sur un petit écran) et un **menu déroulant** au survol ou
  au clavier, qui donne accès à toutes ses pages depuis n'importe où.
- Dans l'onglet Données, les entrées sont regroupées sous des titres
  parlants, dans cet ordre : « Établissement » (section Référentiels),
  « Besoins et disponibilités » (Planification), « Règles et calendrier »
  (Configuration), « Import et export » (Données), « Jeux de données ». Une
  entrée choisit un autre groupe avec son champ `groupe`.
- Une entrée dont la section n'est rangée dans aucun onglet va dans l'onglet
  Données, sous un groupe portant le nom de sa section, et un avertissement
  est journalisé.
- Sur un écran étroit, les cinq onglets passent sur deux rangées plutôt que
  de défiler : aucun n'est caché.
- Un onglet peut afficher un **compteur** (pastille rouge) chargé par HTMX :
  `COMPTEURS_ONGLETS` de `menu.py` associe au titre de l'onglet l'adresse
  d'un fragment qui renvoie `<span class="edt-compteur-onglet">N</span>`, ou
  rien. Aujourd'hui : « À traiter » → `/a-traiter/compteur`. Sans JavaScript,
  l'onglet reste un lien simple ; si le fragment échoue, l'échec est noté dans
  la console sans bandeau d'erreur (`data-erreur-silencieuse`).
- L'onglet actif est celui dont une entrée correspond à l'adresse affichée
  (l'entrée `/salles` couvre aussi `/salles/12`).

### Ranger un écran

```python
# src/edt/web/routes/calculs.py
from edt.web.menu import SECTION_GENERER, EntreeMenu

MENU = [EntreeMenu("Lancer un calcul", "/calculs", section=SECTION_GENERER, ordre=600)]
```

```python
# un import dans l'onglet Données, groupe « Import et export »
MENU = [EntreeMenu("Importer", "/import", section=SECTION_DONNEES, ordre=400)]
```

`EntreeMenu(libelle, url, section="", ordre=100, groupe="")` ; les constantes
existantes ne changent pas. Les groupes connus sont `GROUPE_ETABLISSEMENT`,
`GROUPE_BESOINS`, `GROUPE_REGLES`, `GROUPE_IMPORT_EXPORT`, `GROUPE_JEUX`.

## 2. Mise en page commune

### Gabarit de base (`templates/base.html`)

| Bloc       | Rôle                                                             |
|------------|------------------------------------------------------------------|
| `titre`    | titre de la page (onglet du navigateur et titre `h1`)            |
| `actions`  | boutons à droite du titre                                        |
| `contenu`  | corps de la page                                                 |
| `styles`   | feuilles de style propres à l'écran, dans l'en-tête              |
| `scripts`  | scripts propres à l'écran, en fin de page                        |

Le gabarit pose le bandeau « données fictives », la barre et ses onglets, la
sous-navigation, la zone de messages `#messages`, le pied de page et le
panneau Assistant. La classe `js` est ajoutée à `<html>` quand JavaScript
fonctionne (`.sans-js` masque alors ce qui ne sert que sans lui).

### Macros partagées (`templates/macros.html`, `{% import "macros.html" as ui %}`)

| Macro | Rôle |
|---|---|
| `ui.message(texte, niveau, details)` / `ui.messages(liste)` | message `succes`, `info`, `attention`, `erreur` |
| `ui.etiquette(texte, niveau)` | pastille (niveaux ci-dessus et `neutre`) |
| `ui.tableau(colonnes, lignes, vide)` | tableau simple ; avec `{% call(ligne) %}` : colonne d'actions |
| `ui.cellule(valeur)` | contenu de cellule : tiret si vide, Oui / Non pour un booléen |
| `ui.formulaire(action, ...)` | formulaire simple |
| `ui.champ(nom, libelle, valeur, type, requis, aide, erreur, options, attributs, unite, conteneur, large)` | champ complet : libellé, aide, erreur liée par `aria-describedby` ; `type` `multiple` = liste de cases filtrable, `cases` = quelques cases sur une ligne (jours de la semaine, campus) |
| `ui.bouton_suppression(action)` / `ui.bouton_action(action, libelle, classe, confirmation, champs)` | bouton qui envoie un POST, confirmation facultative |
| `ui.pagination(page, nb_pages, url_page, cible)` | pagination ; `cible` : zone remplacée par HTMX |
| `ui.couleur(code)` | petit carré de couleur |

Les écrans « Données » ont en plus `templates/donnees/_ui.html`
(`d.champ`, `d.rubriques`, `d.barre_liste`, `d.cellule`, `d.retour`).

### Classes CSS réutilisables (`static/css/app.css`)

Aucune couleur en dur dans un écran : les variables `--marque`, `--succes`,
`--attention`, `--erreur`, `--bordure`... sont dans `:root`.

| Classe | Usage |
|---|---|
| `carte`, `carte-erreur`, `carte-attention`, `repliable` (sur `<details>`) | blocs de contenu |
| `introduction` | phrase d'explication sous le titre |
| `tableau-defilant` + `tableau`, `nombre`, `colonne-actions` | tableaux (défilement horizontal sur petit écran) |
| `tableau-compact` (avec `cellule-texte`, `deux-lignes`, `liste-abregee`, `plus`, `abrege`) | listes longues : une ligne par cellule, deux au plus pour un texte libre, listes abrégées « +3 » avec le détail au survol, colonne d'actions toujours visible à droite pendant le défilement horizontal |
| `tri`, `tri-croissant`, `tri-decroissant` | en-tête de colonne triable |
| `pagination`, `pagination-pages` | pagination |
| `formulaire`, `formulaire-large`, `grille-champs`, `champ-large`, `formulaire-actions`, `formulaire-ligne` | formulaires (grille de champs, formulaire compact sur une ligne) |
| `bouton`, `bouton-principal`, `bouton-danger`, `bouton-danger-plein`, `bouton-petit` | boutons |
| `barre-liste`, `barre-liste-resume` | recherche et filtres d'une liste |
| `message-*`, `etiquette-*`, `compteurs`, `definitions` | états et chiffres |
| `arbre`, `arbre-noeud`, `matrice` | arbre (groupes), matrice de saisie (trajets) |
| `cases-en-ligne` | cases à cocher côte à côte (contrôle `cases`) |
| `tableau-regles` (avec `cellule-empilee`), `ligne-proposee`, `apercu-regle`, `apercu-phrase`, `cartes-types`, `carte-type`, `exemples-regle`, `liste-regles-touchees` | règles personnalisées : liste, règle proposée surlignée, aperçu de la phrase, choix du type, règles touchées par une suppression |
| `ligne-boutons`, `colonnes`, `liste-simple`, `discret`, `pre-ligne` | utilitaires |

### Comportements communs (`static/js/app.js`)

| Attribut ou classe | Effet |
|---|---|
| `data-confirmation="..."` sur un formulaire | confirmation avant l'envoi |
| `data-visible-si="champ:v1,v2"` sur un bloc | bloc affiché (et envoyé) seulement si `champ` vaut v1 ou v2 ; plusieurs conditions séparées par `;` doivent toutes tenir |
| `data-attente="..."` sur un formulaire | boutons désactivés et texte d'attente pendant l'envoi (opérations longues) |
| `.choix-multiple` | filtre au clavier d'une liste de cases à cocher |
| `<details id="...">` dans une zone remplacée par HTMX | reste ouvert après le remplacement |
| `data-arbre="clé"` + `<details data-noeud="...">` ; boutons `data-arbre-action="deplier\|replier"` `data-arbre-cible="id"` | arbre repliable, état conservé d'une page à l'autre (stockage local), « tout déplier » / « tout replier » (boutons masqués sans JavaScript) |
| `data-erreur-silencieuse` sur un élément HTMX accessoire | son échec est noté dans la console, sans bandeau d'erreur |
| erreurs HTMX | le fragment d'erreur renvoyé s'affiche dans `#messages` |

Ces comportements s'appliquent aussi aux fragments chargés par HTMX.

## 3. Le mécanisme générique de l'espace « Données »

Chaque entité est décrite **une fois**, de façon déclarative, dans
`edt.services.referentiels` ; les services génériques et les écrans
génériques s'en déduisent.

```
description (Entite)  ──▶  services génériques (operations)  ──▶  écrans génériques (Ecran)
 champs, colonnes,          lister, lire, creer, modifier,          liste, formulaire,
 filtres, règles            analyser_suppression, supprimer         suppression, ligne
```

### Décrire une nouvelle entité

1. Dans le module de sa famille (`lieux`, `personnes`, `enseignements`,
   `calendrier`), déclarer un `Entite` :

   ```python
   EQUIPEMENT = Entite(
       code="equipement", modele=Equipement,
       nom="équipement", pluriel="équipements", feminin=False,
       demonstratif="Cet équipement", nouveau="Nouvel équipement",
       libelle=lambda e: e.libelle,
       champs=(
           Champ("code", "Code", obligatoire=True, aide="Par exemple « VIDEOPROJ »."),
           Champ("libelle", "Libellé", obligatoire=True),
       ),
       colonnes=(
           Colonne("code", "Code", lambda e: e.code, tri=Equipement.code),
           Colonne("libelle", "Libellé", lambda e: e.libelle, tri=Equipement.libelle),
       ),
       tri_defaut="code",
       recherche=(Equipement.code, Equipement.libelle),
       uniques=(Unicite(("code",), "Un équipement porte déjà ce code.", "code"),),
       usages=(Decompte(par_colonne(besoin_equipement.c.equipement_id),
                        "est requis par 1 besoin", "est requis par {n} besoins"),),
   )
   ```

2. L'ajouter à `ENTITES` dans `catalogue.py`.
3. Déclarer son écran dans un module `edt/web/routes/donnees_*.py` :

   ```python
   ECRAN = Ecran(EQUIPEMENT, "/equipements", introduction="...", edition_en_ligne=True)
   routeur.include_router(routeur_ecran(ECRAN))
   MENU = [EntreeMenu("Équipements", "/equipements", section=SECTION_REFERENTIELS, ordre=130)]
   ```

Types de champ (`TypeChamp`) et contrôle affiché :

| Type | Saisie | Contrôle |
|---|---|---|
| `TEXTE`, `TEXTE_LONG` | texte (longueur maximale déduite de la colonne) | `text`, `textarea` |
| `ENTIER` | nombre, bornes `minimum` / `maximum`, `unite` | `number` |
| `BOOLEEN` | case (absente = non) | `checkbox` |
| `CHOIX` | une valeur de `choix` (`choix_enum`, `choix_jours`) | `select` |
| `REFERENCE` / `REFERENCES` | identifiant(s) d'une autre table (`Reference`, avec `condition` facultative) | `select` / cases filtrables |
| `DATE` | « 2026-09-07 » ou « 07/09/2026 » | `date` |
| `HEURE` | « 08:30 », « 8 h 30 » → minutes | `time` |
| `DUREE` | « 2 h », « 1 h 30 », « 90 min » → minutes | `text` |
| `COULEUR` | « #2f6fad » | `color` |

Possibilités de la description :

- colonnes de liste (`Colonne`) : une valeur peut être une **liste**
  (groupes, équipements) affichée « A1-G1, A1-G2 +3 » (`apercu` éléments,
  liste complète au survol) ; `texte=True` pour un texte libre qui peut tenir
  sur deux lignes (les autres colonnes restent sur une ligne) ; `detail` pour
  le texte complet d'une valeur abrégée (volume d'un besoin : « 6 séances ·
  14/09 → 15/11 », détail au survol) ; `filtre="semestre"` pour omettre la
  colonne quand ce filtre est appliqué (elle serait identique partout) ;

- `groupe` d'un champ : rubrique du formulaire (`fieldset`) ;
- `visible_si=("mode_volume", ("total",))` : champ utile à un seul mode ;
- `virtuel=True` + `lire` : champ de formulaire sans colonne, traduit par le
  crochet `preparer` (les deux nombres d'un besoin, la forme d'une
  indisponibilité, les chevauchements d'un groupe) ;
- `filtres` (`Filtre`) : choix de filtre, condition SQL, valeur par défaut
  (`defaut`), champ prérempli à l'ajout (`champ`) ;
- `uniques` : contrôlés **avant** l'écriture, erreur sous le champ ;
- `usages` : ce qui empêche une suppression (`ErreurRegleMetier` levée
  avant d'atteindre la base, avec le décompte) ; `consequences` : ce que la
  suppression emporte, et ce qui se passe ensuite (« 17 séances déjà placées
  resteront sans salle : elles apparaîtront dans « À traiter » et
  retrouveront une salle au prochain calcul ou par un replacement à la
  main ») ;
- `Decompte(compter, singulier, pluriel, lien=..., objets=...)` : `lien` est
  l'adresse de la liste des éléments comptés, avec `{id}` pour l'élément à
  supprimer, et `objets` les nomme pour le libellé du lien (« Voir ces
  12 besoins »). Exemple : `lien=TOUS_SEMESTRES + "&salle_imposee={id}"`,
  `objets=BESOINS` (`TOUS_SEMESTRES` = `/besoins?semestre=`, car le
  décompte compte tous les semestres). Liens en place : salle imposée,
  enseignant, matière, type de séance et groupe vers les besoins ; campus et
  type de salle vers les salles. Les autres usages (campus imposé, types de
  salle et équipements demandés par un besoin) n'ont pas de filtre
  correspondant : le refus reste sans lien ;
- crochets `valider` (contrôles entre champs), `preparer`,
  `apres_ecriture`, `apres_creation` (après un ajout seulement, dans la même
  transaction : une année scolaire créée reçoit ses règles proposées),
  `analyser` (refus et conséquences calculés, en textes ou en
  `operations.Raison` avec un lien).

### Écran générique (`edt.web.donnees.ecrans.Ecran`)

`routeur_ecran(ecran)` donne : la liste (`GET <p>`, paramètres `q`, `tri`,
`sens=desc`, `page`, un paramètre par filtre), le formulaire d'ajout
(`GET <p>/nouveau`, prérempli par les paramètres d'adresse qui portent le nom
d'un champ), la création (`POST <p>`), la modification (`GET`/`POST <p>/<n>`),
la confirmation et la suppression (`GET`/`POST <p>/<n>/supprimer`), la ligne
seule et son édition sur place (`<p>/<n>/ligne`, `<p>/<n>/ligne/edition`),
la confirmation et la suppression sur place (`GET <p>/<n>/ligne/suppression`,
`POST <p>/<n>/ligne/supprimer`).

**Suppression.** Avec JavaScript, « Supprimer » remplace la ligne par une
ligne de confirmation : ce que la suppression emportera (« Supprimer
définitivement » / « Annuler »), ou pourquoi elle est refusée, avec un lien
vers les éléments qui bloquent (« Fermer » rend la ligne). La confirmation
supprime puis rafraîchit la zone de liste (compte et page à jour) et affiche
le message dans `#messages` (échange HTMX « hors bande »). Sans JavaScript, le
même lien ouvre la page de confirmation, qui fait la même chose.
`AnalyseSuppression` porte des `Raison(texte, lien, libelle_lien)` : `blocages`
et `effets` (`refus` et `consequences` en donnent les textes), et `regles` :
les règles personnalisées touchées (voir « Supprimer une cible visée par des
règles » au § 4).

- Un filtre qui a une valeur par défaut (l'année ou le semestre en cours) est
  rappelé dans le résumé (« 0 groupe (Année : 2026-2027) ») mais ne compte pas
  comme un filtre posé : une liste vide invite alors à ajouter un élément ou
  à choisir une autre valeur ; une liste vidée par une recherche ou un filtre
  posé propose de les effacer. Une valeur de filtre absente de ses choix, un
  numéro de page ou un identifiant aberrant donnent un message, pas une
  erreur.
- Les entiers saisis sont bornés : par le champ (`maximum`, par exemple
  100 000 places ou étudiants) puis par ce que la base sait enregistrer ;
  seuls les chiffres 0 à 9 sont acceptés.
- La liste fonctionne sans JavaScript (liens et bouton « Filtrer ») ; avec
  HTMX, recherche, filtres, tri et pagination remplacent seulement la zone
  `#zone-liste` (en-tête `HX-Target: zone-liste`) et l'adresse suit.
- Une saisie refusée réaffiche le formulaire (statut 422) avec les messages
  sous les champs et les valeurs saisies ; rien n'est écrit.
- Retour : « Modifier » et « Supprimer » portent l'adresse de la liste
  (paramètre `retour`, limité aux pages de l'écran : recherche, filtres, tri
  et page). Après un enregistrement ou une suppression, on revient sur cette
  liste ; si sa page a disparu, à la dernière page qui existe. « Annuler »
  ramène là d'où l'on venait : la liste, ou la fiche quand la suppression a
  été demandée depuis la fiche (`depuis=fiche`). L'adresse d'une liste
  omet les filtres vides sans valeur par défaut et le tri par défaut.
- Options d'un écran : `introduction`, `gabarit_liste` (étendre
  `donnees/liste.html`, blocs `avant_liste` et `apres_liste`),
  `contexte_liste`, `edition_en_ligne`, `actions_ligne` (« Dupliquer »),
  `liens`, `parent`, `titre_page`, `complement_zone` (gabarit ajouté au bas
  de la zone de liste, donc rafraîchi avec elle : la charge hebdomadaire des
  besoins), `suite_creation(session, cree)` (ce qu'une création a entraîné :
  `SuiteCreation(texte, adresse)`, phrase ajoutée au message et page où aller
  ensuite à la place de la liste).
- Mesures de référence (jeu grand, 1 280 px de large) : une ligne de liste
  fait 37 px (une ligne de texte) ou 58 px (deux lignes).

## 4. Les écrans de l'espace « Données »

| Groupe | Adresse | Écran |
|---|---|---|
| Établissement | `/campus` | campus (édition sur place) et matrice des trajets (`POST /campus/trajets`) |
| | `/salles` | salles : type, capacité, étage, équipements, accessibilité ; filtres campus, type, accessibilité |
| | `/types-salle`, `/equipements`, `/types-seance` | listes configurables (édition sur place) |
| | `/enseignants` | enseignants, statut, maxima (« 6 h »), enseignant fictif |
| | `/groupes`, `/groupes/arbre` | groupes (liste filtrable et arbre repliable promotion > classe > demi-groupe, groupes à part regroupés par nature), chevauchements, salle accessible requise |
| | `/etudiants` | étudiants (fictifs) et inscriptions |
| | `/matieres` | matières, code module, promotion |
| Besoins et disponibilités | `/besoins` | besoins : filtres semestre, promotion, groupe (et ses sous-groupes), matière, type, enseignant, salle imposée, volume ; ajout rapide ; duplication (`POST /besoins/<n>/dupliquer`) ; charge hebdomadaire des groupes concernés par les filtres (repliée, les plus chargés d'abord, 60 au plus) ; formulaire par mode de volume |
| | `/indisponibilites` | enseignant, salle ou groupe ; chaque semaine ou période datée ; plage horaire |
| Règles et calendrier | `/regles` | règles personnalisées de l'année : voir ci-dessous |
| | `/calendrier` | années et semestres ; ajout d'une année par défaut (`POST /calendrier/annees/par-defaut`, qui propose aussi les règles de base : « 4 règles proposées, à revoir dans Règles personnalisées ») ; une année créée par le formulaire générique (`POST /calendrier/annees`) mène à ses règles proposées (`/regles?annee=<n>&etat=proposee`, « 4 règles personnalisées proposées pour cette année, ci-dessous ») ; fériés (`POST /calendrier/annees/<n>/feries`) ; sous-écrans `/calendrier/annees`, `/calendrier/semestres`, `/calendrier/jours-fermes` |
| | `/grille` | créneaux de la grille (édition sur place) |
| | `/parametres` | paramètres construits depuis `Parametres` (libellé, description, unité, bornes) : un entier devient un champ numérique (une heure : « 08:30 »), un oui / non une case, un ensemble de jours (`type == "jours"` : les jours travaillés de l'établissement) sept cases du lundi au dimanche, envoyées « 0,1,2,3,4 » au service (aucune case cochée : « au moins un jour est attendu » sous le champ), un paramètre **à choix** (`type == "choix"`, comme le mode de la pause déjeuner : horaires déterminés, pause libre bornée, pas de pause) une liste déroulante avec les libellés du contrat ; les champs utiles à un seul mode sont masqués selon ce mode (`VISIBLE_SI` de `reglages.py`) ; une incohérence du déjeuner s'affiche sous le champ fautif ; `POST /parametres/defaut` |
| Jeux de données | `/jeux` | établissement fictif petit / moyen / grand (`edt.services.jeux`), graine, version de référence ; remise à zéro |

Page `/jeux` : la base n'est touchée qu'au travers de
`edt.services.jeux.recharger_jeu_fictif` et `remettre_a_zero`, qui ouvrent
leurs propres sessions (ces routes n'ont pas de `SessionRequete`). Un
chargement dans une base qui contient déjà des données, et toute remise à
zéro, exigent une case de confirmation qui liste ce qui sera effacé. Pendant
un calcul, les deux actions sont refusées avec un message clair. Une seule
de ces opérations tourne à la fois : une seconde demande pendant la première
(deux personnes qui cliquent en même temps) est refusée tout de suite avec
« un chargement ou une remise à zéro est déjà en cours », sans toucher à la
base ; une page demandée pendant ce temps reçoit « base de données occupée,
réessayer », jamais le conseil d'initialiser la base.

### Règles personnalisées (`/regles`)

Données › Règles et calendrier › « Règles personnalisées »
(`edt/web/routes/donnees_regles_personnalisees.py`). L'écran ne parle qu'au
contrat `edt.services.regles` (`lister`, `lire_par_id`, `valider`, `creer`,
`modifier`, `supprimer`, `changer_etat`, `activer`, `proposer_pour_annee`) ;
le formulaire est construit par `edt.web.donnees.regles` depuis
`CATALOGUE_TYPES`. Rien n'est propre à un type dans la couche web : un type
ajouté au catalogue a son formulaire sans autre code.

**Liste.** Une année scolaire à la fois (`annee`, par défaut l'année en
cours). Filtres `etat`, `type`, `niveau`, `origine`, `portee` (nature de la
cible) et `groupe` (les règles d'un groupe **et** celles qu'il hérite de ses
ancêtres) ; recherche `q`, sans tenir compte des accents ni de la casse, sur
le code, la phrase, la cible et le commentaire ; 50 règles par page. Chaque
ligne donne le code, la phrase (deux lignes au plus, entière au survol), le
type et l'origine, la portée et la période, le niveau, l'état et les
actions ; le tableau tient dans 1 280 px. Une règle **proposée** a son fond
surligné, l'état « Proposée » et deux boutons : « Activer » et « Écarter »
(elle devient inactive) ; une règle active a « Désactiver », une inactive
« Activer ». Un bandeau compte les règles proposées de l'année (« sans effet
tant qu'elles ne sont pas confirmées »), avec « Les voir » (toutes les
proposées de l'année, sans les autres filtres ni la recherche) et « Tout
confirmer » (`POST /regles/confirmer` : celles qui feraient doublon avec une
règle active restent proposées et le message dit pourquoi). « Proposer les
règles de base » (`POST /regles/proposer`) appelle `proposer_pour_annee` :
règles de base et reprise de l'année précédente, en état proposé ; le
message compte les créées, celles déjà présentes et celles à revoir (cible
absente cette année). La liste fonctionne sans JavaScript ; avec HTMX,
recherche, filtres et pagination remplacent `#zone-liste`.

**Ajout guidé.** `GET /regles/nouveau` : étape 1, une carte par type
(libellé, description, exemples de phrases, portées admises).
`GET /regles/nouveau?type=PAUSE_DEJEUNER` : étape 2, le formulaire.

| Rubrique | Champs |
|---|---|
| Portée | `portee` (natures admises par le type ; « l'établissement » seulement si le type l'admet) puis une liste par nature, `cible_<nature>`, affichée selon la nature choisie (`data-visible-si`) ; groupes en arbre (promotion, └ classe, └ demi-groupe), groupes à part ensuite |
| Paramètres | un champ par `ChampRegle`, nommé `p_<nom>` (`PREFIXE_PARAMETRE`) : `JOURS` et campus en cases sur une ligne, salles et créneaux (`CODES`, `MOMENTS`) en liste de cases filtrable, `CHOIX` et `CODE` en liste déroulante, `HEURE` en « HH:MM », `DUREE` et `ENTIER` en nombre (bornes du schéma) ; `condition` du schéma → `data-visible-si` (durée de la pause : mode libre seulement) ; `defaut_global` rappelé dans l'aide (« Défaut de l'établissement : 12 h 45 ») ; un paramètre réservé à certaines portées (`ChampRegle.portees` : « Personnes concernées », établissement seulement) n'est affiché que pour elles et retiré du brouillon pour une autre portée, son bloc entier masqué s'il n'a que de tels paramètres |
| Application | `niveau` (nécessaire, important, facultatif), `periode` (`annee` ; `semestre` avec `semestre` ; `dates` avec `date_debut` et `date_fin`, absent pour un type non datable : `periode_datable` faux, comme les pauses ; forcé, il est refusé avec le message du domaine sous la période), `commentaire` |

L'aperçu (`POST /regles/apercu`) part à chaque changement : il passe le
brouillon à `valider` et affiche la phrase telle qu'elle sera enregistrée,
avec le niveau et la période, ou la liste de ce qui reste à corriger. Il est
porté par la section `apercu-regle` (`hx-include="closest form"`,
`from:closest form`), **pas** par le formulaire : HTMX valide un formulaire
avant de l'envoyer et, tant qu'un champ requis est vide ou hors bornes,
n'enverrait rien, sans rien dire. Un formulaire neuf commence par « La phrase
de la règle s'affiche ici dès qu'elle est complète » ; une saisie refusée
redemande l'aperçu au chargement.

À l'enregistrement (`POST /regles/nouveau`, `POST /regles/<n>/modifier`),
`ErreurSaisie.erreurs` (clés `portee.cible`, `parametres.duree`,
`periode.semestre`...) est ramenée sous les champs par `erreurs_par_champ`
(`parametres.X` → `p_X`, `portee.cible` → `cible_<nature>`) ; les erreurs
globales d'un brouillon mal formé vont sous leur bloc (`portee` et `periode`
sous leur choix, `parametres` ou un paramètre inconnu sous le titre du bloc
« Paramètres ») et le reste (`origine`, doublon d'une règle active...) dans le
bandeau : aucune n'est perdue. Statut
422, saisie conservée, jamais d'erreur 500. La modification réutilise le même
formulaire, prérempli (`valeurs_de_regle`) ; son aperçu est calculé comme si
la règle était inactive, pour qu'elle ne fasse pas doublon avec elle-même
(le contrôle complet a lieu à l'enregistrement).

**Suppression d'une règle.** Comme les autres listes : confirmation dans la
ligne (`GET /regles/<n>/ligne/suppression`, `POST /regles/<n>/ligne/supprimer`)
ou page de confirmation sans JavaScript (`GET`/`POST /regles/<n>/supprimer`),
avec le conseil de la désactiver pour la garder sans effet.

**Supprimer une cible visée par des règles.** Supprimer un groupe, un
enseignant, un campus, une salle, un type de séance, une matière, un besoin
ou un semestre liste d'abord les règles touchées, chacune avec « Modifier » et
« Supprimer » (retour vers la confirmation), et ce qu'il lui arrivera :
« sera supprimée », ou « L115 sera retiré de ses salles » pour une règle qui
cite l'élément dans ses paramètres (salles d'une règle de salles, campus
d'une règle de campus) ; une règle qui ne vaudrait plus rien sans lui (seule
salle citée, ordre « CM avant TD » sans CM) est supprimée. Le bouton devient
« Supprimer cet enseignant et ses 2 règles » : une seule transaction, une ligne de
journal par règle (« Règle R005 (2026-2027) supprimée avec enseignant
« Mme Roux » »). Le formulaire envoie les identifiants vus (`_regles`) :
si la liste a changé entre l'affichage et la confirmation, rien n'est
supprimé et la page le dit. Un groupe vise aussi les règles de ses
sous-groupes et de ses matières ; un semestre, celles de ses besoins.

`services/regles` ne donne ni les règles touchées par la suppression d'un
élément quelconque ni le retrait d'un code des paramètres d'une règle : c'est
fait côté référentiels, dans `edt.services.referentiels.regles_cibles`
(`regles_touchees`, `appliquer`, table `PARAMETRES_CITANT`), avec
`regles_visant`, `lire_par_id`, `modifier` et `supprimer` du contrat.

## 5. Services (`edt.services.referentiels`)

Fonctions typées, sans validation de transaction, aux retours
sérialisables : elles servent telles quelles à une API ou à un assistant.

```python
lister(session, code, *, recherche="", tri=None, descendant=False, page=1, taille=50,
       filtres=None) -> PageListe
lire(session, code, ident) -> dict              # valeurs canoniques + id + nom_affiche
creer(session, code, valeurs) -> dict
modifier(session, code, ident, valeurs, *, partiel=False) -> dict
analyser_suppression(session, code, ident) -> AnalyseSuppression   # refus, conséquences, règles
supprimer(session, code, ident, *, regles_confirmees=None) -> str
    # ErreurRegleMetier si encore utilisé, ou si des règles personnalisées sont touchées
    # et que regles_confirmees (leurs id) ne correspond pas exactement
options(session, code, nom_champ) -> list[tuple[str, str]]
decrire(code) -> dict                           # champs, types, choix, filtres, tris
```

`identifiants(session, code, *, recherche="", filtres=None) -> list[int]`
donne tous les éléments retenus (toutes pages).

`code` : `campus`, `type_salle`, `equipement`, `salle`, `enseignant`,
`groupe`, `etudiant`, `matiere`, `type_seance`, `besoin`, `indisponibilite`,
`annee_scolaire`, `semestre`, `jour_ferme`, `creneau`. Services propres :
`matrice_trajets`, `enregistrer_trajets`, `arbre_groupes`, `sous_arbre`,
`dupliquer_besoin`, `charges_hebdomadaires`, `creer_annee_par_defaut`,
`preremplir_jours_feries`, `apercu_calendrier`, `parametres_affichables`,
`enregistrer_parametres_saisis`, `proposer_regles` (règles proposées à une année,
renvoie leur nombre ; `creer_annee_par_defaut` le donne dans `regles_proposees`),
`regles_touchees` et `appliquer_aux_regles` (règles touchées par une suppression). Une saisie refusée lève `ErreurSaisie`
(sous-classe d'`ErreurDonneesInvalides`), dont `champs` donne le message de
chaque champ fautif.

## 6. Le panneau Assistant

Un bouton fixe « Assistant », en bas à droite de toutes les pages, ouvre un
panneau latéral qui recouvre la page sans la décaler. Il se ferme par sa
croix, par le bouton ou par Échap ; le focus passe au panneau puis revient au
bouton ; son état (ouvert ou fermé) est conservé d'une page à l'autre
(stockage local du navigateur). Sans JavaScript, le lien ouvre le panneau par
l'ancre `#assistant`.

Aujourd'hui il affiche une explication (« Bientôt : posez vos questions et
demandez des modifications en langage courant »), trois exemples de demandes
et un champ de saisie désactivé.

À brancher plus tard :

1. une route `POST /assistant` (module `edt/web/routes/assistant.py`) qui
   reçoit la question et renvoie un fragment de conversation ; dans
   `base.html`, activer le champ et donner au formulaire
   `hx-post="/assistant"` et `hx-target` vers une zone de réponses du
   panneau ;
2. côté service, exposer à l'assistant les fonctions de la section 5 comme
   outils : `decrire` pour connaître les champs, `lister` pour chercher,
   `creer` / `modifier(partiel=True)` / `supprimer` pour agir. Toute
   modification proposée est montrée et **confirmée par l'utilisateur**
   avant l'appel ; les erreurs métier (`ErreurSaisie`, `ErreurRegleMetier`)
   reviennent à l'assistant comme messages à reformuler ;
3. journaliser chaque demande et chaque action au niveau INFO, comme le
   reste de l'application.
