# Décisions de cadrage de la V1

Ce document consolide les réponses de l'équipe au questionnaire
(`docs/questions-avant-developpement.md`, réponses brutes dans `docs/reponses/`)
et les arbitrages pris pour ce qui est resté sans réponse. **Il fait autorité
pour tout le code de la V1.** Une décision se change ici, par PR, avant de
changer le code.

Règle générale donnée par l'équipe : **simplicité et efficacité**, tout est
**configurable**, tous les **cas réalistes** sont pris en charge, les données
sont **fictives et générées par nous** tant que l'école n'a rien fourni.

## 1. Périmètre et usage

- Application web pour un responsable de scolarité **non informaticien** :
  vocabulaire métier, aucune notion de solveur visible, messages d'erreur clairs.
- **Démo = prod** : l'application gère un établissement complet (toutes les
  promotions, plusieurs campus). Pas de mode démo, pas de placement de secours.
- Déployée sur un **VPS** (Ubuntu 26.04, 2 cœurs, 3,7 Go, Python 3.14),
  accessible par le web, **sans écran de connexion**. Mono-utilisateur dans les
  faits, mais rien dans le code ne doit supposer un seul utilisateur (pas d'état
  global en mémoire hors du suivi des calculs, tout passe par la base).
- Le professeur manipulera librement l'application : aucun écran ne doit
  planter ni afficher une erreur brute.
- Toutes les données sont **créables, modifiables et supprimables** dans
  l'interface. L'import de fichiers vient en complément.
- Classement des fonctionnalités validé (question 1.3). Obligatoire : saisie
  des référentiels, import CSV, contraintes de la spec (sections 2 et 3),
  génération, vues groupe / enseignant / salle, message d'infaisabilité,
  modification par formulaire, export CSV, jeu de données. Utile : glisser-
  déposer, historique, PDF, iCal, tableau de bord, panneau « pourquoi ça
  échoue ». Plus tard : réaménagement sur imprévu, requêtes en langage
  naturel, authentification.

## 2. Stack

| Sujet | Choix |
|---|---|
| Langage | Python, `requires-python >= 3.11` (3.11 en local, 3.14 sur le VPS) |
| Solveur | OR-Tools CP-SAT (9.15 ou plus) |
| Base | SQLite via SQLAlchemy 2 (mode WAL). Pas de migrations en V1 : la base se recrée et se repeuple par commande |
| Web | FastAPI + Jinja2 + HTMX, aucun outil Node, HTMX embarqué dans le dépôt |
| CLI | `python -m edt <commande>` (Typer) : toute fonctionnalité est aussi une commande |
| Tests | pytest, ruff pour le format et le lint, GitHub Actions sur chaque PR |
| Déploiement | service systemd (uvicorn) derrière nginx, script de déploiement |

Architecture en couches, sans exception :

```
web (routes, gabarits)  ─┐
cli (commandes)         ─┼─▶ services (fonctions typées) ─▶ domaine / solveur / verification ─▶ db
futur LLM / API hébergée ┘
```

Une fonctionnalité existe d'abord comme **fonction de service typée**
(paramètres et retours explicites, sérialisables en JSON), puis comme route
web et commande CLI. C'est ce qui permettra l'hébergement multi-utilisateurs
et le pilotage par LLM sans réécriture.

## 3. Qualité, journalisation, erreurs

- **Rien n'échoue en silence.** Interdits : `except: pass`, `except Exception`
  sans journalisation ni relance, valeur de repli silencieuse.
- Journalisation avec le module `logging`, configurée en un seul endroit :
  console + fichier tournant `logs/edt.log`, un fichier par calcul dans
  `logs/generations/`. Chaque requête web porte un identifiant repris dans les
  journaux et dans la page d'erreur affichée à l'utilisateur.
- Hiérarchie d'exceptions métier unique (`ErreurEDT` et sous-classes) ; les
  services lèvent des erreurs métier, la couche web les traduit en message
  lisible, la CLI en code de sortie non nul.
- Les données importées ne sont **jamais corrigées automatiquement** : ce qui
  ne respecte pas les règles est signalé comme anomalie.
