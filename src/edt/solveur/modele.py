"""Modèle CP-SAT des horaires de la semaine type : la première phase du calcul.

Le calcul se fait en deux phases (justification et mesures dans
`docs/solveur.md`) :

1. **horaires** (ce module) : pour chaque séance, un début et un campus ;
2. **salles** (`edt.solveur.salles`) : une salle pour chaque séance, les
   horaires étant fixés.

Variables
---------
Pour chaque séance, un booléen par **choix** possible, et exactement un choix
vrai. Un choix (`Option`) est un couple (début, campus) accompagné des salles
de ce campus admissibles et disponibles à ce début. Les débuts et les salles
viennent des domaines pré-filtrés de `edt.solveur.preparation` : toutes les
contraintes qui portent sur une seule séance sont donc déjà respectées.

Contraintes
-----------
- **personnes** (`ENSEIGNANT_UNIQUE`, `GROUPE_UNIQUE`, `PAUSE`) : deux séances
  qui partagent une personne et coexistent ne sont jamais en cours au même
  instant, chaque séance étant allongée de la pause ;
- **trajets** (`CAMPUS_TRAJET`) et pause sans unicité : les couples de choix
  trop rapprochés de deux séances d'une même personne sont interdits ;
- **maxima journaliers** (`MAX_ENSEIGNANT`) ;
- **pause déjeuner libre bornée** (`DEJEUNER` ou règle `PAUSE_DEJEUNER`,
  réglage de chaque personne dans `Instantane.reglages`) : pour chaque
  personne (groupe feuille avec les séances de sa lignée, enseignant :
  `Instantane.emplois`), chaque jour, l'une des positions de pause possibles
  de sa fenêtre `[X, Y[` reste libre de toute séance (voir `_pause_dejeuner`) ;
- **capacité des salles** (`SALLE_UNIQUE`, condition nécessaire) : à chaque
  instant, les séances en cours se répartissent entre des classes de salles
  interchangeables sans dépasser le nombre de salles libres de chaque classe.
  C'est ce qui rend la seconde phase presque toujours possible ; quand elle
  ne l'est pas, elle renvoie les séances en cause et `interdire` écarte cette
  combinaison d'horaires ;
- **symétries** : les séances interchangeables d'un même besoin sont
  ordonnées par début.

Semaines actives : une contrainte ne lie deux séances que si une semaine au
moins les réunit. Deux séances aux semaines disjointes ne partagent aucune
contrainte.

Contraintes souples
-------------------
Une contrainte de niveau « important » ou « facultatif » (`Regles.souple`)
n'est pas posée en dur : chaque **entorse** est un littéral ou un entier du
modèle, qui coûte `Regles.poids` dans l'objectif. Unités d'entorse :

- `HORAIRES`, `JOURS`, `DEJEUNER`, `DISPO_ENSEIGNANT`, `DISPO_GROUPE` : une
  séance placée à un début qui enfreint la règle (étiquettes de
  `SeancePreparee.entorses`) ;
- `PAUSE`, `CAMPUS_TRAJET` : une paire de séances d'une même personne, le
  même jour, trop rapprochées (une seule entorse par paire, quel que soit le
  nombre de semaines) ;
- `MAX_ENSEIGNANT` : une heure entamée au-delà du maximum journalier, par
  jour et par ensemble de semaines actives ;
- `DEJEUNER` en pause libre bornée : une personne (groupe ou enseignant), un
  jour et un ensemble de semaines sans N minutes libres d'un seul tenant
  dans la fenêtre.

Règles personnalisées (`edt.solveur.personnalisees`) : les types de sorte
« séance » sont réglés dans les domaines par la préparation (entorse par choix,
sans variable : `_creer_variables` la note) ; la pause déjeuner et la pause
minimale de chaque personne viennent de `Instantane.reglages` avec leur
source ; les types d'emploi du temps et de relation (maximum par jour,
journées libres, trous, campus par jour, matières par jour, ordre dans la
semaine, répartition d'une matière) ont chacun leur poseur
(`edt.solveur.regles_emploi`, étape `_regles_personnalisees`). Chaque entorse
est imputée à sa source (`Regles.Imputation` : un code du catalogue ou
l'identifiant d'une règle), au poids du niveau de la source ;
`Bilan.entorses_regles` les compte par règle.

Les entorses aux exigences de salle (`EQUIPEMENT`, `CAPACITE`, `TYPE_SALLE`,
`ACCESSIBILITE`, `DISPO_SALLE`) relèvent de la seconde phase
(`edt.solveur.salles`). `bilan` compte les entorses de ce modèle à partir des
seuls choix des séances, objectif posé ou non.

Objectif
--------
Le modèle est d'abord résolu **sans objectif** : il s'agit d'obtenir vite un
emploi du temps valide. `poser_objectif` ajoute ensuite l'objectif de qualité
au même modèle, et `partir_de` donne la solution valide comme point de départ
complet : l'amélioration commence là où la première recherche s'est arrêtée,
et ne peut que faire mieux. `bilan` mesure une solution (trous, samedis,
valeur de l'objectif) à partir des seuls choix des séances.
"""

from __future__ import annotations

import logging
import time
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable, Sequence
from dataclasses import dataclass, field
from itertools import combinations

from ortools.sat.python import cp_model

from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import SAMEDI, CibleIndisponibilite
from edt.domaine.heures import se_chevauchent
from edt.domaine.regles import NaturePersonne
from edt.erreurs import ErreurEDT
from edt.solveur.contrat import OptionsSolveur
from edt.solveur.preparation import Preparation
from edt.solveur.regles import ExigencePause, Imputation
from edt.solveur.regles_emploi import PAR_MODELE

journal = logging.getLogger(__name__)

C = CodeContrainte

Litteral = cp_model.IntVar | bool
"""Booléen du modèle, ou constante Python quand la valeur est déjà connue."""

Plage = tuple[int, int, Litteral]
"""Plage `[début, fin[` sur l'axe codé de la semaine, et le littéral qui dit si elle a lieu."""

Vus = set[tuple[int, tuple[int, ...]]]
"""Contraintes « au plus n parmi » déjà posées, pour ne pas les répéter."""


@dataclass(frozen=True)
class Option:
    """Un choix possible pour une séance : un début codé, un campus et les salles qui vont avec."""

    debut: int
    campus: int
    salles: tuple[int, ...]


@dataclass(frozen=True)
class Bilan:
    """Qualité d'une solution d'horaires.

    - `objectif` : valeur des termes de confort (somme pondérée des quatre
      termes, comptés semaine par semaine), 0 tant qu'ils ne sont pas posés ;
    - `trous` : créneaux libres entre deux séances d'un groupe (`None` si le
      poids des trous est nul ou si le confort n'est pas posé) ;
    - `samedis` : séances placées le samedi (`None` dans les mêmes cas) ;
    - `entorses` : nombre d'entorses par contrainte souple de ce modèle ;
    - `entorses_regles` : nombre d'entorses par règle personnalisée souple
      (identifiant de la règle) ;
    - `penalite` : coût de toutes ces entorses dans l'objectif.
    """

    objectif: int
    trous: int | None
    samedis: int | None
    entorses: dict[CodeContrainte, int] = field(default_factory=dict)
    entorses_regles: dict[int, int] = field(default_factory=dict)
    penalite: int = 0

    @property
    def total(self) -> int:
        """Valeur complète de l'objectif : confort et entorses."""
        return self.objectif + self.penalite


