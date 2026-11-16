"""Replacement de séances datées : le moteur de la couche calendrier.

`replacer` est une fonction pure. Elle reçoit les occurrences qui tiennent
leur place (**fixes**) et celles qu'il faut déplacer (**à replacer**), et
cherche pour chacune de ces dernières une date, une heure et une salle qui
respectent toutes les contraintes actives face aux occurrences déjà en place.

Il replace les séances qui tombent un jour fermé ou pendant une
indisponibilité (`edt.solveur.instanciation`). Il accepte un ensemble
quelconque d'occurrences à replacer, une fenêtre de dates et un coût
d'éloignement.

Règles appliquées à une place candidate (les mêmes que la semaine type,
date par date)
----------------------------------------------------------------------
- calendrier : dans le semestre et dans la fenêtre, jour non fermé pour les
  groupes (`JOUR_FERME`), jour de cours permis (`JOURS`), jour et heure
  imposés par le besoin respectés (une séance ponctuelle ne change pas de date) ;
- jours permis de la séance à sa date (`JOURS`, règles `JOURS_TRAVAILLES`
  retenues dont la période couvre la date : `Regles.violations_de_jour`) ;
- horaires : début de créneau, demi-journée, ouverture (`HORAIRES`), déjeuner
  à horaires déterminés de chacune de ses personnes (`DEJEUNER` ou règle
  `PAUSE_DEJEUNER`), règles `PLAGE_HORAIRE` et `CRENEAUX_INTERDITS`, par
  `Regles.refus_horaire` ; déjeuner en pause libre bornée : la séance
  replacée ne doit pas priver une de ses personnes (groupes feuilles de sa
  lignée, enseignants) de sa pause ce jour-là (`Regles.pause_dejeuner_tenue`) ;
- indisponibilités récurrentes et datées des enseignants, des groupes liés
  et de la salle (`DISPO_*`) ; règles `CAMPUS` et `SALLES` sur la salle choisie ;
- personnes : unicité, pause et trajet face aux occurrences du même jour
  (`ENSEIGNANT_UNIQUE`, `GROUPE_UNIQUE`, `PAUSE`, `CAMPUS_TRAJET`) ;
- maxima des enseignants par jour et par semaine (`MAX_ENSEIGNANT`) ;
- salle : compatible avec le besoin et libre (`SALLE_UNIQUE`, `CAPACITE`...) ;
- règles personnalisées d'emploi du temps et de relation (`MAX_PAR_JOUR`,
  `CAMPUS_PAR_JOUR`, `MATIERES_PAR_JOUR`, `JOURNEES_LIBRES`, `SANS_TROU`,
  `ORDRE_SEMAINE`, `REPARTITION_MATIERE`) : la candidate, salle choisie, est
  soumise avec les occurrences en place de la même semaine réelle aux
  vérificateurs datés de `edt.verification` (définitions communes) ; une
  anomalie qui la cite refuse la place (règle nécessaire) ou s'ajoute au
  coût (règle souple). Le choix de la salle tient compte de
  `CAMPUS_PAR_JOUR` (campus déjà occupés ce jour-là par la personne).

Contraintes souples (niveau « important » ou « facultatif ») : une place qui
les enfreint reste permise, mais chaque entorse ajoute `Regles.poids` au
coût de la place (1000 pour une règle importante, soit dix semaines
d'éloignement ; 20 pour une règle facultative, soit deux jours). Les
contraintes nécessaires restent des interdictions.

Méthode
-------
Les occurrences sont traitées une par une, les plus contraintes d'abord
(moins de salles compatibles, puis les plus longues). Chacune prend la place
valide la moins coûteuse, éloignement et entorses compris : d'abord la même
semaine, puis les semaines voisines. Une occurrence replacée devient fixe
pour les suivantes. Ce qui ne trouve aucune place sort avec le statut « à
programmer » et la raison : jamais d'échec global, jamais de perte
silencieuse.

Limite connue : le choix est glouton. Chaque occurrence est placée au mieux
pour elle, sans remettre en cause les précédentes ; le coût total n'est pas
garanti minimal.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import threading
import time
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field

from edt.domaine.calendrier import lundi_de
from edt.domaine.contraintes import CodeContrainte, Niveau, Niveaux, normaliser_codes
from edt.domaine.enumerations import CibleIndisponibilite, StatutOccurrence
from edt.domaine.heures import se_chevauchent
from edt.domaine.instantane import Besoin, Instantane, Occurrence
from edt.domaine.regles import NaturePersonne, NaturePortee, TypeRegle
from edt.erreurs import ErreurDonneesInvalides
from edt.solveur.instanciation import empechement
from edt.solveur.personnalisees import Choix, enfreint
from edt.solveur.regles import Regles, Violation, pause_dejeuner_tenue
from edt.verification import verifier_occurrences

journal = logging.getLogger(__name__)

C = CodeContrainte


@dataclass(frozen=True)
class OptionsReplacement:
    """Réglages d'un replacement.

    - `contraintes_desactivees` : codes du catalogue à ignorer ;
    - `niveaux` : niveau de chaque contrainte présente (voir `Regles`) ; à
      défaut, les niveaux par défaut sans les codes ignorés ;
    - `niveaux_regles` : niveau de chaque règle personnalisée retenue
      (`edt.solveur.personnalisees`) ; `None` : chaque règle active à son niveau ;
    - `date_debut`, `date_fin` : fenêtre des dates permises (bornes incluses ;
      par défaut tout le semestre) ;
    - coût d'éloignement d'une place : `cout_semaine` par semaine d'écart avec
      la date d'origine, plus `cout_jour` par jour d'écart, plus
      `cout_horaire` si l'heure de début change. Avec les valeurs par défaut,
      la même semaine passe toujours avant une semaine voisine.
    """

    contraintes_desactivees: frozenset[CodeContrainte] = frozenset()
    niveaux: Niveaux | None = None
    niveaux_regles: dict[int, Niveau] | None = None
    date_debut: dt.date | None = None
    date_fin: dt.date | None = None
    cout_semaine: int = 100
    cout_jour: int = 10
    cout_horaire: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "contraintes_desactivees", normaliser_codes(self.contraintes_desactivees)
        )
        if min(self.cout_semaine, self.cout_jour, self.cout_horaire) < 0:
            raise ErreurDonneesInvalides("Les coûts d'éloignement ne peuvent pas être négatifs.")
        if self.date_debut and self.date_fin and self.date_fin < self.date_debut:
            raise ErreurDonneesInvalides(
                "La fenêtre de replacement est vide : sa fin précède son début."
            )


@dataclass(frozen=True)
class Replacement:
    """Sort d'une occurrence à replacer.

    - `origine` : l'occurrence telle qu'elle était prévue ;
    - `occurrence` : la même, à sa nouvelle place (statut « déplacée ») ou
      laissée à sa date d'origine avec le statut « à programmer » ;
    - `cout` : coût d'éloignement de la nouvelle place (`None` si à programmer) ;
    - `raison` : pourquoi aucune place n'a été trouvée (vide si replacée).
    """

    origine: Occurrence
    occurrence: Occurrence
    cout: int | None
    raison: str = ""

    @property
    def replacee(self) -> bool:
        """Vrai si une place a été trouvée."""
        return self.cout is not None


@dataclass(frozen=True)
class ResultatReplacement:
    """Résultat d'un replacement : une entrée par occurrence à replacer, dans l'ordre reçu."""

    replacements: tuple[Replacement, ...]
    arrete: bool = False
    contraintes_desactivees: frozenset[CodeContrainte] = frozenset()
    statistiques: Mapping[str, int | float] = field(default_factory=dict)

    @property
    def replacees(self) -> tuple[Replacement, ...]:
        """Occurrences qui ont trouvé une place."""
        return tuple(r for r in self.replacements if r.replacee)

    @property
    def a_programmer(self) -> tuple[Replacement, ...]:
        """Occurrences restées sans place : à programmer à la main."""
        return tuple(r for r in self.replacements if not r.replacee)

    @property
    def occurrences(self) -> tuple[Occurrence, ...]:
        """Les occurrences après replacement (déplacées et à programmer)."""
        return tuple(r.occurrence for r in self.replacements)


@dataclass(frozen=True)
class _Place:
    """Occurrence en place : ce qu'il faut pour la comparer à une candidate."""

    besoin: Besoin
    debut: int
    fin: int
    salle_id: int | None


