# Données fictives

L'école n'a fourni aucune donnée. Le paquet `edt.synthese` fabrique donc un
établissement d'ingénieurs **fictif mais réaliste** (type ISEP) qui alimente
les tests, le solveur et la démonstration. Tout y est inventé : noms tirés
par Faker (`fr_FR`), identifiants anonymes, aucune donnée de santé
(l'accessibilité est un drapeau porté par un groupe).

Chaque jeu est **faisable par construction** : le générateur construit en même
temps que les besoins un emploi du temps **témoin** qui respecte toutes les
contraintes dures, et le soumet au vérificateur officiel.

## Utilisation

```bash
python -m edt init-db
python -m edt jeu --taille moyen --graine 0           # base vide de référentiels
python -m edt jeu --taille grand --reset --oui         # remise à zéro puis chargement
python -m edt jeu --taille petit --reference           # + témoin enregistré comme référence
```

| Fonction | Rôle |
|---|---|
| `edt.synthese.generer_jeu(session, taille, graine=0, annee_debut=None, enregistrer_reference=False) -> RapportJeu` | génère le jeu dans une base initialisée et vide de référentiels ; ne valide pas la transaction |
| `edt.services.jeux.charger_jeu_fictif(session, taille, graine, ...)` | même chose, refusée pendant un calcul |
| `edt.services.jeux.recharger_jeu_fictif(taille, graine, reinitialiser=..., ...)` | remise à zéro facultative puis chargement, dans ses propres sessions (à appeler depuis une route web) |
| `edt.services.jeux.remettre_a_zero()` | supprime tout et recrée la base avec ses données par défaut |
| `edt.services.jeux.tailles_disponibles()` | les trois tailles et leur description, pour l'écran de configuration |

`taille` est `"petit"`, `"moyen"`, `"grand"` ou une `ConfigurationJeu`.
Même taille et même graine donnent **exactement** les mêmes lignes (un seul
`random.Random(graine)`, Faker initialisé avec la graine, aucune donnée tirée
de l'horloge, ordre d'insertion fixe). Seule la durée mesurée dans le rapport
varie.

Le générateur réutilise les lignes posées par `initialiser_base` (grille,
types de salle, équipements, types de séance, paramètres, année scolaire,
semestres, jours fériés) ; il ajoute seulement les types de salle
`LANGUES` et `PROJET`.

## Contenu des trois tailles

Chiffres de la graine 0, année 2026-2027 (la génération du jeu grand prend
environ 6 s ; le seuil du test `perf` est de 60 s).

| | petit | moyen | grand |
|---|---|---|---|
| Promotions | A1 (2 classes) | A3 (6 classes + 2 d'apprentis) | A1 à A5 (40 classes dont 6 d'apprentis) |
| Semestres garnis | S1 | S1, S2 | S1, S2 |
| Sites / salles | 2 / 14 | 3 / 32 | 3 / 99 |
| Groupes / étudiants / inscriptions | 13 / 65 / 195 | 39 / 246 / 646 | 229 / 1 471 / 4 105 |
| Enseignants (dont recrutés pendant le témoin) | 7 (1) | 34 (5) | 136 (6) |
| Matières | 7 | 30 | 148 |
| Besoins (par semaine / total / ponctuel) | 23 (15 / 4 / 4) | 221 (119 / 50 / 52) | 1 164 (694 / 301 / 169) |
| Séances hebdomadaires S1 / S2 | 23 / - | 111 / 110 | 595 / 569 |
| Fermetures / indisponibilités | 22 / 4 | 60 / 26 | 152 / 171 |
| Charge des demi-groupes S1 (moyenne / max) | 40 % / 48 % | 59 % / 80 % | 56 % / 80 % |
| Charge des enseignants S1 (moyenne / max) | 11 % / 25 % | 14 % / 30 % | 18 % / 35 % |
| Besoins écartés | 0 | 0 | 0 |
| Vérificateur sur le témoin | 0 erreur, 1 information | 0 erreur, 9 informations par semestre | 0 erreur, 51 / 41 informations |

Taux de charge : minutes occupées dans la semaine (union des plages) sur les
minutes de créneaux du lundi au vendredi (2 400). Pour un demi-groupe, toute
séance d'un groupe en conflit avec lui compte (sa classe, sa promotion, ses
groupes de langue). Les informations du vérificateur sont les créneaux
imposés hors grille (DST de 2e année à 8 h, amphi de rentrée à 10 h,
partiels à 14 h).

