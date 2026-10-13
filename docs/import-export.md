# Import et export

Ce document décrit le sous-système d'échange de données par fichiers :
le **format d'échange** des données de l'établissement, l'**import d'un
emploi du temps existant** et les **exports**. Les décisions qui le fondent
sont dans [`decisions.md`](decisions.md) (sections 3, 10 et 12) ; le modèle
de données est dans [`modele-donnees.md`](modele-donnees.md).

L'école n'a fourni aucun fichier : le format est **le nôtre**. Tout format
extérieur (export d'un autre logiciel) sera lu par un **adaptateur isolé**
qui produit les mêmes structures intermédiaires (voir « Écrire un
adaptateur »).

## Trois accès, les mêmes fonctions

| Accès | Où | Ce qu'il fait |
|---|---|---|
| Service | `edt.services.echange` | fonctions typées appelées par le web et la ligne de commande |
| Ligne de commande | `edt.commandes.echange` | `gabarit`, `importer`, `exporter-donnees`, `exporter-edt` |
| Web | `edt.web.routes.echange` | `/import` (simulation puis application), `/export` |

```python
# edt.services.echange
def produire_gabarit(session, format="xlsx") -> FichierProduit
def exporter_donnees(session, format="xlsx") -> FichierProduit
def lire_options(mode, semestre_id=None, semaine_type=None, tout_remplacer=False,
                 confirmer_remplacement=False) -> OptionsImport
def simuler_import(session, fichiers: Sequence[FichierEnvoye], options: OptionsImport) -> RapportImport
def appliquer_import(session, fichiers: Sequence[FichierEnvoye], options: OptionsImport) -> RapportImport
def compter_remplacement(session) -> dict[str, int]
def rapport_telechargeable(rapport: RapportImport) -> FichierProduit
def exporter_emploi_du_temps(session, version_id, format="csv", couche="hebdo", *,
                             groupe_id=None, enseignant_id=None, salle_id=None) -> FichierProduit
def resoudre_filtre(session, version_id, *, groupe=None, enseignant=None, salle=None) -> dict
def choix_export(session) -> ChoixExport
def semestres_importables(session) -> list[tuple[int, str]]
def verifier_taille(tailles: Sequence[int]) -> None          # 20 Mo au plus par envoi
```

Les services ne valident jamais la transaction (l'appelant le fait) et
renvoient des objets sérialisables (`vers_dict()`). Un import est refusé
pendant qu'un calcul tourne (`ErreurCalculEnCours`).

Ligne de commande :

```sh
python -m edt gabarit --format xlsx                 # ou zip : un CSV par feuille
python -m edt exporter-donnees --format zip --sortie sauvegardes/
python -m edt importer donnees.xlsx --simuler       # bilan et rapport, rien n'est écrit
python -m edt importer salles.csv enseignants.csv --rapport rapport.csv
python -m edt importer donnees.xlsx --tout-remplacer          # demande confirmation (--oui)
python -m edt importer edt.ics --mode reference --semestre 1 --semaine-type 12/10/2026
python -m edt exporter-edt --version 3 --format ics --couche date --groupe A1-G1
```

`importer` sort avec le code 1 s'il y a des rejets (les lignes valides
sont tout de même entrées, sauf avec `--simuler`).

Web :

- `GET /import` : dépôt d'un ou plusieurs fichiers, choix du mode, puis
  « Simuler » (fragment HTMX : bilan, semaine type, les 200 premiers
  signalements avec leur total, aperçu des 20 premières lignes de chaque
  tableau) et « Appliquer l'import » (HTMX aussi : le navigateur renvoie les
  mêmes fichiers, rien n'est conservé sur le serveur). 20 Mo au plus par envoi.
- `POST /import/rapport.csv` : le rapport complet en CSV, recalculé par une
  simulation sur les fichiers du formulaire (le bouton « Télécharger le
  rapport » le renvoie). Il n'est jamais encodé dans la page. Après une
  application, il liste les lignes qui ne sont pas entrées.
- L'import, le calcul de la page et son rendu s'exécutent dans le pool de
  fils du serveur : un gros fichier ne gèle pas les autres requêtes.
- `GET /export` : gabarit vide, données, emploi du temps d'une version (la
  version active du semestre en cours est présélectionnée).
- `GET /export/gabarit.xlsx`, `/export/gabarit.zip`, `/export/donnees.xlsx`,
  `/export/donnees.zip`.
- `GET /export/emploi-du-temps.csv` et `GET /export/emploi-du-temps.ics` :
  `version_id` (obligatoire), `couche` (`hebdo` ou `date`, défaut `hebdo`),
  au plus un de `groupe_id`, `enseignant_id`, `salle_id`.

## Le format d'échange

Une **description déclarative unique** (`edt.echange.format`) donne pour
chaque feuille ses colonnes : nom de l'en-tête, libellé, type, caractère
obligatoire, aide, exemple, appartenance à l'**identifiant métier**. Le
gabarit vide, l'export des données, la validation à l'import et les
tableaux ci-dessous en découlent (un test vérifie que ce document est à jour).

Deux présentations équivalentes :

- un **classeur XLSX** : un onglet « Lisez-moi » en tête (règles, codes des
  listes configurables, description de chaque colonne), puis un onglet par
  feuille ; en-têtes obligatoires en gras, aide en commentaire ;
- **un fichier CSV par feuille** (`salles.csv`...), réunis dans une archive
  ZIP pour le téléchargement, avec `LISEZ-MOI.txt`. Les CSV produits sont en
  UTF-8 avec BOM, séparateur point-virgule.

Dans tous les fichiers produits (CSV et XLSX : données, emploi du temps,
rapport), un texte qui commence par `=`, `+`, `-`, `@`, une tabulation ou un
retour chariot reçoit une espace insécable en tête : un tableur ne le prend
pas pour une formule. La relecture retire cette espace, le texte revient
donc à l'identique ; un nombre (« -1 ») n'est pas modifié.

Un import accepte des CSV, des classeurs, des archives ZIP de CSV, et
plusieurs fichiers à la fois. Une feuille est reconnue par le nom de son
onglet ou de son fichier (sans casse ni accents, éventuellement suivi d'un
complément : `salles_ndc.csv`), à défaut par ses en-têtes.

