"""Patient self-registration, without a previous session (SCRUM-110).

Two routes::

    GET  /api/v1/registro/documentos   the two legal texts in force
    POST /api/v1/registro/pacientes    create profile + active PACIENTE account

**No authentication, on purpose, and nothing else relaxed.** A patient creates
her own account; a physician may help her fill the form in person, but takes no
part in the operation and is not recorded as its author. The administrative
routes of ``/api/v1/cuentas`` keep requiring ADMIN, and MEDICO gains nothing.
What this router can create is fixed by ``seguridad.autorregistrar_paciente``:
a **new** profile with exactly one active PACIENTE account. It cannot create
another role, cannot activate or deactivate anything and cannot attach an
account to an existing profile -- the body has no field for any of that.

**Proportional protection for an open write.**

* A sliding-window limit per observed origin and in total
  (:mod:`app.services.limite`), checked before the body is even validated, so
  neither the Argon2id hash nor the inserts can be driven in a loop. 429 with
  ``Retry-After``.
* Duplicates and races are settled by PostgreSQL's unique constraints and
  answered with one generic 409 that does not say which value was taken.
* The 422 does not echo values: the body carries a password, so the route is
  listed in ``RUTAS_CON_CREDENCIALES``, like the login.
* The 201 carries two identifiers, the role and the state -- no email, name,
  national id, phone, password or digest.

**This router owns the transaction.** The service calls the function; the audit
entry is added here in the same transaction; exactly one ``commit`` follows a
complete success and a ``rollback`` closes every other path. The entry
``CUENTA_PACIENTE_AUTORREGISTRADA`` names the new account as its own actor, so a
rollback cannot leave a false success and the trail never attributes the
registration to a physician or an administrator.

All data handled by this prototype is fictitious and simulated.
"""

from __future__ import annotations

import logging
from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.dependencias import MENSAJE_ERROR_INTERNO
from app.config import settings
from app.db.session import get_db
from app.legal import DOCUMENTOS_VIGENTES
from app.models.enums import NombreRol
from app.schemas.registro import (
    AutorregistroEntrada,
    CuentaAutorregistrada,
    DocumentoPublicado,
    DocumentosVigentes,
)
from app.services import auditoria
from app.services import autorregistro as servicio
from app.services.auditoria import AccionAuditada
from app.services.autorregistro import (
    ConflictoConcurrente,
    DatosRechazados,
    DatosYaRegistrados,
    ErrorDeAutorregistro,
)
from app.services.errores import diagnostico_seguro
from app.services.limite import LimiteDeSolicitudes

registrador = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/registro", tags=["registro"])

RUTA_PACIENTES = "/pacientes"
RUTA_DOCUMENTOS = "/documentos"

# The body of this route carries a password; its 422 must not echo values.
RUTAS_CON_CREDENCIALES_DE_REGISTRO = frozenset({f"{router.prefix}{RUTA_PACIENTES}"})

MENSAJE_DEMASIADAS_SOLICITUDES = (
    "Se recibieron demasiadas solicitudes de registro. Espera unos minutos e "
    "inténtalo de nuevo."
)

_ESTADO_POR_ERROR: tuple[tuple[type[ErrorDeAutorregistro], HTTPStatus], ...] = (
    (DatosYaRegistrados, HTTPStatus.CONFLICT),
    (ConflictoConcurrente, HTTPStatus.CONFLICT),
    (DatosRechazados, HTTPStatus.UNPROCESSABLE_ENTITY),
)

# One limiter per process, built from the settings at import.
_LIMITADOR = LimiteDeSolicitudes(
    maximo_por_origen=settings.registro_maximo_por_origen,
    maximo_global=settings.registro_maximo_global,
    ventana_segundos=settings.registro_ventana_segundos,
)


def obtener_limitador() -> LimiteDeSolicitudes:
    """A dependency, so tests can replace the limiter without touching globals."""
    return _LIMITADOR


def _origen(peticion: Request) -> str:
    return auditoria.direccion_de_origen(peticion.client.host if peticion.client else None)


