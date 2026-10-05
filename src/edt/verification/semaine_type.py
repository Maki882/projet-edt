"""Vérification d'une semaine type : `verifier(instantane, placements, contraintes_actives)`.

Étapes, dans l'ordre :

1. validation de structure des placements (jour de 0 à 6, heures dans la
   journée, fin après le début, salle connue) : un placement mal formé rend
   la vérification impossible et lève `ErreurDonneesInvalides` avec la liste
   de tous les défauts ;
2. `VOLUME` : problèmes de dépliage de l'instantané (rapportés tels quels,
   avec leur propre code), placements d'une séance inconnue, séances sans
   placement, séances placées plusieurs fois ;
3. `VERROU` : une séance verrouillée est placée exactement à son verrou ;
4. règles d'une séance (`regles_seance`), règles entre deux séances
   (`regles_paires`), maxima des enseignants (`regles_enseignants`).

Une séance placée plusieurs fois est signalée (`VOLUME`) et **chacun** de
ses placements est vérifié comme les autres : rien n'est écarté en silence.
Un placement d'une séance inconnue n'a pas de besoin : il est signalé
(`VOLUME`) mais ne peut pas être vérifié autrement.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence

from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import Gravite
from edt.domaine.instantane import Instantane, Placement
from edt.erreurs import ErreurDonneesInvalides
from edt.verification.contexte import (
    Contexte,
    Element,
    cle_chronologique,
    cle_tri,
    creneau_impose,
    defauts_de_plage,
    resoudre_codes,
)
from edt.verification.contrat import Anomalie
from edt.verification.messages import enumerer, nom_jour, plage
from edt.verification.regles_enseignants import verifier_maxima
from edt.verification.regles_paires import verifier_paires
from edt.verification.regles_seance import ecart_au_verrou, verifier_seances

journal = logging.getLogger(__name__)


def verifier(
    instantane: Instantane,
    placements: Sequence[Placement],
    contraintes_actives: Iterable[str] | None = None,
) -> list[Anomalie]:
    """Liste les anomalies d'une semaine type (liste vide : emploi du temps valide).

    `placements` associe des séances de `instantane.seances` à un jour, une
    plage horaire et une salle. `contraintes_actives` restreint la
    vérification aux codes fournis (`None` : tous les codes actifs par
    défaut). Les jours fermés relèvent de la couche datée
    (`verifier_occurrences`), sauf les problèmes de dépliage de l'instantané.

    Lève `ErreurDonneesInvalides` si un code de contrainte est inconnu ou si
    un placement est mal formé ; `ErreurIntrouvable` si l'instantané
    lui-même est incohérent (enseignant ou groupe d'un besoin absent...).
    """
    chrono = time.perf_counter()
    codes = resoudre_codes(contraintes_actives)
    placements = tuple(placements)
    valider_placements(instantane, placements)
    ctx = Contexte(instantane, codes, datee=False)
    elements, inconnus = construire_elements(instantane, placements)

    anomalies: list[Anomalie] = []
    anomalies.extend(_problemes_depliage(ctx))
    anomalies.extend(_volume(ctx, elements, inconnus))
    anomalies.extend(_verrous(ctx, elements))
    anomalies.extend(verifier_seances(ctx, elements))
    anomalies.extend(verifier_paires(ctx, elements))
    anomalies.extend(verifier_maxima(ctx, elements))
    anomalies.sort(key=cle_tri)

    journal.info(
        "Vérification de la semaine type « %s » : %d placement(s), %d anomalie(s) dont %d "
        "bloquante(s), %d contrainte(s) vérifiée(s), %.0f ms.",
        instantane.libelle,
        len(placements),
        len(anomalies),
        sum(anomalie.bloquante for anomalie in anomalies),
        len(codes),
        (time.perf_counter() - chrono) * 1000,
    )
    return anomalies


def valider_placements(instantane: Instantane, placements: Sequence[Placement]) -> None:
    """Lève `ErreurDonneesInvalides` (avec tous les défauts) si un placement est mal formé."""
    salles = {salle.id for salle in instantane.salles}
    defauts: list[str] = []
    for numero, placement in enumerate(placements, start=1):
        if not isinstance(placement, Placement):
            defauts.append(
                f"élément n° {numero} : un placement est attendu (lu : {type(placement).__name__})"
            )
            continue
        nom = f"placement n° {numero} (séance « {placement.seance_id} »)"
        if not isinstance(placement.seance_id, str):
            defauts.append(f"{nom} : identifiant de séance absent")
        if placement.jour is None:
            defauts.append(f"{nom} : jour manquant")
        defauts.extend(
            f"{nom} : {defaut}"
            for defaut in defauts_de_plage(placement.jour, placement.debut, placement.fin)
        )
        if placement.salle_id is not None and placement.salle_id not in salles:
            defauts.append(f"{nom} : salle {placement.salle_id!r} inconnue dans ce semestre")
    if defauts:
        raise ErreurDonneesInvalides(
            f"{len(defauts)} défaut(s) dans les placements reçus : la vérification est "
            "impossible tant qu'ils ne sont pas corrigés.",
            details=defauts,
        )


def construire_elements(
    instantane: Instantane, placements: Sequence[Placement]
) -> tuple[list[Element], list[tuple[int, Placement]]]:
    """Éléments des placements de séances connues, et placements de séances inconnues (avec leur rang)."""
    seances = {seance.id: seance for seance in instantane.seances}
    elements: list[Element] = []
    inconnus: list[tuple[int, Placement]] = []
    for position, placement in enumerate(placements):
        seance = seances.get(placement.seance_id)
        if seance is None:
            inconnus.append((position, placement))
            continue
        elements.append(
            Element(
                besoin=seance.besoin,
                jour=placement.jour,
                debut=placement.debut,
                fin=placement.fin,
                salle_id=placement.salle_id,
                position=position,
                seance_id=seance.id,
                semaines=seance.semaines,
                imposee=creneau_impose(
                    seance.besoin, seance.verrou, placement.jour, placement.debut
                ),
                jour_impose=seance.jour_impose,
            )
        )
    return elements, inconnus


def _problemes_depliage(ctx: Contexte) -> Iterator[Anomalie]:
    besoins = {besoin.id: besoin for besoin in ctx.instantane.besoins}
    for probleme in ctx.instantane.problemes_depliage:
        if not ctx.actif(probleme.code):
            continue
        besoin = besoins.get(probleme.besoin_id)
        yield ctx.anomalie(
            probleme.code,
            Gravite.ERREUR,
            probleme.message,
            besoins=(probleme.besoin_id,),
            groupes=besoin.groupes if besoin is not None else (),
            enseignants=besoin.enseignants if besoin is not None else (),
        )


def _volume(
    ctx: Contexte, elements: list[Element], inconnus: list[tuple[int, Placement]]
) -> Iterator[Anomalie]:
    if not ctx.actif(CodeContrainte.VOLUME):
        if inconnus:
            journal.warning(
                "%d placement(s) désignent une séance inconnue et ne sont pas vérifiés "
                "(contrainte VOLUME non demandée) : %s",
                len(inconnus),
                ", ".join(sorted({placement.seance_id for _, placement in inconnus})),
            )
        return

    for _, placement in inconnus:
        yield _placement_inconnu(ctx, placement)

    par_seance: dict[str, list[Element]] = defaultdict(list)
    for element in elements:
        assert element.seance_id is not None  # semaine type
        par_seance[element.seance_id].append(element)

    for seance in ctx.instantane.seances:
        places = par_seance.get(seance.id, [])
        libelle = ctx.libelle_seance(seance.besoin, seance.id)
        ressources = {
            "enseignants": seance.besoin.enseignants,
            "groupes": seance.besoin.groupes,
        }
        if not places:
            yield ctx.anomalie(
                CodeContrainte.VOLUME,
                Gravite.ERREUR,
                f"La séance {libelle}{ctx.precision_semaines(seance.semaines)} n'est pas placée.",
                seances=(seance.id,),
                besoins=(seance.besoin.id,),
                **ressources,
            )
        elif len(places) > 1:
            lieux = [ctx.moment(element) for element in sorted(places, key=cle_chronologique)]
            yield ctx.anomalie(
                CodeContrainte.VOLUME,
                Gravite.ERREUR,
                f"La séance {libelle} est placée {len(places)} fois au lieu d'une : "
                f"{enumerer(lieux)}.",
                places,
                salles=[e.salle_id for e in places if e.salle_id is not None],
                **ressources,
            )


CONSEIL_RECALCUL = "Relancer le calcul pour remettre l'emploi du temps en accord avec les besoins."
CAUSES_SEANCE_RETIREE = (
    "la période du besoin ou celle du semestre a été raccourcie, ou le nombre de séances "
    "demandées a diminué"
)


def _placement_inconnu(ctx: Contexte, placement: Placement) -> Anomalie:
    """Placement d'une séance que les besoins du semestre ne demandent plus (ou jamais demandée).

    Cas typique : le semestre ou la période d'un besoin a été raccourci après le
    calcul, et la version garde des séances que le dépliage ne produit plus.
    Le message nomme le besoin quand il existe encore (l'identifiant technique
    reste dans le champ `seances`) et dit quoi faire.
    """
    ou = f"le {nom_jour(placement.jour)} {plage(placement.debut, placement.fin)}"
    if placement.salle_id is not None:
        ou += f" en salle {ctx.salle(placement.salle_id).code}"
    besoin_id, _, rang = placement.seance_id.partition("-")
    besoin = ctx.besoin(int(besoin_id)) if besoin_id.isdigit() else None
    salles = () if placement.salle_id is None else (placement.salle_id,)
    if besoin is None:
        reference = (
            f"n° {besoin_id}" if besoin_id.isdigit() else f"référence « {placement.seance_id} »"
        )
        return ctx.anomalie(
            CodeContrainte.VOLUME,
            Gravite.ERREUR,
            f"La séance placée {ou} se rattache à un besoin qui n'existe pas dans ce semestre "
            f"({reference}) : elle ne peut pas être vérifiée. {CONSEIL_RECALCUL}",
            seances=(placement.seance_id,),
            salles=salles,
            jour=placement.jour,
        )
    numero = f"n° {rang} " if rang.isdigit() else ""
    return ctx.anomalie(
        CodeContrainte.VOLUME,
        Gravite.ERREUR,
        f"La séance {numero}de « {ctx.libelle_seance(besoin, None)} », placée {ou}, n'est plus "
        f"demandée : {CAUSES_SEANCE_RETIREE}. {CONSEIL_RECALCUL}",
        seances=(placement.seance_id,),
        besoins=(besoin.id,),
        salles=salles,
        enseignants=besoin.enseignants,
        groupes=besoin.groupes,
        jour=placement.jour,
    )


def _verrous(ctx: Contexte, elements: list[Element]) -> Iterator[Anomalie]:
    if not ctx.actif(CodeContrainte.VERROU):
        return
    for element in elements:
        assert element.seance_id is not None  # semaine type
        verrou = ctx.instantane.seance(element.seance_id).verrou
        ecart = None if verrou is None else ecart_au_verrou(ctx, element, verrou)
        if ecart is None:
            continue
        attendu, place = ecart
        yield ctx.anomalie(
            CodeContrainte.VERROU,
            Gravite.ERREUR,
            f"La séance verrouillée {ctx.libelle(element)} doit rester {attendu}, mais elle "
            f"est placée {place}.",
            (element,),
            **ctx.ressources(element),
            **ctx.reperes(element),
        )
