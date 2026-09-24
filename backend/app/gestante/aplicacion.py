"""Ensamblado del adaptador de la interfaz de la gestante (SCRUM-72).

Una aplicacion FastAPI **propia y separada** de ``app.main``. La separacion es
lo que permite que este ticket no toque la API central: la aplicacion central no
gana rutas, no gana CORS y no gana middleware.

**Por que no se importa ``app.main``.** Ese modulo llama a
``exigir_configuracion_jwt()`` mientras se importa, y con razon: la API se niega
a arrancar sin material de firma. Este adaptador no firma ni verifica ningun
token --le pregunta al servidor central por HTTP-- asi que exigirle un secreto
que no usa haria que la interfaz dejara de abrirse por una configuracion que no
le concierne.

**El 422 de esta aplicacion no devuelve valores en la ruta con credenciales.**
Pydantic adjunta a cada error el valor que lo provoco, y FastAPI lo publica tal
cual. ``SecretStr`` enmascara la contrasena *una vez construido el modelo*, asi
que no protege nada cuando la validacion falla por otro campo: una peticion sin
``email`` devolveria un 422 con la contrasena en claro dentro del cuerpo. Es
exactamente el problema que ``app.api.v1.autenticacion`` resolvio para la API
central, y aqui se resuelve igual para esta aplicacion, sin tocar aquella.

Todas las cuentas y los datos son ficticios y simulados.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from http import HTTPStatus

import httpx
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.gestante.central import ClienteCentral, ClienteCentralHTTP
from app.gestante.config import GestanteSettings, cargar_settings_gestante
from app.gestante.rutas import ContextoAdaptador, crear_router
from app.gestante.sesion import ahora_utc

TITULO = "FetalAlert — adaptador de la interfaz de la gestante"

# Rutas de esta aplicacion cuyo cuerpo lleva una credencial. Hoy es una.
RUTAS_CON_CREDENCIALES = frozenset({"/adaptador/iniciar-sesion"})

# Clave con la que Pydantic devuelve el valor que no paso la validacion.
CAMPO_DE_ECO = "input"


def lleva_credenciales(peticion: Request) -> bool:
    """Si el 422 de esta peticion podria contener una credencial.

    Se decide por la ruta resuelta y no por la URL, por la misma razon que en la
    API central: ``request.url.path`` incluye el prefijo bajo el que se sirva la
    aplicacion, mientras que ``scope['route'].path`` es la plantilla declarada,
    identica con prefijo o sin el.

    Si la ruta no esta resuelta se sanea igualmente: es una situacion no
    prevista, y en ella el lado seguro es no devolver valores.
    """
    ruta = getattr(peticion.scope.get("route"), "path", None)
    return ruta is None or ruta in RUTAS_CON_CREDENCIALES


async def sanear_errores_de_validacion(
    peticion: Request, excepcion: RequestValidationError
) -> JSONResponse:
    """Quita del 422 el eco del valor rechazado en las rutas con credenciales.

    Lo que se quita es el valor, no el diagnostico: el campo que fallo, el tipo
    de error y su mensaje siguen ahi, que es lo que hace falta para corregir la
    peticion.
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


def crear_cliente_http(settings: GestanteSettings) -> httpx.Client:
    """El cliente HTTP hacia la API central.

    Sin cabeceras por omision: este cliente no lleva ninguna credencial propia.
    El token de la persona que inicia sesion se adjunta peticion a peticion en
    ``ClienteCentralHTTP.identidad``, y ``EDGE_API_TOKEN`` no se lee aqui --es
    del nodo edge, y este proceso no lo necesita--.
    """
    return httpx.Client(
        base_url=settings.api_base_url, timeout=settings.http_timeout
    )


def crear_aplicacion(
    *,
    settings: GestanteSettings | None = None,
    cliente_central: ClienteCentral | None = None,
    reloj: Callable[[], datetime] | None = None,
    constructor_cliente_edge: (
        Callable[[GestanteSettings, str], httpx.Client] | None
    ) = None,
) -> FastAPI:
    """Construye el adaptador.

    Los cuatro argumentos existen para las pruebas y tienen valores reales por
    omision, de modo que nada en produccion dependa de una costura de prueba:

    * ``settings`` se lee del entorno si no se pasa;
    * ``cliente_central`` se construye sobre httpx si no se pasa, y entonces
      esta aplicacion es su duena y lo cierra al apagarse;
    * ``reloj`` es el reloj real si no se pasa;
    * ``constructor_cliente_edge`` construye el cliente HTTP real de la
      sincronizacion de movimientos si no se pasa una prueba con
      ``httpx.MockTransport``.
    """
    configuracion = settings or cargar_settings_gestante()
    http: httpx.Client | None = None

    if cliente_central is None:
        http = crear_cliente_http(configuracion)
        cliente_central = ClienteCentralHTTP(http)

    contexto = ContextoAdaptador(
        settings=configuracion,
        cliente_central=cliente_central,
        reloj=reloj or ahora_utc,
        constructor_cliente_edge=constructor_cliente_edge,
    )

    @asynccontextmanager
    async def ciclo_de_vida(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            # Solo se cierra el cliente que esta aplicacion creo. Uno inyectado
            # pertenece a quien lo paso.
            if http is not None:
                http.close()

    aplicacion = FastAPI(
        title=TITULO,
        lifespan=ciclo_de_vida,
        # La interfaz no consume un esquema OpenAPI y publicarlo solo anadiria
        # superficie: este adaptador es para un navegador, no para integradores.
        openapi_url=None,
    )
    aplicacion.add_exception_handler(
        RequestValidationError, sanear_errores_de_validacion
    )
    aplicacion.include_router(crear_router(contexto))

    # Accesible para las pruebas, que necesitan mirar el contexto sin
    # reconstruirlo.
    aplicacion.state.contexto = contexto

    return aplicacion
