# Fonctionnement du projet

> Version texte de `Fonctionnement du projet.docx`. **Ébauche** : certains détails
> peuvent encore changer. Les points marqués « à définir » sont ouverts.

## 1. Description de l'application

### 1.1 Présentation générale

Le projet consiste à développer une application permettant de générer
automatiquement les emplois du temps de l'ISEP.

La création d'un emploi du temps est une tâche complexe puisqu'elle doit prendre
en compte de nombreuses contraintes liées aux étudiants, aux enseignants, aux
salles disponibles et à l'organisation des différents campus. L'objectif de
l'application est donc de faciliter cette organisation en générant un emploi du
temps qui respecte l'ensemble de ces contraintes.

L'utilisateur pourra fournir à l'application les informations nécessaires
concernant les cours à organiser, les classes concernées, les enseignants, les
salles disponibles ainsi que les différentes contraintes à respecter. À partir de
ces informations, l'application devra proposer un emploi du temps dans lequel
chaque cours est associé à un jour, un horaire et une salle, tout en évitant les
incompatibilités.

### 1.2 Objectifs de l'application

L'objectif principal est d'automatiser la création des emplois du temps afin de
réduire le travail nécessaire à leur organisation et d'éviter les conflits qui
pourraient apparaître lors d'une création manuelle.

L'application devra notamment s'assurer qu'une même salle n'est pas utilisée par
deux cours simultanément, qu'un professeur n'est pas affecté à plusieurs classes
au même moment et qu'une classe n'a pas plusieurs cours sur un même créneau.

Elle devra également prendre en compte les contraintes liées aux déplacements
entre les différents campus de l'ISEP. Deux cours qui se suivent devront ainsi
avoir lieu sur le même campus. Lorsqu'un changement de campus est nécessaire, un
temps libre d'au moins une heure devra séparer les deux cours afin de permettre
le déplacement.

L'accessibilité devra également être prise en compte. Lorsqu'un étudiant en
situation de handicap doit assister à un cours, celui-ci devra être placé dans
une salle qui lui est accessible, notamment en fonction de la possibilité
d'accéder à la salle sans utiliser un escalier lorsque cela est nécessaire.

### 1.3 Organisation générale des cours

L'application devra construire les emplois du temps en respectant l'organisation
horaire de l'établissement. Les journées de cours commencent à 8 h 30 et se
terminent au plus tard à 18 h. Un cours dure normalement deux heures et une pause
de 15 minutes sépare deux cours successifs.

Une pause déjeuner devra également être prévue au cours de la journée.

Les cours sont normalement organisés du lundi au vendredi. Aucun cours classique
ne devra donc être placé le samedi ou le dimanche. Une exception est toutefois
prévue pour certains travaux pratiques (TP), qui pourront avoir lieu le samedi
matin.

### 1.4 Intérêt de l'application

Cette application permettra de simplifier une tâche qui devient rapidement
difficile lorsque le nombre de classes, d'enseignants, de salles et de cours
augmente. Plutôt que de vérifier manuellement chaque contrainte, l'utilisateur
pourra demander à l'application de construire un emploi du temps cohérent à
partir des informations fournies.

L'application devra ainsi permettre de limiter les erreurs d'organisation, les
conflits de salles ou d'horaires et les emplois du temps impossibles à suivre
pour les étudiants ou les enseignants. Elle devra également contribuer à produire
des emplois du temps adaptés aux contraintes particulières des personnes
concernées.

## 2. Contraintes déjà définies

