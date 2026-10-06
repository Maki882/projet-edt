# Règles personnalisées

Conception du paramétrage fin des contraintes. **Proposition à valider : rien n'est encore
codé.** Elle répond à la demande d'Axel du 06/10/2026 : tout paramétrer (jours travaillés,
critères personnalisés comme « le groupe X ne travaille pas le samedi » ou « le groupe Y a une
pause déjeuner d'au moins 1 h 15 »), pour que l'assistant conversationnel branché plus tard
dispose d'un catalogue de règles structuré, validé et lisible.

Elle suppose fusionnés deux chantiers en cours, qu'elle ne redéfinit pas : les **niveaux**
par contrainte et par calcul (`Niveau`, `POIDS_NIVEAU`, `calcul.parametres["niveaux"]`,
gravité d'une anomalie selon le niveau) et la **pause déjeuner à trois modes**
(`dejeuner_mode` : `fixe`, `libre`, `aucun` ; `dejeuner_debut_min`, `dejeuner_fin_min`,
`dejeuner_duree_min`).

Une **contrainte** est une entrée du catalogue fixe (`edt.domaine.contraintes`) ; une **règle
personnalisée** (ici « règle ») est une donnée, saisie, importée ou proposée par l'assistant.

## 1. Principe

### 1.1 Anatomie d'une règle

| Élément | Contenu | Exemple |
|---|---|---|
| Type | un code du catalogue des types (§ 2), stable | `PAUSE_DEJEUNER` |
| Portée | une nature et une cible : établissement, groupe (avec ses sous-groupes), enseignant, salle, campus, type de séance, matière, besoin | la classe `A2-G3` |
| Paramètres | valeurs validées par le petit schéma du type (nom, type, unité, défaut, bornes) | pause libre d'au moins 75 min entre 11 h 45 et 14 h 15 |
| Niveau | nécessaire, important ou facultatif ; chaque type a un niveau par défaut | important |
| Actif | une règle inactive est gardée, mais ni posée ni vérifiée | oui |
| Période | année scolaire, semestre ou dates ; vide = toujours | S1 2026-2027 |
| Origine | saisie, import, assistant | assistant |
| Code | identifiant métier stable, attribué à la création | `R007` |
| Commentaire | texte libre | « demande de la responsable de promotion » |

Chaque règle a une **phrase** générée, jamais saisie : « La classe A2-G3 a une pause déjeuner
libre d'au moins 1 h 15 entre 11 h 45 et 14 h 15. » Elle sert partout : liste, page Générer,
anomalies, export, réponses de l'assistant. Le niveau s'affiche à côté, en étiquette.

### 1.2 Ce que désigne une portée

Les types sont de trois **sortes**. *Séance* : la règle s'applique à chaque séance concernée,
une par une. *Emploi du temps* : elle s'applique à l'emploi du temps de chaque **personne**
concernée, c'est-à-dire un **groupe feuille** (ses séances et celles de ses ascendants, la
définition des trous et des indicateurs) ou un enseignant non fictif. *Relation* : elle lie
des séances entre elles (même matière, même groupe).

| Portée | Séances concernées (sorte « séance ») | Personnes concernées (sorte « emploi du temps ») |
|---|---|---|
| Établissement | toutes | groupes feuilles, enseignants ou les deux (paramètre commun `personnes`, défaut : groupes) |
| Groupe | séances d'un groupe **lié** : ascendants, descendants, chevauchants, partage d'étudiant (comme `DISPO_GROUPE`) | groupes feuilles de son sous-arbre |
| Enseignant | ses séances | lui |
| Salle, campus | séances placées dans cette salle, sur ce campus | (non admis) |
| Type de séance, matière, besoin | séances de ce type, de cette matière, de ce besoin | (non admis) |

Limite héritée de la définition des trous : l'emploi du temps d'un demi-groupe ne compte pas
ses groupes transverses (langues, parcours), qui ont chacun le leur.

### 1.3 Deux familles : réglages et exigences

- **Réglage** : le type a une valeur par défaut dans les paramètres globaux. La règle
  **remplace** cette valeur pour sa portée, et la plus précise gagne le long d'une **lignée** :
  groupe feuille → classe → promotion → établissement ; enseignant → établissement ;
  besoin → matière → type de séance → établissement. Trois types : `JOURS_TRAVAILLES`,
  `PAUSE_DEJEUNER`, `PAUSE_MINIMALE`. Une règle de réglage de portée établissement est
  refusée : on modifie le paramètre.
- **Exigence** : aucune valeur globale. Les règles **s'ajoutent** : chacune doit être
  respectée, à son niveau.

