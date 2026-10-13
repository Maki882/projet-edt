"""Règles personnalisées sur l'emploi du temps des personnes et les relations entre séances (temps B).

Vérificateurs déclarés (`@enregistrer("verificateur", TypeRegle.X)`) pour
`MAX_PAR_JOUR`, `JOURNEES_LIBRES`, `SANS_TROU`, `CAMPUS_PAR_JOUR`,
`MATIERES_PAR_JOUR` (sorte « emploi du temps ») et `ORDRE_SEMAINE`,
`REPARTITION_MATIERE` (sorte « relation »), selon le § 2.2 de
`docs/regles-personnalisees.md`.

Personnes : celles de la règle (`RegleResolue.personnes` : groupes feuilles
et/ou enseignants non fictifs), avec leur emploi du temps
(`Instantane.emplois`, retrouvé séance par séance par `Contexte.personnes_de`).
Pour une relation, seules les séances de la règle comptent
(`RegleResolue.seances`, une matière pour une portée matière).

Semaines : sur la semaine type, chaque séance est ramenée aux semaines de la
règle, puis les semaines sont regroupées par ensemble de séances actives
ensemble (deux séances aux semaines disjointes ne se gênent pas) ; sur les
séances datées, on raisonne par date (jour) ou par semaine réelle, du lundi au
dimanche, et au jour près pour la période de la règle.

Unité d'une anomalie (une par entorse, au sens du § 2.2) :

- par personne et par jour : `MAX_PAR_JOUR`, `CAMPUS_PAR_JOUR`,
  `MATIERES_PAR_JOUR` ;
- par personne et par semaine : `JOURNEES_LIBRES`, `SANS_TROU` (sur la
  semaine type, une anomalie par personne, qui détaille les semaines) ;
- par paire : `ORDRE_SEMAINE` ; par groupe, matière et semaine (même jour) ou
  par jour en trop (jours différents) : `REPARTITION_MATIERE`.

Des personnes dans la même situation (mêmes séances, mêmes semaines, même
constat) sont réunies en une anomalie ; le message nomme un ascendant dont
toutes les feuilles sont concernées. Gravité de base : erreur ; le niveau de
la règle la plafonne ensuite (`edt.verification.niveaux`).

Définitions propres au vérificateur, communes au solveur :

- **demi-journée** : le matin va jusqu'au début de la plage du déjeuner
  (`dejeuner_debut_min`), l'après-midi commence là ; une séance occupe chaque
  demi-journée qu'elle recoupe ;
- **jours travaillés** (`JOURNEES_LIBRES` sans `parmi`) : le standard de
  l'établissement (`Instantane.jours_travailles`) ;
- **créneau de trou** (`SANS_TROU`) : comme l'indicateur `trous_creneaux` :
  un créneau de la grille du jour entièrement compris entre le début de la
  première séance et la fin de la dernière, recoupé par aucune séance ;
- **ordre** (`ORDRE_SEMAINE`) : une séance « avant » doit commencer
  strictement avant chaque séance « après » de la même matière, la même
  semaine (jour puis heure de début).
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from edt.domaine.calendrier import lundi_de
from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import DemiJournee, Gravite
from edt.domaine.heures import MINUTES_PAR_JOUR, se_chevauchent
from edt.domaine.regles import (
    MEME_JOUR,
    UNITE_JOURNEE,
    UNITE_SEANCES,
    NaturePersonne,
    Personne,
    RegleResolue,
    TypeRegle,
    enregistrer,
)
from edt.verification.contexte import Contexte, Element, cle_chronologique
from edt.verification.contrat import Anomalie
from edt.verification.messages import date_texte, duree, enumerer, plage, semaines_texte
from edt.verification.regles_dejeuner import configurations
from edt.verification.regles_personnalisees import entete, minuscule_article, sujet_personnes

Semaines = frozenset[int] | None


@dataclass
class _Constat:
    """Une entorse d'une personne : séances en cause, semaines, texte (verbe et suite), repères."""

    seances: tuple[Element, ...]
    semaines: Semaines
    verbe: tuple[str, str]
    suite: str
    lister: bool = True
    reperes: dict[str, object] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Outils communs
