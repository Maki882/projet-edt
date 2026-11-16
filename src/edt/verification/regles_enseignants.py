"""Maxima horaires des enseignants (`MAX_ENSEIGNANT`).

Pour chaque enseignant **non fictif** dont un maximum est renseigné, les
durées placées (`fin - debut`) de ses séances sont additionnées :

- semaine type : pour chaque semaine du semestre, les séances actives cette
  semaine-là (maximum hebdomadaire) ; pour chaque jour et chaque semaine,
  les séances de ce jour actives cette semaine-là (maximum journalier).
  Une seule anomalie par enseignant pour le maximum hebdomadaire, une par
  enseignant et par jour de la semaine type pour le maximum journalier : le
  message détaille les semaines en cause et leurs totaux ;
- couche datée : par date réelle (maximum journalier) et par semaine réelle
  du lundi au dimanche (maximum hebdomadaire), une anomalie par dépassement.

Un total égal au maximum est permis.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterator, Sequence

from edt.domaine.calendrier import lundi_de
from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import Gravite
from edt.domaine.instantane import Enseignant
from edt.verification.contexte import Contexte, Element
from edt.verification.contrat import Anomalie
from edt.verification.messages import date_texte, duree, nom_jour, semaines_texte


def verifier_maxima(ctx: Contexte, elements: Sequence[Element]) -> Iterator[Anomalie]:
    """Anomalies `MAX_ENSEIGNANT` (voir l'en-tête du module)."""
    if not ctx.actif(CodeContrainte.MAX_ENSEIGNANT):
        return
    par_enseignant: dict[int, list[Element]] = defaultdict(list)
    for element in elements:
        for enseignant in ctx.enseignants_reels(element.besoin):
            par_enseignant[enseignant].append(element)
    for enseignant_id in sorted(par_enseignant):
        enseignant = ctx.instantane.enseignant(enseignant_id)
        if enseignant.max_minutes_jour is None and enseignant.max_minutes_semaine is None:
            continue
        seances = par_enseignant[enseignant_id]
        if ctx.datee:
            yield from _maxima_dates(ctx, enseignant, seances)
        else:
            yield from _maxima_semaine_type(ctx, enseignant, seances)


def _detail_par_semaine(depassements: dict[int, int]) -> str:
    """« 18 h les semaines 1 à 10 ; 20 h la semaine 11 » (semaines regroupées par total)."""
    par_total: dict[int, list[int]] = defaultdict(list)
    for semaine, total in depassements.items():
        par_total[total].append(semaine)
    morceaux = sorted(par_total.items(), key=lambda item: min(item[1]))
    return " ; ".join(
        f"{duree(total)} de cours {semaines_texte(semaines, article=True)}"
        for total, semaines in morceaux
    )


def _maxima_semaine_type(
    ctx: Contexte, enseignant: Enseignant, seances: list[Element]
) -> Iterator[Anomalie]:
    maximum = enseignant.max_minutes_semaine
    if maximum is not None:
        totaux: dict[int, int] = defaultdict(int)
        for element in seances:
            for semaine in element.semaines or ():
                totaux[semaine] += element.duree
        depassements = {semaine: total for semaine, total in totaux.items() if total > maximum}
        if depassements:
            yield ctx.anomalie(
                CodeContrainte.MAX_ENSEIGNANT,
                Gravite.ERREUR,
                f"{enseignant.nom} dépasse son maximum de {duree(maximum)} de cours par "
                f"semaine : {_detail_par_semaine(depassements)}.",
                [e for e in seances if not depassements.keys().isdisjoint(e.semaines or ())],
                enseignants=(enseignant.id,),
            )

    maximum = enseignant.max_minutes_jour
    if maximum is None:
        return
    par_jour: dict[int, list[Element]] = defaultdict(list)
    for element in seances:
        par_jour[element.jour].append(element)
    for jour in sorted(par_jour):
        totaux = defaultdict(int)
        for element in par_jour[jour]:
            for semaine in element.semaines or ():
                totaux[semaine] += element.duree
        depassements = {semaine: total for semaine, total in totaux.items() if total > maximum}
        if depassements:
            yield ctx.anomalie(
                CodeContrainte.MAX_ENSEIGNANT,
                Gravite.ERREUR,
                f"{enseignant.nom} dépasse son maximum de {duree(maximum)} de cours par jour "
                f"le {nom_jour(jour)} : {_detail_par_semaine(depassements)}.",
                [e for e in par_jour[jour] if not depassements.keys().isdisjoint(e.semaines or ())],
                enseignants=(enseignant.id,),
                jour=jour,
            )


def _maxima_dates(
    ctx: Contexte, enseignant: Enseignant, seances: list[Element]
) -> Iterator[Anomalie]:
    maximum = enseignant.max_minutes_jour
    if maximum is not None:
        par_date: dict[dt.date, list[Element]] = defaultdict(list)
        for element in seances:
            assert element.date is not None  # couche datée
            par_date[element.date].append(element)
        for jour in sorted(par_date):
            total = sum(element.duree for element in par_date[jour])
            if total > maximum:
                yield ctx.anomalie(
                    CodeContrainte.MAX_ENSEIGNANT,
                    Gravite.ERREUR,
                    f"{enseignant.nom} dépasse son maximum de {duree(maximum)} de cours par jour "
                    f"le {date_texte(jour)} : {duree(total)} de cours.",
                    par_date[jour],
                    enseignants=(enseignant.id,),
                    date=jour,
                )

    maximum = enseignant.max_minutes_semaine
    if maximum is None:
        return
    par_semaine: dict[dt.date, list[Element]] = defaultdict(list)
    for element in seances:
        assert element.date is not None  # couche datée
        par_semaine[lundi_de(element.date)].append(element)
    for lundi in sorted(par_semaine):
        total = sum(element.duree for element in par_semaine[lundi])
        if total > maximum:
            yield ctx.anomalie(
                CodeContrainte.MAX_ENSEIGNANT,
                Gravite.ERREUR,
                f"{enseignant.nom} dépasse son maximum de {duree(maximum)} de cours par semaine "
                f"la semaine du {date_texte(lundi)} : {duree(total)} de cours.",
                par_semaine[lundi],
                enseignants=(enseignant.id,),
                date=lundi,
            )
