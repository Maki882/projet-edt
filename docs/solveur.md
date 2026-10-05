# Solveur

> Document en cours de rédaction : les mesures sont notées au fil de l'eau.

## Mesures

Poste de développement (Windows, Python 3.11, OR-Tools 9.15), **2 fils de
calcul**, mode « faisable », graine 1. Les jeux `moyen` et `grand` sont ceux
de `edt.synthese` (graine 0) ; « généré n » désigne le générateur de
`tests/solveur/generateur.py` (deux campus).

### Première formulation : un seul modèle, intervalles optionnels par salle

Une variable entière de début et un booléen par salle admissible pour chaque
séance ; `NoOverlap` par personne ; un intervalle optionnel par (séance,
salle) et un `NoOverlap` par salle.

| Jeu | Séances | Salles | Variables | Contraintes | Construction | Résolution | Issue |
|---|---|---|---|---|---|---|---|
| moyen S1 | 111 | 32 | 2 778 | 9 265 | 0,10 s | 1,7 s | solution |
| moyen S2 | 110 | 32 | 2 704 | 9 213 | 0,10 s | 1,4 s | solution |
| généré 200 | 200 | 22 | 5 087 | 12 227 | 0,13 s | 8 à 25 s selon les exécutions | solution |
| généré 800 | 800 | 82 | 42 968 | 75 282 | 0,75 s | 120 s | **pas de solution** (prétraitement de CP-SAT inachevé) |
| grand S1 | 595 | 99 | 29 842 | 90 658 | 5,7 s | 115 s | **pas de solution** (135 732 branches, 0 conflit) |

Constat : la recherche ne rencontre presque aucun conflit (le problème n'est
pas combinatoirement dur), mais chaque décision coûte cher : les `NoOverlap`
de salles portent des milliers d'intervalles optionnels dont la présence
reste inconnue tant que la salle n'est pas choisie. Variante essayée : un
booléen par début admissible et « au plus une séance par instant » pour les
personnes, intervalles optionnels gardés pour les salles : 200 séances en
25 s, 800 séances sans solution en 120 s. Le temps limite de l'équipe (30 s
pour 200 séances, 120 s pour le jeu grand) est dépassé : conformément à la
décision 8 de `docs/decisions.md`, on passe à la résolution en deux phases
(horaires puis salles).
