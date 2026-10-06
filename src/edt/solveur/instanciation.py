"""Instanciation : de la semaine type aux séances datées.

`instancier` est une fonction pure. Chaque séance de la semaine type donne
une **occurrence** par semaine active, à la date de son jour dans cette
semaine. Certaines occurrences ne peuvent pas avoir lieu à leur date : elles
sont renvoyées à part, avec la raison, pour être **replacées**
(`edt.solveur.replacement.replacer`).

Une occurrence est « à replacer » si, à sa date :

- le jour est fermé pour ses groupes (`JOUR_FERME`, `Instantane.fermeture`) ;
- sa salle, l'un de ses enseignants non fictifs ou un groupe lié à l'un de
  ses groupes est indisponible (`DISPO_SALLE`, `DISPO_ENSEIGNANT`,
  `DISPO_GROUPE`), que l'indisponibilité soit récurrente ou datée. Les
  indisponibilités datées ne restreignent pas la semaine type : c'est ici
  qu'elles sont prises en compte ;
- la date sort du semestre (première ou dernière semaine incomplète) ;
- sa place est déjà prise par une séance **en place** (saisie à la main ou
  importée, que l'appelant transmet) : même salle, même groupe ou même
  enseignant au même moment, pause ou trajet non tenus.

Seules les indisponibilités dures comptent. Une contrainte ignorée par
l'utilisateur n'est pas examinée, et une contrainte **souple** (niveau
« important » ou « facultatif ») n'empêche rien ici : la semaine type l'a
déjà arbitrée, et déplacer chaque semaine une séance que le moteur a placée
en connaissance de cause n'aurait pas de sens.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from edt.domaine.contraintes import CodeContrainte, Niveaux
from edt.domaine.enumerations import CibleIndisponibilite
from edt.domaine.heures import plage_vers_texte
from edt.domaine.instantane import Besoin, Instantane, Occurrence, Placement
from edt.solveur.regles import Regles

journal = logging.getLogger(__name__)

C = CodeContrainte


@dataclass(frozen=True)
class OccurrenceARevoir:
    """Occurrence qui ne peut pas avoir lieu à sa date.

    - `occurrence` : la séance datée telle que la semaine type la prévoit ;
    - `code` : contrainte qui l'empêche (`JOUR_FERME`, `DISPO_*`) ;
    - `raison` : phrase française pour l'utilisateur.
    """

    occurrence: Occurrence
    code: CodeContrainte
    raison: str


@dataclass(frozen=True)
class ResultatInstanciation:
    """Séances datées d'une semaine type.

    - `occurrences` : celles qui ont lieu à leur date (statut « planifiée ») ;
    - `a_replacer` : celles qui tombent un jour fermé ou pendant une
      indisponibilité, avec la raison. Elles ne figurent pas dans `occurrences`.
    """

    occurrences: tuple[Occurrence, ...]
    a_replacer: tuple[OccurrenceARevoir, ...]

    @property
    def nb_occurrences(self) -> int:
        """Nombre total de séances datées, à replacer comprises."""
        return len(self.occurrences) + len(self.a_replacer)


def empechement(
    regles: Regles, besoin: Besoin, occurrence: Occurrence
) -> tuple[CodeContrainte, str] | None:
    """Ce qui empêche l'occurrence d'avoir lieu à sa date (code, raison), ou `None`.

    Ne regarde que l'occurrence elle-même : calendrier et indisponibilités
    des contraintes dures. Les conflits avec d'autres occurrences relèvent
    du replacement.
    """
    instantane = regles.instantane
    date, debut, fin = occurrence.date, occurrence.debut, occurrence.fin
    if not instantane.date_debut <= date <= instantane.date_fin:
        return C.JOUR_FERME, f"le {date:%d/%m/%Y} est en dehors du semestre"
    if regles.active(C.JOUR_FERME):
        fermeture = instantane.fermeture(date, besoin)
        if fermeture is not None:
            return C.JOUR_FERME, f"le {date:%d/%m/%Y} est un jour fermé ({fermeture.libelle})"

    def indisponible(cible: CibleIndisponibilite, ressource_id: int, nom: str) -> str | None:
        indisponibilite = regles.indisponible_le(cible, ressource_id, date, debut, fin)
        if indisponibilite is None:
            return None
        motif = f" ({indisponibilite.motif})" if indisponibilite.motif.strip() else ""
        horaire = plage_vers_texte(indisponibilite.debut, indisponibilite.fin)
        return f"{nom} est indisponible le {date:%d/%m/%Y} ({horaire}){motif}"

    if regles.dure(C.DISPO_ENSEIGNANT):
        for enseignant_id in sorted(regles.enseignants_reels(besoin)):
            raison = indisponible(
                CibleIndisponibilite.ENSEIGNANT,
                enseignant_id,
                instantane.enseignant(enseignant_id).nom,
            )
            if raison:
                return C.DISPO_ENSEIGNANT, raison
    if regles.dure(C.DISPO_GROUPE):
        for groupe_id in sorted(regles.groupes_lies(besoin)):
            raison = indisponible(
                CibleIndisponibilite.GROUPE,
                groupe_id,
                f"le groupe {instantane.groupe(groupe_id).code}",
            )
            if raison:
                return C.DISPO_GROUPE, raison
    if regles.dure(C.DISPO_SALLE) and occurrence.salle_id is not None:
        raison = indisponible(
            CibleIndisponibilite.SALLE,
            occurrence.salle_id,
            f"la salle {instantane.salle(occurrence.salle_id).code}",
        )
        if raison:
            return C.DISPO_SALLE, raison
    return None


def _heurt(
    regles: Regles, besoin: Besoin, occurrence: Occurrence, en_place: Sequence[Occurrence]
) -> tuple[CodeContrainte, str] | None:
    """Contrainte dure violée face à une séance en place le même jour (code, raison), ou `None`."""
    for autre in en_place:
        besoin_autre = regles.instantane.besoin(autre.besoin_id)
        codes = [
            code
            for code in regles.codes_violes(
                besoin,
                occurrence.debut,
                occurrence.fin,
                occurrence.salle_id,
                besoin_autre,
                autre.debut,
                autre.fin,
                autre.salle_id,
            )
            if regles.dure(code)
        ]
        if codes:
            horaire = plage_vers_texte(autre.debut, autre.fin)
            return (
                codes[0],
                f"place prise par une séance saisie à la main ({besoin_autre.libelle}, le "
                f"{autre.date:%d/%m/%Y} {horaire})",
            )
    return None


def instancier(
    instantane: Instantane,
    placements: Sequence[Placement],
    contraintes_desactivees: Iterable[str] = (),
    en_place: Sequence[Occurrence] = (),
    niveaux: Niveaux | None = None,
) -> ResultatInstanciation:
    """Produit les séances datées de la semaine type `placements`.

    Chaque placement désigne une séance de l'instantané (`ErreurIntrouvable`
    sinon) et donne une occurrence par semaine active de cette séance. Les
    occurrences sortent triées par date, heure puis séance.

    `en_place` : séances datées qui gardent leur place quoi qu'il arrive
    (saisies à la main, importées). Une occurrence de la semaine type qui
    heurte l'une d'elles est rendue « à replacer » : la semaine type, valide
    seule, ne doit pas créer de double réservation avec une retouche.
    `niveaux` : niveaux des contraintes (voir `Regles`) ; seules les dures
    comptent ici.
    """
    regles = Regles(instantane, contraintes_desactivees, niveaux)
    occupees: dict[dt.date, list[Occurrence]] = defaultdict(list)
    for autre in en_place:
        if autre.statut.occupe_les_ressources:
            occupees[autre.date].append(autre)
    occurrences: list[Occurrence] = []
    a_replacer: list[OccurrenceARevoir] = []
    for placement in placements:
        seance = instantane.seance(placement.seance_id)
        for numero in sorted(seance.semaines):
            occurrence = Occurrence(
                id=None,
                besoin_id=seance.besoin.id,
                seance_id=seance.id,
                date=instantane.date_de(numero, placement.jour),
                debut=placement.debut,
                fin=placement.fin,
                salle_id=placement.salle_id,
            )
            obstacle = empechement(regles, seance.besoin, occurrence) or _heurt(
                regles, seance.besoin, occurrence, occupees.get(occurrence.date, ())
            )
            if obstacle is None:
                occurrences.append(occurrence)
            else:
                a_replacer.append(OccurrenceARevoir(occurrence, *obstacle))

    def ordre(occurrence: Occurrence) -> tuple:
        return occurrence.date, occurrence.debut, occurrence.seance_id or ""

    occurrences.sort(key=ordre)
    a_replacer.sort(key=lambda revoir: ordre(revoir.occurrence))
    journal.info(
        "Instanciation : %d séance(s) hebdomadaire(s), %d occurrence(s) datée(s), dont %d à "
        "replacer",
        len(placements),
        len(occurrences) + len(a_replacer),
        len(a_replacer),
    )
    return ResultatInstanciation(tuple(occurrences), tuple(a_replacer))
