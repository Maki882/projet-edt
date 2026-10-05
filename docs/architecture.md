# Architecture

Ce document décrit le socle de l'application : les couches, l'arborescence,
les deux mécanismes d'extension, les contrats partagés entre les lots, les
conventions de journalisation et d'erreurs. Les décisions qui le fondent sont
dans [`decisions.md`](decisions.md) ; le modèle de données est dans
[`modele-donnees.md`](modele-donnees.md).

## Couches

```
web (routes, gabarits)  ─┐
cli (commandes)         ─┼─▶ services ─▶ domaine / solveur / verification
futur LLM / API         ─┘      │
                                └────▶ db (ORM, SQLite)
```

| Couche | Paquet | Rôle | Peut importer |
|---|---|---|---|
| Interfaces | `edt.web`, `edt.cli`, `edt.commandes` | routes, gabarits, commandes : aucune logique métier | `services`, `domaine` |
| Services | `edt.services` | fonctions typées, une par fonctionnalité | `domaine`, `solveur`, `verification`, `db` |
| Logique pure | `edt.domaine`, `edt.solveur`, `edt.verification` | calculs sans base ni réseau | `domaine` seulement |
| Données | `edt.db` | moteur, sessions, modèles ORM, données par défaut | `domaine` |
| Transverse | `edt.config`, `edt.journal`, `edt.erreurs` | configuration, journaux, erreurs | rien d'autre |

Règles qui en découlent :

- une fonctionnalité existe **d'abord comme fonction de service** (paramètres
  et retours explicites), puis comme route web **et** comme commande ;
- le solveur et le vérificateur **ne voient jamais l'ORM** : ils travaillent
  sur l'instantané (voir « Contrats partagés ») ;
- aucun état global en mémoire, sauf le suivi des calculs en cours
  (`edt.services.generation`) : tout le reste est en base.

## Arborescence

```
pyproject.toml                 paquet, dépendances, pytest, ruff
.github/workflows/ci.yml       ruff + pytest sur chaque PR
docs/                          décisions, architecture, modèle de données
src/edt/
  __init__.py                  version
  __main__.py                  python -m edt
  cli.py                       application Typer, découverte des commandes
  config.py                    variables d'environnement EDT_*
  journal.py                   configuration unique des journaux
  erreurs.py                   hiérarchie ErreurEDT
  commandes/                   un module par lot (objet `commandes`)
    base.py                    init-db, jeu-minimal, serve, version, doctor
  db/
    base.py                    base déclarative, nommage des contraintes
    moteur.py                  moteur SQLite (clés étrangères, WAL), sessions
    modeles.py                 toutes les tables
    initialisation.py          schéma, données par défaut, année scolaire
    jeux.py                    jeu de données minimal
  domaine/
    enumerations.py            valeurs fermées et leurs libellés
    heures.py                  minutes depuis minuit <-> « 8 h 30 »
    calendrier.py              semaines d'un semestre, jours fériés
    contraintes.py             catalogue des contraintes (codes stables)
    parametres.py              paramètres globaux typés
    instantane.py              CONTRAT : semestre à planifier, placements
    depliage.py                besoins -> séances hebdomadaires
  services/
    instantane.py              base -> instantané
    conflits_groupes.py        relation de conflit entre groupes
    generation.py              orchestration d'un calcul
    parametres.py              lecture et écriture des paramètres
    calendrier.py              années scolaires, semestre par défaut
    etat.py                    diagnostic de la base, compteurs
  verification/
    contrat.py                 CONTRAT : Anomalie, verifier (provisoire)
  solveur/
    contrat.py                 CONTRAT : OptionsSolveur, ResultatSolveur
    provisoire.py              moteur glouton PROVISOIRE
  web/
    app.py                     creer_app(), découverte des routes
    asgi.py                    app = creer_app()  (uvicorn edt.web.asgi:app)
    dependances.py             SessionRequete
    gabarits.py                rendre(), rediriger(), filtres Jinja
    intergiciels.py            identifiant de requête, journal des requêtes
    erreurs.py                 erreurs -> pages lisibles
    menu.py                    EntreeMenu, sections du menu
    routes/                    un module par écran (objets `routeur`, `MENU`)
      accueil.py               / et /sante
    templates/                 base.html, macros.html, un dossier par écran
    static/                    css/app.css, js/htmx.min.js, js/app.js
tests/
  conftest.py                  fixtures communes (base temporaire, jeu minimal)
  socle/                       tests du socle ; un dossier par lot ensuite
verifier_conflits.py           premier vérificateur de l'équipe (conservé)
```