def au_plus(
    cp: cp_model.CpModel, litteraux: Sequence[Litteral], capacite: int = 1, vus: Vus | None = None
) -> bool:
    """Impose qu'au plus `capacite` des `litteraux` soient vrais.

    Les constantes sont décomptées ; rien n'est posé si la limite ne peut pas
    être dépassée ou si la même contrainte figure déjà dans `vus`. Renvoie
    vrai si une contrainte a été ajoutée.
    """
    acquis = sum(1 for litteral in litteraux if litteral is True)
    variables = [
        litteral for litteral in litteraux if litteral is not True and litteral is not False
    ]
    reste = capacite - acquis
    if len(variables) <= reste:
        return False
    if vus is not None:
        cle = (reste, tuple(sorted({variable.index for variable in variables})))
        if cle in vus:
            return False
        vus.add(cle)
    if reste < 0:
        cp.add_bool_or([])  # déjà trop de séances certaines : aucun emploi du temps possible
    elif reste == 0:
        cp.add_bool_and([variable.negated() for variable in variables])
    elif reste == 1:
        cp.add_at_most_one(variables)
    else:
        cp.add(sum(variables) <= reste)
    return True


def limiter_par_instant(
    cp: cp_model.CpModel,
    plages: Sequence[Plage],
    capacite: int | Callable[[int], int] = 1,
    vus: Vus | None = None,
) -> int:
    """Impose qu'au plus `capacite` des `plages` soient en cours à chaque instant.

    Deux plages se recoupent si et seulement si l'une contient le début de
    l'autre : il suffit d'examiner les instants où une plage peut commencer.
    `capacite` peut dépendre de l'instant. Renvoie le nombre de contraintes
    ajoutées.
    """
    instants = sorted({debut for debut, _, _ in plages})
    en_cours: list[list[Litteral]] = [[] for _ in instants]
    for debut, fin, litteral in plages:
        for rang in range(bisect_left(instants, debut), bisect_left(instants, fin)):
            en_cours[rang].append(litteral)
    ajoutees = 0
    for instant, litteraux in zip(instants, en_cours, strict=True):
        limite = capacite(instant) if callable(capacite) else capacite
        ajoutees += au_plus(cp, litteraux, limite, vus)
    return ajoutees


def couvrir_par_cliques(voisins: dict[Hashable, set]) -> list[list]:
    """Couvre les arêtes d'un graphe par des cliques (et chaque sommet isolé par lui-même).

    `voisins` donne les voisins de chaque sommet (relation symétrique). Chaque
    arête est dans une clique au moins, et les sommets d'une clique sont deux
    à deux voisins : poser « au plus un » sur chaque clique équivaut
    exactement à l'interdire sur chaque arête.
    """
    rang = {sommet: position for position, sommet in enumerate(voisins)}
    couvertes: set[tuple[int, int]] = set()
    cliques: list[list] = []
    for sommet in voisins:
        if not voisins[sommet]:
            cliques.append([sommet])
        for autre in sorted(voisins[sommet], key=rang.__getitem__):
            if rang[autre] < rang[sommet] or (rang[sommet], rang[autre]) in couvertes:
                continue
            clique = [sommet, autre]
            for candidat in sorted(voisins[sommet] & voisins[autre], key=rang.__getitem__):
                if all(candidat in voisins[membre] for membre in clique[2:]):
                    clique.append(candidat)
            couvertes.update(combinations(sorted(rang[membre] for membre in clique), 2))
            cliques.append(clique)
    return cliques


