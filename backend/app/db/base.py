"""Declarative base and metadata conventions for the FetalAlert operational model.

Every ORM model in ``app.models`` inherits from :class:`Base`, so a single
declarative registry backs the whole application. The metadata is bound to the
PostgreSQL ``operacional`` schema and carries a deterministic naming convention
so Alembic can autogenerate and drop constraints by name.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

SCHEMA_OPERACIONAL = "operacional"

# Deterministic constraint names. Without these, PostgreSQL invents names for
# unnamed CHECK/UNIQUE constraints and Alembic cannot reference them on downgrade.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata_operacional = MetaData(
    schema=SCHEMA_OPERACIONAL,
    naming_convention=NAMING_CONVENTION,
)


class Base(DeclarativeBase):
    """Single declarative registry shared by every FetalAlert ORM model."""

    metadata = metadata_operacional
