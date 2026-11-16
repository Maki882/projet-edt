"""Dépliage : des besoins aux séances hebdomadaires à placer, avec leurs semaines actives.

Fonction pure, déterministe, exécutée avant toute résolution.

Semaines ouvertes d'un besoin
-----------------------------
Une semaine du semestre est *ouverte* pour un besoin si elle recoupe sa
fenêtre (`date_debut`..`date_fin`, par défaut tout le semestre) et si elle
n'est pas entièrement fermée pour lui : au moins un **jour travaillé** de
l'établissement (paramètre `jours_travailles`, lundi à vendredi par défaut)
n'est couvert par aucune fermeture globale ni par une fermeture visant l'un
de ses groupes. Une semaine entièrement fermée n'est pas
instanciée ; une semaine partiellement fermée (un jour férié) reste active
et l'occurrence qui tombe le jour fermé est replacée par la couche datée.

Règles par mode de volume
-------------------------
- `par_semaine` : `nombre` séances (rangs 1 à `nombre`), chacune active sur
  toutes les semaines ouvertes.
- `total` : `nombre` séances réparties **régulièrement** sur les S semaines
  ouvertes de la fenêtre. La semaine d'indice i (0 à S-1) reçoit
  `ceil((i + 1) * nombre / S) - ceil(i * nombre / S)` séances : chaque
  semaine en a `nombre // S` ou une de plus, les semaines les plus chargées
  sont espacées régulièrement et la première semaine est toujours servie.
  La séance de rang r est active les semaines qui reçoivent au moins r
  séances. Si `nombre` dépasse `S * max_par_semaine`, la demande est
  impossible : on déplie ce qui tient et on signale le reste (`VOLUME`).
- `ponctuel` : une séance (rang 1) active la seule semaine de sa date ; son
  jour est imposé par la date. Elle reste permise pendant une période
  d'examens ou une semaine spéciale, pas un jour férié ni pendant des vacances.

Aucune donnée n'est corrigée : tout besoin qui ne peut pas être déplié
complètement produit un `ProblemeDepliage` explicite.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from edt.domaine.calendrier import Semaine, semaine_de
from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import JOURS, ModeVolume
from edt.domaine.instantane import Besoin, JourFerme, ProblemeDepliage, SeanceAPlacer, id_seance

JOURS_OUVRES_PAR_DEFAUT: tuple[int, ...] = (0, 1, 2, 3, 4)
"""Lundi à vendredi : valeur par défaut du paramètre `jours_travailles`."""

_JoursFermes = tuple[frozenset[int], tuple[tuple[frozenset[int], range], ...]]
"""Jours fermés pour tous, puis plages de jours fermés pour certains groupes (numéros de jour)."""


@dataclass(frozen=True)
class Depliage:
    """Résultat du dépliage : les séances à placer et les besoins problématiques."""

    seances: tuple[SeanceAPlacer, ...]
    problemes: tuple[ProblemeDepliage, ...]


def repartition_reguliere(nombre: int, nb_semaines: int) -> list[int]:
    """Nombre de séances par semaine pour répartir `nombre` séances sur `nb_semaines`.

    Exemple : 10 séances sur 15 semaines donnent 1, 1, 0, 1, 1, 0, ...
    """
    if nombre < 0 or nb_semaines <= 0:
        raise ValueError(
            f"Répartition impossible : {nombre} séance(s) sur {nb_semaines} semaine(s)."
        )

    def plafond(indice: int) -> int:
        return -((-indice * nombre) // nb_semaines)

    return [plafond(indice + 1) - plafond(indice) for indice in range(nb_semaines)]


class _Fermetures:
    """Semaines et fermetures d'un semestre, préparées une fois pour répondre vite.

    Interroger chaque fermeture pour chaque jour de chaque semaine de chaque
    besoin coûte cher sur un établissement complet (plus d'un million d'appels
    à `JourFerme.concerne`). Ici les dates deviennent des numéros de jour
    (`date.toordinal()`), une fermeture devient une plage de numéros, et « la
    semaine est entièrement fermée » devient « ses jours ouvrés sont tous dans
    l'ensemble des jours fermés ». La règle est exactement celle de
    `JourFerme.concerne` ; seule la façon de la calculer change.
    """

    def __init__(
        self,
        semaines: Iterable[Semaine],
        jours_fermes: Iterable[JourFerme],
        jours_travailles: Iterable[int] = JOURS_OUVRES_PAR_DEFAUT,
    ) -> None:
        travailles = tuple(sorted(set(jours_travailles)))
        if not travailles:
            raise ValueError("Au moins un jour travaillé est attendu.")
        self._semaines = tuple(
            (semaine, lundi, tuple(lundi + jour for jour in travailles))
            for semaine, lundi in ((semaine, semaine.lundi.toordinal()) for semaine in semaines)
        )
        self._fermetures = tuple(jours_fermes)
        # Une préparation par nature de séance (ponctuelle ou non), faite à la demande.
        self._preparees: dict[bool, _JoursFermes] = {}

    def _preparer(self, ponctuelle: bool) -> _JoursFermes:
        if not self._semaines:
            return frozenset(), ()
        premier = min(lundi for _, lundi, _ in self._semaines)
        dernier = max(lundi for _, lundi, _ in self._semaines) + 6
        pour_tous: set[int] = set()
        par_groupes: list[tuple[frozenset[int], range]] = []
        for ferme in self._fermetures:
            # Seuls comptent les jours des semaines données : on y borne la plage.
            jours = range(
                max(ferme.date_debut.toordinal(), premier),
                min(ferme.date_fin.toordinal(), dernier) + 1,
            )
            if not jours or (ponctuelle and ferme.nature.admet_ponctuels):
                continue
            if ferme.groupes is None:
                pour_tous.update(jours)
            else:
                par_groupes.append((ferme.groupes, jours))
        return frozenset(pour_tous), tuple(par_groupes)

    def jours_fermes(self, groupes: Iterable[int], ponctuelle: bool = False) -> frozenset[int]:
        """Numéros des jours où une séance des groupes `groupes` ne peut pas avoir lieu."""
        if ponctuelle not in self._preparees:
            self._preparees[ponctuelle] = self._preparer(ponctuelle)
        pour_tous, par_groupes = self._preparees[ponctuelle]
        propres = [jours for vises, jours in par_groupes if not vises.isdisjoint(groupes)]
        return pour_tous.union(*propres) if propres else pour_tous

    def semaines_ouvertes(self, besoin: Besoin) -> tuple[Semaine, ...]:
        """Semaines où le besoin peut avoir cours (voir la règle en tête de module)."""
        fermes = self.jours_fermes(besoin.groupes, besoin.ponctuel)
        debut = None if besoin.date_debut is None else besoin.date_debut.toordinal()
        fin = None if besoin.date_fin is None else besoin.date_fin.toordinal()
        return tuple(
            semaine
            for semaine, lundi, jours_ouvres in self._semaines
            if (debut is None or lundi + 6 >= debut)  # elle ne finit pas avant la fenêtre
            and (fin is None or lundi <= fin)  # elle ne commence pas après la fenêtre
            and not fermes.issuperset(jours_ouvres)  # elle n'est pas entièrement fermée
        )


def semaines_ouvertes(
    besoin: Besoin,
    semaines: Sequence[Semaine],
    jours_fermes: Iterable[JourFerme],
    jours_travailles: Iterable[int] = JOURS_OUVRES_PAR_DEFAUT,
) -> tuple[Semaine, ...]:
    """Semaines du semestre où le besoin peut avoir cours (voir la règle en tête de module)."""
    return _Fermetures(semaines, jours_fermes, jours_travailles).semaines_ouvertes(besoin)


def deplier(
    besoins: Iterable[Besoin],
    semaines: Sequence[Semaine],
    jours_fermes: Iterable[JourFerme],
    jours_travailles: Iterable[int] = JOURS_OUVRES_PAR_DEFAUT,
    periode: tuple[dt.date, dt.date] | None = None,
) -> Depliage:
    """Déplie les besoins d'un semestre en séances hebdomadaires à placer.

    `semaines` sont les semaines du semestre (voir
    `edt.domaine.calendrier.semaines_entre`), `jours_fermes` les fermetures
    du calendrier, `jours_travailles` les jours travaillés de l'établissement
    (`Parametres.jours_travailles`), `periode` les dates exactes du semestre
    (une séance ponctuelle datée dans la première ou la dernière semaine mais
    hors de ces dates est un problème, pas une séance). Un besoin sans aucun
    groupe est un problème de dépliage : la demande est incomplète. Les
    séances sortent triées par besoin puis par rang.
    """
    fermetures = tuple(jours_fermes)
    calendrier = _Fermetures(semaines, fermetures, jours_travailles)
    seances: list[SeanceAPlacer] = []
    problemes: list[ProblemeDepliage] = []

    for besoin in sorted(besoins, key=lambda b: b.id):
        if not besoin.groupes:
            problemes.append(
                ProblemeDepliage(
                    besoin.id,
                    CodeContrainte.VOLUME,
                    f"{besoin.libelle} : aucun groupe n'est inscrit à ce besoin ; compléter la "
                    "demande avant le calcul.",
                )
            )
            continue
        if besoin.ponctuel:
            _deplier_ponctuel(besoin, semaines, fermetures, seances, problemes, periode)
            continue

        ouvertes = calendrier.semaines_ouvertes(besoin)
        if not ouvertes:
            problemes.append(
                ProblemeDepliage(
                    besoin.id,
                    CodeContrainte.VOLUME,
                    f"{besoin.libelle} : aucune semaine de cours ouverte dans sa période "
                    "(fenêtre hors du semestre ou semaines toutes fermées).",
                )
            )
            continue

        if besoin.mode_volume is ModeVolume.PAR_SEMAINE:
            toutes = frozenset(semaine.numero for semaine in ouvertes)
            seances.extend(
                SeanceAPlacer(id_seance(besoin.id, rang), besoin, rang, toutes)
                for rang in range(1, besoin.nombre + 1)
            )
            continue

        capacite = len(ouvertes) * besoin.max_par_semaine
        if besoin.nombre > capacite:
            problemes.append(
                ProblemeDepliage(
                    besoin.id,
                    CodeContrainte.VOLUME,
                    f"{besoin.libelle} : {besoin.nombre} séances demandées, mais la période "
                    f"n'en permet que {capacite} ({len(ouvertes)} semaine(s) ouverte(s) à "
                    f"{besoin.max_par_semaine} séance(s) par semaine au plus).",
                )
            )
        par_semaine = repartition_reguliere(min(besoin.nombre, capacite), len(ouvertes))
        for rang in range(1, max(par_semaine) + 1):
            actives = frozenset(
                semaine.numero
                for semaine, quantite in zip(ouvertes, par_semaine, strict=True)
                if quantite >= rang
            )
            seances.append(SeanceAPlacer(id_seance(besoin.id, rang), besoin, rang, actives))

    return Depliage(seances=tuple(seances), problemes=tuple(problemes))


def _deplier_ponctuel(
    besoin: Besoin,
    semaines: Sequence[Semaine],
    fermetures: tuple[JourFerme, ...],
    seances: list[SeanceAPlacer],
    problemes: list[ProblemeDepliage],
    periode: tuple[dt.date, dt.date] | None = None,
) -> None:
    jour = besoin.date_ponctuelle
    if jour is None:
        problemes.append(
            ProblemeDepliage(
                besoin.id,
                CodeContrainte.VOLUME,
                f"{besoin.libelle} : séance ponctuelle sans date.",
            )
        )
        return
    semaine = semaine_de(jour, semaines)
    hors_periode = periode is not None and not periode[0] <= jour <= periode[1]
    if semaine is None or hors_periode:
        bornes = f" (du {periode[0]:%d/%m/%Y} au {periode[1]:%d/%m/%Y})" if periode else ""
        problemes.append(
            ProblemeDepliage(
                besoin.id,
                CodeContrainte.VOLUME,
                f"{besoin.libelle} : la date du {jour:%d/%m/%Y} est en dehors du semestre{bornes}.",
            )
        )
        return
    if besoin.jour_impose is not None and besoin.jour_impose != jour.weekday():
        problemes.append(
            ProblemeDepliage(
                besoin.id,
                CodeContrainte.VOLUME,
                f"{besoin.libelle} : le jour imposé ({JOURS[besoin.jour_impose]}) contredit "
                f"la date du {jour:%d/%m/%Y} ({JOURS[jour.weekday()]}) ; corriger le besoin.",
            )
        )
    for ferme in fermetures:
        if ferme.concerne(jour, besoin.groupes, ponctuelle=True):
            problemes.append(
                ProblemeDepliage(
                    besoin.id,
                    CodeContrainte.JOUR_FERME,
                    f"{besoin.libelle} : la date du {jour:%d/%m/%Y} est un jour fermé "
                    f"({ferme.libelle}).",
                )
            )
            break
    seances.append(SeanceAPlacer(id_seance(besoin.id, 1), besoin, 1, frozenset({semaine.numero})))
