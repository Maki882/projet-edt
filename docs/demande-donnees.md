# Demande de données à la direction

Liste des données d'une année passée qui serviraient au projet, classées par
priorité. On n'obtiendra sans doute pas tout : les blocs **A** suffisent à faire
tourner l'application, les blocs **B** la rendent réaliste, les blocs **C** sont
un plus. Une version courte prête à envoyer est en fin de document.

## Les systèmes de l'école et ce qu'ils contiennent

- **Hyperplanning / PRONOTE Campus** (Index Education) : c'est le logiciel qui
  produit réellement les emplois du temps. Le portail public de l'ISEP
  (planning-2627.isep.fr) est un Hyperplanning. Il contient les cours placés,
  les salles et sites, les enseignants, les promotions et groupes de TD, les
  matières et types de cours, les indisponibilités, le calendrier, et les
  modifications en cours d'année (cours annulés, dépositionnés, déplacés).
  Il propose un **export texte / CSV paramétrable**, une table à la fois
  (menu Imports/Exports, « Exporter un fichier texte », choix du type de
  données et des colonnes), ainsi qu'un **export iCal** par ressource.
- **Aimaira** (gestion de scolarité) : maquette pédagogique (parcours, UE,
  modules, options, mutualisations entre programmes), inscriptions et
  cohortes (promotions, groupes, effectifs), référentiel enseignants avec
  statut contractuel, module de planification (dont l'usage réel à l'ISEP
  pour l'EDT n'est pas confirmé), contrôle de présence, stages et alternance.
  Aucun connecteur public entre Aimaira et Hyperplanning : il faudra deux
  familles d'exports à recouper par enseignant, salle et groupe. Les exports
  de listes Aimaira ne sont pas documentés publiquement ; il existe un flux
  OData pour la BI, à configurer par la DSI.
- **Fichiers internes** (direction des études, services généraux, mission
  handicap) : accessibilité des salles, calendrier officiel, règles non
  écrites, journal des imprévus, matrice de trajets entre campus.

## Format souhaité

- **Tableaux plats** (CSV ou Excel, une ligne par élément), pas de grille
  hebdomadaire mise en forme ni de PDF. L'export texte d'Hyperplanning ne
  sort qu'une table à la fois : il faut donc **un fichier par type de
  données** (cours, salles, enseignants, promotions, groupes, matières,
  indisponibilités…), avec **toutes les colonnes cochées**, identifiants
  internes compris.
- **Un extrait immédiat** de 20 à 50 lignes par fichier, avant l'envoi
  complet, pour figer les colonnes et commencer l'import sans attendre.
- **Année complète** de préférence (deux semestres), sinon un semestre.
- **La version réalisée** de l'emploi du temps (après modifications de
  l'année), plutôt que la version prévue de rentrée, si possible avec le
  statut des cours (annulé, dépositionné, déplacé).
- **Sans données nominatives d'étudiants** et **sans information de santé**
  (voir « Confidentialité »).

## A. Indispensable : de quoi faire tourner le générateur

### A1. Cours de l'année (Hyperplanning, type de données « Cours »)

Une ligne par séance : date ou jour et semaine, heure de début, heure de fin,
durée, matière, type de cours (CM, TD, TP, projet, examen…), enseignant(s),
promotion / TD / groupe(s) concerné(s), salle(s), site, effectif prévu,
statut si la colonne existe.

Usage : reconstituer les volumes à placer, référence de comparaison, jeu de
test grandeur nature.

### A2. Salles et sites (Hyperplanning, type « Salles », complété par les services généraux)

Nom ou code, site ou campus, bâtiment, capacité, description ou famille de
salle, groupe de salles interchangeables si utilisé. À compléter à la main
pour les salles réellement utilisées : type (banalisée, amphi, informatique,
laboratoire, projet, langues), équipements (vidéoprojecteur, PC, matériel de
labo), **étage, ascenseur du bâtiment, accessibilité sans escalier** (ces
champs ne semblent pas exister dans Hyperplanning).

Usage : contraintes de capacité, de type de salle, de campus et
d'accessibilité.

### A3. Enseignants (Hyperplanning type « Professeurs », ou Aimaira référentiel intervenants)

Identifiant stable ou nom-prénom, matières enseignées, site de
rattachement, statut permanent ou vacataire. **Sans coordonnées
personnelles.**

Usage : identifier chaque enseignant sans ambiguïté et distinguer les
vacataires, dont les disponibilités sont restreintes.

