"""Minimal clinical reads for PACIENTE and MEDICO (SCRUM-98, sub-phase 4).

Three routes, all of them reads, and none of them takes a patient or a physician
identifier from the caller. The scope comes from :class:`ContextoClinico`, which
the shared guard resolves from the verified token and installs in the very
transaction these queries run in.

**Three routes and not four, and that is a decision rather than an omission.**
An earlier draft of this sub-phase proposed a fourth, ``GET
/clinico/embarazos/{id}``, returning one episode's detail. It was dropped on
review: the listing already returns every field that detail route would have
had, so the only thing it added was a second way to reach the same row -- a
second place to get the scope check right, a second 404 to keep
indistinguishable from a foreign one, and a second entry point to audit. The
three that remain cover what the ticket has to demonstrate: the longitudinal
listing, the scope within one episode, and the readings within one session.
Surface that buys nothing is surface that can still be got wrong.

**Who may reach this router.** PACIENTE and MEDICO. **ADMIN is refused**, and the
refusal is deliberate rather than incidental: an administrative credential is a
technical responsibility over accounts, and admitting it here would turn it into
a way into clinical data. The role guard answers it 403 and records
``ACCESO_DENEGADO_ROL`` with the identity that tried.

**The two scopes.**

* A patient reads all the pregnancies of her own profile, finished ones included,
  and each episode stays separate: sessions are always requested within one
  ``id_embarazo``.
* A physician reads a pregnancy only through a direct ``SeguimientoClinico`` that
  is active and current today, and inside it reads the whole history. Sharing a
  clinic grants nothing.

**A foreign resource and a non-existent one answer the same 404**, with the same
sentence. The only thing that differs between the two answers is the identifier
the caller itself supplied.

**A denial is recorded; a successful read is not.** Every 404 these routes
produce writes one ``ACCESO_CLINICO_DENEGADO`` entry: who asked, from which
address, for which entity and with which identifier. That is the whole entry --
no body, no biometric value, no token, no credential, and nothing the database
answered. It records that an identified account reached for something outside its
scope, which is what lets somebody notice an account walking the identifier
space; recording *what was there* would be the leak the 404 exists to prevent.

The entry goes in a **transaction of its own**, on the audit session and not on
the one the read used, for two reasons: the read never happened, so there is no
business transaction to join, and the denial has already been decided, so the
answer must not depend on the trail. If the write fails, the failure is recorded
through the sanitised channel and the caller still gets its 404 -- degrading a
denial into a 500 would turn a broken audit table into a way of telling foreign
resources apart from missing ones.

A successful read writes nothing. A trail that grew with every legitimate query
would bury the denials it exists to surface, and the rows a caller is entitled to
read are not an event.

Two more entries can appear before a handler runs, and the guard writes them:
``ACCESO_DENEGADO_ROL`` when the role is wrong, ``CONTEXTO_CLINICO_AUSENTE`` when
the account has no resolvable clinical profile. With ``ACCESO_CLINICO_DENEGADO``
the closed catalogue stands at ten.

**Nothing else is written.** The business session is closed by the dependency and
its transaction dies with the request, which is also what disposes of the
installed context.

All data in this project is simulated and completely fictitious.
"""

from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.dependencias import (
    CONTEXTO_LECTURA_CLINICA,
    exigir_contexto_de_rol,
    fallo_de_base,
    get_db_auditoria,
)
from app.db.session import get_db
from app.models.enums import NombreRol
from app.schemas.clinico import EmbarazoResumen, LecturaResumen, SesionResumen
from app.services.consulta_clinica import (
    RecursoClinicoInexistente,
    listar_embarazos,
    listar_lecturas,
    listar_sesiones,
)
from app.services import auditoria
from app.services.auditoria import AccionAuditada, FalloDeAuditoria
from app.services.contexto import ContextoClinico
from app.services.tokens import ID_USUARIO_MAXIMO

router = APIRouter(prefix="/api/v1/clinico", tags=["clinico"])

# Los identificadores son claves INTEGER de PostgreSQL. Acotarlos en la ruta
# convierte un id imposible en un 422 en vez de un error del driver.
IDENTIFICADOR_MAXIMO = ID_USUARIO_MAXIMO

# La guardia de las tres rutas. PACIENTE y MEDICO; ADMIN no aparece, y esa
# ausencia es el contrato. La entidad que nombra un rechazo por rol es
# ``embarazo``, porque el episodio es el recurso del que cuelga todo lo demas.
EXIGIR_LECTURA_CLINICA = exigir_contexto_de_rol(
    NombreRol.PACIENTE, NombreRol.MEDICO, entidad=auditoria.ENTIDAD_EMBARAZO
)

RESPUESTAS_COMUNES = {
    HTTPStatus.UNAUTHORIZED: {
        "description": "Sin credencial valida, o cuenta desactivada."
    },
    HTTPStatus.FORBIDDEN: {
        "description": (
            "El rol no puede leer informacion clinica, o la cuenta no tiene un "
            "perfil clinico resoluble."
        )
    },
    HTTPStatus.NOT_FOUND: {
        "description": (
            "El recurso no existe o no esta al alcance de quien pregunta. Las "
            "dos situaciones responden lo mismo a proposito."
        )
    },
}


