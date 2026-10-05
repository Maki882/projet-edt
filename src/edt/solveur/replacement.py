"""Replacement de séances datées : le moteur de la couche calendrier.

`replacer` est une fonction pure. Elle reçoit les occurrences qui tiennent
leur place (**fixes**) et celles qu'il faut déplacer (**à replacer**), et
cherche pour chacune de ces dernières une date, une heure et une salle qui
respectent toutes les contraintes actives face aux occurrences déjà en place.

Il sert aujourd'hui à replacer les séances qui tombent un jour fermé ou
pendant une indisponibilité (`edt.solveur.instanciation`). Il est écrit pour
servir aussi, plus tard, au réaménagement sur imprévu : un ensemble
quelconque d'occurrences à replacer, une fenêtre de dates, un coût
d'éloignement.

Règles appliquées à une place candidate (les mêmes que la semaine type,
date par date)
----------------------------------------------------------------------
- calendrier : dans le semestre et dans la fenêtre, jour non fermé pour les
  groupes (`JOUR_FERME`), jour de cours permis (`JOURS`), jour et heure
  imposés par le besoin respectés (une séance ponctuelle ne change pas de date) ;
- horaires : début de créneau, demi-journée, ouverture (`HORAIRES`), déjeuner
  (`DEJEUNER`) ;
- indisponibilités récurrentes et datées des enseignants, des groupes liés
  et de la salle (`DISPO_*`) ;
- personnes : unicité, pause et trajet face aux occurrences du même jour
  (`ENSEIGNANT_UNIQUE`, `GROUPE_UNIQUE`, `PAUSE`, `CAMPUS_TRAJET`) ;
- maxima des enseignants par jour et par semaine (`MAX_ENSEIGNANT`) ;
- salle : compatible avec le besoin et libre (`SALLE_UNIQUE`, `CAPACITE`...).

Méthode
-------
Les occurrences sont traitées une par une, les plus contraintes d'abord
(moins de salles compatibles, puis les plus longues). Chacune prend la place
valide la moins coûteuse : d'abord la même semaine, puis les semaines
voisines. Une occurrence replacée devient fixe pour les suivantes. Ce qui ne
trouve aucune place sort avec le statut « à programmer » et la raison :
jamais d'échec global, jamais de perte silencieuse.

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
from edt.domaine.contraintes import CodeContrainte, normaliser_codes
from edt.domaine.enumerations import CibleIndisponibilite, StatutOccurrence
from edt.domaine.heures import se_chevauchent
from edt.domaine.instantane import Besoin, Instantane, Occurrence
from edt.erreurs import ErreurDonneesInvalides
from edt.solveur.instanciation import empechement
from edt.solveur.regles import Regles

journal = logging.getLogger(__name__)

C = CodeContrainte


@dataclass(frozen=True)
class OptionsReplacement:
    """Réglages d'un replacement.

    - `contraintes_desactivees` : codes du catalogue à ignorer ;
    - `date_debut`, `date_fin` : fenêtre des dates permises (bornes incluses ;
      par défaut tout le semestre) ;
    - coût d'éloignement d'une place : `cout_semaine` par semaine d'écart avec
      la date d'origine, plus `cout_jour` par jour d'écart, plus
      `cout_horaire` si l'heure de début change. Avec les valeurs par défaut,
      la même semaine passe toujours avant une semaine voisine.
    """

    contraintes_desactivees: frozenset[CodeContrainte] = frozenset()
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

    def ajouter(self, besoin: Besoin, date: dt.date, debut: int, fin: int, salle_id) -> None:
        place = _Place(besoin, debut, fin, salle_id)
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

    def salle_libre(
        self, besoin: Besoin, date: dt.date, debut: int, fin: int, voisins: Sequence[_Place], ordre
    ) -> int | None:
        """Première salle de `ordre` compatible, disponible, libre et à portée de trajet."""
        regles = self.regles
        for salle_id in ordre:
            if regles.active(C.DISPO_SALLE) and regles.indisponible_le(
                CibleIndisponibilite.SALLE, salle_id, date, debut, fin
            ):
                continue
            if regles.active(C.SALLE_UNIQUE) and any(
                se_chevauchent(debut, fin, place.debut, place.fin)
                for place in self.par_salle.get((date, salle_id), ())
            ):
                continue
            if any(
                regles.codes_violes(
                    besoin, debut, fin, salle_id, v.besoin, v.debut, v.fin, v.salle_id
                )
                for v in voisins
            ):
                continue
            return salle_id
        return None


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
    regles = Regles(instantane, options.contraintes_desactivees)
    planning = _Planning(regles)
    for occurrence in occurrences_fixes:
        if occurrence.statut.occupe_les_ressources:
            planning.ajouter(
                instantane.besoin(occurrence.besoin_id),
                occurrence.date,
                occurrence.debut,
                occurrence.fin,
                occurrence.salle_id,
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
            planning.ajouter(besoin, nouvelle.date, nouvelle.debut, nouvelle.fin, nouvelle.salle_id)

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


def _candidates(
    regles: Regles,
    besoin: Besoin,
    origine: Occurrence,
    premier: dt.date,
    dernier: dt.date,
    options: OptionsReplacement,
) -> Iterator[tuple[int, dt.date, int]]:
    """Places `(coût, date, début)` permises par le calendrier et la grille, par coût croissant."""
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
    places = []
    for date in dates:
        jour = date.weekday()
        if besoin.jour_impose is not None and jour != besoin.jour_impose:
            continue
        impose = besoin.ponctuel or (
            besoin.jour_impose is not None and besoin.debut_impose is not None
        )
        if not impose and regles.active(C.JOURS) and not regles.jour_permis(jour, besoin):
            continue
        semaines = abs((lundi_de(date) - lundi_origine).days) // 7
        jours = abs((date - origine.date).days)
        for debut in regles.debuts_a_examiner(besoin, jour):
            if regles.refus_horaire(besoin, duree, jour, debut) is not None:
                continue
            cout = (
                options.cout_semaine * semaines
                + options.cout_jour * jours
                + options.cout_horaire * (debut != origine.debut)
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
        # À égalité, on garde la salle d'origine.
        salles = [origine.salle_id] + [salle for salle in salles if salle != origine.salle_id]
    refus: dict[str, int] = defaultdict(int)
    nb_examinees = 0
    for cout, date, debut in _candidates(regles, besoin, origine, premier, dernier, options):
        nb_examinees += 1
        fin = debut + duree
        candidate = dataclasses.replace(origine, date=date, debut=debut, fin=fin, salle_id=None)
        if empechement(regles, besoin, candidate) is not None:
            refus["un jour fermé ou une indisponibilité"] += 1
            continue
        if planning.maximum_depasse(besoin, date, duree):
            refus["le maximum horaire d'un enseignant"] += 1
            continue
        voisins = planning.voisins(besoin, date)
        if any(
            regles.codes_violes(besoin, debut, fin, None, v.besoin, v.debut, v.fin, v.salle_id)
            for v in voisins
        ):
            refus["l'emploi du temps de ses groupes ou de ses enseignants"] += 1
            continue
        salle_id = planning.salle_libre(besoin, date, debut, fin, voisins, salles)
        if salle_id is None:
            refus["l'absence de salle libre"] += 1
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
        return Replacement(origine, occurrence, cout)

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
    return _a_programmer(
        origine,
        f"aucune place libre entre le {premier:%d/%m/%Y} et le {dernier:%d/%m/%Y} : "
        f"{nb_examinees} créneau(x) examiné(s), tous écartés ({details})",
    )