- Équilibre production / vérification : tests ciblés (un test par règle, un
  test d'intégration par parcours), pas de recherche d'exhaustivité.
- Documentation : docstring sur chaque module et fonction publique, un fichier
  `docs/` par sous-système, diagramme de classes UML (Mermaid) tenu à jour.
- Code, commentaires, commits et documentation en **français**.

## 4. Modèle de données (entités)

Noms de tables en français, au singulier. Le détail des colonnes est dans
`docs/modele-donnees.md` (produit avec le socle).

- **Année scolaire** et **semestre** (dates de début et de fin paramétrables).
- **Campus** et **matrice de trajets** entre campus (défaut 60 minutes).
  Nomenclature des salles : préfixe `N` = Notre-Dame-des-Champs, `L` = Issy,
  plus des salles d'établissements voisins (ex. `Stan301`) rattachées à leur
  propre site.
- **Salle** : code, campus, bâtiment, étage, capacité, type de salle,
  équipements, accessibilité PMR (oui / non / inconnu). Unicité (code, campus).
- **Type de salle** et **équipement** : listes configurables.
- **Enseignant** : identifiant stable, nom, prénom, statut (permanent,
  vacataire, extérieur), maxima horaires facultatifs. Un enseignant fictif par
  matière remplace un enseignant inconnu à l'import.
- **Groupe** : un arbre (promotion > classe > demi-groupe) plus des groupes
  transverses (langues par niveau, options, parcours) et des groupes
  **tiers-temps** pour les épreuves. Chaque groupe porte son effectif et un
  drapeau « salle accessible requise ». Unicité (code, année).
- **Étudiant** (facultatif, fictif) et inscriptions aux groupes. Aucune donnée
  de santé, aucune donnée réelle nominative.
- **Conflit entre groupes** : deux groupes ne peuvent pas avoir cours en même
  temps si l'un contient l'autre, s'ils sont déclarés chevauchants, ou s'ils
  partagent un étudiant. Une seule fonction de service calcule cette relation ;
  le solveur et le vérificateur la consomment.
- **Matière** : code module (clé), libellé, promotion de rattachement.
- **Type de séance** : CM, TD, TP, projet, langue, DST, examen, autre. Liste
  configurable, avec « samedi autorisé » et type de salle par défaut.
- **Grille horaire** : créneaux configurables (jour, début, fin, demi-journée).
  Défaut : 8 h 30-10 h 30, 10 h 45-12 h 45, 13 h 45-15 h 45, 16 h 00-18 h 00 du
  lundi au vendredi ; samedi matin deux créneaux. Déjeuner 12 h 45-13 h 45 :
  la pause déjeuner peut aussi être réglée « sans horaires déterminés »
  (paramètre `dejeuner_horaires_determines`, réglable dans l'écran
  « Générer » comme dans les paramètres).
- **Indisponibilité** : pour un enseignant, une salle ou un groupe ; récurrente
  (jour + plage horaire) ou datée (plage de dates) ; dure. Le champ `poids`
  reste en base, inutilisé : une préférence horaire s'exprime par une règle
  personnalisée `CRENEAUX_INTERDITS` facultative.
- **Jour fermé** : férié, vacances, examens, semaine spéciale ; portée globale
  ou limitée à un groupe et ses descendants. Calendrier français prérempli,
  fériés mobiles compris.
- **Besoin** (la demande) : matière, type de séance, groupes (1 à n),
  enseignants (0 à n), durée en minutes, exigences de salle (types acceptés,
  équipements, salle ou campus imposé), créneau imposé facultatif, et un mode
  de volume :
  - `par_semaine` : n séances chaque semaine active du semestre ;
  - `total` : n séances à répartir régulièrement entre deux dates, avec un
    maximum par semaine ;
  - `ponctuel` : séance datée saisie à la main (examen, événement, semaine
    spéciale).
- **Version d'emploi du temps** : chaque génération crée une version (générée),
  un import d'emploi du temps existant crée une version (référence). Une seule
  version est « active » par semestre. Historique conservé.
- **Séance hebdomadaire** : élément de la semaine type d'une version (besoin,
  jour, heure de début et de fin, salle, verrou, semaines actives).
- **Occurrence** : séance datée, issue de l'instanciation de la semaine type
  sur le calendrier, d'un replacement, d'une saisie manuelle ou d'un import ;
  statut (planifiée, annulée, déplacée, à programmer).
- **Calcul** : suivi d'une génération (état, durée, paramètres, contraintes
  désactivées, message, fichier journal).
