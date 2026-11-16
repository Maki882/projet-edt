"""Occupation des salles et des personnes sur un créneau de la version active d'un semestre.

Trois questions, en lecture seule :

- `plage_du_moment` : la plage horaire d'une demi-journée d'un jour, d'après
  la grille (du début du premier créneau du matin à la fin du dernier), ou la
  journée d'ouverture de l'établissement ;
- `salles_libres` : les salles libres sur un créneau, un jour de la **semaine
  type** (séances hebdomadaires, toutes les semaines du semestre) ou à une
  **date** (séances datées s'il y en a, sinon séances de la semaine type de
  cette semaine-là). Une salle est occupée par une séance qui recoupe le
  créneau ou par une indisponibilité de la salle (récurrente ce jour-là, ou
  datée). En semaine type, une salle occupée certaines semaines seulement est
  rangée à part avec les semaines où elle est libre, et une indisponibilité
  datée qui touche une salle libre est rendue comme exception datée ;
- `seances_sur_creneau` : les séances d'un enseignant, d'une salle ou d'un
  groupe (et des groupes qui partagent ses étudiants) qui tombent sur un
  créneau récurrent ou sur une période datée : ce qu'une indisponibilité
  nouvelle mettrait en conflit.

Les créneaux sont des plages semi-ouvertes `[début, fin[` en minutes. Le
résultat est fait de dataclasses figées, sérialisables.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from edt.db import modeles as m
from edt.domaine.calendrier import Semaine, semaine_de, semaines_entre
from edt.domaine.enumerations import JOURS, CibleIndisponibilite, DemiJournee, StatutOccurrence
from edt.domaine.heures import minutes_vers_texte, se_chevauchent
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable, ErreurRegleMetier
from edt.services.conflits_groupes import conflits_groupes
from edt.services.parametres import lire_parametres

journal = logging.getLogger(__name__)

MOMENTS = ("matin", "apres_midi", "journee")
"""Moments d'une journée reconnus par `plage_du_moment`."""

_LIBELLES_MOMENT = {"matin": "le matin", "apres_midi": "l'après-midi", "journee": "la journée"}
_STATUTS_TENUS = (StatutOccurrence.PLANIFIEE, StatutOccurrence.DEPLACEE)


@dataclass(frozen=True)
class SalleVue:
    """Une salle telle qu'elle est citée : code, campus, places, type."""

    id: int
    code: str
    campus: str
    capacite: int
    type_salle: str


@dataclass(frozen=True)
class Occupation:
    """Ce qui occupe une salle sur le créneau : une séance ou une indisponibilité.

    `semaines` : semaines du semestre concernées (semaine type) ; vide pour
    une occupation à une date.
    """

    salle: SalleVue
    debut: int
    fin: int
    libelle: str
    semaines: tuple[int, ...] = ()


@dataclass(frozen=True)
class ExceptionDatee:
    """Indisponibilité datée qui touche une salle autrement libre sur le créneau (semaine type)."""

    salle: SalleVue
    dates: tuple[dt.date, ...]
    debut: int
    fin: int
    motif: str | None


@dataclass(frozen=True)
class SallesLibres:
    """Salles libres sur un créneau (voir `salles_libres`)."""

    semestre_code: str
    version_numero: int
    jour: int
    date: dt.date | None
    debut: int
    fin: int
    nb_semaines: int
    libres: tuple[SalleVue, ...]
    libres_certaines_semaines: tuple[tuple[SalleVue, tuple[int, ...]], ...]
    occupees: tuple[Occupation, ...]
    exceptions: tuple[ExceptionDatee, ...]
    fermeture: str | None = None


# --------------------------------------------------------------------------- #
# Plage d'un moment
# --------------------------------------------------------------------------- #


