"""Controlled provisioning and life cycle of accounts (SCRUM-97).

**Four entities that must not be confused.**

* ``Paciente`` / ``Medico`` -- the clinical profile of a (fictitious) person. It
  exists before any account and outlives every account and every pregnancy.
* ``Usuario`` -- the digital identity that authenticates. It is activated and
  deactivated; it is never recreated for a new pregnancy.
* ``UsuarioPaciente`` / ``UsuarioMedico`` -- the one link between that identity
  and that profile.
* ``Embarazo`` -- one gestational episode. A second pregnancy is a second
  ``Embarazo`` of the same ``id_paciente``, never a second patient, account or
  link. A device assignment is temporary and defines no identity at all.

**What this module does and what it leaves to its caller.** It implements the
rules, reads, ``add()`` and ``flush()``. It never commits and never rolls back:
the router owns the transaction, exactly as in ``app.services.ingesta`` and
``app.services.principal``. The audit entry is written by the router too, inside
the same transaction, before its single commit.

**Where each guarantee lives.**

* The role of a new account comes from the kind of profile -- a constant of
  :data:`PERFIL_PACIENTE` or :data:`PERFIL_MEDICO` -- and never from input. No
  path here can produce ADMIN.
* The checks before inserting give a precise, controlled answer. They are not
  what makes a duplicate impossible: ``uq_usuario_email``,
  ``uq_usuario_paciente_id_paciente``, ``uq_usuario_medico_id_medico`` and the
  deferred ``rol_vinculo_coherente`` trigger are. When a concurrent request gets
  past the checks, PostgreSQL refuses the insert and
  :func:`clasificar_error_de_integridad` turns that refusal into the same 409.
* The profile row is taken ``FOR NO KEY UPDATE`` before anything is checked, so
  two provisionings for the same profile run one after the other, and the second
  one sees the link the first committed. ``NO KEY`` so a pregnancy being
  inserted for that patient -- which takes ``FOR KEY SHARE`` -- is not blocked.
* A state change takes the account row ``FOR NO KEY UPDATE``: two changes of the
  same account serialise, and the second one reads the state the first left.

**Deactivation deletes nothing.** It flips ``Usuario.activo``. The account, its
digest, its link, the profile, pregnancies, sessions, readings and audit trail
stay exactly where they were. ``app.services.principal`` reads ``activo`` on
every protected request, so a token issued before deactivation is refused on
its next use. Reactivation flips the same row back: same ``id_usuario``, same
digest, same link.

All accounts and profiles are fictitious and simulated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.catalogos import Rol
from app.models.enums import NombreRol
from app.models.seguridad import Usuario, UsuarioMedico, UsuarioPaciente
from app.services.correo import canonizar_email
from app.services.errores import SQLSTATE_CHECK, SQLSTATE_UNIQUE, diagnostico_seguro
from app.services.passwords import hashear

# Fixed messages. None names a value the caller sent or a row it did not ask
# about; every one of them is only ever read by an authenticated ADMIN.
MENSAJE_PACIENTE_INEXISTENTE = "No existe un perfil de paciente con ese identificador."
MENSAJE_MEDICO_INEXISTENTE = "No existe un perfil de medico con ese identificador."
MENSAJE_CUENTA_INEXISTENTE = "No existe una cuenta con ese identificador."
MENSAJE_PERFIL_YA_VINCULADO = (
    "El perfil clinico ya tiene una cuenta vinculada. No se creo ninguna cuenta."
)
MENSAJE_EMAIL_EN_USO = (
    "El correo de acceso ya pertenece a otra cuenta. No se creo ninguna cuenta."
)
MENSAJE_CUENTA_ADMINISTRATIVA = (
    "Las cuentas ADMIN no se administran con esta operacion. No se modifico nada."
)
MENSAJE_VINCULO_INCOHERENTE = (
    "La cuenta y su vinculo clinico no son coherentes con el rol. No se guardo nada."
)
MENSAJE_CONFLICTO_CONCURRENTE = (
    "Otra operacion sobre la misma cuenta o perfil se cruzo con esta. "
    "No se guardo nada; puede reintentarse."
)

# Name the deferred trigger reports for the role/link rule. Written as a literal
# in revision 54053d46abd6 and repeated here because the application recognises
# the error by it; a test pins that both agree.
RESTRICCION_ROL_VINCULO = "rol_vinculo_coherente"

# SQLSTATE of the two ways PostgreSQL resolves a race by aborting one side.
SQLSTATE_SERIALIZACION = "40001"
SQLSTATE_DEADLOCK = "40P01"


class ErrorDeCuenta(Exception):
    """A refusal this module decided, with the fixed text to answer it with."""

    mensaje: str = ""

    def __init__(self, mensaje: str | None = None) -> None:
        self.detalle = mensaje if mensaje is not None else self.mensaje
        super().__init__(self.detalle)


class PerfilInexistente(ErrorDeCuenta):
    """The clinical profile does not exist. Answered 404."""


class CuentaInexistente(ErrorDeCuenta):
    """The account does not exist. Answered 404."""

    mensaje = MENSAJE_CUENTA_INEXISTENTE


class PerfilYaVinculado(ErrorDeCuenta):
    """The profile already has its one account. Answered 409."""

    mensaje = MENSAJE_PERFIL_YA_VINCULADO


class EmailEnUso(ErrorDeCuenta):
    """The canonical email belongs to another account. Answered 409."""

    mensaje = MENSAJE_EMAIL_EN_USO


class CuentaAdministrativa(ErrorDeCuenta):
    """The target is an ADMIN account, the caller's own included. Answered 409."""

    mensaje = MENSAJE_CUENTA_ADMINISTRATIVA