def _no_encontrado(
    peticion: Request,
    sesion_auditoria: Session,
    contexto: ContextoClinico,
    error: RecursoClinicoInexistente,
) -> HTTPException:
    """Deja constancia de la denegacion y construye el 404. Quien llama lo lanza.

    El texto de la respuesta sale del servicio, que es quien decide que ajeno e
    inexistente digan lo mismo; esta funcion no lo toca. Lo unico que anade es la
    entrada de auditoria, y la anade **antes** de devolver para que no exista un
    camino en el que la respuesta salga sin haberla intentado.

    Los cuatro datos de la entrada son los de la peticion: la cuenta que el token
    identifico, la direccion que el servidor observo, la entidad que la ruta
    nombra y el identificador que quien llama envio. Ni el cuerpo, ni una sola
    lectura, ni el token.

    Un fallo de la auditoria no cambia la respuesta. Ya esta registrado y
    saneado por el servicio; convertirlo en un 500 haria que una tabla de
    auditoria rota distinguiera un recurso ajeno de uno inexistente, que es
    exactamente lo que este 404 existe para impedir.
    """
    try:
        auditoria.registrar_con_commit(
            sesion_auditoria,
            AccionAuditada.ACCESO_CLINICO_DENEGADO,
            id_usuario=contexto.id_usuario,
            ip_origen=auditoria.direccion_de_origen(
                peticion.client.host if peticion.client else None
            ),
            nombre_entidad=error.entidad,
            id_entidad=str(error.id_solicitado),
        )
    except FalloDeAuditoria:
        pass

    return HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(error))


@router.get(
    "/embarazos",
    response_model=list[EmbarazoResumen],
    summary="Listar los episodios de embarazo que la cuenta puede leer",
    response_description="Los episodios al alcance, del mas reciente al mas antiguo",
    responses=RESPUESTAS_COMUNES,
)
def listar_mis_embarazos(
    contexto: ContextoClinico = Depends(EXIGIR_LECTURA_CLINICA),
    sesion_bd: Session = Depends(get_db),
) -> list[EmbarazoResumen]:
    """Los episodios al alcance de quien pregunta. Nunca los de nadie mas.

    Una lista vacia es una respuesta legitima: una gestante sin embarazos
    registrados, o un medico sin asignaciones vigentes hoy. No es un 404, porque
    la pregunta -- «que puedo leer» -- si tiene respuesta.
    """
    try:
        return [
            EmbarazoResumen.model_validate(fila)
            for fila in listar_embarazos(sesion_bd, contexto)
        ]
    except SQLAlchemyError as error:
        raise fallo_de_base(sesion_bd, CONTEXTO_LECTURA_CLINICA, error) from None


@router.get(
    "/embarazos/{id_embarazo}/sesiones",
    response_model=list[SesionResumen],
    summary="Listar las sesiones de monitoreo de un episodio",
    response_description="Las sesiones del episodio, de la mas reciente a la mas antigua",
    responses=RESPUESTAS_COMUNES,
)
def listar_sesiones_del_embarazo(
    peticion: Request,
    id_embarazo: int = Path(ge=1, le=IDENTIFICADOR_MAXIMO),
    contexto: ContextoClinico = Depends(EXIGIR_LECTURA_CLINICA),
    sesion_bd: Session = Depends(get_db),
    sesion_auditoria: Session = Depends(get_db_auditoria),
) -> list[SesionResumen]:
    """Las sesiones de un episodio, y de ese episodio solamente.

    Pedir las sesiones **dentro de** un ``id_embarazo`` es lo que mantiene los
    episodios separados: una gestante con dos embarazos no recibe nunca una serie
    que los mezcle, y para ver el otro tiene que nombrarlo.

    Un medico con asignacion vigente recibe todo el historial del episodio, no
    solo el tramo que solapa con su asignacion.
    """
    try:
        return [
            SesionResumen.model_validate(fila)
            for fila in listar_sesiones(sesion_bd, contexto, id_embarazo)
        ]
    except RecursoClinicoInexistente as error:
        raise _no_encontrado(
            peticion, sesion_auditoria, contexto, error
        ) from None
    except SQLAlchemyError as error:
        raise fallo_de_base(sesion_bd, CONTEXTO_LECTURA_CLINICA, error) from None


@router.get(
    "/sesiones/{id_sesion}/lecturas",
    response_model=list[LecturaResumen],
    summary="Listar las lecturas biometricas de una sesion",
    response_description="Las lecturas de la sesion, en orden de captura",
    responses=RESPUESTAS_COMUNES,
)
def listar_lecturas_de_la_sesion(
    peticion: Request,
    id_sesion: int = Path(ge=1, le=IDENTIFICADOR_MAXIMO),
    contexto: ContextoClinico = Depends(EXIGIR_LECTURA_CLINICA),
    sesion_bd: Session = Depends(get_db),
    sesion_auditoria: Session = Depends(get_db_auditoria),
) -> list[LecturaResumen]:
    """Las lecturas de una sesion, si el episodio del que cuelga es legible."""
    try:
        return [
            LecturaResumen.model_validate(fila)
            for fila in listar_lecturas(sesion_bd, contexto, id_sesion)
        ]
    except RecursoClinicoInexistente as error:
        raise _no_encontrado(
            peticion, sesion_auditoria, contexto, error
        ) from None
    except SQLAlchemyError as error:
        raise fallo_de_base(sesion_bd, CONTEXTO_LECTURA_CLINICA, error) from None


# Nombrado para que una prueba pueda importar exactamente lo que produccion usa.
__all__ = ["router", "EXIGIR_LECTURA_CLINICA"]
