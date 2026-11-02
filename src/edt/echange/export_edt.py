"""Export d'une version d'emploi du temps : CSV et iCal, pour tout l'établissement ou une ressource.

Deux couches :

- `hebdo` : la semaine type, une ligne (ou un événement récurrent) par séance
  hebdomadaire, avec ses semaines actives ;
- `date` : les séances datées (occurrences) enregistrées pour la version.

Filtres (au plus un) : un groupe (ses séances, celles des groupes qui le
contiennent et celles de ses sous-groupes), un enseignant, une salle.

CSV : UTF-8 avec BOM, séparateur point-virgule ; les colonnes `date`,
`jour`, `debut`, `fin`, `matiere`, `type`, `groupes`, `enseignants`,
`salle`, `statut` et `libelle` sont celles du tableau plat d'import : un
export se réimporte comme emploi du temps existant.

iCal : fuseau Europe/Paris déclaré (VTIMEZONE), identifiants d'événement
stables (`seance-<besoin>-<rang>@edt` pour la semaine type,
`occurrence-<id>@edt` pour une séance datée), lieu, description lisible et
propriétés `X-EDT-*` relues par notre import. En couche `hebdo`, chaque
séance est un événement récurrent hebdomadaire ; les semaines inactives et
les jours fermés en sont exclus (EXDATE).
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from edt.db import modeles as m
from edt.domaine.enumerations import JOURS, EnumLibelle, StatutOccurrence
from edt.domaine.heures import minutes_vers_hhmm
from edt.echange.donnees import obtenir_par_id, references_salles
from edt.echange.ecriture import csv_octets
from edt.echange.ical import NOM_FUSEAU, PROPRIETES_EDT, fuseau_paris
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable
from edt.services.instantane import charger_instantane

journal = logging.getLogger(__name__)

PARIS = ZoneInfo(NOM_FUSEAU)
DOMAINE_UID = "edt"


class Couche(EnumLibelle):
    """Couche exportée d'une version."""

    HEBDO = "hebdo", "Semaine type"
    DATE = "date", "Séances datées"


COLONNES_HEBDO = (
    "jour",
    "debut",
    "fin",
    "matiere",
    "matiere_libelle",
    "type",
    "libelle",
    "groupes",
    "enseignants",
    "enseignants_noms",
    "salle",
    "campus",
    "semaines",
    "verrou",
    "besoin",
)
COLONNES_DATE = (
    "date",
    "jour",
    "debut",
    "fin",
    "matiere",
    "matiere_libelle",
    "type",
    "libelle",
    "groupes",
    "enseignants",
    "enseignants_noms",
    "salle",
    "campus",
    "statut",
    "besoin",
)

STATUTS_ICAL = {
    StatutOccurrence.PLANIFIEE: "CONFIRMED",
    StatutOccurrence.DEPLACEE: "CONFIRMED",
    StatutOccurrence.ANNULEE: "CANCELLED",
    StatutOccurrence.A_PROGRAMMER: "TENTATIVE",
}


@dataclass(frozen=True)
class Filtre:
    """Restriction de l'export à une ressource (au plus un identifiant renseigné)."""

    groupe_id: int | None = None
    enseignant_id: int | None = None
    salle_id: int | None = None

    def __post_init__(self) -> None:
        renseignes = [
            x for x in (self.groupe_id, self.enseignant_id, self.salle_id) if x is not None
        ]
        if len(renseignes) > 1:
            raise ErreurDonneesInvalides(
                "Choisir au plus un filtre : un groupe, un enseignant ou une salle."
            )


@dataclass(frozen=True)
class Element:
    """Une séance à exporter (séance hebdomadaire ou occurrence datée)."""

    besoin: m.Besoin
    rang: int | None
    jour: int
    debut: int
    fin: int
    salle: m.Salle | None
    date: dt.date | None = None
    statut: StatutOccurrence | None = None
    verrou: bool = False
    semaines: tuple[int, ...] = ()
    occurrence_id: int | None = None


@dataclass(frozen=True)
class Export:
    """Contenu exporté et la description de ce qui a été retenu (pour le nom du fichier)."""

    version: m.VersionEdt
    couche: Couche
    elements: tuple[Element, ...]
    suffixe: str
    references_salles: dict[int, str]