def plage_du_moment(session: Session, jour: int, moment: str) -> tuple[int, int]:
    """Plage `[début, fin[` du matin, de l'après-midi ou de la journée d'un jour (0 = lundi).

    Matin et après-midi suivent la grille horaire de ce jour ; la journée va de
    l'ouverture à la fermeture de l'établissement. Lève `ErreurDonneesInvalides`
    pour un moment inconnu ou une demi-journée sans créneau ce jour-là.
    """
    _verifier_jour(jour)
    if moment not in MOMENTS:
        raise ErreurDonneesInvalides(
            f"Moment inconnu : « {moment} » ; attendu : " + ", ".join(MOMENTS) + "."
        )
    if moment == "journee":
        parametres = lire_parametres(session)
        return parametres.ouverture_min, parametres.fermeture_min
    demi = DemiJournee(moment)
    bornes = session.execute(
        select(m.Creneau.debut_min, m.Creneau.fin_min).where(
            m.Creneau.jour == jour, m.Creneau.demi_journee == demi
        )
    ).all()
    if not bornes:
        raise ErreurDonneesInvalides(
            f"La grille horaire n'a aucun créneau le {JOURS[jour]} {_LIBELLES_MOMENT[moment]} : "
            "préciser l'horaire (début et fin)."
        )
    return min(debut for debut, _ in bornes), max(fin for _, fin in bornes)


# --------------------------------------------------------------------------- #
# Salles libres
# --------------------------------------------------------------------------- #


def salles_libres(
    session: Session,
    *,
    semestre_id: int,
    debut: int,
    fin: int,
    jour: int | None = None,
    date: dt.date | None = None,
    capacite_min: int | None = None,
    campus: str | None = None,
    type_salle: str | None = None,
) -> SallesLibres:
    """Salles libres sur `[debut, fin[`, un jour de la semaine type ou à une date (voir l'en-tête).

    Filtres : capacité minimale, code de campus, code de type de salle (un
    code inconnu est refusé avec la liste des codes). Lève `ErreurRegleMetier`
    si le semestre n'a pas de version active.
    """
    semestre, version = _semestre_et_version(session, semestre_id)
    _verifier_plage(debut, fin)
    if (jour is None) == (date is None):
        raise ErreurDonneesInvalides("Donner un jour de la semaine type ou une date, pas les deux.")
    semaines = semaines_entre(semestre.date_debut, semestre.date_fin)
    if date is not None:
        if not semestre.date_debut <= date <= semestre.date_fin:
            raise ErreurDonneesInvalides(
                f"Le {date:%d/%m/%Y} est hors du semestre {semestre.code} (du "
                f"{semestre.date_debut:%d/%m/%Y} au {semestre.date_fin:%d/%m/%Y})."
            )
        jour = date.weekday()
    assert jour is not None
    _verifier_jour(jour)
    salles = _salles_candidates(session, capacite_min, campus, type_salle)
    vues = {salle.id: _vue(salle) for salle in salles}
    if date is None:
        return _semaine_type(session, semestre, version, semaines, vues, jour, debut, fin)
    return _a_la_date(session, semestre, version, semaines, vues, date, debut, fin)


def _semaine_type(
    session: Session,
    semestre: m.Semestre,
    version: m.VersionEdt,
    semaines: tuple[Semaine, ...],
    vues: dict[int, SalleVue],
    jour: int,
    debut: int,
    fin: int,
) -> SallesLibres:
    toutes = _semaines_ouvertes(session, semestre, semaines, jour)
    occupees: dict[int, set[int]] = {ident: set() for ident in vues}
    occupations: list[Occupation] = []
    for seance in _seances_hebdo(session, version.id, jour, debut, fin, salles=vues):
        semaines_seance = tuple(sorted(set(seance.semaines or ()) & toutes))
        if not semaines_seance:
            continue
        occupees[seance.salle_id].update(semaines_seance)
        occupations.append(
            Occupation(
                vues[seance.salle_id],
                seance.debut_min,
                seance.fin_min,
                _libelle_seance(seance.besoin),
                semaines_seance,
            )
        )
    exceptions: list[ExceptionDatee] = []
    for indisponibilite in _indisponibilites_salles(session, vues):
        if not se_chevauchent(debut, fin, indisponibilite.debut_min, indisponibilite.fin_min):
            continue
        vue = vues[indisponibilite.salle_id]
        if indisponibilite.jour is not None:
            if indisponibilite.jour != jour:
                continue
            occupees[vue.id].update(toutes)
            occupations.append(
                Occupation(
                    vue,
                    max(debut, indisponibilite.debut_min),
                    min(fin, indisponibilite.fin_min),
                    _libelle_indisponibilite(indisponibilite),
                    tuple(sorted(toutes)),
                )
            )
            continue
        dates = tuple(
            semaine.date_du(jour)
            for semaine in semaines
            if semaine.numero in toutes
            and indisponibilite.date_debut <= semaine.date_du(jour) <= indisponibilite.date_fin
        )
        if dates:
            exceptions.append(
                ExceptionDatee(
                    vue,
                    dates,
                    max(debut, indisponibilite.debut_min),
                    min(fin, indisponibilite.fin_min),
                    indisponibilite.motif,
                )
            )
    libres = [vue for vue in vues.values() if not occupees[vue.id]]
    partielles = [
        (vue, tuple(sorted(toutes - occupees[vue.id])))
        for vue in vues.values()
        if occupees[vue.id] and not occupees[vue.id] >= toutes
    ]
    pleines = {ident for ident, prises in occupees.items() if prises >= toutes}
    exceptions = [e for e in exceptions if e.salle.id not in pleines]
    return SallesLibres(
        semestre_code=semestre.code,
        version_numero=version.numero,
        jour=jour,
        date=None,
        debut=debut,
        fin=fin,
        nb_semaines=len(toutes),
        libres=tuple(sorted(libres, key=_ordre)),
        libres_certaines_semaines=tuple(sorted(partielles, key=lambda p: _ordre(p[0]))),
        occupees=tuple(
            sorted(
                (o for o in occupations if o.salle.id in pleines),
                key=lambda o: (_ordre(o.salle), o.debut),
            )
        ),
        exceptions=tuple(sorted(exceptions, key=lambda e: (_ordre(e.salle), e.dates))),
    )


