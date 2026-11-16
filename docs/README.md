# Carte de la documentation

Cette page dit quel document lire selon la question posée. Elle s'adresse à
tout lecteur du dépôt : utilisateur de l'application, enseignant, développeur
ou exploitant du serveur.

## Par lecteur

- **Utilisateur** (responsable de scolarité) : le
  [guide de l'utilisateur](livrets/3-guide-utilisateur.md), puis
  l'[assistant](assistant.md) pour les demandes en langage courant.
- **Enseignant, jury** : les trois livrets, dans l'ordre :
  [spécifications fonctionnelles](livrets/1-specifications-fonctionnelles.md),
  [spécifications techniques et organisation](livrets/2-specifications-techniques-et-organisation.md),
  [guide de l'utilisateur](livrets/3-guide-utilisateur.md) ; leurs versions
  PDF : [livret 1](livrets/pdf/1-specifications-fonctionnelles.pdf),
  [livret 2](livrets/pdf/2-specifications-techniques-et-organisation.pdf),
  [livret 3](livrets/pdf/3-guide-utilisateur.pdf), aussi jointes à la
  version 1.0.0 publiée sur GitHub (page « Releases » du dépôt) ; puis la
  [stratégie de test](../tests/README.md). Les tests se lancent aussi depuis
  l'application, dans les onglets « Tests unitaires » et « Tests d'intégration »
  (commande équivalente : `python -m edt tests`).
- **Développeur** : le [guide de contribution](../CONTRIBUTING.md), les
  [choix de conception](decisions.md) et l'[architecture](architecture.md),
  puis le document du sous-système à modifier.
- **Exploitant** : le [guide de déploiement](deploiement.md).

## Tous les documents

| Document | Sujet | Public |
|---|---|---|
| [README](../README.md) | présentation, démarrage en cinq minutes, commandes, tests | tous |
| [Livret 1 : spécifications fonctionnelles](livrets/1-specifications-fonctionnelles.md) | ce que fait l'application, fonctionnalités classées, diagramme de classes | enseignant, utilisateur |
| [Livret 2 : spécifications techniques et organisation](livrets/2-specifications-techniques-et-organisation.md) | technologies, architecture d'ensemble, organisation de l'équipe | enseignant, développeur |
| [Livret 3 : guide de l'utilisateur](livrets/3-guide-utilisateur.md) | mode d'emploi, écran par écran | utilisateur |
| [Choix de conception](decisions.md) | périmètre, pile technique, modèle, calendrier, règles, solveur, import et export, interface, évolutions | développeur, enseignant |
| [Architecture](architecture.md) | couches, arborescence, configuration, base, extension, contrats, journaux, erreurs | développeur |
| [Modèle de données](modele-donnees.md) | diagramme de classes UML et description de chaque table | développeur, enseignant |
| [Solveur](solveur.md) | formulation CP-SAT, niveaux, objectif, séances datées, performances, limites | développeur |
| [Vérificateur](verification.md) | règles, anomalies, gravités, indicateurs | développeur |
| [Règles personnalisées](regles-personnalisees.md) | les 14 types de règles, leur sens, leur chaîne technique | développeur |
| [Import et export](import-export.md) | format d'échange, import d'un emploi du temps existant, exports | développeur, utilisateur avancé |
| [Données fictives](donnees-fictives.md) | générateur des jeux petit, moyen et grand | développeur |
| [Interface web](interface.md) | navigation, mise en page, écrans, cas limites | développeur |
| [Assistant](assistant.md) | assistant conversationnel : fonctionnement, garde-fous, limites | utilisateur, développeur |
| [Tests](../tests/README.md) | rangement unitaire et intégration, commandes, tests lancés depuis l'application | développeur, enseignant |
| [Banque de tests](banque-de-tests.md) | conventions des cas limites et inventaire | développeur |
| [Déploiement](deploiement.md) | installation du serveur, mise à jour, sauvegardes, pannes | exploitant |
| [Guide de contribution](../CONTRIBUTING.md) | Git et GitHub, conventions de code et d'écriture, tests | développeur |
| [Consignes des assistants de programmation](../AGENTS.md) | méthode de travail propre aux assistants | développeur |

Les captures d'écran du README sont dans `docs/images/` ; celles des livrets,
avec leurs schémas, dans `docs/livrets/images/`. Les PDF des livrets sont dans
`docs/livrets/pdf/` ; `python docs/livrets/produire_pdf.py` les produit à
partir du texte Markdown (il faut Chrome ou Chromium).