def charger(session: Session, version_id: int, couche: Couche, filtre: Filtre) -> Export:
    """Séances de la version, filtrées, triées par date (ou jour) et heure."""
    version = obtenir_par_id(session, m.VersionEdt, version_id)
    if version is None:
        raise ErreurIntrouvable(f"La version d'emploi du temps {version_id} n'existe pas.")
    options_besoin = (
        selectinload(m.Besoin.matiere),
        selectinload(m.Besoin.type_seance),
        selectinload(m.Besoin.groupes),
        selectinload(m.Besoin.enseignants),
    )
    if couche is Couche.HEBDO:
        lignes = session.scalars(
            select(m.SeanceHebdo)
            .where(m.SeanceHebdo.version_id == version_id)
            .options(selectinload(m.SeanceHebdo.besoin).options(*options_besoin))
        )
        elements = [
            Element(
                besoin=s.besoin,
                rang=s.rang,
                jour=s.jour,
                debut=s.debut_min,
                fin=s.fin_min,
                salle=s.salle,
                verrou=s.verrou,
                semaines=tuple(sorted(s.semaines)),
            )
            for s in lignes
        ]
    else:
        lignes = session.scalars(
            select(m.Occurrence)
            .where(m.Occurrence.version_id == version_id)
            .options(
                selectinload(m.Occurrence.besoin).options(*options_besoin),
                selectinload(m.Occurrence.seance_hebdo),
            )
        )
        elements = [
            Element(
                besoin=o.besoin,
                rang=o.seance_hebdo.rang if o.seance_hebdo else None,
                jour=o.date.weekday(),
                debut=o.debut_min,
                fin=o.fin_min,
                salle=o.salle,
                date=o.date,
                statut=StatutOccurrence(o.statut),
                occurrence_id=o.id,
            )
            for o in lignes
        ]
    retenus, suffixe = _filtrer(session, elements, filtre)
    retenus.sort(key=lambda e: (e.date or dt.date.min, e.jour, e.debut, e.besoin.id, e.rang or 0))
    salles = list(session.scalars(select(m.Salle).options(selectinload(m.Salle.campus))))
    return Export(version, couche, tuple(retenus), suffixe, references_salles(salles))


def _filtrer(
    session: Session, elements: list[Element], filtre: Filtre
) -> tuple[list[Element], str]:
    if filtre.groupe_id is not None:
        groupe = obtenir_par_id(session, m.Groupe, filtre.groupe_id)
        if groupe is None:
            raise ErreurIntrouvable(f"Le groupe {filtre.groupe_id} n'existe pas.")
        lies = _groupes_lies(session, groupe)
        return [
            e for e in elements if any(g.id in lies for g in e.besoin.groupes)
        ], f"groupe-{groupe.code}"
    if filtre.enseignant_id is not None:
        enseignant = obtenir_par_id(session, m.Enseignant, filtre.enseignant_id)
        if enseignant is None:
            raise ErreurIntrouvable(f"L'enseignant {filtre.enseignant_id} n'existe pas.")
        return (
            [e for e in elements if any(x.id == enseignant.id for x in e.besoin.enseignants)],
            f"enseignant-{enseignant.identifiant}",
        )
    if filtre.salle_id is not None:
        salle = obtenir_par_id(session, m.Salle, filtre.salle_id)
        if salle is None:
            raise ErreurIntrouvable(f"La salle {filtre.salle_id} n'existe pas.")
        return [
            e for e in elements if e.salle is not None and e.salle.id == salle.id
        ], f"salle-{salle.code}"
    return list(elements), ""


def _groupes_lies(session: Session, groupe: m.Groupe) -> set[int]:
    """Le groupe, les groupes qui le contiennent et ses sous-groupes."""
    groupes = list(
        session.scalars(
            select(m.Groupe).where(m.Groupe.annee_scolaire_id == groupe.annee_scolaire_id)
        )
    )
    parents = {g.id: g.parent_id for g in groupes}
    lies = {groupe.id}
    courant = groupe.parent_id
    while courant is not None and courant not in lies:
        lies.add(courant)
        courant = parents.get(courant)
    enfants: dict[int, list[int]] = {}
    for g in groupes:
        if g.parent_id is not None:
            enfants.setdefault(g.parent_id, []).append(g.id)
    pile = [groupe.id]
    while pile:
        for enfant in enfants.get(pile.pop(), ()):
            if enfant not in lies:
                lies.add(enfant)
                pile.append(enfant)
    return lies


