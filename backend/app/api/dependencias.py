"""Reusable authentication and authorisation dependencies (SCRUM-70).

Every protected route declares its allowed roles here and nowhere else. There is
no role comparison copied into a handler, no free-form role string, and no
default that lets an unknown role through: :func:`exigir_roles` is given an
explicit allowlist and refuses everything outside it.

**``auto_error=False``, deliberately.** ``HTTPBearer`` can raise its own 401,
and on the installed FastAPI it does produce 401 with ``WWW-Authenticate:
Bearer``. It is still switched off, for three reasons that have nothing to do
with that status code being wrong today: the challenge it builds carries no
``error="invalid_token"`` parameter, so RFC 6750 cannot be honoured through it;
the body would be the framework's ``Not authenticated`` rather than a message
this project wrote; and a behaviour owned by a dependency cannot change under us
when the framework is upgraded. With it off, every 401 in this application is
built by :func:`_no_autenticado` and every one of them is tested.

**401 and 403 mean different things and are never swapped.** 401 is "I do not
know who you are" -- no header, a scheme that is not Bearer, a token that does
not verify, or an account that no longer exists or is no longer active. 403 is
"I know exactly who you are and this role may not do this". Only 401 carries a
``WWW-Authenticate`` challenge; answering 403 with one would invite a client to
retry credentials that are perfectly valid.

**What this layer does not decide.** Whether the authenticated patient owns the
pregnancy she is sending readings for is **not** checked here and is not checked
anywhere in SCRUM-70. RBAC limits operations by role; row-level isolation
belongs to SCRUM-71.

**The clinical context (SCRUM-98).** :func:`contexto_actual` is the second half
of that story: it resolves which clinical profile the caller is and hands that
identity to PostgreSQL for the length of the request's transaction. It is a
separate dependency from :func:`usuario_actual` on purpose -- authentication
happens on every protected route, and a clinical context only where rows are at
stake -- and it composes the first rather than repeating it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from http import HTTPStatus
from typing import Literal

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import ConfiguracionJWT, exigir_configuracion_jwt
from app.db.session import get_db
from app.models.enums import NombreRol
from app.services import auditoria
from app.services.auditoria import AccionAuditada, FalloDeAuditoria
from app.services.errores import diagnostico_seguro
from app.db.contexto import instalar_contexto
from app.services.contexto import (
    ContextoClinico,
    ContextoNoResoluble,
    resolver_contexto,
)
from app.services.principal import PrincipalAutenticado, resolver_principal
from app.services.tokens import TokenInvalido, validar

registrador = logging.getLogger(__name__)

# ``auto_error=False`` makes this return ``None`` instead of raising, so the
# absent header and the wrong scheme reach our own handling. ``scheme_name`` and
# ``description`` are what Swagger shows in its Authorize dialog.
esquema_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="BearerJWT",
    description=(
        "Token de sesión obtenido en POST /api/v1/autenticacion/token. "
        "Se envía como 'Authorization: Bearer <token>'."
    ),
)

ESQUEMA_BEARER = "Bearer"

MENSAJE_NO_AUTENTICADO = "Se requiere una credencial de sesion valida."
MENSAJE_ROL_NO_AUTORIZADO = "El rol de la cuenta no permite esta operacion."

# RFC 6750: a request with no credential gets the bare challenge, and one whose
# credential was rejected gets ``error="invalid_token"``. The distinction is for
# the client -- "you sent nothing" against "what you sent did not work" -- and
# neither form says anything about *why* the token failed.
DESAFIO_SIN_CREDENCIAL = ESQUEMA_BEARER
DESAFIO_TOKEN_INVALIDO = f'{ESQUEMA_BEARER} error="invalid_token"'


def get_db_auditoria() -> Iterator[Session]:
    """A session for audit entries that must not share the request's transaction.

    A distinct callable and not ``Depends(get_db)`` twice: FastAPI caches a
    dependency per request, so asking for the same function again would hand
    back the very session whose rollback the entry has to survive. Delegating to
    ``get_db`` keeps the open/close contract in one place.
    """
    yield from get_db()


def obtener_configuracion_jwt() -> ConfiguracionJWT:
    """The validated JWT configuration for this request.

    Re-validated rather than cached in a module global so there is no path where
    a process keeps serving with configuration that stopped being acceptable,
    and so a test can override this one dependency instead of the environment.
    ``app.main`` already ran the same check at start-up; this is the same rule
    applied again, not a different one.
    """
    return exigir_configuracion_jwt()


# Where a database failure happened, as a fixed label. The type admits these two
# literals and nothing else, so no caller can pass text that came from a request.
CONTEXTO_AUTENTICACION = "autenticacion"
CONTEXTO_RESOLUCION_DEL_PRINCIPAL = "resolucion del principal"
CONTEXTO_LECTURA_CLINICA = "lectura clinica"
ContextoDeFallo = Literal[
    "autenticacion", "resolucion del principal", "lectura clinica"
]

_FORMATO_FALLO_DE_BASE = "Fallo de base de datos en %s: %s"

# The public body of every 500 on the identity path. Deliberately its own text:
# ``app.services.errores.MENSAJE_INESPERADO`` belongs to the ingestion endpoint
# and says that "the session could not be registered" and "the whole transaction
# was rolled back", which is false for a login or for ``/yo`` and would send
# whoever reads it looking in the wrong place. This one says nothing about what
# failed or why.
MENSAJE_ERROR_INTERNO = "Error interno del servidor. No se pudo completar la operacion."


def registrar_fallo_de_base(contexto: ContextoDeFallo, error: SQLAlchemyError) -> None:
    """Log a database failure on the identity path with the safe diagnostic only.

    ``diagnostico_seguro`` keeps the exception class and identifiers PostgreSQL
    took from our own schema -- SQLSTATE, constraint, table, column -- and has no
    field for the statement, its parameters, the driver's message or the
    connection URL. Nothing else is formatted: not ``str(error)``, not
    ``repr(error)``, and no ``exc_info``, because the traceback of a driver error
    carries the statement and its bound parameters -- on the login path, the
    email that was typed.

    The label is this project's, not the ingestion helper's: a login failure must
    not be recorded as a failure "registering the session".
    """
    registrador.error(
        _FORMATO_FALLO_DE_BASE, contexto, diagnostico_seguro(error).como_texto()
    )


def fallo_de_base(
    sesion_bd: Session, contexto: ContextoDeFallo, error: SQLAlchemyError
) -> HTTPException:
    """Undo, record safely, and build the generic 500. The caller raises it.

    The rollback is itself guarded: on a connection that just failed, releasing
    the transaction can fail too, and an unguarded rollback would let a second
    driver error travel to the server -- statement, parameters and all -- which
    is precisely what this function exists to stop. That second failure is
    recorded the same sanitised way; it is not swallowed.

    Callers raise the result ``from None``, so the driver exception is not even
    attached as the cause of the HTTP error.
    """
    try:
        sesion_bd.rollback()
    except SQLAlchemyError as error_al_revertir:
        registrar_fallo_de_base(contexto, error_al_revertir)

    registrar_fallo_de_base(contexto, error)
    return HTTPException(
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=MENSAJE_ERROR_INTERNO
    )


def _no_autenticado(desafio: str, detalle: str) -> HTTPException:
    """A 401 with its challenge. The only place this application builds one."""
    return HTTPException(
        status_code=HTTPStatus.UNAUTHORIZED,
        detail=detalle,
        headers={"WWW-Authenticate": desafio},
    )


def usuario_actual(
    credenciales: HTTPAuthorizationCredentials | None = Depends(esquema_bearer),
    configuracion: ConfiguracionJWT = Depends(obtener_configuracion_jwt),
    sesion_bd: Session = Depends(get_db),
) -> PrincipalAutenticado:
    """The caller, verified end to end, or a 401 that says nothing else.

    Six steps, in this order, and the order is the security:

    1. the ``Authorization`` header is read -- absent, empty or not Bearer ends
       here;
    2. the token's signature, algorithm, expiry and required claims are checked
       by ``app.services.tokens``, which trusts no claim before the signature;
    3. ``sub`` is confirmed to be a decimal ASCII string and becomes an integer;
    4. the account is read from PostgreSQL;
    5. its **current** state and **current** role are what the principal carries
       -- never a role the token claimed, because the token carries none;
    6. a deleted or deactivated account is refused even with a perfectly valid
       token.

    This is a read. Nothing here commits, and the session it borrows is the
    request's own, so a protected read leaves no transaction behind.
    """
    if credenciales is None or credenciales.scheme.lower() != ESQUEMA_BEARER.lower():
        raise _no_autenticado(DESAFIO_SIN_CREDENCIAL, MENSAJE_NO_AUTENTICADO)

    try:
        id_usuario = validar(credenciales.credentials, configuracion)
    except TokenInvalido as error:
        raise _no_autenticado(DESAFIO_TOKEN_INVALIDO, error.detalle) from error

    try:
        principal = resolver_principal(sesion_bd, id_usuario)
    except SQLAlchemyError as error:
        # A database failure is not an authentication failure: answering 401
        # would tell a valid caller its credential is bad. It is a sanitised 500.
        raise fallo_de_base(
            sesion_bd, CONTEXTO_RESOLUCION_DEL_PRINCIPAL, error
        ) from None

    if principal is None:
        # Deleted, never existed, or deactivated. The three are one answer: this
        # token no longer speaks for anybody.
        raise _no_autenticado(DESAFIO_TOKEN_INVALIDO, MENSAJE_NO_AUTENTICADO)

    return principal


def exigir_roles(
    *roles_admitidos: NombreRol,
    entidad: str,
) -> Callable[..., PrincipalAutenticado]:
    """Build the dependency that guards one operation with an explicit allowlist.

    ``roles_admitidos`` is a closed set of :class:`NombreRol` members, not
    strings: a typo is an ``AttributeError`` at import instead of a role that
    silently never matches. An empty allowlist is refused outright -- a guard
    that admits nobody is a mistake, not a policy.

    ``entidad`` is the stable technical name of the resource being protected,
    recorded in the denial entry. It is a table name and not an HTTP route:
    ``auditoria_log.id_entidad_afectada`` identifies a row, so the column beside
    it has to name something rows belong to.

    A denial is audited in its own committed transaction, because the handler it
    blocks never runs and an entry waiting for that commit would be lost. If the
    entry cannot be written the answer stays 403 -- the operation is refused
    either way, and turning a correct refusal into a 500 would tell the caller
    less while granting nothing.
    """
    if not roles_admitidos:
        raise ValueError("Una dependencia de autorizacion debe admitir algun rol.")

    admitidos = frozenset(roles_admitidos)

    def verificar_rol(
        peticion: Request,
        principal: PrincipalAutenticado = Depends(usuario_actual),
        sesion_auditoria: Session = Depends(get_db_auditoria),
    ) -> PrincipalAutenticado:
        if principal.rol in admitidos:
            return principal

        try:
            auditoria.registrar_con_commit(
                sesion_auditoria,
                AccionAuditada.ACCESO_DENEGADO_ROL,
                id_usuario=principal.id_usuario,
                ip_origen=auditoria.direccion_de_origen(
                    peticion.client.host if peticion.client else None
                ),
                nombre_entidad=entidad,
                id_entidad=None,
            )
        except FalloDeAuditoria:
            # Already logged, sanitised, by the audit service. The refusal stands:
            # nothing was granted, so there is no failure to close.
            pass

        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN, detail=MENSAJE_ROL_NO_AUTORIZADO
        )

    return verificar_rol


# The body of every refusal on the clinical-context path. One sentence, the same
# for a missing link, a link the role does not allow and an ambiguous one: the
# three are "this account has no clinical scope", and telling them apart would
# publish the shape of somebody's record to whoever probes the endpoint.
MENSAJE_SIN_CONTEXTO_CLINICO = (
    "La cuenta no tiene un perfil clinico utilizable para esta operacion."
)


def _contexto_para(
    peticion: Request,
    principal: PrincipalAutenticado,
    sesion_bd: Session,
    sesion_auditoria: Session,
) -> ContextoClinico:
    """Instala la identidad en PostgreSQL y resuelve el perfil clinico.

    Tres pasos, y el orden es la seguridad:

    1. el ``id_usuario`` **que el token ya probo** se entrega a PostgreSQL con
       ``set_config(..., true)`` -- local a la transaccion, de modo que muere
       con ella y no puede viajar a la siguiente peticion por una conexion
       reutilizada;
    2. el perfil se resuelve desde las relaciones que guarda PostgreSQL, nunca
       desde algo que el cliente enviara;
    3. se devuelve el contexto tipado, que un handler no puede ensanchar.

    **Por que instalar va primero.** El paso 2 consulta ``usuario_paciente`` y
    ``usuario_medico``, y desde esta revision las dos llevan FORCE RLS con una
    politica que filtra por ``seguridad.usuario_actual_id()``. Resolver antes de
    instalar dejaba esa consulta sin identidad: bajo un rol realmente
    restringido devolvia cero filas, el perfil salia «sin vinculo» y toda ruta
    de PACIENTE o de MEDICO respondia 403. No se vio antes porque las suites
    HTTP se conectaban con el usuario de inicializacion del contenedor, que es
    superusuario y no esta sujeto a ninguna politica.

    Instalar primero no adelanta ninguna autorizacion. Lo que se instala es la
    identidad autenticada, no un permiso: el valor sale de
    ``PrincipalAutenticado.id_usuario``, que SCRUM-70 ya verifico contra la
    firma del token y contra el estado de la cuenta. Las politicas siguen
    decidiendo que filas corresponden a esa identidad, y el paso 2 sigue siendo
    quien puede negar el contexto.

    ``sesion_bd`` es la sesion de la peticion, asi que la identidad queda
    instalada exactamente en la conexion que ejecutara las sentencias
    protegidas. Pedir ``get_db`` otra vez no abre una segunda sesion: FastAPI
    cachea la dependencia por peticion, y por eso la auditoria usa
    ``get_db_auditoria``.

    **Un rechazo es 403 y no dice mas.** La identidad es valida -- el token
    verifico y la cuenta esta activa --, asi que 401 seria falso; la fila que el
    llamante queria puede ni existir, asi que 404 afirmaria algo que esta capa no
    ha comprobado. Lo cierto es que la cuenta no tiene alcance clinico, y eso es
    403. La entrada que explica por que va a la traza de auditoria, donde una
    administradora puede leerla, y no a la respuesta.
    """
    try:
        instalar_contexto(sesion_bd, principal.id_usuario)
    except SQLAlchemyError as error:
        # El contexto no pudo instalarse, asi que las sentencias protegidas
        # correrian sin identidad. Bajo las politicas eso significa cero filas,
        # que es seguro pero silencioso; un 500 saneado lo dice.
        raise fallo_de_base(
            sesion_bd, CONTEXTO_RESOLUCION_DEL_PRINCIPAL, error
        ) from None

    try:
        contexto = resolver_contexto(sesion_bd, principal)
    except ContextoNoResoluble as error:
        try:
            auditoria.registrar_con_commit(
                sesion_auditoria,
                AccionAuditada.CONTEXTO_CLINICO_AUSENTE,
                id_usuario=principal.id_usuario,
                ip_origen=auditoria.direccion_de_origen(
                    peticion.client.host if peticion.client else None
                ),
                nombre_entidad=auditoria.ENTIDAD_USUARIO,
                id_entidad=str(principal.id_usuario),
            )
        except FalloDeAuditoria:
            # Ya registrado, saneado, por el servicio de auditoria. El rechazo
            # se mantiene: no se concedio nada, asi que no hay nada que cerrar.
            pass
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN, detail=MENSAJE_SIN_CONTEXTO_CLINICO
        ) from error

    # ``resolver_contexto`` deriva el perfil del mismo ``id_usuario`` que se
    # instalo, asi que no hay una segunda identidad que instalar ni un valor que
    # corregir: la asercion lo deja escrito para que un cambio futuro en el
    # resolutor no pueda separarlos en silencio.
    assert contexto.id_usuario == principal.id_usuario

    return contexto


def contexto_actual(
    peticion: Request,
    principal: PrincipalAutenticado = Depends(usuario_actual),
    sesion_bd: Session = Depends(get_db),
    sesion_auditoria: Session = Depends(get_db_auditoria),
) -> ContextoClinico:
    """El contexto de una ruta que no restringe el rol. Ver :func:`_contexto_para`."""
    return _contexto_para(peticion, principal, sesion_bd, sesion_auditoria)


def exigir_contexto_de_rol(
    *roles_admitidos: NombreRol, entidad: str
) -> Callable[..., ContextoClinico]:
    """El rol y el contexto clinico, en una dependencia y en ese orden.

    Componer las dos en vez de pedirlas por separado fija la precedencia: el rol
    se comprueba primero, asi que una identidad que no puede hacer la operacion
    recibe 403 sin que se resuelva ni se instale nada. Solo despues se busca el
    perfil clinico y se entrega la identidad a PostgreSQL.

    Lo que devuelve es el contexto, no el principal: un handler que recibiera
    ambos podria filtrar por uno y auditar con el otro. El contexto lleva
    ``id_usuario``, asi que no hace falta el segundo.
    """
    verificar_rol = exigir_roles(*roles_admitidos, entidad=entidad)

    def resolver(
        peticion: Request,
        principal: PrincipalAutenticado = Depends(verificar_rol),
        sesion_bd: Session = Depends(get_db),
        sesion_auditoria: Session = Depends(get_db_auditoria),
    ) -> ContextoClinico:
        return _contexto_para(peticion, principal, sesion_bd, sesion_auditoria)

    return resolver
