"""Vérification des séances datées : `verifier_occurrences(instantane, occurrences, contraintes_actives)`.

Mêmes règles que la semaine type (`semaine_type`), avec ces différences :

- deux occurrences **coexistent** si elles ont lieu à la même date ;
- seules les occurrences qui ont lieu (`statut.occupe_les_ressources` :
  planifiée ou déplacée) sont vérifiées et occupent salle, enseignants et
  groupes ; une occurrence annulée est ignorée ; une occurrence « à
  programmer » donne un avertissement `VOLUME` ;
- les indisponibilités (récurrentes et datées) s'évaluent à la date avec
  `Indisponibilite.concerne(date)` ;
- les maxima des enseignants se comptent par date réelle et par semaine
  réelle (du lundi au dimanche) ;
- `JOUR_FERME` : une occurrence qui a lieu à une date fermée pour son besoin
  (`Instantane.fermeture`) est une erreur ;
- `HORAIRES` (e) : un besoin ponctuel impose sa date, un besoin à jour imposé
  impose le jour de la semaine ;
- `VERROU` : une occurrence **planifiée** d'une séance verrouillée a lieu au
  jour de la semaine, aux heures et dans la salle du verrou (une occurrence
  déplacée a, par définition, quitté sa place : elle n'est pas concernée) ;
- `VOLUME` : une occurrence d'un besoin inconnu est une erreur (elle ne peut
  pas être vérifiée) ; une occurrence rattachée à une séance hebdomadaire
  absente du semestre est un avertissement (vérifiée sans son verrou
  éventuel). Le compte des occurrences par séance n'est pas vérifié ici :
  la liste reçue peut n'être qu'une partie du semestre.

Une occurrence mal formée (heures hors de la journée, fin avant le début,
salle inconnue, identifiant en double, séance d'un autre besoin) rend la
vérification impossible : `ErreurDonneesInvalides` avec tous les défauts.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Iterable, Iterator, Sequence

from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import Gravite, StatutOccurrence
from edt.domaine.instantane import Besoin, Instantane, Occurrence, SeanceAPlacer
from edt.erreurs import ErreurDonneesInvalides
from edt.verification.contexte import (
    Contexte,
    Element,
    cle_tri,
    defauts_de_plage,
    resoudre_codes,
)
from edt.verification.contrat import Anomalie
from edt.verification.messages import date_texte, plage
from edt.verification.regles_enseignants import verifier_maxima
from edt.verification.regles_paires import verifier_paires
from edt.verification.regles_seance import ecart_au_verrou, verifier_seances

journal = logging.getLogger(__name__)


def verifier_occurrences(
    instantane: Instantane,
    occurrences: Sequence[Occurrence],
    contraintes_actives: Iterable[str] | None = None,
) -> list[Anomalie]:
    """Liste les anomalies d'un ensemble de séances datées (liste vide : valide).

    Voir l'en-tête du module pour les différences avec la semaine type.
    Lève `ErreurDonneesInvalides` si un code de contrainte est inconnu ou si
    une occurrence est mal formée.
    """
    chrono = time.perf_counter()
    codes = resoudre_codes(contraintes_actives)
    occurrences = tuple(occurrences)
    valider_occurrences(instantane, occurrences)
    ctx = Contexte(instantane, codes, datee=True)
    besoins = {besoin.id: besoin for besoin in instantane.besoins}
    seances = {seance.id: seance for seance in instantane.seances}

    anomalies: list[Anomalie] = []
    elements: list[Element] = []
    for position, occurrence in enumerate(occurrences):
        besoin = besoins.get(occurrence.besoin_id)
        if besoin is None:
            anomalies.extend(_besoin_inconnu(ctx, occurrence))
            continue
        seance = None if occurrence.seance_id is None else seances.get(occurrence.seance_id)
        if occurrence.seance_id is not None and seance is None:
            anomalies.extend(_seance_inconnue(ctx, occurrence, besoin))
        element = _element(occurrence, besoin, seance, position)
        if occurrence.statut is StatutOccurrence.A_PROGRAMMER:
            anomalies.extend(_a_programmer(ctx, element))
            continue
        if not occurrence.statut.occupe_les_ressources:
            continue
        elements.append(element)
        anomalies.extend(_jour_ferme(ctx, element))
        if occurrence.statut is StatutOccurrence.PLANIFIEE and seance is not None:
            anomalies.extend(_verrou(ctx, element, seance))

    anomalies.extend(verifier_seances(ctx, elements))
    anomalies.extend(verifier_paires(ctx, elements))
    anomalies.extend(verifier_maxima(ctx, elements))
    anomalies.sort(key=cle_tri)

    journal.info(
        "Vérification des séances datées de « %s » : %d occurrence(s) dont %d vérifiée(s), "
        "%d anomalie(s) dont %d bloquante(s), %d contrainte(s) vérifiée(s), %.0f ms.",
        instantane.libelle,
        len(occurrences),
        len(elements),
        len(anomalies),
        sum(anomalie.bloquante for anomalie in anomalies),
        len(codes),
        (time.perf_counter() - chrono) * 1000,
    )
    return anomalies


def valider_occurrences(instantane: Instantane, occurrences: Sequence[Occurrence]) -> None:
    """Lève `ErreurDonneesInvalides` (avec tous les défauts) si une occurrence est mal formée."""
    salles = {salle.id for salle in instantane.salles}
    numeros_par_id: dict[int, int] = {}
    defauts: list[str] = []
    for numero, occurrence in enumerate(occurrences, start=1):
        if not isinstance(occurrence, Occurrence):
            defauts.append(
                f"élément n° {numero} : une séance datée est attendue "
                f"(lu : {type(occurrence).__name__})"
            )
            continue
        nom = f"séance datée n° {numero}"
        if occurrence.id is not None:
            nom += f" (identifiant {occurrence.id})"
            if occurrence.id in numeros_par_id:
                defauts.append(
                    f"{nom} : identifiant déjà porté par la séance datée "
                    f"n° {numeros_par_id[occurrence.id]}"
                )
            else:
                numeros_par_id[occurrence.id] = numero
        if not isinstance(occurrence.date, dt.date):
            defauts.append(f"{nom} : date absente ou invalide ({occurrence.date!r})")
        if not isinstance(occurrence.statut, StatutOccurrence):
            defauts.append(f"{nom} : statut invalide ({occurrence.statut!r})")
        defauts.extend(
            f"{nom} : {defaut}"
            for defaut in defauts_de_plage(None, occurrence.debut, occurrence.fin)
        )
        if occurrence.salle_id is not None and occurrence.salle_id not in salles:
            defauts.append(f"{nom} : salle {occurrence.salle_id!r} inconnue dans ce semestre")
        if occurrence.seance_id is not None:
            besoin, _, rang = str(occurrence.seance_id).partition("-")
            if not (besoin.isdigit() and rang.isdigit()) or int(besoin) != occurrence.besoin_id:
                defauts.append(
                    f"{nom} : la séance « {occurrence.seance_id} » n'appartient pas au "
                    f"besoin {occurrence.besoin_id}"
                )
    if defauts:
        raise ErreurDonneesInvalides(
            f"{len(defauts)} défaut(s) dans les séances datées reçues : la vérification est "
            "impossible tant qu'ils ne sont pas corrigés.",
            details=defauts,
        )


def _element(
    occurrence: Occurrence, besoin: Besoin, seance: SeanceAPlacer | None, position: int
) -> Element:
    imposee = (
        besoin.debut_impose is not None
        or besoin.ponctuel
        or (seance is not None and seance.verrou is not None)
    )
    return Element(
        besoin=besoin,
        jour=occurrence.date.weekday(),
        debut=occurrence.debut,
        fin=occurrence.fin,
        salle_id=occurrence.salle_id,
        position=position,
        seance_id=None if seance is None else seance.id,
        occurrence_id=occurrence.id,
        date=occurrence.date,
        imposee=imposee,
        jour_impose=besoin.jour_impose if besoin.date_ponctuelle is None else None,
        date_imposee=besoin.date_ponctuelle,
    )


def _besoin_inconnu(ctx: Contexte, occurrence: Occurrence) -> Iterator[Anomalie]:
    if not ctx.actif(CodeContrainte.VOLUME):
        journal.warning(
            "Séance datée du %s rattachée au besoin inconnu %d : non vérifiée "
            "(contrainte VOLUME non demandée).",
            occurrence.date.isoformat(),
            occurrence.besoin_id,
        )
        return
    yield ctx.anomalie(
        CodeContrainte.VOLUME,
        Gravite.ERREUR,
        f"La séance datée du {date_texte(occurrence.date)} "
        f"{plage(occurrence.debut, occurrence.fin)} se rattache à un besoin inconnu dans ce "
        f"semestre (n° {occurrence.besoin_id}) : elle ne peut pas être vérifiée.",
        occurrences=() if occurrence.id is None else (occurrence.id,),
        besoins=(occurrence.besoin_id,),
        salles=() if occurrence.salle_id is None else (occurrence.salle_id,),
        date=occurrence.date,
    )


def _seance_inconnue(ctx: Contexte, occurrence: Occurrence, besoin: Besoin) -> Iterator[Anomalie]:
    if not ctx.actif(CodeContrainte.VOLUME):
        journal.warning(
            "Séance datée du %s rattachée à la séance hebdomadaire inconnue %s : vérifiée sans "
            "son verrou éventuel (contrainte VOLUME non demandée).",
            occurrence.date.isoformat(),
            occurrence.seance_id,
        )
        return
    yield ctx.anomalie(
        CodeContrainte.VOLUME,
        Gravite.AVERTISSEMENT,
        f"La séance {ctx.libelle_seance(besoin, None)} du {date_texte(occurrence.date)} se "
        f"rattache à la séance hebdomadaire « {occurrence.seance_id} », qui n'existe pas dans "
        "ce semestre : elle est vérifiée sans son éventuel verrou.",
        besoins=(besoin.id,),
        groupes=besoin.groupes,
        date=occurrence.date,
    )


def _a_programmer(ctx: Contexte, element: Element) -> Iterator[Anomalie]:
    if not ctx.actif(CodeContrainte.VOLUME):
        return
    yield ctx.anomalie(
        CodeContrainte.VOLUME,
        Gravite.AVERTISSEMENT,
        f"La séance {ctx.libelle(element)} prévue {ctx.moment(element)} reste à programmer : "
        "aucune place valide ne lui a encore été trouvée.",
        (element,),
        **ctx.ressources(element),
        **ctx.reperes(element),
    )


def _jour_ferme(ctx: Contexte, element: Element) -> Iterator[Anomalie]:
    if not ctx.actif(CodeContrainte.JOUR_FERME):
        return
    assert element.date is not None  # couche datée
    fermeture = ctx.instantane.fermeture(element.date, element.besoin)
    if fermeture is None:
        return
    groupes = element.besoin.groupes
    if fermeture.groupes is not None:
        groupes = groupes & fermeture.groupes
    yield ctx.anomalie(
        CodeContrainte.JOUR_FERME,
        Gravite.ERREUR,
        f"La séance {ctx.libelle(element)} a lieu {ctx.moment(element)} alors que ce jour est "
        f"fermé ({fermeture.libelle}, {fermeture.nature.libelle.lower()}).",
        (element,),
        groupes=groupes,
        enseignants=element.besoin.enseignants,
        salles=() if element.salle_id is None else (element.salle_id,),
        **ctx.reperes(element),
    )


def _verrou(ctx: Contexte, element: Element, seance: SeanceAPlacer) -> Iterator[Anomalie]:
    if seance.verrou is None or not ctx.actif(CodeContrainte.VERROU):
        return
    ecart = ecart_au_verrou(ctx, element, seance.verrou)
    if ecart is None:
        return
    attendu, place = ecart
    yield ctx.anomalie(
        CodeContrainte.VERROU,
        Gravite.ERREUR,
        f"La séance verrouillée {ctx.libelle(element)} doit avoir lieu {attendu} chaque semaine, "
        f"mais elle est planifiée {place}.",
        (element,),
        **ctx.ressources(element),
        **ctx.reperes(element),
    )