## Configuration

Tout se règle par variables d'environnement, avec des valeurs par défaut
utilisables telles quelles (`edt.config.charger_configuration()`).

| Variable | Défaut | Rôle |
|---|---|---|
| `EDT_DB_URL` | `sqlite:///data/edt.db` | URL SQLAlchemy de la base |
| `EDT_LOG_DIR` | `logs` | dossier des journaux |
| `EDT_HOST` | `127.0.0.1` | adresse d'écoute |
| `EDT_PORT` | `8000` | port d'écoute |
| `EDT_ENV` | `dev` | `dev` ou `prod` (en `prod`, `/docs` est désactivé) |
| `EDT_LOG_LEVEL` | `INFO` | niveau de journalisation |

Une valeur invalide lève `ErreurConfiguration` : jamais de repli silencieux.
Les chemins relatifs se résolvent depuis le répertoire de lancement.

## Mécanismes d'extension

Chaque lot ajoute ses commandes et ses écrans **dans ses propres fichiers**.
Ni `edt/cli.py` ni `edt/web/app.py` ne se modifient : ils découvrent les
modules au démarrage. Un module dont le nom commence par `_` est ignoré
(code partagé). Un module qui n'expose pas l'objet attendu fait échouer le
démarrage avec un message explicite.

### Ajouter une commande

Créer un module dans `src/edt/commandes/` qui expose `commandes`, un
`typer.Typer`. Ses commandes s'ajoutent à `python -m edt`.

```python
# src/edt/commandes/salles.py
"""Commandes du lot « référentiels » : salles."""

from typing import Annotated

import typer

from edt.db.moteur import session_base
from edt.services import salles as service_salles   # le service du lot

commandes = typer.Typer()


@commandes.command("lister-salles")
def lister_salles(
    campus: Annotated[str | None, typer.Option(help="Code du campus.")] = None,
) -> None:
    """Affiche les salles (cette phrase devient l'aide de la commande)."""
    with session_base() as session:        # validée à la sortie, annulée sur erreur
        for salle in service_salles.lister(session, campus=campus):
            typer.echo(f"{salle.code}  {salle.capacite} places")
```

- La commande appelle un service et affiche le résultat ; elle ne contient
  pas de logique métier.
- Elle laisse remonter les `ErreurEDT` : le message est affiché en clair et
  le code de sortie vaut 1. Toute autre exception est journalisée avec sa
  trace et donne le code 2.
- La journalisation est déjà configurée quand la commande démarre.
- Pour regrouper des sous-commandes (`python -m edt donnees generer`),
  rattacher un `typer.Typer` nommé : `commandes.add_typer(groupe, name="donnees")`.

### Ajouter un écran

Créer un module dans `src/edt/web/routes/` qui expose `routeur` (un
`fastapi.APIRouter`, obligatoire) et `MENU` (une liste d'`EntreeMenu`,
facultative), et ses gabarits dans `src/edt/web/templates/<écran>/`.

