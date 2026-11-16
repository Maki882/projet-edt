# Règles personnalisées

Ce document décrit les règles personnalisées : ce qu'est une règle, les
quatorze types du catalogue, leur portée, leur niveau, leur état et leur
période, leur sémantique, et la chaîne technique qui les porte du domaine à
l'interface. Il s'adresse aux développeurs. Les sections sont numérotées
parce que des docstrings du code y renvoient (§ 2.2, § 4.4, § 4.7).

Une **contrainte** est une entrée du catalogue fixe (`edt.domaine.contraintes`,
par exemple `PAUSE` ou `JOURS`) ; une **règle personnalisée** (ici « règle »)
est une donnée de l'établissement, saisie, importée, proposée par
l'application ou créée par l'assistant : « le groupe A1 ne travaille pas le
samedi », « la classe A2-G3 a une pause déjeuner d'au moins 1 h 15 ».

Prérequis : les niveaux des contraintes (`Niveau`, `POIDS_NIVEAU`,
[solveur.md](solveur.md), « Niveaux des contraintes ») et la pause déjeuner
à trois modes (`dejeuner_mode` : `fixe`, `libre`, `aucun`).

## 1. Principe

### 1.1 Anatomie d'une règle

| Élément | Contenu | Exemple |
|---|---|---|
| Type | un code du catalogue des types (§ 2), stable | `PAUSE_DEJEUNER` |
| Portée | une nature et une cible : établissement, groupe (avec ses sous-groupes), enseignant, campus, type de séance, matière, besoin. Pas de portée salle (§ 2.4) | la classe `A2-G3` |
| Paramètres | valeurs validées par le schéma du type (nom, type, unité, défaut, bornes) | pause libre d'au moins 75 min entre 11 h 45 et 14 h 15 |
| Niveau | nécessaire, important ou facultatif ; chaque type a un niveau par défaut | important |
| État | proposée, active ou inactive ; seule une règle active est posée et vérifiée | active |
| Période | une **année scolaire**, obligatoire ; dans l'année, tout un semestre ou des dates (§ 1.6) | 2026-2027, S1 |
| Origine | saisie, import, assistant, base (règle de base), reprise (de l'année précédente) | saisie |
| Code | identifiant métier stable dans l'année, attribué à la création | `R007` |
| Commentaire | texte libre | « demande de la responsable de promotion » |

Chaque règle a une **phrase** générée, jamais saisie : « La classe A2-G3 a une
pause déjeuner libre d'au moins 1 h 15 entre 11 h 45 et 14 h 15. » Elle sert
partout : liste, page Générer, anomalies, export, réponses de l'assistant. Le
niveau s'affiche à côté, en étiquette.

### 1.2 Ce que désigne une portée

Les types sont de trois **sortes**. *Séance* : la règle s'applique à chaque
séance concernée, une par une. *Emploi du temps* : elle s'applique à l'emploi
du temps de chaque **personne** concernée, c'est-à-dire un **groupe feuille**
(ses séances et celles de ses ascendants, la définition des trous et des
indicateurs) ou un enseignant non fictif. *Relation* : elle lie des séances
entre elles (même matière, même groupe).

| Portée | Séances concernées (sorte « séance ») | Personnes concernées (sorte « emploi du temps ») |
|---|---|---|
| Établissement | toutes | groupes feuilles, enseignants ou les deux (paramètre commun `personnes`, défaut : groupes) |
| Groupe | exigences : séances d'un groupe **lié** (ascendants, descendants, chevauchants, partage d'étudiant), comme `DISPO_GROUPE` ; jours travaillés : lignée de chaque groupe feuille (§ 1.3) | groupes feuilles de son sous-arbre |
| Enseignant | ses séances | lui |
| Campus | séances placées sur ce campus | (non admis) |
| Type de séance, matière, besoin | séances de ce type, de cette matière, de ce besoin | (non admis) |

Le paramètre `personnes` n'a de sens que pour la portée établissement : pour
une autre portée il est ignoré et absent des valeurs canoniques, donc de
l'export.

Une règle sur un **besoin** concerne le semestre du besoin. Sans période,
elle prend ce semestre ; un autre semestre explicite est refusé
(`periode.semestre`) ; des dates restent possibles. Au chargement de l'autre
semestre, elle est hors périmètre (ni effet ni erreur).

### 1.3 Deux familles : réglages et exigences

- **Réglage** : le type a une valeur standard dans les paramètres globaux.
  Une règle est une **exception** : elle peut s'écarter du standard dans les
  deux sens, et la plus précise gagne le long de la **lignée** de chaque axe.
  Trois types : `JOURS_TRAVAILLES`, `PAUSE_DEJEUNER`, `PAUSE_MINIMALE`. Une
  règle de réglage de portée établissement est refusée : le paramètre global
  se modifie à la place.
- **Exigence** : aucune valeur globale. Les règles **s'ajoutent** : chacune
  doit être respectée, à son niveau.

Chaque valeur retenue garde sa **source** : un paramètre global (contrainte
du catalogue, au niveau choisi pour le calcul ; `Source.contrainte` vaut
`JOURS`, `DEJEUNER` ou `PAUSE`) ou une règle (son code, son niveau ;
`Source.regle`). Une entorse est imputée à sa source, une fois. Deux règles
actives d'un même type de réglage, de même cible et de périodes qui se
recoupent sont refusées à la validation (erreur sur `portee` : « la règle
R007 règle déjà la pause déjeuner de A2-G3 : la modifier »). Les exigences
se cumulent librement.