- **Disponibilité des salles** : une salle ne peut accueillir qu'un seul cours sur un même créneau.
- **Disponibilité des enseignants** : un professeur ne peut pas enseigner à deux classes simultanément.
- **Disponibilité des étudiants** : une même classe ou un même groupe d'étudiants ne peut pas avoir deux cours simultanément.
- **Changement de campus** : deux cours qui se suivent doivent avoir lieu sur le même campus. Si le cours suivant se déroule sur un autre campus, il faut prévoir au moins 1 heure entre les deux.
- **Accessibilité des salles** : lorsqu'un cours accueille une personne en situation de handicap, la salle attribuée doit être compatible avec ses besoins d'accessibilité. Par exemple, une salle nécessitant l'utilisation d'un escalier ne doit pas être attribuée à une personne ne pouvant pas l'emprunter.
- **Horaires d'ouverture** : les cours commencent au plus tôt à 8 h 30 et se terminent au plus tard à 18 h.
- **Durée des cours** : un cours dure normalement 2 heures.
- **Pause entre deux cours** : deux cours successifs sont séparés par une pause de 15 minutes.
- **Pause déjeuner** : une période sans cours doit être prévue afin que les étudiants et les enseignants puissent déjeuner. *À définir : sa durée et sa plage horaire.*
- **Jours de cours** : les cours ordinaires sont organisés du lundi au vendredi. Il n'y a normalement pas cours le samedi et le dimanche.
- **Exception pour les TP** : certains TP peuvent être organisés le samedi matin.

## 3. Contraintes supplémentaires envisagées

- **Capacité des salles.** Une classe de 40 étudiants ne doit pas être placée dans une salle de 25 places. Il faut donc connaître l'effectif du groupe et la capacité maximale de chaque salle.
- **Type de salle.** Certains enseignements nécessitent une salle particulière : laboratoire, salle informatique, salle de TP, amphithéâtre, etc. Un cours nécessitant des ordinateurs ne peut être placé que dans une salle équipée.
- **Disponibilités des professeurs.** Certains enseignants peuvent ne pas être disponibles certains jours ou à certaines heures. Le générateur doit respecter leurs indisponibilités.
- **Disponibilité exceptionnelle des salles.** Une salle peut être indisponible temporairement : travaux, examen, événement, maintenance, etc.
- **Groupes et sous-groupes.** Une promotion peut être divisée en plusieurs groupes de TD ou TP. Deux groupes différents peuvent avoir cours simultanément, alors que les étudiants appartenant au même groupe ne le peuvent pas.
- **Nombre de séances à placer.** Chaque matière doit avoir un certain nombre de cours, TD ou TP à organiser dans la semaine. L'application doit vérifier qu'ils ont tous été placés.
- **Matériel nécessaire.** Certains cours peuvent nécessiter des équipements spécifiques : vidéoprojecteur, ordinateurs, matériel électronique, équipements de laboratoire, etc.

## 4. Principes de fonctionnement

- **Aucune contrainte n'est abandonnée.** L'application ne doit jamais supprimer une contrainte simplement parce qu'elle n'arrive pas à construire l'emploi du temps. Si aucune solution n'est possible, elle prévient l'utilisateur en indiquant qu'un emploi du temps respectant toutes les contraintes n'a pas pu être généré.
- **Contraintes obligatoires et préférences.** Plus tard, on distinguera les contraintes absolues (« un professeur ne peut pas être dans deux salles en même temps ») des préférences (« éviter de donner cours à un professeur à 8 h 30 ») que l'application essaie de satisfaire lorsqu'elle le peut. Cette distinction n'est pas dans la première version.
- **Classement des fonctionnalités.** Conformément aux spécifications fonctionnelles demandées par le professeur, les fonctionnalités seront classées par niveau d'utilité : obligatoire, utile, optionnelle.

## 5. Points à définir

Questions ouvertes relevées à la lecture de l'ébauche, à trancher en équipe :

- Grille horaire exacte des créneaux entre 8 h 30 et 18 h (cours de 2 h + pauses de 15 min) et durée du déjeuner.
- Horizon de génération : semaine type répétée, ou semestre réel avec dates et vacances.
- Format d'entrée des données : saisie dans l'interface, import CSV/Excel, ou les deux.
- Modélisation du handicap : par étudiant, donc tous les cours de son groupe doivent être en salle accessible.
- Samedi matin : plage horaire exacte et application ou non de la pause déjeuner.