class _Planning:
    """Occurrences en place, indexées par date et par ressource."""

    def __init__(self, regles: Regles) -> None:
        self.regles = regles
        self.par_salle: dict[tuple[dt.date, int], list[_Place]] = defaultdict(list)
        self.par_enseignant: dict[tuple[dt.date, int], list[_Place]] = defaultdict(list)
        self.par_groupe: dict[tuple[dt.date, int], list[_Place]] = defaultdict(list)
        self.minutes_jour: dict[tuple[int, dt.date], int] = defaultdict(int)
        self.minutes_semaine: dict[tuple[int, dt.date], int] = defaultdict(int)
        self.par_semaine: dict[dt.date, list[Occurrence]] = defaultdict(list)
        self._campus_de = {salle.id: salle.campus_id for salle in regles.instantane.salles}

    def ajouter(
        self,
        besoin: Besoin,
        date: dt.date,
        debut: int,
        fin: int,
        salle_id,
        occurrence: Occurrence | None = None,
    ) -> None:
        place = _Place(besoin, debut, fin, salle_id)
        if occurrence is not None:
            self.par_semaine[lundi_de(date)].append(occurrence)
        if salle_id is not None:
            self.par_salle[(date, salle_id)].append(place)
        for enseignant in self.regles.enseignants_reels(besoin):
            self.par_enseignant[(date, enseignant)].append(place)
            self.minutes_jour[(enseignant, date)] += fin - debut
            self.minutes_semaine[(enseignant, lundi_de(date))] += fin - debut
        for groupe in besoin.groupes:
            self.par_groupe[(date, groupe)].append(place)

    def voisins(self, besoin: Besoin, date: dt.date) -> list[_Place]:
        """Occurrences du même jour qui peuvent partager une personne avec `besoin`."""
        voisins: dict[int, _Place] = {}
        for enseignant in self.regles.enseignants_reels(besoin):
            for place in self.par_enseignant.get((date, enseignant), ()):
                voisins[id(place)] = place
        for groupe in self.regles.groupes_lies(besoin):
            for place in self.par_groupe.get((date, groupe), ()):
                voisins[id(place)] = place
        return list(voisins.values())

    def maximum_depasse(self, besoin: Besoin, date: dt.date, duree: int) -> bool:
        """Vrai si la séance ferait dépasser un maximum horaire à l'un de ses enseignants."""
        if not self.regles.active(C.MAX_ENSEIGNANT):
            return False
        for enseignant_id in self.regles.enseignants_reels(besoin):
            enseignant = self.regles.instantane.enseignant(enseignant_id)
            jour = self.minutes_jour.get((enseignant_id, date), 0) + duree
            semaine = self.minutes_semaine.get((enseignant_id, lundi_de(date)), 0) + duree
            if enseignant.max_minutes_jour is not None and jour > enseignant.max_minutes_jour:
                return True
            if (
                enseignant.max_minutes_semaine is not None
                and semaine > enseignant.max_minutes_semaine
            ):
                return True
        return False

    def pauses_dejeuner_supprimees(
        self, besoin: Besoin, date: dt.date, debut: int, fin: int
    ) -> dict[Violation, int]:
        """Personnes qui perdraient leur pause libre ce jour-là si la séance s'y mettait, par source.

        Chaque personne du besoin (`Regles.personnes`) a son réglage
        (`Instantane.reglages.dejeuner`) ; seules celles en pause libre bornée
        comptent. L'emploi du temps de la journée d'un groupe feuille réunit
        les places de sa lignée, celui d'un enseignant les siennes. Renvoie,
        par violation (la contrainte `DEJEUNER` ou la règle), le nombre de
        personnes privées de pause.
        """
        regles = self.regles
        reglages = regles.instantane.reglages
        supprimees: dict[Violation, int] = defaultdict(int)
        for personne in regles.personnes(besoin):
            reglage = reglages.dejeuner[personne]
            if reglage.fenetre is None:
                continue
            if reglage.source.regle is None and not regles.active(C.DEJEUNER):
                continue
            if not se_chevauchent(debut, fin, reglage.fenetre[0], reglage.fenetre[1]):
                continue
            if personne.nature is NaturePersonne.ENSEIGNANT:
                places = list(self.par_enseignant.get((date, personne.id), ()))
            else:
                lignee = regles.instantane.emploi(personne).lignee or (personne.id,)
                places = [p for g in lignee for p in self.par_groupe.get((date, g), ())]
            journee = [(p.debut, p.fin) for p in places] + [(debut, fin)]
            if not pause_dejeuner_tenue(journee, reglage.fenetre):
                violation = regles.violation_de_source(
                    reglage.source, C.DEJEUNER, "la pause déjeuner"
                )
                supprimees[violation] += 1
        return dict(supprimees)

    def _places_des_personnes(self, date: dt.date, personnes: Iterable) -> list[_Place]:
        """Occurrences du jour des personnes données (enseignants, groupes feuilles et leur lignée)."""
        places: dict[int, _Place] = {}
        for personne in personnes:
            if personne.nature is NaturePersonne.ENSEIGNANT:
                for place in self.par_enseignant.get((date, personne.id), ()):
                    places[id(place)] = place
            else:
                lignee = self.regles.instantane.emploi(personne).lignee or (personne.id,)
                for groupe in lignee:
                    for place in self.par_groupe.get((date, groupe), ()):
                        places[id(place)] = place
        return list(places.values())

    def campus_refuses_du_jour(
        self, besoin: Besoin, date: dt.date, seance_id: str | None
    ) -> list[tuple[frozenset[int], Violation]]:
        """Règles `CAMPUS_PAR_JOUR` de la séance : par règle, les campus déjà occupés ce jour-là
        par ses personnes (une salle d'un autre campus enfreindrait la règle)."""
        regles = self.regles
        resultat = []
        personnes = set(regles.personnes(besoin))
        for regle in regles.regles_emploi_de(besoin, seance_id):
            if regle.type is not TypeRegle.CAMPUS_PAR_JOUR or not regle.couvre(date):
                continue
            places = self._places_des_personnes(date, personnes & set(regle.personnes))
            campus = frozenset(
                self._campus_de[p.salle_id] for p in places if p.salle_id is not None
            )
            if campus:
                resultat.append((campus, regles.violation_de_regle(regle)))
        return resultat

    def regles_emploi_enfreintes(self, besoin: Besoin, candidate: Occurrence) -> list[Violation]:
        """Règles d'emploi du temps et de relation qu'enfreindrait la candidate (salle choisie).

        Les occurrences en place de la même semaine réelle qui partagent une
        personne avec la candidate sont soumises avec elle aux vérificateurs
        datés (`edt.verification.verifier_occurrences`, code `REGLE`, aux
        niveaux des règles du calcul) ; une anomalie qui cite la candidate
        compte pour une violation de sa règle. Rien n'est fait si aucune
        règle de ces types ne concerne la séance.
        """
        regles = self.regles
        concernantes = {
            regle.id: regle for regle in regles.regles_emploi_de(besoin, candidate.seance_id)
        }
        if not concernantes:
            return []
        personnes = set(regles.personnes(besoin))
        instantane = regles.instantane
        semaine = [
            occurrence
            for occurrence in self.par_semaine.get(lundi_de(candidate.date), ())
            if not personnes.isdisjoint(regles.personnes(instantane.besoin(occurrence.besoin_id)))
        ]
        temoin = dataclasses.replace(candidate, id=-1, statut=StatutOccurrence.DEPLACEE)
        anomalies = verifier_occurrences(
            instantane, [*semaine, temoin], [C.REGLE], niveaux_regles=regles.niveaux_regles
        )
        violations = []
        for anomalie in anomalies:
            if anomalie.regle_id not in concernantes:
                continue
            if -1 not in anomalie.occurrences and (
                candidate.seance_id is None or candidate.seance_id not in anomalie.seances
            ):
                continue
            violations.append(regles.violation_de_regle(concernantes[anomalie.regle_id]))
        return violations

    def salle_libre(
        self,
        besoin: Besoin,
        date: dt.date,
        debut: int,
        fin: int,
        voisins: Sequence[_Place],
        ordre,
        seance_id: str | None = None,
        besoin_jour: Besoin | None = None,
    ) -> tuple[int, int] | None:
        """`(salle, coût des entorses)` : la salle de `ordre` la moins coûteuse, libre et permise.

        Une salle indisponible (`DISPO_SALLE`), déjà prise (`SALLE_UNIQUE`),
        hors de portée de trajet (`CAMPUS_TRAJET`) ou refusée par une règle
        `SALLES`, `CAMPUS` ou d'horaires de portée campus de la séance est
        écartée si la contrainte est dure, pénalisée si elle est souple. Le
        coût des exigences de salle souples que la salle ne satisfait pas
        s'ajoute. Avec `besoin_jour`, les jours permis se jugent ici, avec le
        campus de chaque salle (une règle de jours vise un campus possible).
        """
        regles = self.regles
        compatibles = regles.salles_compatibles(besoin)
        # Règles jugées au choix de la salle : SALLES, CAMPUS, et les règles d'horaires de
        # portée campus (elles ne jouent que sur le campus de la salle).
        regles_salle = [
            regle
            for regle in (regles.regles_de_seance(seance_id) if seance_id else ())
            if regle.couvre(date)
            and (
                regle.type in (TypeRegle.SALLES, TypeRegle.CAMPUS)
                or regle.portee is NaturePortee.CAMPUS
            )
        ]
        campus_de = self._campus_de
        campus_du_jour = self.campus_refuses_du_jour(besoin, date, seance_id)
        meilleure: tuple[int, int] | None = None
        for salle_id in ordre:
            cout = sum(regles.poids(code) for code in compatibles.entorses.get(salle_id, ()))
            # CAMPUS_PAR_JOUR : une salle sur un autre campus que ceux déjà occupés ce jour-là.
            changement = [
                violation
                for campus, violation in campus_du_jour
                if campus_de.get(salle_id) not in campus
            ]
            if any(regles.dure_violation(v) for v in changement):
                continue
            cout += sum(regles.poids_violation(v) for v in changement)
            if besoin_jour is not None:
                du_jour = regles.violations_de_jour(
                    seance_id, date.weekday(), campus_de.get(salle_id), besoin_jour, date
                )
                if any(regles.dure_violation(v) for v in du_jour):
                    continue
                cout += sum(regles.poids_violation(v) for v in regles.souples_violations(du_jour))
            if regles_salle:
                choix = Choix(
                    date.weekday(), debut, fin, campus_id=campus_de.get(salle_id), salle_id=salle_id
                )
                violations = [
                    regles.violation_de_regle(regle)
                    for regle in regles_salle
                    if enfreint(regle, choix)
                ]
                if any(regles.dure_violation(v) for v in violations):
                    continue
                cout += sum(regles.poids_violation(v) for v in violations)
            if regles.active(C.DISPO_SALLE) and regles.indisponible_le(
                CibleIndisponibilite.SALLE, salle_id, date, debut, fin
            ):
                if regles.dure(C.DISPO_SALLE):
                    continue
                cout += regles.poids(C.DISPO_SALLE)
            if regles.active(C.SALLE_UNIQUE) and any(
                se_chevauchent(debut, fin, place.debut, place.fin)
                for place in self.par_salle.get((date, salle_id), ())
            ):
                continue
            violations = [
                violation
                for v in voisins
                for violation in regles.violations_de_paire(
                    besoin, debut, fin, salle_id, v.besoin, v.debut, v.fin, v.salle_id
                )
                if violation.code is C.CAMPUS_TRAJET  # les autres règles de paire sont vues avant
            ]
            if any(regles.dure_violation(v) for v in violations):
                continue
            cout += sum(regles.poids_violation(v) for v in violations)
            if meilleure is None or cout < meilleure[1]:
                meilleure = (salle_id, cout)
            if cout == 0:
                break
        return meilleure


