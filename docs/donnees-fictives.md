# Données fictives

Ce document décrit le générateur de jeux de données fictifs : son usage, ce
qu'il produit et comment il garantit la faisabilité. Il s'adresse aux
développeurs.

Toutes les données de l'application sont fictives. Le paquet `edt.synthese`
fabrique un établissement d'ingénieurs **fictif mais réaliste** (type ISEP)
qui alimente les tests, le solveur et la démonstration. Tout y est inventé : noms tirés
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
`graine` est un entier de 0 à 2 147 483 647 (la borne du formulaire web) ; une
graine négative est refusée, car `random.Random(-5)` redonnerait le jeu de la
graine 5. `annee_debut` va de 2000 à 2100. Une valeur refusée lève
`ErreurDonneesInvalides` avant toute écriture (`valider_demande`) ; la commande
`jeu` la vérifie aussi **avant** la remise à zéro.

Même taille et même graine donnent les mêmes données (un seul
`random.Random(graine)`, aucune donnée tirée de l'horloge, ordre d'insertion
fixe). Faker ne fournit que les **noms et prénoms** : une autre version de
Faker change les noms, rien d'autre (le nombre de tirages ne dépend pas des
noms proposés). Le test `test_empreinte_figee_du_jeu_petit_graine_0` fige une
empreinte du jeu « petit » sans les noms : si elle change sans modification
du générateur, le jeu dépend de la machine. Seule la durée mesurée dans le
rapport varie.

Le générateur réutilise les lignes posées par `initialiser_base` (grille,
types de salle, équipements, types de séance, paramètres, année scolaire,
semestres, jours fériés) ; il ajoute seulement les types de salle
`LANGUES` et `PROJET`.

## Contenu des trois tailles

Chiffres de la graine 0, année 2026-2027 (la génération du jeu grand prend
environ 1,5 s ; le seuil du test `perf` est de 60 s).

| | petit | moyen | grand |
|---|---|---|---|
| Promotions | A1 (2 classes) | A3 (6 classes + 2 d'apprentis) | A1 à A5 (40 classes dont 6 d'apprentis) |
| Semestres garnis | S1 | S1, S2 | S1, S2 |
| Sites / salles | 2 / 14 | 3 / 34 | 3 / 99 |
| Groupes / étudiants / inscriptions | 13 / 65 / 195 | 39 / 246 / 646 | 232 / 1 471 / 4 105 |
| Enseignants (dont recrutés pendant le témoin) | 7 (1) | 34 (5) | 143 (11) |
| Matières | 7 | 30 | 154 |
| Besoins (par semaine / total / ponctuel) | 23 (15 / 4 / 4) | 221 (119 / 50 / 52) | 1 177 (707 / 301 / 169) |
| Séances hebdomadaires S1 / S2 | 23 / - | 111 / 110 | 602 / 575 |
| Fermetures / indisponibilités | 22 / 4 | 60 / 26 | 160 / 170 |
| Charge des demi-groupes S1 (moyenne / max) | 40 % / 52 % | 59 % / 80 % | 56 % / 80 % |
| Charge des enseignants S1 (moyenne / max) | 14 % / 25 % | 14 % / 30 % | 19 % / 35 % |
| Règles personnalisées (actives / inactives / proposées) | 9 (6 / 1 / 2) | 14 (11 / 1 / 2) | 16 (13 / 1 / 2) |
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
allemand, chinois), parcours en années 4-5 (`A4-P-CYB`... ; huit parcours au catalogue, un par groupe de 34 étudiants environ). Une option
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
(indisponibilité récurrente de la promotion). Un S2 de moins de six
semaines n'a pas de stage (journalisé). La semaine d'intégration et les
partiels ferment la semaine entière, du lundi au dimanche, comme les
vacances : la semaine est retirée des semaines actives quels que soient les
jours travaillés.

## Règles personnalisées

Chaque jeu reçoit des règles personnalisées (`docs/regles-personnalisees.md`,
§ 4.7), choisies selon les groupes, matières et sites présents
(`edt.synthese.regles`) : le jeu grand les a toutes, le moyen celles de la
3e année, le petit celles de la 1re année. Codes `R001`... dans l'ordre du
tableau ; origine « saisie », sauf les deux règles de base du catalogue
(origine « base », laissées **proposées**, donc sans effet). Dates de
création fixes : le jeu reste déterministe.