Le résumé du rapport (`RapportJeu.resume()`) donne aussi, par semestre, le
taux d'occupation par type de salle et les indicateurs du vérificateur sur
le témoin (`edt.verification.calculer_indicateurs` : trous, changements de
campus, samedi, créneaux extrêmes, valeur de l'objectif) : c'est le point de
comparaison avec la solution du solveur.

## Hypothèses de réalisme

**Sites.** Notre-Dame-des-Champs (`NDC`, salles `N104`...), Issy-les-Moulineaux
(`ISSY`, salles `L205`...) et un établissement voisin de NDC où l'école loue
des salles (`STAN`, salles `Stan301`...). Trajets : NDC-Issy 60 min,
NDC-voisin 10 min, Issy-voisin 65 min.

**Salles.** Amphithéâtres (capacités fixées par la configuration), salles de
cours (24 à 48 places), salles informatiques, laboratoires d'électronique
(type `TP`) et de physique (`LABO`), salles de langues, salles de projet ;
étage, bâtiment et équipements cohérents avec le genre (ordinateurs en salle
informatique, matériel d'électronique au labo...). Accessibilité tirée
(oui 55 %, non 25 %, inconnu 20 %), la première salle de chaque genre et de
chaque site étant accessible.

**Groupes.** Années 1-2 : cycle intégré à NDC ; années 3-5 : cycle ingénieur
à Issy, avec des classes d'apprentis (`A3-APP1`...). Promotion > classes de
30 à 36 étudiants (22 à 28 pour les apprentis) > deux demi-groupes. Groupes
transverses de la filière classique, alimentés par des inscriptions
d'étudiants : anglais par niveau (`A1-ANG3`), LV2 en années 1-3 (espagnol,
allemand, chinois), parcours en années 4-5 (`A4-P-CYB`...). Une option
« entrepreneuriat » en 5e année sans liste d'étudiants, déclarée
chevauchante avec la promotion. Un groupe tiers-temps par promotion
(`A1-TT`, environ 3 % de l'effectif). Un demi-groupe (deux dans le jeu
grand, en A1 et A2) porte le drapeau « salle accessible requise » : toutes
les séances de sa classe, de sa promotion et de ses groupes de langue
exigent alors une salle accessible. Le parent vaut
toujours la somme de ses enfants ; un demi-groupe ou un transverse vaut le
nombre de ses inscrits.

**Enseignants.** Identifiants `E0001`..., civilité et nom tirés (aucun doublon
« Mme Martin »), vivier par discipline. Statuts : permanents, vacataires
(un ou deux jours de présence, parfois le samedi matin ou une seule
demi-journée), extérieurs (un jour). Les jours d'absence sont des
indisponibilités récurrentes. Environ 20 % ont des maxima horaires, quelques
permanents une absence datée (jusqu'à trois jours). Un ou deux TD sont
confiés à un enseignant fictif « à recruter » (`FICTIF-01`).

**Matières.** Codes `<département>.<année><semestre><numéro>` (« IM.1101 » :
mathématiques, 1re année, semestre 1, module 1 ; « IX » pour les DST, 9x pour
la vie de l'école, 61-66 pour les parcours). Programme d'école du numérique
par année et par semestre (`edt.synthese.catalogue.MODULES`).

**Besoins.**

- CM de promotion en amphithéâtre (pour les années 3-5, la filière classique
  sans les apprentis : un besoin à plusieurs classes) ; CM « commun » à toute
  la promotion en mode `total` sur la première moitié du semestre ;
- TD par classe, TP par demi-groupe (2 h ou 4 h, samedi matin permis) ; TP
  d'électronique en mode `total` avec **alternance** : le premier
  demi-groupe a la première moitié de la fenêtre, le second l'autre ; TP
  d'électronique du cycle ingénieur avec site imposé (Issy) ;
- langues par groupe transverse (et par classe pour les apprentis), projets
  par classe (2 h ou 4 h, parfois deux encadrants) ;
- matières courtes en mode `total` sur une moitié de semestre ;
- **DST hebdomadaires à créneau imposé** : 1re année le jeudi à 13 h 45
  (3 h, tiers-temps 4 h), 2e année le lundi à 8 h (hors grille ; 3 h,
  tiers-temps 4 h jusqu'à midi) ; une séance par classe, une pour le groupe
  tiers-temps ;
- **partiels** (années 3-5) : semaine fermée propre à la promotion (nature
  « examens »), épreuves ponctuelles par classe à début imposé (8 h 30 pour
  2 h, 14 h pour 3 h), tiers-temps dans sa propre salle avec un tiers de
  temps en plus, deux salles réservées par le service des examens ;
- événements ponctuels : amphi de rentrée (salle et créneau imposés),
  rencontre avec le tuteur pendant la semaine d'intégration, conférence
  métiers à une date choisie par le témoin.

**Calendrier.** Les deux semestres de l'initialisation ; vacances de la
Toussaint, de Noël (deux semaines), d'hiver et de printemps (fermetures
globales) ; semaine d'intégration de la 1re année (« semaine spéciale ») ;
stage en entreprise sur les quatre dernières semaines du S2 (A4 dans le jeu
grand, A3 dans le moyen), partiels du S2 de cette promotion la semaine
d'avant ; mercredi après-midi libéré pour le sport en années 1-2
(indisponibilité récurrente de la promotion).

## Méthode du témoin

Le générateur prépare d'abord les besoins « en projet », puis les place un
par un dans un emploi du temps témoin (`edt.synthese.temoin`), et n'écrit en
base que ceux dont **toutes** les séances ont trouvé une place.

1. **Dépliage officiel.** Les séances d'un besoin et leurs semaines actives
   viennent de `edt.domaine.depliage.deplier`, avec les fermetures réelles de
   la base. Les semaines sont des masques de bits : deux séances aux
   semaines disjointes ne se gênent jamais. Après écriture, le dépliage des
   besoins créés doit redonner exactement les séances du témoin (contrôle
   interne, erreur sinon).
2. **Ordre de placement**, du plus contraint au moins contraint : créneaux
   imposés, barrettes de langues et de parcours, séances de promotion,
   séances de classe (4 h puis 2 h), TP longs, TP, matières courtes,
   épreuves, événements.
3. **Barrettes.** Les groupes d'anglais d'une promotion (deux vagues en 3e
   année), de LV2 ou de parcours sont placés au même créneau, chacun avec sa
   salle et son enseignant ; sans créneau commun, ils sont placés séparément.
   Le modèle ne connaît pas les barrettes : le solveur les retrouvera, car
   chaque groupe transverse est en conflit avec presque toutes les classes.
4. **Choix d'un créneau** : jours de semaine avant le samedi, jours sans autre
   séance du même besoin, créneaux déjà utilisés par un groupe frère ou par
   les mêmes groupes sur d'autres semaines, jours les moins chargés, puis un
   tirage déterministe.
5. **Choix d'une salle** : la plus petite salle compatible du site préféré de
   la promotion (puis du site voisin, puis de l'autre campus).
6. **Choix des enseignants** dans le vivier de la discipline, le moins chargé
   d'abord, sous un plafond hebdomadaire (15 h permanent, 8 h vacataire, 6 h
   extérieur). Si personne n'est libre, un permanent est recruté et le
   placement retenté.

Règles respectées (sémantique de `docs/architecture.md` et
`docs/verification.md`) : salle, enseignant non fictif et groupes en
conflit jamais en même temps ; 15 minutes au moins (le temps de trajet entre
deux sites) entre deux séances d'une même personne qui coexistent, appliqué
aussi aux salles par prudence ; début sur un créneau de la grille, dans la
plage d'ouverture et dans la demi-journée, hors déjeuner, du lundi au
vendredi (samedi matin pour les TP), sauf créneau imposé ; capacité, type,
équipements, site et salle imposés, accessibilité (groupe drapeauté ou en
conflit avec un groupe drapeauté ; « inconnu » vaut « non ») ;
indisponibilités récurrentes des enseignants, des salles et des groupes liés.

**Indisponibilités et fermetures.** Celles qui contraignent la semaine type
(vacataires, sport) sont décidées avant le placement et respectées. Les
autres sont posées **après**, là où le témoin laisse la place : absences
datées sur des jours sans séance de l'enseignant, salles réservées aux
partiels et salle en travaux sur des semaines où elles sont vides, salle de
réunion le vendredi après-midi, maxima horaires supérieurs à la charge du
témoin. Seuls les jours fériés de l'initialisation tombent sur des séances :
la couche datée les replacera.

**Sortie.** Le témoin n'est pas écrit en base : il est dans le rapport
(`RapportJeu.temoin_par_semestre`, `identifiant de séance -> (jour, début,
fin, salle)` ; `RapportJeu.placements(code)` le donne au format du
vérificateur). Avec `enregistrer_reference=True`, il est aussi enregistré
comme version « référence » **non active** de chaque semestre : le solveur
n'en reçoit aucun indice, mais on peut lui comparer sa solution.

## Jeu sur mesure

```python
import dataclasses
from edt.synthese import GRAND, ConfigPromotion, SallesSite

config = dataclasses.replace(
    GRAND,
    nom="deux promotions",
    promotions=(ConfigPromotion(1, 6), ConfigPromotion(3, 5, nb_classes_apprentis=1)),
    semestres=("S1",),
    part_vacataires=0.4,
)
rapport = generer_jeu(session, config, graine=12)
```

Paramètres : promotions (année, classes, classes d'apprentis), salles par
site (`SallesSite` : capacités des amphis, nombres de salles par genre),
semestres, nombre maximal de modules par promotion et semestre, bornes des
effectifs, part de tiers-temps, nombre de groupes PMR, part de vacataires et
d'extérieurs, part d'enseignants avec maxima, nombre d'absences datées,
nombre d'enseignants fictifs, promotions en stage, épreuves par partiel, et
interrupteurs `dst`, `partiels`, `evenements`, `etudiants` (sans étudiants,
les groupes transverses sont déclarés chevauchants avec leur promotion).
Une configuration incohérente lève `ErreurDonneesInvalides` avec la liste
des problèmes. Un besoin qui ne trouve pas de place est **écarté**, compté et
expliqué dans le rapport (jamais en silence) : c'est le signe qu'il manque
des salles pour la configuration choisie.

## Limites

- **Tiers-temps.** Les étudiants tiers-temps composent dans une autre salle,
  au même moment que leur classe. Le modèle ne sait pas dire « ces étudiants
  quittent leur classe pour cette épreuve » : s'ils étaient inscrits dans le
  groupe tiers-temps, ce groupe serait en conflit avec leur classe et les
  deux épreuves simultanées seraient refusées (`GROUPE_UNIQUE`). Le groupe
  tiers-temps n'a donc **ni inscriptions ni chevauchement déclaré** (son
  effectif est saisi), et la salle de la classe compte ces étudiants en trop.
  Proposition : une colonne `Besoin.epreuve_principale_id` (le besoin
  tiers-temps désigne l'épreuve dont il est le double) qui imposerait le même
  début et lèverait le conflit de groupes entre les deux seules séances.
- **Fermetures et groupes transverses.** Une fermeture de portée « groupe »
  ne vaut que pour ses descendants : les partiels, le stage et la semaine
  d'intégration reçoivent donc une fermeture par groupe transverse et
  tiers-temps de la promotion. Proposition : une colonne
  `Groupe.promotion_id` (rattachement sans inclusion) prise en compte par
  les fermetures.
- **Matière et semestre.** Une matière n'a pas de semestre ; c'est le besoin
  qui le porte, et le code du module l'indique (« IM.1101 »).
- **Barrettes** non représentables : voir la méthode du témoin.
- **DST** : la durée « fixée par l'enseignant » est une durée fixe (3 h,
  4 h en tiers-temps) dans le créneau réservé.
- **Option sans liste** : déclarée chevauchante avec sa promotion, elle n'est
  en conflit qu'avec la lignée de la promotion, pas avec les parcours (règle
  du projet).
- **Apprentis** : semaine type « école » seulement (décision V1).
- Le témoin est glouton : il prouve la faisabilité, pas la qualité. Ses
  indicateurs (trous, changements de campus) sont un plancher que le
  solveur doit améliorer.
