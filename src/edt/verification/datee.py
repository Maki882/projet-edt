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
  (`Instantane.fermeture`) ou **hors du semestre** (avant `date_debut` ou
  après `date_fin` : semestre raccourci, import qui déborde) est une erreur,
  comme pour l'instanciation, qui n'y place jamais de séance ;
- `HORAIRES` (e) : un besoin ponctuel impose sa date, un besoin à jour imposé
  impose le jour de la semaine ;
- `VERROU` : une occurrence **planifiée** d'une séance verrouillée a lieu au
  jour de la semaine, aux heures et dans la salle du verrou (une occurrence
  déplacée a, par définition, quitté sa place : elle n'est pas concernée) ;
- `VOLUME` : une occurrence d'un besoin inconnu est une erreur (elle ne peut
  pas être vérifiée) ; une occurrence rattachée à une séance hebdomadaire
  absente du semestre est un avertissement (vérifiée sans son verrou
  éventuel) ; plusieurs occurrences d'une même séance qui se chevauchent à
  la même date sont un **doublon** : une erreur qui les cite toutes (elles
  ne sont pas en conflit « avec elles-mêmes »). Le compte des occurrences par
  séance n'est pas vérifié ici : la liste reçue peut n'être qu'une partie du
  semestre ;
- « salle à définir » (`VOLUME`, avertissement) : **un** avertissement par
  séance, qui cite toutes ses occurrences sans salle (une salle supprimée
  laisse sans salle toutes les dates d'une séance).

Une occurrence mal formée (heures hors de la journée, fin avant le début,
date qui n'est pas exactement un `datetime.date`, identifiant de salle qui
n'est pas un entier, identifiant en double, séance d'un autre besoin) rend la
vérification impossible : `ErreurDonneesInvalides` avec tous les défauts. Une
salle **inconnue** de l'instantané (supprimée depuis) ne l'est pas : la séance
est vérifiée comme une séance sans salle.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence

from edt.domaine.contraintes import CodeContrainte, Niveau, Niveaux
from edt.domaine.enumerations import Gravite, StatutOccurrence
from edt.domaine.instantane import Besoin, Instantane, Occurrence, SeanceAPlacer
from edt.erreurs import ErreurDonneesInvalides
from edt.verification.contexte import (
    Contexte,
    Element,
    cle_chronologique,
    cle_tri,
    creneau_impose,
    defaut_de_salle,
    defauts_de_plage,
    est_numero,
    salle_reelle,
    signaler_salles_disparues,
)
from edt.verification.contrat import Anomalie
from edt.verification.messages import date_texte, plage
from edt.verification.niveaux import appliquer_niveaux, choisir_contraintes, choisir_regles
from edt.verification.regles_dejeuner import verifier_dejeuner_libre
from edt.verification.regles_enseignants import verifier_maxima
from edt.verification.regles_paires import verifier_paires
from edt.verification.regles_personnalisees import verifier_regles
from edt.verification.regles_seance import ecart_au_verrou, verifier_seances
from edt.verification.semaine_type import CAUSES_SEANCE_RETIREE, CONSEIL_RECALCUL

journal = logging.getLogger(__name__)


def verifier_occurrences(
    instantane: Instantane,
    occurrences: Sequence[Occurrence],
    contraintes_actives: Iterable[str] | None = None,
    *,
    niveaux: Niveaux | None = None,
    niveaux_regles: Mapping[int, Niveau] | None = None,
) -> list[Anomalie]:
    """Liste les anomalies d'un ensemble de séances datées (liste vide : valide).

    Voir l'en-tête du module pour les différences avec la semaine type.

    `niveaux` (facultatif, voir `edt.verification.niveaux`) : seules ses
    contraintes sont vérifiées, la gravité de chaque anomalie suit le niveau
    de sa contrainte sans dépasser celle de la règle, et `contraintes_actives`
    est ignoré. Sans `niveaux`, tout est vérifié en dur.

    `niveaux_regles` (facultatif) : identifiant de règle personnalisée →
    niveau retenu ; une règle absente est ignorée, comme si elle n'existait
    pas (défaut : toutes les règles actives de l'instantané, à leur niveau).
    Les règles ne sont vérifiées que si `REGLE` l'est ; leurs anomalies
    suivent toujours le niveau de leur règle.
    Lève `ErreurDonneesInvalides` si un code de contrainte est inconnu, si
    une occurrence est mal formée ou si l'arbre des groupes boucle
    (`valider_groupes`).
    """
    chrono = time.perf_counter()
    niveaux_appliques, codes = choisir_contraintes(contraintes_actives, niveaux)
    instantane, regles_retenues = choisir_regles(instantane, codes, niveaux_regles)
    occurrences = tuple(occurrences)
    valider_occurrences(occurrences)
    ctx = Contexte(instantane, codes, datee=True, niveaux_regles=regles_retenues)
    besoins = {besoin.id: besoin for besoin in instantane.besoins}
    seances = {seance.id: seance for seance in instantane.seances}
    salles = frozenset(salle.id for salle in instantane.salles)

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
        element = _element(occurrence, besoin, seance, position, salles)
        if occurrence.statut is StatutOccurrence.A_PROGRAMMER:
            anomalies.extend(_a_programmer(ctx, element))
            continue
        if not occurrence.statut.occupe_les_ressources:
            continue
        elements.append(element)
        anomalies.extend(_jour_ferme(ctx, element))
        if occurrence.statut is StatutOccurrence.PLANIFIEE and seance is not None:
            anomalies.extend(_verrou(ctx, element, seance))

    signaler_salles_disparues(elements)
    anomalies.extend(_doublons(ctx, elements))
    anomalies.extend(verifier_seances(ctx, elements))
    anomalies.extend(verifier_dejeuner_libre(ctx, elements))
    anomalies.extend(verifier_paires(ctx, elements))
    anomalies.extend(verifier_maxima(ctx, elements))
    anomalies.extend(verifier_regles(ctx, elements))
    anomalies = appliquer_niveaux(anomalies, niveaux_appliques, regles_retenues)
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


def valider_occurrences(occurrences: Sequence[Occurrence]) -> None:
    """Lève `ErreurDonneesInvalides` (avec tous les défauts) si une occurrence est mal formée.

    La date doit être exactement un `datetime.date` : un `datetime` (date et
    heure) ne se compare pas aux dates (tri des jours) et ne leur est jamais
    égal (conflits manqués). Une salle inconnue de l'instantané n'est pas un
    défaut de structure (voir l'en-tête).
    """
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
        if type(occurrence.date) is not dt.date:
            defauts.append(f"{nom} : date absente ou invalide ({occurrence.date!r})")
        if not isinstance(occurrence.statut, StatutOccurrence):
            defauts.append(f"{nom} : statut invalide ({occurrence.statut!r})")
        defauts.extend(
            f"{nom} : {defaut}"
            for defaut in defauts_de_plage(None, occurrence.debut, occurrence.fin)
        )
        defaut = defaut_de_salle(occurrence.salle_id)
        if defaut is not None:
            defauts.append(f"{nom} : {defaut}")
        if occurrence.seance_id is not None:
            besoin, _, rang = str(occurrence.seance_id).partition("-")
            lisible = est_numero(besoin) and est_numero(rang)
            if not lisible or int(besoin) != occurrence.besoin_id:
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
    occurrence: Occurrence,
    besoin: Besoin,
    seance: SeanceAPlacer | None,
    position: int,
    salles: frozenset[int],
) -> Element:
    # Le verrou fixe la place de la semaine type : une occurrence déplacée à la
    # main (autre jour ou autre heure) ne bénéficie plus des écarts admis.
    verrou = None if seance is None else seance.verrou
    imposee = creneau_impose(besoin, verrou, occurrence.date.weekday(), occurrence.debut)
    salle_id, salle_inconnue = salle_reelle(occurrence.salle_id, salles)
    return Element(
        besoin=besoin,
        jour=occurrence.date.weekday(),
        debut=occurrence.debut,
        fin=occurrence.fin,
        salle_id=salle_id,
        position=position,
        seance_id=None if seance is None else seance.id,
        occurrence_id=occurrence.id,
        date=occurrence.date,
        imposee=imposee,
        jour_impose=besoin.jour_impose if besoin.date_ponctuelle is None else None,
        date_imposee=besoin.date_ponctuelle,
        salle_inconnue=salle_inconnue,
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
        salles=(occurrence.salle_id,) if ctx.salle_connue(occurrence.salle_id) else (),
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
        f"La séance {_numero(occurrence.seance_id)}de « {ctx.libelle_seance(besoin, None)} » "
        f"du {date_texte(occurrence.date)} vient d'une séance de la semaine type que le besoin ne "
        f"demande plus ({CAUSES_SEANCE_RETIREE}) : elle est vérifiée sans son éventuel verrou. "
        f"{CONSEIL_RECALCUL}",
        seances=() if occurrence.seance_id is None else (occurrence.seance_id,),
        occurrences=() if occurrence.id is None else (occurrence.id,),
        besoins=(besoin.id,),
        groupes=besoin.groupes,
        date=occurrence.date,
    )


