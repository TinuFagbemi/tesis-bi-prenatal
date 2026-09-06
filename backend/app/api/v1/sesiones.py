"""Reception of monitoring sessions with their biometric readings (SCRUM-62/63).

This router is the owner of the HTTP transaction, and that is nearly all it
does. The recognition of resends and the writing live in
``app.services.idempotencia``, which coordinates ``app.services.ingesta``; the
translation of database failures lives in ``app.services.errores``. What stays
here is the decision nobody else can take: **when to commit and when to roll
back**, and how each failure becomes a response.

One request, one transaction, and exactly one of the two endings: a single
``commit`` when a package is created, or a single ``rollback`` on every other
path -- a replay, a collision, a rejected reference, a broken rule, a constraint
PostgreSQL refuses, or something nobody foresaw. There is no path that answers
success before the commit, and none that swallows an error and keeps inserting.

**Idempotency (SCRUM-63).** Every request carries an ``Idempotency-Key``, and it
is mandatory: without a valid one the request is refused with 400 before a single
statement runs. The key is claimed **before any business row is written**, with
``INSERT ... ON CONFLICT DO NOTHING``, so PostgreSQL -- not this process --
decides who got it. Three consequences worth stating plainly:

* the same package sent twice under the same key creates **one** session and
  answers 201 twice, the second one marked ``Idempotency-Replayed: true``;
* a different package under a key already taken is a 409, and nothing is
  written;
* a failure *after* the claim rolls the claim back with everything else, so the
  key is free again and a corrected retry works. The key is never poisoned by a
  request that did not finish.

Not in this ticket, on purpose: authentication. This endpoint has none and is
**not production-ready**.
"""

from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException, Request, Response
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
from app.services.idempotencia import (
    LONGITUD_MAXIMA_DE_CLAVE,
    LONGITUD_MINIMA_DE_CLAVE,
    MENSAJE_CLAVE_INVALIDA,
    PATRON_DE_CLAVE,
    RECURSO_SESIONES_MONITOREO,
    AnomaliaDeIdempotencia,
    ColisionDeIdempotencia,
    clave_valida,
    procesar_ingesta_idempotente,
    registrar_anomalia,
    registrar_colision,
    registrar_replay,
)
from app.services.ingesta import ReferenciaInexistente, ReglaDeNegocioViolada

router = APIRouter(prefix="/api/v1", tags=["monitoreo"])

# ``http.HTTPStatus`` instead of ``fastapi.status``: the constant for 422 is
# deprecated in the installed Starlette, and the standard library one carries no
# such warning and cannot drift with the framework version.

CABECERA_IDEMPOTENCIA = "Idempotency-Key"

# Answered on every 201 so a client -- and a test -- can tell «I created it now»
# from «it already existed». Without it the two are indistinguishable: same
# status, same body, by design.
CABECERA_REPLAY = "Idempotency-Replayed"
REPLAY_SI = "true"
REPLAY_NO = "false"

DESCRIPCION_DE_LA_CLAVE = (
    "Identificador que el cliente asigna al paquete, para que un reenvío pueda "
    "reconocerse. Entre 8 y 128 caracteres de [A-Za-z0-9_-]; un UUID sirve. Su "
    "ausencia o un formato inválido se responden con 400. La clave identifica un "
    "paquete inmutable: un reenvío repite el mismo cuerpo, y un cuerpo distinto "
    "necesita una clave distinta."
)

# Documentación manual del parámetro, y la razón de que sea manual: la cabecera
# se lee de ``Request`` para poder responder 400 en vez del 422 que FastAPI
# produce con un ``Header`` obligatorio, y un parámetro leído así no aparece solo
# en el esquema. Declararlo aquí es lo que permite publicar ``required: true``
# sin renunciar al 400 -- el flag y el comportamiento dicen lo mismo.
#
# Ni el patrón ni las longitudes se escriben a mano: salen de las constantes que
# el servicio aplica, así que el contrato documentado no puede separarse del
# exigido.
PARAMETRO_DE_LA_CLAVE = {
    "name": CABECERA_IDEMPOTENCIA,
    "in": "header",
    "required": True,
    "description": DESCRIPCION_DE_LA_CLAVE,
    "schema": {
        "type": "string",
        "minLength": LONGITUD_MINIMA_DE_CLAVE,
        "maxLength": LONGITUD_MAXIMA_DE_CLAVE,
        "pattern": PATRON_DE_CLAVE.pattern,
    },
}

CABECERA_REPLAY_DOCUMENTADA = {
    CABECERA_REPLAY: {
        "description": (
            "'false' cuando el paquete se creó en esta solicitud; 'true' cuando "
            "ya existía y la respuesta es la que produjo la primera."
        ),
        "schema": {"type": "string", "enum": [REPLAY_NO, REPLAY_SI]},
    }
}


def exigir_clave_de_idempotencia(peticion: Request) -> str:
    """The key, or a 400 before anything else happens.

    Read straight from the request rather than declared as a ``Header``
    parameter, and the reason is the status code: ``Header(...)`` makes FastAPI
    answer 422 for a missing key, which is the code this project reserves for a
    body that breaks the contract or a rule of the domain, while
    ``Header(default=None)`` makes the parameter optional in OpenAPI and the
    schema would then contradict the contract. Reading it here keeps the 400 and
    lets ``PARAMETRO_DE_LA_CLAVE`` declare the truth: required.

    Being a dependency is what fixes the precedence: FastAPI resolves and calls
    sub-dependencies **before** it validates the body, so a request missing the
    key *and* carrying a malformed body is answered 400. The framing is wrong,
    and saying so beats listing fields of a body that was never going to be read.

    Nothing is written on this path: it runs before the endpoint opens a
    transaction.
    """
    clave = peticion.headers.get(CABECERA_IDEMPOTENCIA)
    if not clave_valida(clave):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail=MENSAJE_CLAVE_INVALIDA
        )
    return clave


