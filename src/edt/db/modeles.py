"""Modèles ORM : toutes les tables de l'application.

La description détaillée (diagramme de classes, colonnes, unicités, règles
de suppression) est dans `docs/modele-donnees.md` ; les deux doivent rester
synchronisés.

Conventions :

- noms de tables en français, au singulier ;
- heures en **minutes depuis minuit** (colonnes `*_min`), dates sans fuseau ;
- jours de la semaine de 0 (lundi) à 6 (dimanche) ;
- énumérations stockées par leur valeur texte (voir `edt.domaine.enumerations`) ;
- données importables : colonnes de provenance (`source_fichier`,
  `source_ligne`, `extra`) apportées par `Provenance`.

Règles de suppression (clés étrangères) :

- **CASCADE** pour ce qui appartient à son parent (semestres d'une année,
  groupes enfants, lignes d'association d'un besoin, séances d'une version) ;
- **NO ACTION** (suppression refusée par la base) pour ce qui est référencé
  par une demande : on ne modifie jamais un besoin en silence. Supprimer un
  groupe, un enseignant, une matière ou une salle imposée encore utilisés par
  un besoin est donc refusé ;
- **SET NULL** pour une salle affectée à une séance déjà placée : la séance
  reste visible, signalée « sans salle ».
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as EnumSQL
from sqlalchemy.orm import Mapped, mapped_column, relationship

from edt.db.base import Base
from edt.domaine.enumerations import (
    Accessibilite,
    CibleIndisponibilite,
    DemiJournee,
    EtatCalcul,
    ModeVolume,
    NatureGroupe,
    NatureJourFerme,
    OrigineOccurrence,
    OrigineVersion,
    StatutEnseignant,
    StatutOccurrence,
)
from edt.erreurs import ErreurDonneesInvalides


def type_enum(enumeration: type[StrEnum], nom: str) -> EnumSQL:
    """Type de colonne pour une énumération : valeur texte + contrainte CHECK nommée."""
    return EnumSQL(
        enumeration,
        values_callable=lambda membres: [membre.value for membre in membres],
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        length=32,
        name=nom,
    )


class Provenance:
    """Colonnes de provenance des données importées (vides pour une saisie manuelle)."""

    source_fichier: Mapped[str | None] = mapped_column(
        String(255), default=None, comment="Nom du fichier importé d'où vient la ligne"
    )
    source_ligne: Mapped[int | None] = mapped_column(
        Integer, default=None, comment="Numéro de ligne dans le fichier importé"
    )
    extra: Mapped[dict | None] = mapped_column(
        JSON, default=None, comment="Colonnes du fichier non reconnues, conservées telles quelles"
    )


# --------------------------------------------------------------------------- #
# Calendrier
# --------------------------------------------------------------------------- #


class AnneeScolaire(Base):
    """Année scolaire (par exemple « 2026-2027 »)."""

    __tablename__ = "annee_scolaire"
    __table_args__ = (
        UniqueConstraint("libelle"),
        CheckConstraint("date_debut <= date_fin", name="dates_ordonnees"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    libelle: Mapped[str] = mapped_column(String(32), comment="Libellé unique, ex. « 2026-2027 »")
    date_debut: Mapped[dt.date] = mapped_column(Date, comment="Premier jour de l'année")
    date_fin: Mapped[dt.date] = mapped_column(Date, comment="Dernier jour de l'année")

    semestres: Mapped[list[Semestre]] = relationship(
        back_populates="annee",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Semestre.date_debut",
    )

    def __repr__(self) -> str:
        return f"<AnneeScolaire {self.libelle}>"


class Semestre(Base):
    """Semestre d'une année scolaire : l'unité de génération d'un emploi du temps."""

    __tablename__ = "semestre"
    __table_args__ = (
        UniqueConstraint("annee_scolaire_id", "code"),
        CheckConstraint("date_debut <= date_fin", name="dates_ordonnees"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    annee_scolaire_id: Mapped[int] = mapped_column(
        ForeignKey("annee_scolaire.id", ondelete="CASCADE"), comment="Année de rattachement"
    )
    code: Mapped[str] = mapped_column(String(16), comment="Code court, ex. « S1 »")
    libelle: Mapped[str] = mapped_column(String(64), comment="Libellé affiché")
    date_debut: Mapped[dt.date] = mapped_column(
        Date, comment="Premier jour de cours (paramétrable)"
    )
    date_fin: Mapped[dt.date] = mapped_column(Date, comment="Dernier jour de cours (paramétrable)")

    annee: Mapped[AnneeScolaire] = relationship(back_populates="semestres")

    def __repr__(self) -> str:
        return f"<Semestre {self.code} {self.date_debut}..{self.date_fin}>"


class JourFerme(Provenance, Base):
    """Fermeture du calendrier : un jour ou une plage de dates sans cours.

    Portée globale (`groupe_id` vide) ou limitée à un groupe et ses descendants.
    """

    __tablename__ = "jour_ferme"
    __table_args__ = (CheckConstraint("date_debut <= date_fin", name="dates_ordonnees"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    annee_scolaire_id: Mapped[int] = mapped_column(
        ForeignKey("annee_scolaire.id", ondelete="CASCADE"), comment="Année concernée"
    )
    date_debut: Mapped[dt.date] = mapped_column(Date, comment="Premier jour fermé")
    date_fin: Mapped[dt.date] = mapped_column(Date, comment="Dernier jour fermé (inclus)")
    libelle: Mapped[str] = mapped_column(String(128), comment="Ex. « Toussaint », « Partiels »")
    nature: Mapped[NatureJourFerme] = mapped_column(
        type_enum(NatureJourFerme, "nature"), comment="Férié, vacances, examens, semaine spéciale"
    )
    groupe_id: Mapped[int | None] = mapped_column(
        ForeignKey("groupe.id", ondelete="CASCADE"),
        default=None,
        comment="Vide = fermeture globale ; sinon groupe concerné (et ses descendants)",
    )

    annee: Mapped[AnneeScolaire] = relationship()
    groupe: Mapped[Groupe | None] = relationship()


class Creneau(Base):
    """Créneau de la grille horaire (grille unique, configurable)."""

    __tablename__ = "creneau"
    __table_args__ = (
        UniqueConstraint("jour", "debut_min"),
        CheckConstraint("jour BETWEEN 0 AND 6", name="jour_valide"),
        CheckConstraint("debut_min >= 0 AND debut_min < fin_min AND fin_min <= 1440", name="plage"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    jour: Mapped[int] = mapped_column(Integer, comment="0 = lundi ... 6 = dimanche")
    debut_min: Mapped[int] = mapped_column(Integer, comment="Début, en minutes depuis minuit")
    fin_min: Mapped[int] = mapped_column(Integer, comment="Fin, en minutes depuis minuit")
    demi_journee: Mapped[DemiJournee] = mapped_column(
        type_enum(DemiJournee, "demi_journee"), comment="Matin ou après-midi"
    )


# --------------------------------------------------------------------------- #
# Lieux
# --------------------------------------------------------------------------- #


class Campus(Base):
    """Site d'enseignement (campus de l'école ou établissement voisin)."""

    __tablename__ = "campus"
    __table_args__ = (UniqueConstraint("code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), comment="Code unique, ex. « NDC »")
    nom: Mapped[str] = mapped_column(String(128), comment="Nom complet")
    prefixe_salle: Mapped[str | None] = mapped_column(
        String(16), default=None, comment="Préfixe des codes de salle du site, ex. « N », « L »"
    )

    def __repr__(self) -> str:
        return f"<Campus {self.code}>"


class Trajet(Base):
    """Temps de trajet entre deux campus (symétrique : une ligne par paire, a < b)."""

    __tablename__ = "trajet"
    __table_args__ = (
        UniqueConstraint("campus_a_id", "campus_b_id"),
        CheckConstraint("campus_a_id < campus_b_id", name="paire_ordonnee"),
        CheckConstraint("duree_min >= 0", name="duree_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    campus_a_id: Mapped[int] = mapped_column(
        ForeignKey("campus.id", ondelete="CASCADE"), comment="Campus de plus petit identifiant"
    )
    campus_b_id: Mapped[int] = mapped_column(
        ForeignKey("campus.id", ondelete="CASCADE"), comment="Campus de plus grand identifiant"
    )
    duree_min: Mapped[int] = mapped_column(Integer, comment="Temps de trajet, en minutes")


class TypeSalle(Base):
    """Type de salle (amphithéâtre, salle informatique...), liste configurable."""

    __tablename__ = "type_salle"
    __table_args__ = (UniqueConstraint("code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), comment="Code unique, ex. « AMPHI »")
    libelle: Mapped[str] = mapped_column(String(128), comment="Libellé affiché")

    def __repr__(self) -> str:
        return f"<TypeSalle {self.code}>"


class Equipement(Base):
    """Équipement d'une salle (vidéoprojecteur, ordinateurs...), liste configurable."""

    __tablename__ = "equipement"
    __table_args__ = (UniqueConstraint("code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), comment="Code unique, ex. « VIDEOPROJ »")
    libelle: Mapped[str] = mapped_column(String(128), comment="Libellé affiché")

    def __repr__(self) -> str:
        return f"<Equipement {self.code}>"


salle_equipement = Table(
    "salle_equipement",
    Base.metadata,
    Column("salle_id", ForeignKey("salle.id", ondelete="CASCADE"), primary_key=True),
    Column("equipement_id", ForeignKey("equipement.id", ondelete="CASCADE"), primary_key=True),
    comment="Équipements présents dans une salle",
)


class Salle(Provenance, Base):
    """Salle de cours."""

    __tablename__ = "salle"
    __table_args__ = (
        UniqueConstraint("code", "campus_id"),
        CheckConstraint("capacite >= 0", name="capacite_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), comment="Code de la salle, ex. « N204 »")
    campus_id: Mapped[int] = mapped_column(
        ForeignKey("campus.id", ondelete="NO ACTION"), comment="Campus de la salle"
    )
    batiment: Mapped[str | None] = mapped_column(String(64), default=None, comment="Bâtiment")
    etage: Mapped[int | None] = mapped_column(Integer, default=None, comment="Étage (0 = RDC)")
    capacite: Mapped[int] = mapped_column(Integer, comment="Nombre de places")
    type_salle_id: Mapped[int] = mapped_column(
        ForeignKey("type_salle.id", ondelete="NO ACTION"), comment="Type de la salle"
    )
    accessibilite: Mapped[Accessibilite] = mapped_column(
        type_enum(Accessibilite, "accessibilite"),
        default=Accessibilite.INCONNU,
        comment="Accessible aux personnes à mobilité réduite : oui, non, inconnu",
    )

    campus: Mapped[Campus] = relationship()
    type_salle: Mapped[TypeSalle] = relationship()
    equipements: Mapped[list[Equipement]] = relationship(
        secondary=salle_equipement, passive_deletes=True, order_by="Equipement.code"
    )

    def __repr__(self) -> str:
        return f"<Salle {self.code}>"


# --------------------------------------------------------------------------- #
# Personnes et groupes
# --------------------------------------------------------------------------- #


class Enseignant(Provenance, Base):
    """Enseignant, identifié par un identifiant stable."""

    __tablename__ = "enseignant"
    __table_args__ = (UniqueConstraint("identifiant"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    identifiant: Mapped[str] = mapped_column(
        String(64), comment="Identifiant stable et unique (matricule), clé de réimport"
    )
    civilite: Mapped[str | None] = mapped_column(
        String(16), default=None, comment="« Mme », « M. »"
    )
    nom: Mapped[str] = mapped_column(String(128), comment="Nom de famille")
    prenom: Mapped[str | None] = mapped_column(String(128), default=None, comment="Prénom")
    statut: Mapped[StatutEnseignant] = mapped_column(
        type_enum(StatutEnseignant, "statut"),
        default=StatutEnseignant.PERMANENT,
        comment="Permanent, vacataire ou extérieur",
    )
    max_minutes_jour: Mapped[int | None] = mapped_column(
        Integer, default=None, comment="Maximum d'enseignement par jour, en minutes (facultatif)"
    )
    max_minutes_semaine: Mapped[int | None] = mapped_column(
        Integer, default=None, comment="Maximum d'enseignement par semaine, en minutes (facultatif)"
    )
    fictif: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Enseignant de remplacement d'une matière (enseignant inconnu à l'import) : "
        "n'entre dans aucune contrainte d'enseignant",
    )

    @property
    def nom_affiche(self) -> str:
        """Nom lisible : « Mme Martin » ou, sans civilité, « Claire Martin »."""
        if self.civilite:
            return f"{self.civilite} {self.nom}"
        return f"{self.prenom} {self.nom}" if self.prenom else self.nom

    def __repr__(self) -> str:
        return f"<Enseignant {self.identifiant}>"


class Groupe(Provenance, Base):
    """Groupe d'étudiants : nœud de l'arbre promotion > classe > demi-groupe, ou groupe à part.

    Les groupes transverses (langues, options) et tiers-temps n'ont en général
    pas de parent ; leurs recouvrements avec les classes viennent des
    chevauchements déclarés ou des étudiants inscrits.
    """

    __tablename__ = "groupe"
    __table_args__ = (
        UniqueConstraint("code", "annee_scolaire_id"),
        CheckConstraint("effectif >= 0", name="effectif_positif"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="pas_son_propre_parent"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    annee_scolaire_id: Mapped[int] = mapped_column(
        ForeignKey("annee_scolaire.id", ondelete="CASCADE"), comment="Année du groupe"
    )
    code: Mapped[str] = mapped_column(String(64), comment="Code unique dans l'année, ex. « A1-G2 »")
    libelle: Mapped[str] = mapped_column(String(128), comment="Libellé affiché")
    nature: Mapped[NatureGroupe] = mapped_column(
        type_enum(NatureGroupe, "nature"),
        comment="Promotion, classe, demi-groupe, transverse ou tiers-temps",
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("groupe.id", ondelete="CASCADE"),
        default=None,
        comment="Groupe contenant (vide pour une promotion ou un groupe à part)",
    )
    effectif: Mapped[int] = mapped_column(Integer, default=0, comment="Nombre d'étudiants")
    accessibilite_requise: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="Le groupe doit avoir cours en salle accessible"
    )

    parent: Mapped[Groupe | None] = relationship(remote_side="Groupe.id")
    annee: Mapped[AnneeScolaire] = relationship()

    def __repr__(self) -> str:
        return f"<Groupe {self.code}>"


groupe_chevauchement = Table(
    "groupe_chevauchement",
    Base.metadata,
    Column("groupe_a_id", ForeignKey("groupe.id", ondelete="CASCADE"), primary_key=True),
    Column("groupe_b_id", ForeignKey("groupe.id", ondelete="CASCADE"), primary_key=True),
    CheckConstraint("groupe_a_id < groupe_b_id", name="paire_ordonnee"),
    comment="Paires de groupes déclarés chevauchants (a < b) : ils partagent des étudiants",
)


inscription = Table(
    "inscription",
    Base.metadata,
    Column("etudiant_id", ForeignKey("etudiant.id", ondelete="CASCADE"), primary_key=True),
    Column("groupe_id", ForeignKey("groupe.id", ondelete="CASCADE"), primary_key=True),
    comment="Inscription d'un étudiant dans un groupe",
)


class Etudiant(Provenance, Base):
    """Étudiant (facultatif, fictif). Aucune donnée de santé ni donnée réelle nominative."""

    __tablename__ = "etudiant"
    __table_args__ = (UniqueConstraint("identifiant"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    identifiant: Mapped[str] = mapped_column(String(64), comment="Identifiant stable et unique")
    nom: Mapped[str] = mapped_column(String(128), comment="Nom (fictif)")
    prenom: Mapped[str | None] = mapped_column(String(128), default=None, comment="Prénom (fictif)")

    groupes: Mapped[list[Groupe]] = relationship(secondary=inscription, passive_deletes=True)

    def __repr__(self) -> str:
        return f"<Etudiant {self.identifiant}>"


# --------------------------------------------------------------------------- #
# Enseignements
# --------------------------------------------------------------------------- #


class Matiere(Provenance, Base):
    """Matière, identifiée par son code module et sa promotion de rattachement."""

    __tablename__ = "matiere"
    __table_args__ = (
        UniqueConstraint("code", "promotion_id"),
        # SQLite considère deux NULL comme distincts : unicité à part sans promotion.
        Index(
            "uq_matiere_code_sans_promotion",
            "code",
            unique=True,
            sqlite_where=text("promotion_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), comment="Code module, ex. « II.1102 »")
    libelle: Mapped[str] = mapped_column(String(128), comment="Libellé affiché")
    promotion_id: Mapped[int | None] = mapped_column(
        ForeignKey("groupe.id", ondelete="CASCADE"),
        default=None,
        comment="Promotion de rattachement (groupe de nature promotion)",
    )

    promotion: Mapped[Groupe | None] = relationship()

    def __repr__(self) -> str:
        return f"<Matiere {self.code}>"


class TypeSeance(Base):
    """Type de séance (CM, TD, TP...), liste configurable."""

    __tablename__ = "type_seance"
    __table_args__ = (UniqueConstraint("code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), comment="Code unique, ex. « TD »")
    libelle: Mapped[str] = mapped_column(String(128), comment="Libellé affiché")
    samedi_autorise: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="Ce type de séance peut avoir lieu le samedi matin"
    )
    type_salle_defaut_id: Mapped[int | None] = mapped_column(
        ForeignKey("type_salle.id", ondelete="SET NULL"),
        default=None,
        comment="Type de salle retenu quand le besoin n'en précise aucun",
    )
    couleur: Mapped[str | None] = mapped_column(
        String(7), default=None, comment="Couleur d'affichage, ex. « #2f6fad »"
    )

    type_salle_defaut: Mapped[TypeSalle | None] = relationship()

    def __repr__(self) -> str:
        return f"<TypeSeance {self.code}>"


class Indisponibilite(Provenance, Base):
    """Indisponibilité d'un enseignant, d'une salle ou d'un groupe.

    Deux formes exclusives :

    - **récurrente** : `jour` renseigné, la plage horaire s'applique chaque semaine ;
    - **datée** : `date_debut` et `date_fin` renseignées, la plage horaire
      s'applique chaque jour de la période (par défaut la journée entière).
    """

    __tablename__ = "indisponibilite"
    __table_args__ = (
        CheckConstraint(
            "(cible = 'enseignant' AND enseignant_id IS NOT NULL AND salle_id IS NULL AND groupe_id IS NULL)"
            " OR (cible = 'salle' AND salle_id IS NOT NULL AND enseignant_id IS NULL AND groupe_id IS NULL)"
            " OR (cible = 'groupe' AND groupe_id IS NOT NULL AND enseignant_id IS NULL AND salle_id IS NULL)",
            name="une_seule_cible",
        ),
        CheckConstraint(
            "(jour IS NOT NULL AND date_debut IS NULL AND date_fin IS NULL)"
            " OR (jour IS NULL AND date_debut IS NOT NULL AND date_fin IS NOT NULL"
            " AND date_debut <= date_fin)",
            name="recurrente_ou_datee",
        ),
        CheckConstraint("jour IS NULL OR jour BETWEEN 0 AND 6", name="jour_valide"),
        CheckConstraint("debut_min >= 0 AND debut_min < fin_min AND fin_min <= 1440", name="plage"),
        Index("ix_indisponibilite_enseignant_id", "enseignant_id"),
        Index("ix_indisponibilite_salle_id", "salle_id"),
        Index("ix_indisponibilite_groupe_id", "groupe_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cible: Mapped[CibleIndisponibilite] = mapped_column(
        type_enum(CibleIndisponibilite, "cible"), comment="Enseignant, salle ou groupe"
    )
    enseignant_id: Mapped[int | None] = mapped_column(
        ForeignKey("enseignant.id", ondelete="CASCADE"), default=None
    )
    salle_id: Mapped[int | None] = mapped_column(
        ForeignKey("salle.id", ondelete="CASCADE"), default=None
    )
    groupe_id: Mapped[int | None] = mapped_column(
        ForeignKey("groupe.id", ondelete="CASCADE"), default=None
    )
    jour: Mapped[int | None] = mapped_column(
        Integer, default=None, comment="Forme récurrente : jour de la semaine (0 = lundi)"
    )
    date_debut: Mapped[dt.date | None] = mapped_column(
        Date, default=None, comment="Forme datée : premier jour"
    )
    date_fin: Mapped[dt.date | None] = mapped_column(
        Date, default=None, comment="Forme datée : dernier jour (inclus)"
    )
    debut_min: Mapped[int] = mapped_column(
        Integer, default=0, comment="Début de la plage horaire (défaut : 0 = minuit)"
    )
    fin_min: Mapped[int] = mapped_column(
        Integer, default=1440, comment="Fin de la plage horaire (défaut : 1440 = journée entière)"
    )
    motif: Mapped[str | None] = mapped_column(String(255), default=None, comment="Motif affiché")
    dure: Mapped[bool] = mapped_column(
        Boolean, default=True, comment="Contrainte dure (toujours vrai en V1)"
    )
    poids: Mapped[int] = mapped_column(
        Integer, default=0, comment="Poids de la préférence (prévu pour les versions suivantes)"
    )

    enseignant: Mapped[Enseignant | None] = relationship()
    salle: Mapped[Salle | None] = relationship()
    groupe: Mapped[Groupe | None] = relationship()

    @property
    def ressource_id(self) -> int:
        """Identifiant de la ressource visée, quel que soit son type."""
        valeur = {
            CibleIndisponibilite.ENSEIGNANT: self.enseignant_id,
            CibleIndisponibilite.SALLE: self.salle_id,
            CibleIndisponibilite.GROUPE: self.groupe_id,
        }[CibleIndisponibilite(self.cible)]
        if valeur is None:
            raise ErreurDonneesInvalides(
                f"Indisponibilité {self.id} : la ressource visée ({self.cible}) n'est pas renseignée."
            )
        return valeur


besoin_groupe = Table(
    "besoin_groupe",
    Base.metadata,
    Column("besoin_id", ForeignKey("besoin.id", ondelete="CASCADE"), primary_key=True),
    Column("groupe_id", ForeignKey("groupe.id", ondelete="NO ACTION"), primary_key=True),
    comment="Groupes qui suivent la séance d'un besoin (1 à n)",
)

besoin_enseignant = Table(
    "besoin_enseignant",
    Base.metadata,
    Column("besoin_id", ForeignKey("besoin.id", ondelete="CASCADE"), primary_key=True),
    Column("enseignant_id", ForeignKey("enseignant.id", ondelete="NO ACTION"), primary_key=True),
    comment="Enseignants qui assurent la séance d'un besoin (0 à n)",
)

besoin_type_salle = Table(
    "besoin_type_salle",
    Base.metadata,
    Column("besoin_id", ForeignKey("besoin.id", ondelete="CASCADE"), primary_key=True),
    Column("type_salle_id", ForeignKey("type_salle.id", ondelete="NO ACTION"), primary_key=True),
    comment="Types de salle acceptés par un besoin (aucun = type par défaut du type de séance)",
)

besoin_equipement = Table(
    "besoin_equipement",
    Base.metadata,
    Column("besoin_id", ForeignKey("besoin.id", ondelete="CASCADE"), primary_key=True),
    Column("equipement_id", ForeignKey("equipement.id", ondelete="NO ACTION"), primary_key=True),
    comment="Équipements requis par un besoin",
)


class Besoin(Provenance, Base):
    """Besoin : la demande d'enseignement, entrée du générateur.

    Le volume s'exprime selon `mode_volume` :

    - `par_semaine` : `nombre` séances chaque semaine active (fenêtre
      facultative `date_debut`..`date_fin`, par défaut tout le semestre) ;
    - `total` : `nombre` séances à répartir régulièrement dans la fenêtre,
      au plus `max_par_semaine` par semaine ;
    - `ponctuel` : une séance datée (`date_ponctuelle`), saisie à la main.
    """

    __tablename__ = "besoin"
    __table_args__ = (
        CheckConstraint("duree_min > 0 AND duree_min <= 1440", name="duree_valide"),
        CheckConstraint("nombre >= 1", name="nombre_positif"),
        CheckConstraint("max_par_semaine >= 1", name="max_par_semaine_positif"),
        CheckConstraint(
            "date_debut IS NULL OR date_fin IS NULL OR date_debut <= date_fin",
            name="fenetre_ordonnee",
        ),
        CheckConstraint(
            "(mode_volume = 'ponctuel') = (date_ponctuelle IS NOT NULL)",
            name="date_ponctuelle_si_ponctuel",
        ),
        CheckConstraint("mode_volume <> 'ponctuel' OR nombre = 1", name="ponctuel_unique"),
        CheckConstraint("jour_impose IS NULL OR jour_impose BETWEEN 0 AND 6", name="jour_valide"),
        CheckConstraint(
            "debut_impose_min IS NULL OR debut_impose_min BETWEEN 0 AND 1439", name="debut_valide"
        ),
        Index("ix_besoin_semestre_id", "semestre_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    semestre_id: Mapped[int] = mapped_column(
        ForeignKey("semestre.id", ondelete="CASCADE"), comment="Semestre du besoin"
    )
    matiere_id: Mapped[int] = mapped_column(
        ForeignKey("matiere.id", ondelete="NO ACTION"), comment="Matière enseignée"
    )
    type_seance_id: Mapped[int] = mapped_column(
        ForeignKey("type_seance.id", ondelete="NO ACTION"), comment="Type de séance"
    )
    libelle: Mapped[str | None] = mapped_column(
        String(128), default=None, comment="Précision facultative, ex. « Partiel »"
    )
    duree_min: Mapped[int] = mapped_column(Integer, default=120, comment="Durée d'une séance")
    mode_volume: Mapped[ModeVolume] = mapped_column(
        type_enum(ModeVolume, "mode_volume"),
        default=ModeVolume.PAR_SEMAINE,
        comment="par_semaine, total ou ponctuel",
    )
    nombre: Mapped[int] = mapped_column(
        Integer,
        default=1,
        comment="Séances par semaine (par_semaine), total de séances (total), 1 (ponctuel)",
    )
    max_par_semaine: Mapped[int] = mapped_column(
        Integer, default=1, comment="Mode total : maximum de séances par semaine"
    )
    date_debut: Mapped[dt.date | None] = mapped_column(
        Date, default=None, comment="Début de la fenêtre (vide = début du semestre)"
    )
    date_fin: Mapped[dt.date | None] = mapped_column(
        Date, default=None, comment="Fin de la fenêtre (vide = fin du semestre)"
    )
    date_ponctuelle: Mapped[dt.date | None] = mapped_column(
        Date, default=None, comment="Mode ponctuel : date de la séance"
    )
    jour_impose: Mapped[int | None] = mapped_column(
        Integer, default=None, comment="Créneau imposé : jour (0 = lundi), facultatif"
    )
    debut_impose_min: Mapped[int | None] = mapped_column(
        Integer, default=None, comment="Créneau imposé : heure de début, facultative"
    )
    salle_imposee_id: Mapped[int | None] = mapped_column(
        ForeignKey("salle.id", ondelete="NO ACTION"), default=None, comment="Salle imposée"
    )
    campus_impose_id: Mapped[int | None] = mapped_column(
        ForeignKey("campus.id", ondelete="NO ACTION"), default=None, comment="Campus imposé"
    )
    commentaire: Mapped[str | None] = mapped_column(Text, default=None, comment="Note libre")

    semestre: Mapped[Semestre] = relationship()
    matiere: Mapped[Matiere] = relationship()
    type_seance: Mapped[TypeSeance] = relationship()
    salle_imposee: Mapped[Salle | None] = relationship()
    campus_impose: Mapped[Campus | None] = relationship()
    groupes: Mapped[list[Groupe]] = relationship(
        secondary=besoin_groupe, passive_deletes=True, order_by="Groupe.code"
    )
    enseignants: Mapped[list[Enseignant]] = relationship(
        secondary=besoin_enseignant, passive_deletes=True, order_by="Enseignant.nom"
    )
    types_salle: Mapped[list[TypeSalle]] = relationship(
        secondary=besoin_type_salle, passive_deletes=True, order_by="TypeSalle.code"
    )
    equipements: Mapped[list[Equipement]] = relationship(
        secondary=besoin_equipement, passive_deletes=True, order_by="Equipement.code"
    )

    def __repr__(self) -> str:
        return f"<Besoin {self.id} {self.mode_volume}>"


# --------------------------------------------------------------------------- #
# Emplois du temps
# --------------------------------------------------------------------------- #


class Calcul(Base):
    """Suivi d'une génération (voir `edt.services.generation`)."""

    __tablename__ = "calcul"
    __table_args__ = (Index("ix_calcul_semestre_id", "semestre_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    semestre_id: Mapped[int] = mapped_column(
        ForeignKey("semestre.id", ondelete="CASCADE"), comment="Semestre calculé"
    )
    etat: Mapped[EtatCalcul] = mapped_column(
        type_enum(EtatCalcul, "etat"), default=EtatCalcul.EN_COURS, comment="État du calcul"
    )
    demarre_le: Mapped[dt.datetime] = mapped_column(DateTime, comment="Début (heure locale)")
    termine_le: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None, comment="Fin")
    duree_s: Mapped[float | None] = mapped_column(Float, default=None, comment="Durée en secondes")
    parametres: Mapped[dict] = mapped_column(
        JSON, default=dict, comment="Options du calcul (temps limite, graine, objectif...)"
    )
    contraintes_desactivees: Mapped[list] = mapped_column(
        JSON, default=list, comment="Codes des contraintes désactivées par l'utilisateur"
    )
    statistiques: Mapped[dict] = mapped_column(
        JSON, default=dict, comment="Mesures renvoyées par le moteur"
    )
    message: Mapped[str] = mapped_column(Text, default="", comment="Message lisible du résultat")
    fichier_journal: Mapped[str | None] = mapped_column(
        String(512), default=None, comment="Chemin du journal dédié du calcul"
    )
    arret_demande: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="L'utilisateur a demandé l'arrêt"
    )

    semestre: Mapped[Semestre] = relationship()


class VersionEdt(Base):
    """Version d'emploi du temps d'un semestre (générée ou importée comme référence).

    Une seule version est active par semestre (index unique partiel).
    """

    __tablename__ = "version_edt"
    __table_args__ = (
        UniqueConstraint("semestre_id", "numero"),
        Index(
            "uq_version_edt_active_par_semestre",
            "semestre_id",
            unique=True,
            sqlite_where=text("active = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    semestre_id: Mapped[int] = mapped_column(
        ForeignKey("semestre.id", ondelete="CASCADE"), comment="Semestre de la version"
    )
    numero: Mapped[int] = mapped_column(Integer, comment="Numéro d'ordre dans le semestre, dès 1")
    libelle: Mapped[str] = mapped_column(String(128), comment="Libellé affiché")
    origine: Mapped[OrigineVersion] = mapped_column(
        type_enum(OrigineVersion, "origine"), comment="Générée ou référence (import)"
    )
    active: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="Version active du semestre (une seule)"
    )
    cree_le: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.now, comment="Date de création (heure locale)"
    )
    calcul_id: Mapped[int | None] = mapped_column(
        ForeignKey("calcul.id", ondelete="SET NULL"),
        default=None,
        comment="Calcul qui a produit la version (vide pour un import)",
    )
    commentaire: Mapped[str | None] = mapped_column(Text, default=None, comment="Note libre")

    semestre: Mapped[Semestre] = relationship()
    calcul: Mapped[Calcul | None] = relationship()
    seances: Mapped[list[SeanceHebdo]] = relationship(
        back_populates="version", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<VersionEdt {self.id} n°{self.numero}>"


class SeanceHebdo(Provenance, Base):
    """Séance hebdomadaire : élément de la semaine type d'une version.

    `(besoin_id, rang)` identifie la séance d'une version à l'autre ; c'est
    la clé qui relie une séance placée à la séance à placer de l'instantané.
    """

    __tablename__ = "seance_hebdo"
    __table_args__ = (
        UniqueConstraint("version_id", "besoin_id", "rang"),
        CheckConstraint("jour BETWEEN 0 AND 6", name="jour_valide"),
        CheckConstraint("debut_min >= 0 AND debut_min < fin_min AND fin_min <= 1440", name="plage"),
        CheckConstraint("rang >= 1", name="rang_positif"),
        Index("ix_seance_hebdo_besoin_id", "besoin_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("version_edt.id", ondelete="CASCADE"), comment="Version propriétaire"
    )
    besoin_id: Mapped[int] = mapped_column(
        ForeignKey("besoin.id", ondelete="CASCADE"), comment="Besoin satisfait par la séance"
    )
    rang: Mapped[int] = mapped_column(
        Integer, default=1, comment="Rang de la séance parmi celles du besoin, dès 1"
    )
    jour: Mapped[int] = mapped_column(Integer, comment="0 = lundi ... 6 = dimanche")
    debut_min: Mapped[int] = mapped_column(Integer, comment="Début, en minutes depuis minuit")
    fin_min: Mapped[int] = mapped_column(Integer, comment="Fin, en minutes depuis minuit")
    salle_id: Mapped[int | None] = mapped_column(
        ForeignKey("salle.id", ondelete="SET NULL"),
        default=None,
        comment="Salle affectée (vide = salle à définir)",
    )
    verrou: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="Séance verrouillée : le calcul ne la déplace pas"
    )
    semaines: Mapped[list] = mapped_column(
        JSON,
        default=list,
        comment="Semaines actives : numéros relatifs au semestre (1 = première semaine)",
    )

    version: Mapped[VersionEdt] = relationship(back_populates="seances")
    besoin: Mapped[Besoin] = relationship()
    salle: Mapped[Salle | None] = relationship()


class Occurrence(Provenance, Base):
    """Séance datée, issue de la semaine type, d'un replacement, d'une saisie ou d'un import."""

    __tablename__ = "occurrence"
    __table_args__ = (
        CheckConstraint("debut_min >= 0 AND debut_min < fin_min AND fin_min <= 1440", name="plage"),
        Index("ix_occurrence_version_id_date", "version_id", "date"),
        Index("ix_occurrence_besoin_id", "besoin_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("version_edt.id", ondelete="CASCADE"), comment="Version propriétaire"
    )
    seance_hebdo_id: Mapped[int | None] = mapped_column(
        ForeignKey("seance_hebdo.id", ondelete="CASCADE"),
        default=None,
        comment="Séance hebdomadaire d'origine (vide pour une saisie ou un import daté)",
    )
    besoin_id: Mapped[int] = mapped_column(
        ForeignKey("besoin.id", ondelete="CASCADE"), comment="Besoin satisfait par la séance"
    )
    date: Mapped[dt.date] = mapped_column(Date, comment="Date de la séance (sans fuseau)")
    debut_min: Mapped[int] = mapped_column(Integer, comment="Début, en minutes depuis minuit")
    fin_min: Mapped[int] = mapped_column(Integer, comment="Fin, en minutes depuis minuit")
    salle_id: Mapped[int | None] = mapped_column(
        ForeignKey("salle.id", ondelete="SET NULL"), default=None, comment="Salle affectée"
    )
    statut: Mapped[StatutOccurrence] = mapped_column(
        type_enum(StatutOccurrence, "statut"),
        default=StatutOccurrence.PLANIFIEE,
        comment="Planifiée, annulée, déplacée ou à programmer",
    )
    origine: Mapped[OrigineOccurrence] = mapped_column(
        type_enum(OrigineOccurrence, "origine"),
        default=OrigineOccurrence.INSTANCIATION,
        comment="Semaine type, replacement, saisie manuelle ou import",
    )
    date_initiale: Mapped[dt.date | None] = mapped_column(
        Date, default=None, comment="Date prévue par la semaine type avant déplacement"
    )
    commentaire: Mapped[str | None] = mapped_column(Text, default=None, comment="Note libre")

    version: Mapped[VersionEdt] = relationship()
    seance_hebdo: Mapped[SeanceHebdo | None] = relationship()
    besoin: Mapped[Besoin] = relationship()
    salle: Mapped[Salle | None] = relationship()


# --------------------------------------------------------------------------- #
# Réglages
# --------------------------------------------------------------------------- #


class Parametre(Base):
    """Paramètre global (clé / valeur JSON). Voir `edt.domaine.parametres`."""

    __tablename__ = "parametre"

    cle: Mapped[str] = mapped_column(String(64), primary_key=True, comment="Nom du paramètre")
    valeur: Mapped[int | bool | str] = mapped_column(
        JSON, comment="Valeur, typée selon le paramètre : entier, booléen ou chaîne"
    )
    modifie_le: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.now, onupdate=dt.datetime.now, comment="Dernière modification"
    )


MODELES: tuple[type[Base], ...] = (
    AnneeScolaire,
    Semestre,
    JourFerme,
    Creneau,
    Campus,
    Trajet,
    TypeSalle,
    Equipement,
    Salle,
    Enseignant,
    Groupe,
    Etudiant,
    Matiere,
    TypeSeance,
    Indisponibilite,
    Besoin,
    Calcul,
    VersionEdt,
    SeanceHebdo,
    Occurrence,
    Parametre,
)
"""Tous les modèles à table propre (les tables d'association sont dans `Base.metadata`)."""