def _a_la_date(
    session: Session,
    semestre: m.Semestre,
    version: m.VersionEdt,
    semaines: tuple[Semaine, ...],
    vues: dict[int, SalleVue],
    date: dt.date,
    debut: int,
    fin: int,
) -> SallesLibres:
    jour = date.weekday()
    occupations: list[Occupation] = []
    if _a_des_occurrences(session, version.id):
        for occurrence in _occurrences(session, version.id, date, date, debut, fin, salles=vues):
            occupations.append(
                Occupation(
                    vues[occurrence.salle_id],
                    occurrence.debut_min,
                    occurrence.fin_min,
                    _libelle_seance(occurrence.besoin),
                )
            )
    else:
        semaine = semaine_de(date, semaines)
        numero = semaine.numero if semaine is not None else None
        for seance in _seances_hebdo(session, version.id, jour, debut, fin, salles=vues):
            if numero in (seance.semaines or ()):
                occupations.append(
                    Occupation(
                        vues[seance.salle_id],
                        seance.debut_min,
                        seance.fin_min,
                        _libelle_seance(seance.besoin),
                    )
                )
    for indisponibilite in _indisponibilites_salles(session, vues):
        if not se_chevauchent(debut, fin, indisponibilite.debut_min, indisponibilite.fin_min):
            continue
        tombe = (
            indisponibilite.jour == jour
            if indisponibilite.jour is not None
            else indisponibilite.date_debut <= date <= indisponibilite.date_fin
        )
        if tombe:
            occupations.append(
                Occupation(
                    vues[indisponibilite.salle_id],
                    max(debut, indisponibilite.debut_min),
                    min(fin, indisponibilite.fin_min),
                    _libelle_indisponibilite(indisponibilite),
                )
            )
    prises = {occupation.salle.id for occupation in occupations}
    return SallesLibres(
        semestre_code=semestre.code,
        version_numero=version.numero,
        jour=jour,
        date=date,
        debut=debut,
        fin=fin,
        nb_semaines=len(semaines),
        libres=tuple(sorted((v for v in vues.values() if v.id not in prises), key=_ordre)),
        libres_certaines_semaines=(),
        occupees=tuple(sorted(occupations, key=lambda o: (_ordre(o.salle), o.debut))),
        exceptions=(),
        fermeture=_fermeture(session, semestre.annee_scolaire_id, date),
    )


# --------------------------------------------------------------------------- #
# Séances d'une ressource sur un créneau
# --------------------------------------------------------------------------- #