Quand une séance ou une paire de séances concerne plusieurs personnes, chacune apporte sa
valeur effective et toutes s'appliquent : la plus exigeante l'emporte de fait. Chaque valeur
garde sa **source** : un paramètre global (contrainte du catalogue, au niveau choisi pour le
calcul) ou une règle (son code, son niveau). Une entorse est imputée à sa source, une fois.

**Les jours.** Jours permis d'une séance = base ∩ restrictions.

- La **base** suit la lignée de l'enseignement (besoin → matière → type de séance →
  établissement). Au niveau du type de séance, faute de règle, la case « samedi autorisé »
  ajoute le samedi ; au niveau de l'établissement, c'est le paramètre `jours_travailles`
  (§ 3). On **ouvre** donc un jour par l'enseignement : « les DST peuvent avoir lieu le samedi ».
- Les **restrictions** sont toutes les règles de portée groupe, enseignant ou campus qui
  touchent la séance. On **ferme** un jour par les personnes ou les lieux : « le groupe A1 ne
  travaille pas le samedi ». Une règle de personne n'ouvre jamais un jour ; la validation le dit.

Unicité : deux règles actives d'un même type de réglage, de même cible et de périodes qui se
recoupent sont refusées (« la règle R007 règle déjà la pause déjeuner de A2-G3 : la
modifier »). Les exigences se cumulent librement.

### 1.4 Données existantes qui disent déjà des choses voisines

| Donnée existante | Choix | Raison |
|---|---|---|
| Indisponibilité d'un enseignant, d'une salle, d'un groupe (récurrente ou datée) | **Cohabiter** | Une indisponibilité est un **fait** sur une ressource (« M. Petit est à Lyon le mardi ») : datable, nécessaire, déjà importée. Une règle est une **politique**, de portée large ou de niveau souple (« pas de CM le vendredi après-midi », « de préférence pas le lundi matin pour Mme Roux »). L'assistant crée une indisponibilité pour une absence, une règle `CRENEAUX_INTERDITS` sinon. Codes distincts (`DISPO_*` et `REGLE`). |
| Jour, heure, salle, campus imposés d'un besoin ; types de salle et équipements | **Réutiliser** | Attributs de la demande : un seul endroit pour les dire. `CAMPUS` et `SALLES` sont refusés en portée besoin (« renseigner la salle imposée du besoin »). `JOURS_TRAVAILLES` et `PLAGE_HORAIRE` y restent permis : ils disent autre chose (plusieurs jours, une fenêtre, un niveau). |
| « Samedi autorisé » d'un type de séance | **Réutiliser** comme valeur implicite du type de séance dans la base des jours | Rien à ressaisir ; écran et import des types inchangés. Une règle `JOURS_TRAVAILLES` explicite sur ce type la remplace, et l'écran du type le signale. Le solveur et le vérificateur ne lisent plus la case, seulement les jours résolus (§ 4.2). |
| Maxima d'un enseignant (`max_minutes_jour`, `max_minutes_semaine`) | **Réutiliser** | La fiche reste le lieu de ses maxima (`MAX_ENSEIGNANT`). `MAX_PAR_JOUR` en minutes est refusé en portée enseignant ; il reste permis en nombre de séances. |
| Jours fermés | **Réutiliser** | Aucun type de règle pour le calendrier daté. |
| Paramètres globaux (jours, pause, déjeuner) | **Surchargés** (§ 1.3) | Ils restent les valeurs par défaut. |

## 2. Catalogue des types, première fournée

Quatorze types. Codes stables (`TypeRegle`) : on n'en renomme jamais, on en ajoute. Valeurs :
heures `HH:MM`, durées en minutes, jours en noms français, cibles par leur code.

### 2.1 Vue d'ensemble

| Code | Libellé | Sorte | Famille | Portées admises | Paramètres : type, unité, défaut, bornes | Niveau par défaut |
|---|---|---|---|---|---|---|
| `JOURS_TRAVAILLES` | Jours travaillés | séance | réglage | groupe, enseignant, campus, type de séance, matière, besoin | `jours` : liste de jours, au moins un (un jour sans créneau est signalé, pas refusé) | nécessaire |
| `PLAGE_HORAIRE` | Horaires | séance | exigence | toutes | `debut`, `fin` : heures, `debut` < `fin` ; `jours` : liste, vide = tous | nécessaire |
| `CRENEAUX_INTERDITS` | Créneaux interdits | séance | exigence | toutes | `jours` : liste, au moins un ; `debut`, `fin` : heures, défaut ouverture et fermeture | nécessaire |
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

### 2.2 Sémantique, entorse, modèle, vérification

Une entorse se compte **par semaine active** dans la période de la règle, comme les
préférences (trous, samedi) : les poids de l'objectif restent comparables. « Aucune
variable » : la règle retire des choix (en dur) ou pose un coefficient sur les booléens de
choix existants (en souple). Toutes les règles se posent dans la phase des horaires, sauf
mention contraire.

