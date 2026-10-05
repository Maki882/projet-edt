"""Dépliage : des besoins aux séances hebdomadaires à placer, avec leurs semaines actives.

Fonction pure, déterministe, exécutée avant toute résolution.

Semaines ouvertes d'un besoin
-----------------------------
Une semaine du semestre est *ouverte* pour un besoin si elle recoupe sa
fenêtre (`date_debut`..`date_fin`, par défaut tout le semestre) et si elle
n'est pas entièrement fermée pour lui : au moins un jour du lundi au
vendredi n'est couvert par aucune fermeture globale ni par une fermeture
visant l'un de ses groupes. Une semaine entièrement fermée n'est pas
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

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from edt.domaine.calendrier import Semaine, semaine_de
from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import JOURS, ModeVolume
from edt.domaine.instantane import Besoin, JourFerme, ProblemeDepliage, SeanceAPlacer, id_seance

JOURS_OUVRES = range(5)
"""Lundi à vendredi : les jours examinés pour dire qu'une semaine est entièrement fermée."""


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


def semaines_ouvertes(
    besoin: Besoin, semaines: Sequence[Semaine], jours_fermes: Iterable[JourFerme]
) -> tuple[Semaine, ...]:
    """Semaines du semestre où le besoin peut avoir cours (voir la règle en tête de module)."""
    fermetures = tuple(jours_fermes)
    ouvertes = []
    for semaine in semaines:
        if besoin.date_debut is not None and semaine.dimanche < besoin.date_debut:
            continue
        if besoin.date_fin is not None and semaine.lundi > besoin.date_fin:
            continue
        entierement_fermee = all(
            any(
                ferme.concerne(semaine.date_du(jour), besoin.groupes, ponctuelle=besoin.ponctuel)
                for ferme in fermetures
            )
            for jour in JOURS_OUVRES
        )
        if not entierement_fermee:
            ouvertes.append(semaine)
    return tuple(ouvertes)


def deplier(
    besoins: Iterable[Besoin], semaines: Sequence[Semaine], jours_fermes: Iterable[JourFerme]
) -> Depliage:
    """Déplie les besoins d'un semestre en séances hebdomadaires à placer.

    `semaines` sont les semaines du semestre (voir
    `edt.domaine.calendrier.semaines_entre`), `jours_fermes` les fermetures
    du calendrier. Les séances sortent triées par besoin puis par rang.
    """
    fermetures = tuple(jours_fermes)
    seances: list[SeanceAPlacer] = []
    problemes: list[ProblemeDepliage] = []

    for besoin in sorted(besoins, key=lambda b: b.id):
        if besoin.ponctuel:
            _deplier_ponctuel(besoin, semaines, fermetures, seances, problemes)
            continue

        ouvertes = semaines_ouvertes(besoin, semaines, fermetures)
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
    if semaine is None:
        problemes.append(
            ProblemeDepliage(
                besoin.id,
                CodeContrainte.VOLUME,
                f"{besoin.libelle} : la date du {jour:%d/%m/%Y} est en dehors du semestre.",
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
