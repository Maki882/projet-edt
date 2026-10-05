# Réponses au questionnaire

Réponses brutes de l'équipe, conservées pour traçabilité. La synthèse qui fait
autorité est `docs/decisions.md`.

| Fichier | Section |
|---|---|
| `section-01-perimetre.txt` | 1. Périmètre, utilisateurs, démonstration |
| `section-02-donnees-reelles.md` | 2. Données réelles et confidentialité |
| `section-03-import.txt` | 3. Import, identifiants, jeu synthétique |
| `section-04-calendrier.txt` | 4. Calendrier et horizon (avec le tableau des DST) |
| `section-06-groupes.txt` | 6. Structure académique et groupes |
| `../questions-avant-developpement.md` | 3 et 11 : réponses saisies directement après chaque question (lignes « R: ») |

Réponses données de vive voix ou par message, sans fichier :

- **7.1** : les données seront générées. **7.2** : tout doit être configurable,
  données réalistes générées.
- **Sections 7 à 11** : prendre en compte tous les cas réalistes possibles ;
  les données sont générées.
- **12.1** : interface web qui tourne sur le VPS. **12.2** : toutes les données
  sont éditables dans l'interface. **12.3** : il faut une interface pour
  configurer et ingérer les données. **12.4** : vues par groupe (tous types de
  groupes), par semaine et par jour. **12.6** : défaut (modification par
  formulaire, glisser-déposer plus tard).
- **Tout le projet** : privilégier la simplicité et l'efficacité ; code bien
  documenté, débogable, bien organisé ; rien n'échoue en silence ; équilibre
  entre production et vérification.
- **Hébergement** : VPS OVH, pas d'identifiants de connexion pour accéder à
  l'application ; toutes les données sont fictives pour le moment.
- **Questions sans réponse** : « on verra plus tard » ou « fais ce qui te
  semble le mieux ».
