"""The pseudonym map: operational key -> stable random UUID (SCRUM-98).

Two tables with the same shape, and the shape is the whole argument.

**The UUID is random, not derived.** ``gen_random_uuid()`` produces it; nothing
hashes ``id_paciente``. A hash of a sequential key is reversible by trying the
integers -- there are thirty patients in the simulated dataset and a deployment
would have thousands, which is nothing -- so a derived pseudonym would have made
these tables decorative. The map exists precisely because the correspondence has
to be *stored* rather than computable.

**The UUID is stable.** The ETL inserts one the first time it sees a row and
reuses it on every later run. That stability is what lets a published surface
follow one pregnancy across months, and it is also why this is pseudonymisation
and not anonymisation: the link persists, so the data stays re-identifiable to
whoever holds the map.

**Nothing updates or deletes a pseudonym.** The ETL is granted SELECT and INSERT
and nothing else: changing a pseudonym already issued would split that
pregnancy's published series in two, and no ETL operation should be able to do
that. Maintenance does have the full set, because restoring a backup is what a
restore is -- and a backup that does not bring these two tables back leaves every
published surface without its longitudinality.

``ON DELETE RESTRICT`` on both foreign keys: a patient whose pseudonym exists
cannot be deleted out from under the published data.

All the people these rows point at are simulated and completely fictitious.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_privada import BasePrivada

# El generador vive en el DEFAULT de la columna y no en Python a proposito: asi
# el seudonimo lo emite PostgreSQL en la misma sentencia que inserta la fila, y
# no hay un camino en el que un proceso escriba uno calculado por su cuenta.
SEUDONIMO_ALEATORIO = text("gen_random_uuid()")
AHORA = text("now()")


class SeudonimoPaciente(BasePrivada):
    """Una paciente, un seudonimo estable."""

    __tablename__ = "seudonimo_paciente"

    id_paciente: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("operacional.paciente.id_paciente", ondelete="RESTRICT"),
        primary_key=True,
    )
    seudonimo: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        nullable=False,
        unique=True,
        server_default=SEUDONIMO_ALEATORIO,
    )
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=AHORA
    )


class SeudonimoEmbarazo(BasePrivada):
    """Un episodio, un seudonimo estable.

    Uno por embarazo y no uno por paciente: asi la superficie publicada conserva
    la separacion entre episodios que las rutas clinicas ya respetan, y un
    analisis no puede encadenar dos embarazos de la misma persona salvo por el
    seudonimo de paciente, que se publica aparte y a proposito.
    """

    __tablename__ = "seudonimo_embarazo"

    id_embarazo: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("operacional.embarazo.id_embarazo", ondelete="RESTRICT"),
        primary_key=True,
    )
    seudonimo: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        nullable=False,
        unique=True,
        server_default=SEUDONIMO_ALEATORIO,
    )
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=AHORA
    )