| Code | En dur (nécessaire) | En souple, une entorse = | Modèle CP-SAT, coût sur le jeu grand | Vérification |
|---|---|---|---|---|
| `JOURS_TRAVAILLES` | chaque séance concernée a lieu un jour permis (§ 1.3) | une séance un jour non permis | domaines ; aucune variable | par séance |
| `PLAGE_HORAIRE` | la séance tient dans `[debut, fin]` les jours visés | une séance qui déborde | domaines ; aucune variable. Portée salle : choix retirés comme pour `DISPO_SALLE` ; en souple, pénalité en phase salles | par séance |
| `CRENEAUX_INTERDITS` | la séance ne recoupe aucune plage interdite | une séance qui recoupe | même traduction interne que `PLAGE_HORAIRE` (des plages interdites) | par séance |
| `CAMPUS` | la séance a lieu sur un campus permis | une séance sur un campus refusé | le campus est dans le choix ; aucune variable | par séance |
| `SALLES` | salle dans la liste (imposer) ou hors de la liste (interdire) | une séance dans une salle refusée | dur : domaines de salles ; souple : objectif de la **phase salles** seulement | par séance |
| `PAUSE_DEJEUNER` | fixe : aucune séance de la personne ne recoupe la plage ; libre : chaque jour où elle a cours dans la fenêtre, au moins `duree` libres d'un seul tenant dans `[debut, fin]` ; aucun : rien | une personne, un jour, sans pause conforme | fixe : domaines, aucune variable ; libre : modélisation du mode libre global avec les bornes de la règle, environ jours × fenêtres candidates booléens par personne | par personne et jour |
| `PAUSE_MINIMALE` | deux séances consécutives d'une personne sont séparées d'au moins `duree` | une paire trop rapprochée | les cliques de personnes allongent les plages de la plus petite pause dure en vigueur ; toute autre pause passe par des couples de choix interdits comme `CAMPUS_TRAJET` (dur) ou un booléen par couple (souple) ; coût proportionnel aux paires de séances de la personne | par paire, comme `PAUSE` |
| `MAX_PAR_JOUR` | total du jour au plus `maximum` | un jour en dépassement | une inégalité par personne, jour et ensemble de semaines, comme `MAX_ENSEIGNANT` ; souple : un booléen par inégalité | par personne et jour |
| `JOURNEES_LIBRES` | au moins `nombre` demi-journées (ou journées) travaillées sans séance, prises dans `parmi` | une demi-journée (ou journée) libre manquante | un booléen « occupée » par personne et demi-journée, une inégalité par personne ; environ 2 000 booléens pour tous les enseignants | par personne et semaine |
| `SANS_TROU` | au plus `maximum` créneaux de trou par semaine (définition de l'objectif) | un créneau de trou au-delà | booléens de trous de l'objectif, posés aussi en mode faisable pour la portée. **Cher** : les trous de tout l'établissement doublent presque le modèle, d'où l'absence de portée établissement | par personne et semaine |
| `CAMPUS_PAR_JOUR` | les séances d'un jour sont sur un seul campus | un jour sur deux campus | booléens de présence par campus de l'objectif (environ 18 par personne), une clause par paire de campus | par personne et jour |
| `MATIERES_PAR_JOUR` | au plus `maximum` matières différentes dans la journée | une matière de trop un jour | un booléen par personne, jour et matière (environ 10 000 pour tous les groupes feuilles) | par personne et jour |
| `ORDRE_SEMAINE` | pour chaque groupe feuille et chaque matière, toute séance `avant` commence avant toute séance `apres` de la même semaine | une paire dans le mauvais ordre | un entier de début par séance concernée (somme des choix), une inégalité par paire ; 600 entiers au plus | par paire |
| `REPARTITION_MATIERE` | même jour : les séances de la matière du groupe tombent le même jour ; jours différents : au plus une par jour | une séance hors du jour commun, ou une de trop le même jour | « au plus une par jour » sans variable ; « même jour » : un booléen par jour et par couple matière-groupe | par groupe, matière et semaine |

Gravité côté vérificateur : celle du niveau (`GRAVITE_DU_NIVEAU`), plafonnée à
« information » pour une séance imposée (début imposé, ponctuelle, verrou) qui s'écarte
d'une règle de jours ou d'horaires, comme aujourd'hui pour `JOURS` et `HORAIRES`.

### 2.3 Phrases que l'assistant devra produire

| Code | Exemples |
|---|---|
| `JOURS_TRAVAILLES` | « Le groupe A1 ne travaille pas le samedi. » « M. Petit ne travaille que le lundi et le mardi. » « Les DST peuvent avoir lieu du lundi au samedi. » |
| `PLAGE_HORAIRE` | « Le lundi, les A3 commencent à 10 h au plus tôt. » « Le vendredi, l'établissement ferme à 17 h. » |
| `CRENEAUX_INTERDITS` | « Pas de CM le vendredi après-midi. » « Mme Roux n'a pas cours le lundi de 8 h 30 à 10 h 30. » |
| `CAMPUS` | « Les séances de la promotion A3 ont lieu à Issy. » « Les TP n'ont jamais lieu sur le site Stan. » |
| `SALLES` | « Les TP d'électronique des A2 ont lieu en N016 ou en N018. » « M. Garcia n'enseigne pas en L101. » |
| `PAUSE_DEJEUNER` | « La classe A2-G3 a une pause déjeuner libre d'au moins 1 h 15 entre 11 h 45 et 14 h 15. » « M. Petit déjeune de 12 h à 13 h. » |
| `PAUSE_MINIMALE` | « Le demi-groupe A1-G2a a au moins 30 min entre deux séances. » |
| `MAX_PAR_JOUR` | « Les A1 n'ont pas plus de 6 h de cours par jour. » « Mme Roux ne donne pas plus de 3 séances par jour. » |
| `JOURNEES_LIBRES` | « Chaque enseignant a au moins une demi-journée libre par semaine. » « Mme Roux vient au plus deux jours par semaine. » (pour un enseignant, en journées, la phrase prend la tournure de présence) |
| `SANS_TROU` | « La classe A3-APP1 n'a aucun trou dans la semaine. » |
| `CAMPUS_PAR_JOUR` | « Les A3 ne changent pas de campus dans la journée. » |
| `MATIERES_PAR_JOUR` | « Les A1 n'ont pas plus de 3 matières différentes par jour. » |
| `ORDRE_SEMAINE` | « Dans chaque matière, le CM précède les TD de la semaine. » |
| `REPARTITION_MATIERE` | « Les séances de mathématiques des A1 ont lieu des jours différents. » |

### 2.4 Candidats écartés

| Candidat | Raison |
|---|---|
| Maximum de séances consécutives | Avec la grille actuelle (deux créneaux par demi-journée, séance qui ne déborde pas de sa demi-journée), un enchaînement dépasse rarement deux créneaux : effet nul pour un coût réel. À reprendre avec une grille à créneaux d'une heure. |
| Pas de premier ou de dernier créneau | Couvert par `PLAGE_HORAIRE` (« commencer à 10 h 45 au plus tôt ») et, en souhait global, par la préférence « créneaux extrêmes ». |
| Maximum par semaine d'un groupe | Ne dépend pas du placement (somme des besoins) : relève des pré-vérifications et de la charge affichée sur l'écran des besoins. |
| Salle ou campus imposé d'un seul besoin | Colonnes du besoin (§ 1.4). |
| Jours de présence d'un enseignant | Fusionné dans `JOURNEES_LIBRES` (en journées). |
| Préférence sur une indisponibilité (champ `poids`) | Remplacée par `CRENEAUX_INTERDITS` au niveau facultatif. |

## 3. Paramètres globaux nouveaux

**`jours_travailles`**, « Jours travaillés de l'établissement ». Nouveau type de paramètre
« ensemble de jours », enregistré comme liste JSON de numéros (0 = lundi), affiché en sept
cases. Défaut : lundi à vendredi. Au moins un jour ; un jour sans créneau dans la grille est
signalé par les pré-vérifications, pas refusé (la grille peut être saisie après). Il remplace
tout ce qui code en dur « lundi à vendredi » : `JOURS_OUVRES = range(5)` du dépliage (une
semaine est entièrement fermée quand aucun jour travaillé n'est ouvert) ; la règle `JOURS`,
où un jour de la grille hors des jours travaillés devient un **jour d'appoint**, permis
seulement par la base d'une séance (case « samedi autorisé » ou règle de l'enseignement,
§ 1.3) ; les colonnes de la vue semaine (`vues.JOURS_OUVRES`), les jours proposés pour
modifier une séance, les tests `SAMEDI` des pré-vérifications et de la reconstitution d'un
emploi du temps importé. La préférence « samedi » (`poids_samedi`) garde son sens.

**Aucun autre paramètre.** Tout autre défaut s'exprime par une règle de portée établissement
(« chaque groupe a au plus 6 h par jour ») : l'écran des paramètres reste court et
l'assistant n'a qu'un mécanisme à connaître.

## 4. Chaîne technique

### 4.1 Domaine et base

**`edt.domaine.regles`** (pur, sans ORM) : `TypeRegle`, `NaturePortee`, `OrigineRegle` ; la
définition de chaque type (libellé, sorte, famille, portées admises, schéma des paramètres,
niveau par défaut, gabarits de phrase, exemples) ; la validation (valeurs canoniques, erreur
par champ) ; la phrase ; la résolution (§ 4.2). Ajouter un type = une entrée ici, un poseur
dans le solveur, un vérificateur ; un test vérifie que les trois registres couvrent le catalogue.

**Table `regle`** :

| Colonne | Type | Rôle |
|---|---|---|
| `id` | entier | clé |
| `code` | texte (16), unique | identifiant métier stable (« R007 »), attribué si vide |
| `type` | texte (32), CHECK | `TypeRegle` |
| `portee` | texte (16), CHECK | nature de la portée |
| `groupe_id`, `enseignant_id`, `salle_id`, `campus_id`, `type_seance_id`, `matiere_id`, `besoin_id` | clés étrangères facultatives | cible ; CHECK « seule la colonne de la portée est renseignée, aucune pour l'établissement », comme `une_seule_cible` des indisponibilités |
| `parametres` | JSON | valeurs canoniques, validées par le schéma du type à chaque écriture |
| `niveau` | texte, CHECK | `Niveau` |
| `actif` | booléen | |
| `annee_scolaire_id`, `semestre_id` | clés facultatives, CASCADE | période ; une règle de groupe porte l'année du groupe |
| `date_debut`, `date_fin` | dates facultatives, CHECK ordonnées | période datée |
| `origine` | texte, CHECK | saisie, import, assistant |
| `commentaire` | texte | |
| `cree_le`, `modifie_le` | date et heure | |
| `source_fichier`, `source_ligne`, `extra` | provenance | comme les autres tables importables |

Index : un par clé de cible, un sur (`type`, `actif`). Supprimer une cible encore visée est
**refusé** (NO ACTION) et `analyser_suppression` liste les règles, comme pour un besoin ; un
besoin supprimé emporte ses règles (CASCADE : elles lui appartiennent). Pas de migration :
`init-db --reset` crée la table. Diagramme UML de `docs/modele-donnees.md` mis à jour.

**Code du catalogue `REGLE`** (« Règle personnalisée ») : le code de toutes les anomalies des
règles. Non modifiable et absent du tableau des niveaux de la page Générer : chaque règle
porte son propre niveau. Tri, filtres et libellés existants fonctionnent sans changement.

### 4.2 Instantané

- `Instantane.regles: tuple[RegleResolue, ...] = ()` : règles actives dont la période recoupe
  le semestre, **résolues** par `charger_instantane` : paramètres convertis (minutes, numéros
  de jour), semaines couvertes (une période datée se lit à la semaine sur la semaine type, au
  jour près sur les séances datées), cibles (séances ou personnes, sous-groupes et groupes liés
  compris, § 1.2), phrase.
- `Instantane.emplois` (en cache) : l'emploi du temps de chaque groupe feuille et de chaque
  enseignant. **Définition unique** qui remplace les deux copies actuelles
  (`ModeleHoraires._emplois_du_temps_des_groupes` et les ascendants des indicateurs).
- `Instantane.reglages` (en cache) : pour chaque séance, ses jours permis ; pour chaque
  personne, son déjeuner et sa pause ; chaque valeur avec sa source. Le solveur et le
  vérificateur lisent ces valeurs et ne lisent plus directement `plage_dejeuner`,
  `fenetre_dejeuner`, `pause_min` ni `Besoin.samedi_autorise`.
- Une règle enregistrée devenue invalide (paramètre hors bornes, cible hors de l'année) fait
  échouer le chargement avec une `ErreurDonneesInvalides` qui la nomme.
- Niveaux du calcul : `OptionsSolveur.niveaux_regles: dict[int, Niveau]`, et le même argument
  pour le vérificateur ; une règle absente est ignorée pour ce calcul (convention des niveaux).
  `calcul.parametres["regles"]` copie chaque règle appliquée (code, type, portée, paramètres,
  niveau retenu, phrase). Une version se vérifie avec les règles **actuelles** (des données,
  comme les besoins) et les niveaux choisis au calcul pour celles qui existaient alors.

### 4.3 Solveur

- **Préparation** : en dur, les types « séance » retirent des débuts, des campus ou des
  salles ; un domaine vidé par une règle est une impossibilité certaine, rapportée en nommant
  la règle. En souple, ils marquent une pénalité par choix (début, campus), sans variable.
- **Modèle des horaires** : une étape `_regles_personnalisees` appelle un **poseur par type**
  (`edt.solveur.personnalisees`, registre `POSEURS`). Un type sans poseur fait refuser le
  calcul avec un message clair.
- **Dur ou souple** : nécessaire = contrainte ; important ou facultatif = booléen d'entorse
  dans l'objectif, poids `POIDS_NIVEAU[niveau]` × semaines concernées. Même mécanisme et même
  calibrage que les contraintes du catalogue rendues souples par le chantier des niveaux.
- **Phase des salles** : un objectif (pénalités de `SALLES` souples) s'il en existe.
  **Bilan** : entorses comptées par règle, reprises dans le résultat.
- **Pré-vérifications** : par règle, avertissements explicites (« le groupe A1 n'a plus que
  4 jours permis pour 26 h de cours »), contradictions entre règles dures (deux `SALLES`
  imposées disjointes), séance imposée hors règle (information).
- **Couche datée** : `replacer` n'accepte un créneau que s'il respecte les règles nécessaires
  (fonctions du vérificateur appliquées à l'occurrence candidate) et préfère, à égalité,
  celui qui enfreint le moins les autres.

### 4.4 Vérificateur

- `edt.verification.regles_personnalisees` : un vérificateur par type (registre
  `VERIFICATEURS`), avec les définitions du solveur, tirées de `edt.domaine.regles` et de
  `Instantane.emplois`.
- Anomalie : code `REGLE`, nouveau champ `regle_id`, champ `niveau` du chantier des niveaux.
  Une anomalie par séance, par personne et jour, ou par paire, selon la sorte. Le message cite
  la règle puis l'écart : « Règle R007 (La classe A2-G3 a une pause déjeuner libre d'au moins
  1 h 15 entre 11 h 45 et 14 h 15) : le mardi, A2-G3a n'a que 45 min de libre dans cette
  fenêtre. »
- Pas de double signalement : `JOURS`, `DEJEUNER` et `PAUSE` ne relèvent rien pour une séance
  ou une personne dont la valeur vient d'une règle ; la règle le fait, à son niveau.
- Indicateurs : entorses par règle, pour le tableau de bord et la comparaison avec le bilan.
- Tests par type : « respectée », « enfreinte signalée », « impossible signalée »
  (`AGENTS.md`), et un test croisé solveur ↔ vérificateur : sous une règle dure, aucune
  anomalie de cette règle ; sous une règle souple, mêmes entorses des deux côtés.

### 4.5 Interface

- **Données › Règles et calendrier › Règles personnalisées** (`/regles`, module
  `web/routes/regles.py`). Liste : phrase, niveau, portée, période, origine, bascule actif ou
  inactif ; filtres par type, niveau, origine, état, groupe (sous-groupes compris),
  enseignant, matière. Ajout guidé en quatre temps : le type (libellé et une phrase
  d'exemple), la portée (nature puis recherche de la cible), les paramètres (formulaire tiré
  du schéma : cases de jours, heures, durées, listes), puis niveau, période et commentaire.
  Un **aperçu** se met à jour à chaque changement (`POST /regles/apercu`, service
  `proposer`) : phrase, séances et personnes concernées, ce que la règle remplace, doublons.
  Erreurs sous chaque champ. Modifier, dupliquer, supprimer avec confirmation.
- **Fiches** groupe, enseignant, matière : encart « Règles qui s'appliquent », héritées
  comprises, avec les valeurs de l'établissement (service `expliquer`). Utile, pas obligatoire.
- **Paramètres** : `jours_travailles` en sept cases.
- **Générer** : sous le tableau des contraintes, un bloc « Règles personnalisées » : phrase,
  niveau prérempli depuis la règle (ou le dernier calcul), choix « ne pas appliquer », champs
  `niveau_regle_<id>`. Un changement relance les pré-vérifications ; le choix vaut pour ce
  calcul seulement. Au-delà de vingt règles, regroupement par type, replié.
- **Résultat, À traiter, panneau des anomalies** : phrase de la règle et lien vers elle ;
  entorses par règle dans le résultat.

### 4.6 Import et export

Une feuille `regles`, 15ᵉ du format, après `indisponibilites`, identifiée par `code` (vide
à l'import = attribué). Colonnes : `code`, `type`, `portee`, `cible` (code comme dans les
autres feuilles ; `code@campus` pour une salle ambiguë ; un besoin par `matière / type /
groupes`, `semestre` alors obligatoire), `parametres`, `niveau` et `actif` (vides = niveau du
type, oui), `annee`, `semestre`, `date_debut`, `date_fin`, `commentaire`, et `phrase` (export
seulement, ignorée à l'import). Les paramètres tiennent dans une cellule, `clé = valeur`
séparés par `;`, listes séparées par `,`, valeurs aux formats lisibles de l'import :
`mode = libre ; debut = 11h45 ; fin = 14h15 ; duree = 1 h 15`. Validation par le service
(`valider`) : une ligne invalide est rejetée avec le message du champ. Origine `import`.
Réimport : mise à jour par code.

### 4.7 Jeu fictif

Le générateur ajoute quelques règles au jeu grand (les autres tailles en gardent une partie).
Le témoin doit rester sans erreur : il respecte les règles nécessaires de sorte « séance »
(filtres simples dans son choix glouton) ; les autres sont importantes ou facultatives.

| Règle | Type | Niveau |
|---|---|---|
| Le groupe A1 ne travaille pas le samedi. | `JOURS_TRAVAILLES` | nécessaire |
| La classe A2-G3 a une pause déjeuner libre d'au moins 1 h 15 entre 11 h 45 et 14 h 15. | `PAUSE_DEJEUNER` | important |
| Deux vacataires viennent au plus deux jours par semaine. | `JOURNEES_LIBRES` | important |
| Les TP d'électronique des A2 ont lieu en N016 ou en N018. | `SALLES` | important |
| Les A3 ne changent pas de campus dans la journée. | `CAMPUS_PAR_JOUR` | important |
| Le vendredi, l'établissement ferme à 17 h. | `PLAGE_HORAIRE` | facultatif |
| Dans chaque matière, le CM précède les TD de la semaine. | `ORDRE_SEMAINE` | facultatif |
| Les séances de mathématiques des A1 ont lieu des jours différents. | `REPARTITION_MATIERE` | facultatif |

### 4.8 API pour l'assistant : `edt.services.regles`

Fonctions typées, retours sérialisables en JSON, erreurs métier (`ErreurSaisie`, un message
par champ, clé = chemin JSON). Rien n'est écrit sans confirmation de l'utilisateur
(`docs/interface.md`, § 6) ; chaque action est journalisée au niveau INFO avec son origine.

| Fonction | Rôle | Écrit |
|---|---|---|
| `decrire_types()` | catalogue : types, sortes, portées admises, paramètres (type, unité, défaut, bornes, choix), niveau par défaut, exemples ; c'est la définition d'outil donnée au modèle de langue | non |
| `lister(session, filtres)` | règles avec leur phrase | non |
| `valider(session, brouillon)` | normalise, résout les codes, renvoie la règle canonique ou les erreurs par champ | non |
| `proposer(session, brouillon)` | `valider` + phrase + effets : séances et personnes concernées, ce qui est remplacé, doublons, contradictions, risque sur la faisabilité | non |
| `creer(session, brouillon, origine)` | crée une règle valide, renvoie son code | oui |
| `modifier(session, code, changements)` | modification partielle, revalidée en entier | oui |
| `supprimer(session, code)` | suppression | oui |
| `expliquer(session, cible)` | pour une règle, une ressource (groupe, enseignant, séance) ou une anomalie : texte et structure | non |

Brouillon produit par le modèle de langue, puis réponse de `proposer` (les valeurs omises
prennent leur défaut, renvoyé explicitement) :

```json
{"type": "PAUSE_DEJEUNER", "portee": {"nature": "groupe", "cible": "A2-G3"},
 "parametres": {"mode": "libre", "debut": "11:45", "fin": "14:15", "duree": 75},
 "niveau": "important", "actif": true,
 "periode": {"annee": "2026-2027", "semestre": "S1"}, "commentaire": "Demande de la RP"}
```

```json
{"valide": true, "regle": {"code": null, "type": "PAUSE_DEJEUNER", "...": "..."},
 "phrase": "La classe A2-G3 a une pause déjeuner libre d'au moins 1 h 15 entre 11 h 45 et 14 h 15.",
 "effets": {"personnes": 2, "seances": 31, "doublons": [], "avertissements": [],
            "remplace": ["établissement : déjeuner de 12 h 45 à 13 h 45"]},
 "erreurs": {}}
```

Une erreur revient champ par champ, pour que l'assistant reformule :
`{"valide": false, "erreurs": {"parametres.duree": "La pause dure de 15 à 180 minutes (lu : 200)."}}`.

`expliquer` renvoie un texte prêt à afficher et sa structure : « Pour A2-G3a : déjeuner libre
d'au moins 1 h 15 entre 11 h 45 et 14 h 15 (règle R007, héritée de la classe A2-G3,
importante) ; jours : lundi à vendredi et pause de 15 min (établissement). Dans la version
active, R007 est enfreinte le mardi, semaines 3 à 5. »

## 5. Plan de réalisation

Prérequis : chantiers des niveaux (solveur souple compris) et de la pause déjeuner fusionnés.
Une branche et une PR par lot, au nom de son auteur.

| Vague | Lot | Contenu | Dépend de | Estimation |
|---|---|---|---|---|
| 0 | Contrat | `edt.domaine.regles` (types, schémas, phrases, résolution), table `regle`, `jours_travailles`, `Instantane.regles`, `emplois`, `reglages`, code `REGLE`, `Anomalie.regle_id`, squelette du service (`decrire_types`, `valider`, `creer`, `lister`), tests du contrat, documents de référence | prérequis | 2,5 j |
| 1 | Solveur | domaines, poseurs des 14 types, souplesse, phase salles, pré-vérifications, replacement, mesures | contrat | 5 j |
| 1 | Vérificateur | 14 vérificateurs, messages, indicateurs, tests croisés | contrat | 3 j |
| 1 | Web données | écran `/regles` avec aperçu, paramètre des jours, encart des fiches | contrat | 3 j |
| 1 | Web emploi du temps | bloc de la page Générer, anomalies et résultat | contrat | 2 j |
| 1 | Échange | feuille `regles`, gabarit, rapport, documentation | contrat | 1,5 j |
| 1 | Synthèse | règles fictives, témoin qui respecte les règles nécessaires | contrat | 1 j |
| 2 | Intégration | `proposer` et `expliquer` complets, mesures sur le serveur, calibrage des poids, documentation | vague 1 | 2,5 j |

Total : environ 20 jours-personne, deux semaines et demie à quatre. Dans la vague 1, le
vérificateur d'abord (l'oracle des tests du solveur) ; les autres lots sont libres.
Compatible avec la fin des développements (16/11) si le contrat est fusionné avant le 20/10.

| Risque | Parade |
|---|---|
| Performance de CP-SAT sur l'établissement complet (595 séances, 229 groupes, environ 41 600 variables en mode faisable) | Les types « séance » ne coûtent rien et réduisent même les domaines. Budget mesuré sur le jeu grand avec les règles fictives : au plus 25 % de variables en plus et un délai de première solution inchangé à 10 % près ; sinon le type est restreint à des portées étroites. `SANS_TROU` et `MATIERES_PAR_JOUR` n'acceptent pas la portée établissement. |
| Souplesse et calcul en deux temps | Les entorses souples n'entrent qu'à l'amélioration : en mode « première solution valide », elles sont comptées, pas optimisées. Même réponse que le chantier des niveaux pour les contraintes souples, reprise telle quelle. |
| Cohérence solveur / vérificateur | Une seule résolution (`edt.domaine.regles`, `Instantane.emplois`, `Instantane.reglages`) lue des deux côtés ; tests croisés par type ; le vérificateur reste l'oracle. |
| Salles souples | Le campus est choisi en phase 1 sans voir la préférence de salle : la phase 2 fait au mieux. Documenté ; une règle `SALLES` nécessaire, elle, est exacte. |
| Charge de l'interface | Liste paginée et filtrée ; bloc de la page Générer replié au-delà de vingt règles ; aperçu calculé à la demande. |
| Règles incohérentes ou trop nombreuses (assistant) | Validation stricte, doublons de réglage refusés, contradictions dures signalées par `proposer` et les pré-vérifications, confirmation avant toute écriture, origine visible et filtrable. |

## 6. Décisions à réviser

À reporter dans `docs/decisions.md` (numéros de ses sections) par la PR du contrat :

- **§ 6, `JOURS`** : « lundi à vendredi » devient « jours travaillés de l'établissement
  (paramètre) » ; le samedi reste une permission du type de séance, et une règle de
  l'enseignement peut ouvrir un jour d'appoint.
- **§ 6, catalogue** : ajout du code `REGLE` ; `JOURS`, `DEJEUNER` et `PAUSE` cèdent la place
  à la règle qui les surcharge, pour sa portée.
- **§ 4** : un paramètre peut valoir un « ensemble de jours » ; le champ `poids` des
  indisponibilités n'est plus le chemin des préférences horaires (`CRENEAUX_INTERDITS`
  facultatif), il reste en base, inutilisé.
