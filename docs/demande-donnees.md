# Demande de données à la direction

Liste des données d'une année passée qui serviraient au projet, classées par
priorité. On n'obtiendra sans doute pas tout : les blocs **A** suffisent à faire
tourner l'application, les blocs **B** la rendent réaliste, les blocs **C** sont
un plus. La version courte envoyée au directeur est en fin de document.

Les chemins de menus ci-dessous ont été vérifiés dans la documentation
officielle d'Index Education (doc.index-education.com et
docs.index-education.com) le 28/09/2026 ; ce qui n'a pas pu l'être est marqué
« non documenté ».

## Les systèmes de l'école et ce qu'ils contiennent

- **HYPERPLANNING** (Index Education). Le portail public de l'ISEP
  (planning-2627.isep.fr) affiche « HYPERPLANNING.net 2025.8.10.1 » : l'école
  est en version 2025. L'éditeur a annoncé « En 2026, HYPERPLANNING devient
  PRONOTE Campus », mais la scolarité connaît le produit sous le nom
  Hyperplanning, et les menus ci-dessous sont ceux de cette version.
  Il contient : cours placés, salles et sites, enseignants, promotions,
  partitions de TD, options et regroupements, matières, types de cours,
  cursus et modules (volumes prévus), calendriers par promotion,
  indisponibilités et vœux, séances annulées (avec motif) ou dépositionnées,
  remplacements, réservations de salles, paramètres des sites (délais de
  trajet), paramètres des contraintes du placement automatique.
  Exports : **texte / CSV paramétrable** une table à la fois
  (Imports/Exports > TXT/CSV/XML/SQL > Exporter un fichier texte, choix du type
  de données et des colonnes), **iCal par ressource** (Imports/Exports > ICAL),
  export SQL automatique (MySQL/Oracle, droits serveur). Produire un export
  demande un profil d'utilisation avec le droit Import/Export.