def replacer(
    instantane: Instantane,
    occurrences_fixes: Sequence[Occurrence],
    occurrences_a_replacer: Sequence[Occurrence],
    options: OptionsReplacement | None = None,
    arret: threading.Event | None = None,
) -> ResultatReplacement:
    """Replace chaque occurrence de `occurrences_a_replacer` face aux `occurrences_fixes`.

    Seules les occurrences fixes qui ont lieu (planifiées ou déplacées)
    occupent leurs ressources. Voir l'en-tête du module pour les règles et la
    méthode. Si `arret` est levé, les occurrences non encore traitées sortent
    « à programmer » et `arrete` vaut vrai.
    """
    options = options or OptionsReplacement()
    debut_calcul = time.perf_counter()
    regles = Regles(
        instantane, options.contraintes_desactivees, options.niveaux, options.niveaux_regles
    )
    planning = _Planning(regles)
    for occurrence in occurrences_fixes:
        if occurrence.statut.occupe_les_ressources:
            planning.ajouter(
                instantane.besoin(occurrence.besoin_id),
                occurrence.date,
                occurrence.debut,
                occurrence.fin,
                occurrence.salle_id,
                occurrence,
            )

    premier = max(d for d in (instantane.date_debut, options.date_debut) if d is not None)
    dernier = min(d for d in (instantane.date_fin, options.date_fin) if d is not None)

    def contrainte(position: int) -> tuple:
        occurrence = occurrences_a_replacer[position]
        besoin = instantane.besoin(occurrence.besoin_id)
        return (
            len(regles.salles_compatibles(besoin).salles),
            -(occurrence.fin - occurrence.debut),
            occurrence.date,
            occurrence.seance_id or "",
            position,
        )

    sorts: dict[int, Replacement] = {}
    arrete = False
    for position in sorted(range(len(occurrences_a_replacer)), key=contrainte):
        origine = occurrences_a_replacer[position]
        besoin = instantane.besoin(origine.besoin_id)
        if arret is not None and arret.is_set():
            arrete = True
            sorts[position] = _a_programmer(origine, "le replacement a été interrompu")
            continue
        sorts[position] = _placer(regles, planning, besoin, origine, premier, dernier, options)
        place = sorts[position]
        if place.replacee:
            nouvelle = place.occurrence
            planning.ajouter(
                besoin, nouvelle.date, nouvelle.debut, nouvelle.fin, nouvelle.salle_id, nouvelle
            )

    replacements = tuple(sorts[position] for position in range(len(occurrences_a_replacer)))
    nb_replacees = sum(1 for r in replacements if r.replacee)
    duree = time.perf_counter() - debut_calcul
    journal.info(
        "Replacement : %d occurrence(s) à replacer, %d replacée(s), %d à programmer (%.3f s)",
        len(replacements),
        nb_replacees,
        len(replacements) - nb_replacees,
        duree,
    )
    for replacement in replacements:
        if not replacement.replacee:
            journal.warning(
                "À programmer : besoin %d, séance %s du %s : %s",
                replacement.origine.besoin_id,
                replacement.origine.seance_id,
                replacement.origine.date.isoformat(),
                replacement.raison,
            )
    return ResultatReplacement(
        replacements=replacements,
        arrete=arrete,
        contraintes_desactivees=options.contraintes_desactivees,
        statistiques={
            "duree_s": round(duree, 3),
            "nb_a_replacer": len(replacements),
            "nb_replacees": nb_replacees,
            "nb_a_programmer": len(replacements) - nb_replacees,
            "cout_total": sum(r.cout or 0 for r in replacements),
        },
    )


