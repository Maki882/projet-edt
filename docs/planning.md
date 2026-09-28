# Cadre du cours et planning

Source : documents du module PTS Informatique (ISEP, enseignant Hervé Allanic).

## Ce que le module attend d'un projet

- Interface graphique ou web
- Base de données
- Gestion de version avec GitHub (obligatoire pour le code)
- Tests unitaires et tests d'intégration
- Langage libre (Python par défaut), projet validé par l'enseignant

## Notation (sur 20)

| Critère | Points |
|---|---|
| Qualité de la documentation | 3 |
| Utilisation de Git et GitHub pour travailler en équipe | 3 |
| Qualité des tests unitaires et d'intégration | 3 |
| Soutenance | 3 |
| Travail réalisé pendant les séances (note individuelle) | 8 |

Le travail en séance et l'usage de Git sont notés individuellement : chacun
doit avoir des commits et des PR à son nom.

## Calendrier des séances

| Date | Séance | Contenu |
|---|---|---|
| 14/09/2026 | 1 | Présentation, équipes, choix du projet |
| 21/09/2026 | 2 | Spécifications fonctionnelles et modélisation UML |
| 28/09/2026 | 3 | Spécifications techniques et organisation du travail |
| 05/10/2026 | 4 | Démarrage des développements avec GitHub en équipe |
| 12/10/2026 | 5 | Mise en place des tests unitaires |
| *vacances* | | *2 semaines* |
| 02/11/2026 | 6 | Mise en place des tests d'intégration |
| 16/11/2026 | 7 | Finalisation des développements |
| 23/11/2026 | 8 | Documentation utilisateur et préparation de la soutenance |
| 14/12/2026 | | **Soutenance** |

## Livrables documentaires

### Spécifications fonctionnelles (format livret)

Compréhensible par un non-informaticien, sans détail technique.

1. Description de l'application : vue d'ensemble, contexte, intérêt (1 à 2 pages)
2. Fonctionnement de l'application : fonctionnalités, cas d'usage, maquettes
   d'écran, chaque fonctionnalité classée **obligatoire / utile / optionnelle**
   (2 pages minimum)
3. Modélisation : diagramme de classes UML (1 page)

État actuel : la partie 1 et les contraintes sont dans `spec.md`. Il reste à
rédiger la partie 2 (fonctionnalités classées) et la partie 3 (UML).

### Spécifications techniques et organisation (format livret)

1. Spécifications techniques (2 pages minimum, 2 ou 3 lignes par technologie) :
   matériel ciblé, système d'exploitation, interfaces externes, stockage des
   données, autres technologies, langages, bibliothèques.
2. Organisation du projet (2 pages minimum) : découpage en lots et sous-étapes,
   planning (qui fait quoi et quand, Gantt éventuel), environnement de
   développement (IDE, tests, gestion des bibliothèques), méthodes de suivi
   (comptes rendus, Trello…), moyens de communication, référentiel GitHub.

### Documentation utilisateur

À produire pour la séance du 23/11/2026.

## Note

Le document `Sujets.pdf` fourni provient d'un autre module (CT.2309,
Approfondissement Informatique, G. Carpentier) et liste des sujets de projets
avec des contraintes MVC, interface graphique et client-serveur. Il sert
d'inspiration, pas de cadre pour ce projet.
