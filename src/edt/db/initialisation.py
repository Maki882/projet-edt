"""Création du schéma et des données par défaut.

Il n'y a pas de migrations : `initialiser_base(reset=True)` supprime tout et
recrée. Sans `reset`, l'initialisation est **idempotente** : elle crée les
tables manquantes et ajoute les données par défaut absentes, sans jamais
modifier une ligne existante (on peut donc la relancer à chaque déploiement).

Données par défaut :

- la grille horaire (lundi-vendredi quatre créneaux, samedi matin deux) ;
- les types de salle, les équipements courants, les types de séance ;
- les paramètres globaux (valeurs de `edt.domaine.parametres.Parametres`) ;
- l'année scolaire en cours, ses deux semestres et ses jours fériés
  (fériés mobiles calculés).

Les vacances et fermetures propres à l'école ne sont pas devinées : elles se
saisissent dans l'interface ou viennent d'un jeu de données.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from sqlalchemy import Engine, MetaData, inspect, select
from sqlalchemy.orm import Session

from edt.config import url_affichable
from edt.db.base import Base
from edt.db.modeles import (
    AnneeScolaire,
    Creneau,
    Equipement,
    JourFerme,
    Parametre,
    Semestre,
    TypeSalle,
    TypeSeance,
)
from edt.db.moteur import obtenir_moteur, session_base
from edt.domaine.calendrier import annee_scolaire_de, decoupage_par_defaut, jours_feries_entre
from edt.domaine.enumerations import DemiJournee, NatureJourFerme
from edt.domaine.parametres import definitions

journal = logging.getLogger(__name__)

H = 60

GRILLE_SEMAINE: tuple[tuple[int, int, DemiJournee], ...] = (
    (8 * H + 30, 10 * H + 30, DemiJournee.MATIN),
    (10 * H + 45, 12 * H + 45, DemiJournee.MATIN),
    (13 * H + 45, 15 * H + 45, DemiJournee.APRES_MIDI),
    (16 * H, 18 * H, DemiJournee.APRES_MIDI),
)
"""Créneaux par défaut du lundi au vendredi (début, fin, demi-journée)."""

GRILLE_SAMEDI: tuple[tuple[int, int, DemiJournee], ...] = GRILLE_SEMAINE[:2]
"""Créneaux par défaut du samedi : la matinée seulement."""

TYPES_SALLE: tuple[tuple[str, str], ...] = (
    ("AMPHI", "Amphithéâtre"),
    ("COURS", "Salle de cours"),
    ("INFO", "Salle informatique"),
    ("TP", "Salle de travaux pratiques"),
    ("LABO", "Laboratoire"),
)

EQUIPEMENTS: tuple[tuple[str, str], ...] = (
    ("VIDEOPROJ", "Vidéoprojecteur"),
    ("ORDINATEURS", "Postes informatiques"),
    ("VISIO", "Visioconférence"),
    ("ELECTRONIQUE", "Matériel d'électronique"),
    ("LABORATOIRE", "Équipements de laboratoire"),
)

TYPES_SEANCE: tuple[tuple[str, str, bool, str | None, str], ...] = (
    ("CM", "Cours magistral", False, "AMPHI", "#2f6fad"),
    ("TD", "Travaux dirigés", False, "COURS", "#3f9b6c"),
    ("TP", "Travaux pratiques", True, "TP", "#c9822b"),
    ("PROJET", "Projet", False, "COURS", "#8a63b8"),
    ("LANGUE", "Langue", False, "COURS", "#2a9d9a"),
    ("DST", "Devoir surveillé", False, None, "#c0504d"),
    ("EXAMEN", "Examen", False, None, "#a23b72"),
    ("AUTRE", "Autre", False, None, "#6b7280"),
)
"""Types de séance par défaut : code, libellé, samedi autorisé, type de salle par défaut, couleur."""


@dataclass(frozen=True)
class RapportInitialisation:
    """Ce que `initialiser_base` a fait."""

    url: str
    reinitialisee: bool
    nb_tables: int
    ajouts: dict[str, int] = field(default_factory=dict)

    @property
    def nb_ajouts(self) -> int:
        """Nombre total de lignes par défaut ajoutées."""
        return sum(self.ajouts.values())


# --------------------------------------------------------------------------- #
# Schéma
# --------------------------------------------------------------------------- #


def creer_schema(moteur: Engine) -> None:
    """Crée les tables manquantes (ne modifie pas une table existante)."""
    Base.metadata.create_all(moteur)


def supprimer_schema(moteur: Engine) -> None:
    """Supprime **toutes** les tables de la base, y compris celles d'un ancien schéma."""
    existant = MetaData()
    existant.reflect(bind=moteur)
    existant.drop_all(moteur)


