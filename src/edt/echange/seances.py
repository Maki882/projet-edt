"""Tableau intermédiaire des séances d'un emploi du temps existant, et lecteur du tableau plat.

Tout lecteur d'emploi du temps (le tableau plat CSV/XLSX du format d'échange,
le fichier iCal, tout adaptateur pour un autre logiciel) produit la même
chose : une liste de `SeanceLue`. La reconstitution des besoins et de la
semaine type (`edt.echange.reconstitution`) ne lit rien d'autre.

Une `SeanceLue` garde les références **telles qu'écrites** (codes de
matière, de groupes, identifiants d'enseignants, code de salle) : leur
résolution, avec ses règles (enseignant inconnu,
salle à définir, groupe inconnu...), appartient à la reconstitution.

Écrire un adaptateur pour un autre logiciel : une fonction pure
`lire_<logiciel>(nom, contenu) -> tuple[list[SeanceLue], list[Signalement]]`
qui convertit chaque séance du fichier en `SeanceLue` (heure locale
Europe/Paris, sans fuseau) et chaque élément illisible en signalement
d'erreur ; puis l'appeler dans `edt.services.echange` selon la nature du
fichier. Voir `docs/import-export.md`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from edt.domaine.enumerations import JOURS, EnumLibelle, Gravite
from edt.domaine.heures import MINUTES_PAR_JOUR, minutes_vers_hhmm
from edt.echange.format import FEUILLE_SEANCES, StatutSeance
from edt.echange.lecture import Tableau, preparer_tableau
from edt.echange.rapport import Bilan, Signalement
from edt.echange.valeurs import cle_code


class ModeImport(EnumLibelle):
    """Ce qu'un import fait entrer : des données, ou un emploi du temps existant (trois modes)."""

    DONNEES = "donnees", "Données de l'établissement"
    REFERENCE = "reference", "Emploi du temps de référence (pour comparaison)"
    VERROUS = "verrous", "Emploi du temps en séances verrouillées"
    A_REPLACER = "a_replacer", "Séances à replacer par le générateur"

    @property
    def emploi_du_temps(self) -> bool:
        """Vrai pour les trois modes d'import d'un emploi du temps existant."""
        return self is not ModeImport.DONNEES


@dataclass(frozen=True)
class SeanceLue:
    """Une séance lue dans un fichier, avant toute résolution.

    - `date` : date de la séance ; `None` pour une séance hebdomadaire (`jour`) ;
    - `debut`, `fin` : minutes depuis minuit, heure locale Europe/Paris ;
    - `recurrente` : issue d'une règle de récurrence (iCal) : une occurrence
      qui tombe un jour fermé du calendrier est retirée ;
    - `precision` : complément de position pour le rapport (« occurrence du 12/10/2026 »).
    """

    fichier: str
    ligne: int | None
    date: dt.date | None
    jour: int | None
    debut: int
    fin: int
    matiere: str | None
    type: str | None
    groupes: tuple[str, ...]
    enseignants: tuple[str, ...]
    salle: str | None
    statut: StatutSeance | None = None
    libelle: str | None = None
    recurrente: bool = False
    precision: str = ""
    extra: dict[str, str] = field(default_factory=dict, compare=False, hash=False)

    @property
    def duree(self) -> int:
        """Durée en minutes."""
        return self.fin - self.debut

    def cle_doublon(self) -> tuple:
        """Clé de deux lignes strictement identiques (codes comparés sans casse)."""
        return (
            self.date,
            self.jour,
            self.debut,
            self.fin,
            cle_code(self.matiere or ""),
            cle_code(self.type or ""),
            frozenset(cle_code(g) for g in self.groupes),
            frozenset(cle_code(e) for e in self.enseignants),
            cle_code(self.salle or ""),
            self.statut,
            cle_code(self.libelle or ""),
        )

    def signalement(self, gravite: Gravite, message: str, colonne: str = "") -> Signalement:
        """Signalement positionné sur cette séance."""
        if self.precision:
            message = f"{message} ({self.precision})"
        return Signalement(gravite, message, self.fichier, "séances", self.ligne, colonne)

    @property
    def description(self) -> str:
        """« IM.1101 CM A1 le 05/10/2026 08:30-10:30 », pour les messages."""
        quand = f"le {self.date:%d/%m/%Y}" if self.date else f"chaque {_jour(self.jour)}"
        groupes = ", ".join(self.groupes) or "sans groupe"
        return (
            f"{self.matiere or '?'} {self.type or '?'} {groupes} {quand} "
            f"{minutes_vers_hhmm(self.debut)}-{minutes_vers_hhmm(self.fin)}"
        )


def _jour(numero: int | None) -> str:
    return "?" if numero is None else JOURS[numero]


def lire_tableau_seances(
    tableau: Tableau, bilan: Bilan, signalements: list[Signalement]
) -> list[SeanceLue]:
    """Lit un tableau plat de séances (format `FEUILLE_SEANCES`).

    Une ligne illisible (heure mal formée, ni date ni jour, fin avant le
    début, jour contraire à la date) est rejetée et signalée.
    """
    seances = []
    for ligne in preparer_tableau(tableau, FEUILLE_SEANCES, bilan, signalements):
        erreur = None
        if ligne["date"] is None and ligne["jour"] is None:
            erreur = (
                "date",
                "Renseigner la date de la séance (ou son jour, pour une séance hebdomadaire).",
            )
        elif (
            ligne["date"] is not None
            and ligne["jour"] is not None
            and ligne["date"].weekday() != ligne["jour"]
        ):
            erreur = (
                "jour",
                f"Le jour ({_jour(ligne['jour'])}) ne correspond pas à la date du {ligne['date']:%d/%m/%Y}.",
            )
        elif not 0 <= ligne["debut"] < ligne["fin"] <= MINUTES_PAR_JOUR:
            erreur = ("fin", "L'heure de fin doit suivre l'heure de début.")
        if erreur is not None:
            bilan.ajouter("rejetes")
            signalements.append(
                Signalement(
                    Gravite.ERREUR, erreur[1], ligne.fichier, ligne.feuille, ligne.numero, erreur[0]
                )
            )
            continue
        seances.append(
            SeanceLue(
                fichier=ligne.fichier,
                ligne=ligne.numero,
                date=ligne["date"],
                jour=None if ligne["date"] is not None else ligne["jour"],
                debut=ligne["debut"],
                fin=ligne["fin"],
                matiere=ligne["matiere"],
                type=ligne["type"],
                groupes=ligne["groupes"] or (),
                enseignants=ligne["enseignants"] or (),
                salle=ligne["salle"],
                statut=ligne["statut"],
                libelle=ligne["libelle"],
                extra=dict(ligne.extra),
            )
        )
    return seances