# --------------------------------------------------------------------------- #


def _par_personne(
    ctx: Contexte, regle: RegleResolue, elements: Sequence[Element], *, relation: bool = False
) -> dict[Personne, list[Element]]:
    """Séances placées de chaque personne de la règle, ramenées à la période de la règle."""
    voulues = set(regle.personnes)
    besoins = ctx.besoins_vises(regle) if relation else None
    resultat: dict[Personne, list[Element]] = defaultdict(list)
    for element in elements:
        if besoins is not None and element.besoin.id not in besoins:
            continue
        restreint = ctx.restreindre(element, regle)
        if restreint is None:
            continue
        for personne in ctx.personnes_de(restreint):
            if personne in voulues:
                resultat[personne].append(restreint)
    return dict(sorted(resultat.items(), key=lambda item: (item[0].nature, item[0].id)))


def _jours(elements: list[Element]) -> list[list[Element]]:
    """Séances regroupées par jour (jour de la semaine type ou date)."""
    jours: dict[object, list[Element]] = defaultdict(list)
    for element in elements:
        jours[element.cle_jour].append(element)
    return [jours[cle] for cle in sorted(jours, key=str)]


def _semaines(elements: list[Element]) -> list[tuple[list[Element], Semaines, dt.date | None]]:
    """Ensembles de séances qui ont lieu ensemble : (séances, semaines, lundi).

    Semaine type : regroupement des semaines par ensemble de séances actives
    (`lundi` vaut `None`). Couche datée : une entrée par semaine réelle
    (`semaines` vaut `None`, `lundi` est le lundi de la semaine).
    """
    if elements and all(e.date is not None for e in elements):
        par_lundi: dict[dt.date, list[Element]] = defaultdict(list)
        for element in elements:
            if element.date is not None:
                par_lundi[lundi_de(element.date)].append(element)
        return [(par_lundi[lundi], None, lundi) for lundi in sorted(par_lundi)]
    return [(actives, semaines, None) for actives, semaines in configurations(elements)]


def _detail(valeurs: list[tuple[Semaines, str]]) -> str:
    """« 8 h les semaines 1 à 7 ; 9 h la semaine 8 » (semaine type) ou « 8 h » (couche datée)."""
    if all(semaines is None for semaines, _ in valeurs):
        return " ; ".join(dict.fromkeys(texte for _, texte in valeurs))
    par_texte: dict[str, set[int]] = defaultdict(set)
    for semaines, texte in valeurs:
        par_texte[texte] |= set(semaines or ())
    morceaux = sorted(par_texte.items(), key=lambda item: min(item[1]))
    return " ; ".join(
        f"{texte} {semaines_texte(numeros, article=True)}" for texte, numeros in morceaux
    )


def _union(semaines: list[Semaines]) -> Semaines:
    if all(s is None for s in semaines):
        return None
    return frozenset().union(*(s or () for s in semaines))


def _par_jour_et_semaines(
    ctx: Contexte,
    seances: list[Element],
    mesure: Callable[[list[Element]], Any],
    en_defaut: Callable[[Any], bool],
    texte: Callable[[Any], str],
    constat: Callable[[Element, str], tuple[tuple[str, str], str]],
) -> list[_Constat]:
    """Constats d'une personne pour une règle par jour : une mesure par jour et par semaines."""
    constats = []
    for du_jour in _jours(seances):
        en_cause: dict[int, Element] = {}
        valeurs: list[tuple[Semaines, str]] = []
        for actives, semaines, _ in _semaines(du_jour):
            valeur = mesure(actives)
            if en_defaut(valeur):
                en_cause.update((e.position, e) for e in actives)
                valeurs.append((semaines, texte(valeur)))
        if not valeurs:
            continue
        premiere = min(en_cause.values(), key=cle_chronologique)
        verbe, suite = constat(premiere, _detail(valeurs))
        constats.append(
            _Constat(
                tuple(sorted(en_cause.values(), key=cle_chronologique)),
                _union([s for s, _ in valeurs]),
                verbe,
                suite,
                reperes=ctx.reperes(premiere),
            )
        )
    return constats