def exigir_cupo(
    peticion: Request, limitador: LimiteDeSolicitudes = Depends(obtener_limitador)
) -> None:
    """Refuse with 429 before the body is validated, hashed or written."""
    decision = limitador.consumir(_origen(peticion))
    if not decision.permitida:
        registrador.warning("Registro: limite de solicitudes alcanzado http=429")
        raise HTTPException(
            status_code=HTTPStatus.TOO_MANY_REQUESTS,
            detail=MENSAJE_DEMASIADAS_SOLICITUDES,
            headers={"Retry-After": str(decision.reintentar_en_segundos)},
        )


def _revertir(sesion_bd: Session) -> None:
    try:
        sesion_bd.rollback()
    except SQLAlchemyError as error:
        registrador.error(
            "Registro: fallo al revertir %s", diagnostico_seguro(error).como_texto()
        )


@router.get(
    RUTA_DOCUMENTOS,
    response_model=DocumentosVigentes,
    summary="Política de privacidad y términos vigentes del autorregistro",
)
def documentos_vigentes() -> DocumentosVigentes:
    """The exact texts, versions and digests the registration will record."""
    return DocumentosVigentes(
        documentos=[
            DocumentoPublicado(
                documento=d.documento,
                version=d.version,
                titulo=d.titulo,
                texto=d.texto,
                sha256=d.sha256,
            )
            for d in DOCUMENTOS_VIGENTES.values()
        ]
    )


@router.post(
    RUTA_PACIENTES,
    response_model=CuentaAutorregistrada,
    status_code=HTTPStatus.CREATED,
    summary="Autorregistro de una paciente: perfil y cuenta PACIENTE activa",
    response_description="Perfil, celular, cuenta, vínculo y aceptaciones guardados",
    dependencies=[Depends(exigir_cupo)],
    responses={
        HTTPStatus.CONFLICT: {
            "description": (
                "La cédula o el correo ya están registrados, o una operación "
                "concurrente ganó la carrera. No se guardó nada."
            )
        },
        HTTPStatus.UNPROCESSABLE_ENTITY: {
            "description": "El cuerpo no cumple el contrato. No se devuelven los valores."
        },
        HTTPStatus.TOO_MANY_REQUESTS: {
            "description": "Límite de solicitudes de registro alcanzado. Ver Retry-After."
        },
        HTTPStatus.INTERNAL_SERVER_ERROR: {
            "description": "Error interno. La transacción completa fue revertida."
        },
    },
)
def autorregistrar_paciente(
    entrada: AutorregistroEntrada,
    peticion: Request,
    sesion_bd: Session = Depends(get_db),
) -> CuentaAutorregistrada:
    """Create everything or nothing, and confirm only after the commit."""
    try:
        cuenta = servicio.autorregistrar(sesion_bd, entrada)
        auditoria.registrar(
            sesion_bd,
            AccionAuditada.CUENTA_PACIENTE_AUTORREGISTRADA,
            id_usuario=cuenta.id_usuario,
            ip_origen=_origen(peticion),
            nombre_entidad=auditoria.ENTIDAD_USUARIO,
            id_entidad=str(cuenta.id_usuario),
        )
        # The deferred role/link trigger runs here; its refusal is a DBAPIError
        # handled like any other.
        sesion_bd.commit()
    except DBAPIError as error:
        _revertir(sesion_bd)
        diagnostico = diagnostico_seguro(error).como_texto()
        rechazo = servicio.clasificar_rechazo(error)
        if rechazo is not None:
            estado = next(e for tipo, e in _ESTADO_POR_ERROR if isinstance(rechazo, tipo))
            registrador.warning("Registro: rechazo de base http=%d %s", estado, diagnostico)
            raise HTTPException(status_code=estado, detail=rechazo.detalle) from None
        registrador.error("Registro: fallo de base http=500 %s", diagnostico)
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=MENSAJE_ERROR_INTERNO
        ) from None
    except Exception as error:  # noqa: BLE001 -- last resort; nothing unsanitised leaves
        _revertir(sesion_bd)
        registrador.error(
            "Registro: fallo inesperado http=500 %s", diagnostico_seguro(error).como_texto()
        )
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=MENSAJE_ERROR_INTERNO
        ) from None

    return CuentaAutorregistrada(
        id_usuario=cuenta.id_usuario,
        id_paciente=cuenta.id_paciente,
        rol=NombreRol.PACIENTE,
        activo=True,
    )