class ConflictoDeCuenta(ErrorDeCuenta):
    """PostgreSQL refused the write for a reason this contract knows. Answered 409."""


# ---------------------------------------------------------------------------
# Kinds of clinical profile
# ---------------------------------------------------------------------------


def _vinculo_paciente(id_usuario: int, id_perfil: int) -> Base:
    return UsuarioPaciente(id_usuario=id_usuario, id_paciente=id_perfil)


def _vinculo_medico(id_usuario: int, id_perfil: int) -> Base:
    return UsuarioMedico(id_usuario=id_usuario, id_medico=id_perfil)


# Las cuatro respuestas del helper de estado, y las unicas. Cualquier otra cosa
# -- incluida ``sin_autorizacion`` -- se trata como «no hay perfil»: es lo unico
# que esta capa puede afirmar sin convertir la respuesta en un oraculo.
ESTADO_INEXISTENTE = "inexistente"
ESTADO_DISPONIBLE = "disponible"
ESTADO_VINCULADO = "vinculado"
ESTADO_SIN_AUTORIZACION = "sin_autorizacion"
ESTADOS = frozenset(
    {ESTADO_INEXISTENTE, ESTADO_DISPONIBLE, ESTADO_VINCULADO, ESTADO_SIN_AUTORIZACION}
)


@dataclass(frozen=True)
class TipoDePerfil:
    """Everything that differs between provisioning a PACIENTE and a MEDICO.

    ``rol`` is fixed here and is the only source of the new account's role.

    ``helper_de_estado`` names the SECURITY DEFINER function that answers
    whether the profile exists and whether it is already taken. It is named
    rather than built from a model class on purpose: ``fetalalert_api`` has no
    privilege at all on ``operacional.paciente`` or ``operacional.medico``, and
    reading the bridges directly would need an ADMIN branch in their row-level
    policy -- which is exactly the enumeration this project refuses to grant.
    """

    rol: NombreRol
    helper_de_estado: str
    crear_vinculo: Callable[[int, int], Base]
    mensaje_inexistente: str


PERFIL_PACIENTE = TipoDePerfil(
    rol=NombreRol.PACIENTE,
    helper_de_estado="estado_del_perfil_paciente",
    crear_vinculo=_vinculo_paciente,
    mensaje_inexistente=MENSAJE_PACIENTE_INEXISTENTE,
)

PERFIL_MEDICO = TipoDePerfil(
    rol=NombreRol.MEDICO,
    helper_de_estado="estado_del_perfil_medico",
    crear_vinculo=_vinculo_medico,
    mensaje_inexistente=MENSAJE_MEDICO_INEXISTENTE,
)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CuentaProvisionada:
    """What the router needs to answer and to audit. No email, no digest."""

    id_usuario: int
    id_perfil: int
    rol: NombreRol
    activo: bool


@dataclass(frozen=True)
class EstadoDeCuenta:
    """The state after the request, and whether it actually changed."""

    id_usuario: int
    rol: NombreRol
    activo: bool
    hubo_transicion: bool


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------


def provisionar(
    sesion_bd: Session,
    tipo: TipoDePerfil,
    id_perfil: int,
    *,
    email: str,
    password: str,
) -> CuentaProvisionada:
    """Create one active account and its link to an existing profile.

    The order is the contract: lock and confirm the profile, refuse a profile
    that already has its account, canonicalise and refuse an email in use,
    resolve the role by name, hash, insert the account, flush for its id, insert
    the link. Nothing is committed; if anything after the first insert fails,
    the caller's rollback takes the account with it, so there is no orphan.

    **The first two steps are one call.** ``seguridad.estado_del_perfil_*`` runs
    as its own NOLOGIN owner, takes the same ``FOR NO KEY UPDATE`` this code used
    to take, and answers with a single word. It returns no column of the profile
    and no column of the bridge, so the account that calls it learns exactly what
    it needs to provision and nothing it could enumerate with.

    Anything other than ``disponible`` or ``vinculado`` becomes the same 404 as a
    missing profile. ``sin_autorizacion`` lands there too: a caller without the
    administrative context has no standing to be told whether the row exists.
    """
    estado = sesion_bd.execute(
        select(getattr(func.seguridad, tipo.helper_de_estado)(id_perfil))
    ).scalar_one()

    if estado == ESTADO_VINCULADO:
        raise PerfilYaVinculado()
    if estado != ESTADO_DISPONIBLE:
        raise PerfilInexistente(tipo.mensaje_inexistente)

    email_canonico = canonizar_email(email)
    en_uso = sesion_bd.execute(
        select(Usuario.id_usuario).where(Usuario.email == email_canonico)
    ).first()
    if en_uso is not None:
        raise EmailEnUso()

    # By name, never by a numeric id: the catalogue's ids are the dataset's.
    id_rol = sesion_bd.execute(
        select(Rol.id_rol).where(Rol.nombre_rol == tipo.rol)
    ).scalar_one()

    usuario = Usuario(
        id_rol=id_rol,
        email=email_canonico,
        password_hash=hashear(password),
        activo=True,
    )
    sesion_bd.add(usuario)
    sesion_bd.flush()

    sesion_bd.add(tipo.crear_vinculo(usuario.id_usuario, id_perfil))
    sesion_bd.flush()

    return CuentaProvisionada(
        id_usuario=usuario.id_usuario,
        id_perfil=id_perfil,
        rol=tipo.rol,
        activo=usuario.activo,
    )