class ModeleHoraires:
    """Modèle CP-SAT des horaires d'un calcul préparé (voir l'en-tête du module).

    - `options[i]` : les choix possibles de la séance d'indice `i` ;
    - `litteraux[i]` : le booléen de chaque choix (`True` s'il n'y en a qu'un).

    `point_de_controle` est appelé entre les étapes de la construction ; il
    lève une exception pour interrompre (arrêt demandé, temps dépassé).
    """

    def __init__(
        self,
        preparation: Preparation,
        options: OptionsSolveur,
        point_de_controle: Callable[[], None] = lambda: None,
    ) -> None:
        self.preparation = preparation
        self.regles = preparation.regles
        self.instantane = preparation.instantane
        self.seances = preparation.seances
        self.cp = cp_model.CpModel()
        self.options: list[tuple[Option, ...]] = []
        self.litteraux: list[list[Litteral]] = []
        self.objectif_actif = False
        self.nb_coupes = 0
        self._vus: Vus = set()
        # Variables auxiliaires déduites des choix : (variable, nature, données). Natures :
        # "ou", "et", "aucun" (de littéraux) et "surplus" (heures au-delà d'un maximum).
        self._definitions: list[tuple[cp_model.IntVar, str, object]] = []
        # Objectif : (coefficient, variable) ; compteurs lisibles : (nombre de semaines, littéral).
        self._termes: list[tuple[int, Litteral]] = []
        self._termes_entorses: list[tuple[int, Litteral]] = []
        self._compteurs: dict[str, list[tuple[int, Litteral]] | None] = {}
        # Entorses aux contraintes souples : par code, (unités, variable qui vaut l'entorse).
        self._entorses: dict[Imputation, list[tuple[int, Litteral]]] = defaultdict(list)
        self._cache_trous: dict[tuple[int, ...], list[cp_model.IntVar]] = {}
        self.index_de: dict[str, int] = {}
        self._campus_de = {salle.id: salle.campus_id for salle in self.instantane.salles}

        etapes = [
            self._creer_variables,
            self._separer_les_personnes,
            self._pauses_et_trajets,
            self._maxima_journaliers,
            self._pause_dejeuner,
            self._regles_personnalisees,
            self._capacite_des_salles,
            self._briser_les_symetries,
        ]
        if options.utiliser_indices:
            etapes.append(self._donner_les_indices)
        for etape in etapes:
            debut = time.perf_counter()
            avant = len(self.cp.proto.constraints)
            etape()
            journal.debug(
                "Étape %s : %.3f s, %d contrainte(s)",
                etape.__name__.strip("_"),
                time.perf_counter() - debut,
                len(self.cp.proto.constraints) - avant,
            )
            point_de_controle()

    # -- lecture et coupes ------------------------------------------------------

    @property
    def nb_variables(self) -> int:
        """Nombre de variables du modèle."""
        return len(self.cp.proto.variables)

    @property
    def nb_contraintes(self) -> int:
        """Nombre de contraintes du modèle."""
        return len(self.cp.proto.constraints)

    def choix(self, valeur: Callable[[cp_model.IntVar], bool]) -> tuple[int, ...]:
        """Rang du choix retenu pour chaque séance ; `valeur` lit un booléen de la solution."""
        return tuple(
            next(r for r, litteral in enumerate(litteraux) if litteral is True or valeur(litteral))
            for litteraux in self.litteraux
        )

    def horaires(self, choix: Sequence[int]) -> list[Option]:
        """Les `Option` correspondant aux rangs `choix` (un par séance)."""
        return [self.options[index][rang] for index, rang in enumerate(choix)]

    def interdire(self, choix: Sequence[int], indices: Iterable[int]) -> None:
        """Coupe : les séances `indices` ne peuvent pas toutes garder le choix de `choix`.

        Utilisée quand la phase des salles prouve que ces séances, à ces
        horaires, ne peuvent pas toutes recevoir une salle.
        """
        clause = [
            self.litteraux[index][choix[index]].negated()
            for index in indices
            if self.litteraux[index][choix[index]] is not True
        ]
        self.cp.add_bool_or(clause)  # clause vide : ces horaires fixes sont impossibles
        self.nb_coupes += 1

    # -- outils -----------------------------------------------------------------

    def _plages(
        self, index: int, marge: int = 0, rangs: Iterable[int] | None = None
    ) -> list[Plage]:
        """Plages horaires des choix de la séance `index`, allongées de `marge`."""
        duree = self.seances[index].duree + marge
        options, litteraux = self.options[index], self.litteraux[index]
        if rangs is None:
            rangs = range(len(options))
        return [(options[r].debut, options[r].debut + duree, litteraux[r]) for r in rangs]

    def _au_moins_un(self, litteraux: Sequence[Litteral]) -> Litteral:
        """Littéral impliqué par chacun des `litteraux` (vrai dès que l'un d'eux l'est).

        Un seul sens suffit : ce littéral ne sert qu'à interdire ou à
        pénaliser, jamais à exiger.
        """
        if any(litteral is True for litteral in litteraux):
            return True
        if len(litteraux) == 1:
            return litteraux[0]
        resultat = self.cp.new_bool_var("")
        for litteral in litteraux:
            self.cp.add_implication(litteral, resultat)
        self._definitions.append((resultat, "ou", list(litteraux)))
        return resultat

    def _sauf_si(self, exceptions: Sequence[Litteral]) -> cp_model.IntVar:
        """Booléen de pénalité : vrai dès qu'aucune des `exceptions` n'est vraie."""
        penalite = self.cp.new_bool_var("")
        exceptions = [litteral for litteral in exceptions if litteral is not False]
        self.cp.add_bool_or([penalite, *exceptions])
        self._definitions.append((penalite, "aucun", exceptions))
        return penalite

    def au_moins_un(self, litteraux: Sequence[Litteral]) -> Litteral:
        """`_au_moins_un`, pour les poseurs de `edt.solveur.regles_emploi`."""
        return self._au_moins_un(litteraux)

    def definir(self, variable: cp_model.IntVar, nature: str, donnees: object) -> None:
        """Enregistre la définition d'une variable auxiliaire (pour `bilan`, voir `_completer`)."""
        self._definitions.append((variable, nature, donnees))

    def noter_entorse(self, imputation: Imputation, unites: int, litteral: Litteral) -> None:
        """Compte `unites` entorses imputées à `imputation` quand `litteral` est vrai."""
        self._entorses[imputation].append((unites, litteral))

    def trous(self, emploi: Sequence[int]) -> list[cp_model.IntVar]:
        """Booléens de trou d'un emploi du temps (`_trous`), partagés entre l'objectif et les règles."""
        cle = tuple(emploi)
        if cle not in self._cache_trous:
            self._cache_trous[cle] = self._trous(emploi, self._creneaux_par_jour())
        return self._cache_trous[cle]

    def _ensemble(self, litteraux: Sequence[Litteral]) -> Litteral:
        """Littéral impliqué par la conjonction des `litteraux` (vrai dès qu'ils le sont tous)."""
        restants = [litteral for litteral in litteraux if litteral is not True]
        if any(litteral is False for litteral in litteraux):
            return False
        if not restants:
            return True
        if len(restants) == 1:
            return restants[0]
        resultat = self.cp.new_bool_var("")
        self.cp.add_bool_or([*(litteral.negated() for litteral in restants), resultat])
        self._definitions.append((resultat, "et", restants))
        return resultat

    def _par_personne(self) -> tuple[dict[int, list[int]], dict[int, list[int]]]:
        """Indices des séances par enseignant non fictif et par groupe."""
        par_enseignant: dict[int, list[int]] = defaultdict(list)
        par_groupe: dict[int, list[int]] = defaultdict(list)
        for seance in self.seances:
            for enseignant in self.regles.enseignants_reels(seance.besoin):
                par_enseignant[enseignant].append(seance.index)
            for groupe in seance.besoin.groupes:
                par_groupe[groupe].append(seance.index)
        return par_enseignant, par_groupe

    # -- variables --------------------------------------------------------------

    def _creer_variables(self) -> None:
        cache: dict[tuple[int, int, int], tuple[Option, ...]] = {}
        self.index_de = {seance.seance.id: seance.index for seance in self.seances}
        for seance in self.seances:
            # Les séances d'un même besoin partagent leurs domaines (mêmes objets).
            cle = (id(seance.debuts), id(seance.salles), id(seance.debuts_interdits))
            options = cache.get(cle)
            if options is None:
                liste = []
                for debut in seance.debuts:
                    par_campus: dict[int, list[int]] = defaultdict(list)
                    for salle in seance.salles:
                        if debut not in seance.debuts_interdits.get(salle, ()):
                            par_campus[self._campus_de[salle]].append(salle)
                    liste.extend(
                        Option(debut, campus, tuple(par_campus[campus]))
                        for campus in sorted(par_campus)
                    )
                options = tuple(liste)
                cache[cle] = options
            if not options:
                raise ErreurEDT(
                    f"Erreur interne : la séance {seance.seance.id} n'a aucun choix possible "
                    "alors que les pré-vérifications l'ont acceptée."
                )
            if len(options) == 1:
                litteraux: list[Litteral] = [True]
            else:
                litteraux = [self.cp.new_bool_var("") for _ in options]
                self.cp.add_exactly_one(litteraux)
            self.options.append(options)
            self.litteraux.append(litteraux)
            # Choix qui enfreignent une contrainte ou une règle souple : une entorse par séance
            # placée là, imputée à la source.
            for option, litteral in zip(options, litteraux, strict=True):
                violations = [
                    *seance.entorses.get(option.debut, ()),
                    *seance.entorses_campus.get((option.debut, option.campus), ()),
                ]
                for violation in violations:
                    self._entorses[violation.imputation].append((1, litteral))

    # -- personnes --------------------------------------------------------------

    def _separer_les_personnes(self) -> None:
        """`ENSEIGNANT_UNIQUE`, `GROUPE_UNIQUE` et, par l'allongement des plages, `PAUSE`.

        Les séances sont réunies en **unités** : même ensemble de groupes (ou
        même enseignant) et mêmes semaines actives. Deux séances d'une même
        unité se gênent toujours. Deux unités se gênent si leurs semaines se
        croisent et si elles partagent l'enseignant ou ont des groupes en
        conflit. Pour chaque unité et chaque instant, un booléen « occupée »
        est impliqué par les choix de ses séances en cours ; « au plus une
        unité occupée » est posé sur chaque clique d'unités qui se gênent.
        Passer par les unités évite de répéter les mêmes séances dans des
        centaines de contraintes presque identiques (groupes transverses).
        """
        regles, preparation = self.regles, self.preparation
        avec_enseignants = regles.active(C.ENSEIGNANT_UNIQUE)
        avec_groupes = regles.active(C.GROUPE_UNIQUE)
        # Une pause souple n'allonge pas les plages : ses entorses sont comptées par paire.
        # La plus petite pause dure en vigueur vaut pour tout le monde ; le reste passe par des
        # couples de choix (`_pauses_et_trajets`).
        pause = regles.pause_garantie()

        # Unités : ("g", groupes, semaines) et ("e", enseignant, semaines). Avec les
        # violations héritées tolérées, chaque séance verrouillée forme sa propre unité.
        unites: dict[tuple, list[int]] = defaultdict(list)
        for seance in self.seances:
            seule = (seance.index,) if seance.verrouillee and preparation.isolees else ()
            if avec_groupes:
                groupes = tuple(sorted(seance.besoin.groupes))
                unites[("g", groupes, seance.semaines, *seule)].append(seance.index)
            if avec_enseignants:
                for enseignant in sorted(regles.enseignants_reels(seance.besoin)):
                    unites[("e", enseignant, seance.semaines, *seule)].append(seance.index)

        # Graphe des unités qui se gênent.
        par_groupe: dict[int, list[tuple]] = defaultdict(list)
        par_enseignant: dict[int, list[tuple]] = defaultdict(list)
        for cle in unites:
            if cle[0] == "g":
                for groupe in cle[1]:
                    par_groupe[groupe].append(cle)
            else:
                par_enseignant[cle[1]].append(cle)
        voisins: dict[tuple, set] = {cle: set() for cle in unites}
        for cle in unites:
            if cle[0] == "g":
                lies = frozenset().union(*(self.instantane.groupes_lies(g) for g in cle[1]))
                candidats = {autre for groupe in lies for autre in par_groupe.get(groupe, ())}
            else:
                candidats = set(par_enseignant[cle[1]])
            for autre in candidats:
                if autre == cle or not cle[2] & autre[2]:
                    continue
                if len(cle) > 3 and len(autre) > 3 and preparation.dispensees(cle[3], autre[3]):
                    continue  # deux séances verrouillées dont la contradiction est tolérée
                voisins[cle].add(autre)
                voisins[autre].add(cle)

        # Occupation de chaque unité à chaque instant.
        instants = sorted({option.debut for options in self.options for option in options})
        occupation: dict[tuple, dict[int, Litteral]] = {}
        nb_contraintes = 0
        for cle, indices in unites.items():
            plages = [plage for index in indices for plage in self._plages(index, pause)]
            en_cours: dict[int, list[Litteral]] = defaultdict(list)
            for debut, fin, litteral in plages:
                for rang in range(bisect_left(instants, debut), bisect_left(instants, fin)):
                    en_cours[rang].append(litteral)
            if len(indices) >= 2:
                # Les séances d'une même unité se gênent entre elles.
                for litteraux in en_cours.values():
                    nb_contraintes += au_plus(self.cp, litteraux, 1, self._vus)
            if voisins[cle]:
                occupation[cle] = {
                    rang: self._au_moins_un(litteraux) for rang, litteraux in en_cours.items()
                }

        cliques = [clique for clique in couvrir_par_cliques(voisins) if len(clique) >= 2]
        for clique in cliques:
            rangs = sorted({rang for cle in clique for rang in occupation[cle]})
            for rang in rangs:
                occupees = [occupation[cle][rang] for cle in clique if rang in occupation[cle]]
                nb_contraintes += au_plus(self.cp, occupees, 1, self._vus)
        journal.debug(
            "Personnes : %d unité(s), %d clique(s) d'unités qui se gênent, %d contrainte(s)",
            len(unites),
            len(cliques),
            nb_contraintes,
        )

    # -- paires -----------------------------------------------------------------

    def _couples_a_regler(
        self,
        options_i: tuple[Option, ...],
        duree_i: int,
        options_j: tuple[Option, ...],
        duree_j: int,
        separees: bool,
        pauses: tuple[ExigencePause, ...],
    ) -> dict[Imputation | None, list[tuple[tuple[int, ...], tuple[int, ...]]]]:
        """Couples de choix de deux séances d'une même personne à interdire ou à pénaliser.

        Renvoie, par nature, des couples `(rangs de la première, rangs de la
        seconde)` : aucun choix du premier groupe n'est compatible avec un
        choix du second. La nature `None` est l'interdiction (une contrainte
        ou une règle dure est enfreinte) ; une autre nature est une entorse à
        cette contrainte souple (`CAMPUS_TRAJET`) ou à cette source de pause
        (`PAUSE`, ou l'identifiant d'une règle `PAUSE_MINIMALE`).

        `pauses` : les pauses exigées entre les deux séances
        (`Regles.exigences_de_pause`). `separees` : une contrainte d'unicité
        active interdit déjà le chevauchement et l'allongement des plages
        garantit `Regles.pause_garantie()` : il ne reste alors que les pauses
        plus longues et les trajets. Sinon les deux séances peuvent se
        chevaucher ; si elles ne se chevauchent pas, elles respectent les
        pauses et, sur deux campus, le temps de trajet. Comme pour le
        vérificateur, pause et trajet ne concernent que deux séances qui se
        suivent.
        """
        regles = self.regles
        garantie = regles.pause_garantie() if separees else 0
        trajets = regles.active(C.CAMPUS_TRAJET)
        pas = self.preparation.pas
        a_regler = [pause for pause in pauses if pause.duree > garantie]
        par_jour: dict[int, list[tuple[int, Option]]] = defaultdict(list)
        for rang, option in enumerate(options_j):
            par_jour[option.debut // pas].append((rang, option))
        par_nature: dict[Imputation | None, dict[int, list[int]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for rang_i, a in enumerate(options_i):
            for rang_j, b in par_jour.get(a.debut // pas, ()):
                # Minutes libres entre les deux séances ; négatif si elles se chevauchent.
                ecart = max(a.debut, b.debut) - min(a.debut + duree_i, b.debut + duree_j)
                if ecart < 0:
                    continue  # chevauchement : affaire des contraintes d'unicité, pas d'ici
                trop_pres = (
                    trajets
                    and a.campus != b.campus
                    and ecart < self.instantane.trajet(a.campus, b.campus)
                )
                sans_pause = [pause for pause in a_regler if ecart < pause.duree]
                if any(pause.dure for pause in sans_pause) or (
                    trop_pres and regles.dure(C.CAMPUS_TRAJET)
                ):
                    par_nature[None][rang_i].append(rang_j)
                    continue
                for pause in sans_pause:
                    par_nature[pause.violation.imputation][rang_i].append(rang_j)
                if trop_pres:
                    par_nature[C.CAMPUS_TRAJET][rang_i].append(rang_j)
        resultat: dict[Imputation | None, list[tuple[tuple[int, ...], tuple[int, ...]]]] = {}
        for nature, par_rang in par_nature.items():
            groupes: dict[tuple[int, ...], list[int]] = defaultdict(list)
            for rang_i, rangs_j in par_rang.items():
                groupes[tuple(rangs_j)].append(rang_i)
            resultat[nature] = [(tuple(rangs_i), rangs_j) for rangs_j, rangs_i in groupes.items()]
        return resultat

    def _pauses_et_trajets(self) -> None:
        """`CAMPUS_TRAJET`, et les pauses que l'allongement des plages ne garantit pas.

        Une contrainte ou une règle dure interdit les couples de choix ; une
        souple les pénalise : un littéral d'entorse par paire de séances et
        par source, vrai dès qu'un couple fautif est retenu. La pause exigée
        entre deux séances est celle de chacune de leurs personnes communes
        (`Regles.exigences_de_pause`), standard ou règle `PAUSE_MINIMALE`.
        """
        regles = self.regles
        trajets = regles.active(C.CAMPUS_TRAJET)
        unicite_enseignant = regles.active(C.ENSEIGNANT_UNIQUE)
        unicite_groupe = regles.active(C.GROUPE_UNIQUE)
        campus = [frozenset(option.campus for option in options) for options in self.options]
        tous = sorted(frozenset().union(*campus)) if campus else []
        # Trajets qui dépassent la séparation déjà garantie entre deux séances séparées.
        garantie = regles.pause_garantie()
        eloignes = trajets and any(
            self.instantane.trajet(a, b) > garantie for a, b in combinations(tous, 2)
        )
        # Les couples à régler ne dépendent que des débuts, des campus, des durées et des
        # pauses exigées : deux paires aux mêmes choix (à la salle près) partagent le calcul.
        # Les signatures sont numérotées pour que la clé du cache reste petite : il y a des
        # dizaines de milliers de paires sur un grand établissement.
        numeros: dict[tuple, int] = {}
        signatures = [
            numeros.setdefault(tuple((o.debut, o.campus) for o in options), len(numeros))
            for options in self.options
        ]
        cache: dict[tuple, dict] = {}
        nb_contraintes = nb_entorses = 0
        for i, j, enseignant, groupe in self.preparation.paires_de_personnes():
            separees = (enseignant and unicite_enseignant) or (groupe and unicite_groupe)
            trajets_concernes = eloignes and not (len(campus[i]) == 1 and campus[i] == campus[j])
            pauses = regles.exigences_de_pause(self.seances[i].besoin, self.seances[j].besoin)
            seuil = garantie if separees else 0
            pauses_concernees = tuple(pause for pause in pauses if pause.duree > seuil)
            if not trajets_concernes and not pauses_concernees:
                continue
            duree_i, duree_j = self.seances[i].duree, self.seances[j].duree
            cle = (signatures[i], signatures[j], duree_i, duree_j, separees, pauses_concernees)
            couples = cache.get(cle)
            if couples is None:
                couples = self._couples_a_regler(
                    self.options[i], duree_i, self.options[j], duree_j, separees, pauses_concernees
                )
                cache[cle] = couples
            if not couples:
                continue
            litteraux_i, litteraux_j = self.litteraux[i], self.litteraux[j]
            for nature, groupes in couples.items():
                if nature is None:
                    # Les choix d'une séance s'excluent déjà entre eux : « ni l'un de ceux-ci,
                    # ni l'un de ceux-là » s'écrit donc « au plus un » sur leur réunion.
                    for rangs_i, rangs_j in groupes:
                        ensemble = [litteraux_i[r] for r in rangs_i]
                        ensemble += [litteraux_j[r] for r in rangs_j]
                        nb_contraintes += au_plus(self.cp, ensemble, 1)
                    continue
                fautifs = [
                    self._ensemble(
                        [
                            self._au_moins_un([litteraux_i[r] for r in rangs_i]),
                            self._au_moins_un([litteraux_j[r] for r in rangs_j]),
                        ]
                    )
                    for rangs_i, rangs_j in groupes
                ]
                entorse = self._au_moins_un([f for f in fautifs if f is not False])
                if entorse is not False:
                    self._entorses[nature].append((1, entorse))
                    nb_entorses += 1
        journal.debug(
            "Trajets et pauses : %d contrainte(s), %d entorse(s) possible(s)",
            nb_contraintes,
            nb_entorses,
        )

    # -- maxima -----------------------------------------------------------------

    def _maxima_journaliers(self) -> None:
        """`MAX_ENSEIGNANT`, maximum par jour (le maximum par semaine ne dépend pas du placement).

        Contrainte souple : un entier d'entorse par jour et par ensemble de
        semaines, le nombre d'heures entamées au-delà du maximum.
        """
        if not self.regles.active(C.MAX_ENSEIGNANT):
            return
        souple = self.regles.souple(C.MAX_ENSEIGNANT)
        pas = self.preparation.pas
        par_enseignant, _ = self._par_personne()
        for enseignant_id in sorted(par_enseignant):
            maximum = self.instantane.enseignant(enseignant_id).max_minutes_jour
            if maximum is None:
                continue
            ensembles = self.preparation.ensembles_par_semaines(
                par_enseignant[enseignant_id], isoler=False
            )
            for ensemble in ensembles:
                if sum(self.seances[index].duree for index in ensemble) <= maximum:
                    continue
                acquis: dict[int, int] = defaultdict(int)
                termes: dict[int, list[tuple[int, Litteral]]] = defaultdict(list)
                for index in ensemble:
                    duree = self.seances[index].duree
                    for option, litteral in zip(
                        self.options[index], self.litteraux[index], strict=True
                    ):
                        if litteral is True:
                            acquis[option.debut // pas] += duree
                        else:
                            termes[option.debut // pas].append((duree, litteral))
                for jour in sorted(set(acquis) | set(termes)):
                    charge = sum(duree * litteral for duree, litteral in termes[jour])
                    if not souple:
                        if termes[jour]:
                            self.cp.add(charge <= maximum - acquis[jour])
                        elif acquis[jour] > maximum:
                            self.cp.add_bool_or([])  # séances à horaire fixe au-delà du maximum
                        continue
                    pire = acquis[jour] + sum(duree for duree, _ in termes[jour]) - maximum
                    if pire <= 0:
                        continue  # ce jour-là, le maximum ne peut pas être dépassé
                    heures = self.cp.new_int_var(0, -(-pire // 60), "")
                    self.cp.add(charge + acquis[jour] - 60 * heures <= maximum)
                    self._definitions.append(
                        (heures, "surplus", (list(termes[jour]), acquis[jour], maximum, 60))
                    )
                    self._entorses[C.MAX_ENSEIGNANT].append((1, heures))

    # -- pause déjeuner libre bornée -----------------------------------------------

    def _indices_des_emplois(self) -> dict[tuple[int, ...], list[tuple]]:
        """Pour chaque emploi du temps distinct (indices triés), les personnes qui le suivent.

        `Instantane.emplois` : un groupe feuille suit ses séances et celles de
        sa lignée, un enseignant les siennes. Deux personnes au même emploi du
        temps (deux demi-groupes sans séance propre) le partagent.
        """
        index_de = self.index_de
        emplois: dict[tuple[int, ...], list[tuple]] = defaultdict(list)
        for emploi in self.instantane.emplois:
            indices = tuple(sorted(index_de[s] for s in emploi.seances if s in index_de))
            if indices:
                emplois[indices].append(emploi.personne)
        return emplois

    def _designation(self, personne) -> str:
        """« le groupe A1 » ou « Mme Martin », pour le journal."""
        if personne.nature is NaturePersonne.GROUPE:
            return f"le groupe {self.instantane.groupe(personne.id).code}"
        return self.instantane.enseignant(personne.id).nom

    def _pause_dejeuner(self) -> None:
        """Pause déjeuner libre bornée de chaque personne (`DEJEUNER` ou règle `PAUSE_DEJEUNER`).

        Pour une personne, un jour et un ensemble de semaines : l'une des
        **positions de pause** `[p, p + N[` de sa fenêtre `[X, Y[` est libre de
        toute séance. Les positions examinées sont X et les fins possibles de
        ses séances dans `]X, Y - N]` : un intervalle libre d'au moins N
        minutes commence toujours à X ou à la fin d'une séance, l'énumération
        est donc exacte. Un jour sans séance dans la fenêtre n'occupe aucune
        position : la règle y est satisfaite d'elle-même, sans condition
        supplémentaire. Une position qu'aucune séance ne peut recouper la
        satisfait d'avance : rien n'est posé (c'est le cas d'une grille qui
        ménage déjà la pause).

        Chaque personne a son réglage (`Instantane.reglages.dejeuner`) : les
        personnes au même emploi du temps, à la même fenêtre et à la même
        source partagent la contrainte mais comptent chacune pour une
        entorse, imputée à la source (la contrainte `DEJEUNER` ou la règle).
        Le mode fixe est réglé dans les domaines (`Regles.violations_horaire`).

        Taille : un littéral « la séance recoupe la position » par séance et
        ensemble de choix (une implication par choix), partagé entre toutes
        les personnes de la séance ; puis, par personne, jour et position, un
        littéral « occupée » (une implication par séance). Dure : clause « une
        position libre » ; souple : un littéral d'entorse par (personne, jour,
        semaines), vrai si toutes sont occupées.
        """
        regles = self.regles
        reglages = self.instantane.reglages
        # Groupes de personnes : même emploi du temps, même fenêtre, même source.
        groupes: dict[tuple, list] = defaultdict(list)
        for indices, personnes in self._indices_des_emplois().items():
            for personne in personnes:
                reglage = reglages.dejeuner[personne]
                if reglage.fenetre is None:
                    continue
                if reglage.source.regle is None and not regles.active(C.DEJEUNER):
                    continue
                violation = regles.violation_de_source(
                    reglage.source, C.DEJEUNER, "la pause déjeuner"
                )
                groupes[(indices, reglage.fenetre, violation)].append(personne)
        if not groupes:
            return
        pas = self.preparation.pas
        # Choix de chaque séance qui recoupent une fenêtre, par jour : (début, fin, rang du choix).
        fenetres = sorted({fenetre[:2] for _, fenetre, _ in groupes})
        dans_la_fenetre: dict[tuple[int, int, tuple[int, int]], list[tuple[int, int, int]]] = (
            defaultdict(list)
        )
        jours_de: dict[tuple[int, tuple[int, int]], set[int]] = defaultdict(set)
        for index, seance in enumerate(self.seances):
            for rang, option in enumerate(self.options[index]):
                jour, minute = divmod(option.debut, pas)
                for fenetre in fenetres:
                    if se_chevauchent(minute, minute + seance.duree, *fenetre):
                        dans_la_fenetre[(index, jour, fenetre)].append(
                            (minute, minute + seance.duree, rang)
                        )
                        jours_de[(index, fenetre)].add(jour)
        recoupe: dict[tuple[int, tuple[int, ...]], Litteral] = {}

        def seance_sur(index: int, rangs: tuple[int, ...]) -> Litteral:
            """Littéral « la séance `index` est sur l'un de ses choix `rangs` » (partagé)."""
            cle = (index, rangs)
            if cle not in recoupe:
                recoupe[cle] = self._au_moins_un([self.litteraux[index][rang] for rang in rangs])
            return recoupe[cle]

        nb_contraintes = nb_entorses = nb_examines = 0
        for (indices, fenetre, violation), personnes in groupes.items():
            debut_f, fin_f, duree_pause = fenetre
            bornes = (debut_f, fin_f)
            souple = regles.souple_violation(violation)
            for ensemble in self.preparation.ensembles_par_semaines(indices, isoler=False):
                par_jour: dict[int, list[int]] = defaultdict(list)
                for index in ensemble:
                    for jour in jours_de.get((index, bornes), ()):
                        par_jour[jour].append(index)
                for jour in sorted(par_jour):
                    nb_examines += 1
                    choix = {
                        index: dans_la_fenetre[(index, jour, bornes)] for index in par_jour[jour]
                    }
                    positions = sorted(
                        {debut_f}
                        | {
                            fin
                            for liste in choix.values()
                            for _, fin, _ in liste
                            if debut_f < fin <= fin_f - duree_pause
                        }
                    )
                    dessus: list[list[tuple[int, tuple[int, ...]]]] = []
                    for position in positions:
                        sur_la_position = []
                        for index, liste in choix.items():
                            rangs = tuple(
                                rang
                                for debut, fin, rang in liste
                                if se_chevauchent(debut, fin, position, position + duree_pause)
                            )
                            if rangs:
                                sur_la_position.append((index, rangs))
                        dessus.append(sur_la_position)
                    if not all(dessus):
                        continue  # une position qu'aucune séance ne peut recouper : pause garantie
                    occupees = [
                        self._au_moins_un([seance_sur(index, rangs) for index, rangs in liste])
                        for liste in dessus
                    ]
                    # Satisfaite dès qu'une position est libre.
                    exceptions = [occupee.negated() for occupee in occupees if occupee is not True]
                    if souple:
                        penalite = self._sauf_si(exceptions)
                        self._entorses[violation.imputation].append((len(personnes), penalite))
                        nb_entorses += len(personnes)
                    else:
                        self.cp.add_bool_or(exceptions)  # clause vide : impossible, prouvé
                        nb_contraintes += 1
                    journal.debug(
                        "Pause déjeuner libre : %s, jour %d, %d position(s) de pause possibles",
                        ", ".join(self._designation(p) for p in personnes),
                        jour,
                        len(positions),
                    )
        journal.debug(
            "Pause déjeuner libre : %d journée(s) examinée(s), %d contrainte(s), %d entorse(s) "
            "possible(s), %d littéral(aux) de séance",
            nb_examines,
            nb_contraintes,
            nb_entorses,
            len(recoupe),
        )

    # -- règles personnalisées d'emploi du temps et de relation ----------------------

    def _regles_personnalisees(self) -> None:
        """Un poseur par type (`edt.solveur.regles_emploi.PAR_MODELE`) ; les types de sorte
        « séance » et les réglages de personne sont déjà réglés ailleurs."""
        for regle in sorted(self.instantane.regles, key=lambda r: r.id):
            poseur = PAR_MODELE.get(regle.type)
            if poseur is None:
                continue
            avant_contraintes = self.nb_contraintes
            avant_entorses = sum(len(liste) for liste in self._entorses.values())
            poseur(self, regle)
            journal.debug(
                "Règle %s (%s, %s) : %d contrainte(s), %d littéral(aux) d'entorse",
                regle.code,
                regle.type.value,
                regle.niveau.value,
                self.nb_contraintes - avant_contraintes,
                sum(len(liste) for liste in self._entorses.values()) - avant_entorses,
            )

    # -- salles (condition nécessaire) --------------------------------------------

    def _capacite_des_salles(self) -> None:
        """Pas plus de séances en cours que de salles libres capables de les accueillir.

        - la **famille** d'un choix est l'ensemble des salles admissibles de la
          séance sur le campus du choix ;
        - une **classe** réunit les salles qui appartiennent exactement aux
          mêmes familles : elles sont interchangeables pour ce calcul.

        À chaque instant, les séances en cours d'une famille (et de mêmes
        semaines actives) se répartissent entre les classes de la famille, et
        aucune classe ne reçoit, une semaine donnée, plus de séances qu'elle
        n'a de salles disponibles à cet instant. Toute affectation réelle de
        salles donne une telle répartition : la condition est nécessaire, elle
        n'écarte aucune solution. La seconde phase fait l'affectation réelle.
        """
        regles = self.regles
        if not regles.active(C.SALLE_UNIQUE):
            return
        pas = self.preparation.pas
        instants = sorted({option.debut for options in self.options for option in options})

        # Séances en cours par (famille, semaines actives) et par instant.
        familles: dict[tuple[int, tuple[int, ...]], frozenset[int]] = {}
        en_cours: dict[tuple[frozenset[int], int], dict[int, list[Litteral]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for seance in self.seances:
            if seance.index in self.preparation.isolees:
                continue  # verrou toléré : peut partager sa salle avec un autre verrou
            for option, litteral in zip(
                self.options[seance.index], self.litteraux[seance.index], strict=True
            ):
                cle = (option.campus, seance.salles)
                famille = familles.get(cle)
                if famille is None:
                    famille = familles[cle] = frozenset(
                        salle for salle in seance.salles if self._campus_de[salle] == option.campus
                    )
                premier = bisect_left(instants, option.debut)
                dernier = bisect_left(instants, option.debut + seance.duree)
                for rang in range(premier, dernier):
                    en_cours[(famille, seance.semaines)][rang].append(litteral)

        # Classes de salles : mêmes familles.
        toutes = sorted(set(familles.values()), key=sorted)
        signatures: dict[frozenset[int], list[int]] = defaultdict(list)
        for salle in sorted({salle for famille in toutes for salle in famille}):
            signature = frozenset(n for n, famille in enumerate(toutes) if salle in famille)
            signatures[signature].append(salle)
        classes = list(signatures.values())
        classes_de = {
            famille: [n for n, classe in enumerate(classes) if classe[0] in famille]
            for famille in toutes
        }
        fermees: dict[int, list[tuple[int, int]]] = defaultdict(list)
        if regles.dure(C.DISPO_SALLE):
            for salle in self.instantane.salles:
                for plage in regles.recurrentes(CibleIndisponibilite.SALLE, salle.id):
                    base = plage.jour * pas
                    fermees[salle.id].append((base + plage.debut, base + plage.fin))

        # Répartition des séances en cours entre les classes de leur famille.
        # `parts[(classe, instant)]` : (semaines, expression, majorant) par famille.
        parts: dict[tuple[int, int], list[tuple[int, object, int]]] = defaultdict(list)
        nb_variables = 0
        for (famille, semaines), par_instant in en_cours.items():
            for rang, litteraux in par_instant.items():
                total = sum(litteraux)
                if len(classes_de[famille]) == 1:
                    parts[(classes_de[famille][0], rang)].append((semaines, total, len(litteraux)))
                    continue
                repartition = []
                for classe in classes_de[famille]:
                    majorant = min(len(litteraux), len(classes[classe]))
                    part = self.cp.new_int_var(0, majorant, "")
                    repartition.append(part)
                    parts[(classe, rang)].append((semaines, part, majorant))
                self.cp.add(sum(repartition) == total)
                nb_variables += len(repartition)

        # Capacité de chaque classe, pour chaque ensemble de semaines qui se croisent.
        nb_contraintes = 0
        for (classe, rang), entrees in parts.items():
            instant = instants[rang]
            libres = sum(
                1
                for salle in classes[classe]
                if not any(debut <= instant < fin for debut, fin in fermees.get(salle, ()))
            )
            if sum(majorant for _, _, majorant in entrees) <= libres:
                continue
            for ensemble in self._semaines_qui_se_croisent(
                {semaines for semaines, _, _ in entrees}
            ):
                retenues = [entree for entree in entrees if entree[0] in ensemble]
                if sum(majorant for _, _, majorant in retenues) > libres:
                    self.cp.add(sum(expression for _, expression, _ in retenues) <= libres)
                    nb_contraintes += 1
        journal.debug(
            "Capacité des salles : %d famille(s), %d classe(s) de salles, %d variable(s) de "
            "répartition, %d contrainte(s) de capacité",
            len(toutes),
            len(classes),
            nb_variables,
            nb_contraintes,
        )

    @staticmethod
    def _semaines_qui_se_croisent(masques: set[int]) -> list[frozenset[int]]:
        """Ensembles maximaux de masques de semaines qui ont une semaine en commun."""
        tous = 0
        for masque in masques:
            tous |= masque
        ensembles: set[frozenset[int]] = set()
        bit = 0
        while tous >> bit:
            if tous >> bit & 1:
                ensembles.add(frozenset(m for m in masques if m >> bit & 1))
            bit += 1
        return sorted(
            (e for e in ensembles if not any(e < autre for autre in ensembles)), key=sorted
        )

    # -- symétries ----------------------------------------------------------------

    def _classes_de_symetrie(self) -> list[list[int]]:
        """Séances interchangeables : même besoin, mêmes semaines, sans verrou, par rang."""
        classes: dict[tuple[int, int, int], list[int]] = defaultdict(list)
        for seance in self.seances:
            if not seance.verrouillee and len(self.options[seance.index]) > 1:
                cle = (seance.besoin.id, seance.semaines, id(self.options[seance.index]))
                classes[cle].append(seance.index)
        return [
            sorted(indices, key=lambda i: self.seances[i].seance.rang)
            for indices in classes.values()
            if len(indices) >= 2
        ]

    def _briser_les_symetries(self) -> None:
        """Ordonne par début les séances interchangeables d'un même besoin."""
        for classe in self._classes_de_symetrie():
            debuts = [option.debut for option in self.options[classe[0]]]
            expressions = [
                cp_model.LinearExpr.weighted_sum(self.litteraux[index], debuts) for index in classe
            ]
            for avant, apres in zip(expressions, expressions[1:], strict=False):
                self.cp.add(avant <= apres)

    # -- indices ------------------------------------------------------------------

    def _donner_les_indices(self) -> None:
        """Donne le placement précédent de chaque séance comme point de départ.

        Dans une classe de séances interchangeables, les indices sont
        redistribués par début croissant pour rester compatibles avec le
        bris de symétrie.
        """
        indices: dict[int, tuple[int, int]] = {}
        for seance in self.seances:
            indice = seance.seance.indice
            if indice is not None:
                debut = self.preparation.coder(indice.jour, indice.debut)
                indices[seance.index] = (debut, self._campus_de.get(indice.salle_id, -1))
        for classe in self._classes_de_symetrie():
            if all(index in indices for index in classe):
                indices.update(zip(classe, sorted(indices[index] for index in classe), strict=True))
        nb_indices = 0
        for index, (debut, campus) in indices.items():
            options, litteraux = self.options[index], self.litteraux[index]
            candidats = [r for r, option in enumerate(options) if option.debut == debut]
            vise = next((r for r in candidats if options[r].campus == campus), None)
            if vise is None and candidats:
                vise = candidats[0]
            if vise is None or len(options) == 1:
                continue
            for rang, litteral in enumerate(litteraux):
                self.cp.add_hint(litteral, rang == vise)
            nb_indices += 1
        journal.debug("Indices donnés au solveur pour %d séance(s)", nb_indices)

    # -- objectif -----------------------------------------------------------------

    def poser_objectif(self, confort: bool = True) -> bool:
        """Ajoute au modèle l'objectif à minimiser ; renvoie faux s'il est sans effet.

        Toujours : le coût des entorses aux contraintes souples (`Regles.poids`
        par entorse). Avec `confort` : `poids_trous` × trous des groupes +
        `poids_changement_campus` × changements de campus + `poids_samedi` ×
        séances du samedi + `poids_creneaux_extremes` × séances sur le premier
        ou le dernier créneau du jour, le tout compté semaine par semaine
        comme le fait `edt.verification.calculer_indicateurs` (définitions et
        écarts dans `docs/solveur.md`).
        """
        self._termes_entorses = [
            (self.regles.poids_imputation(imputation) * unites, variable)
            for imputation, liste in self._entorses.items()
            for unites, variable in liste
        ]
        if confort:
            self._poser_le_confort()
        termes = [*self._termes_entorses, *self._termes]
        variables = [(poids, variable) for poids, variable in termes if variable is not True]
        if not variables:
            journal.info("Objectif demandé mais sans effet : aucun terme ne dépend du placement.")
            return False
        constante = sum(poids for poids, variable in termes if variable is True)
        self.cp.minimize(sum(poids * variable for poids, variable in variables) + constante)
        self.objectif_actif = True
        journal.debug(
            "Objectif : %d terme(s) variables dont %d d'entorses, constante %d",
            len(variables),
            sum(1 for _, variable in self._termes_entorses if variable is not True),
            constante,
        )
        return True

    def _poser_le_confort(self) -> None:
        """Termes de confort de l'objectif (trous, campus, samedi, créneaux extrêmes)."""
        parametres = self.instantane.parametres
        pas = self.preparation.pas
        creneaux = self._creneaux_par_jour()
        extremes = {
            jour: (min(liste), max(liste, key=lambda c: (c[1], c[0])))
            for jour, liste in creneaux.items()
        }
        samedis: list[tuple[int, Litteral]] = []
        trous: list[tuple[int, Litteral]] = []
        for seance in self.seances:
            nb_semaines = seance.semaines.bit_count()
            for option, litteral in zip(
                self.options[seance.index], self.litteraux[seance.index], strict=True
            ):
                jour, minute = divmod(option.debut, pas)
                poids = 0
                if jour == SAMEDI:
                    poids += parametres.poids_samedi * nb_semaines
                    samedis.append((nb_semaines, litteral))
                if any(
                    se_chevauchent(minute, minute + seance.duree, *creneau)
                    for creneau in extremes.get(jour, ())
                ):
                    poids += parametres.poids_creneaux_extremes * nb_semaines
                if poids:
                    self._termes.append((poids, litteral))

        par_enseignant, _ = self._par_personne()
        emplois_groupes = self._emplois_du_temps_des_groupes()
        if parametres.poids_trous:
            for emploi, nb_groupes in emplois_groupes:
                for actives, nb_semaines in self._configurations(emploi):
                    fois = nb_groupes * nb_semaines
                    for trou in self.trous(actives):
                        trous.append((fois, trou))
                        self._termes.append((parametres.poids_trous * fois, trou))
        if parametres.poids_changement_campus:
            emplois = emplois_groupes + [
                (par_enseignant[e], 1)
                for e in sorted(par_enseignant)
                if len(par_enseignant[e]) >= 2
            ]
            for emploi, nb_personnes in emplois:
                for actives, nb_semaines in self._configurations(emploi):
                    self._termes.extend(
                        (
                            parametres.poids_changement_campus * nb_personnes * nb_semaines,
                            changement,
                        )
                        for changement in self._changements_de_campus(actives)
                    )
        self._compteurs = {
            "trous": trous if parametres.poids_trous else None,
            "samedis": samedis if parametres.poids_samedi else None,
        }

    # -- mesure d'une solution et point de départ ---------------------------------

    def _completer(self, valeurs: list[int | None], depuis: int = 0) -> None:
        """Calcule les variables auxiliaires d'indice au moins `depuis` à partir des autres.

        Chaque variable auxiliaire reçoit sa plus petite valeur permise : « ou »
        vaut vrai si l'un de ses littéraux est vrai, « et » s'ils le sont tous,
        « aucun » si aucun ne l'est ; un « surplus » vaut le nombre d'unités
        entamées au-delà du maximum ; un « depasse » vaut vrai si la somme
        pondérée dépasse le maximum. Les définitions sont dans l'ordre de
        création : leurs littéraux sont toujours déjà connus.
        """

        def vrai(litteral: Litteral) -> bool:
            if litteral is True or litteral is False:
                return litteral
            index = litteral.index
            return bool(valeurs[index]) if index >= 0 else not valeurs[-index - 1]

        for variable, nature, donnees in self._definitions:
            if variable.index < depuis:
                continue
            if nature == "surplus":
                termes, acquis, maximum, unite = donnees
                charge = acquis + sum(duree for duree, litteral in termes if vrai(litteral))
                valeurs[variable.index] = max(0, -(-(charge - maximum) // unite))
                continue
            if nature == "depasse":
                termes, acquis, maximum = donnees
                total = acquis + sum(coef for coef, litteral in termes if vrai(litteral))
                valeurs[variable.index] = int(total > maximum)
                continue
            un_vrai = any(vrai(litteral) for litteral in donnees)
            if nature == "ou":
                valeurs[variable.index] = int(un_vrai)
            elif nature == "et":
                valeurs[variable.index] = int(all(vrai(litteral) for litteral in donnees))
            else:
                valeurs[variable.index] = int(not un_vrai)

    def bilan(self, choix: Sequence[int]) -> Bilan:
        """Qualité de la solution `choix` (rang du choix de chaque séance).

        Les entorses sont comptées dès la construction du modèle ; le confort
        ne l'est qu'une fois `poser_objectif(confort=True)` appelé.
        """
        valeurs: list[int | None] = [None] * self.nb_variables
        for litteraux, rang in zip(self.litteraux, choix, strict=True):
            for position, litteral in enumerate(litteraux):
                if litteral is not True:
                    valeurs[litteral.index] = int(position == rang)
        self._completer(valeurs)

        def valeur(variable: Litteral) -> int:
            return 1 if variable is True else int(valeurs[variable.index] or 0)

        def total(termes: list[tuple[int, Litteral]] | None) -> int | None:
            if termes is None:
                return None
            return sum(poids * valeur(variable) for poids, variable in termes)

        comptes = {
            imputation: nombre
            for imputation, liste in self._entorses.items()
            if (nombre := sum(unites * valeur(variable) for unites, variable in liste)) > 0
        }
        penalite = sum(
            self.regles.poids_imputation(imputation) * unites * valeur(variable)
            for imputation, liste in self._entorses.items()
            for unites, variable in liste
        )
        return Bilan(
            objectif=total(self._termes) or 0,
            trous=total(self._compteurs.get("trous")),
            samedis=total(self._compteurs.get("samedis")),
            entorses={code: n for code, n in comptes.items() if isinstance(code, CodeContrainte)},
            entorses_regles={
                regle_id: n for regle_id, n in comptes.items() if isinstance(regle_id, int)
            },
            penalite=penalite,
        )

    def partir_de(self, solution: Sequence[int]) -> None:
        """Donne une solution du modèle sans objectif comme point de départ complet.

        `solution` porte la valeur de chaque variable du modèle tel qu'il a
        été résolu ; les variables ajoutées depuis (celles de l'objectif) sont
        déduites, et les variables auxiliaires déjà présentes (entorses)
        reprennent leur plus petite valeur permise. L'indice ainsi donné à
        CP-SAT est une solution complète et valide du modèle avec objectif :
        l'amélioration part d'elle.
        """
        valeurs: list[int | None] = [*solution, *([None] * (self.nb_variables - len(solution)))]
        self._completer(valeurs)
        self.cp.clear_hints()
        for index, valeur in enumerate(valeurs):
            self.cp.add_hint(self.cp.get_int_var_from_proto_index(index), valeur)

    def _creneaux_par_jour(self) -> dict[int, list[tuple[int, int]]]:
        par_jour: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for creneau in self.instantane.creneaux:
            par_jour[creneau.jour].append((creneau.debut, creneau.fin))
        return {jour: sorted(creneaux) for jour, creneaux in par_jour.items()}

    def _emplois_du_temps_des_groupes(self) -> list[tuple[list[int], int]]:
        """Emplois du temps des groupes feuilles : `(séances, nombre de groupes qui les suivent)`.

        Définition unique de `Instantane.emplois` : un groupe feuille suit ses
        séances et celles de ses ascendants. Deux demi-groupes sans séance
        propre ont le même emploi du temps, celui de leur classe : il est
        modélisé une fois et compte pour deux, comme dans les indicateurs du
        vérificateur. Seuls les emplois d'au moins deux séances comptent.
        """
        emplois = [
            (list(indices), sum(1 for p in personnes if p.nature is NaturePersonne.GROUPE))
            for indices, personnes in sorted(self._indices_des_emplois().items())
            if len(indices) >= 2
        ]
        return [(emploi, nombre) for emploi, nombre in emplois if nombre]

    def _configurations(self, emploi: Sequence[int]) -> list[tuple[list[int], int]]:
        """Regroupe les semaines du semestre par ensemble de séances actives.

        Renvoie `(séances actives, nombre de semaines)` pour chaque ensemble
        d'au moins deux séances : une situation qui dure n semaines pèse n.
        """
        configurations: dict[tuple[int, ...], int] = defaultdict(int)
        for semaine in self.instantane.semaines:
            actives = tuple(i for i in emploi if self.seances[i].semaines >> semaine.numero & 1)
            if len(actives) >= 2:
                configurations[actives] += 1
        return [(list(actives), nombre) for actives, nombre in sorted(configurations.items())]

    def _trous(
        self, emploi: Sequence[int], creneaux: dict[int, list[tuple[int, int]]]
    ) -> list[cp_model.IntVar]:
        """Un booléen par (jour, créneau) « libre entre deux séances de cet emploi du temps ».

        Un créneau est un trou si une séance finit avant lui, une autre
        commence après lui et aucune ne le recoupe, le même jour.
        """
        pas = self.preparation.pas
        par_jour: dict[int, list[Plage]] = defaultdict(list)
        for index in emploi:
            duree = self.seances[index].duree
            for option, litteral in zip(self.options[index], self.litteraux[index], strict=True):
                jour, minute = divmod(option.debut, pas)
                par_jour[jour].append((minute, minute + duree, litteral))
        trous: list[cp_model.IntVar] = []
        for jour in sorted(par_jour):
            plages = par_jour[jour]
            for debut, fin in creneaux.get(jour, ())[1:-1]:
                avant = [lit for _, f, lit in plages if f <= debut]
                apres = [lit for d, _, lit in plages if d >= fin]
                dessus = [lit for d, f, lit in plages if se_chevauchent(d, f, debut, fin)]
                if not avant or not apres or any(lit is True for lit in dessus):
                    continue
                # Une séance avant et une après, aucune dessus => trou.
                encadre = [
                    lit.negated()
                    for lit in (self._au_moins_un(avant), self._au_moins_un(apres))
                    if lit is not True
                ]
                trous.append(self._sauf_si([*dessus, *encadre]))
        return trous

    def _changements_de_campus(self, emploi: Sequence[int]) -> list[cp_model.IntVar]:
        """Un booléen par (jour, paire de campus) « des séances sur ces deux campus ce jour-là »."""
        pas = self.preparation.pas
        presences: dict[int, dict[int, list[Litteral]]] = defaultdict(lambda: defaultdict(list))
        for index in emploi:
            for option, litteral in zip(self.options[index], self.litteraux[index], strict=True):
                presences[option.debut // pas][option.campus].append(litteral)
        changements: list[cp_model.IntVar] = []
        for jour in sorted(presences):
            par_campus = presences[jour]
            if len(par_campus) < 2:
                continue
            sur = {campus: self._au_moins_un(par_campus[campus]) for campus in sorted(par_campus)}
            for a, b in combinations(sorted(sur), 2):
                # Des séances sur les deux campus => un changement.
                changements.append(
                    self._sauf_si([lit.negated() for lit in (sur[a], sur[b]) if lit is not True])
                )
        return changements
