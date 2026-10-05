"""Règles entre deux séances : SALLE_UNIQUE, ENSEIGNANT_UNIQUE, GROUPE_UNIQUE, PAUSE, CAMPUS_TRAJET.

Définitions (identiques à celles du solveur)
--------------------------------------------
- deux séances **coexistent** si elles ont lieu le même jour : même jour de
  la semaine et semaines actives qui se croisent (semaine type), ou même
  date (couche datée) ;
- elles **se chevauchent** si, en plus, leurs plages `[debut, fin[` se
  recoupent (deux séances contiguës ne se chevauchent pas) ;
- elles **partagent une personne** si elles ont un enseignant non fictif en
  commun ou si leurs besoins sont en conflit de groupes
  (`Instantane.besoins_en_conflit_de_groupes`).

Règles
------
- `SALLE_UNIQUE` : chevauchement et même salle (non nulle) ;
- `ENSEIGNANT_UNIQUE` : chevauchement et enseignant non fictif commun ;
- `GROUPE_UNIQUE` : chevauchement et besoins en conflit de groupes ;
- `PAUSE` : coexistence, personne partagée, pas de chevauchement et écart
  entre la fin de la première et le début de la seconde inférieur à
  `pause_min` (les salles ne comptent pas) ;
- `CAMPUS_TRAJET` : idem, avec deux salles sur deux campus différents et un
  écart inférieur au temps de trajet entre ces campus.

Une paire en violation produit **une** anomalie par code, qui cite les deux
séances dans l'ordre chronologique.

Algorithme
----------
Les séances sont regroupées par jour. Dans un jour, chaque séance n'est
comparée qu'aux séances qui partagent avec elle une salle, un enseignant non
fictif ou un groupe lié (index par ressource), et chaque paire n'est
examinée qu'une fois : le coût suit le nombre de séances qui partagent une
ressource, pas le carré du nombre de séances.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Sequence

from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import Gravite
from edt.domaine.heures import ecart, se_chevauchent
from edt.verification.contexte import Contexte, Element
from edt.verification.contrat import Anomalie
from edt.verification.messages import duree, enumerer, heure, plage

CODES_PAIRES = frozenset(
    {
        CodeContrainte.SALLE_UNIQUE,
        CodeContrainte.ENSEIGNANT_UNIQUE,
        CodeContrainte.GROUPE_UNIQUE,
        CodeContrainte.PAUSE,
        CodeContrainte.CAMPUS_TRAJET,
    }
)
_CODES_PERSONNES = CODES_PAIRES - {CodeContrainte.SALLE_UNIQUE}


def verifier_paires(ctx: Contexte, elements: Sequence[Element]) -> Iterator[Anomalie]:
    """Anomalies des règles entre deux séances (voir l'en-tête du module)."""
    codes = CODES_PAIRES & ctx.codes
    if not codes:
        return
    par_jour: dict[object, list[Element]] = defaultdict(list)
    for element in elements:
        par_jour[element.cle_jour].append(element)
    for cle in sorted(par_jour):  # type: ignore[type-var]
        yield from _verifier_jour(ctx, par_jour[cle], codes)


def _verifier_jour(
    ctx: Contexte, elements: list[Element], codes: frozenset[CodeContrainte]
) -> Iterator[Anomalie]:
    avec_salles = CodeContrainte.SALLE_UNIQUE in codes
    avec_personnes = not codes.isdisjoint(_CODES_PERSONNES)
    par_salle: dict[int, list[int]] = defaultdict(list)
    par_enseignant: dict[int, list[int]] = defaultdict(list)
    par_groupe: dict[int, list[int]] = defaultdict(list)
    for indice, element in enumerate(elements):
        if avec_salles and element.salle_id is not None:
            par_salle[element.salle_id].append(indice)
        if avec_personnes:
            for enseignant in ctx.enseignants_reels(element.besoin):
                par_enseignant[enseignant].append(indice)
            for groupe in element.besoin.groupes:
                par_groupe[groupe].append(indice)

    for indice, element in enumerate(elements):
        voisins: set[int] = set()
        if avec_salles and element.salle_id is not None:
            voisins.update(par_salle[element.salle_id])
        if avec_personnes:
            for enseignant in ctx.enseignants_reels(element.besoin):
                voisins.update(par_enseignant[enseignant])
            for groupe in ctx.groupes_lies(element.besoin):
                voisins.update(par_groupe.get(groupe, ()))
        for autre in sorted(voisins):
            if autre > indice:
                yield from _examiner(ctx, element, elements[autre], codes)


def _examiner(
    ctx: Contexte, a: Element, b: Element, codes: frozenset[CodeContrainte]
) -> Iterator[Anomalie]:
    if not ctx.coexistent(a, b):
        return
    if (b.debut, b.fin, b.position) < (a.debut, a.fin, a.position):
        a, b = b, a
    if se_chevauchent(a.debut, a.fin, b.debut, b.fin):
        yield from _chevauchement(ctx, a, b, codes)
    else:
        yield from _enchainement(ctx, a, b, codes)


def _situation(ctx: Contexte, a: Element, b: Element) -> str:
    """« le mardi de 10 h 45 à 12 h 45 : X et Y » (ou chaque plage si elles diffèrent)."""
    precision = ctx.precision_semaines(ctx.semaines_communes(a, b))
    if (a.debut, a.fin) == (b.debut, b.fin):
        return (
            f"{ctx.quand(a)} {plage(a.debut, a.fin)}{precision} : "
            f"{ctx.libelle(a)} et {ctx.libelle(b)}"
        )
    return (
        f"{ctx.quand(a)}{precision} : {ctx.libelle(a)} {plage(a.debut, a.fin)} "
        f"et {ctx.libelle(b)} {plage(b.debut, b.fin)}"
    )


def _chevauchement(
    ctx: Contexte, a: Element, b: Element, codes: frozenset[CodeContrainte]
) -> Iterator[Anomalie]:
    reperes = ctx.reperes(a)
    if CodeContrainte.SALLE_UNIQUE in codes and a.salle_id is not None and a.salle_id == b.salle_id:
        salle = ctx.salle(a.salle_id)
        yield ctx.anomalie(
            CodeContrainte.SALLE_UNIQUE,
            Gravite.ERREUR,
            f"La salle {salle.code} accueille deux séances en même temps {_situation(ctx, a, b)}.",
            (a, b),
            salles=(salle.id,),
            **reperes,
        )
    enseignants = ctx.enseignants_reels(a.besoin) & ctx.enseignants_reels(b.besoin)
    if CodeContrainte.ENSEIGNANT_UNIQUE in codes and enseignants:
        noms = sorted(ctx.nom_enseignant(enseignant) for enseignant in enseignants)
        verbe = "donne" if len(noms) == 1 else "donnent"
        yield ctx.anomalie(
            CodeContrainte.ENSEIGNANT_UNIQUE,
            Gravite.ERREUR,
            f"{enumerer(noms)} {verbe} deux séances en même temps {_situation(ctx, a, b)}.",
            (a, b),
            enseignants=enseignants,
            **reperes,
        )
    if CodeContrainte.GROUPE_UNIQUE in codes and ctx.instantane.besoins_en_conflit_de_groupes(
        a.besoin, b.besoin
    ):
        groupes = ctx.groupes_en_conflit(a.besoin, b.besoin)
        if len(groupes) == 1:
            sujet = f"Le groupe {ctx.code_groupe(groupes[0])} a"
        else:
            codes_groupes = sorted(ctx.code_groupe(groupe) for groupe in groupes)
            sujet = f"Les groupes {enumerer(codes_groupes)}, qui ont des étudiants en commun, ont"
        yield ctx.anomalie(
            CodeContrainte.GROUPE_UNIQUE,
            Gravite.ERREUR,
            f"{sujet} deux séances en même temps {_situation(ctx, a, b)}.",
            (a, b),
            groupes=groupes,
            **reperes,
        )


def _enchainement(
    ctx: Contexte, a: Element, b: Element, codes: frozenset[CodeContrainte]
) -> Iterator[Anomalie]:
    """Paire sans chevauchement : `a` finit avant que `b` commence."""
    enseignants = ctx.enseignants_reels(a.besoin) & ctx.enseignants_reels(b.besoin)
    conflit = ctx.instantane.besoins_en_conflit_de_groupes(a.besoin, b.besoin)
    if not enseignants and not conflit:
        return
    groupes = ctx.groupes_en_conflit(a.besoin, b.besoin) if conflit else ()
    libre = ecart(a.debut, a.fin, b.debut, b.fin)
    sujet, pluriel = ctx.personnes(enseignants, groupes)
    quand = f"{ctx.quand(a)}{ctx.precision_semaines(ctx.semaines_communes(a, b))}"
    reperes = ctx.reperes(a)

    pause = ctx.parametres.pause_min
    if CodeContrainte.PAUSE in codes and libre < pause:
        negation = "n'ont" if pluriel else "n'a"
        if libre == 0:
            constat = f"{sujet} {negation} aucune pause"
        else:
            constat = f"{sujet} {negation} que {duree(libre)} de pause"
        yield ctx.anomalie(
            CodeContrainte.PAUSE,
            Gravite.ERREUR,
            f"{constat} {quand} entre {ctx.libelle(a)}, qui finit à {heure(a.fin)}, et "
            f"{ctx.libelle(b)}, qui commence à {heure(b.debut)} ; il faut au moins "
            f"{duree(pause)}.",
            (a, b),
            enseignants=enseignants,
            groupes=groupes,
            **reperes,
        )

    if CodeContrainte.CAMPUS_TRAJET not in codes or a.salle_id is None or b.salle_id is None:
        return
    salle_a, salle_b = ctx.salle(a.salle_id), ctx.salle(b.salle_id)
    if salle_a.campus_id == salle_b.campus_id:
        return
    trajet = ctx.instantane.trajet(salle_a.campus_id, salle_b.campus_id)
    if libre >= trajet:
        return
    campus_a, campus_b = ctx.campus(salle_a.campus_id), ctx.campus(salle_b.campus_id)
    delai = "sans aucun battement" if libre == 0 else f"en {duree(libre)} seulement"
    yield ctx.anomalie(
        CodeContrainte.CAMPUS_TRAJET,
        Gravite.ERREUR,
        f"{sujet} {'doivent' if pluriel else 'doit'} changer de campus {quand} {delai} : "
        f"{ctx.libelle(a)} finit à {heure(a.fin)} en salle {salle_a.code} ({campus_a.nom}) et "
        f"{ctx.libelle(b)} commence à {heure(b.debut)} en salle {salle_b.code} "
        f"({campus_b.nom}) ; le trajet demande {duree(trajet)}.",
        (a, b),
        salles=(salle_a.id, salle_b.id),
        enseignants=enseignants,
        groupes=groupes,
        **reperes,
    )