def nom_fichier(export: Export, extension: str) -> str:
    """« emploi-du-temps-v3-hebdo-groupe-A1-G1.csv » (caractères sûrs seulement)."""
    morceaux = ["emploi-du-temps", f"v{export.version.numero}", export.couche.value]
    if export.suffixe:
        morceaux.append(export.suffixe)
    nom = "-".join(morceaux)
    return re.sub(r"[^A-Za-z0-9._-]+", "_", nom) + f".{extension}"


def semaines_texte(semaines: Iterable[int]) -> str:
    """Semaines compactées : 1, 2, 3, 5, 6 donne « 1-3, 5-6 »."""
    plages: list[list[int]] = []
    for numero in sorted(set(semaines)):
        if plages and numero == plages[-1][1] + 1:
            plages[-1][1] = numero
        else:
            plages.append([numero, numero])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in plages)


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #


def en_csv(export: Export) -> bytes:
    """Une ligne par séance hebdomadaire ou par occurrence datée."""
    colonnes = COLONNES_HEBDO if export.couche is Couche.HEBDO else COLONNES_DATE
    lignes = []
    for e in export.elements:
        b = e.besoin
        valeurs = {
            "date": e.date.strftime("%d/%m/%Y") if e.date else "",
            "jour": JOURS[e.jour],
            "debut": minutes_vers_hhmm(e.debut),
            "fin": minutes_vers_hhmm(e.fin),
            "matiere": b.matiere.code,
            "matiere_libelle": b.matiere.libelle,
            "type": b.type_seance.code,
            "libelle": b.libelle or "",
            "groupes": "; ".join(sorted(g.code for g in b.groupes)),
            "enseignants": "; ".join(sorted(x.identifiant for x in b.enseignants)),
            "enseignants_noms": "; ".join(sorted(x.nom_affiche for x in b.enseignants)),
            "salle": export.references_salles.get(e.salle.id, e.salle.code) if e.salle else "",
            "campus": e.salle.campus.code if e.salle else "",
            "semaines": semaines_texte(e.semaines),
            "verrou": "oui" if e.verrou else "non",
            "statut": e.statut.value if e.statut else "",
            "besoin": b.id,
        }
        lignes.append([valeurs[colonne] for colonne in colonnes])
    return csv_octets(colonnes, lignes)


# --------------------------------------------------------------------------- #
# iCal
# --------------------------------------------------------------------------- #


def en_ical(session: Session, export: Export) -> bytes:
    """Calendrier iCal de l'export (fuseau Europe/Paris déclaré)."""
    calendrier = Calendar()
    calendrier.add("prodid", "-//EDT//Emplois du temps//FR")
    calendrier.add("version", "2.0")
    calendrier.add("calscale", "GREGORIAN")
    calendrier.add("method", "PUBLISH")
    calendrier.add("x-wr-calname", f"EDT - {export.version.libelle}")
    calendrier.add("x-wr-timezone", NOM_FUSEAU)
    calendrier.add_component(fuseau_paris())
    horodatage = export.version.cree_le.replace(tzinfo=PARIS).astimezone(dt.UTC)
    instantane = None
    if export.couche is Couche.HEBDO and export.elements:
        instantane = charger_instantane(session, export.version.semestre_id)
    for element in export.elements:
        evenement = _evenement(export, element, horodatage, instantane)
        if evenement is not None:
            calendrier.add_component(evenement)
    return calendrier.to_ical()


def _paris(jour: dt.date, minutes: int) -> dt.datetime:
    return dt.datetime.combine(jour, dt.time()).replace(tzinfo=PARIS) + dt.timedelta(
        minutes=minutes
    )