**Déjeuner et pause.** Chaque personne résout sa lignée (groupe feuille →
classe → promotion ; enseignant) ; sans règle, elle reçoit la valeur de
l'établissement. Toutes les personnes d'une séance ou d'une paire de séances
sont servies : ce sont des besoins de chaque personne.

**Jours travaillés : précédence.** Le **standard** d'une séance est le
paramètre `jours_travailles` (§ 3), plus le samedi si son type de séance
l'autorise (case « samedi autorisé ») et qu'aucune règle ne vise ce type :
une règle explicite l'emporte sur cette permission implicite. Chaque **axe**
de la séance résout sa lignée ; la règle la plus précise l'emporte, qu'elle
ouvre ou ferme un jour.

| Axe | Lignée, du plus précis au moins précis |
|---|---|
| chaque groupe feuille concerné | demi-groupe → classe → promotion |
| chaque enseignant non fictif | enseignant |
| l'enseignement | besoin → matière → type de séance |
| le lieu | campus du choix examiné (le moteur choisit le campus avec le début) |

**Jours permis = intersection des valeurs explicites des axes ; sans aucune
règle, le standard.** Un axe sans règle est neutre : il ne s'oppose pas à
l'exception d'un autre axe. Sinon, ouvrir le samedi à un groupe exigerait
aussi une règle sur chacun de ses enseignants. Exemples : A3-APP1 ouvre le
samedi et son
enseignant n'a pas de règle, samedi permis ; si l'enseignant le ferme,
samedi fermé (la séance satisfait les deux) ; la case du type DST ouvre le
samedi, mais pas pour A1 si la promotion le ferme ; une règle « du lundi au
vendredi » sur le type DST l'emporte sur la case. L'axe du lieu est résolu
par `ReglesJours.jours_permis(campus_id)` ; sans campus (`None`), seuls
comptent les axes des personnes et de l'enseignement.

Niveaux souples : une séance placée le jour *j* enfreint chaque règle
retenue qui ne contient pas *j*, à son niveau, et le standard (contrainte
`JOURS`, niveau du calcul) si *j* est hors du standard et qu'aucune règle
retenue ne contient *j*. Tout étant nécessaire, cela revient exactement à
l'intersection.

Grille : ouvrir un jour ne crée pas de créneau. Une séance sans heure
imposée commence sur un créneau de la grille (`HORAIRES`) ; un jour ouvert
sans créneau reste donc inutilisable. La règle est gardée, la grille pouvant
être complétée ensuite.

### 1.4 Données voisines déjà présentes

| Donnée | Choix | Raison |
|---|---|---|
| Indisponibilité d'un enseignant, d'une salle, d'un groupe (récurrente ou datée) | **Cohabiter** | Une indisponibilité est un **fait** sur une ressource (« M. Petit est à Lyon le mardi ») : datable, nécessaire, importable. Une règle est une **politique**, de portée large ou de niveau souple (« pas de CM le vendredi après-midi », « de préférence pas le lundi matin pour Mme Roux »). Codes distincts (`DISPO_*` et `REGLE`). |
| Jour, heure, salle, campus imposés d'un besoin ; types de salle et équipements | **Réutiliser** | Attributs de la demande : un seul endroit pour les dire. `CAMPUS` et `SALLES` sont refusés en portée besoin (« renseigner la salle imposée du besoin »). `JOURS_TRAVAILLES` et `PLAGE_HORAIRE` y restent permis : ils disent autre chose (plusieurs jours, une fenêtre, un niveau). |
| Fiche d'une salle (type, capacité, équipements, accessibilité, indisponibilités) | **Réutiliser** | Le paramétrage des salles reste sur la page des données de l'établissement. Les règles `CAMPUS` et `SALLES` ont des effets sur les salles sans viser une salle comme portée. |
| « Samedi autorisé » d'un type de séance | **Réutiliser** dans le standard des jours | Rien à ressaisir. Une règle `JOURS_TRAVAILLES` sur ce type l'emporte. Le solveur et le vérificateur ne lisent pas la case, seulement les jours résolus (§ 4.2). |
| Maxima d'un enseignant (`max_minutes_jour`, `max_minutes_semaine`) | **Réutiliser** | La fiche reste le lieu de ses maxima (`MAX_ENSEIGNANT`). `MAX_PAR_JOUR` en minutes est refusé pour un enseignant (portée enseignant, ou portée établissement avec « les enseignants » ou « tous ») ; il reste permis en nombre de séances. |
| Jours fermés | **Réutiliser** | Aucun type de règle pour le calendrier daté. |
| Paramètres globaux (jours, pause, déjeuner) | **Standard** (§ 1.3) | Les règles en sont les exceptions. |

### 1.5 Année scolaire, états et règles proposées

- Chaque paramétrage vaut **une année scolaire** : toute règle y est
  rattachée, comme les groupes. Le code est unique dans l'année (`R007` peut
  exister en 2025-2026 et en 2026-2027).
- Une règle **proposée** n'a aucun effet (ni posée, ni vérifiée) : elle
  apparaît « à activer », s'active une à une ou en bloc, se modifie ou
  se supprime. Une règle **inactive** est gardée sans effet ; elle se
  réactive d'un clic.