```python
# src/edt/web/routes/salles.py
"""Écrans du lot « référentiels » : salles."""

from typing import Annotated

from fastapi import APIRouter, Form, Request

from edt.services import salles as service_salles
from edt.web.dependances import SessionRequete
from edt.web.gabarits import rediriger, rendre
from edt.web.menu import SECTION_REFERENTIELS, EntreeMenu

routeur = APIRouter(prefix="/salles")
MENU = [EntreeMenu("Salles", "/salles", section=SECTION_REFERENTIELS, ordre=120)]


@routeur.get("")
def liste(request: Request, session: SessionRequete):
    return rendre(request, "salles/liste.html", {"salles": service_salles.lister(session)})


@routeur.post("")
def creer(
    request: Request,
    session: SessionRequete,
    code: Annotated[str, Form()],
    capacite: Annotated[int, Form()],
):
    service_salles.creer(session, code=code, capacite=capacite)   # lève ErreurEDT si refus
    return rediriger(request, "/salles", f"Salle {code} enregistrée.")
```

```html
{# src/edt/web/templates/salles/liste.html #}
{% extends "base.html" %}
{% import "macros.html" as ui %}

{% block titre %}Salles{% endblock %}

{% block contenu %}
{% call(salle) ui.tableau([("code", "Code"), ("capacite", "Places")], salles, vide="Aucune salle.") %}
  {{ ui.bouton_suppression("/salles/" ~ salle.id ~ "/supprimer") }}
{% endcall %}

{% call ui.formulaire("/salles", libelle_bouton="Ajouter la salle") %}
  {{ ui.champ("code", "Code", requis=true) }}
  {{ ui.champ("capacite", "Places", type="number", requis=true, attributs={"min": 0}) }}
{% endcall %}
{% endblock %}
```

À savoir :