- **Paramètre** : réglages globaux (temps limite, poids de l'objectif, pause
  déjeuner à horaires déterminés ou non et sa plage, etc.). Un paramètre peut
  valoir un **ensemble de jours** : `jours_travailles`, les jours travaillés
  de l'établissement (lundi à vendredi par défaut), standard des jours.
- **Règle personnalisée** (`docs/regles-personnalisees.md`) : toute règle
  appartient à une **année scolaire** (code unique dans l'année, « R007 ») ;
  type du catalogue, portée (établissement, groupe, enseignant, campus, type
  de séance, matière, besoin), paramètres canoniques, niveau, état
  (**proposée** sans effet, active, inactive), période (semestre ou dates),
  origine (saisie, import, assistant, base, reprise). La création d'une année
  propose les règles de base et la reprise des règles actives de l'année
  précédente ; rien n'est imposé.
- Provenance des données importées (fichier, ligne, colonnes non reconnues).

## 5. Calendrier

- On génère **une semaine type par semestre**, puis on l'**instancie** sur le
  calendrier réel du semestre. La démonstration couvre une année complète.
- Une séance hebdomadaire porte ses **semaines actives**. Deux séances dont les
  semaines actives sont disjointes peuvent occuper le même créneau et la même
  ressource : le modèle doit être exact sur ce point (indispensable pour les
  matières courtes du mode `total`).
- Mode `total` : la répartition sur les semaines est calculée avant la
  résolution par une règle déterministe et documentée (régulière sur la
  fenêtre, dans la limite du maximum par semaine).
- Une occurrence qui tombe un **jour fermé** est **replacée automatiquement**
  pour préserver le volume. Si aucun placement ne respecte les contraintes, elle
  passe au statut « à programmer » et apparaît dans une liste.
- Les semaines spéciales reçoivent un planning saisi à la main (besoins
  ponctuels) ; les semaines entièrement fermées ne sont pas instanciées.
- Apprentis : seule leur semaine type « école » est gérée en V1. Pas
  d'alternance semaine A / semaine B en V1.
- Partiels : seulement de la 3ᵉ à la 5ᵉ année ; modélisés par des fermetures
  propres aux promotions concernées, des indisponibilités de salles et des
  besoins ponctuels (groupe tiers-temps dans une salle séparée).
- DST : 1ʳᵉ année le jeudi de 13 h 45 à 18 h (durée fixée par l'enseignant),
  2ᵉ année le lundi de 8 h à 12 h (créneau fixe). Un créneau imposé peut sortir
  de la grille ; il est alors signalé comme information, pas comme erreur.
- Heures en heure locale Europe/Paris, stockées sans fuseau.

## 6. Catalogue des contraintes

Les codes sont stables et **communs au vérificateur et au solveur**. Depuis le
6 octobre 2026, chaque règle porte un **niveau** choisi à chaque calcul
(`edt.domaine.contraintes.Niveau`) : **nécessaire** (jamais enfreinte, une
entorse est une erreur), **important** (le moteur ne l'enfreint que faute de
choix, chaque entorse pèse 1 000 dans l'objectif et est signalée comme
avertissement), **facultatif** (souhait, une entorse pèse 20 et n'est qu'une
information). Sept règles définissent le problème et restent toujours
nécessaires : `SALLE_UNIQUE`, `ENSEIGNANT_UNIQUE`, `GROUPE_UNIQUE`, `VERROU`,
`JOUR_FERME`, `VOLUME` et `REGLE` (chaque règle personnalisée porte son propre
niveau). Par défaut, `PAUSE`, `DEJEUNER`, `EQUIPEMENT` et
`MAX_ENSEIGNANT` sont importantes, les autres nécessaires. Les niveaux sont
enregistrés avec le calcul et appliqués ensuite à la vérification de la version
produite. L'interface ne permet plus d'ignorer une règle ; les calculs anciens
ou lancés en ligne de commande avec `-d` gardent leurs règles ignorées (clé
absente), tracées dans le résultat. Aucune contrainte n'est jamais abandonnée
automatiquement.

| Code | Règle |
|---|---|
| `SALLE_UNIQUE` | Une salle accueille une seule séance à la fois |
| `ENSEIGNANT_UNIQUE` | Un enseignant donne une seule séance à la fois |
| `GROUPE_UNIQUE` | Deux groupes en conflit n'ont pas cours en même temps |
| `CAMPUS_TRAJET` | Pour un groupe ou un enseignant, deux séances du même jour sur deux campus différents sont séparées d'au moins le temps de trajet (le déjeuner d'une heure suffit) |
| `ACCESSIBILITE` | Une séance dont un groupe requiert l'accessibilité a lieu dans une salle accessible (« inconnu » vaut « non », réglable) |
| `HORAIRES` | Séance dans la plage d'ouverture et sur un début de créneau de la grille, sauf créneau imposé |
| `JOURS` | Jours travaillés de l'établissement (paramètre `jours_travailles`, lundi à vendredi par défaut) : un **standard** dont une règle personnalisée `JOURS_TRAVAILLES` peut s'écarter dans les deux sens, pour sa portée. La case « samedi autorisé » d'un type de séance reste une exception implicite, qu'une règle explicite sur ce type remplace |
| `PAUSE` | 15 minutes entre deux séances consécutives d'une même ressource |
| `DEJEUNER` | La plage de déjeuner reste libre pour chaque groupe et chaque enseignant. Si la pause déjeuner est réglée « sans horaires déterminés », aucune plage n'est réservée : la règle est inapplicable (le vérificateur ne relève rien, le solveur ne la pose pas) ; c'est un réglage choisi par l'utilisateur, pas un abandon automatique |
| `CAPACITE` | Capacité de la salle supérieure ou égale à la somme des effectifs |
| `TYPE_SALLE` | La salle est d'un type accepté par le besoin |
| `EQUIPEMENT` | La salle possède les équipements requis |
| `DISPO_ENSEIGNANT` / `DISPO_SALLE` / `DISPO_GROUPE` | Respect des indisponibilités |
| `MAX_ENSEIGNANT` | Maxima horaires de l'enseignant, s'ils sont renseignés |
| `VOLUME` | Toutes les séances demandées sont placées |
| `VERROU` | Une séance verrouillée reste à sa place |
| `JOUR_FERME` | Aucune occurrence un jour fermé pour les groupes concernés |
| `REGLE` | Règle personnalisée (`docs/regles-personnalisees.md`) : chaque entorse porte ce code et le niveau de la règle. `JOURS`, `DEJEUNER` et `PAUSE` cèdent la place à la règle qui les surcharge, pour sa portée |

Préférences (objectif à minimiser, activable, activé pour la démonstration) :
10 × trous des groupes + 5 × changements de campus + 2 × samedi +
1 × créneaux extrêmes. Poids réglables.

## 7. Vérificateur (prioritaire, avant le solveur)

- Fonctions Python pures, **indépendantes du solveur**, qui listent les
  violations d'un emploi du temps (semaine type ou occurrences datées) :
  liste vide = emploi du temps valide.
- Il sert d'**oracle à tous les tests du solveur**, valide les modifications
  manuelles, audite un emploi du temps importé et alimente le panneau des
  anomalies.
- Il reprend et généralise le premier vérificateur de chevauchements écrit par
  l'équipe (`verifier_conflits.py`).
- Indicateurs calculés au même endroit : pourcentage de séances placées, nombre
  de conflits, taux d'utilisation des salles, changements de campus, trous.

## 8. Solveur

- Formulation : variables entières de début et de salle par séance, domaine de
  salles **pré-filtré** (capacité, type, équipement, accessibilité,
  disponibilité), non-chevauchement par ressource, bris de symétrie entre
  séances identiques. Mesurer dès le jeu moyen ; passer à deux phases
  (créneaux puis salles) seulement si le temps limite est dépassé.
  **Mise à jour du 05/10/2026** : le temps limite était dépassé sur
  l'établissement complet ; le solveur résout donc en deux phases, horaires
  puis salles (`docs/solveur.md`).
- **Mise à jour du 05/10/2026** (mesures sur le serveur, `docs/solveur.md`) :
  l'objectif de compacité est **actif par défaut**. Le calcul trouve d'abord
  un emploi du temps valide, puis l'améliore dans le temps restant ; un arrêt
  ou un temps dépassé conserve la meilleure solution trouvée. Le mode
  « première solution valide » reste réglable dans les paramètres.
- Temps limite de 5 minutes réglable (maximum 15) ; relance longue de
  15 minutes (maximum 30) ; au plus 8 fils de calcul, jamais plus que de
  cœurs. Deux messages distincts : « impossible (prouvé) » et « pas trouvé
  dans le temps imparti ».
- **Échec global** si aucune solution : jamais d'emploi du temps partiel en V1.
- **Pré-vérifications obligatoires** avant de lancer, par famille (charge par
  groupe, par enseignant, par type de salle, salles accessibles, conflits entre
  verrous), affichées comme avertissements explicites.
- Verrous = contraintes dures. Violations héritées entre séances figées :
  calcul refusé avec la liste, option pour les tolérer.
- Recalcul complet avec la solution précédente donnée comme indice. Bouton
  « regénérer » avec une nouvelle graine.
- Mode déterministe (graine fixe, 1 worker) pour les tests ; multi-workers
  sinon (2 cœurs sur le VPS).
- Calcul en arrière-plan (thread), un seul à la fois, état interrogé chaque
  seconde, temps écoulé, bouton Arrêter.
- Performance visée : 200 séances en moins de 30 s ; test de charge marqué
  `perf` avec un seuil de 120 s sur le jeu « grand ».
- Le même moteur de **replacement daté** sert au replacement des jours fermés
  et servira au réaménagement sur imprévu (perturbation minimale).

## 9. Données fictives

- Générateur paramétrable à graine fixe, réaliste pour une école d'ingénieurs
  de type ISEP : cycle intégré et cycle ingénieur, apprentis, classes,
  demi-groupes, groupes transverses, un groupe PMR, groupes tiers-temps,
  enseignants permanents et vacataires avec indisponibilités, deux campus plus
  un site voisin, salles typées et équipées, matières avec codes modules,
  besoins dans les deux modes de volume, DST, partiels, calendrier de l'année.
- Trois tailles : **petit** (tests unitaires), **moyen** (une promotion),
  **grand** (établissement complet).
- Noms fictifs lisibles (« Mme Martin ») et mention « données fictives » dans
  l'interface.

## 10. Import et export

- Gabarit fixe téléchargeable : un CSV par entité ou un classeur XLSX à
  onglets. Détection de l'encodage, du séparateur et du BOM.
- Import **partiel avec rapport** ligne par ligne (les lignes valides entrent,
  les autres sont listées) ; réimport = mise à jour par identifiant métier ;
  plusieurs fichiers acceptés avec dédoublonnage.
- Trois modes pour un emploi du temps existant : référence, verrous, séances à
  replacer. Lecteur iCal (occurrences et règles de récurrence).
- Séances annulées non comptées dans les besoins ; événements spéciaux importés
  comme type « autre » figés.
- Aucun logiciel source n'est supposé : un format = un adaptateur isolé.
- Exports : CSV (obligatoire), iCal, impression PDF par le navigateur.
- Disponible dans l'interface et en ligne de commande, par les mêmes fonctions.

## 11. Interface

- Écrans d'édition pour toutes les entités (liste triable, recherche,
  formulaire, suppression avec confirmation).