| Règle (jeu grand) | Type | Niveau | État | Témoin | Jeux |
|---|---|---|---|---|---|
| La promotion A1 ne travaille que du lundi au vendredi (pas même ses TP le samedi). | `JOURS_TRAVAILLES` | nécessaire | active | posée | petit, grand |
| La classe A3-APP1 a aussi cours le samedi. | `JOURS_TRAVAILLES` | important | active | posée | moyen, grand |
| La classe A2-G3 a une pause déjeuner libre d'au moins 1 h 15 entre 11 h 45 et 14 h 15 (semestre 1 ; A3-G2 en moyen, A1-G1 en petit). | `PAUSE_DEJEUNER` | important | active | posée | tous |
| La classe A4-APP1 a au moins 30 min entre deux séances (A3-APP2 en moyen). | `PAUSE_MINIMALE` | important | active | posée | moyen, grand |
| Les CM n'ont pas lieu le vendredi de 13 h 45 à 18 h. | `CRENEAUX_INTERDITS` | important | active | posée | tous |
| Les séances de la promotion A3 ont lieu sur le campus d'Issy. | `CAMPUS` | nécessaire | active | posée | moyen, grand |
| Les séances d'Algorithmique et programmation 1 ont lieu sur le campus NDC. | `CAMPUS` | nécessaire | active | posée | petit, grand |
| Les séances d'Électronique embarquée ont lieu dans quatre laboratoires nommés (Électronique numérique 1 et deux laboratoires en petit). | `SALLES` | important | active | posée | tous |
| La promotion A3 ne change pas de campus dans la journée. | `CAMPUS_PAR_JOUR` | important | active | par construction | moyen, grand |
| La promotion A5 n'a pas plus de 6 h de cours par jour (A3-APP2 en moyen). | `MAX_PAR_JOUR` | important | active | posée | moyen, grand |
| Les séances d'Analyse 1 ont lieu des jours différents (Traitement du signal en moyen). | `REPARTITION_MATIERE` | facultatif | inactive | sans effet | tous |
| Un permanent de mathématiques commence à 9 h au plus tôt. | `PLAGE_HORAIRE` | nécessaire | active | posée | tous |
| Deux vacataires viennent au plus deux jours par semaine. | `JOURNEES_LIBRES` | important | active | par construction | moyen, grand |
| Dans chaque matière, le CM précède les TD de la semaine. | `ORDRE_SEMAINE` | facultatif | proposée | sans effet | tous |
| Chaque enseignant a au moins une demi-journée libre par semaine. | `JOURNEES_LIBRES` | facultatif | proposée | sans effet | tous |

