"""Durable claim of an ``Idempotency-Key``, so a resend is recognised (SCRUM-63).

This table is the whole mechanism. Nothing in Python decides whether a package
has already been received: the ``UNIQUE (recurso, clave)`` constraint does, and
it is the only thing that does. A dictionary in memory, a lock or a previous
``SELECT`` would all be races; PostgreSQL is the authority.

**What a row means.** «This exact package was already processed, and this was
the answer». It is written in the *same* transaction that writes the session and
its readings, and it becomes visible only when that transaction commits, so a
visible row always describes a completed operation. That is also what keeps the
key from being poisoned: any failure rolls the claim back with everything else,
and the key is free again.

**Why the result columns are nullable.** The claim is taken *before* the session
exists, which is what makes «the key was claimed and then the insert failed» a
reachable -- and therefore testable -- situation. ``id_sesion`` and
``ids_lectura`` are filled in by an ``UPDATE`` later in that same transaction.
PostgreSQL 16 has no deferrable ``NOT NULL`` or ``CHECK``, so «a committed row is
complete» is an invariant of the calling code, not of the schema. What the schema
*does* enforce is that the two never disagree: either both are absent or both are
present, never half a result.

**Why ``ids_lectura`` is stored instead of recomputed.** The response returns the
identifiers in the order the package listed its readings. Rebuilding that with
``ORDER BY id_lectura`` would rely on insertion order matching sequence order --
true today, but a property of the current code rather than of the schema. Storing
the list keeps a replay identical to the first answer by construction.
``lecturas_creadas`` is deliberately **not** a column: it is ``len(ids_lectura)``,
and a second copy could only ever disagree with the first.

**Why ``ON DELETE RESTRICT``.** With ``CASCADE``, deleting a session would
silently free the key that identified it, and the next resend would be treated as
a first submission and create a second session -- the exact opposite of what this
table exists for. ``RESTRICT`` makes PostgreSQL refuse instead, so the claim
outlives anything but a deliberate removal. This is not a retention policy: no
expiry, no purge, no cleanup job is defined anywhere.

This table holds no clinical data: a resource name, an opaque client-supplied
key, a hash and identifiers. All of it belongs to fictitious simulated data.
"""

from datetime import datetime

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Endpoint the key is scoped to. It travels as data rather than as an implicit
# «everything shares one namespace», so a future write endpoint can reuse the
# same table without its keys colliding with these.
LONGITUD_RECURSO = 80

# Upper bound of the client-supplied key. A UUID takes 36 characters and a
# base64url token of 32 bytes takes 43, so 128 leaves room without letting a
# caller store an arbitrarily long string.
LONGITUD_CLAVE = 128

# SHA-256 in hexadecimal: always exactly 64 characters.
LONGITUD_HUELLA = 64


class IdempotenciaSolicitud(Base):
    """One claimed ``Idempotency-Key`` and the answer it is bound to."""

    __tablename__ = "idempotencia_solicitud"
    __table_args__ = (
        # The guarantee. Unnamed on purpose: the naming convention derives
        # ``uq_idempotencia_solicitud_recurso_clave`` from the columns, so the
        # name cannot drift from what it constrains.
        UniqueConstraint("recurso", "clave"),
        CheckConstraint(
            "(id_sesion IS NULL AND ids_lectura IS NULL) "
            "OR (id_sesion IS NOT NULL AND ids_lectura IS NOT NULL "
            "AND cardinality(ids_lectura) > 0)",
            name="resultado_completo",
        ),
    )

    id_idempotencia: Mapped[int] = mapped_column(Integer, primary_key=True)
    recurso: Mapped[str] = mapped_column(String(LONGITUD_RECURSO), nullable=False)
    clave: Mapped[str] = mapped_column(String(LONGITUD_CLAVE), nullable=False)
    huella: Mapped[str] = mapped_column(String(LONGITUD_HUELLA), nullable=False)
    # No index of its own: this column is never a search key. Every lookup goes
    # through (recurso, clave), which the UNIQUE constraint already indexes.
    id_sesion: Mapped[int | None] = mapped_column(
        ForeignKey("sesion_monitoreo.id_sesion", ondelete="RESTRICT"),
        nullable=True,
    )
    ids_lectura: Mapped[list[int] | None] = mapped_column(
        ARRAY(Integer),
        nullable=True,
    )
    fecha_hora: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