def _a_programmer(origine: Occurrence, raison: str) -> Replacement:
    occurrence = dataclasses.replace(origine, statut=StatutOccurrence.A_PROGRAMMER)
    return Replacement(origine, occurrence, None, raison)


def _jours_par_campus(regles: Regles, besoin: Besoin, seance_id: str | None) -> bool:
    """Vrai si une règle de jours vise le campus d'une salle possible de la séance.

    Le jour se juge alors salle par salle (campus de la salle), comme la
    préparation le fait campus par campus sur la semaine type.
    """
    if seance_id is None or seance_id not in regles.instantane.reglages.jours:
        return False
    campus = {salle.campus_id for salle in regles.salles_compatibles(besoin).salles}
    return bool(regles.jours_de(seance_id).campus_regles & campus)


def _candidates(
    regles: Regles,
    besoin: Besoin,
    origine: Occurrence,
    premier: dt.date,
    dernier: dt.date,
    options: OptionsReplacement,
) -> Iterator[tuple[int, dt.date, int]]:
    """Places `(coût, date, début)` permises par le calendrier et la grille, par coût croissant.

    Le coût est l'éloignement de la date d'origine, plus les entorses aux
    règles souples de jour et d'horaire (`JOURS`, `HORAIRES`, `DEJEUNER`).
    """
    duree = origine.fin - origine.debut
    if besoin.date_debut is not None:
        premier = max(premier, besoin.date_debut)
    if besoin.date_fin is not None:
        dernier = min(dernier, besoin.date_fin)
    if besoin.ponctuel:
        # Une séance ponctuelle a sa date imposée : elle ne peut que changer d'heure ou de salle.
        dates: Iterable[dt.date] = (
            [besoin.date_ponctuelle]
            if besoin.date_ponctuelle is not None and premier <= besoin.date_ponctuelle <= dernier
            else []
        )
    else:
        dates = (premier + dt.timedelta(days=n) for n in range((dernier - premier).days + 1))
    lundi_origine = lundi_de(origine.date)
    par_campus = _jours_par_campus(regles, besoin, origine.seance_id)
    campus_possibles = sorted({s.campus_id for s in regles.salles_compatibles(besoin).salles})
    places = []
    for date in dates:
        jour = date.weekday()
        if besoin.jour_impose is not None and jour != besoin.jour_impose:
            continue
        impose = besoin.ponctuel or (
            besoin.jour_impose is not None and besoin.debut_impose is not None
        )
        if not impose and not regles.instantane.debuts_de_creneau(jour):
            continue
        if impose:
            violations_jour: list = []
        elif par_campus:
            # Jugé salle par salle (`_Planning.salle_libre`) : le jour n'est écarté ici que
            # s'il est fermé sur tous les campus possibles.
            fermes = [
                any(
                    regles.dure_violation(v)
                    for v in regles.violations_de_jour(
                        origine.seance_id, jour, campus, besoin, date
                    )
                )
                for campus in campus_possibles
            ]
            if fermes and all(fermes):
                continue
            violations_jour = []
        else:
            # Les règles de jours sont résolues à la date : hors de la période d'une
            # ouverture datée, le standard reprend sa place.
            violations_jour = regles.violations_de_jour(origine.seance_id, jour, None, besoin, date)
        if any(regles.dure_violation(v) for v in violations_jour):
            continue
        entorses_jour = sum(
            regles.poids_violation(v) for v in regles.souples_violations(violations_jour)
        )
        semaines = abs((lundi_de(date) - lundi_origine).days) // 7
        jours = abs((date - origine.date).days)
        for debut in regles.debuts_a_examiner(besoin, jour):
            if (
                regles.refus_horaire(besoin, duree, jour, debut, origine.seance_id, date)
                is not None
            ):
                continue
            entorses = entorses_jour + sum(
                regles.poids_violation(v)
                for v in regles.entorses_horaire(
                    besoin, duree, jour, debut, origine.seance_id, date
                )
            )
            cout = (
                options.cout_semaine * semaines
                + options.cout_jour * jours
                + options.cout_horaire * (debut != origine.debut)
                + entorses
            )
            places.append((cout, date, debut))
    return iter(sorted(places))