- Écran de configuration et d'ingestion des données (paramètres, grille,
  calendrier, import, chargement d'un jeu fictif, remise à zéro).
- Écran « besoins » : tableau éditable, c'est l'entrée du générateur.
- Lancement d'un calcul : pré-vérifications, contraintes à cocher, suivi en
  direct, résultat ou explication de l'échec.
- Emploi du temps : vue **semaine** et vue **jour**, par groupe (tous types),
  par enseignant et par salle ; semaine type et semaines datées avec
  navigation ; modification par formulaire dans un panneau latéral (créneau,
  salle parmi les salles compatibles), vérifiée par le vérificateur.
- Panneau des anomalies et des séances « à programmer ».
- Suppression d'un élément visé par des règles personnalisées : la
  confirmation liste ces règles et propose de les supprimer avec lui (au lieu
  d'un simple refus).

## 12. Confidentialité

- Aucune donnée personnelle réelle dans le dépôt. Les fichiers réels éventuels
  restent hors du dépôt et hors des dossiers lus par les assistants ; seuls des
  fichiers pseudonymisés ou fictifs y entrent.
- Aucune donnée de santé : l'accessibilité est un drapeau porté par un groupe.

## 13. Reporté

Réaménagement sur imprévu (l'architecture et le moteur de replacement le
préparent), requêtes en langage naturel (leur service, `edt.services.regles`,
est construit dès maintenant), authentification et rôles,
glisser-déposer, alternance A / B, calendrier d'alternance des apprentis,
diagnostic fin d'infaisabilité, sélection d'un périmètre de génération,
migrations de schéma.