- `proposer_pour_annee(session, annee)` crée en une fois, à l'état
  proposée :
  - les **règles de base** du catalogue (`REGLES_DE_BASE`, dans
    `edt.domaine.regles`), jamais nécessaires, pour qu'aucune ne rende un
    calcul impossible par surprise : « Dans chaque matière, le CM précède
    les TD de la semaine. » et « Chaque enseignant a au moins une
    demi-journée libre par semaine. » (facultatives) ;
  - la **reprise** des règles actives de l'année précédente : cible
    retrouvée par son code (groupe de même code dans la nouvelle année ;
    enseignant, matière, type de séance, campus inchangés), semestre de même
    code, dates décalées d'un an. Une règle dont la cible n'existe pas dans
    la nouvelle année n'est pas proposée ; elle est listée « à revoir ».
  Sans doublon **par contenu** : une règle de base confirmée l'année
  précédente, ou revenue d'un import, n'est pas reproposée ; une règle déjà
  reprise non plus (`reprise_de_id`).
- La création d'une année par l'écran du calendrier appelle
  `proposer_pour_annee` et annonce le nombre de règles proposées. `init-db`
  ne le fait pas (le jeu minimal pose les deux règles de base, proposées).
  L'écran des règles offre aussi « Proposer les règles de base ».

### 1.6 Période

- **Toute l'année** (ni semestre ni dates), **un semestre** de l'année, ou
  **des dates** dans l'année.
- Sur la semaine type, une règle vaut pour toute séance dont les semaines
  actives croisent sa période (`RegleResolue.semaines`) ; sur une séance
  datée, au jour près (`RegleResolue.couvre(date)`). Une règle dont la
  période ne croise aucune semaine du semestre est absente de l'instantané.
- **Réglages de personne non datables** (`PAUSE_DEJEUNER`,
  `PAUSE_MINIMALE` ; `DefinitionType.periode_datable` faux) : une personne a
  une seule valeur par semestre. La période vaut l'année ou un semestre
  entier ; des dates sont refusées à la validation (erreur `periode`), donc à
  la saisie, à l'API et à l'import. Deux réglages actifs de même type et même
  cible sont refusés dès qu'ils touchent un même semestre (« toute l'année »
  touche les deux).
- **`JOURS_TRAVAILLES` datées** : deux règles de même cible aux périodes
  **disjointes se succèdent** : chaque séance suit celle qui couvre ses
  semaines (la plus couvrante si plusieurs, puis le plus petit code ;
  journalisé). Deux règles qui se recoupent font échouer
  `Instantane.reglages` en les nommant. Sur une séance datée,
  `Reglages.jours_de(seance_id, date)` ne retient que les règles de jours qui
  couvrent la date, le standard reprenant sa place sinon. Le retrait de la
  permission « samedi autorisé » par une règle sur le type de séance suit la
  même période.

## 2. Catalogue des types

Quatorze types. Codes stables (`TypeRegle`) : un code ne se renomme jamais,
un type s'ajoute. Valeurs : heures `HH:MM`, durées en minutes, jours en noms
français, cibles par leur code.

### 2.1 Vue d'ensemble

« Courantes » = établissement, groupe, enseignant, campus, type de séance,
matière, besoin.

| Code | Libellé | Sorte | Famille | Portées admises | Paramètres : type, unité, défaut, bornes | Niveau par défaut |
|---|---|---|---|---|---|---|
| `JOURS_TRAVAILLES` | Jours travaillés | séance | réglage | groupe, enseignant, campus, type de séance, matière, besoin | `jours` : liste de jours, au moins un ; peut ouvrir ou fermer (§ 1.3) | nécessaire |
| `PLAGE_HORAIRE` | Horaires | séance | exigence | courantes | `debut`, `fin` : heures, `debut` < `fin` ; `jours` : liste, vide = tous | nécessaire |
| `CRENEAUX_INTERDITS` | Créneaux interdits | séance | exigence | courantes | `jours` : liste, au moins un ; `debut`, `fin` : heures, défaut ouverture et fermeture | nécessaire |
| `CAMPUS` | Campus imposés ou interdits | séance | exigence | établissement, groupe, enseignant, type de séance, matière | `mode` : imposer ou interdire ; `campus` : liste de codes, au moins un | nécessaire |
| `SALLES` | Salles imposées ou interdites | séance | exigence | groupe, enseignant, type de séance, matière | `mode` : imposer ou interdire ; `salles` : liste, au moins une | nécessaire |
| `PAUSE_DEJEUNER` | Pause déjeuner | emploi du temps | réglage | groupe, enseignant | `mode` : fixe, libre, aucun ; `debut`, `fin` : heures ; `duree` : 15 à 180 min (mode libre) ; défauts = paramètres globaux | important |
| `PAUSE_MINIMALE` | Pause entre deux séances | emploi du temps | réglage | groupe, enseignant | `duree` : 0 à 120 min, défaut `pause_min` | important |
| `MAX_PAR_JOUR` | Maximum par jour | emploi du temps | exigence | établissement, groupe, enseignant (en séances seulement) | `unite` : minutes ou séances (défaut minutes) ; `maximum` : 30 à 720 min, ou 1 à 8 séances | important |
| `JOURNEES_LIBRES` | Demi-journées ou journées libres | emploi du temps | exigence | établissement, groupe, enseignant | `unite` : demi-journée ou journée ; `nombre` : 1 à 6, défaut 1 ; `parmi` : demi-journées ou jours, vide = tous les jours travaillés | important |
| `SANS_TROU` | Trous limités | emploi du temps | exigence | groupe, enseignant | `maximum` : créneaux de trou par semaine, 0 à 10, défaut 0 | facultatif |
| `CAMPUS_PAR_JOUR` | Un seul campus par jour | emploi du temps | exigence | établissement, groupe, enseignant | aucun | important |
| `MATIERES_PAR_JOUR` | Matières par jour | emploi du temps | exigence | groupe | `maximum` : 1 à 8 | facultatif |
| `ORDRE_SEMAINE` | Ordre dans la semaine | relation | exigence | établissement, groupe, matière | `avant`, `apres` : types de séance, défaut CM puis TD | facultatif |
| `REPARTITION_MATIERE` | Regroupement ou étalement d'une matière | relation | exigence | établissement, groupe, matière | `mode` : même jour ou jours différents | facultatif |