def _emettre(
    ctx: Contexte, regle: RegleResolue, constats: list[tuple[Personne, _Constat]]
) -> Iterator[Anomalie]:
    """Une anomalie par situation : les personnes au même constat sont réunies."""
    reunis: dict[tuple, tuple[_Constat, list[Personne]]] = {}
    for personne, constat in constats:
        cle = (
            tuple(e.position for e in constat.seances),
            constat.semaines,
            constat.verbe,
            constat.suite,
        )
        reunis.setdefault(cle, (constat, []))[1].append(personne)
    for constat, personnes in reunis.values():
        groupes = [p.id for p in personnes if p.nature is NaturePersonne.GROUPE]
        enseignants = [p.id for p in personnes if p.nature is NaturePersonne.ENSEIGNANT]
        sujet, pluriel, en_cause = sujet_personnes(ctx, groupes, enseignants)
        texte = minuscule_article(f"{sujet} {constat.verbe[pluriel]} {constat.suite}")
        if constat.lister and constat.seances:
            liste = enumerer([f"{ctx.libelle(e)} {plage(e.debut, e.fin)}" for e in constat.seances])
            texte += f" : {liste}"
        yield ctx.anomalie(
            CodeContrainte.REGLE,
            Gravite.ERREUR,
            f"{entete(regle)} : {texte}.",
            constat.seances,
            groupes=en_cause,
            enseignants=enseignants,
            regle=regle,
            **constat.reperes,
        )


def _pluriel(nombre: int, singulier: str, pluriel: str) -> str:
    return f"{nombre} {singulier if nombre <= 1 else pluriel}"


# --------------------------------------------------------------------------- #
# Par personne et par jour
# --------------------------------------------------------------------------- #


@enregistrer("verificateur", TypeRegle.MAX_PAR_JOUR)
def verifier_max_par_jour(
    ctx: Contexte, regle: RegleResolue, elements: Sequence[Element]
) -> Iterator[Anomalie]:
    """Jours où une personne dépasse le maximum (minutes de cours ou nombre de séances)."""
    maximum = regle.valeurs["maximum"]
    seances_unite = regle.valeurs.get("unite") == UNITE_SEANCES

    def mesure(actives: list[Element]) -> int:
        return len(actives) if seances_unite else sum(e.duree for e in actives)

    def texte(valeur: int) -> str:
        return (
            _pluriel(valeur, "séance", "séances") if seances_unite else f"{duree(valeur)} de cours"
        )

    limite = _pluriel(maximum, "séance", "séances") if seances_unite else duree(maximum)

    def constat(premiere: Element, detail: str) -> tuple[tuple[str, str], str]:
        return ("a", "ont"), f"plus de {limite} {ctx.quand(premiere)} ({detail})"

    constats = [
        (personne, c)
        for personne, seances in _par_personne(ctx, regle, elements).items()
        for c in _par_jour_et_semaines(ctx, seances, mesure, lambda v: v > maximum, texte, constat)
    ]
    yield from _emettre(ctx, regle, constats)


@enregistrer("verificateur", TypeRegle.CAMPUS_PAR_JOUR)
def verifier_campus_par_jour(
    ctx: Contexte, regle: RegleResolue, elements: Sequence[Element]
) -> Iterator[Anomalie]:
    """Jours où une personne a cours sur plusieurs campus (séances avec salle)."""

    def mesure(actives: list[Element]) -> frozenset[int]:
        return frozenset(c for e in actives if (c := ctx.campus_de(e)) is not None)

    def texte(valeur: frozenset[int]) -> str:
        return enumerer(sorted(ctx.campus(c).nom for c in valeur))

    def constat(premiere: Element, detail: str) -> tuple[tuple[str, str], str]:
        return ("a", "ont"), f"cours sur plusieurs campus {ctx.quand(premiere)} ({detail})"

    constats = [
        (personne, c)
        for personne, seances in _par_personne(ctx, regle, elements).items()
        for c in _par_jour_et_semaines(ctx, seances, mesure, lambda v: len(v) > 1, texte, constat)
    ]
    yield from _emettre(ctx, regle, constats)