def seances_sur_creneau(
    session: Session,
    *,
    semestre_id: int,
    cible: CibleIndisponibilite | str,
    ressource_id: int,
    debut: int,
    fin: int,
    jour: int | None = None,
    date_debut: dt.date | None = None,
    date_fin: dt.date | None = None,
) -> tuple[str, ...]:
    """Séances de la version active qui tombent sur le créneau, pour une ressource.

    Créneau récurrent (`jour`) : séances de la semaine type ce jour-là. Période
    datée (`date_debut`, `date_fin`) : séances datées de la période, ou, sans
    séances datées, séances de la semaine type des jours de la période. Une
    phrase par séance (« mercredi 8 h 30 - 10 h 30 : TD Mathématiques, A3-G1 »,
    datée s'il le faut). Sans version active : aucune séance.
    """
    cible = CibleIndisponibilite(cible)
    semestre = session.get(m.Semestre, semestre_id)
    if semestre is None:
        raise ErreurIntrouvable(f"Le semestre n° {semestre_id} n'existe pas.")
    version = _version_active(session, semestre_id)
    if version is None:
        return ()
    _verifier_plage(debut, fin)
    groupes = (
        _groupes_lies(session, semestre.annee_scolaire_id, ressource_id)
        if cible is CibleIndisponibilite.GROUPE
        else frozenset()
    )

    def concerne(besoin: m.Besoin, salle_id: int | None) -> bool:
        if cible is CibleIndisponibilite.SALLE:
            return salle_id == ressource_id
        if cible is CibleIndisponibilite.ENSEIGNANT:
            return any(e.id == ressource_id for e in besoin.enseignants)
        return any(g.id in groupes for g in besoin.groupes)

    phrases: list[tuple[tuple, str]] = []
    if jour is not None:
        _verifier_jour(jour)
        for seance in _seances_hebdo(session, version.id, jour, debut, fin):
            if concerne(seance.besoin, seance.salle_id):
                phrases.append(
                    (
                        (seance.debut_min, seance.besoin_id),
                        f"{JOURS[jour]} {_plage(seance.debut_min, seance.fin_min)} : "
                        f"{_libelle_seance(seance.besoin)}",
                    )
                )
        return tuple(texte for _, texte in sorted(phrases))
    if date_debut is None:
        raise ErreurDonneesInvalides("Donner un jour (créneau récurrent) ou une date de début.")
    date_fin = date_fin or date_debut
    premiere, derniere = max(date_debut, semestre.date_debut), min(date_fin, semestre.date_fin)
    if premiere > derniere:
        return ()
    if _a_des_occurrences(session, version.id):
        for occurrence in _occurrences(session, version.id, premiere, derniere, debut, fin):
            if concerne(occurrence.besoin, occurrence.salle_id):
                phrases.append(
                    (
                        (occurrence.date, occurrence.debut_min, occurrence.besoin_id),
                        f"{JOURS[occurrence.date.weekday()]} {occurrence.date:%d/%m/%Y} "
                        f"{_plage(occurrence.debut_min, occurrence.fin_min)} : "
                        f"{_libelle_seance(occurrence.besoin)}",
                    )
                )
        return tuple(texte for _, texte in sorted(phrases))
    semaines = semaines_entre(semestre.date_debut, semestre.date_fin)
    jour_courant = premiere
    while jour_courant <= derniere:
        semaine = semaine_de(jour_courant, semaines)
        for seance in _seances_hebdo(session, version.id, jour_courant.weekday(), debut, fin):
            if (
                semaine is not None
                and semaine.numero in (seance.semaines or ())
                and concerne(seance.besoin, seance.salle_id)
            ):
                phrases.append(
                    (
                        (jour_courant, seance.debut_min, seance.besoin_id),
                        f"{JOURS[jour_courant.weekday()]} {jour_courant:%d/%m/%Y} "
                        f"{_plage(seance.debut_min, seance.fin_min)} : "
                        f"{_libelle_seance(seance.besoin)}",
                    )
                )
        jour_courant += dt.timedelta(days=1)
    return tuple(texte for _, texte in sorted(phrases))


# --------------------------------------------------------------------------- #
# Interne
# --------------------------------------------------------------------------- #


def _verifier_jour(jour: int) -> None:
    if isinstance(jour, bool) or not isinstance(jour, int) or not 0 <= jour <= 6:
        raise ErreurDonneesInvalides(
            f"Jour invalide : {jour!r} (attendu : 0 = lundi à 6 = dimanche)."
        )


