"""Authentication endpoints: obtaining a session token and reading one's identity.

**The login body is JSON, not an OAuth2 form.** Every other contract in this API
is a typed Pydantic model, the only client that exists sends JSON, and a form
would need an extra dependency to parse. A JWT is a token *format*; issuing one
does not make this project an OAuth2 provider, and the documentation does not
claim it is. Swagger's Authorize dialog uses the ``BearerJWT`` scheme declared in
``app.api.dependencias``: paste the token, and protected routes work.

**Invalid credentials are one answer, whatever went wrong.** Unknown account,
wrong password and deactivated account produce the same status, the same body
and the same header, and they all reach that answer *after* an Argon2id
verification -- see ``app.services.principal.autenticar``, where the absence of
an early return is the whole point. Telling them apart would turn this endpoint
into an oracle for which accounts exist.

**Transport.** The token is a bearer credential: whoever holds it is the user
until it expires. This MVP serves plain HTTP on a local address and implements
no TLS, so a token must not travel outside the controlled environment without
HTTPS. A signed JWT is not an encrypted one -- its payload is readable by
anybody holding it, which is why nothing personal is ever put in it.

All accounts are fictitious and simulated.
"""

from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.dependencias import (
    CONTEXTO_AUTENTICACION,
    DESAFIO_SIN_CREDENCIAL,
    MENSAJE_ERROR_INTERNO,
    fallo_de_base,
    get_db_auditoria,
    obtener_configuracion_jwt,
    usuario_actual,
)
from app.config import ConfiguracionJWT
from app.db.session import get_db
from app.schemas.autenticacion import CredencialesEntrada, IdentidadActual, TokenEmitido
from app.services import auditoria
from app.services.auditoria import AccionAuditada, FalloDeAuditoria
from app.services.principal import PrincipalAutenticado, autenticar
from app.services.tokens import emitir

router = APIRouter(prefix="/api/v1/autenticacion", tags=["autenticacion"])

# One sentence for every way a credential can be wrong. It never says which of
# the three situations occurred.
MENSAJE_CREDENCIALES_INVALIDAS = "Credenciales invalidas."

# Rutas cuyo cuerpo lleva una credencial. El manejador de abajo les quita el eco
# del valor rechazado; ninguna otra ruta cambia de comportamiento.
RUTAS_CON_CREDENCIALES = frozenset({f"{router.prefix}/token"})

# Clave que Pydantic usa para devolver el valor que no paso la validacion.
CAMPO_DE_ECO = "input"


def lleva_credenciales(peticion: Request) -> bool:
    """Si el 422 de esta peticion podria contener una credencial.

    **Se decide por la ruta resuelta, no por la URL.** ``request.url.path``
    incluye el ``root_path`` cuando la API se sirve bajo un prefijo, de modo que
    ``/prefijo/api/v1/autenticacion/token`` dejaba de coincidir con la lista y el
    422 volvia a devolver la contrasena. ``scope["route"]`` es el ``APIRoute`` que
    Starlette eligio, y su ``path`` es la plantilla declarada, identica con
    prefijo o sin el.

    **Si la ruta no esta resuelta, se sanea.** Un ``RequestValidationError`` lo
    lanza FastAPI dentro de una ruta ya elegida, asi que con las rutas reales de
    esta API ``scope["route"]`` siempre existe -- las pruebas lo fijan para la
    ingesta --. No tenerla seria una situacion no prevista, y en ella el lado
    seguro es no devolver valores. El filtro no se amplia por eso a otras rutas:
    las resueltas que no estan en la lista conservan su 422 intacto.
    """
    ruta = getattr(peticion.scope.get("route"), "path", None)
    return ruta is None or ruta in RUTAS_CON_CREDENCIALES


async def sanear_errores_de_validacion(
    peticion: Request, excepcion: RequestValidationError
) -> JSONResponse:
    """Quita del 422 el eco del valor rechazado, en las rutas con credenciales.

    **El problema que resuelve es concreto y estaba ahi.** Pydantic adjunta a
    cada error el valor que lo provoco, en un campo ``input``, y FastAPI lo
    publica tal cual. ``SecretStr`` enmascara la contrasena **una vez construido
    el modelo**, asi que no protege nada cuando la validacion falla por otro
    campo: una peticion sin ``email`` devolvia un 422 con la contrasena en claro
    dentro del cuerpo de la respuesta -- y de ahi a un log, a una captura de
    pantalla o a un reporte de CI.

    El saneamiento se limita a las rutas de :data:`RUTAS_CON_CREDENCIALES`. El
    422 del endpoint de ingesta conserva exactamente el contrato que SCRUM-62
    definio y sus pruebas comprueban; cambiarlo desde aqui seria tocar algo que
    este ticket no vino a tocar.

    Lo que se quita es el valor, no el diagnostico: el campo que fallo, el tipo
    de error y su mensaje siguen ahi, que es lo que un cliente necesita para
    corregir su peticion.
    """
    errores = jsonable_encoder(excepcion.errors())

    if lleva_credenciales(peticion):
        errores = [
            {clave: valor for clave, valor in error.items() if clave != CAMPO_DE_ECO}
            for error in errores
        ]

    return JSONResponse(
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY, content={"detail": errores}
    )