def tables_manquantes(moteur: Engine) -> list[str]:
    """Noms des tables du modèle absentes de la base (liste vide = schéma complet)."""
    presentes = set(inspect(moteur).get_table_names())
    return sorted(set(Base.metadata.tables) - presentes)


def base_initialisee(moteur: Engine) -> bool:
    """Vrai si toutes les tables du modèle existent."""
    return not tables_manquantes(moteur)


# --------------------------------------------------------------------------- #
# Calendrier
# --------------------------------------------------------------------------- #


def assurer_annee_scolaire(session: Session, annee_debut: int) -> AnneeScolaire:
    """Renvoie l'année scolaire qui commence en `annee_debut`, en la créant au besoin.

    À la création, l'année reçoit ses deux semestres aux dates par défaut
    (`edt.domaine.calendrier.decoupage_par_defaut`) et ses jours fériés. Une
    année déjà présente n'est pas modifiée.
    """
    decoupage = decoupage_par_defaut(annee_debut)
    annee = session.scalar(select(AnneeScolaire).where(AnneeScolaire.libelle == decoupage.libelle))
    if annee is not None:
        return annee

    annee = AnneeScolaire(
        libelle=decoupage.libelle, date_debut=decoupage.debut, date_fin=decoupage.fin
    )
    annee.semestres = [
        Semestre(
            code="S1",
            libelle=f"Semestre 1 ({decoupage.libelle})",
            date_debut=decoupage.semestre_1[0],
            date_fin=decoupage.semestre_1[1],
        ),
        Semestre(
            code="S2",
            libelle=f"Semestre 2 ({decoupage.libelle})",
            date_debut=decoupage.semestre_2[0],
            date_fin=decoupage.semestre_2[1],
        ),
    ]
    session.add(annee)
    session.flush()
    nb_feries = preremplir_feries(session, annee)
    journal.info(
        "Année scolaire %s créée (2 semestres, %d jours fériés)", decoupage.libelle, nb_feries
    )
    return annee


def preremplir_feries(session: Session, annee: AnneeScolaire) -> int:
    """Ajoute à l'année ses jours fériés français manquants (fériés mobiles compris).

    Idempotent : un férié global déjà présent à la même date n'est pas
    dupliqué. Renvoie le nombre de jours ajoutés.
    """
    deja = set(
        session.scalars(
            select(JourFerme.date_debut).where(
                JourFerme.annee_scolaire_id == annee.id,
                JourFerme.nature == NatureJourFerme.FERIE,
                JourFerme.groupe_id.is_(None),
            )
        )
    )
    ajoutes = 0
    for jour, libelle in jours_feries_entre(annee.date_debut, annee.date_fin):
        if jour in deja:
            continue
        session.add(
            JourFerme(
                annee_scolaire_id=annee.id,
                date_debut=jour,
                date_fin=jour,
                libelle=libelle,
                nature=NatureJourFerme.FERIE,
            )
        )
        ajoutes += 1
    session.flush()
    return ajoutes


# --------------------------------------------------------------------------- #
# Données par défaut
# --------------------------------------------------------------------------- #


def inserer_donnees_par_defaut(
    session: Session, aujourdhui: dt.date | None = None
) -> dict[str, int]:
    """Ajoute les données par défaut absentes et renvoie le nombre d'ajouts par table.

    `aujourdhui` sert à choisir l'année scolaire à créer (défaut : la date du jour).
    Ne valide pas la transaction.
    """
    ajouts = {
        "creneau": _inserer_grille(session),
        "type_salle": _inserer_types_salle(session),
        "equipement": _inserer_equipements(session),
        "type_seance": _inserer_types_seance(session),
        "parametre": _inserer_parametres(session),
    }
    nb_annees = len(session.scalars(select(AnneeScolaire.id)).all())
    assurer_annee_scolaire(session, annee_scolaire_de(aujourdhui or dt.date.today()))
    ajouts["annee_scolaire"] = len(session.scalars(select(AnneeScolaire.id)).all()) - nb_annees
    return ajouts