def _numero(seance_id: str | None) -> str:
    """« n° 2 » pour la séance « 107-2 », rien si le rang est illisible."""
    rang = (seance_id or "").partition("-")[2]
    return f"n° {rang} " if est_numero(rang) else ""


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
    instantane = ctx.instantane
    if not instantane.date_debut <= element.date <= instantane.date_fin:
        # Hors du semestre, le calendrier est fermé (l'instanciation n'y place rien) ; les
        # règles personnalisées, bornées aux semaines du semestre, ne la verraient pas.
        yield ctx.anomalie(
            CodeContrainte.JOUR_FERME,
            Gravite.ERREUR,
            f"La séance {ctx.libelle(element)} a lieu {ctx.moment(element)}, en dehors du "
            f"semestre « {instantane.libelle} » (du {date_texte(instantane.date_debut)} au "
            f"{date_texte(instantane.date_fin)}) : la replacer dans le semestre ou la supprimer.",
            (element,),
            **ctx.ressources(element),
            **ctx.reperes(element),
        )
        return
    fermeture = instantane.fermeture(element.date, element.besoin)
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


def _doublons(ctx: Contexte, elements: Sequence[Element]) -> Iterator[Anomalie]:
    """Occurrences d'une même séance qui se chevauchent à la même date : un doublon (`VOLUME`).

    Cas typique : une instanciation reprise ou un import qui ajoute une seconde
    fois les séances datées. Une erreur par groupe de copies, qui les cite
    toutes ; les copies ne sont pas en conflit « avec elles-mêmes »
    (`regles_paires`). Deux occurrences d'une même séance à des heures
    disjointes (rattrapage) ne sont pas un doublon.
    """
    if not ctx.actif(CodeContrainte.VOLUME):
        return
    par_seance: dict[tuple[str, object], list[Element]] = defaultdict(list)
    for element in elements:
        if element.seance_id is not None:
            par_seance[(element.seance_id, element.date)].append(element)
    for copies in par_seance.values():
        if len(copies) < 2:
            continue
        copies.sort(key=cle_chronologique)
        grappes: list[list[Element]] = []
        fin = 0
        for element in copies:
            if grappes and element.debut < fin:
                grappes[-1].append(element)
                fin = max(fin, element.fin)
            else:
                grappes.append([element])
                fin = element.fin
        for grappe in grappes:
            if len(grappe) < 2:
                continue
            premiere = grappe[0]
            yield ctx.anomalie(
                CodeContrainte.VOLUME,
                Gravite.ERREUR,
                f"La séance {ctx.libelle(premiere)} a lieu {len(grappe)} fois en même temps "
                f"{ctx.quand(premiere)} ({plage(premiere.debut, max(e.fin for e in grappe))}) : "
                "garder une seule de ces séances datées.",
                grappe,
                **ctx.ressources(premiere),
                **ctx.reperes(premiere),
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