RESPUESTA_401_LOGIN = {
    "description": (
        "Credenciales inválidas. La respuesta es idéntica para una cuenta "
        "inexistente, una contraseña incorrecta y una cuenta desactivada."
    )
}


@router.post(
    "/token",
    response_model=TokenEmitido,
    status_code=HTTPStatus.OK,
    summary="Obtener un token de sesión con credenciales simuladas",
    response_description="Token de sesión, su tipo y su vigencia en segundos",
    responses={
        HTTPStatus.UNAUTHORIZED: RESPUESTA_401_LOGIN,
        HTTPStatus.UNPROCESSABLE_ENTITY: {
            "description": "El cuerpo no cumple el contrato de credenciales."
        },
        HTTPStatus.INTERNAL_SERVER_ERROR: {
            "description": "Error interno. No se emitió ningún token."
        },
    },
)
def obtener_token(
    credenciales: CredencialesEntrada,
    peticion: Request,
    configuracion: ConfiguracionJWT = Depends(obtener_configuracion_jwt),
    sesion_bd: Session = Depends(get_db),
    sesion_auditoria: Session = Depends(get_db_auditoria),
) -> TokenEmitido:
    """Verify simulated credentials and hand back a signed session token.

    The audit entry for a successful login is committed **before** the token is
    built. That order is the fail-closed rule of this ticket: an access granted
    without a trace is precisely what the audit trail exists to prevent, so if
    the entry cannot be written, no token is issued.

    A failed login is also recorded, with ``id_usuario`` left NULL -- the column
    is nullable for exactly this case. The identifier that was typed is **not**
    stored, in the row or in a log: keeping it to "know who tried" would build
    the list of attempted addresses this endpoint refuses to leak.
    """
    origen = auditoria.direccion_de_origen(
        peticion.client.host if peticion.client else None
    )
    try:
        principal = autenticar(
            sesion_bd, credenciales.email, credenciales.password.get_secret_value()
        )
    except SQLAlchemyError as error:
        # The statement that failed carries the typed email as a bound
        # parameter. Uncaught, the driver error would reach the server log with
        # it; here it becomes a sanitised record and a generic 500.
        raise fallo_de_base(sesion_bd, CONTEXTO_AUTENTICACION, error) from None

    if principal is None:
        try:
            auditoria.registrar_con_commit(
                sesion_auditoria,
                AccionAuditada.LOGIN_FALLIDO,
                id_usuario=None,
                ip_origen=origen,
            )
        except FalloDeAuditoria:
            # Already logged, sanitised. The refusal stands: nothing was granted.
            pass
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED,
            detail=MENSAJE_CREDENCIALES_INVALIDAS,
            headers={"WWW-Authenticate": DESAFIO_SIN_CREDENCIAL},
        )

    try:
        auditoria.registrar_con_commit(
            sesion_auditoria,
            AccionAuditada.LOGIN_EXITOSO,
            id_usuario=principal.id_usuario,
            ip_origen=origen,
            nombre_entidad=auditoria.ENTIDAD_USUARIO,
            id_entidad=str(principal.id_usuario),
        )
    except FalloDeAuditoria as error:
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=MENSAJE_ERROR_INTERNO
        ) from error

    token = emitir(principal.id_usuario, configuracion)
    return TokenEmitido(
        access_token=token.access_token, expires_in=token.expira_en_segundos
    )


@router.get(
    "/yo",
    response_model=IdentidadActual,
    status_code=HTTPStatus.OK,
    summary="Identidad técnica de la sesión actual",
    response_description="Identificador interno y rol vigente de la cuenta",
    responses={
        HTTPStatus.UNAUTHORIZED: {
            "description": (
                "Falta la credencial, su esquema no es Bearer, el token no es "
                "válido o la cuenta ya no existe o está desactivada."
            )
        },
        HTTPStatus.INTERNAL_SERVER_ERROR: {
            "description": "Error interno al resolver la identidad."
        },
    },
)
def identidad_actual(
    principal: PrincipalAutenticado = Depends(usuario_actual),
) -> IdentidadActual:
    """Who the caller is, as far as this API is concerned, and nothing more.

    Two fields. It exists so an authenticated client -- the simulated edge node
    among them -- can confirm that its credential works and which role it
    carries before doing anything else. It is **not** evidence of differentiated
    business permissions: those are demonstrated on the operations themselves.

    Every role may call it, because it returns only the caller's own identity.
    It never grows a name, a national id, a contact, a clinical profile or a
    list of patients or clinics.
    """
    return IdentidadActual(id_usuario=principal.id_usuario, rol=principal.rol)