- **Session** : le type `SessionRequete` fournit une session validée
  automatiquement à la fin de la route, *avant* l'envoi de la réponse, et
  annulée si la route lève une erreur. Une route n'appelle jamais
  `session.commit()` ; un service non plus (il fait `session.flush()` s'il a
  besoin d'un identifiant).
- **Rendu** : `rendre(request, gabarit, contexte)` ajoute le menu, les
  messages et l'identifiant de requête. `rediriger(request, url, message)`
  fait la redirection après un POST et dépose le message pour la page
  suivante ; il gère aussi les requêtes HTMX (`HX-Redirect`).
- **Gabarits** : étendre `base.html` (blocs `titre`, `actions`, `contenu`) et
  importer `macros.html` : `ui.message`, `ui.messages`, `ui.tableau`,
  `ui.formulaire`, `ui.champ`, `ui.bouton_suppression`, `ui.etiquette`.
  Filtres : `heure` (510 → « 8 h 30 »), `hhmm` (« 08:30 »), `duree`, `jour`,
  `date_fr`, `dateheure_fr`. Classes CSS : voir `static/css/app.css`.
- **Fragments HTMX** : un gabarit sans `{% extends %}`, rendu par la même
  fonction `rendre`. `edt.web.gabarits.est_htmx(request)` dit si la requête
  vient de HTMX. HTMX 2 est embarqué dans `static/js/htmx.min.js`.
- **Menu** : sections et plages d'ordre dans `edt/web/menu.py`
  (Référentiels 100-199, Planification 200-299, Emploi du temps 300-399,
  Données 400-499, Configuration 500-599).
- **Erreurs** : laisser remonter. Une `ErreurEDT` devient une page avec son
  message (404 introuvable, 409 règle métier, 422 données invalides) ; une
  violation d'unicité ou une suppression refusée par la base devient un 409
  expliqué ; tout le reste devient une page « erreur inattendue » avec la
  référence de l'incident, la trace allant dans le journal. Pour une requête
  HTMX, l'erreur revient en fragment de message affiché dans `#messages`.

## Contrats partagés

Le vérificateur et le solveur sont écrits séparément et doivent raisonner
sur **la même représentation**. Cette représentation et les signatures
ci-dessous sont le contrat : on n'en change rien sans le dire à tous.

### L'instantané (`edt.domaine.instantane`)

Dataclasses figées, sans ORM. Construit par :

```python
# edt.services.instantane
def charger_instantane(
    session: Session, semestre_id: int, version_base_id: int | None = None
) -> Instantane:
def charger_placements(session: Session, version_id: int) -> tuple[Placement, ...]:
def charger_occurrences(session: Session, version_id: int) -> tuple[Occurrence, ...]:
```

```mermaid
classDiagram
    direction LR
    class Instantane {
        +int semestre_id
        +date date_debut
        +date date_fin
        +tuple semaines
        +tuple creneaux
        +tuple campus
        +tuple trajets
        +tuple salles
        +tuple enseignants
        +tuple groupes
        +frozenset conflits_groupes
        +tuple indisponibilites
        +tuple jours_fermes
        +Parametres parametres
        +tuple besoins
        +tuple seances
        +tuple problemes_depliage
        +int version_base_id
        +salle(id) Salle
        +enseignant(id) Enseignant
        +groupe(id) Groupe
        +besoin(id) Besoin
        +seance(id) SeanceAPlacer
        +semaine(numero) Semaine
        +date_de(numero_semaine, jour) date
        +semaine_du(date) Semaine
        +fermeture(date, besoin) JourFerme
        +debuts_de_creneau(jour) tuple
        +groupes_lies(groupe_id) frozenset
        +groupes_en_conflit(a, b) bool
        +besoins_en_conflit_de_groupes(a, b) bool
        +trajet(campus_a, campus_b) int
        +salle_accessible(salle) bool
        +indisponibilites_recurrentes(cible, id) tuple
        +indisponibilites_de(cible, id) tuple
    }
    class Besoin {
        +int id
        +str libelle
        +int duree
        +frozenset groupes
        +frozenset enseignants
        +int effectif
        +bool accessibilite_requise
        +frozenset types_salle_acceptes
        +frozenset equipements_requis
        +int salle_imposee_id
        +int campus_impose_id
        +int jour_impose
        +int debut_impose
        +bool samedi_autorise
        +ModeVolume mode_volume
        +ponctuel() bool
    }
    class SeanceAPlacer {
        +str id
        +int rang
        +frozenset semaines
        +Placement verrou
        +Placement indice
        +duree() int
        +jour_impose() int
        +debut_impose() int
    }
    class Placement {
        +str seance_id
        +int jour
        +int debut
        +int fin
        +int salle_id
    }
    class Occurrence {
        +int id
        +int besoin_id
        +str seance_id
        +date date
        +int debut
        +int fin
        +int salle_id
        +StatutOccurrence statut
    }
    Instantane "1" *-- "*" Besoin
    Instantane "1" *-- "*" SeanceAPlacer
    SeanceAPlacer "*" --> "1" Besoin
    Placement "*" ..> "1" SeanceAPlacer : seance_id
    Occurrence "*" ..> "1" Besoin : besoin_id
```

Ce qu'il faut savoir pour l'utiliser :

- **Heures** en minutes depuis minuit, plages semi-ouvertes ; **jours** de 0
  (lundi) à 6 ; **semaines** par numéro relatif au semestre (dès 1).
- **Séance** : identifiée par `"<besoin_id>-<rang>"` (`id_seance`). Ses
  **semaines actives** sont calculées par le dépliage. Deux séances dont les
  semaines actives sont disjointes ne se gênent jamais.
- **Exigences résolues au chargement** : `Besoin.effectif` (somme des
  effectifs), `Besoin.accessibilite_requise` (un groupe du besoin porte le
  drapeau ou est lié à un groupe qui le porte), `Besoin.types_salle_acceptes`
  (ceux du besoin, sinon le type par défaut du type de séance, sinon vide =
  tous les types).
- **Créneau imposé** : lire `SeanceAPlacer.jour_impose` et
  `SeanceAPlacer.debut_impose` (pour une séance ponctuelle, le jour vient de
  la date).
- **Verrou et indice** : repris de la version de base (la version active du
  semestre par défaut). `verrou` est une contrainte ; `indice` un point de
  départ. Un `Placement.salle_id` vide n'existe que pour une séance importée
  ou verrouillée dont la salle reste à choisir.
- **Enseignant fictif** (`Enseignant.fictif`) : exclu de toutes les
  contraintes d'enseignant.
- **Trajets** : `Instantane.trajet(a, b)` vaut 0 pour le même campus, la
  valeur de la matrice sinon, et le paramètre `trajet_defaut_min` à défaut.

### Le dépliage (`edt.domaine.depliage`)

```python
def deplier(
    besoins: Iterable[Besoin], semaines: Sequence[Semaine], jours_fermes: Iterable[JourFerme]
) -> Depliage:                       # Depliage(seances, problemes)
def semaines_ouvertes(
    besoin: Besoin, semaines: Sequence[Semaine], jours_fermes: Iterable[JourFerme]
) -> tuple[Semaine, ...]:
def repartition_reguliere(nombre: int, nb_semaines: int) -> list[int]:
```

- Une semaine est **ouverte** pour un besoin si elle recoupe sa fenêtre et
  si au moins un jour du lundi au vendredi n'est fermé ni globalement ni pour
  l'un de ses groupes.
- `par_semaine` : `nombre` séances, actives toutes les semaines ouvertes.
- `total` : `nombre` séances réparties régulièrement sur les S semaines
  ouvertes ; la semaine d'indice i en reçoit
  `ceil((i+1)·nombre/S) − ceil(i·nombre/S)` ; la séance de rang r est active
  les semaines qui en reçoivent au moins r. 10 séances sur 15 semaines :
  1, 1, 0, 1, 1, 0...
- `ponctuel` : une séance, active la seule semaine de sa date. Elle reste
  permise pendant des examens ou une semaine spéciale
  (`NatureJourFerme.admet_ponctuels`), pas un jour férié ni en vacances.
- Un besoin impossible à déplier entièrement (volume trop grand pour la
  fenêtre, date hors semestre, date un jour fermé) n'est pas corrigé : il
  produit un `ProblemeDepliage` (`Instantane.problemes_depliage`) et le
  calcul est refusé tant que la contrainte concernée (`VOLUME` ou
  `JOUR_FERME`) est active.

### Les conflits entre groupes (`edt.services.conflits_groupes`)

```python
def calculer_conflits_groupes(
    parents: Mapping[int, int | None],
    chevauchements: Iterable[Paire] = (),
    inscriptions: Iterable[Paire] = (),
) -> frozenset[Paire]:               # Paire = tuple[int, int], a < b
def conflits_groupes(session: Session, annee_scolaire_id: int) -> frozenset[Paire]:
```

Deux groupes sont en conflit si l'un est un ascendant de l'autre, s'ils sont
déclarés chevauchants (la déclaration vaut pour toute la lignée des deux
groupes : ascendants et descendants), ou s'ils partagent un étudiant (un
inscrit appartient aussi aux ascendants de son groupe). Deux frères ne sont
pas en conflit. L'instantané embarque le résultat : utiliser
`Instantane.groupes_en_conflit(a, b)`, `groupes_lies(groupe_id)` ou
`besoins_en_conflit_de_groupes(besoin_a, besoin_b)`.