### Règles de lecture

- **Encodage** : BOM UTF-8 ou UTF-16 reconnu ; sinon UTF-8 ; sinon repli
  Windows-1252 avec un avertissement dans le rapport.
- **Séparateur** : point-virgule, virgule ou tabulation, détecté sur la
  ligne d'en-tête (point-virgule à égalité).
- **En-têtes** : reconnus par leur nom ou leur libellé, sans casse ni
  accents (« Capacité » vaut `capacite`), dans n'importe quel ordre.
- **Dates** : `05/10/2026`, `5/10/2026`, `05-10-2026`, ISO `2026-10-05`, ou
  une date de classeur. **Heures** : `8h30`, `8 h 30`, `08:30`, `18 h`.
  **Durées** : `2 h`, `1 h 30`, `90 min`, `01:30` (un nombre seul est refusé :
  l'unité serait devinée). **Oui/non** : oui/non, vrai/faux, 1/0. **Jours** :
  lundi ... dimanche ou leurs trois premières lettres.
- **Codes** : espaces de début et de fin retirés, espaces multiples
  réduits ; deux codes sont comparés sans tenir compte de la casse.
- **Listes** (groupes, enseignants, types de salle, équipements) : dans une
  cellule, séparées par `;` ou `,`.
- **Listes configurables** (types de salle, équipements, types de séance)
  : référencées par leur code ; les codes valides sont listés dans l'onglet
  « Lisez-moi ».
- **Salle** : désignée par son code, ou par `code@campus` si le même code
  existe sur plusieurs campus.
- Une ligne entièrement vide est sautée. Une valeur sans en-tête est gardée
  sous le nom « colonne N » (colonnes non reconnues).
- **Aucune valeur n'est corrigée** : soit elle se lit sans ambiguïté, soit
  la ligne est rejetée avec un message.

### Import partiel, simulation, réimport

- **Import partiel** : chaque ligne valide entre ; chaque ligne invalide est
  listée avec fichier, feuille, numéro de ligne, colonne et message (toutes
  les valeurs fautives d'une ligne, pas seulement la première). Une colonne
  obligatoire absente rejette tout le tableau. Un import ne s'arrête jamais
  au premier défaut.
- **Simulation** : l'import complet s'exécute dans un point de sauvegarde
  annulé à la fin ; le bilan affiché (« 12 créé(s), 30 mis à jour, 2
  inchangé(s), 3 rejeté(s) » par feuille) est donc celui que donnera
  l'application, et rien n'est écrit.
- **Ordre de traitement** : celui des feuilles ci-dessous (une salle après
  son campus, un besoin après ses groupes...), quel que soit l'ordre des
  fichiers. Une référence inconnue (salle d'un campus absent) rejette la
  ligne avec une explication.
- **Réimport** : une ligne dont l'identifiant métier existe met à jour
  l'existant (« mis à jour », ou « inchangé » si rien ne change) ; aucun
  doublon n'est créé et rien n'est supprimé. Une colonne **absente** du
  fichier ne modifie pas la valeur existante ; une cellule **vide** d'une
  colonne présente remet la valeur par défaut. Une liste remplace la liste
  existante.
- **Plusieurs fichiers** : deux lignes de même identifiant métier sont
  fusionnées si elles sont identiques (information dans le rapport) ; sinon
  la première est retenue et les suivantes sont rejetées.
- **Tout remplacer** (confirmation explicite) : supprime d'abord les règles
  personnalisées, campus, trajets, salles, enseignants, groupes, étudiants,
  matières, besoins, indisponibilités, jours fermés, versions d'emploi du
  temps et calculs ;
  conserve années, semestres, grille horaire, listes configurables et
  paramètres. La simulation montre ce qui serait supprimé.
- **Provenance** : une ligne créée ou modifiée garde le nom du fichier et
  son numéro de ligne ; les colonnes non reconnues sont conservées dans le
  champ `extra` des tables qui l'ont (salles, enseignants, groupes, jours
  fermés, étudiants, matières, besoins, indisponibilités, séances
  hebdomadaires, occurrences). Une ligne sans colonne inconnue ne modifie
  pas `extra`. Provenance et `extra` ne sont jamais exportés.
- **Aller-retour** : exporter les données puis réimporter le fichier dans
  une base vide redonne les mêmes données (testé sur le jeu minimal).

### Rapport

Chaque signalement a une gravité : **erreur** (ligne, séance ou fichier qui
n'est pas entré), **avertissement** (entré, mais à vérifier : repli
d'encodage, colonnes non reconnues, enseignant inconnu remplacé),
**information** (trace d'un traitement normal : doublon fusionné, séance
annulée non comptée). Le rapport se télécharge en CSV. Il garde au plus
20 000 signalements détaillés ; au-delà, ils sont comptés (le bilan compte
toujours toutes les lignes) et une dernière ligne du rapport le dit.

### Bornes

Le site est ouvert sans connexion : un fichier ne doit pas pouvoir épuiser
la mémoire ou le processeur du serveur. Au-delà d'une borne, le fichier ou
le tableau concerné est **refusé en entier** avec un signalement d'erreur
qui dit quoi faire ; il n'est jamais tronqué en silence.

| Borne | Valeur | Où |
|---|---|---|
| Taille d'un envoi (tous fichiers) | 20 Mo | `services.echange.TAILLE_MAX_OCTETS` |
| Archive ZIP | 50 fichiers, 20 Mo une fois décompressée | `lecture.MAX_MEMBRES_ARCHIVE`, `TAILLE_MAX_DECOMPRESSEE` |
| Classeur XLSX (c'est aussi une archive) | 300 parties, 20 Mo une fois décompressé | `lecture.MAX_MEMBRES_CLASSEUR`, `TAILLE_MAX_DECOMPRESSEE` |
| Tableau (un CSV ou un onglet) | 50 000 lignes de données, 100 000 lignes lues (vides comprises), 500 colonnes | `lecture.MAX_LIGNES_TABLEAU`, `MAX_LIGNES_LUES`, `MAX_COLONNES` |
| Envoi (tous fichiers et onglets) | 100 000 lignes de données ou séances iCal | `lecture.MAX_LIGNES_ENVOI` |
| Fichier iCal | 50 000 événements ; 1 000 occurrences par règle de récurrence | `ical.MAX_EVENEMENTS`, `MAX_OCCURRENCES` |
| Emploi du temps importé | 200 000 séances une fois les lignes hebdomadaires développées | `reconstitution.MAX_SEANCES_DEVELOPPEES` |
| Rapport | 20 000 signalements détaillés | `rapport.MAX_SIGNALEMENTS` |
| Page de résultat | 200 signalements, 500 besoins, 20 aperçus de 40 colonnes | `web.routes.echange.LIMITE_*` |

Les tailles décompressées sont celles que déclare l'archive, vérifiées avant
toute décompression : à la lecture, un membre qui dépasse sa taille déclarée
lève une erreur au lieu de déborder. Un membre abîmé ou chiffré est signalé.
Un établissement réel reste loin de ces bornes : le classeur exporté du jeu
minimal fait 0,2 Mo décompressé, et un emploi du temps de 800 séances par
semaine sur 30 semaines compte 24 000 lignes.

### La feuille `regles` (règles personnalisées)

Les règles personnalisées (`docs/regles-personnalisees.md`) s'échangent dans
la dernière feuille, traitée après toutes les autres (ses cibles existent
alors). Une ligne = une règle d'une année scolaire.

- **Identifiant** : (`annee`, `code`). Un code vide à l'import est attribué
  (R001, R002...) : une telle ligne crée toujours une nouvelle règle, il faut
  donc donner le code pour réimporter sans doublon (l'export l'écrit
  toujours). Réimport : mise à jour de la règle de même année et de même
  code ; **un code garde son type** (changer de type est refusé : supprimer
  la règle ou prendre un autre code).
- **Cible** : par son code, comme dans les autres feuilles (code du groupe
  dans l'année, identifiant de l'enseignant, code du campus, du type de
  séance ou de la matière), sans tenir compte de la casse ; un besoin
  s'écrit « matière / type / groupes » (« IM.1101 / CM / A1 »), sans
  tenir compte de la casse, cherché dans le semestre de la colonne
  `semestre` (indispensable si le même enseignement existe dans les deux
  semestres ; vide, la règle prend le semestre du besoin) ; vide pour
  l'établissement (une cible donnée
  pour l'établissement est refusée, pas ignorée).
- **Paramètres** : une seule cellule « clé = valeur » séparées par des
  points-virgules, listes séparées par des virgules, aux formats de lecture
  habituels : `mode = libre ; debut = 11h45 ; fin = 14h15 ; duree = 1 h 15`.
  Clés et valeurs selon le type (tableau ci-dessous et onglet Lisez-moi) ;
  une clé absente prend son défaut ; une liste vide s'écrit `jours =`. Ce
  choix plutôt qu'une colonne par paramètre : les quatorze types ont des
  schémas différents (une colonne `mode` ou `maximum` changerait de sens
  d'un type à l'autre) ; une cellule par règle reste lisible et suit le
  schéma du type. Dans un CSV écrit à la main, la cellule qui contient des
  points-virgules se met **entre guillemets** (un tableur et notre export le
  font d'eux-mêmes).
- **Niveau** et **état** : vides = niveau par défaut du type, règle active.
  **Période** : `semestre` (code) ou `date_debut` / `date_fin`, pas les deux ;
  rien = toute l'année. Une pause déjeuner ou une pause minimale ne se date
  pas (ces réglages valent l'année ou un semestre entier) : des dates sur une
  telle ligne la font refuser, l'erreur portant sur la colonne `date_debut`.
- **Validation** : celle du service des règles (`edt.services.regles`,
  bornes, portées admises, codes cités, doublons de réglage). Chacune de ses
  erreurs devient un signalement de la ligne, sur la colonne concernée
  (`parametres` avec le nom de la clé, `cible`, `portee`, `semestre`...) ;
  la ligne n'est pas écrite, les autres entrent.
- **Origine et provenance** : une règle créée par import a l'origine
  « import », le nom du fichier et le numéro de ligne ; une règle mise à
  jour garde son origine. Les colonnes `origine` et `phrase` (phrase
  générée) sont écrites par l'export pour information et ignorées à
  l'import.
- **Export** : les règles de toutes les années, tous états confondus.
  Exporter une année puis l'importer dans la suivante (en changeant la
  colonne `annee`) est une autre voie de reprise.
- **Modes d'import** : les règles ne s'importent qu'avec le mode « données
  de l'établissement ». Les trois modes d'un emploi du temps existant
  (référence, verrous, à replacer) créent des besoins et des versions, jamais
  de règle ; « tout remplacer » vide les règles avant d'importer.

### Feuilles

<!-- début du format généré : ne pas modifier à la main, voir edt.echange.format.documentation_markdown() -->

#### 1. `annees` : Années

Années scolaires. Une année créée par import ne reçoit ni semestre ni jour férié automatiquement : les décrire dans les feuilles semestres et jours_fermes.

Identifiant métier : `libelle`.

| Colonne | Libellé | Type | Obligatoire | Identifiant | Aide | Exemple |
|---|---|---|---|---|---|---|
| `libelle` | Libellé | code | oui | oui |  | 2026-2027 |
| `date_debut` | Début | date JJ/MM/AAAA | oui |  |  | 01/09/2026 |
| `date_fin` | Fin | date JJ/MM/AAAA | oui |  |  | 31/08/2027 |

#### 2. `semestres` : Semestres

Semestres d'une année : l'unité de génération d'un emploi du temps.

Identifiant métier : `annee`, `code`.

| Colonne | Libellé | Type | Obligatoire | Identifiant | Aide | Exemple |
|---|---|---|---|---|---|---|
| `annee` | Année scolaire | code | oui | oui | Libellé de l'année scolaire (feuille annees). | 2026-2027 |
| `code` | Code | code | oui | oui |  | S1 |
| `libelle` | Libellé | texte | oui |  |  | Semestre 1 |
| `date_debut` | Premier jour de cours | date JJ/MM/AAAA | oui |  |  | 07/09/2026 |
| `date_fin` | Dernier jour de cours | date JJ/MM/AAAA | oui |  |  | 29/01/2027 |

#### 3. `campus` : Campus

Sites d'enseignement (campus de l'école ou établissement voisin).

Identifiant métier : `code`.

| Colonne | Libellé | Type | Obligatoire | Identifiant | Aide | Exemple |
|---|---|---|---|---|---|---|
| `code` | Code | code | oui | oui |  | NDC |
| `nom` | Nom | texte | oui |  |  | Notre-Dame-des-Champs |
| `prefixe_salle` | Préfixe des salles | code |  |  | Préfixe des codes de salle du site. | N |

#### 4. `trajets` : Trajets

Temps de trajet entre deux campus (symétrique ; sans ligne, le paramètre « trajet par défaut » s'applique).

Identifiant métier : `campus_a`, `campus_b`.

| Colonne | Libellé | Type | Obligatoire | Identifiant | Aide | Exemple |
|---|---|---|---|---|---|---|
| `campus_a` | Campus A | code | oui | oui |  | NDC |
| `campus_b` | Campus B | code | oui | oui |  | ISSY |
| `duree` | Durée du trajet | durée (2 h, 1 h 30, 90 min) | oui |  |  | 1 h |

#### 5. `salles` : Salles

Salles de cours. Un code de salle est unique sur son campus.

Identifiant métier : `code`, `campus`.

| Colonne | Libellé | Type | Obligatoire | Identifiant | Aide | Exemple |
|---|---|---|---|---|---|---|
| `code` | Code | code | oui | oui |  | N204 |
| `campus` | Campus | code | oui | oui |  | NDC |
| `capacite` | Capacité | nombre entier | oui |  |  | 40 |
| `type` | Type de salle | code | oui |  | Code d'un type de salle (liste configurable). | COURS |
| `batiment` | Bâtiment | texte |  |  |  | Principal |
| `etage` | Étage | nombre entier |  |  | 0 = rez-de-chaussée. | 2 |
| `equipements` | Équipements | liste de codes séparés par ; ou , |  |  | Codes d'équipements (liste configurable). | VIDEOPROJ; VISIO |
| `accessible` | Accessible PMR | une valeur de la liste |  |  | Valeurs : oui, non, inconnu. Vide : inconnu. | oui |

#### 6. `enseignants` : Enseignants

Enseignants, identifiés par un identifiant stable (matricule).

Identifiant métier : `identifiant`.

| Colonne | Libellé | Type | Obligatoire | Identifiant | Aide | Exemple |
|---|---|---|---|---|---|---|
| `identifiant` | Identifiant | code | oui | oui |  | ENS001 |
| `civilite` | Civilité | texte |  |  |  | Mme |
| `nom` | Nom | texte | oui |  |  | Martin |
| `prenom` | Prénom | texte |  |  |  | Claire |
| `statut` | Statut | une valeur de la liste |  |  | Valeurs : permanent, vacataire, exterieur. Vide : permanent. | vacataire |
| `max_par_jour` | Maximum par jour | durée (2 h, 1 h 30, 90 min) |  |  |  | 6 h |
| `max_par_semaine` | Maximum par semaine | durée (2 h, 1 h 30, 90 min) |  |  |  | 18 h |
| `fictif` | Fictif | oui / non |  |  | Enseignant de remplacement (inconnu à l'import) : exclu des contraintes. Vide : non. | non |

#### 7. `groupes` : Groupes

Groupes d'étudiants : promotion > classe > demi-groupe, et groupes à part (langues, options, tiers-temps). Un code de groupe est unique dans son année.

Identifiant métier : `annee`, `code`.

| Colonne | Libellé | Type | Obligatoire | Identifiant | Aide | Exemple |
|---|---|---|---|---|---|---|
| `annee` | Année scolaire | code | oui | oui | Libellé de l'année scolaire (feuille annees). | 2026-2027 |
| `code` | Code | code | oui | oui |  | A1-G1 |
| `libelle` | Libellé | texte | oui |  |  | 1re année, classe 1 |
| `nature` | Nature | une valeur de la liste | oui |  | Valeurs : promotion, classe, demi_groupe, transverse, tiers_temps. | classe |
| `parent` | Groupe parent | code |  |  | Code du groupe qui le contient (vide pour une promotion ou un groupe à part). | A1 |
| `effectif` | Effectif | nombre entier | oui |  |  | 28 |
| `accessibilite_requise` | Salle accessible requise | oui / non |  |  | Vide : non. | non |
| `chevauche` | Chevauche | liste de codes séparés par ; ou , |  |  | Groupes qui partagent des étudiants avec celui-ci (déclaration symétrique : une seule des deux lignes suffit). | A1-ANG-B2 |

#### 8. `jours_fermes` : Jours fermés

Fermetures du calendrier (fériés, vacances, examens, semaines spéciales), globales ou propres à un groupe et à ses sous-groupes.

Identifiant métier : `annee`, `date_debut`, `date_fin`, `groupe`.

| Colonne | Libellé | Type | Obligatoire | Identifiant | Aide | Exemple |
|---|---|---|---|---|---|---|
| `annee` | Année scolaire | code | oui | oui | Libellé de l'année scolaire (feuille annees). | 2026-2027 |
| `date_debut` | Premier jour | date JJ/MM/AAAA | oui | oui |  | 26/10/2026 |
| `date_fin` | Dernier jour | date JJ/MM/AAAA |  | oui | Inclus. Vide : même valeur que date_debut. | 01/11/2026 |
| `libelle` | Libellé | texte | oui |  |  | Vacances de la Toussaint |
| `nature` | Nature | une valeur de la liste | oui |  | Valeurs : ferie, vacances, examens, semaine_speciale, autre. | vacances |
| `groupe` | Groupe concerné | code |  | oui | Vide = fermeture globale. | A1 |

#### 9. `etudiants` : Étudiants (facultative)

Étudiants (facultatif, fictifs ou pseudonymisés) : ils servent à détecter les groupes qui partagent des étudiants. Aucune donnée de santé.

Identifiant métier : `identifiant`.

| Colonne | Libellé | Type | Obligatoire | Identifiant | Aide | Exemple |
|---|---|---|---|---|---|---|
| `identifiant` | Identifiant | code | oui | oui |  | ETU001 |
| `nom` | Nom | texte | oui |  |  | Petit |
| `prenom` | Prénom | texte |  |  |  | Camille |

#### 10. `inscriptions` : Inscriptions (facultative)

Inscription d'un étudiant dans un groupe (facultatif), une ligne par inscription.

Identifiant métier : `annee`, `etudiant`, `groupe`.

| Colonne | Libellé | Type | Obligatoire | Identifiant | Aide | Exemple |
|---|---|---|---|---|---|---|
| `annee` | Année scolaire | code | oui | oui | Libellé de l'année scolaire (feuille annees). | 2026-2027 |
| `etudiant` | Étudiant | code | oui | oui |  | ETU001 |
| `groupe` | Groupe | code | oui | oui |  | A1-G1a |

#### 11. `matieres` : Matières

Matières, identifiées par leur code module et leur promotion de rattachement.

Identifiant métier : `code`, `annee`, `promotion`.

| Colonne | Libellé | Type | Obligatoire | Identifiant | Aide | Exemple |
|---|---|---|---|---|---|---|
| `code` | Code module | code | oui | oui |  | IM.1101 |
| `libelle` | Libellé | texte | oui |  |  | Mathématiques |
| `annee` | Année scolaire | code |  | oui | Obligatoire si la promotion est renseignée. | 2026-2027 |
| `promotion` | Promotion | code |  | oui | Code du groupe de la promotion de rattachement. | A1 |

#### 12. `besoins` : Besoins

La demande d'enseignement : ce que le générateur doit placer. Un besoin est identifié par son semestre, sa matière, son type, ses groupes, son libellé et sa date ponctuelle.

Identifiant métier : `annee`, `semestre`, `matiere`, `promotion`, `type`, `libelle`, `groupes`, `date`.

| Colonne | Libellé | Type | Obligatoire | Identifiant | Aide | Exemple |
|---|---|---|---|---|---|---|
| `annee` | Année scolaire | code | oui | oui | Libellé de l'année scolaire (feuille annees). | 2026-2027 |
| `semestre` | Semestre | code | oui | oui |  | S1 |
| `matiere` | Matière | code | oui | oui |  | IM.1101 |
| `promotion` | Promotion de la matière | code |  | oui | Distingue deux matières de même code ; vide = la matière sans promotion, ou la seule qui porte ce code. | A1 |
| `type` | Type de séance | code | oui | oui | Code d'un type de séance (liste configurable). | TD |
| `libelle` | Libellé | texte |  | oui | Précision facultative ; distingue deux besoins par ailleurs identiques. | Partiel |
| `groupes` | Groupes | liste de codes séparés par ; ou , | oui | oui |  | A1-G1 |
| `enseignants` | Enseignants | liste de codes séparés par ; ou , |  |  | Identifiants. | ENS001 |
| `duree` | Durée d'une séance | durée (2 h, 1 h 30, 90 min) | oui |  |  | 2 h |
| `mode` | Mode de volume | une valeur de la liste |  |  | par_semaine : nombre de séances chaque semaine ; total : nombre de séances réparties dans la période ; ponctuel : une séance datée (colonne date). Valeurs : par_semaine, total, ponctuel. Vide : par_semaine. | par_semaine |
| `nombre` | Nombre de séances | nombre entier |  |  | Vide : 1. | 1 |
| `max_par_semaine` | Maximum par semaine | nombre entier |  |  | Mode total. Vide : 1. | 1 |
| `date_debut` | Début de la période | date JJ/MM/AAAA |  |  | Vide = début du semestre. |  |
| `date_fin` | Fin de la période | date JJ/MM/AAAA |  |  | Vide = fin du semestre. |  |
| `date` | Date ponctuelle | date JJ/MM/AAAA |  | oui | Mode ponctuel seulement. | 19/11/2026 |
| `jour_impose` | Jour imposé | jour (lundi ... dimanche) |  |  |  | jeudi |
| `debut_impose` | Heure de début imposée | heure (08:30, 8h30) |  |  |  | 13:45 |
| `salle_imposee` | Salle imposée | code |  |  | Code de la salle ; « code@campus » si le code existe sur plusieurs campus. | N016 |
| `campus_impose` | Campus imposé | code |  |  |  | NDC |
| `types_salle` | Types de salle acceptés | liste de codes séparés par ; ou , |  |  | Vide = le type par défaut du type de séance. | INFO |
| `equipements` | Équipements requis | liste de codes séparés par ; ou , |  |  |  | ORDINATEURS |
| `commentaire` | Commentaire | texte |  |  |  |  |

#### 13. `indisponibilites` : Indisponibilités

Indisponibilités d'un enseignant, d'une salle ou d'un groupe : récurrentes (colonne jour) ou datées (colonnes date_debut et date_fin).

Identifiant métier : `cible`, `ressource`, `annee`, `jour`, `date_debut`, `date_fin`, `debut`, `fin`.

| Colonne | Libellé | Type | Obligatoire | Identifiant | Aide | Exemple |
|---|---|---|---|---|---|---|
| `cible` | Cible | une valeur de la liste | oui | oui | Valeurs : enseignant, salle, groupe. | enseignant |
| `ressource` | Ressource | code | oui | oui | Identifiant de l'enseignant, code de la salle (« code@campus » si ambigu) ou code du groupe. | ENS004 |
| `annee` | Année scolaire | code |  | oui | Obligatoire pour un groupe. | 2026-2027 |
| `jour` | Jour | jour (lundi ... dimanche) |  | oui | Forme récurrente. | lundi |
| `date_debut` | Premier jour | date JJ/MM/AAAA |  | oui | Forme datée. |  |
| `date_fin` | Dernier jour | date JJ/MM/AAAA |  | oui | Forme datée, inclus. Vide : même valeur que date_debut. |  |
| `debut` | Début | heure (08:30, 8h30) |  | oui | Vide : 00:00. | 13:45 |
| `fin` | Fin | heure (08:30, 8h30) |  | oui | Vide : 24:00. | 18:00 |
| `motif` | Motif | texte |  |  |  | Activité professionnelle |
| `dure` | Contrainte dure | oui / non |  |  | Toujours oui en V1. Vide : oui. |  |
| `poids` | Poids | nombre entier |  |  | Préférences (versions futures). Vide : 0. |  |

#### 14. `regles` : Règles

Règles personnalisées d'une année (docs/regles-personnalisees.md) : un type, une portée et sa cible, des paramètres, un niveau, un état et une période. Une règle créée par import a l'origine « import ». Réimport : mise à jour par année et code.

Identifiant métier : `annee`, `code`.

| Colonne | Libellé | Type | Obligatoire | Identifiant | Aide | Exemple |
|---|---|---|---|---|---|---|
| `annee` | Année scolaire | code | oui | oui | Libellé de l'année scolaire (feuille annees). | 2026-2027 |
| `code` | Code | code |  | oui | Code de la règle dans l'année ; vide = attribué à l'import (R001, R002...). Donner le code pour réimporter sans doublon ; un code garde son type. | R007 |
| `type` | Type de règle | une valeur de la liste | oui |  | Valeurs : JOURS_TRAVAILLES, PLAGE_HORAIRE, CRENEAUX_INTERDITS, CAMPUS, SALLES, PAUSE_DEJEUNER, PAUSE_MINIMALE, MAX_PAR_JOUR, JOURNEES_LIBRES, SANS_TROU, CAMPUS_PAR_JOUR, MATIERES_PAR_JOUR, ORDRE_SEMAINE, REPARTITION_MATIERE. | PAUSE_DEJEUNER |
| `portee` | Portée | une valeur de la liste | oui |  | Valeurs : etablissement, groupe, enseignant, campus, type_seance, matiere, besoin. | groupe |
| `cible` | Cible | code |  |  | Code du groupe, identifiant de l'enseignant, code du campus, du type de séance ou de la matière ; un besoin s'écrit « matière / type / groupes » (ex. « IM.1101 / CM / A1 ») ; vide pour l'établissement. | A2-G3 |
| `parametres` | Paramètres | texte |  |  | « clé = valeur » séparés par des points-virgules, listes séparées par des virgules ; clés et valeurs selon le type (onglet Lisez-moi) ; vide = défauts. | mode = libre ; debut = 11:45 ; fin = 14:15 ; duree = 1 h 15 |
| `niveau` | Niveau | une valeur de la liste |  |  | Vide = niveau par défaut du type. Valeurs : necessaire, important, facultatif. | important |
| `etat` | État | une valeur de la liste |  |  | Valeurs : proposee, active, inactive. Vide : active. | active |
| `semestre` | Semestre | code |  |  | Code du semestre ; semestre et dates vides = toute l'année. | S1 |
| `date_debut` | Début de la période | date JJ/MM/AAAA |  |  | Période datée, au lieu d'un semestre. |  |
| `date_fin` | Fin de la période | date JJ/MM/AAAA |  |  |  |  |
| `commentaire` | Commentaire | texte |  |  |  | Demande de la responsable |
| `origine` | Origine | texte |  |  | Saisie, import, assistant, base ou reprise. Export seulement : ignorée à l'import. |  |
| `phrase` | Phrase | texte |  |  | Phrase générée de la règle. Export seulement : ignorée à l'import. |  |

#### 15. `seances` : Séances

Emploi du temps existant, une ligne par séance : datée (colonne date) ou hebdomadaire (colonne jour, la séance a lieu chaque semaine ouverte du semestre).

Identifiant métier : aucun.

| Colonne | Libellé | Type | Obligatoire | Identifiant | Aide | Exemple |
|---|---|---|---|---|---|---|
| `date` | Date | date JJ/MM/AAAA |  |  | Date de la séance ; vide si la colonne jour est remplie. | 05/10/2026 |
| `jour` | Jour | jour (lundi ... dimanche) |  |  | Séance hebdomadaire, si la date est vide. | lundi |
| `debut` | Début | heure (08:30, 8h30) | oui |  |  | 08:30 |
| `fin` | Fin | heure (08:30, 8h30) | oui |  |  | 10:30 |
| `matiere` | Matière | code | oui |  | Code module. | IM.1101 |
| `type` | Type de séance | code | oui |  | Code d'un type de séance ; AUTRE pour un événement spécial (conférence, forum). | CM |
| `groupes` | Groupes | liste de codes séparés par ; ou , | oui |  |  | A1 |
| `enseignants` | Enseignants | liste de codes séparés par ; ou , |  |  | Identifiants ; vide ou « à recruter » = enseignant fictif de la matière. | ENS001 |
| `salle` | Salle | code |  |  | Code de la salle ; vide ou « à définir » = salle choisie par le générateur. | N016 |
| `statut` | Statut | une valeur de la liste |  |  | Vide = réalisée ; une séance annulée n'est pas comptée. Valeurs : realisee, planifiee, deplacee, annulee, a_programmer. | realisee |
| `libelle` | Libellé | texte |  |  | Intitulé libre (événement spécial). | Forum entreprises |

#### Types de règle et paramètres (colonnes `type` et `parametres` de `regles`)

| Type | Libellé | Portées admises | Paramètres | Niveau par défaut |
|---|---|---|---|---|
| `JOURS_TRAVAILLES` | Jours travaillés | groupe, enseignant, campus, type_seance, matiere, besoin | jours (liste de jours ; au moins 1 ; obligatoire) | necessaire |
| `PLAGE_HORAIRE` | Horaires | etablissement, groupe, enseignant, campus, type_seance, matiere, besoin | debut (heure ; défaut : paramètre global); fin (heure ; défaut : paramètre global); jours (liste de jours) | necessaire |
| `CRENEAUX_INTERDITS` | Créneaux interdits | etablissement, groupe, enseignant, campus, type_seance, matiere, besoin | jours (liste de jours ; au moins 1 ; obligatoire); debut (heure ; défaut : paramètre global); fin (heure ; défaut : paramètre global) | necessaire |
| `CAMPUS` | Campus imposés ou interdits | etablissement, groupe, enseignant, type_seance, matiere | mode (imposer / interdire ; obligatoire); campus (liste de codes ; au moins 1 ; obligatoire) | necessaire |
| `SALLES` | Salles imposées ou interdites | groupe, enseignant, type_seance, matiere | mode (imposer / interdire ; obligatoire); salles (liste de codes ; au moins 1 ; obligatoire) | necessaire |
| `PAUSE_DEJEUNER` | Pause déjeuner | groupe, enseignant | mode (fixe / libre / aucun ; défaut : paramètre global); debut (heure ; défaut : paramètre global ; si mode = fixe ou libre); fin (heure ; défaut : paramètre global ; si mode = fixe ou libre); duree (durée ; 15 à 180 min ; défaut : paramètre global ; si mode = libre) | important |
| `PAUSE_MINIMALE` | Pause entre deux séances | groupe, enseignant | duree (durée ; 0 à 120 min ; défaut : paramètre global) | important |
| `MAX_PAR_JOUR` | Maximum par jour | etablissement, groupe, enseignant | personnes (groupes / enseignants / tous ; défaut groupes); unite (minutes / seances ; défaut minutes); maximum (nombre ; 1 à 720 ; obligatoire) | important |
| `JOURNEES_LIBRES` | Demi-journées ou journées libres | etablissement, groupe, enseignant | personnes (groupes / enseignants / tous ; défaut groupes); unite (demi_journee / journee ; défaut demi_journee); nombre (nombre ; 1 à 6 ; défaut 1); parmi (liste de jours ou de demi-journées) | important |
| `SANS_TROU` | Trous limités | groupe, enseignant | maximum (nombre ; 0 à 10 ; défaut 0) | facultatif |
| `CAMPUS_PAR_JOUR` | Un seul campus par jour | etablissement, groupe, enseignant | personnes (groupes / enseignants / tous ; défaut groupes) | important |
| `MATIERES_PAR_JOUR` | Matières par jour | groupe | maximum (nombre ; 1 à 8 ; obligatoire) | facultatif |
| `ORDRE_SEMAINE` | Ordre dans la semaine | etablissement, groupe, matiere | avant (code ; défaut CM); apres (code ; défaut TD) | facultatif |
| `REPARTITION_MATIERE` | Regroupement ou étalement d'une matière | etablissement, groupe, matiere | mode (meme_jour / jours_differents ; obligatoire) | facultatif |

<!-- fin du format généré -->

## Import d'un emploi du temps existant

Entrée : le **tableau plat** de séances (feuille `seances` ci-dessus, en CSV
ou dans un classeur, un tableau par onglet), ou un fichier **iCal** (`.ics`).
Chaque lecteur produit le même tableau intermédiaire (`SeanceLue`), que la
reconstitution (`edt.echange.reconstitution`) transforme selon le mode.

### Trois modes

| Mode | Besoins | Version d'emploi du temps |
|---|---|---|
| `reference` | créés ou mis à jour | nouvelle version d'origine « référence », **inactive** (pour comparaison) : semaine type et séances datées |
| `verrous` | créés ou mis à jour | nouvelle version **active** dont les séances hebdomadaires sont **verrouillées** : le générateur les garde à leur place et place le reste |
| `a_replacer` | créés ou mis à jour | aucune : le générateur placera tout |

Un même fichier peut être réimporté dans un autre mode : les besoins sont
retrouvés par leur identifiant métier, sans doublon.

### Règles décidées par l'équipe

- Une séance au statut **annulé** n'est pas comptée (information dans le
  rapport) ; une séance **déplacée** compte à sa position finale (celle de
  la ligne) ; sans statut, tout est considéré **réalisé**.
- Enseignant absent, « à recruter », « à définir », « intervenant extérieur »
  ou identifiant **inconnu** (avertissement) : l'enseignant **fictif propre à
  la matière** (`FICTIF-<matière>-<promotion>`), créé au besoin et exclu des
  contraintes d'enseignant.
- Salle vide ou « **à définir** » : aucune salle, le générateur choisira.
- **Groupe inconnu** (de même matière, type ou salle inconnus) : la séance
  est mise de côté et listée, l'import continue.
- **Événement spécial** (type `AUTRE` : conférence, forum) : un besoin à
  part, **figé** (date, heure de début et salle imposées), jamais regroupé
  avec les cours. Le modèle exige un besoin pour toute séance : c'est un
  besoin ponctuel de type `AUTRE`.
- Lignes **strictement identiques** : fusionnées.
- Une occurrence issue d'une **règle de récurrence** (iCal) qui tombe un
  jour fermé du calendrier (férié, vacances) est retirée.
- Une séance qui ne respecte pas la grille ou les règles n'est **ni rejetée
  ni corrigée** : elle entre telle quelle et le vérificateur la signalera.
- Une séance hors des dates du semestre choisi est mise de côté.

### Des séances datées à la semaine type

1. **Regroupement** par (matière, type, groupes, enseignants, durée) : chaque
   groupe de séances devient un besoin.
2. **Volume** : une seule séance datée donne un besoin `ponctuel` à sa date ;
   le même nombre de séances chaque semaine ouverte du semestre donne
   `par_semaine` (une semaine sans la séance est tolérée si l'un de ses jours
   travaillés est fermé pour ses groupes) ; sinon `total` (nombre observé, maximum
   hebdomadaire observé, période du premier au dernier jour). Une ligne
   hebdomadaire (colonne `jour`) vaut une séance chaque semaine ouverte.
   Les semaines ouvertes sont celles du dépliage du semestre : une semaine
   est fermée quand tous les **jours travaillés de l'établissement**
   (paramètre `jours_travailles`) le sont. Les règles personnalisées
   n'interviennent pas : une séance importée qui enfreint une règle de jours
   entre telle quelle, et le vérificateur la signale.
3. **Semaine type** : pour chaque promotion (racine de l'arbre des groupes),
   la semaine dont la disposition (besoin, jour, heure, salle) revient le
   plus souvent ; à égalité, la plus ancienne. Chaque besoin y prend ses
   créneaux et complète avec ses créneaux les plus fréquents s'il lui en
   manque. Elle est **modifiable** : l'option `semaine_type` (une date de la
   semaine voulue) impose une autre semaine ; après l'import, besoins et
   séances se modifient dans les écrans de planification.
4. Deux besoins de même identifiant métier (même matière, type et groupes,
   mais enseignants ou durée différents) reçoivent des libellés distincts.

La simulation affiche les besoins reconstitués (volume, créneaux de la
semaine type, créé / mis à jour / inchangé) avant toute écriture.

### Lecteur iCal

- Événements simples et récurrents : `RRULE` (développée avec
  `dateutil.rrule` sur la période du semestre), `RDATE`, `EXDATE`,
  occurrences remplacées (`RECURRENCE-ID`, la séance passe « déplacée » si
  elle a bougé), `STATUS:CANCELLED` (annulée).
- Tout est converti en heure locale **Europe/Paris** et stocké sans fuseau.
  Une règle décrite dans un fuseau (`TZID=Europe/Paris`) garde son heure
  locale de part et d'autre du changement d'heure ; une heure UTC est
  convertie (06:30Z le 20/10 et 07:30Z le 27/10 donnent tous deux 8 h 30).
- Champs : propriétés `X-EDT-MATIERE`, `X-EDT-TYPE`, `X-EDT-GROUPES`,
  `X-EDT-ENSEIGNANTS`, `X-EDT-SALLE`, `X-EDT-STATUT`, `X-EDT-LIBELLE`
  (écrites par notre export) ; à défaut, type depuis `CATEGORIES`, salle
  depuis `LOCATION`, libellé depuis `SUMMARY`.
- Un événement sur la journée entière, sans heure de fin ou sur plusieurs
  jours n'est pas une séance : il est signalé et non importé.
- Une règle de récurrence qui donnerait plus de 1 000 occurrences (par
  exemple toutes les minutes) n'est pas un cours : elle est refusée.

## Exports

- **Données** : tout l'établissement dans le format d'échange, en XLSX ou en
  ZIP de CSV ; le fichier se réimporte tel quel.
- **Emploi du temps** d'une version, en CSV ou en iCal, pour tout
  l'établissement ou pour un groupe (avec les séances des groupes qui le
  contiennent et de ses sous-groupes), un enseignant ou une salle :
  - couche `hebdo` : une ligne par séance hebdomadaire (jour, heures,
    matière, groupes, enseignants, salle, semaines actives, verrou) ; en
    iCal, un événement récurrent hebdomadaire par séance, semaines inactives
    et jours fermés exclus (`EXDATE`), identifiant `seance-<besoin>-<rang>@edt` ;
  - couche `date` : une ligne par occurrence datée enregistrée (avec son
    statut) ; en iCal, un événement par occurrence, identifiant
    `occurrence-<id>@edt`.
  - Le CSV reprend les colonnes du tableau plat d'import (`date`, `jour`,
    `debut`, `fin`, `matiere`, `type`, `groupes`, `enseignants`, `salle`,
    `statut`, `libelle`) : il se réimporte comme emploi du temps existant.
  - L'iCal déclare le fuseau `Europe/Paris` (VTIMEZONE), porte un lieu, une
    description lisible et les propriétés `X-EDT-*` ; il se réimporte aussi.

## Écrire un adaptateur pour un autre logiciel

Un adaptateur lit le fichier d'un logiciel précis et produit le tableau
intermédiaire ; il ne touche ni à la base ni à la reconstitution.

1. Créer `src/edt/echange/<logiciel>.py` avec une fonction pure :

   ```python
   def lire_<logiciel>(nom: str, contenu: bytes) -> tuple[list[SeanceLue], list[Signalement]]:
   ```

   Chaque séance du fichier devient une `SeanceLue` (date ou jour, début et
   fin en minutes depuis minuit **heure locale Europe/Paris**, codes de
   matière, type, groupes, identifiants d'enseignants, salle, statut,
   libellé, colonnes inconnues dans `extra`) ; chaque élément illisible
   devient un `Signalement` de gravité « erreur » (fichier, ligne, message).
   Ne rien corriger : ce qui manque (matière, groupes) est mis de côté par la
   reconstitution avec un message.
2. Reconnaître le fichier dans `edt.services.echange._importer` (nature du
   fichier ou en-têtes) et appeler le lecteur à la place du tableau plat.
3. Ajouter un petit fichier d'exemple **fictif** dans `tests/echange/fichiers/`
   et un test par particularité du format (encodage, fuseaux, statuts).

Le lecteur iCal (`edt.echange.ical`) est le premier adaptateur et sert de
modèle.

## Limites connues

- Le besoin n'a pas d'identifiant métier en base : il est identifié par
  (semestre, matière, type, groupes, libellé, date ponctuelle). Deux besoins
  identiques sur ces points doivent recevoir des libellés distincts pour
  être réimportés ; l'import le signale.
- Campus, trajets, années, semestres et inscriptions n'ont pas de champs de
  provenance : leurs colonnes non reconnues ne sont pas conservées (le
  rapport le dit).
- Le rapport d'un import n'est pas conservé en base : il est affiché (200
  premiers signalements) et téléchargeable (CSV, recalculé par simulation
  sur les mêmes fichiers), et le journal garde le bilan.
- Les bornes (section « Bornes ») protègent la mémoire et le processeur,
  pas le débit : rien ne limite le nombre d'imports qu'un visiteur lance à
  la suite. Un import de 50 000 lignes valides prend une vingtaine de
  secondes et occupe la base en écriture pendant ce temps.
- Une année créée par import ne reçoit ni semestre ni jour férié
  automatiquement : les décrire dans les feuilles `semestres` et
  `jours_fermes` (l'export les contient).
- Une matière ambiguë (même code pour plusieurs promotions) se distingue par
  la colonne `promotion` dans les besoins ; dans un emploi du temps importé,
  par la promotion des groupes de la séance.
- La couche `date` n'exporte que les occurrences enregistrées : une version
  générée n'en a pas tant que le lot calendrier ne les instancie pas
  (utiliser la couche `hebdo`).
- Les fichiers `.xls` (Excel 97-2003) ne sont pas lus : les enregistrer en
  `.xlsx` ou `.csv`. Pas d'assistant de correspondance de colonnes : le
  format est fixe et décrit dans le gabarit.
