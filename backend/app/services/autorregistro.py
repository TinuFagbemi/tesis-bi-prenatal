"""Self-registration of a patient: one call, one transaction (SCRUM-110).

**What this module does and what it leaves to the router.** It hashes the
password with the project's Argon2id hasher, calls
``seguridad.autorregistrar_paciente`` once and returns the two identifiers. It
never commits and never rolls back: the router owns the transaction, writes the
audit entry inside it and decides the single commit -- the same split as
``app.services.cuentas`` and ``app.services.ingesta``.

**Why everything goes through one function.** The web process has no privilege
on ``operacional.paciente`` or ``telefono_paciente``, and it keeps none. The
function, owned by a NOLOGIN role, writes the profile, the phone, the account,
the link and both acceptances in one statement, and returns two integers. If any
of those inserts fails the statement fails, and the router's rollback takes the
rest with it; the deferred role/link trigger of SCRUM-97 still checks the
account at commit.

**Duplicates are decided by PostgreSQL.** There is no «is this national id
taken?» query before inserting: it would be one more oracle open to anyone, and
it would not stop a concurrent request anyway. The unique constraints of
``paciente.cedula``, ``paciente.email_pac`` and ``usuario.email`` are what make a
second registration impossible, and :func:`clasificar_rechazo` turns their
refusal into one generic 409 that does not say which of the two values was
already there.

**What the acceptances record.** The version and digest come from
:mod:`app.legal` -- the texts the server serves -- and never from the request;
the request only proved, through the schema, that it accepted the version in
force. The time is PostgreSQL's ``now()``.

All data handled by this prototype is fictitious and simulated.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.legal import vigente
from app.models.enums import DocumentoLegal
from app.schemas.registro import AutorregistroEntrada
from app.services.errores import SQLSTATE_CHECK, SQLSTATE_UNIQUE, diagnostico_seguro
from app.services.passwords import hashear

MENSAJE_DATOS_YA_REGISTRADOS = (
    "Ya existe un registro con esa cédula o con ese correo. Si ya tienes una "
    "cuenta, inicia sesión; si no, revisa los datos."
)
MENSAJE_DATOS_RECHAZADOS = (
    "Los datos no cumplen las condiciones del registro. Revísalos e inténtalo "
    "de nuevo."
)
MENSAJE_CONFLICTO_CONCURRENTE = (
    "Otra operación se cruzó con el registro y no se guardó nada. Inténtalo de "
    "nuevo."
)

# Unique constraints that mean «someone already registered with this». The
# phone is not here: its constraint is per profile, and the profile is new.
RESTRICCIONES_DE_DUPLICADO = frozenset(
    {"uq_paciente_cedula", "uq_paciente_email_pac", "uq_usuario_email"}
)

# CHECKs a well-formed request never reaches, because the schema validates the
# same rules first. If one fires, the request was malformed in a way the schema
# did not foresee: it is answered 422, not 500.
RESTRICCION_DATOS = "autorregistro_datos_validos"
RESTRICCIONES_DE_DATOS = frozenset({RESTRICCION_DATOS, "ck_usuario_email_canonico"})

SQLSTATE_SERIALIZACION = "40001"
SQLSTATE_DEADLOCK = "40P01"

LLAMADA = text(
    "SELECT id_paciente_creado, id_usuario_creado"
    "  FROM seguridad.autorregistrar_paciente("
    "    CAST(:cedula AS varchar), CAST(:primer_nombre AS varchar),"
    "    CAST(:segundo_nombre AS varchar), CAST(:apellido_paterno AS varchar),"
    "    CAST(:apellido_materno AS varchar), CAST(:fecha_nac AS date),"
    "    CAST(:email AS varchar), CAST(:celular AS varchar),"
    "    CAST(:password_hash AS varchar),"
    "    CAST(:version_privacidad AS varchar), CAST(:sha256_privacidad AS varchar),"
    "    CAST(:version_terminos AS varchar), CAST(:sha256_terminos AS varchar))"
)


class ErrorDeAutorregistro(Exception):
    """A refusal this module recognised, with the fixed text to answer it with."""

    def __init__(self, detalle: str) -> None:
        super().__init__(detalle)
        self.detalle = detalle


class DatosYaRegistrados(ErrorDeAutorregistro):
    """The national id or the email already belongs to someone. Answered 409."""


class DatosRechazados(ErrorDeAutorregistro):
    """PostgreSQL refused the shape of the data. Answered 422."""


class ConflictoConcurrente(ErrorDeAutorregistro):
    """PostgreSQL aborted this side of a race. Answered 409; may be retried."""


@dataclass(frozen=True)
class CuentaCreada:
    """What the router needs to answer and to audit. No email, no digest."""

    id_paciente: int
    id_usuario: int


def autorregistrar(sesion_bd: Session, entrada: AutorregistroEntrada) -> CuentaCreada:
    """Create profile, phone, active PACIENTE account, link and acceptances.

    Nothing is committed. The password is hashed exactly as typed.
    """
    privacidad = vigente(DocumentoLegal.POLITICA_PRIVACIDAD)
    terminos = vigente(DocumentoLegal.TERMINOS_CONDICIONES)
    fila = sesion_bd.execute(
        LLAMADA,
        {
            "cedula": entrada.cedula,
            "primer_nombre": entrada.primer_nombre,
            "segundo_nombre": entrada.segundo_nombre,
            "apellido_paterno": entrada.apellido_paterno,
            "apellido_materno": entrada.apellido_materno,
            "fecha_nac": entrada.fecha_nac,
            "email": entrada.email,
            "celular": entrada.celular,
            "password_hash": hashear(entrada.password.get_secret_value()),
            "version_privacidad": privacidad.version,
            "sha256_privacidad": privacidad.sha256,
            "version_terminos": terminos.version,
            "sha256_terminos": terminos.sha256,
        },
    ).one()
    return CuentaCreada(id_paciente=fila[0], id_usuario=fila[1])


def clasificar_rechazo(error: DBAPIError) -> ErrorDeAutorregistro | None:
    """The answer a database refusal deserves, or ``None`` when it is unexpected.

    Read by SQLSTATE and constraint name through ``diagnostico_seguro``, never
    by parsing the driver's message. Pure: logs nothing, raises nothing.
    """
    diagnostico = diagnostico_seguro(error)
    if isinstance(error, IntegrityError):
        if (
            diagnostico.sqlstate == SQLSTATE_UNIQUE
            and diagnostico.restriccion in RESTRICCIONES_DE_DUPLICADO
        ):
            return DatosYaRegistrados(MENSAJE_DATOS_YA_REGISTRADOS)
        if (
            diagnostico.sqlstate == SQLSTATE_CHECK
            and diagnostico.restriccion in RESTRICCIONES_DE_DATOS
        ):
            return DatosRechazados(MENSAJE_DATOS_RECHAZADOS)
        return None
    if diagnostico.sqlstate in (SQLSTATE_SERIALIZACION, SQLSTATE_DEADLOCK):
        return ConflictoConcurrente(MENSAJE_CONFLICTO_CONCURRENTE)
    return None