- **Aimaira** (gestion de scolarité) : modules Candidature, Sélection,
  Inscription, Finance, Relation entreprise, Pédagogie (maquettes, UE, modules,
  options, ECTS, mutualisations entre programmes, cohortes et groupes),
  Académie (référentiel enseignant unique, contrats), Planification (usage
  réel à l'ISEP pour l'EDT non confirmé : l'EDT public est sous Hyperplanning),
  Contrôle de présence. Une API publique GraphQL existe (activation par
  l'éditeur sur demande de l'école) et un flux OData pour la BI. L'export de
  listes en Excel ou CSV n'est pas documenté publiquement. Aucun connecteur
  avec Hyperplanning : les deux familles de fichiers devront être recoupées
  par enseignant, salle et groupe.
- **Fichiers internes** (direction des études, services généraux, mission
  handicap) : accessibilité des bâtiments, calendrier officiel, règles non
  écrites, imprévus de l'année, conventions de saisie.

## Format souhaité

- **Tableaux plats** (CSV ou Excel, une ligne par élément), pas de grille
  hebdomadaire mise en forme ni de PDF. Le menu « Exporter un fichier texte »
  d'Hyperplanning ne sort qu'une table à la fois : **un fichier par type de
  données** (Cours, Salles, Enseignants, Promotions, Matières, Calendriers,
  Cursus et modules…), toutes les colonnes cochées, titres de colonnes,
  encodage UTF-8, séparateur point-virgule.
- **Une ligne par séance datée** (date et heures), pas un cours avec sa liste
  de semaines. Si l'export Cours ne donne que les semaines allumées, joindre
  l'export iCal des promotions.
- **Une séance commune** à plusieurs promotions ou groupes sur une seule
  ligne, avec tous ses publics.
- **Un extrait immédiat** de 20 à 50 lignes par fichier, avant l'envoi
  complet, pour figer les colonnes et commencer l'import sans attendre.
- **Année complète** de préférence (deux semestres), sinon un semestre, dans
  l'état réalisé de fin d'année ; les cours annulés inclus si l'export le
  propose.
- **Sans données nominatives d'étudiants** et **sans information de santé**
  (voir « Confidentialité »).

## A. Indispensable : de quoi faire tourner le générateur

### A0. Chiffres clés (simple mail)

Nombre de promotions, de partitions de TD et de groupes, d'enseignants, de
salles par site, de sites, de séances par semaine. Réponse par mail
suffisante, avant même les fichiers.

### A1. Cours de l'année (Hyperplanning, type de données « Cours »)

Une ligne par séance : date ou semaine et jour, heure de début, heure de fin,
durée, matière, type de cours, enseignant(s), public (promotion, TD, option,
regroupement), salle(s), site, effectif du cours, statut si la colonne
existe (annulée avec motif, dépositionnée, remplacée). À défaut de colonne
statut, joindre le « Récapitulatif des cours annulés » de l'onglet Plannings.

### A2. Salles et sites (Hyperplanning type « Salles », complété par les services généraux)

Colonnes Hyperplanning : libellé, code, libellé long, identifiant UID, site,
famille, groupe de salles, capacité, descriptif, accès et propriétaire,
accessible à la réservation, et les colonnes **Accès PMR**, **Handiboucle**,
**Chaises ergonomiques** si elles sont renseignées (présentes dans la fiche
salle récente ; à vérifier sur l'extrait en version 2025). En complément, par
le service immobilier : bâtiment, étage, ascenseur, accès de plain-pied,
type (banalisée, amphi, informatique, laboratoire, projet, langues) et
équipements si la « famille » ne les code pas.

### A3. Enseignants (Hyperplanning type « Enseignants » ; type d'intervenant par Aimaira ou la direction)

Colonnes Hyperplanning : nom, prénom ou identifiant, matières enseignées,
contraintes horaires de la fiche (maximum d'heures par jour et par semaine,
jours et demi-journées libres). **Sans coordonnées personnelles.**
Hyperplanning n'a pas de champ statut : demander séparément le type
d'intervenant (permanent, vacataire, intervenant extérieur) et ses jours de
présence habituels, ainsi que la convention utilisée pour les séances sans
enseignant nommé (« à recruter », intervenant extérieur, distanciel).

### A4. Promotions, TD, options et effectifs (Hyperplanning types « Promotions », « Liens entre TD / options », « Regroupements »)

Promotions, partitions de TD, options (groupes à choix individuel),
regroupements de promotions, liens TD/options, avec pour chacun les colonnes
**Effectif saisi** et **Nombre d'étudiants**, l'appartenance des groupes les
uns aux autres et le calendrier affecté à chaque promotion. Les groupes
transverses (langues par niveau, électifs, cours mutualisés) avec leurs
promotions d'origine. L'effectif suffit, aucune liste d'étudiants.

### A5. Matières et types de cours (Hyperplanning types « Matières », « Matières personnalisées » ; Paramètres > cours > Types de cours)

Code, libellé, module ou UE, promotion de rattachement ; liste exacte des
types de cours définis par l'école (CM, TD, TP, langues, examen, projet…).

### A6. Cursus et modules (Hyperplanning, Plannings > Cursus et modules)

Volumes horaires prévus par promotion, matière, type de cours et module. Si
possible le « Récapitulatif des cours » avec la colonne « Écart avec le
cursus », qui donne directement prévu et réalisé.

### A7. Grille horaire et calendriers (Hyperplanning paramètres généraux)

- Grille horaire et jours ouverts (heures de début et de fin, pas horaire,
  créneaux, samedi ouvert ou non et plage du samedi matin) : une capture
  d'écran suffit.
- Année scolaire, vacances et jours fériés (Paramètres > Paramètres généraux
  > Année scolaire).
- Calendriers par promotion (Paramètres > autres données > Calendriers ;
  export type « Calendriers ») : semaines actives et inactives, ce qui couvre
  stages, alternance et semaines banalisées.
- Pause déjeuner et intercours tels que paramétrés.

### A8. Sites et délais de trajet (Hyperplanning, Paramètres > paramètres généraux > Sites)

Liste des sites avec adresse, tableau « Délais à respecter » (temps de trajet
d'un site à l'autre), nombre de changements de site autorisés par jour pour
les enseignants et les promotions et moment de la journée. Une capture
d'écran suffit.

### A9. Accord écrit et logistique (mail)

Accord sur les usages autorisés (tests, démonstration, soutenance, partage
entre les quatre membres) et la date de suppression ; nom de la personne qui
dispose du droit Import/Export dans Hyperplanning ; confirmation que l'EDT
est produit uniquement sous Hyperplanning (et non le module Planification
d'Aimaira) ; existence d'un accès API ou OData Aimaira via la DSI.

## B. Très utile : rendre la génération réaliste

### B1. Maquette pédagogique (Aimaira Pédagogie, ou direction des études)

Par promotion et par matière : UE et modules, volumes CM, TD, TP, crédits,
période (semestre, semaines de début et de fin), options et parcours,
mutualisations entre programmes, enseignant responsable, type de salle
requis. Complète A6 quand Hyperplanning ne porte pas les volumes.

### B2. Indisponibilités et vœux des enseignants et promotions (Hyperplanning, Plannings > Enseignants > Indisponibilités et vœux)

Indisponibilités (dures, respectées par le placement automatique) et vœux
(préférences, prioritaires mais non garantis), par période ou par semaine,
indisponibilités par défaut. L'export de ces grilles n'est pas documenté :
des captures d'écran ou un tableau enseignant × jour × créneau conviennent.

### B3. Indisponibilités et réservations de salles (Hyperplanning, Plannings > Salles > Indisponibilités et vœux ; réservations en ligne)

Plages d'indisponibilité des salles, réservations hors cours (statut,
demandeur, motif générique), salles réservées à certains utilisateurs
(colonnes accès et propriétaire).

### B4. Séances annulées, dépositionnées et remplacements (Hyperplanning)

« Récapitulatif des cours annulés » avec le motif catégoriel de la liste
« Motifs d'annulation », **sans le commentaire libre** ; séances
dépositionnées (retirées du planning en attente de replanification) ;
remplacements d'enseignants. Hyperplanning ne trace pas les déplacements de
séances : si l'école conserve une sauvegarde de septembre, l'export Cours
refait depuis cette sauvegarde permet de comparer prévu et réalisé.

### B5. Imprévus et pratiques de la scolarité (direction des études, entretien)

Types d'imprévus de l'année passée classés par fréquence, avec la solution
habituelle ; quels cours sont verrouillés ou placés à la main en premier
(vacataires, amphis, intervenants extérieurs) ; captures des écrans de
contraintes du placement automatique (matières, enseignants et promotions,
salles et sites, pause déjeuner, intercours) ; règles non écrites (durées
autres que 2 h, samedi, demi-journées réservées, préférences habituelles).
Un entretien de trente minutes avec la personne qui construit les emplois
du temps.

### B6. Besoins d'accessibilité, anonymisés (mission handicap)

Uniquement sous la forme « le groupe X (année passée) nécessitait des salles
accessibles sans escalier », ou le nombre de groupes concernés par
promotion. **Aucun nom, aucun diagnostic.** Côté salles, la colonne « Accès
PMR » suffit si elle est renseignée.

### B7. Régimes et alternance par groupe (Aimaira Inscription)

Par groupe : formation initiale ou apprentissage, rythme d'alternance,
périodes de stage. Agrégé par groupe, sans nom.

## C. Bonus

- **Export iCal** d'une promotion, d'un enseignant ou d'une salle
  (Imports/Exports > ICAL), pour tester l'import de ce format.
- **Groupes de salles et familles** (salles interchangeables, critères de tri
  de l'école).
- **Colonnes calculées d'occupation des salles** (taux de remplissage,
  occupation annuelle, pic d'occupation), déjà présentes dans la fiche salle.
- **Nombre de présents par séance**, agrégé sans nom (Aimaira contrôle de
  présence ou feuille de présence Hyperplanning).
- **Événements et examens planifiés** avec les salles mobilisées.
- **Équipements requis par matière** ou par type de cours, rarement
  numérisés.
- **Habilitations enseignant × matière**, pour un futur remplacement
  automatique.
- **Captures d'écran** de l'emploi du temps tel que diffusé aux étudiants,
  pour calquer la présentation.

## Confidentialité

- Nous ne demandons **aucune liste d'étudiants** : seuls les effectifs par
  groupe sont nécessaires.
- Nous ne demandons **aucune information de santé nominative** : le besoin
  d'accessibilité nous intéresse uniquement au niveau du groupe, sans nom ni
  nature du handicap.
- Nous ne demandons **pas les commentaires libres** des annulations,
  seulement le motif catégoriel.
- Les noms des enseignants seront **pseudonymisés** avant toute utilisation
  hors de nos machines ; aucune donnée brute ne sera déposée sur GitHub ni
  transmise à un tiers, y compris aux assistants de programmation.
- Les fichiers seront **supprimés à la fin du projet** (après la soutenance du
  14 décembre 2026), sauf accord contraire de l'école ; nous demandons un
  accord écrit sur les usages et cette date, que nous confirmerons.
- Une version déjà pseudonymisée par l'école convient parfaitement, à
  condition que les identifiants restent cohérents entre les fichiers (le
  même enseignant porte le même code partout).

## Priorité minimale si peu de temps

Si une seule chose est possible : **l'export des cours de l'année (A1)**,
**la liste des salles avec capacité et site (A2)**, et deux captures d'écran
(types de cours, paramètres des sites). Tout le reste peut être reconstitué ou
simulé.

## Version courte envoyée au directeur

> Bonjour,
>
> Dans le cadre de notre projet de générateur d'emplois du temps, nous
> aimerions disposer des données d'une année passée, en fichiers CSV ou Excel
> (une ligne par élément), un fichier par type de données, exportés depuis
> Hyperplanning. Si tout n'est pas possible, les points 1 et 2 suffisent pour
> démarrer.
>
> **Indispensable**
> 1. Cours de l'année : date, heures, matière, type de cours, enseignant,
>    promotion ou groupe, salle, site, statut (annulé, dépositionné).
> 2. Salles : nom, code, site, capacité, famille, et les colonnes
>    d'accessibilité (accès PMR) si elles sont renseignées.
> 3. Enseignants : nom ou identifiant, matières, type d'intervenant
>    (permanent, vacataire, extérieur).
> 4. Promotions, TD, options et regroupements, avec effectifs.
> 5. Matières, et liste des types de cours.
> 6. Cursus et modules (volumes prévus par matière et type de cours).
> 7. Grille horaire, année scolaire (vacances, jours fériés), calendriers par
>    promotion, et paramètres des sites (délais de trajet) : des captures
>    d'écran suffisent.
>
> **Utile**
> 8. Maquette pédagogique Aimaira (UE, volumes CM, TD, TP, mutualisations).
> 9. Indisponibilités et vœux des enseignants, réservations de salles.
> 10. Cours annulés avec motif, cours dépositionnés, remplacements
>     d'enseignants.
> 11. Groupes ayant nécessité des salles accessibles, sans aucune donnée
>     nominative.
> 12. Un court entretien avec la personne en charge des emplois du temps.
>
> Aucune liste d'étudiants ni information de santé n'est demandée. Les noms
> des enseignants seront pseudonymisés, rien ne sera publié et les fichiers
> seront supprimés après la soutenance du 14 décembre 2026.
>
> Un extrait de quelques lignes de chaque fichier, même avant l'envoi
> complet, nous serait déjà très utile. Pourriez-vous aussi nous indiquer qui
> dispose du droit d'export dans Hyperplanning ?
>
> Merci beaucoup,
