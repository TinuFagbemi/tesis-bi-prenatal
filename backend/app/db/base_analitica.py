"""Declarative base and metadata of the FetalAlert analytic schema (SCRUM-69).

The operational model owns :mod:`app.db.base`: one registry bound to the
``operacional`` schema, pinned by the tests to its 23 tables. The star schema
lives in this second, separate registry so neither contract leaks into the
other -- importing the analytic models never adds a table to ``Base.metadata``,
and the operational tests keep describing exactly what they always described.

Both registries share one naming convention, imported rather than copied, so
every constraint in the database is named by the same rule.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

from app.db.base import NAMING_CONVENTION

SCHEMA_ANALITICO = "analitico"

metadata_analitico = MetaData(
    schema=SCHEMA_ANALITICO,
    naming_convention=NAMING_CONVENTION,
)


class BaseAnalitica(DeclarativeBase):
    """Declarative registry of the nine structures of the star schema."""

    metadata = metadata_analitico
