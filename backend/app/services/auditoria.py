"""The audit trail of access and relevant actions (SCRUM-70).

**What this is, precisely.** ``operacional.auditoria_log`` is *append-only by
design of the application*: this module only ever inserts, never updates and
never deletes, and the foreign key is ``ON DELETE RESTRICT``, so an account that
produced entries cannot be removed -- only deactivated. It provides
**traceability and technical attribution**. It is **not** cryptographic
immutability and **not** non-repudiation: anybody with administrative
privileges on PostgreSQL can alter the table, and nothing here pretends
otherwise.

**The catalogue is closed.** Four actions, listed in :class:`AccionAuditada`.
Deliberately absent:

* a row per rejected token -- the bearer of an invalid token has no identified
  actor, and the endpoint is reachable without authenticating, so persisting one
  would hand anybody an unauthenticated write into the audit table. Rejections
  reach the sanitised application log instead.
* a row per idempotent replay -- no business row is created, and the replay path
  rolls its transaction back by contract.

**The transaction boundary is not uniform, and it cannot be.** Two shapes:

* :func:`registrar` writes into a transaction **somebody else owns** and commits
  nothing. It is what the ingestion router uses, so the audit entry and the
  session it describes are confirmed together or not at all. A business rollback
  therefore erases the success entry along with the rows it claimed -- there is
  no second transaction where a false success could survive.
* :func:`registrar_con_commit` owns a short transaction of its own. Logins and
  role denials need it: the first has no business transaction to join, and the
  second happens on a path where the business handler never runs, so an entry
  depending on a commit that will never come would simply be lost.

**Failure is closed.** ``registrar_con_commit`` raises
:class:`FalloDeAuditoria` rather than swallowing anything, and the routers turn
that into a refusal. An access granted without a trace is the outcome this
ticket exists to prevent.

**What never reaches a row.** No password, digest, signing secret, token, whole
or partial, ``Authorization`` header, decoded payload, ``Idempotency-Key``,
package fingerprint, national id, phone, email, patient or physician name,
biometric value, clinical body, connection URL or SQL. What is written is an
internal identifier, a stable action code, an entity name, an entity id, an
origin address and an instant.
"""

from __future__ import annotations

import enum
import logging
from datetime import datetime

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.seguridad import AuditoriaLog
from app.services.tokens import ahora_utc

registrador = logging.getLogger(__name__)


class AccionAuditada(str, enum.Enum):
    """Every action this system records. The column is free text; this is not.

    ``auditoria_log.accion`` carries no CHECK constraint, so nothing in
    PostgreSQL stops a typo. Declaring the vocabulary here is what keeps the
    written codes stable and greppable, and what lets a test assert the closed
    set.

    The outcome travels **inside** the code because the table has no
    ``resultado`` column: ``LOGIN_EXITOSO`` and ``LOGIN_FALLIDO`` are two codes,
    not one code with a flag.
    """

    LOGIN_EXITOSO = "LOGIN_EXITOSO"
    LOGIN_FALLIDO = "LOGIN_FALLIDO"
    ACCESO_DENEGADO_ROL = "ACCESO_DENEGADO_ROL"
    SESION_MONITOREO_REGISTRADA = "SESION_MONITOREO_REGISTRADA"


# Physical names of the entities an entry can point at. Table names, not HTTP
# routes: ``id_entidad_afectada`` is the identifier of a row, so the column next
# to it has to name something rows belong to. A route would make the pair
# meaningless.
ENTIDAD_USUARIO = "usuario"
ENTIDAD_SESION_MONITOREO = "sesion_monitoreo"

# ``ip_origen`` is NOT NULL, so there is always a value. This is the one used
# when the server cannot observe a client address: a fixed literal written here,
# never anything derived from the request.
IP_DESCONOCIDA = "desconocida"

# Width of the column, read from it rather than repeated.
LONGITUD_MAXIMA_IP = AuditoriaLog.__table__.c.ip_origen.type.length

MENSAJE_FALLO_DE_AUDITORIA = (
    "No fue posible registrar el evento de auditoria; la operacion no se completa."
)


class FalloDeAuditoria(Exception):
    """The trail could not be written, so the operation must not be confirmed.

    Carries the fixed message above. The database error is chained for the log
    and never published.
    """

    def __init__(self) -> None:
        super().__init__(MENSAJE_FALLO_DE_AUDITORIA)
        self.detalle = MENSAJE_FALLO_DE_AUDITORIA


def direccion_de_origen(host: str | None) -> str:
    """The address to record, or the fixed literal.

    ``host`` is whatever Starlette observed -- ``request.client.host`` -- or
    ``None`` when it observed nothing, which happens behind some transports and
    in the test client. A value that does not fit the column is replaced rather
    than truncated: half an address is not an address, and a silently cut string
    would look like a real one to whoever reads the trail later.

    ``X-Forwarded-For`` is **not** consulted. There is no trusted proxy in this
    MVP, so that header is a value the caller chooses, and recording a
    caller-chosen origin as if it were observed would make the column worse than
    useless.
    """
    if not host or len(host) > LONGITUD_MAXIMA_IP:
        return IP_DESCONOCIDA
    return host


def registrar(
    sesion_bd: Session,
    accion: AccionAuditada,
    *,
    id_usuario: int | None,
    ip_origen: str,
    nombre_entidad: str | None = None,
    id_entidad: str | None = None,
    momento: datetime | None = None,
) -> AuditoriaLog:
    """Add one entry to the caller's transaction. **No commit, no rollback.**

    Used where the entry must share the fate of the operation it describes. The
    row is flushed so a constraint violation surfaces here, inside the caller's
    ``try``, instead of at a commit the caller has already decided to make.

    ``id_usuario`` may be ``None``: the column is nullable precisely so a failed
    login by an unknown account can be recorded without inventing an actor and
    without storing the identifier that was typed.
    """
    entrada = AuditoriaLog(
        id_usuario=id_usuario,
        accion=accion.value,
        nombre_entidad_afectada=nombre_entidad,
        id_entidad_afectada=id_entidad,
        ip_origen=ip_origen,
        fecha_hora=momento if momento is not None else ahora_utc(),
    )
    sesion_bd.add(entrada)
    sesion_bd.flush()
    return entrada


def registrar_con_commit(
    sesion_bd: Session,
    accion: AccionAuditada,
    *,
    id_usuario: int | None,
    ip_origen: str,
    nombre_entidad: str | None = None,
    id_entidad: str | None = None,
    momento: datetime | None = None,
) -> None:
    """Write one entry in a transaction of its own, or refuse the operation.

    For events with no business transaction to join: a login, successful or not,
    and a denial whose handler never runs.

    On failure the transaction is rolled back, the database error is logged
    through a sanitised channel and :class:`FalloDeAuditoria` is raised. There is
    no ``except Exception: pass`` here and there must never be one: the caller is
    expected to turn this into a refusal, because an event that happened without
    a trace is exactly what this table exists to prevent.
    """
    try:
        registrar(
            sesion_bd,
            accion,
            id_usuario=id_usuario,
            ip_origen=ip_origen,
            nombre_entidad=nombre_entidad,
            id_entidad=id_entidad,
            momento=momento,
        )
        sesion_bd.commit()
    except SQLAlchemyError as error:
        sesion_bd.rollback()
        # Only the action and the exception class. The values of the row are not
        # formatted into the message, and neither is the driver's text.
        registrador.error(
            "Auditoria: no se pudo persistir accion=%s error=%s",
            accion.value,
            type(error).__name__,
        )
        raise FalloDeAuditoria from error
