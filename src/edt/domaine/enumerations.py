"""Énumérations partagées par la base, les services, le solveur et l'interface.

Toutes héritent de `StrEnum` : la valeur stockée en base et échangée en JSON
est la chaîne en minuscules, stable. Chaque membre est déclaré avec sa valeur
et son libellé français affichable (`membre.libelle`).

Les jours de la semaine sont des entiers de 0 (lundi) à 6 (dimanche), comme
`datetime.date.weekday()`.
"""

from __future__ import annotations

from enum import StrEnum

JOURS: tuple[str, ...] = (
    "lundi",
    "mardi",
    "mercredi",
    "jeudi",
    "vendredi",
    "samedi",
    "dimanche",
)
"""Noms des jours, indexés par le numéro du jour (0 = lundi)."""

SAMEDI = 5
DIMANCHE = 6


class EnumLibelle(StrEnum):
    """Base des énumérations : chaque membre vaut `(valeur, libellé)`."""

    _libelle: str

    def __new__(cls, valeur: str, libelle: str) -> EnumLibelle:
        membre = str.__new__(cls, valeur)
        membre._value_ = valeur
        membre._libelle = libelle
        return membre

    @property
    def libelle(self) -> str:
        """Libellé français affichable."""
        return self._libelle


class StatutEnseignant(EnumLibelle):
    """Statut administratif d'un enseignant."""

    PERMANENT = "permanent", "Permanent"
    VACATAIRE = "vacataire", "Vacataire"
    EXTERIEUR = "exterieur", "Extérieur"


class Accessibilite(EnumLibelle):
    """Accessibilité d'une salle aux personnes à mobilité réduite."""

    OUI = "oui", "Oui"
    NON = "non", "Non"
    INCONNU = "inconnu", "Inconnu"


class NatureGroupe(EnumLibelle):
    """Nature d'un groupe d'étudiants."""

    PROMOTION = "promotion", "Promotion"
    CLASSE = "classe", "Classe"
    DEMI_GROUPE = "demi_groupe", "Demi-groupe"
    TRANSVERSE = "transverse", "Groupe transverse"
    TIERS_TEMPS = "tiers_temps", "Tiers-temps"


class DemiJournee(EnumLibelle):
    """Demi-journée d'un créneau de la grille."""

    MATIN = "matin", "Matin"
    APRES_MIDI = "apres_midi", "Après-midi"


class CibleIndisponibilite(EnumLibelle):
    """Type de ressource visé par une indisponibilité."""

    ENSEIGNANT = "enseignant", "Enseignant"
    SALLE = "salle", "Salle"
    GROUPE = "groupe", "Groupe"


class NatureJourFerme(EnumLibelle):
    """Nature d'une fermeture du calendrier."""

    FERIE = "ferie", "Jour férié"
    VACANCES = "vacances", "Vacances"
    EXAMENS = "examens", "Examens"
    SEMAINE_SPECIALE = "semaine_speciale", "Semaine spéciale"
    AUTRE = "autre", "Autre fermeture"

    @property
    def admet_ponctuels(self) -> bool:
        """Vrai si les séances ponctuelles restent permises pendant la fermeture.

        Une période d'examens ou une semaine spéciale suspend les cours
        réguliers mais reçoit un planning saisi à la main (besoins ponctuels).
        Un jour férié, des vacances ou une autre fermeture ferment tout.
        """
        return self in (NatureJourFerme.EXAMENS, NatureJourFerme.SEMAINE_SPECIALE)


class ModeVolume(EnumLibelle):
    """Façon d'exprimer le volume d'un besoin."""

    PAR_SEMAINE = "par_semaine", "Par semaine"
    TOTAL = "total", "Volume total"
    PONCTUEL = "ponctuel", "Ponctuel"


class OrigineVersion(EnumLibelle):
    """Origine d'une version d'emploi du temps."""

    GENEREE = "generee", "Générée"
    REFERENCE = "reference", "Référence"


class StatutOccurrence(EnumLibelle):
    """Statut d'une séance datée.

    - `PLANIFIEE` : a lieu à la date et à l'heure indiquées ;
    - `DEPLACEE` : a lieu, mais ailleurs que ne le prévoyait la semaine type
      (`date_initiale` garde la date d'origine) ;
    - `ANNULEE` : n'a pas lieu et n'est pas comptée dans le volume ;
    - `A_PROGRAMMER` : aucun placement valide trouvé, reste à programmer.
    """

    PLANIFIEE = "planifiee", "Planifiée"
    ANNULEE = "annulee", "Annulée"
    DEPLACEE = "deplacee", "Déplacée"
    A_PROGRAMMER = "a_programmer", "À programmer"

    @property
    def occupe_les_ressources(self) -> bool:
        """Vrai si la séance a réellement lieu (elle occupe salle, enseignants, groupes)."""
        return self in (StatutOccurrence.PLANIFIEE, StatutOccurrence.DEPLACEE)


class OrigineOccurrence(EnumLibelle):
    """Façon dont une séance datée a été créée."""

    INSTANCIATION = "instanciation", "Semaine type"
    REPLACEMENT = "replacement", "Replacement"
    MANUELLE = "manuelle", "Saisie manuelle"
    IMPORT = "import", "Import"


class EtatCalcul(EnumLibelle):
    """État d'un calcul (génération d'un emploi du temps)."""

    EN_COURS = "en_cours", "En cours"
    REUSSI = "reussi", "Réussi"
    INFAISABLE = "infaisable", "Impossible (prouvé)"
    TEMPS_DEPASSE = "temps_depasse", "Pas trouvé dans le temps imparti"
    ARRETE = "arrete", "Arrêté"
    ERREUR = "erreur", "Erreur"

    @property
    def est_termine(self) -> bool:
        """Vrai pour tout état autre que « en cours »."""
        return self is not EtatCalcul.EN_COURS


class Gravite(EnumLibelle):
    """Gravité d'une anomalie relevée par le vérificateur."""

    ERREUR = "erreur", "Erreur"
    AVERTISSEMENT = "avertissement", "Avertissement"
    INFORMATION = "information", "Information"
