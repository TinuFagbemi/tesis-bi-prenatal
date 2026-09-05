"""Reception of monitoring sessions with their biometric readings (SCRUM-62).

This router is the owner of the HTTP transaction, and that is almost all it
does. It delegates the reference checks and the writing to
``app.services.ingesta``, and the translation of database failures to
``app.services.errores``; what stays here is the decision nobody else can take:
**when to commit and when to roll back**.

One request, one transaction, one commit. Any failure -- a missing reference, a
rule of the domain, a constraint PostgreSQL rejects, or something nobody
foresaw -- rolls the whole thing back, so a package is never persisted in part.
There is no path that answers success before the commit, and none that swallows
an error and keeps inserting.

Not in this ticket, on purpose: HTTP idempotency of resends (SCRUM-63) and
authentication. Being explicit about what that means, because it is easy to
misread: **this endpoint does not recognise a resend at all.** Posting the very
same JSON twice creates two sessions with two different ``id_sesion``, and both
answers are 201. Nothing here detects, deduplicates or replays. Making a resend
identifiable needs an identifier the client supplies, and that arrives with
SCRUM-63.
"""

from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.monitoreo import SesionMonitoreoCreada, SesionMonitoreoEntrada
from app.services.errores import (
    MENSAJE_INESPERADO,
    clasificar_error_de_base,
    registrar_fallo,
    RespuestaDeError,
)
from app.services.ingesta import (
    ReferenciaInexistente,
    ReglaDeNegocioViolada,
    registrar_sesion,
)

router = APIRouter(prefix="/api/v1", tags=["monitoreo"])

# ``http.HTTPStatus`` instead of ``fastapi.status``: the constant for 422 is
# deprecated in the installed Starlette, and the standard library one carries no
# such warning and cannot drift with the framework version.


@router.post(
    "/sesiones-monitoreo",
    response_model=SesionMonitoreoCreada,
    status_code=HTTPStatus.CREATED,
    summary="Registrar una sesión de monitoreo con sus lecturas biométricas",
    response_description="Identificadores asignados a la sesión y a sus lecturas",
    responses={
        HTTPStatus.NOT_FOUND: {
            "description": "Alguna referencia del paquete no existe todavía."
        },
        HTTPStatus.CONFLICT: {
            "description": (
                "Una referencia dejó de existir mientras se procesaba el "
                "paquete. No se guardó nada."
            )
        },
        HTTPStatus.UNPROCESSABLE_ENTITY: {
            "description": "El paquete no cumple el contrato o una regla del dominio."
        },
        HTTPStatus.INTERNAL_SERVER_ERROR: {
            "description": "Error interno. La transacción completa fue revertida."
        },
    },
)
def registrar_sesion_de_monitoreo(
    entrada: SesionMonitoreoEntrada,
    sesion_bd: Session = Depends(get_db),
) -> SesionMonitoreoCreada:
    """Persist one monitoring session and every reading it carries, atomically.

    The session and its readings are written inside a single transaction and
    confirmed with a single ``commit``. Nothing is created as a side effect: the
    four identifiers in the package must already name existing rows, the device
    must be assigned to that pregnancy over the session's dates, and every
    reading must be captured during the session and filed under the gestational
    week the pregnancy is actually in.

    Resends are not detected: sending the same package twice creates two
    sessions. Idempotency belongs to SCRUM-63.

    All data is fictitious and simulated. This endpoint has no authentication
    yet and is **not production-ready**.
    """
    try:
        resultado = registrar_sesion(sesion_bd, entrada)
        sesion_bd.commit()
    except ReferenciaInexistente as error:
        sesion_bd.rollback()
        # ``detalle`` is text this project wrote, not the driver's.
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail=error.detalle
        ) from error
    except ReglaDeNegocioViolada as error:
        sesion_bd.rollback()
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY, detail=error.detalle
        ) from error
    except SQLAlchemyError as error:
        sesion_bd.rollback()
        respuesta = clasificar_error_de_base(error)
        registrar_fallo(error, respuesta)
        raise HTTPException(
            status_code=respuesta.status_code, detail=respuesta.detalle
        ) from error
    except Exception as error:  # noqa: BLE001 -- last resort, see below
        # Anything not foreseen still has to leave the database untouched and
        # the answer free of internals. The exception is neither formatted nor
        # attached to the response; only the safe diagnostic reaches the log.
        sesion_bd.rollback()
        respuesta = RespuestaDeError(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detalle=MENSAJE_INESPERADO,
        )
        registrar_fallo(error, respuesta)
        raise HTTPException(
            status_code=respuesta.status_code, detail=respuesta.detalle
        ) from error

    return SesionMonitoreoCreada(
        id_sesion=resultado.id_sesion,
        lecturas_creadas=len(resultado.ids_lectura),
        ids_lectura=list(resultado.ids_lectura),
    )