@enregistrer("verificateur", TypeRegle.MATIERES_PAR_JOUR)
def verifier_matieres_par_jour(
    ctx: Contexte, regle: RegleResolue, elements: Sequence[Element]
) -> Iterator[Anomalie]:
    """Jours où un groupe a plus de `maximum` matières différentes."""
    maximum = regle.valeurs["maximum"]

    def mesure(actives: list[Element]) -> int:
        return len({e.besoin.matiere_id for e in actives})

    def texte(valeur: int) -> str:
        return _pluriel(valeur, "matière", "matières")

    limite = _pluriel(maximum, "matière différente", "matières différentes")

    def constat(premiere: Element, detail: str) -> tuple[tuple[str, str], str]:
        return ("a", "ont"), f"plus de {limite} {ctx.quand(premiere)} ({detail})"

    constats = [
        (personne, c)
        for personne, seances in _par_personne(ctx, regle, elements).items()
        for c in _par_jour_et_semaines(ctx, seances, mesure, lambda v: v > maximum, texte, constat)
    ]
    yield from _emettre(ctx, regle, constats)


# --------------------------------------------------------------------------- #
# Par personne et par semaine
# --------------------------------------------------------------------------- #


def _par_semaine(
    ctx: Contexte,
    seances: list[Element],
    mesure: Callable[[list[Element]], tuple[int, list[Element]]],
    en_defaut: Callable[[int], bool],
    texte: Callable[[int], str],
    verbe: tuple[str, str],
    suite: Callable[[str], str],
) -> list[_Constat]:
    """Constats d'une personne pour une règle par semaine.

    Semaine type : un seul constat, qui détaille les semaines en défaut.
    Couche datée : un constat par semaine réelle en défaut (repère : son lundi).
    """
    constats = []
    en_cause: dict[int, Element] = {}
    valeurs: list[tuple[Semaines, str]] = []
    for actives, semaines, lundi in _semaines(seances):
        valeur, concernees = mesure(actives)
        if not en_defaut(valeur):
            continue
        if lundi is not None:
            constats.append(
                _Constat(
                    tuple(sorted(concernees, key=cle_chronologique)),
                    None,
                    verbe,
                    suite(f"{texte(valeur)}, semaine du {date_texte(lundi)}"),
                    lister=False,
                    reperes={"date": lundi},
                )
            )
            continue
        en_cause.update((e.position, e) for e in concernees)
        valeurs.append((semaines, texte(valeur)))
    if valeurs:
        constats.append(
            _Constat(
                tuple(sorted(en_cause.values(), key=cle_chronologique)),
                _union([s for s, _ in valeurs]),
                verbe,
                suite(_detail(valeurs)),
                lister=False,
            )
        )
    return constats


def _demi_journees(ctx: Contexte) -> dict[DemiJournee, tuple[int, int]]:
    """Plages des deux demi-journées : le matin s'arrête au début de la plage du déjeuner."""
    bascule = ctx.parametres.dejeuner_debut_min
    return {DemiJournee.MATIN: (0, bascule), DemiJournee.APRES_MIDI: (bascule, MINUTES_PAR_JOUR)}