Règles de validation propres à certains types :

- les choix se lisent sans casse ni accent (« Fixe », « LIBRE ») et les
  champs conditionnés suivent la valeur canonique ; dans `parmi`, un jour
  entier absorbe ses demi-journées ;
- `JOURNEES_LIBRES` : un nombre supérieur aux moments « parmi », ou qui ne
  laisserait aucun moment pour avoir cours (journées ≥ jours travaillés,
  demi-journées ≥ leur double), est refusé ;
- les valeurs par défaut de type « code » (`ORDRE_SEMAINE` : CM, TD) passent
  par le contrôle des codes connus ;
- une **cible de besoin** se donne par identifiant ou par « matière / type /
  groupes » (sans casse) ; si le même enseignement existe dans plusieurs
  semestres, `periode.semestre` départage, sinon l'erreur dit « ambigu ». Une
  matière d'une autre année est refusée, même par identifiant ;
- un brouillon mal formé (`portee`, `periode` ou `parametres` qui ne sont pas
  des objets, origine inconnue) revient en erreurs par champ, jamais en
  exception.

### 2.2 Sémantique, entorse, modèle, vérification

Une entorse se compte **par semaine active** de la période, comme les
préférences : les poids restent comparables. « Aucune variable » : la règle
retire des choix (dur) ou pose un coefficient sur les booléens de choix
existants (souple). Phase des horaires sauf mention. La traduction détaillée
dans le modèle est dans [solveur.md](solveur.md), « Règles personnalisées ».