@router.post(
    "/sesiones-monitoreo",
    response_model=SesionMonitoreoCreada,
    status_code=HTTPStatus.CREATED,
    summary="Registrar una sesión de monitoreo con sus lecturas biométricas",
    response_description="Identificadores asignados a la sesión y a sus lecturas",
    responses={
        HTTPStatus.CREATED: {
            "description": (
                "La sesión y todas sus lecturas quedaron registradas, o ya lo "
                "estaban por una solicitud anterior con la misma clave."
            ),
            "headers": CABECERA_REPLAY_DOCUMENTADA,
        },
        HTTPStatus.BAD_REQUEST: {
            "description": (
                "Falta la cabecera 'Idempotency-Key' o su formato no es válido. "
                "No se registró nada."
            )
        },
        HTTPStatus.NOT_FOUND: {
            "description": "Alguna referencia del paquete no existe todavía."
        },
        HTTPStatus.CONFLICT: {
            "description": (
                "Conflicto. O la clave de idempotencia ya identifica un paquete "
                "con contenido distinto, o una referencia dejó de existir "
                "mientras se procesaba el paquete. No se guardó nada."
            )
        },
        HTTPStatus.UNPROCESSABLE_ENTITY: {
            "description": "El paquete no cumple el contrato o una regla del dominio."
        },
        HTTPStatus.INTERNAL_SERVER_ERROR: {
            "description": "Error interno. La transacción completa fue revertida."
        },
    },
    openapi_extra={"parameters": [PARAMETRO_DE_LA_CLAVE]},
)
def registrar_sesion_de_monitoreo(
    entrada: SesionMonitoreoEntrada,
    respuesta: Response,
    clave: str = Depends(exigir_clave_de_idempotencia),
    sesion_bd: Session = Depends(get_db),
) -> SesionMonitoreoCreada:
    """Persist one monitoring session and every reading it carries, exactly once.

    Five things happen here and nothing else: the service is called, the
    transaction is ended one way or the other, the event is recorded, failures
    become status codes, and the answer is built. The order of the steps inside
    the package -- claim first, then references, then rows -- belongs to
    ``procesar_ingesta_idempotente`` and is documented there.

    All data is fictitious and simulated. This endpoint has no authentication
    yet and is **not production-ready**.
    """
    try:
        procesado = procesar_ingesta_idempotente(
            sesion_bd, entrada, recurso=RECURSO_SESIONES_MONITOREO, clave=clave
        )
        if procesado.reproducido:
            # Reenvío reconocido: no se escribió una sola fila, y el rollback
            # cierra la transacción de lectura. Nunca un commit por este camino.
            sesion_bd.rollback()
            registrar_replay(
                RECURSO_SESIONES_MONITOREO, clave, procesado.resultado.id_sesion
            )
        else:
            sesion_bd.commit()
    except ColisionDeIdempotencia as error:
        sesion_bd.rollback()
        registrar_colision(RECURSO_SESIONES_MONITOREO, clave)
        # ``detalle`` is text this project wrote, and it never names the field
        # that differs: that would let a caller probe the stored package.
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT, detail=error.detalle
        ) from error
    except AnomaliaDeIdempotencia as error:
        # A claim that cannot be reproduced is never answered as a replay. The
        # exception's own ``detalle`` stays internal: what it describes is a
        # fault on this side of the wire, not something the caller did.
        sesion_bd.rollback()
        registrar_anomalia(RECURSO_SESIONES_MONITOREO, clave)
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=MENSAJE_INESPERADO
        ) from error
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
        respuesta_de_error = clasificar_error_de_base(error)
        registrar_fallo(error, respuesta_de_error)
        raise HTTPException(
            status_code=respuesta_de_error.status_code, detail=respuesta_de_error.detalle
        ) from error
    except Exception as error:  # noqa: BLE001 -- last resort, see below
        # Anything not foreseen still has to leave the database untouched and
        # the answer free of internals. The exception is neither formatted nor
        # attached to the response; only the safe diagnostic reaches the log.
        sesion_bd.rollback()
        respuesta_de_error = RespuestaDeError(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detalle=MENSAJE_INESPERADO,
        )
        registrar_fallo(error, respuesta_de_error)
        raise HTTPException(
            status_code=respuesta_de_error.status_code, detail=respuesta_de_error.detalle
        ) from error

    respuesta.headers[CABECERA_REPLAY] = (
        REPLAY_SI if procesado.reproducido else REPLAY_NO
    )
    # The one place the answer is built, from the one type that describes it.
    # Creation and replay reach this line with the same ``ResultadoIngesta``, so
    # a replay cannot differ from the first answer: there is no second
    # construction site where it could.
    return SesionMonitoreoCreada(
        id_sesion=procesado.resultado.id_sesion,
        lecturas_creadas=len(procesado.resultado.ids_lectura),
        ids_lectura=list(procesado.resultado.ids_lectura),
    )
