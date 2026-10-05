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
from dataclasses import dataclass
from itertools import combinations

from ortools.sat.python import cp_model

from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import SAMEDI, CibleIndisponibilite
from edt.domaine.heures import se_chevauchent
from edt.erreurs import ErreurEDT
from edt.solveur.contrat import OptionsSolveur
from edt.solveur.preparation import Preparation

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
    """Qualité d'une solution d'horaires, comptée semaine par semaine sur le semestre.

    - `objectif` : valeur de l'objectif (somme pondérée des quatre termes) ;
    - `trous` : créneaux libres entre deux séances d'un groupe (`None` si le
      poids des trous est nul : ils ne sont alors pas comptés) ;
    - `samedis` : séances placées le samedi (`None` si leur poids est nul).
    """

    objectif: int
    trous: int | None
    samedis: int | None


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
        # Variables auxiliaires déduites des choix : (variable, "ou" ou "aucun", littéraux).
        self._definitions: list[tuple[cp_model.IntVar, str, list[Litteral]]] = []
        # Objectif : (coefficient, littéral) ; compteurs lisibles : (nombre de semaines, littéral).
        self._termes: list[tuple[int, Litteral]] = []
        self._compteurs: dict[str, list[tuple[int, Litteral]] | None] = {}
        self._campus_de = {salle.id: salle.campus_id for salle in self.instantane.salles}

        etapes = [
            self._creer_variables,
            self._separer_les_personnes,
            self._pauses_et_trajets,
            self._maxima_journaliers,
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
        pause = self.instantane.parametres.pause_min if regles.active(C.PAUSE) else 0

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

    def _choix_incompatibles(
        self,
        options_i: tuple[Option, ...],
        duree_i: int,
        options_j: tuple[Option, ...],
        duree_j: int,
        separees: bool,
        pause: int,
        trajets: bool,
    ) -> list[tuple[tuple[int, ...], tuple[int, ...]]]:
        """Choix que deux séances d'une même personne ne peuvent pas cumuler.

        Renvoie des couples `(rangs de la première, rangs de la seconde)` :
        aucun choix du premier groupe n'est compatible avec un choix du second.

        `separees` : une contrainte d'unicité active interdit déjà le
        chevauchement et impose la pause ; il ne reste que les trajets. Sinon
        (unicité désactivée) les deux séances peuvent se chevaucher, mais pas
        se suivre de trop près, ni être sur deux campus sans le temps de
        trajet (chevauchement compris).
        """
        pas = self.preparation.pas
        par_jour: dict[int, list[tuple[int, Option]]] = defaultdict(list)
        for rang, option in enumerate(options_j):
            par_jour[option.debut // pas].append((rang, option))
        interdits: dict[tuple[int, ...], list[int]] = defaultdict(list)
        for rang_i, a in enumerate(options_i):
            incompatibles = []
            for rang_j, b in par_jour.get(a.debut // pas, ()):
                # Minutes libres entre les deux séances ; négatif si elles se chevauchent.
                ecart = max(a.debut, b.debut) - min(a.debut + duree_i, b.debut + duree_j)
                trop_pres = (
                    trajets
                    and a.campus != b.campus
                    and ecart < self.instantane.trajet(a.campus, b.campus)
                )
                if separees:
                    interdit = ecart >= pause and trop_pres
                else:
                    interdit = 0 <= ecart < pause or trop_pres
                if interdit:
                    incompatibles.append(rang_j)
            if incompatibles:
                interdits[tuple(incompatibles)].append(rang_i)
        return [(tuple(rangs_i), rangs_j) for rangs_j, rangs_i in interdits.items()]

    def _pauses_et_trajets(self) -> None:
        """`CAMPUS_TRAJET`, et `PAUSE` pour les paires que l'unicité ne sépare pas."""
        regles, parametres = self.regles, self.instantane.parametres
        trajets = regles.active(C.CAMPUS_TRAJET)
        pause = parametres.pause_min if regles.active(C.PAUSE) else 0
        unicite_enseignant = regles.active(C.ENSEIGNANT_UNIQUE)
        unicite_groupe = regles.active(C.GROUPE_UNIQUE)
        tout_separe = unicite_enseignant and unicite_groupe
        campus = [frozenset(option.campus for option in options) for options in self.options]
        tous = sorted(frozenset().union(*campus)) if campus else []
        # Trajets qui dépassent la pause déjà garantie entre deux séances séparées.
        eloignes = trajets and any(
            self.instantane.trajet(a, b) > pause for a, b in combinations(tous, 2)
        )
        if not eloignes and (tout_separe or pause == 0):
            return
        cache: dict[
            tuple[int, int, int, int, bool], list[tuple[tuple[int, ...], tuple[int, ...]]]
        ] = {}
        nb_contraintes = 0
        for i, j, enseignant, groupe in self.preparation.paires_de_personnes():
            separees = (enseignant and unicite_enseignant) or (groupe and unicite_groupe)
            if separees and (not eloignes or (len(campus[i]) == 1 and campus[i] == campus[j])):
                continue
            duree_i, duree_j = self.seances[i].duree, self.seances[j].duree
            cle = (id(self.options[i]), id(self.options[j]), duree_i, duree_j, separees)
            interdits = cache.get(cle)
            if interdits is None:
                interdits = self._choix_incompatibles(
                    self.options[i], duree_i, self.options[j], duree_j, separees, pause, trajets
                )
                cache[cle] = interdits
            # Les choix d'une séance s'excluent déjà entre eux : « ni l'un de ceux-ci, ni
            # l'un de ceux-là » s'écrit donc « au plus un » sur leur réunion.
            litteraux_i, litteraux_j = self.litteraux[i], self.litteraux[j]
            for rangs_i, rangs_j in interdits:
                ensemble = [litteraux_i[r] for r in rangs_i] + [litteraux_j[r] for r in rangs_j]
                nb_contraintes += au_plus(self.cp, ensemble, 1)
        journal.debug("Trajets et pauses : %d contrainte(s)", nb_contraintes)

    # -- maxima -----------------------------------------------------------------

    def _maxima_journaliers(self) -> None:
        """`MAX_ENSEIGNANT`, maximum par jour (le maximum par semaine ne dépend pas du placement)."""
        if not self.regles.active(C.MAX_ENSEIGNANT):
            return
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
                termes: dict[int, list] = defaultdict(list)
                for index in ensemble:
                    duree = self.seances[index].duree
                    for option, litteral in zip(
                        self.options[index], self.litteraux[index], strict=True
                    ):
                        if litteral is True:
                            acquis[option.debut // pas] += duree
                        else:
                            termes[option.debut // pas].append(duree * litteral)
                for jour in sorted(set(acquis) | set(termes)):
                    if termes[jour]:
                        self.cp.add(sum(termes[jour]) <= maximum - acquis[jour])
                    elif acquis[jour] > maximum:
                        self.cp.add_bool_or([])  # séances à horaire fixe au-delà du maximum

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
        if regles.active(C.DISPO_SALLE):
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

    def poser_objectif(self) -> bool:
        """Ajoute au modèle l'objectif de qualité à minimiser ; renvoie faux s'il est sans effet.

        `poids_trous` × trous des groupes + `poids_changement_campus` ×
        changements de campus + `poids_samedi` × séances du samedi +
        `poids_creneaux_extremes` × séances sur le premier ou le dernier
        créneau du jour, le tout compté semaine par semaine comme le fait
        `edt.verification.calculer_indicateurs` (définitions et écarts dans
        `docs/solveur.md`).
        """
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

        par_enseignant, par_groupe = self._par_personne()
        emplois_groupes = self._emplois_du_temps_des_groupes(par_groupe)
        if parametres.poids_trous:
            for emploi, nb_groupes in emplois_groupes:
                for actives, nb_semaines in self._configurations(emploi):
                    fois = nb_groupes * nb_semaines
                    for trou in self._trous(actives, creneaux):
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
        variables = [(poids, litteral) for poids, litteral in self._termes if litteral is not True]
        if not variables:
            journal.info("Objectif demandé mais sans effet : aucun terme ne dépend du placement.")
            return False
        constante = sum(poids for poids, litteral in self._termes if litteral is True)
        self.cp.minimize(sum(poids * litteral for poids, litteral in variables) + constante)
        self.objectif_actif = True
        journal.debug("Objectif : %d terme(s) variables, constante %d", len(variables), constante)
        return True

    # -- mesure d'une solution et point de départ ---------------------------------

    def _completer(self, valeurs: list[int | None], depuis: int = 0) -> None:
        """Calcule les variables auxiliaires d'indice au moins `depuis` à partir des autres.

        Chaque variable auxiliaire reçoit sa plus petite valeur permise : « ou »
        vaut vrai si l'un de ses littéraux est vrai, « aucun » si aucun ne
        l'est. Les définitions sont dans l'ordre de création : leurs
        littéraux sont toujours déjà connus.
        """

        def vrai(litteral: Litteral) -> bool:
            if litteral is True or litteral is False:
                return litteral
            index = litteral.index
            return bool(valeurs[index]) if index >= 0 else not valeurs[-index - 1]

        for variable, nature, litteraux in self._definitions:
            if variable.index >= depuis:
                un_vrai = any(vrai(litteral) for litteral in litteraux)
                valeurs[variable.index] = int(un_vrai if nature == "ou" else not un_vrai)

    def bilan(self, choix: Sequence[int]) -> Bilan:
        """Qualité de la solution `choix` (rang du choix de chaque séance), objectif posé."""
        valeurs: list[int | None] = [None] * self.nb_variables
        for litteraux, rang in zip(self.litteraux, choix, strict=True):
            for position, litteral in enumerate(litteraux):
                if litteral is not True:
                    valeurs[litteral.index] = int(position == rang)
        self._completer(valeurs)

        def total(termes: list[tuple[int, Litteral]] | None) -> int | None:
            if termes is None:
                return None
            return sum(
                poids for poids, litteral in termes if litteral is True or valeurs[litteral.index]
            )

        return Bilan(
            objectif=total(self._termes) or 0,
            trous=total(self._compteurs.get("trous")),
            samedis=total(self._compteurs.get("samedis")),
        )

    def partir_de(self, solution: Sequence[int]) -> None:
        """Donne une solution du modèle sans objectif comme point de départ complet.

        `solution` porte la valeur de chaque variable du modèle tel qu'il a
        été résolu ; les variables ajoutées depuis (celles de l'objectif) sont
        déduites. L'indice ainsi donné à CP-SAT est une solution complète et
        valide du modèle avec objectif : l'amélioration part d'elle.
        """
        valeurs: list[int | None] = [*solution, *([None] * (self.nb_variables - len(solution)))]
        self._completer(valeurs, depuis=len(solution))
        self.cp.clear_hints()
        for index, valeur in enumerate(valeurs):
            self.cp.add_hint(self.cp.get_int_var_from_proto_index(index), valeur)

    def _creneaux_par_jour(self) -> dict[int, list[tuple[int, int]]]:
        par_jour: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for creneau in self.instantane.creneaux:
            par_jour[creneau.jour].append((creneau.debut, creneau.fin))
        return {jour: sorted(creneaux) for jour, creneaux in par_jour.items()}

    def _emplois_du_temps_des_groupes(
        self, par_groupe: dict[int, list[int]]
    ) -> list[tuple[list[int], int]]:
        """Emplois du temps des groupes feuilles : `(séances, nombre de groupes qui les suivent)`.

        Un groupe feuille n'est le parent d'aucun autre groupe (même
        définition que le vérificateur) ; il suit ses séances et celles de
        ses ascendants. Deux demi-groupes sans séance propre ont le même
        emploi du temps, celui de leur classe : il est modélisé une fois et
        compte pour deux, comme dans les indicateurs du vérificateur.
        """
        parents = {groupe.id: groupe.parent_id for groupe in self.instantane.groupes}
        avec_enfants = set(parents.values())
        emplois: dict[tuple[int, ...], int] = defaultdict(int)
        for groupe_id in sorted(parents):
            if groupe_id in avec_enfants:
                continue
            lignee, courant = [groupe_id], parents[groupe_id]
            while courant is not None and courant not in lignee:
                lignee.append(courant)
                courant = parents.get(courant)
            emploi = tuple(sorted({i for g in lignee for i in par_groupe.get(g, ())}))
            if len(emploi) >= 2:
                emplois[emploi] += 1
        return [(list(emploi), nombre) for emploi, nombre in sorted(emplois.items())]

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
