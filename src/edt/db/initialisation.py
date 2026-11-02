"""Création du schéma et des données par défaut.

Il n'y a pas de migrations : `initialiser_base(reset=True)` supprime tout et
recrée. Sans `reset`, l'initialisation est **idempotente** : elle crée les
tables manquantes et ajoute les données par défaut absentes, sans jamais
modifier une ligne existante (on peut donc la relancer à chaque déploiement).

Schéma périmé
-------------
Créer les tables manquantes ne suffit pas : après une mise à jour du code,
une table **déjà là** peut ne plus avoir les colonnes du modèle, et
l'application planterait à la première requête qui les touche.
`ecarts_schema()` compare donc chaque table existante au modèle, colonne par
colonne. Tant qu'il reste un écart, `initialiser_base()` sans `reset` refuse
de continuer (`ErreurSchemaPerime`) sans rien modifier, et la base n'est pas
considérée comme initialisée (`base_initialisee`). Le seul remède est
`reset=True`, qui efface les données.

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
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Engine, MetaData, inspect, select
from sqlalchemy import Enum as EnumSQL
from sqlalchemy.engine import Inspector
from sqlalchemy.exc import CompileError
from sqlalchemy.orm import Session
from sqlalchemy.types import TypeEngine

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
from edt.db.moteur import chemin_base, obtenir_moteur, session_base
from edt.domaine.calendrier import annee_scolaire_de, decoupage_par_defaut, jours_feries_entre
from edt.domaine.enumerations import DemiJournee, NatureJourFerme
from edt.domaine.parametres import DEJEUNER_AUCUN, DEJEUNER_FIXE, definitions
from edt.erreurs import ErreurDonneesInvalides, ErreurRegleMetier

journal = logging.getLogger(__name__)

MARCHE_A_SUIVRE_SCHEMA_PERIME = (
    "Le schéma a changé et il n'y a pas de migrations : relancer avec --reset "
    "(« python -m edt init-db --reset ») recrée la base et EFFACE toutes les données."
)
"""Ce qu'il faut faire quand la base ne correspond plus au modèle."""

_CONTRAINTE_ENUMERATION = re.compile(
    r"""\s*"?(?P<colonne>\w+)"?\s+IN\s*\((?P<valeurs>.*)\)\s*""", re.IGNORECASE | re.DOTALL
)
_VALEUR_SQL = re.compile(r"'((?:[^']|'')*)'")


class ErreurSchemaPerime(ErreurRegleMetier):
    """Des tables existantes de la base n'ont plus les colonnes du modèle.

    `ecarts` (repris dans `details`) liste les différences ; le texte complet
    de l'erreur se termine par la marche à suivre.
    """

    def __init__(self, ecarts: list[str]) -> None:
        super().__init__(
            f"Le schéma de la base ne correspond plus au modèle ({len(ecarts)} écart(s)). "
            "Rien n'a été modifié.",
            ecarts,
        )
        self.ecarts: tuple[str, ...] = tuple(ecarts)

    def __str__(self) -> str:
        return f"{super().__str__()}\n{MARCHE_A_SUIVRE_SCHEMA_PERIME}"


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


def sauvegarder_base(url: str | None = None, suffixe: str = "avant-reset") -> Path | None:
    """Copie cohérente du fichier SQLite à côté de lui (« edt-<date>-<suffixe>.db »).

    Utilise l'API de sauvegarde de SQLite : la copie inclut les écritures
    encore dans le fichier -wal. Renvoie le chemin de la copie, ou `None` si
    la base n'a pas encore de fichier. Lève `ErreurRegleMetier` si la copie
    échoue : on ne détruit pas une base qu'on n'a pas su sauvegarder.
    """
    chemin = chemin_base(url or str(obtenir_moteur(url).url))
    if chemin is None or not chemin.exists():
        return None
    horodatage = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    copie = chemin.with_name(f"{chemin.stem}-{horodatage}-{suffixe}{chemin.suffix or '.db'}")
    try:
        with sqlite3.connect(chemin) as source, sqlite3.connect(copie) as cible:
            source.backup(cible)
    except (sqlite3.Error, OSError) as erreur:
        raise ErreurRegleMetier(
            f"La sauvegarde de la base « {chemin} » vers « {copie} » a échoué ({erreur}) : "
            "remise à zéro annulée, rien n'a été modifié."
        ) from erreur
    journal.warning("Base %s sauvegardée dans %s avant remise à zéro", chemin, copie)
    return copie