def _inserer_grille(session: Session) -> int:
    """La grille par défaut n'est posée que si la table des créneaux est vide."""
    if session.scalar(select(Creneau.id).limit(1)) is not None:
        return 0
    creneaux = [
        Creneau(jour=jour, debut_min=debut, fin_min=fin, demi_journee=demi_journee)
        for jour in range(5)
        for debut, fin, demi_journee in GRILLE_SEMAINE
    ]
    creneaux += [
        Creneau(jour=5, debut_min=debut, fin_min=fin, demi_journee=demi_journee)
        for debut, fin, demi_journee in GRILLE_SAMEDI
    ]
    session.add_all(creneaux)
    session.flush()
    return len(creneaux)


def _inserer_types_salle(session: Session) -> int:
    presents = set(session.scalars(select(TypeSalle.code)))
    nouveaux = [
        TypeSalle(code=code, libelle=libelle)
        for code, libelle in TYPES_SALLE
        if code not in presents
    ]
    session.add_all(nouveaux)
    session.flush()
    return len(nouveaux)


def _inserer_equipements(session: Session) -> int:
    presents = set(session.scalars(select(Equipement.code)))
    nouveaux = [
        Equipement(code=code, libelle=libelle)
        for code, libelle in EQUIPEMENTS
        if code not in presents
    ]
    session.add_all(nouveaux)
    session.flush()
    return len(nouveaux)


def _inserer_types_seance(session: Session) -> int:
    presents = set(session.scalars(select(TypeSeance.code)))
    types_salle = {type_salle.code: type_salle for type_salle in session.scalars(select(TypeSalle))}
    nouveaux = [
        TypeSeance(
            code=code,
            libelle=libelle,
            samedi_autorise=samedi,
            type_salle_defaut=types_salle[salle_defaut] if salle_defaut else None,
            couleur=couleur,
        )
        for code, libelle, samedi, salle_defaut, couleur in TYPES_SEANCE
        if code not in presents
    ]
    session.add_all(nouveaux)
    session.flush()
    return len(nouveaux)


def _inserer_parametres(session: Session) -> int:
    presents = set(session.scalars(select(Parametre.cle)))
    nouveaux = [
        Parametre(cle=entree.cle, valeur=entree.defaut)
        for entree in definitions()
        if entree.cle not in presents
    ]
    session.add_all(nouveaux)
    session.flush()
    return len(nouveaux)


# --------------------------------------------------------------------------- #
# Point d'entrée
# --------------------------------------------------------------------------- #


def initialiser_base(
    reset: bool = False, aujourdhui: dt.date | None = None, url: str | None = None
) -> RapportInitialisation:
    """Crée le schéma et les données par défaut de la base configurée.

    - `reset=True` : supprime d'abord toutes les tables (toutes les données sont perdues) ;
    - `aujourdhui` : date de référence pour l'année scolaire créée par défaut ;
    - `url` : base visée (défaut : `EDT_DB_URL`).
    """
    moteur = obtenir_moteur(url)
    url_affichee = url_affichable(moteur.url)
    if reset:
        journal.warning(
            "Remise à zéro de la base %s : toutes les données sont supprimées", url_affichee
        )
        supprimer_schema(moteur)
    creer_schema(moteur)
    with session_base(url) as session:
        ajouts = inserer_donnees_par_defaut(session, aujourdhui)
    rapport = RapportInitialisation(
        url=url_affichee,
        reinitialisee=reset,
        nb_tables=len(Base.metadata.tables),
        ajouts=ajouts,
    )
    journal.info(
        "Base %s initialisée : %d tables, %d lignes par défaut ajoutées",
        url_affichee,
        rapport.nb_tables,
        rapport.nb_ajouts,
    )
    return rapport
