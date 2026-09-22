"""Declarative base of the private pseudonym map (SCRUM-98, sub-phase 5).

A third registry, separate from the operational one and from the star schema,
for the same reason those two are separate: importing it must not add a table to
anybody else's metadata, and the tests that pin the operational schema to its 23
tables keep describing exactly what they always described.

What lives here is the map, and only the map: which UUID corresponds to which
patient and to which pregnancy. Two tables, no dimensions, no facts. The
analytic surfaces that *use* the map are views in the ``publicacion`` schema and
are not declared as models -- Alembic's autogenerate does not compare views, and
declaring them would invite somebody to treat a published surface as a table to
write into.

**This schema is the reason the published surfaces are pseudonymous rather than
anonymous.** The correspondence exists and is stable; that is what makes a
pregnancy followable over time, and it is also what makes the data
re-identifiable to whoever can read these two tables. The protection is who may
read them, not the impossibility of reversing them.

All identifiers in this project belong to simulated, completely fictitious
people.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

from app.db.base import NAMING_CONVENTION

SCHEMA_PRIVADO = "privado"

metadata_privada = MetaData(
    schema=SCHEMA_PRIVADO,
    naming_convention=NAMING_CONVENTION,
)


class BasePrivada(DeclarativeBase):
    """Declarative registry of the two tables of the pseudonym map."""

    metadata = metadata_privada
