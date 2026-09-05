"""Translation of database failures into safe HTTP answers (SCRUM-62).

Two rules shape this module.

**Nothing the driver wrote ever leaves the process.** Not in the HTTP body, and
not in the log either. A psycopg message quotes the failing statement and its
bound parameters, and those parameters are the payload: patient identifiers,
timestamps, biometric values. Neither ``str(error)`` nor ``repr(error)`` is
called anywhere here. What the server records is a short list of fields picked
one by one -- exception class, SQLSTATE, and the constraint, table and column
names PostgreSQL reports -- all of them names from our own schema.

**A failure is classified by SQLSTATE, not by guesswork.** The five digits
PostgreSQL returns say precisely what went wrong, so a rejected value and a
duplicated key never collapse into the same answer.

The mapping, and why each one:

======== ==================== ====== ===================================
SQLSTATE Condición            HTTP   Razón
======== ==================== ====== ===================================
23514    check_violation      422    Dato rechazado por una restricción
                                     de validez. Es el cliente quien lo
                                     envió mal.
23503    foreign_key_violation 409   La referencia existía al verificarla
                                     y dejó de existir: una carrera, que
                                     es un conflicto, no un dato inválido.
23505    unique_violation     500    Ver abajo: hoy no puede originarlo
                                     el cliente.
23502    not_null_violation   500    Pydantic ya garantiza los campos
                                     obligatorios, así que un NULL que
                                     llegue hasta aquí delata un defecto
                                     del servidor, no del payload.
otro     lo que sea           500    Sin clasificación no se inventa
                                     semántica.
======== ==================== ====== ===================================

**Why 23505 is a 500 and not a 409.** A duplicate key normally means "you sent
something that is already there", and 409 would be the honest answer. That is
not what it can mean here. In SCRUM-62 the client sends no ``id_sesion`` and no
``id_lectura``; both primary keys come from PostgreSQL sequences, and neither
table carries a business UNIQUE the client could collide with. So a duplicate
key on these inserts cannot describe anything the caller did -- it describes a
sequence that has fallen behind the rows already stored, which is a fault on
this side of the wire. Answering 409 would blame the client for a server
problem, and would also read as "your resend was detected", which is precisely
what SCRUM-62 does **not** do. When SCRUM-63 introduces an identifier the client
supplies, a real 409 becomes possible and this row will change.

``DataError`` -- a value the column cannot store -- maps to 500 as well. The
rule agreed for this ticket is that it only becomes a 422 when the offending
value can be attributed to the payload *in a controlled way*, and today there is
no such case: the only reachable one was an out-of-range ``mov_valor``, and the
input schema already bounds it to the SMALLINT range, so it never reaches
PostgreSQL. Building an attribution mechanism for a case that cannot happen
would be speculation, so the honest answer is 500 until a real one appears.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from http import HTTPStatus

from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError

registrador = logging.getLogger(__name__)

# PostgreSQL SQLSTATE codes, class 23 -- integrity constraint violation.
SQLSTATE_CHECK = "23514"
SQLSTATE_UNIQUE = "23505"
SQLSTATE_FOREIGN_KEY = "23503"
SQLSTATE_NOT_NULL = "23502"

# Fixed, hand-written messages. None of them quotes the driver, none of them
# names a threshold: telling the client that "SpO2 has to be between 0 and 100"
# would be restating the clinical criterion the endpoint must not own.
MENSAJE_CHECK = (
    "La base de datos rechazó la lectura por una restricción de validez de "
    "los valores enviados. No se registró ninguna fila."
)
# Solo para 23503: una referencia que existía al verificarla y desapareció
# antes del INSERT. Es lo único que hoy puede provocar el cliente y merecer un
# 409.
MENSAJE_CONFLICTO = (
    "No se pudo registrar la sesión: una referencia del paquete cambió "
    "mientras se procesaba. No se guardó nada."
)
MENSAJE_INESPERADO = (
    "No se pudo registrar la sesión por un error interno. No se conservó "
    "ningún cambio: la transacción completa fue revertida."
)

_MENSAJE_POR_SQLSTATE: dict[str, tuple[int, str]] = {
    SQLSTATE_CHECK: (HTTPStatus.UNPROCESSABLE_ENTITY, MENSAJE_CHECK),
    SQLSTATE_FOREIGN_KEY: (HTTPStatus.CONFLICT, MENSAJE_CONFLICTO),
    SQLSTATE_UNIQUE: (HTTPStatus.INTERNAL_SERVER_ERROR, MENSAJE_INESPERADO),
    SQLSTATE_NOT_NULL: (HTTPStatus.INTERNAL_SERVER_ERROR, MENSAJE_INESPERADO),
}


@dataclass(frozen=True)
class RespuestaDeError:
    """The HTTP answer a failure translates into."""

    status_code: int
    detalle: str


@dataclass(frozen=True)
class DiagnosticoSeguro:
    """The only thing about a database failure that may be written to a log.

    Every field is either a Python class name or an identifier PostgreSQL took
    from our own schema. The driver's message, the statement and the bound
    parameters are not represented here, and cannot be: there is no field for
    them.
    """

    excepcion: str
    sqlstate: str | None = None
    restriccion: str | None = None
    tabla: str | None = None
    columna: str | None = None

    def como_texto(self) -> str:
        partes = [f"excepcion={self.excepcion}"]
        for nombre, valor in (
            ("sqlstate", self.sqlstate),
            ("restriccion", self.restriccion),
            ("tabla", self.tabla),
            ("columna", self.columna),
        ):
            if valor is not None:
                partes.append(f"{nombre}={valor}")
        return " ".join(partes)


def extraer_sqlstate(error: BaseException) -> str | None:
    """SQLSTATE of the underlying driver error, or ``None``.

    ``getattr`` all the way down on purpose: the attribute exists on psycopg
    errors, and a test double or a different driver may not provide it. Failing
    to read a diagnostic must never become a second failure.
    """
    original = getattr(error, "orig", None)
    codigo = getattr(original, "sqlstate", None)
    return codigo if isinstance(codigo, str) else None


def diagnostico_seguro(error: BaseException) -> DiagnosticoSeguro:
    """Pick, one by one, the fields that are safe to record."""
    original = getattr(error, "orig", None)
    diagnostico = getattr(original, "diag", None)

    def _campo(nombre: str) -> str | None:
        valor = getattr(diagnostico, nombre, None)
        return valor if isinstance(valor, str) else None

    return DiagnosticoSeguro(
        excepcion=type(error).__name__,
        sqlstate=extraer_sqlstate(error),
        restriccion=_campo("constraint_name"),
        tabla=_campo("table_name"),
        columna=_campo("column_name"),
    )


def clasificar_error_de_base(error: SQLAlchemyError) -> RespuestaDeError:
    """Map a SQLAlchemy failure to the HTTP answer it deserves.

    Pure and total: it takes an exception and returns a response, opens nothing,
    logs nothing and never raises. Logging is the caller's job, so this stays
    testable with a handful of fabricated errors.
    """
    if isinstance(error, IntegrityError):
        codigo = extraer_sqlstate(error)
        if codigo in _MENSAJE_POR_SQLSTATE:
            estado, detalle = _MENSAJE_POR_SQLSTATE[codigo]
            return RespuestaDeError(status_code=estado, detalle=detalle)

    if isinstance(error, DataError):
        # 500 by default: see the module docstring. No controlled attribution
        # to the payload exists today, and inventing one would be a guess.
        return RespuestaDeError(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detalle=MENSAJE_INESPERADO
        )

    return RespuestaDeError(
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detalle=MENSAJE_INESPERADO
    )


def registrar_fallo(error: BaseException, respuesta: RespuestaDeError) -> None:
    """Record a failure using the safe diagnostic and nothing else.

    Never receives, and never builds, the driver's text. ``exc_info`` is left
    off deliberately: a traceback of a database error carries the statement and
    its parameters in the frames.
    """
    diagnostico = diagnostico_seguro(error)
    mensaje = "Fallo al registrar la sesión: http=%s %s"
    if respuesta.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
        registrador.error(mensaje, respuesta.status_code, diagnostico.como_texto())
    else:
        registrador.warning(mensaje, respuesta.status_code, diagnostico.como_texto())
