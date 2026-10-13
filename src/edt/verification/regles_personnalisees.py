"""Règles personnalisées : un vérificateur par type (`docs/regles-personnalisees.md`, § 2.2 et § 4.4).

Chaque règle active de l'instantané (`Instantane.regles`, déjà résolue :
valeurs converties, semaines couvertes, séances ou personnes concernées,
phrase) est vérifiée par le vérificateur de son type, déclaré dans le
registre commun du domaine (`@enregistrer("verificateur", TypeRegle.X)` ;
`VERIFICATEURS` en est la vue). Ses anomalies ont le code `REGLE`, portent la
règle (`regle_id`, `regle_code`, `regle_phrase`) et prennent ensuite le
niveau de la règle (`edt.verification.niveaux`).

Unité d'une anomalie, selon la sorte du type :

- **séance** (`JOURS_TRAVAILLES`, `PLAGE_HORAIRE`, `CRENEAUX_INTERDITS`,
  `CAMPUS`, `SALLES`) : une par séance placée en entorse ;
- **emploi du temps** (`PAUSE_DEJEUNER` : une par personne et par jour, les
  personnes dans la même situation réunies ; `PAUSE_MINIMALE` : une par paire
  de séances trop rapprochées) ;
- **relation** : à venir (temps B).

Message : « Règle R007 (phrase de la règle) : l'écart. » Gravité de base :
erreur, ou information pour une séance imposée (début imposé, ponctuelle,
verrou resté à sa place) qui s'écarte d'une règle de jours, d'horaires ou de
déjeuner, comme `JOURS`, `HORAIRES` et `DEJEUNER` ; le niveau de la règle
plafonne ensuite cette gravité.

Période : sur la semaine type, une règle vaut pour les semaines actives de la
séance qu'elle couvre (`RegleResolue.semaines`) ; sur les séances datées, pour
une date dont la semaine est couverte (la résolution ne garde que les
semaines : une règle datée au jour près s'applique à toute la semaine).

Une règle dont le type n'a pas encore de vérificateur donne un avertissement
explicite (jamais de silence).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence

from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import Gravite
from edt.domaine.heures import ecart, se_chevauchent
from edt.domaine.regles import (
    MODE_IMPOSER,
    REGISTRES,
    NaturePersonne,
    NaturePortee,
    Personne,
    RegleResolue,
    TypeRegle,
    enregistrer,
)
from edt.verification.contexte import Contexte, Element, cle_chronologique
from edt.verification.contrat import Anomalie
from edt.verification.messages import duree, enumerer, heure, plage
from edt.verification.regles_dejeuner import (
    MENTION_IMPOSEES,
    Defaut,
    defaut_fixe,
    defaut_libre,
    jours_en_defaut,
    liste_seances,
    par_personne,
    situations,
)

journal = logging.getLogger(__name__)

VERIFICATEURS: dict[TypeRegle, Callable[..., Iterator[Anomalie]]] = REGISTRES["verificateur"]
"""Vérificateur de chaque type : `(ctx, regle, elements) -> Iterator[Anomalie]`."""

_MENTION_IMPOSEE = " Son créneau est imposé : l'écart est signalé pour information."


def verifier_regles(ctx: Contexte, elements: Sequence[Element]) -> Iterator[Anomalie]:
    """Anomalies `REGLE` de toutes les règles retenues de l'instantané."""
    if not ctx.actif(CodeContrainte.REGLE):
        return
    for regle in sorted(ctx.instantane.regles, key=lambda r: r.id):
        verificateur = VERIFICATEURS.get(regle.type)
        if verificateur is None:
            journal.warning(
                "Règle %s (%s) : aucun vérificateur pour ce type, signalée comme non vérifiée",
                regle.code,
                regle.type,
            )
            yield ctx.anomalie(
                CodeContrainte.REGLE,
                Gravite.AVERTISSEMENT,
                f"{entete(regle)} : ce type de règle (« {regle.type.libelle} ») n'est pas "
                "encore vérifié automatiquement ; la contrôler à la main.",
                regle=regle,
            )
            continue
        yield from verificateur(ctx, regle, elements)


def entete(regle: RegleResolue) -> str:
    """« Règle R007 (La classe A2-G3 a une pause déjeuner…) », sans point final dans la phrase."""
    return f"Règle {regle.code} ({regle.phrase.rstrip('.')})"


# --------------------------------------------------------------------------- #
# Sorte « séance »
# --------------------------------------------------------------------------- #


