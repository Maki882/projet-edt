"""Base déclarative SQLAlchemy et convention de nommage des contraintes."""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

CONVENTION_NOMMAGE = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
"""Noms explicites des contraintes : les messages d'erreur de SQLite deviennent lisibles."""


class Base(DeclarativeBase):
    """Classe mère de tous les modèles ORM."""

    metadata = MetaData(naming_convention=CONVENTION_NOMMAGE)
