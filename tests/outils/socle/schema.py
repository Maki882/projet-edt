"""Aides du lot socle pour simuler un schéma périmé (base créée par une ancienne version du code)."""

from __future__ import annotations

from sqlalchemy import UniqueConstraint
from sqlalchemy.schema import CreateIndex

from edt.db.base import Base
from edt.db.moteur import obtenir_moteur

COLONNE_EN_TROP = "table besoin : colonne en trop : ancien_champ"
COLONNE_ABSENTE = "table salle : colonne etage absente de la base"


def ajouter_colonne(table: str, definition: str) -> None:
    """Simule une colonne retirée du modèle : la base en a une de plus."""
    with obtenir_moteur().begin() as connexion:
        connexion.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {definition}")


def supprimer_table(table: str) -> None:
    with obtenir_moteur().begin() as connexion:
        connexion.exec_driver_sql(f"DROP TABLE {table}")


def recreer_table(
    nom: str, *, sans: str | None = None, definitions: dict[str, str] | None = None
) -> None:
    """Recrée une table (vide) comme l'aurait créée une ancienne version du modèle.

    `sans` retire une colonne (elle a été ajoutée au modèle depuis) ;
    `definitions` remplace la définition SQL de certaines colonnes.
    """
    moteur = obtenir_moteur()
    modele = Base.metadata.tables[nom]
    colonnes = []
    for colonne in modele.columns:
        if colonne.name == sans:
            continue
        definition = (definitions or {}).get(colonne.name)
        if definition is None:
            definition = colonne.type.compile(dialect=moteur.dialect)
            definition += "" if colonne.nullable else " NOT NULL"
        colonnes.append(f"{colonne.name} {definition}")
    cle = ", ".join(colonne.name for colonne in modele.primary_key.columns)
    colonnes.append(f"PRIMARY KEY ({cle})")
    # Unicités et index du modèle conservés : ces tests ne portent que sur les colonnes.
    for contrainte in modele.constraints:
        if isinstance(contrainte, UniqueConstraint):
            noms = ", ".join(c.name for c in contrainte.columns if c.name != sans)
            if noms:
                colonnes.append(f"CONSTRAINT {contrainte.name} UNIQUE ({noms})")
    with moteur.begin() as connexion:
        connexion.exec_driver_sql(f"DROP TABLE {nom}")
        # Une définition par ligne, comme dans les tables créées par SQLAlchemy.
        lignes = ",\n".join(colonnes)
        connexion.exec_driver_sql(f"CREATE TABLE {nom} (\n{lignes}\n)")
        for index in modele.indexes:
            connexion.exec_driver_sql(str(CreateIndex(index).compile(dialect=moteur.dialect)))