def _visees(
    ctx: Contexte, regle: RegleResolue, elements: Sequence[Element]
) -> Iterator[tuple[Element, frozenset[int] | None]]:
    """Séances placées que la règle concerne, avec les semaines où elle vaut pour elles.

    Une séance est concernée si son besoin l'est (toutes les séances d'un
    besoin le sont ensemble, ce qui couvre aussi une séance datée sans séance
    hebdomadaire) et si la période de la règle la couvre ; pour une portée
    campus, si elle est placée sur ce campus.
    """
    besoins = ctx.besoins_vises(regle)
    for element in elements:
        if element.besoin.id not in besoins:
            continue
        semaines = ctx.semaines_regle(regle, element)
        if semaines is not None and not semaines:
            continue
        if regle.portee is NaturePortee.CAMPUS and ctx.campus_de(element) != regle.cible_id:
            continue
        yield element, semaines


def _seance(
    ctx: Contexte,
    regle: RegleResolue,
    element: Element,
    semaines: frozenset[int] | None,
    ecart_texte: str,
    *,
    plafonnee: bool,
    **champs: object,
) -> Anomalie:
    """Anomalie d'une séance : « Règle … : la séance X le mardi de … à … (semaines …) <écart>. »"""
    imposee = plafonnee and element.imposee
    message = (
        f"{entete(regle)} : la séance {ctx.libelle(element)} {ctx.moment(element)}"
        f"{ctx.precision_semaines(semaines)} {ecart_texte}." + (_MENTION_IMPOSEE if imposee else "")
    )
    ressources = ctx.ressources(element)
    ressources.update(champs)  # type: ignore[arg-type]
    return ctx.anomalie(
        CodeContrainte.REGLE,
        Gravite.INFORMATION if imposee else Gravite.ERREUR,
        message,
        (element,),
        regle=regle,
        **ressources,
        **ctx.reperes(element),
    )


@enregistrer("verificateur", TypeRegle.JOURS_TRAVAILLES)
def verifier_jours_travailles(
    ctx: Contexte, regle: RegleResolue, elements: Sequence[Element]
) -> Iterator[Anomalie]:
    """Séances placées un jour que la règle exclut, quand elle est **retenue** pour la séance.

    La précédence (`Instantane.reglages`) dit quelles règles retient chaque
    séance (la plus précise de chaque axe) ; une règle moins précise, masquée
    par une autre de la même lignée, n'est pas enfreinte.
    """
    for element in elements:
        if not any(
            source.regle is not None and source.regle.id == regle.id
            for source in ctx.entorses_jours(element)
        ):
            continue
        semaines = ctx.semaines_regle(regle, element)
        if semaines is not None and not semaines:
            continue
        lieu = ""
        if regle.portee is NaturePortee.CAMPUS and regle.cible_id is not None:
            lieu = f", sur le campus {ctx.campus(regle.cible_id).nom}"
        yield _seance(
            ctx,
            regle,
            element,
            semaines,
            f"a lieu un jour que la règle exclut{lieu}",
            plafonnee=True,
        )


@enregistrer("verificateur", TypeRegle.PLAGE_HORAIRE)
def verifier_plage_horaire(
    ctx: Contexte, regle: RegleResolue, elements: Sequence[Element]
) -> Iterator[Anomalie]:
    """Séances qui ne tiennent pas dans `[debut, fin]` les jours visés (tous si la liste est vide)."""
    debut, fin, jours = regle.valeurs["debut"], regle.valeurs["fin"], regle.jours
    for element, semaines in _visees(ctx, regle, elements):
        if jours and element.jour not in jours:
            continue
        ecarts = []
        if element.debut < debut:
            ecarts.append(f"commence avant {heure(debut)}")
        if element.fin > fin:
            ecarts.append(f"finit après {heure(fin)}")
        if ecarts:
            yield _seance(ctx, regle, element, semaines, enumerer(ecarts), plafonnee=True)


@enregistrer("verificateur", TypeRegle.CRENEAUX_INTERDITS)
def verifier_creneaux_interdits(
    ctx: Contexte, regle: RegleResolue, elements: Sequence[Element]
) -> Iterator[Anomalie]:
    """Séances qui recoupent la plage interdite, les jours visés."""
    debut, fin, jours = regle.valeurs["debut"], regle.valeurs["fin"], regle.jours
    for element, semaines in _visees(ctx, regle, elements):
        if element.jour in jours and se_chevauchent(element.debut, element.fin, debut, fin):
            yield _seance(
                ctx,
                regle,
                element,
                semaines,
                f"recoupe la plage interdite {plage(debut, fin)}",
                plafonnee=True,
            )


