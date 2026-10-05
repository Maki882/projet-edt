"""Adaptateur iCal : fichier `.ics` -> séances lues ; et fuseau Europe/Paris pour l'export.

Lecture (`lire_ical`)
---------------------
C'est un **adaptateur isolé** : il produit le même tableau intermédiaire
(`SeanceLue`) que le lecteur du tableau plat, et rien d'autre.

- Événements simples et récurrents : `RRULE` (développée avec
  `dateutil.rrule`), `RDATE`, dates d'exception `EXDATE`, occurrences
  remplacées (`RECURRENCE-ID`, la séance compte à sa position finale et
  passe « déplacée » si elle a bougé), `STATUS:CANCELLED` (annulée).
- Fuseaux : tout est converti en heure locale Europe/Paris puis stocké sans
  fuseau. Une règle décrite dans un fuseau (`TZID=Europe/Paris`) garde son
  heure locale de part et d'autre du changement d'heure ; une règle en UTC
  garde son heure UTC (c'est ce que dit la norme), donc change d'heure
  locale au passage à l'heure d'hiver.
- Une règle de récurrence est développée sur la période du semestre ; ses
  occurrences qui tombent un jour fermé du calendrier sont retirées par la
  reconstitution (`SeanceLue.recurrente`).
- Champs : les propriétés `X-EDT-MATIERE`, `X-EDT-TYPE`, `X-EDT-GROUPES`,
  `X-EDT-ENSEIGNANTS`, `X-EDT-SALLE`, `X-EDT-STATUT` et `X-EDT-LIBELLE`
  (écrites par notre export) priment ; à défaut, le type vient de
  `CATEGORIES`, la salle de `LOCATION`, le libellé de `SUMMARY`. Ce qui manque (matière, groupes)
  fait mettre la séance de côté par la reconstitution, avec un message.
- Un événement sur la journée entière, sans heure de fin ou sur plusieurs
  jours n'est pas une séance : il est signalé et non importé.
- Bornes : un fichier de plus de `MAX_EVENEMENTS` événements, ou qui donne
  plus de séances qu'il n'en reste pour l'envoi, est refusé avant ou pendant
  la lecture ; une règle de récurrence qui donne plus de `MAX_OCCURRENCES`
  occurrences (ou demande plus de `MAX_ITERATIONS` pas pour les trouver)
  est refusée, avec un signalement d'erreur.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any
from zoneinfo import ZoneInfo

from dateutil.rrule import rruleset, rrulestr
from icalendar import Calendar, Timezone, TimezoneDaylight, TimezoneStandard, vRecur
from icalendar.error import InvalidCalendar

from edt.domaine.enumerations import Gravite
from edt.echange.format import StatutSeance
from edt.echange.rapport import Signalement
from edt.echange.seances import SeanceLue
from edt.echange.valeurs import lire_choix, lire_liste, texte_cellule
from edt.erreurs import ErreurDonneesInvalides

journal = logging.getLogger(__name__)

PARIS = ZoneInfo("Europe/Paris")
NOM_FUSEAU = "Europe/Paris"
MAX_EVENEMENTS = 50_000
"""Événements au plus dans un fichier (compté avant l'analyse du fichier)."""
MAX_OCCURRENCES = 1000
"""Occurrences au plus d'une règle de récurrence (un cours hebdomadaire en compte une vingtaine)."""
MAX_ITERATIONS = 10 * MAX_OCCURRENCES
"""Pas de calcul au plus pour développer une règle (une règle très dense est refusée)."""
MAX_SEANCES = 100_000
"""Séances au plus produites par un fichier (défaut de `lire_ical`)."""

PROPRIETES_EDT = {
    "matiere": "X-EDT-MATIERE",
    "type": "X-EDT-TYPE",
    "groupes": "X-EDT-GROUPES",
    "enseignants": "X-EDT-ENSEIGNANTS",
    "salle": "X-EDT-SALLE",
    "statut": "X-EDT-STATUT",
    "libelle": "X-EDT-LIBELLE",
}
"""Propriétés propres à EDT, écrites par l'export et lues en priorité."""


class _Illisible(Exception):  # noqa: N818 - refus d'un événement, converti en signalement
    """Événement qui n'est pas une séance lisible."""


def vers_paris(valeur: dt.datetime) -> dt.datetime:
    """Heure locale Europe/Paris sans fuseau (une heure « flottante » est déjà locale)."""
    if valeur.tzinfo is None:
        return valeur
    return valeur.astimezone(PARIS).replace(tzinfo=None)


def lire_ical(
    nom: str,
    texte: str,
    periode: tuple[dt.date, dt.date] | None = None,
    max_seances: int = MAX_SEANCES,
) -> tuple[list[SeanceLue], list[Signalement]]:
    """Séances d'un fichier iCal ; `periode` borne le développement des récurrences.

    Chaque événement illisible donne un signalement d'erreur (et rien d'autre).
    Un fichier trop grand (événements, ou séances au-delà de `max_seances`)
    est refusé en entier, avec un signalement d'erreur.
    """
    signalements: list[Signalement] = []
    nombre = texte.upper().count("BEGIN:VEVENT")
    if nombre > MAX_EVENEMENTS:
        journal.warning("Fichier iCal %s refusé : %d événements", nom, nombre)
        return [], [
            Signalement(
                Gravite.ERREUR,
                f"Fichier iCal refusé : {nombre} événements, {MAX_EVENEMENTS} au plus. "
                "Découper le fichier.",
                fichier=nom,
            )
        ]
    try:
        calendrier = Calendar.from_ical(texte)
    except ValueError as erreur:
        return [], [Signalement(Gravite.ERREUR, f"Fichier iCal illisible ({erreur}).", fichier=nom)]
    debuts = [
        numero
        for numero, ligne in enumerate(texte.splitlines(), start=1)
        if ligne.strip().upper() == "BEGIN:VEVENT"
    ]
    evenements = calendrier.walk("VEVENT")
    lignes = {id(ev): debuts[i] if i < len(debuts) else None for i, ev in enumerate(evenements)}

    maitres = []
    remplacements: dict[tuple[str, dt.datetime], Any] = {}
    for ev in evenements:
        identifiant = ev.get("RECURRENCE-ID")
        if identifiant is None:
            maitres.append(ev)
        else:
            remplacements[(str(ev.get("UID", "")), _instant(identifiant.dt))] = ev

    seances: list[SeanceLue] = []

    def refus() -> tuple[list[SeanceLue], list[Signalement]]:
        journal.warning("Fichier iCal %s refusé : plus de %d séances", nom, max_seances)
        message = (
            f"Fichier iCal refusé : il donne plus de {max_seances} séances une fois les "
            "récurrences développées. Découper le fichier ou l'envoi."
        )
        return [], [Signalement(Gravite.ERREUR, message, fichier=nom)]

    for ev in maitres:
        seances.extend(
            _lire_ou_signaler(nom, lignes[id(ev)], ev, remplacements, periode, signalements)
        )
        if len(seances) > max_seances:
            return refus()
    for ev in list(remplacements.values()):
        # Remplacement sans événement d'origine dans le fichier : une séance comme une autre.
        seances.extend(_lire_ou_signaler(nom, lignes[id(ev)], ev, {}, periode, signalements))
        if len(seances) > max_seances:
            return refus()
    journal.info(
        "Fichier iCal %s : %d événement(s), %d séance(s)", nom, len(evenements), len(seances)
    )
    return seances, signalements


def _lire_ou_signaler(
    nom: str,
    ligne: int | None,
    ev: Any,
    remplacements: dict[tuple[str, dt.datetime], Any],
    periode: tuple[dt.date, dt.date] | None,
    signalements: list[Signalement],
) -> list[SeanceLue]:
    """Séances d'un événement ; un événement illisible devient un signalement d'erreur."""
    try:
        return _lire_evenement(nom, ligne, ev, remplacements, periode, signalements)
    except _Illisible as erreur:
        message = str(erreur)
    except InvalidCalendar as erreur:
        message = f"Événement illisible ({erreur}) : non importé."
    signalements.append(Signalement(Gravite.ERREUR, message, nom, "événements", ligne))
    return []


def _lire_evenement(
    nom: str,
    ligne: int | None,
    ev: Any,
    remplacements: dict[tuple[str, dt.datetime], Any],
    periode: tuple[dt.date, dt.date] | None,
    signalements: list[Signalement],
) -> list[SeanceLue]:
    debut, duree = _debut_et_duree(ev)
    champs = _champs(ev)
    uid = str(ev.get("UID", ""))
    regles = ev.get("RRULE")
    if regles is None and not ev.get("RDATE"):
        return [_seance(nom, ligne, debut, duree, champs, uid)]

    instances = _developper(ev, debut, periode)
    exclues = {_instant(x) for x in ev.exdates}
    seances = []
    for instance in instances:
        cle = _instant(instance)
        if cle in exclues or dt.datetime.combine(cle.date(), dt.time()) in exclues:
            continue
        remplacement = remplacements.pop((uid, cle), None)
        if remplacement is not None:
            seances.append(_remplacement(nom, ligne, remplacement, champs, cle, uid))
            continue
        seances.append(
            _seance(
                nom,
                ligne,
                instance,
                duree,
                champs,
                uid,
                recurrente=True,
                precision=f"occurrence du {cle:%d/%m/%Y}",
            )
        )
    signalements.append(
        Signalement(
            Gravite.INFORMATION,
            f"Règle de récurrence développée : {len(seances)} séance(s)"
            + (
                f" sur la période du {periode[0]:%d/%m/%Y} au {periode[1]:%d/%m/%Y}."
                if periode
                else "."
            ),
            nom,
            "événements",
            ligne,
        )
    )
    return seances


def _debut_et_duree(ev: Any) -> tuple[dt.datetime, dt.timedelta]:
    debut = ev.get("DTSTART")
    if debut is None:
        raise _Illisible("Événement sans date de début (DTSTART) : ignoré.")
    debut = debut.dt
    if not isinstance(debut, dt.datetime):
        raise _Illisible(
            f"Événement sur la journée entière du {debut:%d/%m/%Y} : ce n'est pas une séance, "
            "il n'est pas importé."
        )
    if ev.get("DTEND") is not None:
        fin = ev.get("DTEND").dt
        if not isinstance(fin, dt.datetime):
            raise _Illisible("Fin d'événement sans heure (DTEND) : ce n'est pas une séance.")
    elif ev.get("DURATION") is not None:
        fin = debut + ev.get("DURATION").dt
    else:
        raise _Illisible("Événement sans heure de fin (DTEND ou DURATION) : ignoré.")
    try:
        duree = fin - debut
    except TypeError:
        raise _Illisible(
            "Début et fin dans des fuseaux incompatibles (l'un sans fuseau)."
        ) from None
    if duree <= dt.timedelta(0):
        raise _Illisible("La fin de l'événement ne suit pas son début.")
    return debut, duree


def _champs(ev: Any) -> dict[str, Any]:
    def texte(propriete: str) -> str | None:
        valeur = ev.get(propriete)
        return None if valeur is None else texte_cellule(str(valeur))

    statut = None
    statut_edt = texte(PROPRIETES_EDT["statut"])
    if statut_edt is not None:
        try:
            statut = lire_choix(statut_edt, StatutSeance)
        except ErreurDonneesInvalides as erreur:
            raise _Illisible(f"Statut illisible : {erreur.message}") from None
    elif (texte("STATUS") or "").upper() == "CANCELLED":
        statut = StatutSeance.ANNULEE
    return {
        "matiere": texte(PROPRIETES_EDT["matiere"]),
        "type": texte(PROPRIETES_EDT["type"]) or _premiere_categorie(ev),
        "groupes": lire_liste(texte(PROPRIETES_EDT["groupes"])) or (),
        "enseignants": lire_liste(texte(PROPRIETES_EDT["enseignants"])) or (),
        "salle": texte(PROPRIETES_EDT["salle"]) or texte("LOCATION"),
        "statut": statut,
        "libelle": (
            texte(PROPRIETES_EDT["libelle"])
            if PROPRIETES_EDT["libelle"] in ev
            else texte("SUMMARY")
        ),
    }


def _premiere_categorie(ev: Any) -> str | None:
    categories = ev.get("CATEGORIES")
    if categories is None:
        return None
    for groupe in categories if isinstance(categories, list) else [categories]:
        for categorie in getattr(groupe, "cats", []):
            texte = texte_cellule(str(categorie))
            if texte:
                return texte
    return None


def _seance(
    nom: str,
    ligne: int | None,
    debut: dt.datetime,
    duree: dt.timedelta,
    champs: dict[str, Any],
    uid: str,
    *,
    recurrente: bool = False,
    precision: str = "",
) -> SeanceLue:
    local_debut = vers_paris(debut)
    local_fin = vers_paris(debut + duree)
    if local_debut.second or local_fin.second:
        raise _Illisible(f"Heure avec des secondes ({local_debut:%H:%M:%S}) : non importée.")
    minutes_fin = local_fin.hour * 60 + local_fin.minute
    if local_fin.date() != local_debut.date():
        if local_fin.date() == local_debut.date() + dt.timedelta(days=1) and minutes_fin == 0:
            minutes_fin = 24 * 60
        else:
            raise _Illisible(
                f"Événement sur plusieurs jours (du {local_debut:%d/%m/%Y} au "
                f"{local_fin:%d/%m/%Y}) : ce n'est pas une séance, il n'est pas importé."
            )
    return SeanceLue(
        fichier=nom,
        ligne=ligne,
        date=local_debut.date(),
        jour=None,
        debut=local_debut.hour * 60 + local_debut.minute,
        fin=minutes_fin,
        recurrente=recurrente,
        precision=precision,
        extra={"uid": uid} if uid else {},
        **champs,
    )


def _remplacement(
    nom: str,
    ligne: int | None,
    ev: Any,
    champs_maitre: dict[str, Any],
    origine: dt.datetime,
    uid: str,
) -> SeanceLue:
    """Occurrence remplacée (RECURRENCE-ID) : elle compte à sa position finale."""
    debut, duree = _debut_et_duree(ev)
    propres = _champs(ev)
    champs = {cle: propres[cle] or champs_maitre[cle] for cle in propres if cle != "statut"}
    champs["statut"] = propres["statut"]
    if champs["statut"] is None and vers_paris(debut) != origine:
        champs["statut"] = StatutSeance.DEPLACEE
    return _seance(
        nom,
        ligne,
        debut,
        duree,
        champs,
        uid,
        precision=f"occurrence du {origine:%d/%m/%Y} remplacée",
    )


def _instant(valeur: dt.date | dt.datetime) -> dt.datetime:
    """Instant comparable (heure de Paris sans fuseau) d'une date ou d'une date-heure iCal."""
    if isinstance(valeur, dt.datetime):
        return vers_paris(valeur)
    return dt.datetime.combine(valeur, dt.time())


def _dans_fuseau(valeur: dt.date | dt.datetime, fuseau: dt.tzinfo | None) -> dt.datetime:
    """Date-heure sans fuseau, exprimée dans le fuseau de l'événement."""
    if not isinstance(valeur, dt.datetime):
        return dt.datetime.combine(valeur, dt.time())
    if valeur.tzinfo is None:
        return valeur
    return valeur.astimezone(fuseau or PARIS).replace(tzinfo=None)


def _developper(
    ev: Any,
    debut: dt.datetime,
    periode: tuple[dt.date, dt.date] | None,
) -> list[dt.datetime]:
    """Occurrences d'un événement récurrent, dans le fuseau de son début.

    La récurrence se calcule en heure locale du fuseau de l'événement (comme
    le veut la norme), puis chaque occurrence reprend ce fuseau.
    """
    fuseau = debut.tzinfo
    naif = debut.replace(tzinfo=None)
    ensemble = rruleset()
    ensemble.rdate(naif)  # le début compte toujours comme première occurrence
    regles = ev.get("RRULE")
    for regle in regles if isinstance(regles, list) else ([regles] if regles is not None else []):
        valeurs = {cle: valeur for cle, valeur in regle.items() if cle != "UNTIL"}
        try:
            recurrence = rrulestr(vRecur(valeurs).to_ical().decode(), dtstart=naif)
        except (ValueError, TypeError) as erreur:
            raise _Illisible(f"Règle de récurrence illisible ({erreur}).") from None
        if "UNTIL" in regle:
            limite = regle["UNTIL"][0]
            if isinstance(limite, dt.datetime):
                limite = _dans_fuseau(limite, fuseau)
            else:
                limite = dt.datetime.combine(limite, dt.time(23, 59, 59))
            recurrence = recurrence.replace(until=limite)
        ensemble.rrule(recurrence)
    for date_supplementaire, _ in ev.rdates:
        ensemble.rdate(_dans_fuseau(date_supplementaire, fuseau))

    bas = haut = None
    if periode is not None:
        bas = dt.datetime.combine(periode[0] - dt.timedelta(days=1), dt.time())
        haut = dt.datetime.combine(periode[1] + dt.timedelta(days=2), dt.time())
    instances = []
    for pas, instance in enumerate(ensemble, start=1):  # calcul paresseux, borné
        if haut is not None and instance > haut:
            break
        if bas is None or instance >= bas:
            instances.append(instance)
        if len(instances) > MAX_OCCURRENCES or pas > MAX_ITERATIONS:
            raise _Illisible(
                f"Règle de récurrence refusée : trop d'occurrences (plus de {MAX_OCCURRENCES}, "
                "ou une règle sans fin et sans période) ; ce n'est pas un emploi du temps de cours."
            )
    occurrences = [instance.replace(tzinfo=fuseau) for instance in instances]
    if periode is not None:
        occurrences = [o for o in occurrences if periode[0] <= vers_paris(o).date() <= periode[1]]
    return occurrences


# --------------------------------------------------------------------------- #
# Export : fuseau horaire déclaré dans le fichier
# --------------------------------------------------------------------------- #


def fuseau_paris() -> Timezone:
    """Composant VTIMEZONE compact d'Europe/Paris (règles en vigueur depuis 1996)."""
    fuseau = Timezone()
    fuseau.add("tzid", NOM_FUSEAU)
    ete = TimezoneDaylight()
    ete.add("tzname", "CEST")
    ete.add("tzoffsetfrom", dt.timedelta(hours=1))
    ete.add("tzoffsetto", dt.timedelta(hours=2))
    ete.add("dtstart", dt.datetime(1970, 3, 29, 2, 0))
    ete.add("rrule", {"freq": "yearly", "bymonth": 3, "byday": "-1su"})
    hiver = TimezoneStandard()
    hiver.add("tzname", "CET")
    hiver.add("tzoffsetfrom", dt.timedelta(hours=2))
    hiver.add("tzoffsetto", dt.timedelta(hours=1))
    hiver.add("dtstart", dt.datetime(1970, 10, 25, 3, 0))
    hiver.add("rrule", {"freq": "yearly", "bymonth": 10, "byday": "-1su"})
    fuseau.add_component(ete)
    fuseau.add_component(hiver)
    return fuseau
