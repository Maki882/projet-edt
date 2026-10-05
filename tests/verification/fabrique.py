"""Fabrique d'instantanés écrits à la main pour les tests du vérificateur (sans base de données).

Usage :

    f = Fabrique()
    n204 = f.salle("N204")
    martin = f.enseignant("Mme Martin")
    a1 = f.groupe("A1", effectif=60)
    g1 = f.groupe("A1-G1", parent=a1)
    maths = f.cours("Mathématiques TD", [g1], [martin])
    instantane = f.construire()
    anomalies = verifier(instantane, [placer(maths, MARDI, "10h45", n204)])

Par défaut : semestre de 15 semaines à partir du lundi 7 septembre 2026,
grille de l'ISEP (quatre créneaux du lundi au vendredi, deux le samedi
matin), deux campus (1 = Notre-Dame-des-Champs, 2 = Issy), paramètres par
défaut. Les conflits de groupes suivent l'arbre (un groupe est en conflit
avec ses ascendants) ; `conflit(a, b)` en déclare d'autres.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

from edt.domaine.calendrier import semaines_entre
from edt.domaine.enumerations import (
    Accessibilite,
    CibleIndisponibilite,
    DemiJournee,
    ModeVolume,
    NatureGroupe,
    NatureJourFerme,
    StatutEnseignant,
    StatutOccurrence,
)
from edt.domaine.heures import texte_vers_minutes
from edt.domaine.instantane import (
    Besoin,
    Campus,
    Creneau,
    Enseignant,
    Groupe,
    Indisponibilite,
    Instantane,
    JourFerme,
    Occurrence,
    Placement,
    ProblemeDepliage,
    Salle,
    SeanceAPlacer,
    Trajet,
    id_seance,
)
from edt.domaine.parametres import Parametres

LUNDI, MARDI, MERCREDI, JEUDI, VENDREDI, SAMEDI, DIMANCHE = range(7)
DEBUT_SEMESTRE = dt.date(2026, 9, 7)
NOTRE_DAME, ISSY = 1, 2


def h(texte: str | int) -> int:
    """Heure lisible vers minutes : `h("10h45")` vaut 645 (un entier est rendu tel quel)."""
    return texte if isinstance(texte, int) else texte_vers_minutes(texte)


def grille_isep() -> tuple[Creneau, ...]:
    """Grille par défaut de `docs/decisions.md` (section 4)."""
    creneaux = []
    for jour in range(5):
        creneaux += [
            Creneau(jour, h("8h30"), h("10h30"), DemiJournee.MATIN),
            Creneau(jour, h("10h45"), h("12h45"), DemiJournee.MATIN),
            Creneau(jour, h("13h45"), h("15h45"), DemiJournee.APRES_MIDI),
            Creneau(jour, h("16h00"), h("18h00"), DemiJournee.APRES_MIDI),
        ]
    creneaux += [
        Creneau(SAMEDI, h("8h30"), h("10h30"), DemiJournee.MATIN),
        Creneau(SAMEDI, h("10h45"), h("12h45"), DemiJournee.MATIN),
    ]
    return tuple(creneaux)


class Fabrique:
    """Construit un `Instantane` pas à pas ; chaque méthode renvoie l'objet créé."""

    def __init__(self, nb_semaines: int = 15, **parametres: int | bool) -> None:
        self.parametres = Parametres().avec(**parametres)
        fin = DEBUT_SEMESTRE + dt.timedelta(weeks=nb_semaines) - dt.timedelta(days=3)
        self.semaines = semaines_entre(DEBUT_SEMESTRE, fin)
        self.creneaux = grille_isep()
        self.campus = [
            Campus(NOTRE_DAME, "NDC", "Notre-Dame-des-Champs"),
            Campus(ISSY, "ISSY", "Issy"),
        ]
        self.trajets: list[Trajet] = []
        self.salles: list[Salle] = []
        self.enseignants: list[Enseignant] = []
        self.groupes: list[Groupe] = []
        self.conflits: set[tuple[int, int]] = set()
        self.indisponibilites: list[Indisponibilite] = []
        self.jours_fermes: list[JourFerme] = []
        self.besoins: list[Besoin] = []
        self.seances: list[SeanceAPlacer] = []
        self.problemes: list[ProblemeDepliage] = []

    # -- référentiels -----------------------------------------------------------

    def salle(
        self,
        code: str,
        campus: int = NOTRE_DAME,
        capacite: int = 40,
        type_salle: int = 1,
        equipements: Iterable[int] = (),
        accessibilite: Accessibilite = Accessibilite.OUI,
    ) -> int:
        salle = Salle(
            len(self.salles) + 1,
            code,
            campus,
            capacite,
            type_salle,
            frozenset(equipements),
            accessibilite,
        )
        self.salles.append(salle)
        return salle.id

    def enseignant(
        self,
        nom: str,
        fictif: bool = False,
        max_jour: int | None = None,
        max_semaine: int | None = None,
    ) -> int:
        enseignant = Enseignant(
            len(self.enseignants) + 1,
            f"E{len(self.enseignants) + 1:03d}",
            nom,
            StatutEnseignant.PERMANENT,
            max_jour,
            max_semaine,
            fictif,
        )
        self.enseignants.append(enseignant)
        return enseignant.id

    def groupe(
        self,
        code: str,
        parent: int | None = None,
        effectif: int = 20,
        accessibilite_requise: bool = False,
    ) -> int:
        identifiant = len(self.groupes) + 1
        nature = NatureGroupe.PROMOTION if parent is None else NatureGroupe.DEMI_GROUPE
        self.groupes.append(
            Groupe(identifiant, code, code, nature, parent, effectif, accessibilite_requise)
        )
        courant = parent
        while courant is not None:
            self.conflit(identifiant, courant)
            courant = self.groupes[courant - 1].parent_id
        return identifiant

    def conflit(self, a: int, b: int) -> None:
        """Déclare deux groupes en conflit (ils ne peuvent pas avoir cours en même temps)."""
        self.conflits.add((min(a, b), max(a, b)))

    def trajet(self, campus_a: int, campus_b: int, duree: int) -> None:
        self.trajets.append(Trajet(campus_a, campus_b, duree))

    def indisponibilite(
        self,
        cible: CibleIndisponibilite,
        ressource: int,
        debut: str | int,
        fin: str | int,
        *,
        jour: int | None = None,
        date_debut: dt.date | None = None,
        date_fin: dt.date | None = None,
        motif: str = "",
        dure: bool = True,
    ) -> Indisponibilite:
        indisponibilite = Indisponibilite(
            len(self.indisponibilites) + 1,
            cible,
            ressource,
            jour,
            date_debut,
            date_fin,
            h(debut),
            h(fin),
            motif,
            dure,
        )
        self.indisponibilites.append(indisponibilite)
        return indisponibilite

    def jour_ferme(
        self,
        debut: dt.date,
        fin: dt.date | None = None,
        libelle: str = "Fermeture",
        nature: NatureJourFerme = NatureJourFerme.FERIE,
        groupes: Iterable[int] | None = None,
    ) -> JourFerme:
        ferme = JourFerme(
            debut,
            fin or debut,
            libelle,
            nature,
            None if groupes is None else frozenset(groupes),
        )
        self.jours_fermes.append(ferme)
        return ferme

    # -- demande ----------------------------------------------------------------

    def besoin(
        self,
        libelle: str,
        groupes: Iterable[int],
        enseignants: Iterable[int] = (),
        duree: int = 120,
        type_seance: str = "TD",
        **options: object,
    ) -> Besoin:
        groupes = frozenset(groupes)
        effectif = sum(self.groupes[g - 1].effectif for g in groupes)
        accessibilite = any(self.groupes[g - 1].accessibilite_requise for g in groupes)
        valeurs: dict[str, object] = {
            "effectif": effectif,
            "accessibilite_requise": accessibilite,
            "samedi_autorise": False,
        }
        valeurs.update(options)
        besoin = Besoin(
            id=len(self.besoins) + 1,
            libelle=libelle,
            matiere_id=len(self.besoins) + 1,
            matiere_code=f"M{len(self.besoins) + 1}",
            type_seance_id=1,
            type_seance_code=type_seance,
            duree=duree,
            groupes=groupes,
            enseignants=frozenset(enseignants),
            **valeurs,  # type: ignore[arg-type]
        )
        self.besoins.append(besoin)
        return besoin

    def seance(
        self,
        besoin: Besoin,
        rang: int = 1,
        semaines: Iterable[int] | None = None,
        verrou: Placement | None = None,
    ) -> SeanceAPlacer:
        actives = (
            frozenset(s.numero for s in self.semaines) if semaines is None else frozenset(semaines)
        )
        seance = SeanceAPlacer(id_seance(besoin.id, rang), besoin, rang, actives, verrou)
        self.seances.append(seance)
        return seance

    def cours(
        self,
        libelle: str,
        groupes: Iterable[int],
        enseignants: Iterable[int] = (),
        *,
        semaines: Iterable[int] | None = None,
        **options: object,
    ) -> SeanceAPlacer:
        """Un besoin et sa séance unique (rang 1)."""
        return self.seance(self.besoin(libelle, groupes, enseignants, **options), 1, semaines)

    def probleme(self, besoin: Besoin, code, message: str) -> None:
        self.problemes.append(ProblemeDepliage(besoin.id, code, message))

    def construire(self) -> Instantane:
        return Instantane(
            semestre_id=1,
            libelle="Semestre de test",
            date_debut=DEBUT_SEMESTRE,
            date_fin=self.semaines[-1].lundi + dt.timedelta(days=4),
            semaines=self.semaines,
            creneaux=self.creneaux,
            campus=tuple(self.campus),
            trajets=tuple(self.trajets),
            salles=tuple(self.salles),
            enseignants=tuple(self.enseignants),
            groupes=tuple(self.groupes),
            conflits_groupes=frozenset(self.conflits),
            indisponibilites=tuple(self.indisponibilites),
            jours_fermes=tuple(self.jours_fermes),
            parametres=self.parametres,
            besoins=tuple(self.besoins),
            seances=tuple(self.seances),
            problemes_depliage=tuple(self.problemes),
        )