| Code | En dur (nécessaire) | En souple, une entorse = | Modèle CP-SAT | Vérification |
|---|---|---|---|---|
| `JOURS_TRAVAILLES` | chaque séance a lieu un jour permis par la précédence (§ 1.3) | une séance un jour qu'une règle retenue (ou le standard) exclut | domaines, par choix (début, campus) ; aucune variable | par séance |
| `PLAGE_HORAIRE` | la séance tient dans `[debut, fin]` les jours visés | une séance qui déborde | domaines ; aucune variable | par séance |
| `CRENEAUX_INTERDITS` | la séance ne recoupe aucune plage interdite | une séance qui recoupe | même traduction interne que `PLAGE_HORAIRE` (des plages interdites) | par séance |
| `CAMPUS` | la séance a lieu sur un campus permis | une séance sur un campus refusé | le campus est dans le choix ; aucune variable | par séance |
| `SALLES` | salle dans la liste (imposer) ou hors de la liste (interdire) | une séance dans une salle refusée | dur : domaines de salles ; souple : objectif de la **phase des salles** seulement | par séance |
| `PAUSE_DEJEUNER` | fixe : aucune séance de la personne ne recoupe la plage ; libre : chaque jour où elle a cours dans la fenêtre, au moins `duree` libres d'un seul tenant dans `[debut, fin]` ; aucun : rien | une personne, un jour, sans pause conforme | fixe : domaines, aucune variable ; libre : modélisation du mode libre global avec les bornes de la règle | par personne et jour |
| `PAUSE_MINIMALE` | deux séances consécutives d'une personne sont séparées d'au moins `duree` | une paire trop rapprochée | les cliques de personnes allongent les plages de la plus petite pause dure en vigueur ; toute autre pause passe par des couples de choix interdits comme `CAMPUS_TRAJET` (dur) ou un booléen par couple (souple) | par paire, comme `PAUSE` |
| `MAX_PAR_JOUR` | total du jour au plus `maximum` | un jour en dépassement | une inégalité par personne, jour et ensemble de semaines, comme `MAX_ENSEIGNANT` ; souple : un booléen par inégalité | par personne et jour |
| `JOURNEES_LIBRES` | au moins `nombre` demi-journées (ou journées) travaillées sans séance, prises dans `parmi` | une demi-journée (ou journée) libre manquante | un booléen « occupée » par personne et demi-journée, une inégalité par personne | par personne et semaine |
| `SANS_TROU` | au plus `maximum` créneaux de trou par semaine (définition de l'objectif) | un créneau de trou au-delà | booléens de trous de l'objectif, posés aussi en mode faisable pour la portée. **Cher** : les trous de tout l'établissement doublent presque le modèle, d'où l'absence de portée établissement | par personne et semaine |
| `CAMPUS_PAR_JOUR` | les séances d'un jour sont sur un seul campus | un jour sur deux campus | booléens de présence par campus et par jour, une clause par paire de campus | par personne et jour |
| `MATIERES_PAR_JOUR` | au plus `maximum` matières différentes dans la journée | une matière de trop un jour | un booléen par personne, jour et matière | par personne et jour |
| `ORDRE_SEMAINE` | pour chaque groupe feuille et chaque matière, toute séance `avant` commence avant toute séance `apres` de la même semaine | une paire dans le mauvais ordre | une inégalité par paire sur les débuts codés | par paire |
| `REPARTITION_MATIERE` | même jour : les séances de la matière du groupe tombent le même jour ; jours différents : au plus une par jour | une séance hors du jour commun, ou une de trop le même jour | « au plus une par jour » sans variable ; « même jour » : un booléen par jour et par couple matière-groupe | par groupe, matière et semaine |

Gravité côté vérificateur : celle du niveau (`GRAVITE_DU_NIVEAU`), plafonnée
à « information » pour une séance imposée (début imposé, ponctuelle, verrou)
qui s'écarte d'une règle de jours ou d'horaires, comme pour `JOURS` et
`HORAIRES`.

### 2.3 Phrases

La phrase se calcule à partir du type, de la portée et des paramètres. Pour
`JOURS_TRAVAILLES`, elle se calcule par rapport au standard (« ne travaille
pas le vendredi », « a aussi cours le samedi » ; « ne travaille que du lundi
au vendredi » quand la règle égale le standard) ; `MATIERES_PAR_JOUR`
s'accorde au singulier.

| Code | Exemples |
|---|---|
| `JOURS_TRAVAILLES` | « Le groupe A1 ne travaille pas le samedi. » « La classe A3-APP1 a aussi cours le samedi. » « M. Petit ne travaille que le lundi et le mardi. » |
| `PLAGE_HORAIRE` | « Le lundi, les A3 commencent à 10 h au plus tôt. » « Le vendredi, l'établissement ferme à 17 h. » |
| `CRENEAUX_INTERDITS` | « Les CM n'ont pas lieu le vendredi de 13 h 45 à 18 h 00. » |
| `CAMPUS` | « Les séances de la promotion A3 ont lieu sur le campus ISSY. » |
| `SALLES` | « Les séances de la matière Électronique embarquée ont lieu en L003 ou en L004 ou en L104. » |
| `PAUSE_DEJEUNER` | « La classe A2-G3 a une pause déjeuner libre d'au moins 1 h 15 entre 11 h 45 et 14 h 15. » |
| `PAUSE_MINIMALE` | « La classe A3-APP2 a au moins 30 min entre deux séances. » |
| `MAX_PAR_JOUR` | « La classe A3-APP2 n'a pas plus de 6 h de cours par jour. » |
| `JOURNEES_LIBRES` | « Chaque enseignant a au moins une demi-journée libre par semaine. » « Mme Pons vient au plus deux jours par semaine. » (pour un enseignant, en journées, la phrase prend la tournure de présence) |
| `SANS_TROU` | « La classe A3-APP1 n'a aucun trou dans la semaine. » |
| `CAMPUS_PAR_JOUR` | « La promotion A3 ne change pas de campus dans la journée. » |
| `MATIERES_PAR_JOUR` | « Les A1 n'ont pas plus de 3 matières différentes par jour. » |
| `ORDRE_SEMAINE` | « Dans chaque matière, le CM précède les TD de la semaine. » |
| `REPARTITION_MATIERE` | « Les séances de Traitement du signal ont lieu des jours différents (pour chaque groupe). » |

### 2.4 Types écartés

| Candidat | Raison |
|---|---|
| Portée salle (« la salle N016 est réservée aux TP ») | Le paramétrage des salles se fait sur la page des données de l'établissement ; `CAMPUS` et `SALLES` agissent sur les salles depuis une autre portée. |
| Maximum de séances consécutives | Avec la grille par défaut (deux créneaux par demi-journée, séance qui ne déborde pas de sa demi-journée), un enchaînement dépasse rarement deux créneaux : effet nul pour un coût réel. |
| Pas de premier ou de dernier créneau | Couvert par `PLAGE_HORAIRE` (« commencer à 10 h 45 au plus tôt ») et, en souhait global, par la préférence « créneaux extrêmes ». |
| Maximum par semaine d'un groupe | Ne dépend pas du placement (somme des besoins) : relève des pré-vérifications et de la charge affichée sur l'écran des besoins. |
| Salle ou campus imposé d'un seul besoin | Colonnes du besoin (§ 1.4). |
| Jours de présence d'un enseignant | Compris dans `JOURNEES_LIBRES` (en journées). |
| Préférence sur une indisponibilité (champ `poids`) | `CRENEAUX_INTERDITS` au niveau facultatif dit la même chose. |

## 3. Paramètre global des jours travaillés

**`jours_travailles`**, « Jours travaillés de l'établissement » : le
**standard** des jours. Paramètre de type « ensemble de jours », enregistré
comme liste JSON de numéros (0 = lundi), affiché en sept cases sur l'écran
« Paramètres ». Défaut : lundi à vendredi. Au moins un jour ; un jour sans
créneau dans la grille n'est pas refusé. Un jour de la grille hors du
standard (le samedi par défaut) est un **jour d'appoint** : seule une
exception l'ouvre, case « samedi autorisé » d'un type de séance ou règle de
n'importe quel axe (§ 1.3). La préférence « samedi » (`poids_samedi`) garde
son sens.

Aucun autre paramètre global n'accompagne les règles : tout autre défaut
s'exprime par une règle de portée établissement (« chaque groupe a au plus
6 h par jour »), éventuellement une règle de base (§ 1.5). L'écran des
paramètres reste court et l'assistant n'a qu'un mécanisme à connaître.

## 4. Chaîne technique

### 4.1 Domaine et base

