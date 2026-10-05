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
- **personnes** (`ENSEIGNANT_UNIQUE`, `GROUPE_UNIQUE`, `PAUSE`) : dans tout
  ensemble de séances qui se gênent deux à deux, au plus une séance en cours
  à chaque instant, chaque séance étant allongée de la pause ;
- **trajets** (`CAMPUS_TRAJET`) et pause sans unicité : les couples de choix
  trop rapprochés de deux séances d'une même personne sont interdits ;
- **maxima journaliers** (`MAX_ENSEIGNANT`) ;
- **capacité des salles** (`SALLE_UNIQUE`, condition nécessaire) : à chaque
  instant, les séances qui ne peuvent aller que dans une famille de salles ne
  sont pas plus nombreuses que ces salles. C'est ce qui rend la seconde phase
  presque toujours possible ; quand elle ne l'est pas, elle renvoie les
  séances en cause et `interdire` écarte cette combinaison d'horaires ;
- **symétries** : les séances interchangeables d'un même besoin sont
  ordonnées par début.

Semaines actives : chaque contrainte entre séances est posée par ensemble de
séances actives une même semaine (`Preparation.ensembles_par_semaines`) ;
deux séances aux semaines disjointes ne partagent aucune contrainte.
"""

from __future__ import annotations

import logging
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from itertools import combinations

from ortools.sat.python import cp_model

from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import SAMEDI
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

NB_FAMILLES_MAX = 400
"""Plafond du nombre de familles de salles examinées (simple garde-fou)."""


@dataclass(frozen=True)
class Option:
    """Un choix possible pour une séance : un début codé, un campus et les salles qui vont avec."""

    debut: int
    campus: int
    salles: tuple[int, ...]


def limiter_par_instant(
    cp: cp_model.CpModel,
    plages: Sequence[Plage],
    capacite: int = 1,
    vus: set[tuple[int, tuple[int, ...]]] | None = None,
) -> int:
    """Impose qu'au plus `capacite` des `plages` soient en cours à chaque instant.

    Deux plages se recoupent si et seulement si l'une contient le début de
    l'autre : il suffit d'examiner les instants où une plage peut commencer.
    `vus` évite de poser deux fois la même contrainte. Renvoie le nombre de
    contraintes ajoutées.
    """
    instants = sorted({debut for debut, _, _ in plages})
    en_cours: list[list[Litteral]] = [[] for _ in instants]
    for debut, fin, litteral in plages:
        if litteral is False:
            continue
        for rang in range(bisect_left(instants, debut), bisect_left(instants, fin)):
            en_cours[rang].append(litteral)
    ajoutees = 0
    for litteraux in en_cours:
        acquis = sum(1 for litteral in litteraux if litteral is True)
        variables = [litteral for litteral in litteraux if litteral is not True]
        reste = capacite - acquis
        if len(variables) <= reste:
            continue
        if vus is not None:
            cle = (reste, tuple(sorted(variable.index for variable in variables)))
            if cle in vus:
                continue
            vus.add(cle)
        if reste < 0:
            cp.add_bool_or([])  # déjà trop de séances certaines : aucun emploi du temps possible
        elif reste == 0:
            cp.add_bool_and([variable.negated() for variable in variables])
        elif reste == 1:
            cp.add_at_most_one(variables)
        else:
            cp.add(sum(variables) <= reste)
        ajoutees += 1
    return ajoutees


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
        self._vus: set[tuple[int, tuple[int, ...]]] = set()

        etapes = [
            self._creer_variables,
            self._separer_les_personnes,
            self._pauses_et_trajets,
            self._maxima_journaliers,
            self._capacite_des_salles,
            self._briser_les_symetries,
        ]
        if options.optimiser:
            etapes.append(self._poser_objectif)
        if options.utiliser_indices:
            etapes.append(self._donner_les_indices)
        for etape in etapes:
            etape()
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
            next(
                rang
                for rang, litteral in enumerate(litteraux)
                if litteral is True or valeur(litteral)
            )
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

    def _interdire_ensemble(self, a: Litteral, b: Litteral) -> None:
        """Les deux littéraux ne peuvent pas être vrais ensemble."""
        clause = [litteral.negated() for litteral in (a, b) if litteral is not True]
        self.cp.add_bool_or(clause)

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
        campus_de = {salle.id: salle.campus_id for salle in self.instantane.salles}
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
                            par_campus[campus_de[salle]].append(salle)
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
                litteraux = [
                    self.cp.new_bool_var(f"s{seance.index}_{rang}") for rang in range(len(options))
                ]
                self.cp.add_exactly_one(litteraux)
            self.options.append(options)
            self.litteraux.append(litteraux)

    # -- personnes --------------------------------------------------------------

    def _ensembles_de_groupes(self, par_groupe: dict[int, list[int]]) -> list[list[int]]:
        """Ensembles de séances dont les besoins sont deux à deux en conflit de groupes.

        Le conflit entre groupes n'est pas transitif (deux classes d'une même
        promotion ne se gênent pas, mais gênent toutes deux la promotion). On
        couvre donc le graphe des conflits par des cliques de groupes : chaque
        paire de groupes en conflit est dans une clique au moins, et toutes
        les séances d'une clique se gênent deux à deux.
        """
        utiles = set(par_groupe)
        voisins = {g: (self.instantane.groupes_lies(g) & utiles) - {g} for g in utiles}
        couvertes: set[tuple[int, int]] = set()
        cliques: list[list[int]] = []
        for groupe in sorted(utiles):
            if not voisins[groupe]:
                cliques.append([groupe])
            for autre in sorted(voisins[groupe]):
                if autre < groupe or (groupe, autre) in couvertes:
                    continue
                clique = [groupe, autre]
                for candidat in sorted(voisins[groupe] & voisins[autre]):
                    if all(candidat in voisins[membre] for membre in clique[2:]):
                        clique.append(candidat)
                couvertes.update(combinations(sorted(clique), 2))
                cliques.append(clique)
        return [sorted({i for g in clique for i in par_groupe[g]}) for clique in cliques]

    def _separer_les_personnes(self) -> None:
        """`ENSEIGNANT_UNIQUE`, `GROUPE_UNIQUE` et, par l'allongement des plages, `PAUSE`."""
        regles = self.regles
        par_enseignant, par_groupe = self._par_personne()
        unites: list[list[int]] = []
        if regles.active(C.ENSEIGNANT_UNIQUE):
            unites.extend(par_enseignant[e] for e in sorted(par_enseignant))
        if regles.active(C.GROUPE_UNIQUE):
            unites.extend(self._ensembles_de_groupes(par_groupe))

        ensembles = {
            frozenset(ensemble)
            for unite in unites
            for ensemble in self.preparation.ensembles_par_semaines(unite)
            if len(ensemble) >= 2
        }
        # Un ensemble inclus dans un autre n'apporte rien : on ne garde que les plus grands.
        gardes: list[frozenset[int]] = []
        par_membre: dict[int, list[frozenset[int]]] = defaultdict(list)
        for ensemble in sorted(ensembles, key=lambda e: (-len(e), sorted(e))):
            if any(ensemble <= autre for autre in par_membre[min(ensemble)]):
                continue
            gardes.append(ensemble)
            for index in ensemble:
                par_membre[index].append(ensemble)

        pause = self.instantane.parametres.pause_min if regles.active(C.PAUSE) else 0
        nb_contraintes = 0
        for ensemble in gardes:
            plages = [plage for index in sorted(ensemble) for plage in self._plages(index, pause)]
            nb_contraintes += limiter_par_instant(self.cp, plages, 1, self._vus)
        journal.debug(
            "Personnes : %d ensemble(s) de séances qui se gênent, %d contrainte(s)",
            len(gardes),
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
    ) -> list[tuple[int, int]]:
        """Couples de rangs de choix que deux séances d'une même personne ne peuvent pas cumuler.

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
        interdits: list[tuple[int, int]] = []
        for rang_i, a in enumerate(options_i):
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
                    interdits.append((rang_i, rang_j))
        return interdits

    def _pauses_et_trajets(self) -> None:
        """`CAMPUS_TRAJET`, et `PAUSE` pour les paires que l'unicité ne sépare pas."""
        regles, parametres = self.regles, self.instantane.parametres
        trajets = regles.active(C.CAMPUS_TRAJET)
        pause = parametres.pause_min if regles.active(C.PAUSE) else 0
        unicite_enseignant = regles.active(C.ENSEIGNANT_UNIQUE)
        unicite_groupe = regles.active(C.GROUPE_UNIQUE)
        tout_separe = unicite_enseignant and unicite_groupe
        campus = [frozenset(option.campus for option in options) for options in self.options]
        plusieurs_campus = len(frozenset().union(*campus)) > 1 if campus else False
        if not (trajets and plusieurs_campus) and (tout_separe or pause == 0):
            return
        cache: dict[tuple[int, int, int, int, bool], list[tuple[int, int]]] = {}
        nb_clauses = 0
        for i, j, enseignant, groupe in self.preparation.paires_de_personnes():
            separees = (enseignant and unicite_enseignant) or (groupe and unicite_groupe)
            if separees and (not trajets or (len(campus[i]) == 1 and campus[i] == campus[j])):
                continue
            duree_i, duree_j = self.seances[i].duree, self.seances[j].duree
            cle = (id(self.options[i]), id(self.options[j]), duree_i, duree_j, separees)
            interdits = cache.get(cle)
            if interdits is None:
                interdits = self._choix_incompatibles(
                    self.options[i], duree_i, self.options[j], duree_j, separees, pause, trajets
                )
                cache[cle] = interdits
            for rang_i, rang_j in interdits:
                self._interdire_ensemble(self.litteraux[i][rang_i], self.litteraux[j][rang_j])
            nb_clauses += len(interdits)
        journal.debug("Trajets et pauses : %d couple(s) de choix interdit(s)", nb_clauses)

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
        """Pas plus de séances en cours que de salles capables de les accueillir.

        Une **famille** est un ensemble de salles : celles d'un choix, ou la
        réunion de familles qui ont une salle en commun. Pour chaque famille
        et à chaque instant, les séances dont le choix ne permet que des
        salles de la famille ne sont pas plus nombreuses que ces salles
        (condition de Hall). Cette condition est nécessaire : elle n'écarte
        aucune solution. Elle garantit qu'à chaque instant pris isolément les
        salles suffisent ; la seconde phase fait l'affectation réelle.
        """
        if not self.regles.active(C.SALLE_UNIQUE):
            return
        ensembles: dict[tuple[int, ...], frozenset[int]] = {}
        par_domaine: dict[frozenset[int], dict[int, list[int]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for seance in self.seances:
            if seance.index in self.preparation.isolees:
                continue  # verrou toléré : peut partager sa salle avec un autre verrou
            for rang, option in enumerate(self.options[seance.index]):
                domaine = ensembles.get(option.salles)
                if domaine is None:
                    domaine = ensembles[option.salles] = frozenset(option.salles)
                par_domaine[domaine][seance.index].append(rang)

        familles = set(par_domaine)
        a_traiter = sorted(familles, key=sorted)
        while a_traiter and len(familles) < NB_FAMILLES_MAX:
            famille = a_traiter.pop()
            for autre in sorted(familles, key=sorted):
                reunion = famille | autre
                if reunion not in familles and not famille.isdisjoint(autre):
                    familles.add(reunion)
                    a_traiter.append(reunion)
        if a_traiter:
            journal.warning(
                "Capacité des salles : plus de %d familles de salles, les réunions restantes "
                "ne sont pas examinées (la seconde phase reste exacte).",
                NB_FAMILLES_MAX,
            )

        nb_contraintes = 0
        for famille in sorted(familles, key=lambda f: (len(f), sorted(f))):
            rangs: dict[int, list[int]] = defaultdict(list)
            for domaine, par_seance in par_domaine.items():
                if domaine <= famille:
                    for index, liste in par_seance.items():
                        rangs[index].extend(liste)
            if len(rangs) <= len(famille):
                continue
            for ensemble in self.preparation.ensembles_par_semaines(rangs, isoler=False):
                if len(ensemble) <= len(famille):
                    continue
                plages = [
                    plage for index in ensemble for plage in self._plages(index, rangs=rangs[index])
                ]
                nb_contraintes += limiter_par_instant(self.cp, plages, len(famille), self._vus)
        journal.debug(
            "Capacité des salles : %d famille(s), %d contrainte(s)", len(familles), nb_contraintes
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
        campus_de = {salle.id: salle.campus_id for salle in self.instantane.salles}
        indices: dict[int, tuple[int, int | None]] = {}
        for seance in self.seances:
            indice = seance.seance.indice
            if indice is not None:
                debut = self.preparation.coder(indice.jour, indice.debut)
                indices[seance.index] = (debut, campus_de.get(indice.salle_id))
        for classe in self._classes_de_symetrie():
            if all(index in indices for index in classe):
                tries = sorted(
                    (indices[index] for index in classe), key=lambda c: (c[0], c[1] or 0)
                )
                indices.update(zip(classe, tries, strict=True))
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

    def _poser_objectif(self) -> None:
        """Objectif de qualité à minimiser (définitions et écarts dans `docs/solveur.md`).

        `poids_trous` × trous des groupes + `poids_changement_campus` ×
        changements de campus + `poids_samedi` × séances du samedi +
        `poids_creneaux_extremes` × séances sur le premier ou le dernier
        créneau du jour, le tout compté semaine par semaine comme le fait
        `edt.verification.calculer_indicateurs`.
        """
        parametres = self.instantane.parametres
        pas = self.preparation.pas
        creneaux = self._creneaux_par_jour()
        termes: list = []
        constante = 0
        for seance in self.seances:
            nb_semaines = seance.semaines.bit_count()
            for option, litteral in zip(
                self.options[seance.index], self.litteraux[seance.index], strict=True
            ):
                jour, minute = divmod(option.debut, pas)
                poids = 0
                if jour == SAMEDI:
                    poids += parametres.poids_samedi * nb_semaines
                if jour in creneaux and any(
                    se_chevauchent(minute, minute + seance.duree, *creneau)
                    for creneau in (
                        min(creneaux[jour]),
                        max(creneaux[jour], key=lambda c: (c[1], c[0])),
                    )
                ):
                    poids += parametres.poids_creneaux_extremes * nb_semaines
                if poids and litteral is True:
                    constante += poids
                elif poids:
                    termes.append(poids * litteral)

        par_enseignant, par_groupe = self._par_personne()
        emplois_groupes = self._emplois_du_temps_des_groupes(par_groupe)
        if parametres.poids_trous:
            for emploi in emplois_groupes:
                for actives, nb_semaines in self._configurations(emploi):
                    termes.extend(
                        parametres.poids_trous * nb_semaines * trou
                        for trou in self._trous(actives, creneaux)
                    )
        if parametres.poids_changement_campus:
            emplois = emplois_groupes + [
                par_enseignant[e] for e in sorted(par_enseignant) if len(par_enseignant[e]) >= 2
            ]
            for emploi in emplois:
                for actives, nb_semaines in self._configurations(emploi):
                    termes.extend(
                        parametres.poids_changement_campus * nb_semaines * changement
                        for changement in self._changements_de_campus(actives)
                    )
        if not termes:
            journal.info("Objectif demandé mais sans effet : aucun terme ne dépend du placement.")
            return
        self.cp.minimize(sum(termes) + constante)
        self.objectif_actif = True
        journal.debug("Objectif : %d terme(s) variables, constante %d", len(termes), constante)

    def _creneaux_par_jour(self) -> dict[int, list[tuple[int, int]]]:
        par_jour: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for creneau in self.instantane.creneaux:
            par_jour[creneau.jour].append((creneau.debut, creneau.fin))
        return {jour: sorted(creneaux) for jour, creneaux in par_jour.items()}

    def _emplois_du_temps_des_groupes(self, par_groupe: dict[int, list[int]]) -> list[list[int]]:
        """Séances vues par chaque groupe feuille : les siennes et celles de ses ascendants.

        Un groupe feuille n'est le parent d'aucun autre groupe (même
        définition que le vérificateur).
        """
        parents = {groupe.id: groupe.parent_id for groupe in self.instantane.groupes}
        avec_enfants = set(parents.values())
        emplois: set[tuple[int, ...]] = set()
        for groupe_id in sorted(parents):
            if groupe_id in avec_enfants:
                continue
            lignee, courant = [groupe_id], parents[groupe_id]
            while courant is not None and courant not in lignee:
                lignee.append(courant)
                courant = parents.get(courant)
            emploi = tuple(sorted({i for g in lignee for i in par_groupe.get(g, ())}))
            if len(emploi) >= 2:
                emplois.add(emploi)
        return [list(emploi) for emploi in sorted(emplois)]

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
        par_jour: dict[int, list[tuple[int, int, Litteral]]] = defaultdict(list)
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
                trou = self.cp.new_bool_var("")
                # Une séance avant et une après, aucune dessus => trou.
                un_avant = self._au_moins_un(avant)
                un_apres = self._au_moins_un(apres)
                clause = [trou, *dessus]
                clause.extend(lit.negated() for lit in (un_avant, un_apres) if lit is not True)
                self.cp.add_bool_or(clause)
                trous.append(trou)
        return trous

    def _au_moins_un(self, litteraux: Sequence[Litteral]) -> Litteral:
        """Littéral impliqué par chacun des `litteraux` (vrai dès que l'un d'eux l'est).

        Un seul sens suffit : ces littéraux n'apparaissent que dans des
        pénalités, que la minimisation pousse déjà vers « faux ».
        """
        if any(litteral is True for litteral in litteraux):
            return True
        if len(litteraux) == 1:
            return litteraux[0]
        resultat = self.cp.new_bool_var("")
        for litteral in litteraux:
            self.cp.add_implication(litteral, resultat)
        return resultat

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
                changement = self.cp.new_bool_var("")
                clause = [changement]
                clause.extend(lit.negated() for lit in (sur[a], sur[b]) if lit is not True)
                self.cp.add_bool_or(clause)
                changements.append(changement)
        return changements