def _verifier_plage(debut: int, fin: int) -> None:
    if not 0 <= debut < fin <= 24 * 60:
        raise ErreurDonneesInvalides(
            "Horaire invalide : le début doit précéder la fin, dans la journée "
            f"(lu : {_heure(debut)} - {_heure(fin)})."
        )


def _heure(minutes: object) -> str:
    if isinstance(minutes, int) and 0 <= minutes <= 24 * 60:
        return minutes_vers_texte(minutes)
    return repr(minutes)


def _plage(debut: int, fin: int) -> str:
    return f"{minutes_vers_texte(debut)} - {minutes_vers_texte(fin)}"


def _version_active(session: Session, semestre_id: int) -> m.VersionEdt | None:
    return session.scalar(
        select(m.VersionEdt).where(
            m.VersionEdt.semestre_id == semestre_id, m.VersionEdt.active.is_(True)
        )
    )


def _semestre_et_version(session: Session, semestre_id: int) -> tuple[m.Semestre, m.VersionEdt]:
    semestre = session.get(m.Semestre, semestre_id)
    if semestre is None:
        raise ErreurIntrouvable(f"Le semestre n° {semestre_id} n'existe pas.")
    version = _version_active(session, semestre_id)
    if version is None:
        raise ErreurRegleMetier(
            f"Le semestre {semestre.code} n'a pas encore d'emploi du temps actif : lancer un "
            "calcul dans « Générer », ou activer une version."
        )
    return semestre, version


def _salles_candidates(
    session: Session, capacite_min: int | None, campus: str | None, type_salle: str | None
) -> list[m.Salle]:
    requete = select(m.Salle).options(joinedload(m.Salle.campus), joinedload(m.Salle.type_salle))
    if capacite_min is not None:
        if isinstance(capacite_min, bool) or not isinstance(capacite_min, int) or capacite_min < 0:
            raise ErreurDonneesInvalides(f"Capacité minimale invalide : {capacite_min!r}.")
        requete = requete.where(m.Salle.capacite >= capacite_min)
    if campus:
        codes = list(session.scalars(select(m.Campus.code).order_by(m.Campus.code)))
        trouve = _code_sans_casse(campus, codes)
        if trouve is None:
            raise ErreurDonneesInvalides(
                f"Campus inconnu : « {campus} » ; campus : {', '.join(codes) or 'aucun'}."
            )
        requete = requete.join(m.Campus, m.Salle.campus_id == m.Campus.id).where(
            m.Campus.code == trouve
        )
    if type_salle:
        codes = list(session.scalars(select(m.TypeSalle.code).order_by(m.TypeSalle.code)))
        trouve = _code_sans_casse(type_salle, codes)
        if trouve is None:
            raise ErreurDonneesInvalides(
                f"Type de salle inconnu : « {type_salle} » ; types : {', '.join(codes) or 'aucun'}."
            )
        requete = requete.join(m.TypeSalle, m.Salle.type_salle_id == m.TypeSalle.id).where(
            m.TypeSalle.code == trouve
        )
    return list(session.scalars(requete.order_by(m.Salle.code, m.Salle.id)).unique())


def _code_sans_casse(code: str, codes: Iterable[str]) -> str | None:
    cherche = code.strip().casefold()
    return next((c for c in codes if c.casefold() == cherche), None)


def _vue(salle: m.Salle) -> SalleVue:
    return SalleVue(salle.id, salle.code, salle.campus.code, salle.capacite, salle.type_salle.code)


def _ordre(vue: SalleVue) -> tuple[str, str, int]:
    return (vue.campus, vue.code, vue.id)


def _options_besoin():
    return selectinload(m.SeanceHebdo.besoin).options(
        selectinload(m.Besoin.groupes),
        selectinload(m.Besoin.enseignants),
        joinedload(m.Besoin.matiere),
        joinedload(m.Besoin.type_seance),
    )


def _seances_hebdo(
    session: Session,
    version_id: int,
    jour: int,
    debut: int,
    fin: int,
    *,
    salles: dict[int, SalleVue] | None = None,
) -> list[m.SeanceHebdo]:
    requete = select(m.SeanceHebdo).where(
        m.SeanceHebdo.version_id == version_id,
        m.SeanceHebdo.jour == jour,
        m.SeanceHebdo.debut_min < fin,
        m.SeanceHebdo.fin_min > debut,
    )
    if salles is not None:
        requete = requete.where(m.SeanceHebdo.salle_id.in_(list(salles)))
    return list(session.scalars(requete.options(_options_besoin())))