**`edt.domaine.regles`** (pur, sans ORM) : `TypeRegle`, `NaturePortee`,
`EtatRegle`, `OrigineRegle` ; la définition de chaque type
(`CATALOGUE_TYPES` : libellé, sorte, famille, portées admises, schéma des
paramètres, niveau par défaut, période datable, gabarits de phrase,
exemples) ; `REGLES_DE_BASE` ; la validation (valeurs canoniques, erreur par
champ, `ErreurSaisie`) ; la phrase ; la résolution, précédence des jours
comprise (§ 4.2) ; les registres d'implémentation (`enregistrer(registre,
type)`, `types_non_couverts(registre)`). Ajouter un type = une entrée dans
le catalogue, un poseur dans le solveur, un vérificateur ; un test vérifie
que les registres couvrent le catalogue.

**Table `regle`** (colonnes détaillées dans
[modele-donnees.md](modele-donnees.md)) : année scolaire obligatoire
(suppression en cascade), code unique dans l'année, type, portée et une
colonne de cible par nature (`groupe_id`, `enseignant_id`, `campus_id`,
`type_seance_id`, `matiere_id`, `besoin_id`, en NO ACTION ; un CHECK impose
que seule la colonne de la portée soit renseignée, aucune pour
l'établissement), paramètres en JSON canonique, niveau, état, période
(`semestre_id`, ou `date_debut` et `date_fin` ordonnées), origine,
`reprise_de_id` (SET NULL), commentaire, horodatages et provenance d'import.
Index : un par clé de cible, un sur (`annee_scolaire_id`, `etat`, `type`).

**Suppression d'une cible.** La base refuse toujours (NO ACTION) tant
qu'une règle vise la cible ; c'est le service qui orchestre
(`edt.services.referentiels.regles_cibles`). `analyser_suppression` de la
cible liste les règles qui la visent, et celles dont les paramètres la
citent (`PARAMETRES_CITANT` : salle dans `SALLES`, campus dans `CAMPUS`,
type de séance dans `ORDRE_SEMAINE`). La suppression confirmée, dans une
seule transaction, supprime les règles qui visent la cible, retire la salle,
le campus ou le type des listes qui le citent (une règle dont la liste
devient vide est supprimée), puis la cible ; le message et le journal nomment
chaque règle touchée. La suppression d'un groupe touche aussi les règles de
ses sous-groupes et des matières de son sous-arbre ; celle d'un semestre, les
règles qui visent ses besoins.

**Code du catalogue `REGLE`** (« Règle personnalisée ») : le code de toutes
les anomalies des règles. Non modifiable et absent du tableau des niveaux de
la page Générer : chaque règle porte son propre niveau.

### 4.2 Instantané

- `Instantane.regles` : règles **actives** de l'année du semestre dont la
  période le recoupe, **résolues** par `charger_instantane` : paramètres
  convertis (minutes, numéros de jour), semaines couvertes, cibles (séances ou
  personnes, sous-groupes et groupes liés compris, § 1.2), phrase.
- `Instantane.emplois` (en cache) : l'emploi du temps de chaque groupe
  feuille et de chaque enseignant non fictif, définition unique des trous,
  des indicateurs et des règles de sorte « emploi du temps ».
- `Instantane.reglages` (en cache) : pour chaque séance, ses jours permis par
  campus, avec le standard et les règles retenues par axe (§ 1.3) ; pour
  chaque personne, son déjeuner et sa pause ; chaque valeur avec sa source.
  Le solveur et le vérificateur lisent ces valeurs, jamais directement
  `plage_dejeuner`, `fenetre_dejeuner`, `pause_min` ni
  `Besoin.samedi_autorise`.
- Une règle active devenue invalide (paramètre hors bornes, cible hors de
  l'année) fait échouer le chargement avec une `ErreurDonneesInvalides` qui
  la nomme.
- Niveaux du calcul : `OptionsSolveur.niveaux_regles: dict[int, Niveau]`, et
  le même argument pour le vérificateur ; une règle absente est ignorée pour
  ce calcul. `calcul.parametres["regles"]` copie chaque règle du semestre
  (code, type, portée, paramètres, niveau retenu, phrase). Une version se
  vérifie avec les règles **actuelles** (des données, comme les besoins) et
  les niveaux choisis au calcul pour celles qui existaient alors
  ([verification.md](verification.md), « Règles personnalisées »).

### 4.3 Solveur

- **Préparation** : en dur, les types « séance » retirent des débuts, des
  campus ou des salles ; un domaine vidé par une règle est une impossibilité
  certaine, rapportée en nommant la règle. En souple, ils marquent une
  pénalité par choix (début, campus), sans variable.
- **Modèle des horaires** : l'étape `_regles_personnalisees` appelle un
  **poseur par type** (registre `"solveur"`, `edt.solveur.personnalisees` et
  `edt.solveur.regles_emploi`). Une règle active d'un type sans poseur fait
  refuser le calcul en la nommant.
- **Dur ou souple** : nécessaire = contrainte ; important ou facultatif =
  booléen d'entorse dans l'objectif, poids `POIDS_NIVEAU[niveau]`. Même
  mécanisme et même calibrage que les contraintes du catalogue.
- **Phase des salles** : un objectif (pénalités de `SALLES` souples) s'il en
  existe. **Bilan** : entorses comptées par règle
  (`statistiques["entorses_regles"]`), reprises dans le résultat.
- **Couche datée** : `replacer` n'accepte que des places conformes aux règles
  nécessaires (vérificateurs datés pour les types d'emploi du temps et de
  relation) et ajoute au coût d'une place les entorses aux règles souples.

### 4.4 Vérificateur

- `edt.verification.regles_personnalisees` et
  `edt.verification.regles_emploi` : un vérificateur par type (registre
  `"verificateur"`), avec les définitions du solveur, tirées de
  `edt.domaine.regles` et de `Instantane.emplois`.
- Anomalie : code `REGLE`, champs `regle_id`, `regle_code`, `regle_phrase`
  et `niveau`. Une anomalie par séance, par personne et jour, ou par paire,
  selon la sorte. Le message cite la règle puis l'écart : « Règle R007 (La
  classe A2-G3 a une pause déjeuner libre d'au moins 1 h 15 entre 11 h 45 et
  14 h 15) : le mardi, A2-G3a n'a que 45 min de libre dans cette fenêtre. »
- Pas de double signalement : `JOURS`, `DEJEUNER` et `PAUSE` ne relèvent rien
  pour une séance ou une personne dont la valeur vient d'une règle ; la
  règle le fait, à son niveau.
- Indicateur : `entorses_par_regle`, nombre d'anomalies de chaque règle.
- Tests par type : « respectée », « enfreinte signalée », et des tests
  croisés solveur ↔ vérificateur : sous une règle dure, aucune anomalie de
  cette règle ; sous une règle souple, mêmes entorses des deux côtés. Pour
  les jours, un test par exemple de la précédence (§ 1.3).

### 4.5 Interface

- **Données › Règles et calendrier › Règles personnalisées** (`/regles`,
  module `web/routes/donnees_regles_personnalisees.py`), filtrée par année
  (l'année en cours par défaut). Liste : phrase, niveau, portée, période,
  origine, état (bascule active ou inactive) ; filtres, recherche et
  pagination. Un bandeau signale les règles proposées à activer, avec
  « Activer les N règles » (celles qui feraient doublon restent proposées).
  Bouton « Proposer les règles de base » (§ 1.5).
- **Ajout guidé** : le type, la portée (nature puis cible), les paramètres
  (formulaire tiré du schéma : cases de jours, heures, durées, listes), puis
  niveau, période et commentaire. Un **aperçu** de la phrase se met à jour à
  chaque changement (`POST /regles/apercu`). Les erreurs s'affichent sous
  chaque champ.
- **Suppression d'une règle** : confirmation dans la ligne, page de
  confirmation sans JavaScript.
- **Suppression d'une cible** (groupe, enseignant, campus, type de séance,
  matière, besoin, ou salle citée par une règle) : la confirmation garde les
  blocages habituels (besoins qui l'utilisent, avec leur lien) et ajoute la
  liste des règles concernées, chacune avec sa phrase. S'il n'y a pas d'autre
  blocage, le bouton supprime la cible et ses règles (§ 4.1).
- **Paramètres** : `jours_travailles` en sept cases. **Calendrier** : la
  création d'une année annonce les règles proposées.
- **Générer** : sous le tableau des règles du catalogue, un bloc « Règles
  personnalisées » (règles actives du semestre) : phrase, type, portée,
  période, niveau prérempli depuis la règle, choix « Ignorer pour ce calcul » (champs
  `niveau_regle_<id>` et `ignorer_regle_<id>`). Le choix vaut pour ce calcul
  seulement. Au-delà de vingt règles, regroupement par type, replié.
- **Résultat, À traiter, panneau des anomalies** : phrase de la règle ;
  entorses par règle dans le résultat.

### 4.6 Import et export

Une feuille `regles`, quatorzième feuille du format d'échange, identifiée
par (`annee`, `code`) ; `code` vide à l'import = attribué. Les colonnes sont
décrites dans [import-export.md](import-export.md) (section générée
« Feuilles ») ; les paramètres tiennent dans une cellule, `clé = valeur`
séparés par `;`, listes séparées par `,`, valeurs aux formats lisibles de
l'import : `mode = libre ; debut = 11:45 ; fin = 14:15 ; duree = 1 h 15`.
Validation par le service (`valider`) : une ligne invalide est rejetée avec
le message du champ. Origine `import`. Réimport : mise à jour par (année,
code). L'export d'une année donne ses règles, tous états confondus ; les
colonnes `origine` et `phrase` sont exportées et ignorées à l'import.
Exporter une année puis l'importer dans la suivante est l'autre voie de
reprise.

### 4.7 Jeux fictifs

Le générateur (`edt.synthese.regles`) ajoute des règles actives aux jeux
fictifs, choisies selon les groupes, matières et sites présents (le jeu grand
les a toutes, les autres une partie), plus les deux règles de base, laissées
proposées. Le témoin doit rester sans erreur : il respecte toutes les règles
actives de sorte « séance » et de réglage, et `MAX_PAR_JOUR` ; les autres
règles actives sont respectées par construction (« la promotion A3 ne change
pas de campus dans la journée » : la règle `CAMPUS` place toute la promotion
à Issy ; « vient au plus deux jours par semaine » : posée sur des vacataires
dont les indisponibilités laissent au plus deux jours).

Règles du jeu `moyen`, graine 0 (14 règles : 11 actives, 1 inactive, 2
proposées) :

| Code | Règle | Type | Niveau | État |
|---|---|---|---|---|
| R001 | La classe A3-APP1 a aussi cours le samedi. | `JOURS_TRAVAILLES` | important | active |
| R002 | La classe A3-G2 a une pause déjeuner libre d'au moins 1 h 15 entre 11 h 45 et 14 h 15. | `PAUSE_DEJEUNER` | important | active |
| R003 | La classe A3-APP2 a au moins 30 min entre deux séances. | `PAUSE_MINIMALE` | important | active |
| R004 | Les CM n'ont pas lieu le vendredi de 13 h 45 à 18 h 00. | `CRENEAUX_INTERDITS` | important | active |
| R005 | Les séances de la promotion A3 ont lieu sur le campus ISSY. | `CAMPUS` | nécessaire | active |
| R006 | Les séances de la matière Électronique embarquée ont lieu en L003 ou en L004 ou en L104. | `SALLES` | important | active |
| R007 | La promotion A3 ne change pas de campus dans la journée. | `CAMPUS_PAR_JOUR` | important | active |
| R008 | La classe A3-APP2 n'a pas plus de 6 h de cours par jour. | `MAX_PAR_JOUR` | important | active |
| R009 | Les séances de Traitement du signal ont lieu des jours différents (pour chaque groupe). | `REPARTITION_MATIERE` | facultatif | inactive |
| R010 | M. Clerc commence à 9 h 00 au plus tôt. | `PLAGE_HORAIRE` | nécessaire | active |
| R011, R012 | Mme Pons (puis Mme Renaud) vient au plus deux jours par semaine. | `JOURNEES_LIBRES` | important | active |
| R013 | Dans chaque matière, le CM précède les TD de la semaine. | `ORDRE_SEMAINE` | facultatif | proposée |
| R014 | Chaque enseignant a au moins une demi-journée libre par semaine. | `JOURNEES_LIBRES` | facultatif | proposée |

Le jeu `grand` y ajoute notamment « La promotion A1 ne travaille que du lundi
au vendredi. » (`JOURS_TRAVAILLES`, nécessaire) et une règle `CAMPUS`
nécessaire sur une matière d'informatique (16 règles). Les personnes nommées
sont fictives.

### 4.8 Service : `edt.services.regles`

Fonctions typées, retours sérialisables en JSON, erreurs métier
(`ErreurSaisie`, un message par champ, clé = chemin JSON). Chaque écriture
est journalisée au niveau INFO avec son origine. C'est aussi l'interface
utilisée par l'assistant conversationnel : une règle qu'il crée l'est après
confirmation explicite de l'utilisateur, avec l'origine `assistant`.

| Fonction | Rôle | Écrit |
|---|---|---|
| `decrire_types()` | catalogue : types, sortes, portées admises, paramètres (type, unité, défaut, bornes, choix), niveau par défaut, exemples, règle de précédence des jours ; c'est la définition d'outil donnée au modèle de langue | non |
| `lister(session, annee, ...)` | règles de l'année avec leur phrase et leur état, filtres par état, type, niveau, origine, cible | non |
| `lire(session, annee, code)`, `lire_par_id(session, regle_id)` | une règle et sa phrase | non |
| `regles_visant(session, nature, cible_id)` | règles dont la portée vise une cible | non |
| `valider(session, brouillon)` | normalise, résout les codes, renvoie la règle canonique et sa phrase, ou les erreurs par champ ; refuse les doublons de réglage (erreur `portee`) | non |
| `creer(session, brouillon, origine, etat)` | crée une règle valide (active par défaut), renvoie son détail, code attribué si absent | oui |
| `modifier(session, annee, code, changements)` | modification partielle, revalidée en entier ; l'année et le code ne se modifient pas | oui |
| `changer_etat(session, annee, code, etat)`, `activer(...)`, `desactiver(...)` | change l'état ; une activation revalide la règle en entier (une règle devenue invalide reste à son état) | oui |
| `supprimer(session, annee, code)` | suppression | oui |
| `proposer_pour_annee(session, annee)` | crée, à l'état proposée, les règles de base et la reprise de l'année précédente ; liste celles « à revoir » (§ 1.5) | oui |
| `expliquer(session, annee, code)` | phrase, portée, période, niveau, et effets de la règle sur chaque semestre concerné (personnes, séances, ce qui est remplacé) | non |

Brouillon (année en cours si `annee` est omise) :

```json
{"type": "PAUSE_DEJEUNER", "portee": {"nature": "groupe", "cible": "A2-G3"},
 "parametres": {"mode": "libre", "debut": "11:45", "fin": "14:15", "duree": 75},
 "niveau": "important", "annee": "2026-2027", "periode": {"semestre": "S1"},
 "commentaire": "Demande de la responsable de promotion"}