### Le vérificateur (`edt.verification`)

```python
def verifier(
    instantane: Instantane,
    placements: Sequence[Placement],
    contraintes_actives: Iterable[str] | None = None,
) -> list[Anomalie]:
def verifier_occurrences(
    instantane: Instantane,
    occurrences: Sequence[Occurrence],
    contraintes_actives: Iterable[str] | None = None,
) -> list[Anomalie]:
```

`Anomalie(code, gravite, message, seances=(), occurrences=(), salles=(),
enseignants=(), groupes=(), jour=None, date=None)`. Liste sans anomalie de
gravité « erreur » = emploi du temps valide. `contraintes_actives=None`
vérifie toutes les contraintes du catalogue. Tant que le lot n'est pas
livré, les deux fonctions lèvent `ErreurNonLivre` (surtout pas une liste
vide). Point d'entrée à importer : `from edt.verification import verifier`.

### Le solveur (`edt.solveur`)

```python
def resoudre(
    instantane: Instantane,
    options: OptionsSolveur,
    arret: threading.Event | None = None,
) -> ResultatSolveur:
```

- `OptionsSolveur(temps_limite_s=60.0, contraintes_desactivees=frozenset(),
  optimiser=False, graine=0, nb_workers=0, utiliser_indices=True,
  tolerer_violations_heritees=False)` ;
  `OptionsSolveur.depuis_parametres(parametres, ...)` donne les options par
  défaut d'un calcul.
