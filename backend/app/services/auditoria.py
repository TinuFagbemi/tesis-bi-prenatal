"""The audit trail of access and relevant actions (SCRUM-70).

**What this is, precisely.** ``operacional.auditoria_log`` is *append-only by
design of the application*: this module only ever inserts, never updates and
never deletes, and the foreign key is ``ON DELETE RESTRICT``, so an account that
produced entries cannot be removed -- only deactivated. It provides
**traceability and technical attribution**. It is **not** cryptographic
immutability and **not** non-repudiation: anybody with administrative
privileges on PostgreSQL can alter the table, and nothing here pretends
otherwise.

**The catalogue is closed.** Eleven actions, listed in :class:`AccionAuditada`:
the four of SCRUM-70, the four of the account life cycle added by SCRUM-97, and
the three of SCRUM-98 -- ``CONTEXTO_CLINICO_AUSENTE`` for an authenticated
account that cannot be resolved to the clinical profile its role requires,
``ACCESO_CLINICO_DENEGADO`` for an authenticated account that asked for a
clinical resource it may not read, and ``ACCESO_CLINICO_PERMITIDO`` for one that
read a clinical resource it was entitled to.
Deliberately absent:

* a row per rejected token -- the bearer of an invalid token has no identified
  actor, and the endpoint is reachable without authenticating, so persisting one
  would hand anybody an unauthenticated write into the audit table. Rejections
  reach the sanitised application log instead.
* a row per idempotent replay -- no business row is created, and the replay path
  rolls its transaction back by contract.
* a row for an account state change that changed nothing -- deactivating an
  account that is already inactive is answered, not recorded as a second
  deactivation.
* a row for a corrupt or missing ``fetalalert.id_usuario``. The GUC is set by
  ``app.api.dependencias`` from a verified token, so a request never reaches the
  database with a bad one; a bad one means somebody opened a psql session and
  typed it, which is not an authenticated request and has no account to
  attribute. Recording it would mean giving the RLS helpers a side effect --
  they are ``STABLE`` functions called from inside policy expressions, evaluated
  an unpredictable number of times per query, and a write in there would be both
  unbounded and impossible to reason about. The protection stays where it works:
  ``seguridad.usuario_actual_id`` returns NULL for anything that is not one to
  nine digits, every policy that starts from it then matches no row, and
  ``test_rls_postgresql.py`` and ``test_http_como_api_postgresql.py`` exercise
  absent, empty, blank, non-numeric, zero, negative, overflowing and injected
  values against all three clinical tables. Fail closed and silent, by design.
* a row for a refused provisioning -- a duplicate email or an already linked
  profile writes nothing, so there is nothing whose success could be claimed.

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

from sqlalchemy import text
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
    # Account life cycle (SCRUM-97). The actor is the ADMIN in ``id_usuario``;
    # the target is ``usuario`` + its id. Written inside the business transaction.
    CUENTA_PACIENTE_PROVISIONADA = "CUENTA_PACIENTE_PROVISIONADA"
    CUENTA_MEDICO_PROVISIONADA = "CUENTA_MEDICO_PROVISIONADA"
    CUENTA_DESACTIVADA = "CUENTA_DESACTIVADA"
    CUENTA_REACTIVADA = "CUENTA_REACTIVADA"
    # Clinical context (SCRUM-98). Written when an authenticated account cannot
    # be resolved to the clinical profile its role requires: a missing link, a
    # link the role does not allow, or more than one. The request is refused
    # either way, so this entry is what turns a silent "no rows" into something
    # somebody can investigate. In its own transaction, because the handler it
    # blocks never runs.
    CONTEXTO_CLINICO_AUSENTE = "CONTEXTO_CLINICO_AUSENTE"
    # A clinical resource the caller may not read (SCRUM-98, sub-phase 4). The
    # actor is the authenticated account; the target is the entity it named --
    # ``embarazo`` or ``sesion_monitoreo`` -- and the identifier it sent.
    #
    # **The entry says nothing the caller did not already put in the request.**
    # Not which resources exist, not whose they are, not a single biometric
    # value. It records that an identified account asked for something outside
    # its scope, which is what an administrator needs in order to notice
    # somebody walking the identifier space.
    #
    # Written in its own transaction, because the read it blocks never happens
    # and there is no business transaction for the entry to share. A successful
    # read writes nothing: a trail that grew with every legitimate query would
    # bury the denials it exists to surface.
    ACCESO_CLINICO_DENEGADO = "ACCESO_CLINICO_DENEGADO"
    # A clinical resource the caller **was** entitled to read (SCRUM-98). RF-10
    # asks who reached the information and when; RNF-07 asks that accesses to
    # sensitive clinical data be recorded. A trail that only holds refusals
    # answers neither: it says who was turned away, never who actually read a
    # patient's series.
    #
    # **One entry per request, not per row.** A listing that returns four
    # hundred readings is one act of access, and recording four hundred rows
    # would bury the trail in its own volume while telling nobody anything the
    # single entry does not. The target is the resource the route names -- the
    # pregnancy or the session whose identifier the caller sent -- and a
    # collection query names none, so it records the entity with no id.
    #
    # The entry holds the actor, the action, the entity, that identifier and the
    # observed address. Not one biometric value, not the rows returned, not the
    # token, not an email: what was read is reconstructible from the identifier
    # and the account's scope at that moment, and copying the payload into the
    # trail would turn the audit table into a second store of clinical data.
    ACCESO_CLINICO_PERMITIDO = "ACCESO_CLINICO_PERMITIDO"


# Physical names of the entities an entry can point at. Table names, not HTTP
# routes: ``id_entidad_afectada`` is the identifier of a row, so the column next
# to it has to name something rows belong to. A route would make the pair
# meaningless.
ENTIDAD_USUARIO = "usuario"
ENTIDAD_SESION_MONITOREO = "sesion_monitoreo"
ENTIDAD_EMBARAZO = "embarazo"

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


# ---------------------------------------------------------------------------
# La sentencia que escribe la traza
# ---------------------------------------------------------------------------
#
# Escrita a mano, y no construida con ``insert(AuditoriaLog)``, por un privilegio
# concreto. La clave de esta tabla es ``serial``, asi que SQLAlchemy le anade
# ``RETURNING id_log`` -- lo hace tanto el ORM como el nucleo, porque el
# *implicit returning* de PostgreSQL esta activo por omision y no se puede
# desactivar por sentencia en 2.0 --, y PostgreSQL exige ``SELECT`` sobre toda
# columna que un ``RETURNING`` nombre.
#
# La cuenta que atiende peticiones tiene ``INSERT`` sobre esta tabla y nada mas:
# ni ``SELECT`` sobre una sola columna, ni ``UPDATE``, ni ``DELETE``. Leer la
# traza es un acto administrativo, no uno del proceso web. Antes que conceder un
# ``SELECT (id_log)`` para que la biblioteca este comoda, la sentencia se escribe
# como lo que es: una escritura que no lee nada.
#
# Las columnas no se listan a mano dos veces. Salen del modelo, sin la clave
# generada, y la asercion de abajo revienta al importar si alguien anade una
# columna sin pasar por aqui.
COLUMNAS_DE_LA_TRAZA = tuple(
    columna.name
    for columna in AuditoriaLog.__table__.columns
    if not columna.primary_key
)

INSERCION_DE_LA_TRAZA = text(
    "INSERT INTO {esquema}.{tabla} ({columnas}) VALUES ({valores})".format(
        esquema=AuditoriaLog.__table__.schema,
        tabla=AuditoriaLog.__table__.name,
        columnas=", ".join(COLUMNAS_DE_LA_TRAZA),
        valores=", ".join(f":{nombre}" for nombre in COLUMNAS_DE_LA_TRAZA),
    )
)

assert COLUMNAS_DE_LA_TRAZA == (
    "id_usuario",
    "accion",
    "nombre_entidad_afectada",
    "id_entidad_afectada",
    "ip_origen",
    "fecha_hora",
), (
    "El modelo de la traza cambio de columnas. Revisa INSERCION_DE_LA_TRAZA y "
    "los privilegios que la revision de RLS concede sobre auditoria_log."
)


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

    **A core INSERT, not an ORM one, and the reason is a privilege.** Adding a
    mapped instance makes SQLAlchemy append ``RETURNING id_log`` so it can put
    the generated key into the identity map, and PostgreSQL requires ``SELECT``
    on every column a ``RETURNING`` clause names. The account that serves
    requests has ``INSERT`` on this table and nothing else -- no ``SELECT`` on
    any column, not even the surrogate key -- because reading the trail is an
    administrative act, not a web one. So the writer writes and does not read.

    The returned object is transient on purpose: it carries the values that were
    stored, which is what callers and tests use, and no ``id_log``, which is
    what this process is not allowed to learn.
    """
    campos = {
        "id_usuario": id_usuario,
        "accion": accion.value,
        "nombre_entidad_afectada": nombre_entidad,
        "id_entidad_afectada": id_entidad,
        "ip_origen": ip_origen,
        "fecha_hora": momento if momento is not None else ahora_utc(),
    }
    sesion_bd.execute(INSERCION_DE_LA_TRAZA, campos)
    sesion_bd.flush()
    return AuditoriaLog(**campos)


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
