# Demande de données à la direction

Liste des données d'une année passée qui serviraient au projet, classées par
priorité. On n'obtiendra sans doute pas tout : les blocs **A** suffisent à faire
tourner l'application, les blocs **B** la rendent réaliste, les blocs **C** sont
un plus.

Systèmes de l'école : **Pronote Campus** (ex-Hyperplanning, Index Education)
pour les emplois du temps, **Aimaira** pour la scolarité, plus des fichiers
tenus en interne (immobilier, direction des études).

## Format souhaité

- **Tableaux plats** (CSV ou Excel, une ligne par élément) plutôt que grilles
  hebdomadaires mises en forme ou PDF : c'est ce qu'un export « liste » de
  Pronote Campus produit directement.
- **Un extrait immédiat** de quelques lignes par fichier, même avant l'envoi
  complet, pour figer les colonnes et la grille horaire.
- **Année complète** de préférence (deux semestres), sinon un semestre.
- **Sans données nominatives d'étudiants** et **sans information de santé** :
  voir la section « Confidentialité ».

## A. Indispensable : de quoi faire tourner le générateur

### A1. Emploi du temps réalisé (Pronote Campus)

L'export des cours de l'année, une ligne par séance, avec au minimum :
date ou jour, heure de début, heure de fin, matière, type de séance (CM, TD,
TP, projet, examen…), enseignant(s), promotion / classe / groupe(s)
concerné(s), salle(s), site ou campus, effectif prévu.

Usage : reconstituer les volumes à placer, servir de référence de comparaison
et de jeu de test grandeur nature.

### A2. Référentiel des salles (Pronote Campus + service immobilier)

Nom, site ou campus, bâtiment, étage, capacité, type (banalisée, amphi,
informatique, laboratoire, projet, langues…), équipements (vidéoprojecteur,
PC, matériel de labo), **accessibilité sans escalier** (ascenseur, plain-pied).

Usage : contraintes de capacité, de type de salle et d'accessibilité.

### A3. Référentiel des enseignants (Pronote Campus / Aimaira)

Identifiant stable ou nom-prénom, statut (permanent, vacataire), matières
enseignées, site de rattachement. **Sans coordonnées personnelles.**

Usage : identifier chaque enseignant sans ambiguïté et connaître son
rattachement.

### A4. Structure des promotions et groupes (Aimaira / Pronote Campus)

Liste des promotions (A1, A2, A3, I1, I2, apprentissage…), classes ou groupes
de TD, sous-groupes de TP, options et parcours, avec **l'effectif de chaque
groupe** et l'appartenance des groupes les uns aux autres (quel TP dans quel
TD, quel TD dans quelle promotion). Groupes transverses (langues par niveau,
électifs) avec leur promotion d'origine.

Usage : contrainte « un groupe n'a pas deux cours en même temps » et capacité
des salles. L'effectif suffit, pas besoin des listes d'étudiants.

### A5. Grille horaire officielle et calendrier (Pronote Campus / direction des études)

Créneaux de la journée (heures de début et de fin), pauses, plage déjeuner,
jours de cours, règles du samedi, calendrier de l'année : dates des
semestres, vacances, jours fériés, semaines banalisées (examens, projets,
stages, séminaires).

Usage : la grille de placement et l'horizon de génération.

### A6. Sites et campus

Liste des sites, adresses, temps de trajet retenu par l'école entre sites.

Usage : contrainte de changement de campus.

## B. Très utile : rendre la génération réaliste

### B1. Maquette pédagogique (Aimaira / direction des études)

Par promotion et par matière : volumes horaires CM, TD, TP, nombre de séances
ou d'heures par semaine, période (semestre, semaines de début et de fin),
enseignant responsable, type de salle requis.

Usage : décrire la « demande » directement, au lieu de la déduire de l'EDT
réalisé.

### B2. Indisponibilités et contraintes des enseignants (Pronote Campus)

Indisponibilités annuelles (jours ou demi-journées), indisponibilités
ponctuelles, préférences éventuelles, charge maximale hebdomadaire.

Usage : contrainte de disponibilité des enseignants.

### B3. Indisponibilités des salles (Pronote Campus / immobilier)

Réservations hors cours (événements, examens, travaux, maintenance) avec
dates.

Usage : contrainte de disponibilité exceptionnelle des salles.

### B4. Journal des modifications en cours d'année (Pronote Campus)

Cours annulés, déplacés, changés de salle, rattrapés, avec date de la
modification et motif si disponible (absence enseignant, salle indisponible,
événement…). Absences d'enseignants avec période.

Usage : cas réels pour la fonctionnalité de réaménagement sur imprévu, et
mesure de « ce qui dérange le moins ».

### B5. Besoins d'accessibilité, anonymisés (Aimaira / mission handicap)

Uniquement sous la forme : « le groupe X (année passée) nécessitait des salles
accessibles sans escalier », ou le nombre de groupes concernés par promotion.
**Aucun nom, aucun diagnostic.**

Usage : tester la contrainte d'accessibilité sur des cas réels.

### B6. Règles internes non écrites dans les logiciels

Durée standard des cours, exceptions (1 h 30, 3 h, TP de 4 h), règles sur le
samedi, jours ou demi-journées réservés (sport, associations, réunions),
règles de déjeuner, préférences habituelles de la scolarité (pas de cours à
8 h 30 pour telle promotion, regroupement des TP…).

Usage : distinguer contraintes dures et préférences.

## C. Bonus

- **Matériel mobile** (chariots d'ordinateurs, matériel de labo partagé) et
  ses règles de réservation.
- **Taux d'occupation des salles** ou statistiques déjà produites par l'école.
- **Export iCal** d'une promotion ou d'un enseignant, pour tester l'import de
  ce format.
- **Captures d'écran** ou exemple d'emploi du temps tel qu'il est diffusé aux
  étudiants, pour calquer la présentation.
- **Contraintes d'examens** (durée, salles, surveillance) si l'école souhaite
  les voir gérées plus tard.
- **Contact** d'une personne de la scolarité qui pourra répondre à deux ou
  trois questions de précision sur les fichiers.

## Confidentialité

- Nous ne demandons **aucune liste d'étudiants** : seuls les effectifs par
  groupe sont nécessaires.
- Nous ne demandons **aucune information de santé nominative** : le besoin
  d'accessibilité nous intéresse uniquement au niveau du groupe, sans nom ni
  nature du handicap.
- Les noms des enseignants seront **pseudonymisés** avant toute utilisation
  hors de nos machines ; aucune donnée brute ne sera déposée sur GitHub ni
  transmise à un tiers.
- Les fichiers seront **supprimés à la fin du projet** (après la soutenance du
  14 décembre 2026), sauf accord contraire de l'école.
- Si l'école préfère, une version déjà pseudonymisée par ses soins convient
  parfaitement, à condition que les identifiants restent cohérents entre les
  fichiers (le même enseignant porte le même code partout).

## Priorité minimale si peu de temps

Si une seule chose est possible : **l'export des cours de l'année (A1)** et
**la liste des salles avec capacité et site (A2)**. Tout le reste peut être
reconstitué ou simulé.