def placer(
    seance: SeanceAPlacer,
    jour: int,
    debut: str | int,
    salle: int | None = None,
    *,
    fin: str | int | None = None,
) -> Placement:
    """Placement de `seance` ; la fin vaut par défaut début + durée du besoin."""
    debut = h(debut)
    return Placement(seance.id, jour, debut, debut + seance.duree if fin is None else h(fin), salle)


def occurrence(
    seance: SeanceAPlacer,
    date: dt.date,
    debut: str | int,
    salle: int | None = None,
    *,
    fin: str | int | None = None,
    identifiant: int | None = None,
    statut: StatutOccurrence = StatutOccurrence.PLANIFIEE,
) -> Occurrence:
    """Occurrence datée de `seance` ; la fin vaut par défaut début + durée du besoin."""
    debut = h(debut)
    return Occurrence(
        identifiant,
        seance.besoin.id,
        seance.id,
        date,
        debut,
        debut + seance.duree if fin is None else h(fin),
        salle,
        statut,
    )


def codes(anomalies) -> list[str]:
    """Codes des anomalies, dans l'ordre (pour des assertions lisibles)."""
    return [str(anomalie.code) for anomalie in anomalies]


def bloquantes(anomalies) -> list:
    """Anomalies de gravité « erreur »."""
    return [anomalie for anomalie in anomalies if anomalie.bloquante]


__all__ = [
    "DEBUT_SEMESTRE",
    "DIMANCHE",
    "ISSY",
    "JEUDI",
    "LUNDI",
    "MARDI",
    "MERCREDI",
    "NOTRE_DAME",
    "SAMEDI",
    "VENDREDI",
    "Fabrique",
    "ModeVolume",
    "bloquantes",
    "codes",
    "h",
    "occurrence",
    "placer",
]