- `ResultatSolveur(statut, placements=(), contraintes_desactivees=frozenset(),
  message="", avertissements=(), statistiques={})`.
- `StatutSolveur` : `SOLUTION`, `INFAISABLE` (prouvé), `TEMPS_DEPASSE` (pas
  trouvé, sans preuve), `ARRETE`.
- Tout ou rien : `placements` contient un placement par séance de
  l'instantané, ou rien. L'orchestration refuse une solution incomplète.
- Le moteur exporté aujourd'hui (`edt.solveur.provisoire`) est **provisoire** :
  glouton, il ne garantit que l'unicité des ressources et la capacité. Le
  lot solveur écrit le vrai moteur dans ce paquet et change l'import de
  `edt/solveur/__init__.py`.

### L'orchestration d'un calcul (`edt.services.generation`)

```python
def lancer_calcul(
    semestre_id: int,
    options: OptionsSolveur | None = None,
    *,
    moteur: Moteur | None = None,
    version_base_id: int | None = None,
    activer: bool = True,
) -> int:                                         # identifiant du calcul
def etat_calcul(calcul_id: int) -> SuiviCalcul:
def calcul_en_cours() -> SuiviCalcul | None:
def dernier_calcul(semestre_id: int | None = None) -> SuiviCalcul | None:
def demander_arret(calcul_id: int) -> SuiviCalcul:
def attendre_calcul(calcul_id: int, delai_s: float | None = None) -> SuiviCalcul:
def exiger_reussite(suivi: SuiviCalcul) -> SuiviCalcul:
def cloturer_calculs_interrompus() -> int:
```

`SuiviCalcul(id, semestre_id, etat, demarre_le, termine_le, temps_ecoule_s,
message, version_id, contraintes_desactivees, statistiques, fichier_journal,
arret_demande)`, avec `en_cours` et `reussi`.

- Le calcul tourne dans un fil d'arrière-plan ; **un seul à la fois**
  (`ErreurCalculEnCours` sinon). L'interface interroge `etat_calcul` chaque
  seconde.
- États (`EtatCalcul`) : `en_cours`, `reussi`, `infaisable`, `temps_depasse`,
  `arrete`, `erreur`.
- Succès : une nouvelle `version_edt` (activée par défaut, l'ancienne est
  désactivée) et ses `seance_hebdo`. Tout autre cas : **aucune version**.