```

Une erreur revient champ par champ, pour que l'appelant reformule :
`{"parametres.duree": "La pause dure de 15 à 180 minutes (lu : 200)."}`.

## 5. Limites connues

- **Groupes transverses.** Un groupe transverse (langue, parcours) a son
  propre emploi du temps et sa propre lignée, souvent réduite à lui-même :
  une règle d'emploi du temps posée sur une promotion ne l'atteint pas. Il
  faut le viser aussi.
- **Réglages de personne non datables.** Une pause déjeuner ou une pause
  minimale vaut l'année ou un semestre entier (§ 1.6).
- **`expliquer`** ne couvre qu'une règle, ni une ressource ni une anomalie ;
  il ne calcule ni contradictions entre règles ni risque sur la faisabilité.
  Sur une règle de besoin, il ne regarde que le semestre du besoin.
- **Jours ouverts sans créneau.** Une règle qui ouvre un jour sans créneau
  dans la grille est acceptée, et ni l'aperçu ni les pré-vérifications ne le
  signalent : le jour reste inutilisable tant que la grille n'est pas
  complétée.
- **Pré-vérifications.** Les bornes de charge ne retirent pas le temps fermé
  par une règle d'horaires ou de jours : une impossibilité qui tient à
  plusieurs séances est prouvée par CP-SAT, sans nommer la règle
  ([solveur.md](solveur.md), « Limites connues »).