def _a_des_occurrences(session: Session, version_id: int) -> bool:
    return (
        session.scalar(
            select(m.Occurrence.id).where(m.Occurrence.version_id == version_id).limit(1)
        )
        is not None
    )


def _occurrences(
    session: Session,
    version_id: int,
    premiere: dt.date,
    derniere: dt.date,
    debut: int,
    fin: int,
    *,
    salles: dict[int, SalleVue] | None = None,
) -> list[m.Occurrence]:
    requete = select(m.Occurrence).where(
        m.Occurrence.version_id == version_id,
        m.Occurrence.date >= premiere,
        m.Occurrence.date <= derniere,
        m.Occurrence.statut.in_(_STATUTS_TENUS),
        m.Occurrence.debut_min < fin,
        m.Occurrence.fin_min > debut,
    )
    if salles is not None:
        requete = requete.where(m.Occurrence.salle_id.in_(list(salles)))
    return list(
        session.scalars(
            requete.options(
                selectinload(m.Occurrence.besoin).options(
                    selectinload(m.Besoin.groupes),
                    selectinload(m.Besoin.enseignants),
                    joinedload(m.Besoin.matiere),
                    joinedload(m.Besoin.type_seance),
                )
            )
        )
    )


def _indisponibilites_salles(
    session: Session, vues: dict[int, SalleVue]
) -> list[m.Indisponibilite]:
    if not vues:
        return []
    return list(
        session.scalars(
            select(m.Indisponibilite)
            .where(
                m.Indisponibilite.cible == CibleIndisponibilite.SALLE,
                m.Indisponibilite.salle_id.in_(list(vues)),
            )
            .order_by(m.Indisponibilite.id)
        )
    )


def _libelle_seance(besoin: m.Besoin) -> str:
    groupes = ", ".join(sorted(g.code for g in besoin.groupes))
    texte = f"{besoin.type_seance.code} {besoin.matiere.libelle}"
    if besoin.libelle:
        texte += f" ({besoin.libelle})"
    return f"{texte}, {groupes}" if groupes else texte


def _libelle_indisponibilite(indisponibilite: m.Indisponibilite) -> str:
    motif = f" ({indisponibilite.motif})" if indisponibilite.motif else ""
    return f"indisponible{motif}"


def _semaines_ouvertes(
    session: Session, semestre: m.Semestre, semaines: tuple[Semaine, ...], jour: int
) -> frozenset[int]:
    """Semaines dont le jour `jour` tombe dans le semestre et n'est pas fermé pour tous.

    Une semaine de vacances ou un jour férié n'a pas de cours : une salle n'y
    est pas « libre » au sens utile, et une séance de toutes les semaines de
    cours occupe bien sa salle « toutes les semaines ».
    """
    fermetures = session.execute(
        select(m.JourFerme.date_debut, m.JourFerme.date_fin).where(
            m.JourFerme.annee_scolaire_id == semestre.annee_scolaire_id,
            m.JourFerme.groupe_id.is_(None),
        )
    ).all()
    ouvertes = set()
    for semaine in semaines:
        date = semaine.date_du(jour)
        if not semestre.date_debut <= date <= semestre.date_fin:
            continue
        if any(debut <= date <= fin for debut, fin in fermetures):
            continue
        ouvertes.add(semaine.numero)
    return frozenset(ouvertes)


def _fermeture(session: Session, annee_id: int, date: dt.date) -> str | None:
    libelles = session.scalars(
        select(m.JourFerme.libelle).where(
            m.JourFerme.annee_scolaire_id == annee_id,
            m.JourFerme.groupe_id.is_(None),
            m.JourFerme.date_debut <= date,
            m.JourFerme.date_fin >= date,
        )
    ).all()
    return ", ".join(libelles) or None


def _groupes_lies(session: Session, annee_id: int, groupe_id: int) -> frozenset[int]:
    """Le groupe et ceux qui partagent ses étudiants (relation de `conflits_groupes`)."""
    lies = {groupe_id}
    for a, b in conflits_groupes(session, annee_id):
        if a == groupe_id:
            lies.add(b)
        elif b == groupe_id:
            lies.add(a)
    return frozenset(lies)