- **§ 13** : les requêtes en langage naturel restent reportées, mais leur service
  (`edt.services.regles`) est construit dès maintenant.
- Après la soutenance : remplacer la case « samedi autorisé » par une règle du type de séance.

## 7. Questions ouvertes pour Axel

1. **Une règle de groupe peut-elle ouvrir un jour** (« les apprentis A3 ont cours le
   samedi ») ou seulement en fermer ? *Recommandé : seulement fermer ; ouvrir passe par le
   type de séance, la matière ou le besoin (§ 1.3). Un groupe ne contredit jamais sa promotion.*
2. **Une règle créée par l'assistant est-elle active tout de suite ?** *Recommandé : oui,
   après la confirmation explicite du panneau Assistant ; origine visible et filtrable.*
3. **Supprimer un groupe, un enseignant ou une matière visés par une règle** : refuser, ou
   supprimer les règles avec ? *Recommandé : refuser en listant les règles, comme pour un
   besoin ; rien ne disparaît en silence.*
4. **Une règle vaut-elle une seule année scolaire ?** *Recommandé : oui pour une règle de
   groupe (les groupes changent chaque année) ; les autres peuvent valoir toujours.*
5. **Faut-il des règles sur les salles elles-mêmes** (« la salle N016 est réservée aux TP »)
   dès cette fournée ? *Recommandé : non ; les types de salle des besoins et les
   indisponibilités de salle suffisent pour la soutenance.*