def supprimer_schema(moteur: Engine) -> None:
    """Supprime **toutes** les tables de la base, y compris celles d'un ancien schéma."""
    existant = MetaData()
    existant.reflect(bind=moteur)
    existant.drop_all(moteur)


def tables_manquantes(moteur: Engine) -> list[str]:
    """Noms des tables du modèle absentes de la base (liste vide = schéma complet)."""
    presentes = set(inspect(moteur).get_table_names())
    return sorted(set(Base.metadata.tables) - presentes)


def ecarts_schema(moteur: Engine) -> list[str]:
    """Écarts entre les tables existantes de la base et le modèle, en phrases lisibles.

    Pour chaque table du modèle **déjà présente** dans la base, les colonnes
    sont comparées une à une :

    - colonne du modèle absente de la base, colonne de la base en trop ;
    - type : le nom du type SQL, sans sa longueur. SQLite restitue fidèlement
      le type déclaré à la création (vérifié sur toutes les colonnes du
      modèle), mais n'applique pas les longueurs : `VARCHAR(32)` et
      `VARCHAR(64)` s'y comportent à l'identique, signaler ce changement
      obligerait à effacer la base pour rien ;
    - caractère obligatoire (`NOT NULL`) ;
    - valeurs permises d'une énumération, quand la base en porte la
      contrainte (`colonne IN ('a', 'b')`) : une valeur ajoutée au modèle
      serait refusée par la base à l'enregistrement.

    Liste vide = aucune table existante ne diffère du modèle. Ne sont **pas**
    des écarts : une table du modèle absente de la base (`creer_schema` la
    crée, voir `tables_manquantes`) et une table de la base inconnue du
    modèle (elle est simplement ignorée).

    Limite assumée : les autres contraintes (unicités, clés étrangères,
    contrôles entre colonnes) et les index ne sont pas comparés. Après un tel
    changement du modèle, recréer la base avec `reset=True`.
    """
    inspecteur = inspect(moteur)
    presentes = set(inspecteur.get_table_names())
    ecarts: list[str] = []
    for nom in sorted(Base.metadata.tables):
        if nom not in presentes:
            continue
        table = Base.metadata.tables[nom]
        reelles = {colonne["name"]: colonne for colonne in inspecteur.get_columns(nom)}
        permises = (
            _valeurs_permises(inspecteur, nom)
            if any(isinstance(colonne.type, EnumSQL) for colonne in table.columns)
            else {}
        )
        for colonne in table.columns:
            reelle = reelles.pop(colonne.name, None)
            if reelle is None:
                ecarts.append(f"table {nom} : colonne {colonne.name} absente de la base")
                continue
            prefixe = f"table {nom} : colonne {colonne.name}"
            attendu = _nom_de_type(colonne.type, moteur)
            lu = _nom_de_type(reelle["type"], moteur)
            if lu != attendu:
                ecarts.append(f"{prefixe} de type {lu} dans la base, {attendu} dans le modèle")
            if bool(reelle["nullable"]) != bool(colonne.nullable):
                dans_la_base, dans_le_modele = (
                    ("facultative", "obligatoire")
                    if reelle["nullable"]
                    else ("obligatoire", "facultative")
                )
                ecarts.append(
                    f"{prefixe} {dans_la_base} dans la base, {dans_le_modele} dans le modèle"
                )
            if isinstance(colonne.type, EnumSQL) and colonne.name in permises:
                du_modele, de_la_base = set(colonne.type.enums), permises[colonne.name]
                if du_modele - de_la_base:
                    ecarts.append(
                        f"{prefixe} : valeur(s) du modèle refusée(s) par la base : "
                        + ", ".join(sorted(du_modele - de_la_base))
                    )
                if de_la_base - du_modele:
                    ecarts.append(
                        f"{prefixe} : valeur(s) de la base inconnue(s) du modèle : "
                        + ", ".join(sorted(de_la_base - du_modele))
                    )
        ecarts.extend(f"table {nom} : colonne en trop : {en_trop}" for en_trop in reelles)
    return ecarts


