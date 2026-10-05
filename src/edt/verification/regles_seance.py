"""Règles portant sur une seule séance placée.

- `DEJEUNER` : la plage de la séance recoupe `[dejeuner_debut_min,
  dejeuner_fin_min[` (aucune vérification si cette plage est vide) ;
- `HORAIRES` : (a) ouverture et fermeture, (b) début sur un début de
  créneau de la grille du jour, (c) pas de débordement de la demi-journée
  du créneau de début, (d) durée placée égale à la durée du besoin,
  (e) jour, début et date imposés respectés ;
- `JOURS` : jour avec au moins un créneau dans la grille, samedi réservé aux
  types de séance qui l'autorisent ;
- salle : `ACCESSIBILITE`, `CAPACITE`, `TYPE_SALLE` (type accepté, salle
  imposée, campus imposé), `EQUIPEMENT` ; une séance sans salle donne un
  avertissement `VOLUME` (« salle à définir ») et échappe aux règles de salle ;
- disponibilités : `DISPO_ENSEIGNANT` (enseignants non fictifs),
  `DISPO_SALLE`, `DISPO_GROUPE` (groupes du besoin et groupes liés).

Gravité : `ERREUR`, sauf pour une séance **imposée** (début imposé, besoin
ponctuel ou verrou) où `DEJEUNER`, `JOURS` et les points (a), (b), (c) de
`HORAIRES` deviennent des `INFORMATION` : un créneau imposé peut sortir de la
grille. Les points (d) et (e) restent des erreurs.

Chaque règle produit au plus une anomalie par séance et par gravité (les
écarts d'une même règle sont réunis dans un seul message), sauf les
disponibilités : une anomalie par indisponibilité touchée.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import SAMEDI, Accessibilite, CibleIndisponibilite, Gravite
from edt.domaine.heures import se_chevauchent
from edt.domaine.instantane import Indisponibilite, Placement
from edt.verification.contexte import Contexte, Element
from edt.verification.contrat import Anomalie
from edt.verification.messages import date_texte, duree, enumerer, heure, nom_jour, plage

_MENTION_IMPOSEE = " Son créneau est imposé : l'écart est signalé pour information."


def verifier_seances(ctx: Contexte, elements: Sequence[Element]) -> Iterator[Anomalie]:
    """Anomalies des règles qui portent sur une seule séance (voir l'en-tête du module)."""
    for element in elements:
        yield from _dejeuner(ctx, element)
        yield from _horaires(ctx, element)
        yield from _jours(ctx, element)
        yield from _salle(ctx, element)
        yield from _disponibilites(ctx, element)


def ecart_au_verrou(ctx: Contexte, element: Element, verrou: Placement) -> tuple[str, str] | None:
    """Compare une séance placée à son verrou (`VERROU`).

    Renvoie `None` si elle est au jour, aux heures et (si le verrou en fixe
    une) dans la salle du verrou ; sinon le couple (place attendue, place
    réelle) en clair, par exemple (« le mardi de 8 h 30 à 10 h 30 en salle
    N204 », « le jeudi de 10 h 45 à 12 h 45 en salle L101 »).
    """
    meme_horaire = (element.jour, element.debut, element.fin) == (
        verrou.jour,
        verrou.debut,
        verrou.fin,
    )
    meme_salle = verrou.salle_id is None or element.salle_id == verrou.salle_id
    if meme_horaire and meme_salle:
        return None
    attendu = f"le {nom_jour(verrou.jour)} {plage(verrou.debut, verrou.fin)}"
    place = ctx.moment(element)
    if verrou.salle_id is not None:
        attendu += f" en salle {ctx.salle(verrou.salle_id).code}"
        place += (
            " sans salle"
            if element.salle_id is None
            else f" en salle {ctx.salle(element.salle_id).code}"
        )
    return attendu, place


def _gravite_ecart(element: Element) -> Gravite:
    return Gravite.INFORMATION if element.imposee else Gravite.ERREUR


def _dejeuner(ctx: Contexte, element: Element) -> Iterator[Anomalie]:
    if not ctx.actif(CodeContrainte.DEJEUNER):
        return
    debut, fin = ctx.parametres.dejeuner_debut_min, ctx.parametres.dejeuner_fin_min
    if debut >= fin or not se_chevauchent(element.debut, element.fin, debut, fin):
        return
    message = (
        f"La séance {ctx.libelle(element)} {ctx.moment(element)} empiète sur la pause "
        f"déjeuner ({plage(debut, fin)})."
    )
    yield ctx.anomalie(
        CodeContrainte.DEJEUNER,
        _gravite_ecart(element),
        message + (_MENTION_IMPOSEE if element.imposee else ""),
        (element,),
        **ctx.ressources(element),
        **ctx.reperes(element),
    )


def _horaires(ctx: Contexte, element: Element) -> Iterator[Anomalie]:
    if not ctx.actif(CodeContrainte.HORAIRES):
        return
    parametres = ctx.parametres
    jour = nom_jour(element.jour)

    # (a), (b), (c) : écarts à la grille, admis pour un créneau imposé.
    grille: list[str] = []
    if element.debut < parametres.ouverture_min:
        grille.append(f"elle commence avant l'ouverture ({heure(parametres.ouverture_min)})")
    if element.fin > parametres.fermeture_min:
        grille.append(f"elle finit après la fermeture ({heure(parametres.fermeture_min)})")
    debuts = ctx.instantane.debuts_de_creneau(element.jour)
    limite = ctx.limite_demi_journee(element.jour, element.debut)
    if not debuts:
        grille.append(f"la grille horaire n'a aucun créneau le {jour}")
    elif limite is None:
        heures = [heure(debut) for debut in debuts]
        grille.append(
            f"elle ne commence pas sur un début de créneau de la grille du {jour} "
            f"({enumerer(heures, 'ou')})"
        )
    elif element.fin > limite:
        grille.append(
            f"elle déborde de sa demi-journée, dont le dernier créneau finit à {heure(limite)}"
        )

    # (d), (e) : durée et exigences du besoin, toujours des erreurs.
    demandes: list[str] = []
    besoin = element.besoin
    if element.duree != besoin.duree:
        demandes.append(
            f"elle dure {duree(element.duree)} au lieu des {duree(besoin.duree)} demandées"
        )
    if element.date_imposee is not None and element.date != element.date_imposee:
        demandes.append(f"elle doit avoir lieu le {date_texte(element.date_imposee)}")
    if element.jour_impose is not None and element.jour != element.jour_impose:
        demandes.append(f"elle doit avoir lieu un {nom_jour(element.jour_impose)}")
    if besoin.debut_impose is not None and element.debut != besoin.debut_impose:
        demandes.append(f"elle doit commencer à {heure(besoin.debut_impose)}")

    situation = f"La séance {ctx.libelle(element)} {ctx.moment(element)}"
    if grille:
        yield ctx.anomalie(
            CodeContrainte.HORAIRES,
            _gravite_ecart(element),
            f"{situation} sort de la grille horaire : {' ; '.join(grille)}."
            + (_MENTION_IMPOSEE if element.imposee else ""),
            (element,),
            **ctx.ressources(element),
            **ctx.reperes(element),
        )
    if demandes:
        yield ctx.anomalie(
            CodeContrainte.HORAIRES,
            Gravite.ERREUR,
            f"{situation} ne respecte pas ce que demande son besoin : {' ; '.join(demandes)}.",
            (element,),
            **ctx.ressources(element),
            **ctx.reperes(element),
        )


def _jours(ctx: Contexte, element: Element) -> Iterator[Anomalie]:
    if not ctx.actif(CodeContrainte.JOURS):
        return
    jour = nom_jour(element.jour)
    raisons: list[str] = []
    if not ctx.instantane.debuts_de_creneau(element.jour):
        raisons.append(f"la grille horaire n'a aucun créneau le {jour}")
    if element.jour == SAMEDI and not element.besoin.samedi_autorise:
        raisons.append(
            f"le type de séance {element.besoin.type_seance_code} n'est pas autorisé le samedi"
        )
    if not raisons:
        return
    yield ctx.anomalie(
        CodeContrainte.JOURS,
        _gravite_ecart(element),
        f"La séance {ctx.libelle(element)} est placée {ctx.quand(element)} alors que "
        f"{' et que '.join(raisons)}." + (_MENTION_IMPOSEE if element.imposee else ""),
        (element,),
        **ctx.ressources(element),
        **ctx.reperes(element),
    )


def _salle(ctx: Contexte, element: Element) -> Iterator[Anomalie]:
    seance = f"la séance {ctx.libelle(element)} {ctx.moment(element)}"
    if element.salle_id is None:
        if ctx.actif(CodeContrainte.VOLUME):
            yield ctx.anomalie(
                CodeContrainte.VOLUME,
                Gravite.AVERTISSEMENT,
                f"La séance {ctx.libelle(element)} {ctx.moment(element)} n'a pas encore de "
                "salle (salle à définir).",
                (element,),
                **ctx.ressources(element),
                **ctx.reperes(element),
            )
        return

    salle = ctx.salle(element.salle_id)
    besoin = element.besoin
    cibles = {
        "salles": (salle.id,),
        "groupes": tuple(sorted(besoin.groupes)),
        **ctx.reperes(element),
    }

    if (
        ctx.actif(CodeContrainte.ACCESSIBILITE)
        and besoin.accessibilite_requise
        and not ctx.instantane.salle_accessible(salle)
    ):
        if salle.accessibilite is Accessibilite.INCONNU:
            etat = "a une accessibilité inconnue, comptée comme non accessible"
        else:
            etat = "n'est pas accessible"
        yield ctx.anomalie(
            CodeContrainte.ACCESSIBILITE,
            Gravite.ERREUR,
            f"La salle {salle.code} {etat}, alors que {seance} requiert une salle accessible.",
            (element,),
            **cibles,
        )

    if ctx.actif(CodeContrainte.CAPACITE) and salle.capacite < besoin.effectif:
        yield ctx.anomalie(
            CodeContrainte.CAPACITE,
            Gravite.ERREUR,
            f"La salle {salle.code} ({salle.capacite} places) est trop petite pour {seance}, "
            f"qui réunit {besoin.effectif} étudiants.",
            (element,),
            **cibles,
        )

    if ctx.actif(CodeContrainte.TYPE_SALLE):
        raisons: list[str] = []
        if besoin.types_salle_acceptes and salle.type_salle_id not in besoin.types_salle_acceptes:
            raisons.append("son type de salle ne fait pas partie des types acceptés")
        if besoin.salle_imposee_id is not None and salle.id != besoin.salle_imposee_id:
            raisons.append(f"la salle imposée est {ctx.salle(besoin.salle_imposee_id).code}")
        if besoin.campus_impose_id is not None and salle.campus_id != besoin.campus_impose_id:
            raisons.append(f"le campus imposé est {ctx.campus(besoin.campus_impose_id).nom}")
        if raisons:
            yield ctx.anomalie(
                CodeContrainte.TYPE_SALLE,
                Gravite.ERREUR,
                f"La salle {salle.code} ne convient pas à {seance} : {' ; '.join(raisons)}.",
                (element,),
                **cibles,
            )

    manquants = sorted(besoin.equipements_requis - salle.equipements)
    if ctx.actif(CodeContrainte.EQUIPEMENT) and manquants:
        numeros = enumerer([str(numero) for numero in manquants])
        manque = (
            f"l'équipement n° {numeros}" if len(manquants) == 1 else f"les équipements n° {numeros}"
        )
        yield ctx.anomalie(
            CodeContrainte.EQUIPEMENT,
            Gravite.ERREUR,
            f"La salle {salle.code} n'a pas tous les équipements requis par {seance} : "
            f"il manque {manque}.",
            (element,),
            **cibles,
        )


def _disponibilites(ctx: Contexte, element: Element) -> Iterator[Anomalie]:
    besoin = element.besoin
    familles = (
        (
            CodeContrainte.DISPO_ENSEIGNANT,
            CibleIndisponibilite.ENSEIGNANT,
            ctx.enseignants_reels(besoin),
        ),
        (
            CodeContrainte.DISPO_SALLE,
            CibleIndisponibilite.SALLE,
            () if element.salle_id is None else (element.salle_id,),
        ),
        (CodeContrainte.DISPO_GROUPE, CibleIndisponibilite.GROUPE, ctx.groupes_lies(besoin)),
    )
    for code, cible, ressources in familles:
        if not ctx.actif(code):
            continue
        for ressource in sorted(ressources):
            if element.date is not None:
                for indisponibilite in ctx.indisponibilites_dures(cible, ressource):
                    if indisponibilite.concerne(element.date) and se_chevauchent(
                        element.debut, element.fin, indisponibilite.debut, indisponibilite.fin
                    ):
                        yield _anomalie_dispo(ctx, code, cible, ressource, indisponibilite, element)
                continue
            for plage_interdite in ctx.plages_interdites(cible, ressource):
                assert element.semaines is not None  # semaine type
                communes = plage_interdite.semaines & element.semaines
                if (
                    plage_interdite.jour == element.jour
                    and communes
                    and se_chevauchent(
                        element.debut, element.fin, plage_interdite.debut, plage_interdite.fin
                    )
                ):
                    yield _anomalie_dispo(
                        ctx,
                        code,
                        cible,
                        ressource,
                        plage_interdite.indisponibilite,
                        element,
                        communes,
                    )


def _anomalie_dispo(
    ctx: Contexte,
    code: CodeContrainte,
    cible: CibleIndisponibilite,
    ressource: int,
    indisponibilite: Indisponibilite,
    element: Element,
    semaines: frozenset[int] | None = None,
) -> Anomalie:
    if cible is CibleIndisponibilite.ENSEIGNANT:
        sujet = ctx.nom_enseignant(ressource)
        champs = {"enseignants": (ressource,)}
    elif cible is CibleIndisponibilite.SALLE:
        sujet = f"La salle {ctx.salle(ressource).code}"
        champs = {"salles": (ressource,)}
    else:
        sujet = f"Le groupe {ctx.code_groupe(ressource)}"
        champs = {"groupes": (ressource,)}
    motif = f" ({indisponibilite.motif})" if indisponibilite.motif.strip() else ""
    return ctx.anomalie(
        code,
        Gravite.ERREUR,
        f"{sujet} est indisponible {ctx.quand(element)} "
        f"{plage(indisponibilite.debut, indisponibilite.fin)}{motif}, pendant la séance "
        f"{ctx.libelle(element)} prévue {plage(element.debut, element.fin)}"
        f"{ctx.precision_semaines(semaines)}.",
        (element,),
        **champs,
        **ctx.reperes(element),
    )
