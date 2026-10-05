"""Modèle CP-SAT de la semaine type.

Formulation (détail et justification dans `docs/solveur.md`)
------------------------------------------------------------
Par séance :

- une variable entière **début**, dont le domaine est la liste des débuts
  admissibles calculée par `edt.solveur.preparation` (codés
  `jour * pas + minute`) ;
- une variable **salle** au domaine pré-filtré, représentée par un booléen
  par salle admissible (« un parmi n ») : c'est la forme que demandent les
  intervalles optionnels du non-chevauchement par salle.

Contraintes :

- non-chevauchement **par personne** (`ENSEIGNANT_UNIQUE`, `GROUPE_UNIQUE`) :
  un intervalle par séance, allongé de la pause (`PAUSE`), et une contrainte
  `NoOverlap` par ensemble de séances qui se gênent deux à deux ;
- non-chevauchement **par salle** (`SALLE_UNIQUE`) : un intervalle optionnel
  par (séance, salle admissible) et un `NoOverlap` par salle ;
- **semaines actives** : un `NoOverlap` ne réunit que des séances actives une
  même semaine (un ensemble par semaine, doublons et sous-ensembles retirés).
  Deux séances aux semaines disjointes ne partagent donc aucune contrainte ;
- trajet entre campus (`CAMPUS_TRAJET`), maxima journaliers
  (`MAX_ENSEIGNANT`), indisponibilités de salle partielles (`DISPO_SALLE`) ;
- bris de symétrie : les séances interchangeables d'un même besoin sont
  ordonnées par début.

Les autres contraintes sont déjà dans les domaines. L'objectif, facultatif,
est décrit dans `_Objectif`.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from itertools import combinations

from ortools.sat.python import cp_model

from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import SAMEDI, NatureGroupe
from edt.domaine.heures import se_chevauchent
from edt.domaine.instantane import Placement
from edt.solveur.contrat import OptionsSolveur
from edt.solveur.preparation import Preparation

journal = logging.getLogger(__name__)

C = CodeContrainte

Litteral = cp_model.IntVar | bool
"""Booléen du modèle, ou constante Python quand la valeur est déjà connue."""


class Modele:
    """Modèle CP-SAT d'un calcul préparé (voir l'en-tête du module).

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
        self.debuts: list[cp_model.IntVar | int] = []
        self.salles: list[dict[int, Litteral]] = []
        self.objectif_actif = False
        self._intervalles_personne: dict[int, cp_model.IntervalVar] = {}
        self._appartenances: dict[tuple[int, tuple[int, ...]], Litteral] = {}
        self._campus: dict[tuple[int, int], Litteral] = {}
        self._campus_des_salles = {salle.id: salle.campus_id for salle in self.instantane.salles}
        self._minutes_par_jour: list[dict[int, list[int]]] = []
        for seance in self.seances:
            par_jour: dict[int, list[int]] = defaultdict(list)
            for code in seance.debuts:
                jour, minute = preparation.decoder(code)
                par_jour[jour].append(minute)
            self._minutes_par_jour.append(par_jour)

        etapes = [
            self._creer_variables,
            self._non_chevauchement_des_personnes,
            self._non_chevauchement_des_salles,
            self._pauses_et_trajets,
            self._maxima_journaliers,
            self._briser_les_symetries,
        ]
        if options.optimiser:
            etapes.append(self._poser_objectif)
        if options.utiliser_indices:
            etapes.append(self._donner_les_indices)
        for etape in etapes:
            etape()
            point_de_controle()

    # -- lecture ----------------------------------------------------------------

    @property
    def nb_variables(self) -> int:
        """Nombre de variables du modèle."""
        return len(self.cp.proto.variables)

    @property
    def nb_contraintes(self) -> int:
        """Nombre de contraintes du modèle."""
        return len(self.cp.proto.constraints)

    def placements(self, solveur: cp_model.CpSolver) -> tuple[Placement, ...]:
        """Placements de la solution courante de `solveur`, dans l'ordre des séances."""
        placements = []
        for seance, debut, salles in zip(self.seances, self.debuts, self.salles, strict=True):
            code = debut if isinstance(debut, int) else solveur.value(debut)
            jour, minute = self.preparation.decoder(code)
            salle_id = next(
                salle
                for salle, litteral in salles.items()
                if litteral is True or solveur.boolean_value(litteral)
            )
            placements.append(
                Placement(seance.seance.id, jour, minute, minute + seance.duree, salle_id)
            )
        return tuple(placements)

    # -- outils -----------------------------------------------------------------

    def _exiger(self, condition, si: Sequence[Litteral] = ()) -> None:
        """Ajoute `condition` au modèle, seulement quand tous les littéraux `si` sont vrais.

        `condition` est une inégalité linéaire, ou un booléen Python quand les
        deux membres sont déjà connus (séances à horaire fixe).
        """
        if any(litteral is False for litteral in si) or condition is True:
            return
        variables = [litteral for litteral in si if litteral is not True]
        if condition is False:
            # Impossible sous ces conditions : au moins une doit être fausse
            # (liste vide = modèle infaisable, ce que CP-SAT prouve aussitôt).
            self.cp.add_bool_or([litteral.negated() for litteral in variables])
            return
        contrainte = self.cp.add(condition)
        if variables:
            contrainte.only_enforce_if(variables)

    def _appartient(self, index: int, valeurs: Iterable[int]) -> Litteral:
        """Littéral « le début de la séance `index` est l'une des `valeurs` »."""
        domaine = self.seances[index].debuts
        ensemble = set(valeurs)
        dedans = tuple(code for code in domaine if code in ensemble)
        if not dedans:
            return False
        if len(dedans) == len(domaine):
            return True
        cle = (index, dedans)
        litteral = self._appartenances.get(cle)
        if litteral is None:
            litteral = self.cp.new_bool_var(f"dans_{index}_{len(self._appartenances)}")
            dehors = [code for code in domaine if code not in ensemble]
            debut = self.debuts[index]
            self.cp.add_linear_expression_in_domain(
                debut, cp_model.Domain.from_values(list(dedans))
            ).only_enforce_if(litteral)
            self.cp.add_linear_expression_in_domain(
                debut, cp_model.Domain.from_values(dehors)
            ).only_enforce_if(litteral.negated())
            self._appartenances[cle] = litteral
        return litteral

    def _le_jour(self, index: int, jour: int) -> Litteral:
        """Littéral « la séance `index` a lieu le jour `jour` »."""
        minutes = self._minutes_par_jour[index].get(jour)
        if not minutes:
            return False
        return self._appartient(index, (self.preparation.coder(jour, m) for m in minutes))

    def _sur_le_campus(self, index: int, campus_id: int) -> Litteral:
        """Littéral « la séance `index` est dans une salle du campus `campus_id` »."""
        cle = (index, campus_id)
        litteral = self._campus.get(cle)
        if litteral is None:
            salles = self.salles[index]
            dedans = [lit for s, lit in salles.items() if self._campus_des_salles[s] == campus_id]
            if not dedans:
                litteral = False
            elif len(dedans) == len(salles):
                litteral = True
            else:
                litteral = self.cp.new_bool_var(f"campus_{index}_{campus_id}")
                self.cp.add(sum(dedans) == litteral)
            self._campus[cle] = litteral
        return litteral

    def _campus_possibles(self, index: int) -> list[int]:
        return sorted({self._campus_des_salles[salle] for salle in self.salles[index]})

    def _par_semaines(self, indices: Sequence[int], isoler: bool = True) -> list[list[int]]:
        """Découpe des séances en ensembles maximaux actifs une même semaine.

        C'est ici que les semaines actives sont traitées exactement : deux
        séances sont dans un même ensemble si et seulement si une semaine au
        moins les réunit. Avec `isoler`, une séance verrouillée « isolée »
        (violations héritées tolérées) n'est réunie qu'aux séances non
        verrouillées.
        """
        par_masque: dict[int, list[int]] = defaultdict(list)
        for index in indices:
            par_masque[self.seances[index].semaines].append(index)
        if len(par_masque) <= 1:
            ensembles = [sorted(indices)] if indices else []
        else:
            tous = 0
            for masque in par_masque:
                tous |= masque
            signatures: set[frozenset[int]] = set()
            bit = 0
            while tous >> bit:
                if tous >> bit & 1:
                    signatures.add(frozenset(m for m in par_masque if m >> bit & 1))
                bit += 1
            maximales = [s for s in signatures if not any(s < autre for autre in signatures)]
            ensembles = sorted(sorted(i for m in s for i in par_masque[m]) for s in maximales)
        isolees = self.preparation.isolees
        if not isoler or not isolees:
            return ensembles
        resultat = []
        for ensemble in ensembles:
            a_part = [i for i in ensemble if i in isolees]
            resultat.append([i for i in ensemble if i not in isolees])
            libres = [i for i in ensemble if not self.seances[i].verrouillee]
            resultat.extend(libres + [i] for i in a_part)
        return resultat

    # -- variables --------------------------------------------------------------

    def _creer_variables(self) -> None:
        cp = self.cp
        for seance in self.seances:
            nom = seance.seance.id
            if len(seance.debuts) == 1:
                debut: cp_model.IntVar | int = seance.debuts[0]
            else:
                debut = cp.new_int_var_from_domain(
                    cp_model.Domain.from_values(list(seance.debuts)), f"debut_{nom}"
                )
            if len(seance.salles) == 1:
                salles: dict[int, Litteral] = {seance.salles[0]: True}
            else:
                salles = {s: cp.new_bool_var(f"salle_{nom}_{s}") for s in seance.salles}
                cp.add_exactly_one(salles.values())
            # DISPO_SALLE : une salle indisponible à certains débuts seulement.
            for salle_id, interdits in seance.debuts_interdits.items():
                permis = [code for code in seance.debuts if code not in interdits]
                cp.add_linear_expression_in_domain(
                    debut, cp_model.Domain.from_values(permis)
                ).only_enforce_if(salles[salle_id])
            self.debuts.append(debut)
            self.salles.append(salles)

    # -- personnes --------------------------------------------------------------

    def _ensembles_de_groupes(self) -> list[list[int]]:
        """Ensembles de séances dont les besoins sont deux à deux en conflit de groupes.

        Le conflit entre groupes n'est pas transitif (deux classes d'une même
        promotion ne se gênent pas, mais gênent toutes deux la promotion). On
        couvre donc le graphe des conflits par des cliques de groupes : chaque
        paire de groupes en conflit est dans une clique au moins, et toutes
        les séances d'une clique se gênent deux à deux.
        """
        par_groupe: dict[int, list[int]] = defaultdict(list)
        for seance in self.seances:
            for groupe in seance.besoin.groupes:
                par_groupe[groupe].append(seance.index)
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

    def _non_chevauchement_des_personnes(self) -> None:
        """`ENSEIGNANT_UNIQUE`, `GROUPE_UNIQUE` et, dans les mêmes intervalles, `PAUSE`."""
        regles = self.regles
        unites: list[list[int]] = []
        if regles.active(C.ENSEIGNANT_UNIQUE):
            par_enseignant: dict[int, list[int]] = defaultdict(list)
            for seance in self.seances:
                for enseignant in regles.enseignants_reels(seance.besoin):
                    par_enseignant[enseignant].append(seance.index)
            unites.extend(par_enseignant[e] for e in sorted(par_enseignant))
        if regles.active(C.GROUPE_UNIQUE):
            unites.extend(self._ensembles_de_groupes())

        ensembles = {
            frozenset(ensemble)
            for unite in unites
            for ensemble in self._par_semaines(unite)
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
        for ensemble in gardes:
            intervalles = []
            for index in sorted(ensemble):
                intervalle = self._intervalles_personne.get(index)
                if intervalle is None:
                    intervalle = self.cp.new_fixed_size_interval_var(
                        self.debuts[index], self.seances[index].duree + pause, f"personne_{index}"
                    )
                    self._intervalles_personne[index] = intervalle
                intervalles.append(intervalle)
            self.cp.add_no_overlap(intervalles)
        journal.debug("Non-chevauchement des personnes : %d ensemble(s)", len(gardes))

    # -- salles -----------------------------------------------------------------

    def _non_chevauchement_des_salles(self) -> None:
        """`SALLE_UNIQUE` : un intervalle optionnel par (séance, salle admissible)."""
        if not self.regles.active(C.SALLE_UNIQUE):
            return
        par_salle: dict[int, list[int]] = defaultdict(list)
        for seance in self.seances:
            for salle_id in seance.salles:
                par_salle[salle_id].append(seance.index)
        nb_ensembles = 0
        for salle_id in sorted(par_salle):
            intervalles: dict[int, cp_model.IntervalVar] = {}
            for ensemble in self._par_semaines(par_salle[salle_id]):
                if len(ensemble) < 2:
                    continue
                for index in ensemble:
                    if index in intervalles:
                        continue
                    presence = self.salles[index][salle_id]
                    duree = self.seances[index].duree
                    nom = f"salle_{salle_id}_{index}"
                    if presence is True:
                        intervalles[index] = self.cp.new_fixed_size_interval_var(
                            self.debuts[index], duree, nom
                        )
                    else:
                        intervalles[index] = self.cp.new_optional_fixed_size_interval_var(
                            self.debuts[index], duree, presence, nom
                        )
                self.cp.add_no_overlap([intervalles[index] for index in ensemble])
                nb_ensembles += 1
        journal.debug("Non-chevauchement des salles : %d ensemble(s)", nb_ensembles)

    # -- paires -----------------------------------------------------------------

    def _proches(self, i: int, j: int, ecart: int) -> bool:
        """Vrai si deux débuts admissibles du même jour laissent moins de `ecart` minutes."""
        duree_i, duree_j = self.seances[i].duree, self.seances[j].duree
        minutes_j = self._minutes_par_jour[j]
        for jour, minutes_i in self._minutes_par_jour[i].items():
            for a in minutes_i:
                for b in minutes_j.get(jour, ()):
                    if max(a, b) - min(a + duree_i, b + duree_j) < ecart:
                        return True
        return False

    def _pauses_et_trajets(self) -> None:
        """`CAMPUS_TRAJET`, et `PAUSE` pour les paires que l'unicité ne couvre pas.

        Pour chaque paire de séances qui partagent une personne et peuvent
        coexister :

        - si elles sont sur deux campus différents, l'une finit au moins le
          temps de trajet avant que l'autre commence ;
        - si aucune contrainte d'unicité active ne les sépare déjà (contrainte
          désactivée par l'utilisateur), elles peuvent se chevaucher, mais
          sinon elles respectent la pause.
        """
        regles, parametres = self.regles, self.instantane.parametres
        trajets = regles.active(C.CAMPUS_TRAJET)
        pause = regles.active(C.PAUSE) and parametres.pause_min > 0
        unicite_enseignant = regles.active(C.ENSEIGNANT_UNIQUE)
        unicite_groupe = regles.active(C.GROUPE_UNIQUE)
        if not trajets and not (pause and not (unicite_enseignant and unicite_groupe)):
            return
        campus = [self._campus_possibles(seance.index) for seance in self.seances]
        if not pause or (unicite_enseignant and unicite_groupe):
            if len({c for possibles in campus for c in possibles}) < 2:
                return  # un seul campus en jeu : aucun trajet possible
        nb_trajets = nb_pauses = 0
        for i, j, enseignant, groupe in self.preparation.paires_de_personnes():
            separees = (enseignant and unicite_enseignant) or (groupe and unicite_groupe)
            debut_i, debut_j = self.debuts[i], self.debuts[j]
            duree_i, duree_j = self.seances[i].duree, self.seances[j].duree

            if pause and not separees and self._proches(i, j, parametres.pause_min):
                avant = self.cp.new_bool_var(f"avant_{i}_{j}")
                apres = self.cp.new_bool_var(f"apres_{i}_{j}")
                ensemble = self.cp.new_bool_var(f"ensemble_{i}_{j}")
                self._exiger(debut_i + duree_i + parametres.pause_min <= debut_j, [avant])
                self._exiger(debut_j + duree_j + parametres.pause_min <= debut_i, [apres])
                self._exiger(debut_i < debut_j + duree_j, [ensemble])
                self._exiger(debut_j < debut_i + duree_i, [ensemble])
                self.cp.add_bool_or([avant, apres, ensemble])
                nb_pauses += 1

            if not trajets or (len(campus[i]) == 1 and campus[i] == campus[j]):
                continue
            acquis = (parametres.pause_min if regles.active(C.PAUSE) else 0) if separees else -1
            ordre = None
            for campus_i in campus[i]:
                for campus_j in campus[j]:
                    if campus_i == campus_j:
                        continue
                    trajet = self.instantane.trajet(campus_i, campus_j)
                    if trajet <= max(acquis, 0) or not self._proches(i, j, trajet):
                        continue
                    if ordre is None:
                        ordre = self.cp.new_bool_var(f"ordre_{i}_{j}")
                        nb_trajets += 1
                    sur = [self._sur_le_campus(i, campus_i), self._sur_le_campus(j, campus_j)]
                    self._exiger(debut_i + duree_i + trajet <= debut_j, [*sur, ordre])
                    self._exiger(debut_j + duree_j + trajet <= debut_i, [*sur, ordre.negated()])
        journal.debug(
            "Paires de personnes : %d avec trajet entre campus, %d avec pause seule",
            nb_trajets,
            nb_pauses,
        )

    # -- maxima -----------------------------------------------------------------

    def _maxima_journaliers(self) -> None:
        """`MAX_ENSEIGNANT`, maximum par jour (le maximum par semaine ne dépend pas du placement)."""
        if not self.regles.active(C.MAX_ENSEIGNANT):
            return
        par_enseignant: dict[int, list[int]] = defaultdict(list)
        for seance in self.seances:
            for enseignant in self.regles.enseignants_reels(seance.besoin):
                par_enseignant[enseignant].append(seance.index)
        for enseignant_id in sorted(par_enseignant):
            maximum = self.instantane.enseignant(enseignant_id).max_minutes_jour
            if maximum is None:
                continue
            for ensemble in self._par_semaines(par_enseignant[enseignant_id], isoler=False):
                if sum(self.seances[i].duree for i in ensemble) <= maximum:
                    continue
                jours = sorted({jour for i in ensemble for jour in self._minutes_par_jour[i]})
                for jour in jours:
                    acquis, termes = 0, []
                    for index in ensemble:
                        litteral = self._le_jour(index, jour)
                        if litteral is True:
                            acquis += self.seances[index].duree
                        elif litteral is not False:
                            termes.append(self.seances[index].duree * litteral)
                    if termes:
                        self.cp.add(sum(termes) <= maximum - acquis)
                    else:
                        self._exiger(acquis <= maximum)

    # -- symétries ----------------------------------------------------------------

    def _classes_de_symetrie(self) -> list[list[int]]:
        """Séances interchangeables : même besoin, mêmes semaines, sans verrou, par rang."""
        classes: dict[tuple[int, int], list[int]] = defaultdict(list)
        for seance in self.seances:
            if not seance.verrouillee and len(seance.debuts) > 1:
                classes[(seance.besoin.id, seance.semaines)].append(seance.index)
        return [
            sorted(indices, key=lambda i: self.seances[i].seance.rang)
            for indices in classes.values()
            if len(indices) >= 2
        ]

    def _briser_les_symetries(self) -> None:
        """Ordonne par début les séances interchangeables d'un même besoin."""
        for classe in self._classes_de_symetrie():
            for premier, second in zip(classe, classe[1:], strict=False):
                self.cp.add(self.debuts[premier] <= self.debuts[second])

    # -- indices ------------------------------------------------------------------

    def _donner_les_indices(self) -> None:
        """Donne le placement précédent de chaque séance comme point de départ.

        Dans une classe de séances interchangeables, les indices sont
        redistribués par début croissant pour rester compatibles avec le
        bris de symétrie.
        """
        indices: dict[int, tuple[int, int | None]] = {}
        for seance in self.seances:
            indice = seance.seance.indice
            if indice is None:
                continue
            code = self.preparation.coder(indice.jour, indice.debut)
            if code in seance.debuts:
                indices[seance.index] = (code, indice.salle_id)
        for classe in self._classes_de_symetrie():
            if all(index in indices for index in classe):
                tries = sorted(indices[index] for index in classe)
                indices.update(zip(classe, tries, strict=True))
        for index, (code, salle_id) in indices.items():
            debut, salles = self.debuts[index], self.salles[index]
            if not isinstance(debut, int):
                self.cp.add_hint(debut, code)
            if salle_id in salles and len(salles) > 1:
                for salle, litteral in salles.items():
                    self.cp.add_hint(litteral, salle == salle_id)
        journal.debug("Indices donnés au solveur pour %d séance(s)", len(indices))

    # -- objectif -----------------------------------------------------------------

    def _poser_objectif(self) -> None:
        """Objectif de qualité à minimiser (définitions exactes dans `docs/solveur.md`).

        `poids_trous` × trous des groupes + `poids_changement_campus` ×
        changements de campus + `poids_samedi` × séances du samedi +
        `poids_creneaux_extremes` × séances sur le premier ou le dernier
        créneau du jour. Chaque situation compte autant de fois qu'elle dure
        de semaines : une séance pèse son nombre de semaines actives, un trou
        ou un changement de campus le nombre de semaines du semestre.
        """
        parametres = self.instantane.parametres
        nb_semaines = max(1, len(self.instantane.semaines))
        actives = [seance.semaines.bit_count() for seance in self.seances]
        termes: list = []
        if parametres.poids_samedi:
            termes.extend(
                parametres.poids_samedi
                * actives[seance.index]
                * self._le_jour(seance.index, SAMEDI)
                for seance in self.seances
            )
        if parametres.poids_creneaux_extremes:
            termes.extend(
                parametres.poids_creneaux_extremes * actives[index] * litteral
                for index, litteral in enumerate(self._extremes())
            )
        groupes = self._emplois_du_temps_des_groupes()
        if parametres.poids_trous:
            termes.extend(
                parametres.poids_trous * nb_semaines * trou for trou in self._trous(groupes)
            )
        if parametres.poids_changement_campus:
            personnes = groupes + self._emplois_du_temps_des_enseignants()
            termes.extend(
                parametres.poids_changement_campus * nb_semaines * changement
                for changement in self._changements_de_campus(personnes)
            )
        variables = [terme for terme in termes if not isinstance(terme, int)]
        if not variables:
            journal.info("Objectif demandé mais sans effet : aucun terme ne dépend du placement.")
            return
        self.cp.minimize(sum(termes))
        self.objectif_actif = True
        journal.debug("Objectif : %d terme(s) variables", len(variables))

    def _creneaux_par_jour(self) -> dict[int, list[tuple[int, int]]]:
        par_jour: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for creneau in self.instantane.creneaux:
            par_jour[creneau.jour].append((creneau.debut, creneau.fin))
        return {jour: sorted(creneaux) for jour, creneaux in par_jour.items()}

    def _couvre(self, index: int, jour: int, creneau: tuple[int, int]) -> Litteral:
        """Littéral « la séance `index` occupe ce créneau de la grille »."""
        duree = self.seances[index].duree
        return self._appartient(
            index,
            (
                self.preparation.coder(jour, minute)
                for minute in self._minutes_par_jour[index].get(jour, ())
                if se_chevauchent(minute, minute + duree, *creneau)
            ),
        )

    def _extremes(self) -> list[Litteral]:
        """Par séance, « elle occupe le premier ou le dernier créneau de son jour »."""
        creneaux = self._creneaux_par_jour()
        litteraux = []
        for seance in self.seances:
            valeurs = [
                self.preparation.coder(jour, minute)
                for jour, minutes in self._minutes_par_jour[seance.index].items()
                if jour in creneaux
                for minute in minutes
                if any(
                    se_chevauchent(minute, minute + seance.duree, *creneau)
                    for creneau in (creneaux[jour][0], creneaux[jour][-1])
                )
            ]
            litteraux.append(self._appartient(seance.index, valeurs))
        return litteraux

    def _emplois_du_temps_des_groupes(self) -> list[list[int]]:
        """Séances vues par chaque groupe feuille de l'arbre des groupes.

        Un groupe feuille n'a pas de sous-groupe (les groupes transverses et
        tiers-temps, hors de l'arbre, sont écartés). Son emploi du temps
        réunit ses séances et celles des groupes qui le contiennent.
        """
        parents = {groupe.id: groupe.parent_id for groupe in self.instantane.groupes}
        par_groupe: dict[int, list[int]] = defaultdict(list)
        for seance in self.seances:
            for groupe_id in seance.besoin.groupes:
                par_groupe[groupe_id].append(seance.index)
        emplois: set[tuple[int, ...]] = set()
        for groupe in self.instantane.groupes:
            if groupe.id in parents.values() or groupe.nature in (
                NatureGroupe.TRANSVERSE,
                NatureGroupe.TIERS_TEMPS,
            ):
                continue
            lignee, courant = [groupe.id], groupe.parent_id
            while courant is not None and courant not in lignee:
                lignee.append(courant)
                courant = parents.get(courant)
            emploi = tuple(sorted({i for g in lignee for i in par_groupe.get(g, ())}))
            if len(emploi) >= 2:
                emplois.add(emploi)
        return [list(emploi) for emploi in sorted(emplois)]

    def _emplois_du_temps_des_enseignants(self) -> list[list[int]]:
        par_enseignant: dict[int, list[int]] = defaultdict(list)
        for seance in self.seances:
            for enseignant in self.regles.enseignants_reels(seance.besoin):
                par_enseignant[enseignant].append(seance.index)
        return [par_enseignant[e] for e in sorted(par_enseignant) if len(par_enseignant[e]) >= 2]

    def _ou(self, litteraux: Sequence[Litteral], nom: str) -> Litteral:
        """Littéral équivalent à « l'un des littéraux est vrai »."""
        if any(litteral is True for litteral in litteraux):
            return True
        variables = [litteral for litteral in litteraux if litteral is not False]
        if not variables:
            return False
        if len(variables) == 1:
            return variables[0]
        resultat = self.cp.new_bool_var(nom)
        for litteral in variables:
            self.cp.add_implication(litteral, resultat)
        self.cp.add_bool_or([*variables, resultat.negated()])
        return resultat

    def _trous(self, emplois: list[list[int]]) -> list[Litteral]:
        """Un littéral par (groupe, jour, créneau libre entre deux créneaux occupés)."""
        trous: list[Litteral] = []
        for numero, emploi in enumerate(emplois):
            for jour, creneaux in sorted(self._creneaux_par_jour().items()):
                if len(creneaux) < 3:
                    continue
                occupe = [
                    self._ou(
                        [self._couvre(index, jour, creneau) for index in emploi],
                        f"occupe_{numero}_{jour}_{rang}",
                    )
                    for rang, creneau in enumerate(creneaux)
                ]
                for rang in range(1, len(creneaux) - 1):
                    if occupe[rang] is True:
                        continue
                    encadrements = [
                        (avant, apres)
                        for avant in occupe[:rang]
                        for apres in occupe[rang + 1 :]
                        if avant is not False and apres is not False
                    ]
                    if not encadrements:
                        continue
                    trou = self.cp.new_bool_var(f"trou_{numero}_{jour}_{rang}")
                    for avant, apres in encadrements:
                        # avant et après occupés, créneau libre => trou
                        clause = [trou]
                        clause.extend(
                            litteral.negated()
                            for litteral in (avant, apres)
                            if litteral is not True
                        )
                        if occupe[rang] is not False:
                            clause.append(occupe[rang])
                        self.cp.add_bool_or(clause)
                    trous.append(trou)
        return trous

    def _changements_de_campus(self, emplois: list[list[int]]) -> list[Litteral]:
        """Un littéral par (personne, jour) « elle a cours sur au moins deux campus ce jour-là »."""
        changements: list[Litteral] = []
        for numero, emploi in enumerate(emplois):
            campus = sorted({c for index in emploi for c in self._campus_possibles(index)})
            if len(campus) < 2:
                continue
            jours = sorted({jour for index in emploi for jour in self._minutes_par_jour[index]})
            for jour in jours:
                presents = []
                for campus_id in campus:
                    ici = []
                    for index in emploi:
                        jour_ok = self._le_jour(index, jour)
                        campus_ok = self._sur_le_campus(index, campus_id)
                        if jour_ok is False or campus_ok is False:
                            continue
                        if jour_ok is True or campus_ok is True:
                            ici.append(campus_ok if jour_ok is True else jour_ok)
                            continue
                        les_deux = self.cp.new_bool_var(f"ici_{numero}_{jour}_{campus_id}_{index}")
                        self.cp.add_bool_or([jour_ok.negated(), campus_ok.negated(), les_deux])
                        ici.append(les_deux)
                    presents.append(ici)
                if sum(1 for ici in presents if ici) < 2:
                    continue
                # « présent sur le campus » n'a besoin que du sens (une séance => présent) :
                # l'objectif pousse déjà ces littéraux vers faux.
                sur = []
                for campus_id, ici in zip(campus, presents, strict=True):
                    if any(litteral is True for litteral in ici):
                        sur.append(True)
                    elif ici:
                        present = self.cp.new_bool_var(f"present_{numero}_{jour}_{campus_id}")
                        for litteral in ici:
                            self.cp.add_implication(litteral, present)
                        sur.append(present)
                changement = self.cp.new_bool_var(f"changement_{numero}_{jour}")
                for a, b in combinations(sur, 2):
                    clause = [changement]
                    clause.extend(litteral.negated() for litteral in (a, b) if litteral is not True)
                    self.cp.add_bool_or(clause)
                changements.append(changement)
        return changements