@enregistrer("verificateur", TypeRegle.CAMPUS)
def verifier_campus(
    ctx: Contexte, regle: RegleResolue, elements: Sequence[Element]
) -> Iterator[Anomalie]:
    """Séances sur un campus non imposé (mode imposer) ou interdit (mode interdire).

    Une séance sans salle (salle à définir) n'a pas encore de campus : elle
    n'est pas vérifiée.
    """
    listes = set(regle.valeurs["campus"])
    imposer = regle.valeurs["mode"] == MODE_IMPOSER
    noms = enumerer(sorted(ctx.campus(c).nom for c in listes), "ou" if imposer else "et")
    for element, semaines in _visees(ctx, regle, elements):
        campus = ctx.campus_de(element)
        if campus is None or (campus in listes) == imposer:
            continue
        salle = ctx.salle(element.salle_id)  # type: ignore[arg-type]
        attendu = f"campus imposé : {noms}" if imposer else f"campus interdit : {noms}"
        yield _seance(
            ctx,
            regle,
            element,
            semaines,
            f"a lieu en salle {salle.code}, sur le campus {ctx.campus(campus).nom} ({attendu})",
            plafonnee=False,
        )


@enregistrer("verificateur", TypeRegle.SALLES)
def verifier_salles(
    ctx: Contexte, regle: RegleResolue, elements: Sequence[Element]
) -> Iterator[Anomalie]:
    """Séances dans une salle non imposée (mode imposer) ou interdite (mode interdire).

    Une séance sans salle (salle à définir) n'est pas vérifiée.
    """
    listes = set(regle.valeurs["salles"])
    imposer = regle.valeurs["mode"] == MODE_IMPOSER
    codes = enumerer(sorted(ctx.salle(s).code for s in listes), "ou" if imposer else "et")
    for element, semaines in _visees(ctx, regle, elements):
        if element.salle_id is None or (element.salle_id in listes) == imposer:
            continue
        salle = ctx.salle(element.salle_id)
        raison = f"hors des salles imposées ({codes})" if imposer else "salle interdite"
        yield _seance(
            ctx,
            regle,
            element,
            semaines,
            f"a lieu en salle {salle.code}, {raison}",
            plafonnee=False,
        )


# --------------------------------------------------------------------------- #
# Sorte « emploi du temps »
# --------------------------------------------------------------------------- #


def _personnes_reglees(
    ctx: Contexte, regle: RegleResolue, lire: Callable[[Personne], object]
) -> list[Personne]:
    """Personnes de la portée dont le réglage vient de cette règle (et non d'une plus précise)."""
    personnes = []
    for personne in sorted(regle.personnes, key=lambda p: (p.nature, p.id)):
        reglage = lire(personne)
        source = getattr(reglage, "source", None)
        if source is not None and source.regle is not None and source.regle.id == regle.id:
            personnes.append(personne)
    return personnes


def _sujet(
    ctx: Contexte, groupes: Sequence[int], enseignants: Sequence[int]
) -> tuple[str, bool, list[int]]:
    """Sujet d'une phrase pour des personnes réunies, pluriel, et groupes en cause."""
    nommes, en_cause = ctx.nommer_groupes(groupes) if groupes else ([], [])
    sujet, pluriel = ctx.personnes(enseignants, nommes)
    return sujet, pluriel, en_cause


@enregistrer("verificateur", TypeRegle.PAUSE_DEJEUNER)
def verifier_pause_dejeuner(
    ctx: Contexte, regle: RegleResolue, elements: Sequence[Element]
) -> Iterator[Anomalie]:
    """Personnes dont le déjeuner vient de la règle, un jour sans pause conforme.

    Mode fixe : aucune séance de la personne ne recoupe la plage. Mode libre :
    chaque jour où elle a cours dans la fenêtre, au moins `duree` minutes
    libres d'un seul tenant dans `[debut, fin]`. Mode « aucun » : rien.
    """
    personnes = _personnes_reglees(ctx, regle, ctx.dejeuner_de)
    if not personnes:
        return
    reglage = ctx.dejeuner_de(personnes[0])
    if reglage is None or not reglage.actif:
        return
    x, y, n = reglage.debut, reglage.fin, reglage.duree
    fixe = reglage.plage is not None
    if fixe:

        def examiner(seances: list[Element]) -> Defaut | None:
            return defaut_fixe(seances, x, y)
    else:

        def examiner(seances: list[Element]) -> Defaut | None:
            return defaut_libre(seances, x, y, n)

    defauts = [
        (personne, defaut)
        for personne, seances in par_personne(
            ctx, elements, personnes, x, y, regle.semaines
        ).items()
        for defaut in jours_en_defaut(seances, examiner)
    ]
    for defaut, groupes, enseignants in situations(defauts):
        sujet, pluriel, en_cause = _sujet(ctx, groupes, enseignants)
        premiere = defaut.seances[0]
        quand = f"{ctx.quand(premiere)}{ctx.precision_semaines(defaut.semaines)}"
        if fixe:
            verbe = "ont" if pluriel else "a"
            ecart_texte = f"{sujet} {verbe} cours pendant la pause déjeuner ({plage(x, y)}) {quand}"
        else:
            negation = "n'ont" if pluriel else "n'a"
            libre = (
                "aucun moment libre"
                if not defaut.plus_longue
                else f"au plus {duree(defaut.plus_longue)} libres d'affilée"
            )
            ecart_texte = (
                f"{sujet} {negation} pas {duree(n)} de pause entre {heure(x)} et {heure(y)} "
                f"{quand} ({libre})"
            )
        imposees = all(e.imposee for e in defaut.seances)
        yield ctx.anomalie(
            CodeContrainte.REGLE,
            Gravite.INFORMATION if imposees else Gravite.ERREUR,
            f"{entete(regle)} : {_minuscule_article(ecart_texte)} : "
            f"{liste_seances(ctx, defaut)}." + (MENTION_IMPOSEES if imposees else ""),
            defaut.seances,
            groupes=en_cause,
            enseignants=enseignants,
            regle=regle,
            **ctx.reperes(premiere),
        )