@enregistrer("verificateur", TypeRegle.JOURNEES_LIBRES)
def verifier_journees_libres(
    ctx: Contexte, regle: RegleResolue, elements: Sequence[Element]
) -> Iterator[Anomalie]:
    """Semaines où une personne a moins de `nombre` demi-journées (ou journées) libres.

    Candidates : les moments de `parmi`, sinon les jours travaillés de
    l'établissement (en demi-journées : matin et après-midi de chacun) ; une
    candidate est libre si la personne n'y a aucune séance cette semaine-là.
    """
    nombre = regle.valeurs.get("nombre", 1)
    journees = regle.valeurs.get("unite") == UNITE_JOURNEE
    parmi = regle.valeurs.get("parmi") or ()
    demis = _demi_journees(ctx)
    if journees:
        jours = sorted({jour for jour, _ in parmi}) or sorted(ctx.instantane.jours_travailles)
        candidates: list[tuple[int, DemiJournee | None]] = [(jour, None) for jour in jours]
    elif parmi:
        candidates = sorted(
            {
                (jour, demi)
                for jour, demi_choisie in parmi
                for demi in ((demi_choisie,) if demi_choisie else tuple(demis))
            },
            key=lambda c: (c[0], str(c[1])),
        )
    else:
        candidates = [
            (jour, demi) for jour in sorted(ctx.instantane.jours_travailles) for demi in demis
        ]
    unite = ("journée", "journées") if journees else ("demi-journée", "demi-journées")

    def occupe(candidate: tuple[int, DemiJournee | None], element: Element) -> bool:
        jour, demi = candidate
        if element.jour != jour:
            return False
        return demi is None or se_chevauchent(element.debut, element.fin, *demis[demi])

    def mesure(actives: list[Element]) -> tuple[int, list[Element]]:
        libres, concernees = 0, []
        for candidate in candidates:
            occupantes = [e for e in actives if occupe(candidate, e)]
            if occupantes:
                concernees.extend(occupantes)
            else:
                libres += 1
        return libres, concernees

    exigees = _pluriel(nombre, *unite)
    constats = [
        (personne, c)
        for personne, seances in _par_personne(ctx, regle, elements).items()
        for c in _par_semaine(
            ctx,
            seances,
            mesure,
            lambda libres: libres < nombre,
            lambda libres: f"{libres} libre{'s' if libres > 1 else ''}",
            ("n'a pas", "n'ont pas"),
            lambda detail: f"{exigees} libre{'s' if nombre > 1 else ''} par semaine ({detail})",
        )
    ]
    yield from _emettre(ctx, regle, constats)


def creneaux_de_trou(ctx: Contexte, actives: list[Element]) -> tuple[int, list[Element]]:
    """Créneaux de trou d'une semaine (définition de l'indicateur `trous_creneaux`) et séances concernées."""
    trous, concernees = 0, []
    for du_jour in _jours(actives):
        plages = [(e.debut, e.fin) for e in du_jour]
        premier, dernier = min(p[0] for p in plages), max(p[1] for p in plages)
        libres = sum(
            1
            for creneau in ctx.instantane.creneaux
            if creneau.jour == du_jour[0].jour
            and premier <= creneau.debut
            and creneau.fin <= dernier
            and not any(se_chevauchent(creneau.debut, creneau.fin, d, f) for d, f in plages)
        )
        if libres:
            trous += libres
            concernees.extend(du_jour)
    return trous, concernees


@enregistrer("verificateur", TypeRegle.SANS_TROU)
def verifier_sans_trou(
    ctx: Contexte, regle: RegleResolue, elements: Sequence[Element]
) -> Iterator[Anomalie]:
    """Semaines où une personne a plus de `maximum` créneaux de trou."""
    maximum = regle.valeurs.get("maximum", 0)
    limite = (
        "des créneaux de trou"
        if maximum == 0
        else (f"plus de {_pluriel(maximum, 'créneau', 'créneaux')} de trou")
    )
    constats = [
        (personne, c)
        for personne, seances in _par_personne(ctx, regle, elements).items()
        for c in _par_semaine(
            ctx,
            seances,
            lambda actives: creneaux_de_trou(ctx, actives),
            lambda trous: trous > maximum,
            lambda trous: _pluriel(trous, "créneau", "créneaux"),
            ("a", "ont"),
            lambda detail: f"{limite} dans la semaine ({detail})",
        )
    ]
    yield from _emettre(ctx, regle, constats)


# --------------------------------------------------------------------------- #
# Relations entre séances
# --------------------------------------------------------------------------- #


def _ensemble(a: Element, b: Element) -> Semaines | bool:
    """Semaines où les deux séances ont lieu ensemble (semaine type), ou vrai si même semaine réelle."""
    if a.date is not None and b.date is not None:
        return lundi_de(a.date) == lundi_de(b.date)
    return (a.semaines or frozenset()) & (b.semaines or frozenset())