**Posées dans le témoin** (quel que soit leur niveau) : les jours permis de
chaque séance (précédence du contrat : règle la plus précise de chaque
lignée, intersection entre axes), les plages interdites, les campus et
salles filtrés, la pause minimale de chaque personne (la plus grande des
pauses des personnes d'une paire de séances), la pause déjeuner libre et
le maximum par jour de chaque groupe feuille, mesurés prudemment sur
l'union des séances du jour qui ont une semaine en commun. Les plages
d'horaires d'un enseignant lui sont posées comme des interdits du vivier
(pas comme des indisponibilités).

**Par construction** : `CAMPUS_PAR_JOUR` sur A3 découle de la règle
`CAMPUS` (toutes les séances de la promotion à Issy) ; `JOURNEES_LIBRES` vise
des vacataires dont les indisponibilités récurrentes laissent au plus deux
jours de présence, sans samedi.

**Jours** : le témoin suit la même précédence que le vérificateur et le
solveur (`Instantane.reglages`) : standard des jours travaillés (plus le
samedi pour un type de séance qui l'autorise, sauf règle visant ce type),
sinon l'intersection des règles retenues. Un jour ouvert par une règle seule
lui est donc permis : le samedi d'A3-APP1 peut accueillir ses TD et ses CM.
Le témoin le garde en dernier recours (le samedi passe après les jours de
semaine dans son ordre de préférence) : selon la graine, il l'utilise ou non.

**Contrôle** : le vérificateur et le solveur vérifient et posent les
quatorze types de règle. `tests/unitaires/synthese/test_verificateur.py` vérifie que
le témoin des jeux petit, moyen et grand ne provoque **aucune anomalie
`REGLE`**, à aucun niveau ; `tests/outils/synthese/controle_temoin.py`
(`violations_regles`) en fait un second contrôle, direct et indépendant,
à partir de `Instantane.regles`, `reglages` et `emplois`.

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
   séances de 4 h (classe ou demi-groupe : il leur faut une demi-journée
   entière), autres séances de classe, TP, matières courtes, épreuves,
   événements.
3. **Barrettes.** Les groupes d'anglais d'une promotion (deux vagues en 3e
   année), de LV2 ou de parcours sont placés au même créneau, chacun avec sa
   salle et son enseignant ; sans créneau commun, ils sont placés séparément.
   Le modèle ne connaît pas les barrettes : le solveur les retrouvera, car
   chaque groupe transverse est en conflit avec presque toutes les classes.
4. **Choix d'un créneau** : jours de semaine avant le samedi, jours sans autre
   séance du même besoin, créneaux déjà utilisés par un groupe frère ou par
   les mêmes groupes sur d'autres semaines, demi-journées déjà entamées (les
   demi-journées entières restent libres pour les séances de 4 h), jours les
   moins chargés, puis un tirage déterministe. Les jours permis et les plages
   interdites par les règles personnalisées sont filtrés avant.
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
plage d'ouverture et dans la demi-journée, hors déjeuner, un jour permis
(jours travaillés, samedi matin pour les TP ou ouvert par une règle), sauf
créneau imposé ; capacité, type,
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

**Relances.** Le placement glouton peut, pour une graine malheureuse, laisser
un besoin sans place alors qu'il en existait une (graine 13 du jeu moyen).
Le semestre est alors replacé de zéro avec un autre départage, dérivé de la
graine (donc déterministe), jusqu'à quatre essais, et le meilleur est retenu.
Le premier essai est celui d'avant les relances : un jeu sans écart au
premier essai est inchangé. Aucun besoin écarté sur les graines 0 à 39 du
jeu moyen ni 0 à 29 du grand.

**Besoins impossibles d'avance.** Une conférence sans date ouverte ou des
épreuves sans demi-journée ouverte (semestre minuscule, jours fériés) sont
écartés avec leur raison, comme les besoins que le témoin ne peut pas
placer : tout besoin préparé est créé ou écarté, jamais omis.

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
des problèmes : année hors de 1 à 5, classes d'apprentis hors du cycle
ingénieur, site inconnu ou répété, **aucun site avec au moins une salle**,
capacité d'amphithéâtre nulle ou négative, nombre de salles négatif, parts
hors de 0 à 1. Un besoin qui ne trouve pas de place est **écarté**, compté
et expliqué dans le rapport (jamais en silence) : c'est le signe qu'il manque
des salles pour la configuration choisie. Au-delà de 800 enseignants (400
noms de famille chez Faker, deux civilités), des homonymes apparaissent : les
identifiants restent uniques.

## Cas limites

Les fichiers `test_cas_limites.py` de `tests/unitaires/synthese/` et
`tests/integration/synthese/` (marqueur `cas_limite`, une docstring « Cas :
… Risque : … » par test, catalogue dans
[banque-de-tests.md](banque-de-tests.md)) figent le comportement aux bords :

| Situation | Comportement |
|---|---|
| Graine négative, trop grande ou non entière ; année hors de 2000-2100 | refus lisible avant toute écriture ; la commande ne touche pas à la base |
| Configuration sans salle, salles négatives | refus à la construction de la configuration |
| Une seule salle, pas d'amphithéâtre | jeu partiel : besoins écartés avec leur raison, témoin valide |
| Une classe d'un étudiant (demi-groupe vide) | jeu complet et valide |
| Semestre d'un jour, grille d'un créneau, samedi seul, dimanche sans créneau, pause de 2 h | jeu partiel cohérent ; taux de charge à 0 (journalisés) sans créneau un jour travaillé |
| S2 trop court pour un stage | pas de stage, partiels gardés |
| Grille vide, semestre manquant | refus lisible qui dit quoi faire |
| Autre version de Faker, noms épuisés | même jeu hors noms ; homonymes admis, pas de boucle sans fin |
| Graines 10 à 19 de chaque taille (test `perf`) | aucun besoin écarté, témoin et règles valides |

Le service `recharger_jeu_fictif` contrôle la graine et l'année
(`valider_demande`) avant toute remise à zéro : une demande refusée ne vide
jamais la base.

## Limites

- **Tiers-temps.** Les étudiants tiers-temps composent dans une autre salle,
  au même moment que leur classe. Le modèle ne sait pas dire « ces étudiants
  quittent leur classe pour cette épreuve » : s'ils étaient inscrits dans le
  groupe tiers-temps, ce groupe serait en conflit avec leur classe et les
  deux épreuves simultanées seraient refusées (`GROUPE_UNIQUE`). Le groupe
  tiers-temps n'a donc **ni inscriptions ni chevauchement déclaré** (son
  effectif est saisi), et la salle de la classe compte ces étudiants en trop.
  Évolution possible : une colonne `Besoin.epreuve_principale_id` (le besoin
  tiers-temps désigne l'épreuve dont il est le double) qui imposerait le même
  début et lèverait le conflit de groupes entre les deux seules séances.
- **Fermetures et groupes transverses.** Une fermeture de portée « groupe »
  ne vaut que pour ses descendants : les partiels, le stage et la semaine
  d'intégration reçoivent donc une fermeture par groupe transverse et
  tiers-temps de la promotion. Évolution possible : une colonne
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
- **Apprentis** : semaine type « école » seulement ; le calendrier
  d'alternance avec l'entreprise n'est pas modélisé.
- Le témoin est glouton : il prouve la faisabilité, pas la qualité. Ses
  indicateurs (trous, changements de campus) sont un plancher que le
  solveur doit améliorer. Malgré les relances, une graine peut encore
  laisser un besoin écarté (expliqué dans le rapport) ; aucune n'a été
  trouvée sur les graines essayées.