### A4. Promotions, groupes et effectifs (Hyperplanning types « Promotions » et « TD », ou Aimaira cohortes)

Liste des promotions (A1, A2, A3, I1, I2, apprentissage…), groupes de TD,
sous-groupes de TP, options et parcours, avec **l'effectif saisi de chaque
groupe** et l'appartenance des groupes les uns aux autres. Groupes
transverses (langues par niveau, électifs, cours mutualisés entre
promotions) avec leurs promotions d'origine.

Usage : contrainte « un groupe n'a pas deux cours en même temps », capacité
des salles. L'effectif suffit, aucune liste d'étudiants.

### A5. Matières et types de cours (Hyperplanning types « Matières » et « Types de cours »)

Code et libellé de chaque matière, promotion de rattachement, liste des
types de cours définis par l'école.

Usage : regrouper les séances par matière sans doublons de libellés, et
appliquer les règles par type (TP le samedi, type de salle).

### A6. Grille horaire et calendrier (Hyperplanning réglages, direction des études)

Créneaux de la journée (heures de début et de fin), pauses, plage déjeuner,
jours de cours, règles du samedi ; calendrier de l'année : dates des
semestres, vacances, jours fériés, semaines banalisées (examens, projets,
stages, séminaires). Hyperplanning conserve les jours fériés et les
calendriers de semaines actives ; le calendrier officiel est souvent aussi
une circulaire de rentrée.

Usage : grille de placement et horizon de génération.

### A7. Ordres de grandeur et accord écrit (simple mail)

Nombre de promotions, classes, groupes, enseignants, salles par campus,
campus, séances par semaine. Et un accord écrit sur l'usage autorisé des
données (tests, démonstration, soutenance, partage entre les quatre membres)
et la date de suppression.

Usage : dimensionner le solveur avant même de recevoir les fichiers ; cadrer
l'usage.

## B. Très utile : rendre la génération réaliste

### B1. Maquette pédagogique (Aimaira module pédagogie, ou syllabus interne)

Par promotion et par matière : volumes CM, TD, TP, nombre de séances ou
d'heures par semaine, période (semestre, semaines de début et de fin),
enseignant responsable, type de salle requis, options et mutualisations.

Usage : décrire directement la « demande » au lieu de la déduire de l'EDT
réalisé.

### B2. Indisponibilités des enseignants (Hyperplanning, onglet Indisponibilités)

Indisponibilités annuelles récurrentes (jour × créneau), indisponibilités
ponctuelles, charge maximale hebdomadaire.

Usage : contrainte de disponibilité des enseignants.

### B3. Indisponibilités et réservations de salles (Hyperplanning réservations, services généraux)

Réservations hors cours (événements, examens, travaux, maintenance,
associations) avec dates ; salles réservées à une promotion ou une matière.

Usage : disponibilité exceptionnelle des salles.

### B4. Modifications en cours d'année (Hyperplanning)

Cours annulés, dépositionnés (à reprogrammer), déplacés ou changés de salle,
rattrapés, avec la date de la modification ; absences des enseignants avec
période et **motif générique uniquement** (absence, formation, indisponibilité),
jamais de détail personnel. Si l'export figé de rentrée existe encore, la
comparaison prévu / réalisé donne le même résultat.

Usage : cas réels pour la fonctionnalité de réaménagement sur imprévu et
pour mesurer « ce qui dérange le moins ».

### B5. Journal des imprévus et pratiques de la scolarité (direction des études, entretien)

Types d'imprévus rencontrés dans l'année et solutions appliquées, règles non
écrites : durée standard des cours et exceptions (1 h 30, 3 h, TP de 4 h),
règles sur le samedi, demi-journées réservées (sport, associations,
réunions), règles de déjeuner, préférences habituelles (pas de cours à 8 h 30
pour telle promotion, regroupement des TP). Le contact de la personne qui
construit réellement les emplois du temps, pour un entretien de trente
minutes.

Usage : distinguer contraintes dures et préférences ; scénarios de test
réalistes.

### B6. Besoins d'accessibilité, anonymisés (mission handicap)

Uniquement sous la forme « le groupe X (année passée) nécessitait des salles
accessibles sans escalier », ou le nombre de groupes concernés par
promotion. **Aucun nom, aucun diagnostic.**

Usage : tester la contrainte d'accessibilité sur des cas réels.

### B7. Campus et trajets (direction des études)

Liste des sites avec adresse, bâtiments par site, temps de trajet retenu par
l'école entre sites.

Usage : contrainte de changement de campus.

## C. Bonus

