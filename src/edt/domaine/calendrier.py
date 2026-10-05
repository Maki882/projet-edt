"""Calendrier : semaines d'un semestre et jours fériés français.

Aucune dépendance externe : la date de Pâques est calculée (algorithme de
Butcher-Meeus), les fériés mobiles (lundi de Pâques, Ascension, lundi de
Pentecôte) en découlent.

Une **semaine** va du lundi au dimanche. Les semaines d'un semestre sont
numérotées à partir de 1 : la semaine 1 est celle qui contient le premier
jour du semestre. Ce numéro relatif est celui qu'utilisent les « semaines
actives » des séances.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from edt.erreurs import ErreurDonneesInvalides


@dataclass(frozen=True)
class Semaine:
    """Une semaine d'un semestre : son numéro (à partir de 1) et son lundi."""

    numero: int
    lundi: date

    @property
    def dimanche(self) -> date:
        """Dernier jour de la semaine."""
        return self.lundi + timedelta(days=6)

    def date_du(self, jour: int) -> date:
        """Date du jour `jour` (0 = lundi ... 6 = dimanche) dans cette semaine."""
        if not 0 <= jour <= 6:
            raise ErreurDonneesInvalides(f"Jour invalide : {jour!r} (attendu : 0 à 6).")
        return self.lundi + timedelta(days=jour)

    def contient(self, jour: date) -> bool:
        """Vrai si la date appartient à cette semaine."""
        return self.lundi <= jour <= self.dimanche


def lundi_de(jour: date) -> date:
    """Lundi de la semaine qui contient `jour`."""
    return jour - timedelta(days=jour.weekday())


def semaines_entre(debut: date, fin: date) -> tuple[Semaine, ...]:
    """Semaines (lundi-dimanche) qui recoupent la période `[debut, fin]`, numérotées dès 1."""
    if fin < debut:
        raise ErreurDonneesInvalides(
            f"Période invalide : la fin ({fin.isoformat()}) précède le début ({debut.isoformat()})."
        )
    semaines = []
    lundi = lundi_de(debut)
    numero = 1
    while lundi <= fin:
        semaines.append(Semaine(numero=numero, lundi=lundi))
        lundi += timedelta(days=7)
        numero += 1
    return tuple(semaines)


def semaine_de(jour: date, semaines: tuple[Semaine, ...] | list[Semaine]) -> Semaine | None:
    """Semaine de la liste qui contient `jour`, ou `None` si la date est hors période."""
    for semaine in semaines:
        if semaine.contient(jour):
            return semaine
    return None


def paques(annee: int) -> date:
    """Dimanche de Pâques de l'année grégorienne `annee` (algorithme de Butcher-Meeus)."""
    a = annee % 19
    b, c = divmod(annee, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    mois, jour = divmod(h + m - 7 * n + 114, 31)
    return date(annee, mois, jour + 1)


def jours_feries(annee: int) -> dict[date, str]:
    """Les onze jours fériés légaux de France métropolitaine pour l'année civile `annee`."""
    dimanche_de_paques = paques(annee)
    return {
        date(annee, 1, 1): "Jour de l'An",
        dimanche_de_paques + timedelta(days=1): "Lundi de Pâques",
        date(annee, 5, 1): "Fête du Travail",
        date(annee, 5, 8): "Victoire de 1945",
        dimanche_de_paques + timedelta(days=39): "Ascension",
        dimanche_de_paques + timedelta(days=50): "Lundi de Pentecôte",
        date(annee, 7, 14): "Fête nationale",
        date(annee, 8, 15): "Assomption",
        date(annee, 11, 1): "Toussaint",
        date(annee, 11, 11): "Armistice de 1918",
        date(annee, 12, 25): "Noël",
    }


def jours_feries_entre(debut: date, fin: date) -> list[tuple[date, str]]:
    """Jours fériés compris dans `[debut, fin]`, triés par date."""
    if fin < debut:
        raise ErreurDonneesInvalides(
            f"Période invalide : la fin ({fin.isoformat()}) précède le début ({debut.isoformat()})."
        )
    feries: list[tuple[date, str]] = []
    for annee in range(debut.year, fin.year + 1):
        feries.extend(
            (jour, libelle) for jour, libelle in jours_feries(annee).items() if debut <= jour <= fin
        )
    return sorted(feries)


def premier_lundi(annee: int, mois: int) -> date:
    """Premier lundi du mois donné."""
    premier = date(annee, mois, 1)
    return premier + timedelta(days=(7 - premier.weekday()) % 7)


def dernier_vendredi(annee: int, mois: int) -> date:
    """Dernier vendredi du mois donné."""
    suivant = date(annee + 1, 1, 1) if mois == 12 else date(annee, mois + 1, 1)
    dernier = suivant - timedelta(days=1)
    return dernier - timedelta(days=(dernier.weekday() - 4) % 7)


@dataclass(frozen=True)
class DecoupageAnnee:
    """Dates par défaut d'une année scolaire et de ses deux semestres."""

    libelle: str
    debut: date
    fin: date
    semestre_1: tuple[date, date]
    semestre_2: tuple[date, date]


def decoupage_par_defaut(annee_debut: int) -> DecoupageAnnee:
    """Dates proposées par défaut pour l'année scolaire qui commence en `annee_debut`.

    Règle (modifiable ensuite dans l'interface) : l'année va du 1er septembre
    au 31 août ; le semestre 1 du premier lundi de septembre au dernier
    vendredi de janvier ; le semestre 2 du premier lundi de février au
    dernier vendredi de juin.
    """
    return DecoupageAnnee(
        libelle=f"{annee_debut}-{annee_debut + 1}",
        debut=date(annee_debut, 9, 1),
        fin=date(annee_debut + 1, 8, 31),
        semestre_1=(premier_lundi(annee_debut, 9), dernier_vendredi(annee_debut + 1, 1)),
        semestre_2=(premier_lundi(annee_debut + 1, 2), dernier_vendredi(annee_debut + 1, 6)),
    )


def annee_scolaire_de(jour: date) -> int:
    """Année de début de l'année scolaire qui contient `jour` (bascule au 1er août)."""
    return jour.year if jour.month >= 8 else jour.year - 1