@enregistrer("verificateur", TypeRegle.PAUSE_MINIMALE)
def verifier_pause_minimale(
    ctx: Contexte, regle: RegleResolue, elements: Sequence[Element]
) -> Iterator[Anomalie]:
    """Paires de séances consécutives d'une personne réglée par la règle, trop rapprochées.

    Comme `PAUSE` : les deux séances ont lieu le même jour (et une même semaine
    couverte par la règle), ne se chevauchent pas, et l'écart entre la fin de
    la première et le début de la seconde est inférieur à `duree`. Une
    anomalie par paire, qui réunit les personnes concernées.
    """
    personnes = _personnes_reglees(ctx, regle, ctx.pause_de)
    if not personnes:
        return
    minimum = regle.valeurs["duree"]
    par_jour: dict[Personne, dict[object, list[Element]]] = defaultdict(lambda: defaultdict(list))
    voulues = set(personnes)
    for element in elements:
        semaines = ctx.semaines_regle(regle, element)
        if semaines is not None and not semaines:
            continue
        for personne in ctx.personnes_de(element):
            if personne in voulues:
                par_jour[personne][element.cle_jour].append(element)
    paires: dict[
        tuple[int, int], tuple[Element, Element, frozenset[int] | None, list[Personne]]
    ] = {}
    for personne, jours in par_jour.items():
        for seances in jours.values():
            seances.sort(key=cle_chronologique)
            for i, a in enumerate(seances):
                for b in seances[i + 1 :]:
                    communes = _semaines_communes(ctx, regle, a, b)
                    if communes is not None and not communes:
                        continue
                    if se_chevauchent(a.debut, a.fin, b.debut, b.fin):
                        continue
                    if ecart(a.debut, a.fin, b.debut, b.fin) >= minimum:
                        continue
                    entree = paires.setdefault((a.position, b.position), (a, b, communes, []))
                    entree[3].append(personne)
    for a, b, communes, concernees in paires.values():
        groupes = [p.id for p in concernees if p.nature is NaturePersonne.GROUPE]
        enseignants = [p.id for p in concernees if p.nature is NaturePersonne.ENSEIGNANT]
        sujet, pluriel, en_cause = _sujet(ctx, groupes, enseignants)
        libre = ecart(a.debut, a.fin, b.debut, b.fin)
        negation = "n'ont" if pluriel else "n'a"
        constat = (
            f"{sujet} {negation} aucune pause"
            if libre == 0
            else f"{sujet} {negation} que {duree(libre)} de pause"
        )
        constat = _minuscule_article(constat)
        yield ctx.anomalie(
            CodeContrainte.REGLE,
            Gravite.ERREUR,
            f"{entete(regle)} : {constat} {ctx.quand(a)}{ctx.precision_semaines(communes)} "
            f"entre {ctx.libelle(a)}, qui finit à {heure(a.fin)}, et {ctx.libelle(b)}, qui "
            f"commence à {heure(b.debut)} ; il faut au moins {duree(minimum)}.",
            (a, b),
            groupes=en_cause,
            enseignants=enseignants,
            regle=regle,
            **ctx.reperes(a),
        )


def _minuscule_article(texte: str) -> str:
    """Après « : », « Le groupe… » devient « le groupe… » ; un nom propre est gardé."""
    if texte.startswith(("Le ", "Les ")):
        return texte[:1].lower() + texte[1:]
    return texte


def _semaines_communes(
    ctx: Contexte, regle: RegleResolue, a: Element, b: Element
) -> frozenset[int] | None:
    """Semaines où les deux séances ont lieu ensemble et que la règle couvre (`None` : daté)."""
    if a.semaines is None or b.semaines is None:
        return None
    return a.semaines & b.semaines & regle.semaines