- **Export iCal** d'une promotion, d'un enseignant ou d'une salle
  (Hyperplanning, Imports/Exports, ICAL), pour tester l'import de ce format.
- **Équipements requis par matière** ou par type de cours, rarement
  numérisés.
- **Habilitations enseignant × matière** (qui peut enseigner quoi), pour un
  futur remplacement automatique.
- **Contraintes d'examens** (durée, salles, surveillance) si l'école souhaite
  les voir gérées plus tard.
- **Périodes de stage et d'alternance** par groupe (Aimaira relation
  entreprise), pour les indisponibilités de groupes.
- **Taux d'occupation des salles** ou statistiques déjà produites, sinon
  calculables depuis A1.
- **Captures d'écran** ou exemple d'emploi du temps tel que diffusé aux
  étudiants, pour calquer la présentation.
- **Accès au flux OData d'Aimaira** via la DSI, si les exports manuels
  s'avèrent impossibles.

## Confidentialité

- Nous ne demandons **aucune liste d'étudiants** : seuls les effectifs par
  groupe sont nécessaires.
- Nous ne demandons **aucune information de santé nominative** : le besoin
  d'accessibilité nous intéresse uniquement au niveau du groupe, sans nom ni
  nature du handicap.
- Les noms des enseignants seront **pseudonymisés** avant toute utilisation
  hors de nos machines ; aucune donnée brute ne sera déposée sur GitHub ni
  transmise à un tiers, y compris aux assistants de programmation.
- Les fichiers seront **supprimés à la fin du projet** (après la soutenance du
  14 décembre 2026), sauf accord contraire de l'école.
- Une version déjà pseudonymisée par l'école convient parfaitement, à
  condition que les identifiants restent cohérents entre les fichiers (le
  même enseignant porte le même code partout).

## Priorité minimale si peu de temps

Si une seule chose est possible : **l'export des cours de l'année (A1)** et
**la liste des salles avec capacité et site (A2)**. Tout le reste peut être
reconstitué ou simulé.

## Version courte, prête à envoyer

> Pour tester notre générateur d'emplois du temps sur des données réelles,
> nous aurions besoin, pour une année passée, d'exports en tableaux (CSV ou
> Excel, une ligne par élément, toutes les colonnes), de préférence depuis
> Hyperplanning / PRONOTE Campus, un fichier par type de données :
>
> **Indispensable**
> 1. Les cours de l'année (une ligne par séance : jour, heures, matière, type
>    de cours, enseignant, promotion ou groupe, salle, site, statut).
> 2. La liste des salles avec site, bâtiment, capacité et, si possible, type,
>    équipements, étage et accessibilité sans escalier.
> 3. La liste des enseignants (nom ou identifiant, matières, statut permanent
>    ou vacataire), sans coordonnées personnelles.
> 4. Les promotions, groupes de TD et sous-groupes de TP, avec leurs effectifs
>    et leur emboîtement, y compris les groupes transverses (langues, options).
> 5. Les matières et les types de cours.
> 6. La grille horaire officielle (créneaux, pauses, déjeuner, samedi) et le
>    calendrier de l'année (semestres, vacances, jours fériés, semaines
>    banalisées).
> 7. Quelques ordres de grandeur (nombre de promotions, groupes, enseignants,
>    salles, campus, séances par semaine).
>
> **Très utile**
> 8. La maquette pédagogique (volumes CM, TD, TP par matière et promotion).
> 9. Les indisponibilités des enseignants et les réservations de salles.
> 10. Les modifications de l'année : cours annulés, déplacés, rattrapés,
>     absences d'enseignants avec un motif générique seulement.
> 11. Les règles internes non écrites et, si possible, un contact de la
>     scolarité pour un court entretien.
> 12. Les groupes ayant nécessité des salles accessibles, sans aucune donnée
>     nominative ni médicale.
> 13. La liste des sites et le temps de trajet retenu entre eux.
>
> **Bonus** : un export iCal, les équipements requis par matière, les
> contraintes d'examens, les périodes de stage par groupe.
>
> Un extrait de quelques lignes de chaque fichier, envoyé dès maintenant,
> nous permettrait de commencer sans attendre l'ensemble. Nous ne demandons
> aucune liste d'étudiants ni aucune information de santé ; les noms des
> enseignants seront pseudonymisés, rien ne sera publié, et les fichiers
> seront supprimés après la soutenance du 14 décembre 2026. Si seule une
> partie est possible, les points 1 et 2 suffisent pour démarrer.