def _placer(
    regles: Regles,
    planning: _Planning,
    besoin: Besoin,
    origine: Occurrence,
    premier: dt.date,
    dernier: dt.date,
    options: OptionsReplacement,
) -> Replacement:
    """Première place valide de l'occurrence par coût croissant, ou « à programmer »."""
    duree = origine.fin - origine.debut
    salles = [salle.id for salle in regles.salles_compatibles(besoin).salles]
    if origine.salle_id in salles:
        # À égalité, la salle d'origine est gardée.
        salles = [origine.salle_id] + [salle for salle in salles if salle != origine.salle_id]
    refus: dict[str, int] = defaultdict(int)
    nb_examinees = 0
    # La place retenue minimise éloignement et entorses : les candidates arrivent par
    # éloignement croissant, la recherche s'arrête dès qu'aucune ne peut battre la meilleure.
    meilleure: tuple[int, Occurrence] | None = None
    par_campus = _jours_par_campus(regles, besoin, origine.seance_id)
    for cout, date, debut in _candidates(regles, besoin, origine, premier, dernier, options):
        if meilleure is not None and cout >= meilleure[0]:
            break
        nb_examinees += 1
        fin = debut + duree
        candidate = dataclasses.replace(origine, date=date, debut=debut, fin=fin, salle_id=None)
        obstacle = empechement(regles, besoin, candidate, toutes_les_souples=True)
        if obstacle is not None:
            if regles.dure(obstacle[0]):
                refus["un jour fermé ou une indisponibilité"] += 1
                continue
            cout += regles.poids(obstacle[0])  # indisponibilité sous une règle souple : une entorse
        if planning.maximum_depasse(besoin, date, duree):
            if regles.dure(C.MAX_ENSEIGNANT):
                refus["le maximum horaire d'un enseignant"] += 1
                continue
            cout += regles.poids(C.MAX_ENSEIGNANT)
        voisins = planning.voisins(besoin, date)
        violations = [
            violation
            for v in voisins
            for violation in regles.violations_de_paire(
                besoin, debut, fin, None, v.besoin, v.debut, v.fin, v.salle_id
            )
        ]
        if any(regles.dure_violation(v) for v in violations):
            refus["l'emploi du temps de ses groupes ou de ses enseignants"] += 1
            continue
        cout += sum(regles.poids_violation(v) for v in violations)
        sans_pause = planning.pauses_dejeuner_supprimees(besoin, date, debut, fin)
        if any(regles.dure_violation(v) for v in sans_pause):
            refus["la pause déjeuner de ses groupes ou de ses enseignants"] += 1
            continue
        cout += sum(regles.poids_violation(v) * n for v, n in sans_pause.items())
        salle = planning.salle_libre(
            besoin,
            date,
            debut,
            fin,
            voisins,
            salles,
            seance_id=origine.seance_id,
            besoin_jour=besoin if par_campus else None,
        )
        if salle is None:
            refus["l'absence de salle libre"] += 1
            continue
        salle_id, cout_salle = salle
        cout += cout_salle
        if meilleure is not None and cout >= meilleure[0]:
            continue
        enfreintes = planning.regles_emploi_enfreintes(
            besoin, dataclasses.replace(candidate, salle_id=salle_id)
        )
        if any(regles.dure_violation(v) for v in enfreintes):
            refus["une règle d'emploi du temps de ses groupes ou de ses enseignants"] += 1
            continue
        cout += sum(regles.poids_violation(v) for v in enfreintes)
        if meilleure is not None and cout >= meilleure[0]:
            continue
        inchangee = (date, debut, salle_id) == (origine.date, origine.debut, origine.salle_id)
        occurrence = dataclasses.replace(
            origine,
            date=date,
            debut=debut,
            fin=fin,
            salle_id=salle_id,
            statut=StatutOccurrence.PLANIFIEE if inchangee else StatutOccurrence.DEPLACEE,
        )
        meilleure = (cout, occurrence)
    if meilleure is not None:
        return Replacement(origine, meilleure[1], meilleure[0])

    if not salles:
        return _a_programmer(origine, "aucune salle ne convient à cette séance")
    if nb_examinees == 0:
        if besoin.ponctuel:
            raison = "la date de cette séance ponctuelle est imposée et ne convient pas"
        else:
            raison = (
                f"aucun créneau de la grille n'est permis entre le {premier:%d/%m/%Y} et le "
                f"{dernier:%d/%m/%Y}"
            )
        return _a_programmer(origine, raison)
    details = ", ".join(
        f"{nombre} par {motif}" for motif, nombre in sorted(refus.items(), key=lambda r: -r[1])
    )
    creneaux = (
        "le créneau examiné est écarté"
        if nb_examinees == 1
        else f"les {nb_examinees} créneaux examinés sont tous écartés"
    )
    return _a_programmer(
        origine,
        f"aucune place libre entre le {premier:%d/%m/%Y} et le {dernier:%d/%m/%Y} : "
        f"{creneaux}, {details}",
    )