- Ces fonctions ouvrent leurs propres sessions (un calcul dépasse la durée
  d'une requête). `demander_arret` n'atteint que les calculs du processus
  courant ; au démarrage du serveur, `cloturer_calculs_interrompus` marque en
  erreur les calculs qu'un arrêt a laissés « en cours ».
- L'orchestration n'instancie pas les séances datées et n'appelle pas le
  vérificateur : ces étapes appartiennent aux lots suivants.

### Les paramètres (`edt.services.parametres`)

```python
def lire_parametres(session: Session) -> Parametres:
def lire_parametre(session: Session, cle: str) -> ValeurParametre:
def ecrire_parametre(session: Session, cle: str, valeur: ValeurParametre) -> None:
def ecrire_parametres(session: Session, valeurs: dict[str, ValeurParametre]) -> Parametres:
def reinitialiser_parametres(session: Session) -> Parametres:
```

Les paramètres, leurs bornes et leurs valeurs par défaut sont les champs de
`edt.domaine.parametres.Parametres` ; `definitions()` les décrit pour l'écran
de configuration et `convertir_texte(cle, texte)` convertit une saisie.

### Sémantique des contraintes

Le catalogue (`edt.domaine.contraintes.CATALOGUE`) donne le code, le libellé
et la description de chaque contrainte. Les précisions suivantes valent pour
le vérificateur **et** le solveur.

| Code | Précision |
|---|---|
| `SALLE_UNIQUE`, `ENSEIGNANT_UNIQUE`, `GROUPE_UNIQUE` | Deux séances se gênent si elles ont lieu le même jour, que leurs plages se recoupent et que leurs semaines actives se croisent. Groupes : relation de conflit de l'instantané. Enseignants fictifs exclus. |
| `PAUSE`, `CAMPUS_TRAJET` | Concernent les **personnes** (groupes en conflit, enseignants non fictifs), pas les salles. |
| `DEJEUNER` | Plage `dejeuner_debut_min`..`dejeuner_fin_min` libre pour chaque groupe et chaque enseignant non fictif. |
| `HORAIRES` | Début sur un début de créneau de la grille et séance dans la plage `ouverture_min`..`fermeture_min`. Un **jour imposé ou un début imposé non respecté** relève aussi de ce code. Un créneau imposé hors grille est une anomalie de gravité « information », pas une erreur. |
| `JOURS` | Lundi à vendredi ; samedi seulement si `Besoin.samedi_autorise` et sur un créneau de la grille. |
| `CAPACITE` | Capacité de la salle ≥ `Besoin.effectif`. |
| `TYPE_SALLE` | Type de la salle dans `types_salle_acceptes` (vide = tous). Une **salle imposée ou un campus imposé non respecté** relève aussi de ce code (famille « exigences de salle du besoin »). |
| `EQUIPEMENT` | `equipements_requis` inclus dans les équipements de la salle. |
| `ACCESSIBILITE` | Si `Besoin.accessibilite_requise`, alors `Instantane.salle_accessible(salle)`. |
| `DISPO_ENSEIGNANT`, `DISPO_SALLE` | Semaine type : indisponibilités **récurrentes** seulement (`indisponibilites_recurrentes`). Couche datée : toutes (`indisponibilites_de`, `Indisponibilite.concerne(date)`). |
| `DISPO_GROUPE` | Même règle ; de plus l'indisponibilité d'un groupe vaut pour toute séance d'un groupe qui lui est **lié** (`Instantane.groupes_lies`) : ascendants, descendants, groupes déclarés chevauchants ou partageant un étudiant. |
| `MAX_ENSEIGNANT` | `max_minutes_jour` et `max_minutes_semaine` quand ils sont renseignés ; la semaine s'entend semaine active par semaine active. |
| `VOLUME` | Chaque séance de l'instantané a un placement. |
| `VERROU` | Une séance dont `verrou` est renseigné garde ce jour, cette heure et cette salle. |
| `JOUR_FERME` | Couche datée seulement : `Instantane.fermeture(date, besoin)`. Une fermeture de portée « groupe » vaut pour ce groupe et ses descendants (pas pour les groupes seulement liés). |

Les jours fermés et les indisponibilités datées ne restreignent pas la
semaine type : l'occurrence qui tombe dessus est replacée par la couche
datée, ou passe « à programmer ». Seule exception, une semaine entièrement
fermée pour un besoin est retirée de ses semaines actives par le dépliage.

## Journalisation

- Configurée **une seule fois**, par `edt.journal.configurer_journalisation()`
  (appelée par `creer_app()` et par la ligne de commande) : console et
  fichier tournant `<EDT_LOG_DIR>/edt.log` (5 Mo, 5 archives).
- Format : `date niveau [contexte] module : message`. Le contexte vaut
  `req=<identifiant>` pendant une requête web, `calcul=<identifiant>`
  pendant un calcul, `-` sinon. Il vient de variables de contexte : le code
  métier n'a rien à passer.
- Dans chaque module : `journal = logging.getLogger(__name__)`. Jamais de
  `print` dans un service.
- Chaque calcul a en plus son fichier
  `<EDT_LOG_DIR>/generations/calcul-<id>-<date>-<heure>.log`, qui reçoit tout
  ce qui est journalisé dans le fil du calcul. Pour un rappel exécuté dans un
  autre fil (journal de CP-SAT), prendre `edt.journal.journal_calcul_courant()`
  au début de `resoudre` et écrire dessus.
- Niveaux : `INFO` pour ce qu'un exploitant veut suivre (requête, calcul,
  import), `WARNING` pour un refus métier ou une situation anormale gérée,
  `ERROR` avec trace (`journal.exception`) pour l'inattendu.