def _nom_de_type(type_sql: TypeEngine, moteur: Engine) -> str:
    """Nom du type SQL pour la base visée, sans sa longueur : `VARCHAR(32)` donne `VARCHAR`."""
    try:
        texte = type_sql.compile(dialect=moteur.dialect)
    except CompileError:
        # Type déclaré dans la base que SQLAlchemy ne sait pas nommer (table modifiée à la
        # main) : il ne peut pas correspondre au modèle, l'écart sera donc signalé.
        return "inconnu"
    return texte.split("(", 1)[0].strip().upper()


def _valeurs_permises(inspecteur: Inspector, nom_table: str) -> dict[str, set[str]]:
    """Valeurs permises par la base pour chaque colonne contrainte par `colonne IN (...)`.

    C'est la forme de la contrainte que SQLAlchemy pose pour une énumération.
    Une colonne sans contrainte de cette forme n'apparaît pas dans le résultat.
    """
    permises: dict[str, set[str]] = {}
    for contrainte in inspecteur.get_check_constraints(nom_table):
        reconnue = _CONTRAINTE_ENUMERATION.fullmatch(contrainte["sqltext"])
        if reconnue:
            permises[reconnue["colonne"]] = {
                valeur.replace("''", "'") for valeur in _VALEUR_SQL.findall(reconnue["valeurs"])
            }
    return permises


def exiger_schema_a_jour(moteur: Engine) -> None:
    """Lève `ErreurSchemaPerime` si une table existante ne correspond plus au modèle."""
    ecarts = ecarts_schema(moteur)
    if ecarts:
        raise ErreurSchemaPerime(ecarts)


def base_initialisee(moteur: Engine) -> bool:
    """Vrai si toutes les tables du modèle existent et ont les colonnes du modèle."""
    return not tables_manquantes(moteur) and not ecarts_schema(moteur)


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


ANCIENNE_CLE_DEJEUNER = "dejeuner_horaires_determines"
"""Paramètre booléen d'avant `dejeuner_mode`, encore présent dans les bases d'alors."""


def _inserer_parametres(session: Session) -> int:
    """Ajoute les paramètres absents avec leur défaut ; reprend l'ancien réglage du déjeuner.

    Une base d'avant `dejeuner_mode` porte le booléen `dejeuner_horaires_determines` :
    le mode inséré en découle (vrai : horaires déterminés, faux : pas de pause) au
    lieu du défaut, puis la ligne devenue inutile est supprimée. Sans cela, le
    choix « pas de pause » de l'utilisateur basculerait en silence au défaut.
    """
    enregistres = dict(session.execute(select(Parametre.cle, Parametre.valeur)).all())
    defauts = {entree.cle: entree.defaut for entree in definitions()}
    if ANCIENNE_CLE_DEJEUNER in enregistres:
        ancien = enregistres[ANCIENNE_CLE_DEJEUNER]
        if "dejeuner_mode" not in enregistres:
            if not isinstance(ancien, bool):
                raise ErreurDonneesInvalides(
                    f"Le paramètre « {ANCIENNE_CLE_DEJEUNER} » enregistré en base est "
                    f"invalide : oui ou non attendu (lu : {ancien!r})."
                )
            defauts["dejeuner_mode"] = DEJEUNER_FIXE if ancien else DEJEUNER_AUCUN
            journal.info(
                "Ancien paramètre « %s » = %r : le mode de déjeuner « %s » est enregistré à sa place.",
                ANCIENNE_CLE_DEJEUNER,
                ancien,
                defauts["dejeuner_mode"],
            )
        session.delete(session.get(Parametre, ANCIENNE_CLE_DEJEUNER))
        journal.info("Ancien paramètre « %s » supprimé de la base.", ANCIENNE_CLE_DEJEUNER)
    nouveaux = [
        Parametre(cle=cle, valeur=valeur)
        for cle, valeur in defauts.items()
        if cle not in enregistres
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

    Sans `reset`, lève `ErreurSchemaPerime` si une table existante n'a plus les
    colonnes du modèle : rien n'est alors créé ni modifié.
    """
    moteur = obtenir_moteur(url)
    url_affichee = url_affichable(moteur.url)
    if reset:
        journal.warning(
            "Remise à zéro de la base %s : toutes les données sont supprimées", url_affichee
        )
        supprimer_schema(moteur)
    else:
        exiger_schema_a_jour(moteur)
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
