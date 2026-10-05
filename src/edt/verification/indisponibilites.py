"""Indisponibilités ramenées à la semaine type.

Sur la semaine type, une séance est décrite par un jour de la semaine et
des semaines actives. Pour la comparer à une indisponibilité, on projette
celle-ci sous la même forme : une `PlageInterdite` = un jour, une plage
horaire `[debut, fin[` et l'ensemble des semaines du semestre où elle
s'applique ce jour-là.

- une indisponibilité **récurrente** (`jour` renseigné) donne une plage sur
  ce jour, toutes les semaines du semestre ;
- une indisponibilité **datée** (`date_debut`..`date_fin`) donne une plage
  par jour de la semaine couvert, avec les seules semaines du semestre où ce
  jour tombe dans la période.

Seules les indisponibilités **dures** (`dure` vrai) comptent en V1 ; les
autres préparent les préférences des versions suivantes.

Note : le brief prévoyait une fonction `projeter_indisponibilite` du domaine,
qui n'existe pas encore dans le socle ; ce module la remplace en attendant.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from edt.domaine.heures import MINUTES_PAR_JOUR
from edt.domaine.instantane import Indisponibilite, Instantane
from edt.erreurs import ErreurDonneesInvalides


@dataclass(frozen=True)
class PlageInterdite:
    """Une indisponibilité projetée sur un jour de la semaine type."""

    indisponibilite: Indisponibilite
    jour: int
    semaines: frozenset[int]

    @property
    def debut(self) -> int:
        """Début de la plage interdite (minutes depuis minuit)."""
        return self.indisponibilite.debut

    @property
    def fin(self) -> int:
        """Fin de la plage interdite (minutes depuis minuit)."""
        return self.indisponibilite.fin


def valider_indisponibilite(indisponibilite: Indisponibilite) -> None:
    """Lève `ErreurDonneesInvalides` si l'indisponibilité ne décrit pas une plage vérifiable."""
    defauts: list[str] = []
    if indisponibilite.jour is not None:
        if not 0 <= indisponibilite.jour <= 6:
            defauts.append(f"jour {indisponibilite.jour!r} hors de 0 (lundi) à 6 (dimanche)")
    elif indisponibilite.date_debut is None or indisponibilite.date_fin is None:
        defauts.append("ni jour de la semaine ni période datée complète")
    elif indisponibilite.date_fin < indisponibilite.date_debut:
        defauts.append("la fin de la période précède son début")
    if not 0 <= indisponibilite.debut < indisponibilite.fin <= MINUTES_PAR_JOUR:
        defauts.append(
            f"plage horaire invalide ({indisponibilite.debut} à {indisponibilite.fin} minutes)"
        )
    if defauts:
        raise ErreurDonneesInvalides(
            f"L'indisponibilité n° {indisponibilite.id} ({indisponibilite.cible.libelle.lower()} "
            f"{indisponibilite.ressource_id}) est incohérente : vérification impossible.",
            details=defauts,
        )


def projeter(
    instantane: Instantane, indisponibilite: Indisponibilite
) -> tuple[PlageInterdite, ...]:
    """Projette une indisponibilité sur la semaine type (voir l'en-tête du module).

    Renvoie les plages triées par jour ; une période datée hors du semestre
    ne donne aucune plage.
    """
    valider_indisponibilite(indisponibilite)
    if indisponibilite.jour is not None:
        toutes = frozenset(semaine.numero for semaine in instantane.semaines)
        return (PlageInterdite(indisponibilite, indisponibilite.jour, toutes),) if toutes else ()

    # Forme datée : validée ci-dessus, les deux dates sont renseignées.
    assert indisponibilite.date_debut is not None and indisponibilite.date_fin is not None
    semaines_par_jour: dict[int, set[int]] = {}
    for semaine in instantane.semaines:
        premier = max(semaine.lundi, indisponibilite.date_debut)
        dernier = min(semaine.dimanche, indisponibilite.date_fin)
        jour = premier
        while jour <= dernier:
            semaines_par_jour.setdefault(jour.weekday(), set()).add(semaine.numero)
            jour += dt.timedelta(days=1)
    return tuple(
        PlageInterdite(indisponibilite, jour, frozenset(numeros))
        for jour, numeros in sorted(semaines_par_jour.items())
    )