def _moment_absolu(element: Element) -> tuple:
    return (element.date or dt.date.min, element.jour, element.debut)


@enregistrer("verificateur", TypeRegle.ORDRE_SEMAINE)
def verifier_ordre_semaine(
    ctx: Contexte, regle: RegleResolue, elements: Sequence[Element]
) -> Iterator[Anomalie]:
    """Paires d'une même matière où la séance « après » commence avant (ou en même temps) que la séance « avant »."""
    avant, apres = regle.valeurs["avant"], regle.valeurs["apres"]
    constats = []
    for personne, seances in _par_personne(ctx, regle, elements, relation=True).items():
        par_matiere: dict[int, list[Element]] = defaultdict(list)
        for element in seances:
            par_matiere[element.besoin.matiere_id].append(element)
        for du_meme in par_matiere.values():
            premieres = [e for e in du_meme if e.besoin.type_seance_id == avant]
            secondes = [e for e in du_meme if e.besoin.type_seance_id == apres]
            for a in sorted(premieres, key=cle_chronologique):
                for b in sorted(secondes, key=cle_chronologique):
                    ensemble = _ensemble(a, b)
                    if not ensemble or _moment_absolu(a) < _moment_absolu(b):
                        continue
                    semaines = None if isinstance(ensemble, bool) else ensemble
                    suite = (
                        f"{ctx.libelle(b)} ({ctx.moment(b)}) avant {ctx.libelle(a)} "
                        f"({ctx.moment(a)}){ctx.precision_semaines(semaines)}"
                    )
                    constats.append(
                        (
                            personne,
                            _Constat(
                                (a, b),
                                semaines,
                                ("a", "ont"),
                                suite,
                                lister=False,
                                # Séances datées : repère à la date de la séance mal placée.
                                reperes={} if b.date is None else ctx.reperes(b),
                            ),
                        )
                    )
    yield from _emettre(ctx, regle, constats)


@enregistrer("verificateur", TypeRegle.REPARTITION_MATIERE)
def verifier_repartition_matiere(
    ctx: Contexte, regle: RegleResolue, elements: Sequence[Element]
) -> Iterator[Anomalie]:
    """Séances d'une matière d'un groupe sur plusieurs jours (même jour) ou plusieurs le même jour.

    Même jour : une anomalie par groupe, matière et ensemble de semaines où
    les séances tombent sur plus d'un jour. Jours différents : une anomalie
    par jour où plus d'une séance de la matière a lieu.
    """
    meme_jour = regle.valeurs["mode"] == MEME_JOUR
    constats = []
    for personne, seances in _par_personne(ctx, regle, elements, relation=True).items():
        par_matiere: dict[int, list[Element]] = defaultdict(list)
        for element in seances:
            par_matiere[element.besoin.matiere_id].append(element)
        for du_meme in par_matiere.values():
            matiere = du_meme[0].besoin.matiere_code
            for actives, semaines, _ in _semaines(du_meme):
                jours = _jours(actives)
                precision = ctx.precision_semaines(semaines)
                if meme_jour and len(jours) > 1:
                    noms = enumerer([ctx.quand(j[0]).removeprefix("le ") for j in jours])
                    constats.append(
                        (
                            personne,
                            _Constat(
                                tuple(sorted(actives, key=cle_chronologique)),
                                semaines,
                                ("a", "ont"),
                                f"les séances de la matière {matiere} sur {len(jours)} jours "
                                f"({noms}) au lieu d'un seul{precision}",
                            ),
                        )
                    )
                elif not meme_jour:
                    for du_jour in jours:
                        if len(du_jour) < 2:
                            continue
                        constats.append(
                            (
                                personne,
                                _Constat(
                                    tuple(sorted(du_jour, key=cle_chronologique)),
                                    semaines,
                                    ("a", "ont"),
                                    f"{len(du_jour)} séances de la matière {matiere} "
                                    f"{ctx.quand(du_jour[0])}{precision}",
                                    reperes=ctx.reperes(du_jour[0]),
                                ),
                            )
                        )
    yield from _emettre(ctx, regle, constats)