## Erreurs

- **Rien n'échoue en silence** : pas de `except: pass`, pas de
  `except Exception` sans `journal.exception(...)` ni relance, pas de valeur
  de repli silencieuse.
- Les services lèvent des `ErreurEDT` (`edt.erreurs`), dont le message
  français est affichable tel quel ; `details` porte les lignes
  complémentaires.

| Classe | Usage | HTTP | Code de sortie |
|---|---|---|---|
| `ErreurDonneesInvalides` | saisie ou donnée importée hors format ou hors règle | 422 | 1 |
| `ErreurIntrouvable` | l'élément demandé n'existe pas | 404 | 1 |
| `ErreurRegleMetier` | opération refusée par une règle (doublon, élément utilisé) | 409 | 1 |
| `ErreurCalculEnCours` | un calcul tourne déjà (sous-classe de la précédente) | 409 | 1 |
| `ErreurInfaisable` | impossibilité prouvée | 422 | 1 |
| `ErreurTempsDepasse` | pas de solution dans le temps imparti | 422 | 1 |
| `ErreurNonLivre` | fonction d'un lot pas encore livré | 501 | 1 |
| `ErreurConfiguration` | variable d'environnement ou dossier invalide | 500 | 1 |
| autre exception | erreur de programmation : trace dans le journal | 500 | 2 |

## Où ajouter...

| Quoi | Où |
|---|---|
| une **contrainte** | un membre de `CodeContrainte` et son entrée dans `CATALOGUE` (`edt/domaine/contraintes.py`), puis son implémentation dans le vérificateur **et** dans le solveur, avec un test de chaque côté |
| un **paramètre** | un champ de `Parametres` (`edt/domaine/parametres.py`) : il apparaît à la prochaine `init-db` |
| une **table** ou une colonne | `edt/db/modeles.py` (et `MODELES`), puis `docs/modele-donnees.md` (diagramme et tableau) ; recréer la base avec `init-db --reset` ; si le solveur ou le vérificateur en a besoin, l'exposer dans l'instantané |
| une **valeur d'énumération** | `edt/domaine/enumerations.py`, avec son libellé |
| une **donnée par défaut** | `edt/db/initialisation.py` |
| un **service** | un module de `edt/services/` : fonctions typées qui reçoivent la session |
| un **écran** | un module de `edt/web/routes/` et ses gabarits (voir plus haut) |
| une **commande** | un module de `edt/commandes/` (voir plus haut) |
| un **test** | `tests/<lot>/test_*.py`, avec les fixtures de `tests/conftest.py` ; un test long porte `@pytest.mark.perf` |

## Vérifier son travail

```sh
python -m ruff check .
python -m ruff format --check .
python -m pytest -m "not perf"
python -m edt doctor
```