def _evenement(export: Export, e: Element, horodatage: dt.datetime, instantane) -> Event | None:
    b = e.besoin
    evenement = Event()
    if e.date is not None:
        evenement.add("uid", f"occurrence-{e.occurrence_id}@{DOMAINE_UID}")
        premiere = e.date
    else:
        connues = {s.numero for s in instantane.semaines}
        hors = sorted(set(e.semaines) - connues)
        if hors:
            # Les dates du semestre ont changé depuis la version : ces semaines n'ont plus de
            # date. L'export garde les autres et le journal le dit.
            journal.warning(
                "Export iCal de la version %d : séance %d-%s, semaine(s) %s hors du semestre "
                "actuel (%s), ignorée(s)",
                export.version.id,
                b.id,
                e.rang,
                ", ".join(map(str, hors)),
                instantane.libelle,
            )
        semaines = [instantane.semaine(n) for n in e.semaines if n in connues]
        if not semaines:
            return None
        dates = [s.date_du(e.jour) for s in semaines]
        besoin_domaine = instantane.besoin(b.id)
        exclues = [d for d in _toutes_les_semaines(dates[0], dates[-1]) if d not in dates]
        exclues += [d for d in dates if instantane.fermeture(d, besoin_domaine) is not None]
        premiere = dates[0]
        evenement.add("uid", f"seance-{b.id}-{e.rang}@{DOMAINE_UID}")
        fin_serie = _paris(dates[-1], e.debut).astimezone(dt.UTC)
        evenement.add("rrule", {"freq": "weekly", "until": fin_serie})
        if exclues:
            evenement.add("exdate", sorted(_paris(d, e.debut) for d in exclues))
    evenement.add("dtstamp", horodatage)
    evenement.add("dtstart", _paris(premiere, e.debut))
    evenement.add("dtend", _paris(premiere, e.fin))
    groupes = ", ".join(sorted(g.code for g in b.groupes))
    titre = f"{b.type_seance.code} {b.matiere.libelle}"
    if b.libelle:
        titre += f" - {b.libelle}"
    evenement.add("summary", f"{titre} ({groupes})")
    if e.salle is not None:
        evenement.add("location", f"{e.salle.code} - {e.salle.campus.nom}")
    evenement.add("description", _description(e, groupes))
    evenement.add("categories", [b.type_seance.libelle])
    evenement.add("status", STATUTS_ICAL[e.statut] if e.statut else "CONFIRMED")
    evenement.add(PROPRIETES_EDT["matiere"].lower(), b.matiere.code)
    evenement.add(PROPRIETES_EDT["type"].lower(), b.type_seance.code)
    evenement.add(PROPRIETES_EDT["groupes"].lower(), "; ".join(sorted(g.code for g in b.groupes)))
    if b.enseignants:
        evenement.add(
            PROPRIETES_EDT["enseignants"].lower(),
            "; ".join(sorted(x.identifiant for x in b.enseignants)),
        )
    if e.salle is not None:
        evenement.add(
            PROPRIETES_EDT["salle"].lower(), export.references_salles.get(e.salle.id, e.salle.code)
        )
    if e.statut is not None:
        evenement.add(PROPRIETES_EDT["statut"].lower(), e.statut.value)
    evenement.add(PROPRIETES_EDT["libelle"].lower(), b.libelle or "")
    return evenement


def _toutes_les_semaines(premiere: dt.date, derniere: dt.date) -> Sequence[dt.date]:
    jours = []
    courant = premiere
    while courant <= derniere:
        jours.append(courant)
        courant += dt.timedelta(days=7)
    return jours


def _description(e: Element, groupes: str) -> str:
    b = e.besoin
    lignes = [
        f"Matière : {b.matiere.libelle} ({b.matiere.code})",
        f"Type : {b.type_seance.libelle}",
        f"Groupes : {groupes}",
        "Enseignants : "
        + (", ".join(sorted(x.nom_affiche for x in b.enseignants)) or "non renseigné"),
        "Salle : " + (f"{e.salle.code} ({e.salle.campus.nom})" if e.salle else "à définir"),
    ]
    if e.statut is not None:
        lignes.append(f"Statut : {e.statut.libelle}")
    if e.semaines:
        lignes.append(f"Semaines : {semaines_texte(e.semaines)}")
    return "\n".join(lignes)
