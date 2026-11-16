"""Création du schéma et des données par défaut.

Il n'y a pas de migrations : `initialiser_base(reset=True)` supprime tout et
recrée. Sans `reset`, l'initialisation est **idempotente** : elle crée les
tables manquantes et ajoute les données par défaut absentes, sans jamais
modifier une ligne existante (on peut donc la relancer à chaque déploiement).
Les listes par défaut (grille horaire, types de salle, équipements, types de
séance) ne sont posées que dans une base **neuve** (qui n'a encore aucun
paramètre enregistré) : une ligne supprimée exprès par l'utilisateur, ou une
grille vidée pour être ressaisie, n'est pas ressuscitée au déploiement
suivant. Un paramètre absent, lui, est toujours ajouté (paramètre nouveau
d'une version).

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
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Engine, MetaData, UniqueConstraint, inspect, select
from sqlalchemy import Enum as EnumSQL
from sqlalchemy.engine import Inspector
from sqlalchemy.exc import CompileError
from sqlalchemy.orm import Session
from sqlalchemy.types import TypeEngine

from edt.config import charger_configuration, url_affichable
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
from edt.db.moteur import (
    chemin_base,
    connexion_immediate,
    obtenir_moteur,
    session_base,
    transaction_immediate,
)
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
    """Crée les tables manquantes (ne modifie pas une table existante).

    Écrivain long connu : la transaction prend le verrou d'écriture d'emblée
    (`connexion_immediate`) au lieu de lire puis d'échouer « database is
    locked » face à un autre écrivain.
    """
    with connexion_immediate(moteur) as connexion:
        Base.metadata.create_all(connexion)


def copier_base(source: Path, cible: Path) -> int:
    """Copie cohérente de `source` dans `cible` (API de sauvegarde SQLite), puis contrôle.

    La copie lit un instantané de la base, écritures encore dans le fichier
    -wal comprises : le service peut écrire pendant ce temps. Renvoie le nombre
    de tables de la copie ; lève `ErreurDonneesInvalides` si la base est
    illisible, le disque plein ou la copie abîmée.
    """
    try:
        with closing(sqlite3.connect(source, timeout=30)) as connexion_source:
            with closing(sqlite3.connect(cible)) as connexion_cible:
                connexion_source.backup(connexion_cible)
                verdict = connexion_cible.execute("PRAGMA integrity_check").fetchone()[0]
                nb_tables = connexion_cible.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type = 'table'"
                ).fetchone()[0]
    except sqlite3.Error as erreur:
        raise ErreurDonneesInvalides(
            f"Sauvegarde impossible : base {source} illisible ou disque plein ({erreur})."
        ) from erreur
    if verdict != "ok":
        raise ErreurDonneesInvalides(
            f"Sauvegarde refusée : le contrôle d'intégrité de la copie échoue ({verdict}). La base "
            f"{source} est sans doute abîmée (docs/deploiement.md, « Base corrompue »)."
        )
    if nb_tables == 0:
        raise ErreurDonneesInvalides(f"Sauvegarde refusée : {source} ne contient aucune table.")
    return nb_tables


def sauvegarder_base(url: str | None = None) -> Path | None:
    """Copie vérifiée de la base avant sa remise à zéro, dans le dossier « sauvegardes ».

    Le fichier `sauvegardes/edt-<date et heure UTC>-avant-reset.db`, à côté de
    la base, suit le nommage des sauvegardes du déploiement et tourne donc
    avec elles. Il est écrit en « .partiel », puis renommé seulement si son
    contrôle d'intégrité passe (`copier_base`). Renvoie le chemin de la copie,
    ou `None` si la base n'a pas encore de fichier. Lève `ErreurRegleMetier` si
    la copie échoue : une base qui n'a pas pu être sauvegardée n'est pas détruite.
    """
    chemin = chemin_base(url or charger_configuration().db_url)
    if chemin is None or not chemin.is_file():
        return None
    horodatage = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    copie = chemin.parent / "sauvegardes" / f"edt-{horodatage}-avant-reset.db"
    partiel = copie.with_name(copie.name + ".partiel")
    try:
        copie.parent.mkdir(parents=True, exist_ok=True)
        copier_base(chemin, partiel)
        partiel.replace(copie)
    except (ErreurDonneesInvalides, OSError) as erreur:
        partiel.unlink(missing_ok=True)
        raise ErreurRegleMetier(
            f"La sauvegarde de la base « {chemin} » vers « {copie} » a échoué ({erreur}) : "
            "remise à zéro annulée, rien n'a été modifié. Libérer de l'espace disque ou "
            "corriger les droits du dossier, puis relancer."
        ) from erreur
    journal.warning("Base %s sauvegardée dans %s avant remise à zéro", chemin, copie)
    return copie


def supprimer_schema(moteur: Engine) -> None:
    """Supprime **toutes** les tables de la base, y compris celles d'un ancien schéma.

    Réflexion et suppression dans une même transaction ouverte en
    `BEGIN IMMEDIATE` (voir `creer_schema`).
    """
    with connexion_immediate(moteur) as connexion:
        existant = MetaData()
        existant.reflect(bind=connexion)
        existant.drop_all(connexion)


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

    Puis les **unicités** (par colonnes) et les **index** (par nom, colonnes
    et caractère unique) de chaque table : un index unique partiel absent
    laisserait passer deux versions actives, une unicité absente des doublons.

    Liste vide = aucune table existante ne diffère du modèle. Ne sont **pas**
    des écarts : une table du modèle absente de la base (`creer_schema` la
    crée, voir `tables_manquantes`) et une table de la base inconnue du
    modèle (elle est simplement ignorée).

    Limite assumée : les clés étrangères (règle de suppression) et les
    contrôles entre colonnes (CHECK autres que les énumérations) ne sont pas
    comparés. Après un tel changement du modèle, recréer la base avec
    `reset=True`.
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
        ecarts.extend(_ecarts_unicites_et_index(inspecteur, nom, table))
    return ecarts


def _ecarts_unicites_et_index(inspecteur: Inspector, nom: str, table) -> list[str]:
    """Unicités (par colonnes) et index (par nom) du modèle absents de la base, ou en trop."""
    ecarts: list[str] = []
    du_modele = {
        tuple(colonne.name for colonne in contrainte.columns)
        for contrainte in table.constraints
        if isinstance(contrainte, UniqueConstraint)
    }
    de_la_base = {
        tuple(contrainte["column_names"]) for contrainte in inspecteur.get_unique_constraints(nom)
    }
    for colonnes in sorted(du_modele - de_la_base):
        ecarts.append(f"table {nom} : unicité ({', '.join(colonnes)}) absente de la base")
    for colonnes in sorted(de_la_base - du_modele):
        ecarts.append(f"table {nom} : unicité ({', '.join(colonnes)}) en trop dans la base")

    index_modele = {
        index.name: (tuple(colonne.name for colonne in index.columns), bool(index.unique))
        for index in table.indexes
        if index.name
    }
    index_base = {
        index["name"]: (tuple(index["column_names"]), bool(index["unique"]))
        for index in inspecteur.get_indexes(nom)
        if index["name"] and not index["name"].startswith("sqlite_")
    }
    for nom_index in sorted(set(index_modele) - set(index_base)):
        ecarts.append(f"table {nom} : index {nom_index} absent de la base")
    for nom_index in sorted(set(index_base) - set(index_modele)):
        ecarts.append(f"table {nom} : index en trop : {nom_index}")
    for nom_index in sorted(set(index_modele) & set(index_base)):
        if index_modele[nom_index] != index_base[nom_index]:
            colonnes, unique = index_base[nom_index]
            ecarts.append(
                f"table {nom} : index {nom_index} différent dans la base "
                f"(colonnes {', '.join(colonnes)}, {'unique' if unique else 'non unique'})"
            )
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
    # Une base neuve n'a encore aucun paramètre : c'est là, et là seulement, que les listes
    # par défaut sont posées (voir l'en-tête du module).
    neuve = session.scalar(select(Parametre.cle).limit(1)) is None
    ajouts = {
        "creneau": _inserer_grille(session, neuve),
        "type_salle": _inserer_types_salle(session, neuve),
        "equipement": _inserer_equipements(session, neuve),
        "type_seance": _inserer_types_seance(session, neuve),
        "parametre": _inserer_parametres(session),
    }
    nb_annees = len(session.scalars(select(AnneeScolaire.id)).all())
    assurer_annee_scolaire(session, annee_scolaire_de(aujourdhui or dt.date.today()))
    ajouts["annee_scolaire"] = len(session.scalars(select(AnneeScolaire.id)).all()) - nb_annees
    return ajouts


def _inserer_grille(session: Session, neuve: bool) -> int:
    """La grille par défaut n'est posée que dans une base neuve (voir l'en-tête du module)."""
    if not neuve or session.scalar(select(Creneau.id).limit(1)) is not None:
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


def _inserer_types_salle(session: Session, neuve: bool) -> int:
    """Les types de salle par défaut ne sont posés que dans une base neuve (voir l'en-tête)."""
    if not neuve or session.scalar(select(TypeSalle.id).limit(1)) is not None:
        return 0
    nouveaux = [TypeSalle(code=code, libelle=libelle) for code, libelle in TYPES_SALLE]
    session.add_all(nouveaux)
    session.flush()
    return len(nouveaux)


def _inserer_equipements(session: Session, neuve: bool) -> int:
    """Les équipements par défaut ne sont posés que dans une base neuve (voir l'en-tête)."""
    if not neuve or session.scalar(select(Equipement.id).limit(1)) is not None:
        return 0
    nouveaux = [Equipement(code=code, libelle=libelle) for code, libelle in EQUIPEMENTS]
    session.add_all(nouveaux)
    session.flush()
    return len(nouveaux)


def _inserer_types_seance(session: Session, neuve: bool) -> int:
    """Les types de séance par défaut ne sont posés que dans une base neuve (voir l'en-tête)."""
    if not neuve or session.scalar(select(TypeSeance.id).limit(1)) is not None:
        return 0
    types_salle = {type_salle.code: type_salle for type_salle in session.scalars(select(TypeSalle))}
    nouveaux = [
        TypeSeance(
            code=code,
            libelle=libelle,
            samedi_autorise=samedi,
            type_salle_defaut=types_salle.get(salle_defaut) if salle_defaut else None,
            couleur=couleur,
        )
        for code, libelle, samedi, salle_defaut, couleur in TYPES_SEANCE
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
        transaction_immediate(session)  # écrivain long : lit puis écrit, verrou pris d'emblée
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