# ---------------------------------------------------------------------------
# Life cycle
# ---------------------------------------------------------------------------


def cambiar_estado(sesion_bd: Session, id_usuario: int, activo: bool) -> EstadoDeCuenta:
    """Set ``Usuario.activo``, or report that it already had that value.

    ADMIN accounts are refused before anything is compared -- the caller's own
    included -- so this operation can neither lock the administrators out nor
    be used on them. A request that would not change anything flushes nothing
    and says so in ``hubo_transicion``; the router answers it without auditing a
    transition that did not happen.

    ``populate_existing`` makes the locked read overwrite whatever this session
    may already hold for the row, so the comparison is against the state the
    lock protects and not against a copy read before waiting for it.
    """
    fila = sesion_bd.execute(
        select(Usuario, Rol.nombre_rol)
        .join(Rol, Usuario.id_rol == Rol.id_rol)
        .where(Usuario.id_usuario == id_usuario)
        .with_for_update(key_share=True, of=Usuario)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if fila is None:
        raise CuentaInexistente()

    usuario, rol = fila
    if rol == NombreRol.ADMIN:
        raise CuentaAdministrativa()

    if usuario.activo == activo:
        return EstadoDeCuenta(
            id_usuario=usuario.id_usuario, rol=rol, activo=activo, hubo_transicion=False
        )

    usuario.activo = activo
    sesion_bd.flush()
    return EstadoDeCuenta(
        id_usuario=usuario.id_usuario, rol=rol, activo=activo, hubo_transicion=True
    )


# ---------------------------------------------------------------------------
# PostgreSQL refusals this contract recognises
# ---------------------------------------------------------------------------

# Constraint name -> the answer. Only names from our own schema; anything not
# listed is not guessed at and stays a 500.
_CONFLICTOS_POR_RESTRICCION: dict[str, str] = {
    "uq_usuario_email": MENSAJE_EMAIL_EN_USO,
    "uq_usuario_paciente_id_paciente": MENSAJE_PERFIL_YA_VINCULADO,
    "uq_usuario_medico_id_medico": MENSAJE_PERFIL_YA_VINCULADO,
    "pk_usuario_paciente": MENSAJE_VINCULO_INCOHERENTE,
    "pk_usuario_medico": MENSAJE_VINCULO_INCOHERENTE,
}


def clasificar_error_de_integridad(error: DBAPIError) -> ConflictoDeCuenta | None:
    """The 409 a database refusal deserves, or ``None`` when it is not a conflict.

    Recognised by SQLSTATE and constraint name, read through
    ``diagnostico_seguro`` -- never by parsing the driver's message:

    * a UNIQUE or primary key of the account or its links: the email or the
      profile was taken by a request that got past the checks first;
    * the deferred role/link trigger: the rows would leave an account whose
      role and link disagree;
    * a serialisation failure or a deadlock: PostgreSQL aborted one side of a
      race.

    Pure: it logs nothing and raises nothing.
    """
    diagnostico = diagnostico_seguro(error)

    if isinstance(error, IntegrityError):
        if (
            diagnostico.sqlstate == SQLSTATE_UNIQUE
            and diagnostico.restriccion in _CONFLICTOS_POR_RESTRICCION
        ):
            return ConflictoDeCuenta(_CONFLICTOS_POR_RESTRICCION[diagnostico.restriccion])
        if (
            diagnostico.sqlstate == SQLSTATE_CHECK
            and diagnostico.restriccion == RESTRICCION_ROL_VINCULO
        ):
            return ConflictoDeCuenta(MENSAJE_VINCULO_INCOHERENTE)
        return None

    if diagnostico.sqlstate in (SQLSTATE_SERIALIZACION, SQLSTATE_DEADLOCK):
        return ConflictoDeCuenta(MENSAJE_CONFLICTO_CONCURRENTE)

    return None
