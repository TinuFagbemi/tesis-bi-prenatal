"""Administrative provisioning and life cycle of accounts (SCRUM-97).

Three routes and no more::

    POST  /api/v1/cuentas/pacientes/{id_paciente}
    POST  /api/v1/cuentas/medicos/{id_medico}
    PATCH /api/v1/cuentas/{id_usuario}/estado

There is no ``GET``: listing or reading accounts is not part of this ticket, and
an endpoint that answers "which accounts exist" would be one more thing to
protect for no requirement.

**ADMIN only, and before anything else.** Every route depends on
``exigir_roles(NombreRol.ADMIN, entidad="usuario")`` from SCRUM-70. FastAPI
resolves that dependency before it validates the path or the body and before
the handler runs, so an anonymous caller is answered 401 and a PACIENTE or
MEDICO 403 whether the identifier exists or not, and without learning the body
contract either. The role compared is the one PostgreSQL holds now, not one a
token claims.

**The role of a new account comes from the route.** ``/pacientes/...`` creates a
PACIENTE account and ``/medicos/...`` a MEDICO account. The body has no field
for it, and no route creates ADMIN.

**This router owns the transaction.** The service in ``app.services.cuentas``
reads, adds and flushes; the audit entry is added here; and exactly one of two
things ends each request -- a single ``commit`` after a real change, or a
``rollback`` on every other path: a refusal, a no-op, a database error, or
something nobody foresaw. The success entry is written inside the business
transaction, so it is committed with the account or discarded with it; a
rollback cannot leave a false "provisioned" behind.

**Errors.** 404 for a profile or account that does not exist; 409 for an email
or profile already taken, an ADMIN target, a role/link incoherence or a race
PostgreSQL resolved by refusing one side; 422 for a path or body outside the
contract, with the rejected values stripped on the provisioning routes because
the body carries a password; 500 with a generic text for anything else. The
driver's text, the statement and its parameters never reach the answer or the
log.

All accounts and profiles are fictitious and simulated.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from http import HTTPStatus
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.dependencias import MENSAJE_ERROR_INTERNO, exigir_roles
from app.db.session import get_db
from app.models.enums import NombreRol
from app.schemas.cuentas import (
    CuentaMedicoProvisionada,
    CuentaPacienteProvisionada,
    EstadoCuentaEntrada,
    EstadoCuentaSalida,
    ProvisionEntrada,
)
from app.services import auditoria
from app.services import cuentas as servicio_cuentas
from app.services.auditoria import AccionAuditada
from app.services.cuentas import (
    ConflictoDeCuenta,
    CuentaAdministrativa,
    CuentaInexistente,
    EmailEnUso,
    ErrorDeCuenta,
    PerfilInexistente,
    PerfilYaVinculado,
)
from app.services.errores import diagnostico_seguro
from app.services.principal import PrincipalAutenticado
from app.services.tokens import ID_USUARIO_MAXIMO

registrador = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/cuentas", tags=["cuentas"])

RUTA_PACIENTES = "/pacientes/{id_paciente}"
RUTA_MEDICOS = "/medicos/{id_medico}"
RUTA_ESTADO = "/{id_usuario}/estado"

# Templates of the routes whose body carries a password. The 422 handler of
# ``app.api.v1.autenticacion`` strips the echoed values on these, exactly as on
# the login.
RUTAS_CON_CREDENCIALES_DE_CUENTAS = frozenset(
    {f"{router.prefix}{RUTA_PACIENTES}", f"{router.prefix}{RUTA_MEDICOS}"}
)

# ``usuario``, the table the target rows belong to -- also what a denial names.
EXIGIR_ADMIN = exigir_roles(NombreRol.ADMIN, entidad=auditoria.ENTIDAD_USUARIO)

# The three identifiers are PostgreSQL INTEGER keys. Bounding them in the path
# turns an impossible id into a 422 instead of a driver error.
IDENTIFICADOR_MAXIMO = ID_USUARIO_MAXIMO

_ESTADO_POR_ERROR: tuple[tuple[type[ErrorDeCuenta], HTTPStatus], ...] = (
    (PerfilInexistente, HTTPStatus.NOT_FOUND),
    (CuentaInexistente, HTTPStatus.NOT_FOUND),
    (PerfilYaVinculado, HTTPStatus.CONFLICT),
    (EmailEnUso, HTTPStatus.CONFLICT),
    (CuentaAdministrativa, HTTPStatus.CONFLICT),
    (ConflictoDeCuenta, HTTPStatus.CONFLICT),
)

RESPUESTAS_COMUNES = {
    HTTPStatus.UNAUTHORIZED: {
        "description": (
            "Falta la credencial, su esquema no es Bearer, el token no es válido "
            "o la cuenta del administrador ya no existe o está desactivada."
        )
    },
    HTTPStatus.FORBIDDEN: {
        "description": "La identidad es válida pero su rol no es ADMIN. No se consultó nada."
    },
    HTTPStatus.UNPROCESSABLE_ENTITY: {
        "description": "El identificador o el cuerpo no cumplen el contrato."
    },
    HTTPStatus.INTERNAL_SERVER_ERROR: {
        "description": "Error interno. La transacción completa fue revertida."
    },
}

T = TypeVar("T")


def _origen(peticion: Request) -> str:
    return auditoria.direccion_de_origen(peticion.client.host if peticion.client else None)


def _revertir(sesion_bd: Session) -> None:
    """Roll back, and survive a rollback that fails on a broken connection.

    A second driver error escaping from here would carry the statement to the
    server log, which is what the handling around it exists to stop.
    """
    try:
        sesion_bd.rollback()
    except SQLAlchemyError as error:
        registrador.error(
            "Cuentas: fallo al revertir %s", diagnostico_seguro(error).como_texto()
        )


def _en_una_transaccion(sesion_bd: Session, trabajo: Callable[[], T]) -> T:
    """Run ``trabajo`` -- which commits or rolls back itself -- and map failures.

    Every failure path rolls back before answering. Only fixed texts leave: the
    ``detalle`` of a refusal this project wrote, or the generic 500.
    """
    try:
        return trabajo()
    except ErrorDeCuenta as error:
        _revertir(sesion_bd)
        estado = next(e for tipo, e in _ESTADO_POR_ERROR if isinstance(error, tipo))
        raise HTTPException(status_code=estado, detail=error.detalle) from None
    except DBAPIError as error:
        _revertir(sesion_bd)
        diagnostico = diagnostico_seguro(error).como_texto()
        conflicto = servicio_cuentas.clasificar_error_de_integridad(error)
        if conflicto is not None:
            registrador.warning("Cuentas: conflicto de base http=409 %s", diagnostico)
            raise HTTPException(
                status_code=HTTPStatus.CONFLICT, detail=conflicto.detalle
            ) from None
        registrador.error("Cuentas: fallo de base http=500 %s", diagnostico)
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=MENSAJE_ERROR_INTERNO
        ) from None
    except Exception as error:  # noqa: BLE001 -- last resort, see the docstring
        _revertir(sesion_bd)
        registrador.error(
            "Cuentas: fallo inesperado http=500 %s", diagnostico_seguro(error).como_texto()
        )
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=MENSAJE_ERROR_INTERNO
        ) from None


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------

RESPUESTAS_DE_PROVISION = {
    **RESPUESTAS_COMUNES,
    HTTPStatus.NOT_FOUND: {"description": "No existe el perfil clínico indicado."},
    HTTPStatus.CONFLICT: {
        "description": (
            "El perfil ya tiene cuenta, el correo ya está en uso o una operación "
            "concurrente ganó la carrera. No se creó nada."
        )
    },
}


def _provisionar(
    sesion_bd: Session,
    peticion: Request,
    principal: PrincipalAutenticado,
    entrada: ProvisionEntrada,
    *,
    tipo: servicio_cuentas.TipoDePerfil,
    id_perfil: int,
    accion: AccionAuditada,
) -> servicio_cuentas.CuentaProvisionada:
    def trabajo() -> servicio_cuentas.CuentaProvisionada:
        cuenta = servicio_cuentas.provisionar(
            sesion_bd,
            tipo,
            id_perfil,
            email=entrada.email,
            password=entrada.password.get_secret_value(),
        )
        auditoria.registrar(
            sesion_bd,
            accion,
            id_usuario=principal.id_usuario,
            ip_origen=_origen(peticion),
            nombre_entidad=auditoria.ENTIDAD_USUARIO,
            id_entidad=str(cuenta.id_usuario),
        )
        # The deferred role/link trigger runs here; its refusal is a
        # DBAPIError handled like any other.
        sesion_bd.commit()
        return cuenta

    return _en_una_transaccion(sesion_bd, trabajo)


@router.post(
    RUTA_PACIENTES,
    response_model=CuentaPacienteProvisionada,
    status_code=HTTPStatus.CREATED,
    summary="Provisionar la cuenta PACIENTE de un perfil de paciente existente",
    response_description="Cuenta creada, activa y vinculada al perfil",
    responses=RESPUESTAS_DE_PROVISION,
)
def provisionar_cuenta_paciente(
    entrada: ProvisionEntrada,
    peticion: Request,
    id_paciente: int = Path(ge=1, le=IDENTIFICADOR_MAXIMO),
    principal: PrincipalAutenticado = Depends(EXIGIR_ADMIN),
    sesion_bd: Session = Depends(get_db),
) -> CuentaPacienteProvisionada:
    """Create the one PACIENTE account of an existing, still unlinked patient."""
    cuenta = _provisionar(
        sesion_bd,
        peticion,
        principal,
        entrada,
        tipo=servicio_cuentas.PERFIL_PACIENTE,
        id_perfil=id_paciente,
        accion=AccionAuditada.CUENTA_PACIENTE_PROVISIONADA,
    )
    return CuentaPacienteProvisionada(
        id_usuario=cuenta.id_usuario,
        id_paciente=cuenta.id_perfil,
        rol=cuenta.rol,
        activo=cuenta.activo,
    )


@router.post(
    RUTA_MEDICOS,
    response_model=CuentaMedicoProvisionada,
    status_code=HTTPStatus.CREATED,
    summary="Provisionar la cuenta MEDICO de un perfil de médico existente",
    response_description="Cuenta creada, activa y vinculada al perfil",
    responses=RESPUESTAS_DE_PROVISION,
)
def provisionar_cuenta_medico(
    entrada: ProvisionEntrada,
    peticion: Request,
    id_medico: int = Path(ge=1, le=IDENTIFICADOR_MAXIMO),
    principal: PrincipalAutenticado = Depends(EXIGIR_ADMIN),
    sesion_bd: Session = Depends(get_db),
) -> CuentaMedicoProvisionada:
    """Create the one MEDICO account of an existing, still unlinked physician."""
    cuenta = _provisionar(
        sesion_bd,
        peticion,
        principal,
        entrada,
        tipo=servicio_cuentas.PERFIL_MEDICO,
        id_perfil=id_medico,
        accion=AccionAuditada.CUENTA_MEDICO_PROVISIONADA,
    )
    return CuentaMedicoProvisionada(
        id_usuario=cuenta.id_usuario,
        id_medico=cuenta.id_perfil,
        rol=cuenta.rol,
        activo=cuenta.activo,
    )


# ---------------------------------------------------------------------------
# Life cycle
# ---------------------------------------------------------------------------


@router.patch(
    RUTA_ESTADO,
    response_model=EstadoCuentaSalida,
    status_code=HTTPStatus.OK,
    summary="Desactivar o reactivar una cuenta PACIENTE o MEDICO",
    response_description="Estado resultante de la cuenta",
    responses={
        **RESPUESTAS_COMUNES,
        HTTPStatus.NOT_FOUND: {"description": "No existe la cuenta indicada."},
        HTTPStatus.CONFLICT: {
            "description": (
                "La cuenta es ADMIN -- incluida la propia -- o una operación "
                "concurrente ganó la carrera. No se modificó nada."
            )
        },
    },
)
def cambiar_estado_de_cuenta(
    entrada: EstadoCuentaEntrada,
    peticion: Request,
    id_usuario: int = Path(ge=1, le=IDENTIFICADOR_MAXIMO),
    principal: PrincipalAutenticado = Depends(EXIGIR_ADMIN),
    sesion_bd: Session = Depends(get_db),
) -> EstadoCuentaSalida:
    """Deactivate or reactivate the same account. Nothing is deleted, ever.

    Repeating the state the account already has is answered 200 with that state,
    rolled back and not audited: no transition happened. A real transition is
    audited as ``CUENTA_DESACTIVADA`` or ``CUENTA_REACTIVADA`` in the same
    transaction as the change.
    """

    def trabajo() -> servicio_cuentas.EstadoDeCuenta:
        estado = servicio_cuentas.cambiar_estado(sesion_bd, id_usuario, entrada.activo)
        if not estado.hubo_transicion:
            sesion_bd.rollback()
            return estado

        auditoria.registrar(
            sesion_bd,
            AccionAuditada.CUENTA_REACTIVADA if estado.activo else AccionAuditada.CUENTA_DESACTIVADA,
            id_usuario=principal.id_usuario,
            ip_origen=_origen(peticion),
            nombre_entidad=auditoria.ENTIDAD_USUARIO,
            id_entidad=str(estado.id_usuario),
        )
        sesion_bd.commit()
        return estado

    estado = _en_una_transaccion(sesion_bd, trabajo)
    return EstadoCuentaSalida(
        id_usuario=estado.id_usuario, rol=estado.rol, activo=estado.activo
    )
