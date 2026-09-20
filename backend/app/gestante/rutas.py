"""Las rutas del adaptador local de la interfaz de la gestante (SCRUM-72).

Ocho rutas: tres sirven los archivos de la interfaz y cinco la atienden. No hay
mas, y lo que **no** hay es tan parte del diseno como lo que hay: ninguna ruta
devuelve un embarazo, una lectura, un semaforo ni un historial, porque esos
contratos pertenecen a SCRUM-98 y todavia no existen. Una ruta que los
imitara hoy seria una fuente de datos inventada.

**El adaptador no es una segunda capa de negocio.** No valida de nuevo lo que ya
valida un contrato, no clasifica nada, no reimplementa la idempotencia ni los
reintentos, y no toca PostgreSQL. Traduce entre el navegador y dos cosas que ya
existen: la API central por HTTP y ``app.edge`` por llamada de funcion.

**Por que el navegador no habla directamente con la API central.** Tres razones
concretas, y ninguna es preferencia: asi el token nunca entra en JavaScript;
asi la interfaz sigue en pie cuando el servidor central no responde; y asi
``app.main`` no necesita CORS --la pagina y estas rutas son el mismo origen--,
de modo que este ticket no toca la aplicacion central.

**La sesion humana y la credencial del nodo no se mezclan.** El token que se
obtiene al iniciar sesion es de la persona, vive en memoria de este proceso y no
se usa para sincronizar. ``EDGE_API_TOKEN`` es del nodo edge, lo lee
``scripts/edge_node.py`` y este modulo no lo lee, no lo reenvia y no lo conoce.

Todas las cuentas y los datos son ficticios y simulados.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, JSONResponse

from app.gestante import almacen, estado_local, sesion as sesion_local
from app.gestante.central import ClienteCentral, EstadoRespuesta
from app.gestante.config import NOMBRE_DE_COOKIE, GestanteSettings
from app.models.enums import NombreRol
from app.schemas.autenticacion import CredencialesEntrada

# ---------------------------------------------------------------------------
# Textos de las respuestas
# ---------------------------------------------------------------------------

# Una sola frase para cualquier credencial que no sirve. SCRUM-70 responde
# igual ante una cuenta inexistente, una contrasena incorrecta y una cuenta
# desactivada, a proposito; el adaptador conserva esa indistinguibilidad en
# lugar de deshacerla con un mensaje mas util.
MENSAJE_CREDENCIALES = "Correo o contraseña incorrectos."

MENSAJE_SIN_CONEXION = (
    "No hay conexión con el servidor. El primer inicio de sesión en este "
    "dispositivo necesita conexión."
)
MENSAJE_ERROR_REMOTO = (
    "El servidor respondió de forma inesperada. Inténtalo de nuevo más tarde."
)
MENSAJE_SOLO_PACIENTES = (
    "Esta interfaz es exclusiva de las cuentas de pacientes."
)
MENSAJE_SIN_SESION = "Tu sesión no está activa. Inicia sesión de nuevo."

ESTADO_DISPONIBLE = "disponible"
ESTADO_NO_DISPONIBLE = "no_disponible"


# ---------------------------------------------------------------------------
# Estado en memoria
# ---------------------------------------------------------------------------


class AlmacenDeTokens:
    """Los tokens centrales de las sesiones abiertas. **Solo en memoria.**

    Un diccionario y tres operaciones, y el hecho de que sea un diccionario es
    la propiedad importante: no hay ruta hacia el disco. El token no se escribe
    en SQLite, no se envia al navegador, no aparece en la cookie y no entra en
    ningun registro. Si el proceso se reinicia, los tokens desaparecen --y eso
    es correcto--: la ventana local sigue autorizando lo que vive en el
    dispositivo, y lo que necesite al servidor central volvera a pedir
    credenciales.

    Se indexa por el digest del identificador de sesion, no por el
    identificador: ni siquiera esta estructura conserva el valor que viaja en la
    cookie.
    """

    def __init__(self) -> None:
        self._por_sesion: dict[str, str] = {}

    def guardar(self, identificador: str, token: str) -> None:
        self._por_sesion[sesion_local.digest(identificador)] = token

    def obtener(self, identificador: str) -> str | None:
        return self._por_sesion.get(sesion_local.digest(identificador))

    def olvidar(self, identificador: str) -> None:
        self._por_sesion.pop(sesion_local.digest(identificador), None)


@dataclass
class ContextoAdaptador:
    """Todo lo que las rutas necesitan, reunido y sustituible en una prueba.

    ``reloj`` se inyecta para que una prueba pueda recorrer la ventana de
    sesion sin esperarla, igual que ``app.edge.sincronizacion`` inyecta el suyo.
    ``cliente_central`` se inyecta para que las pruebas no necesiten ni la API
    ni PostgreSQL.
    """

    settings: GestanteSettings
    cliente_central: ClienteCentral
    reloj: Callable[[], datetime] = sesion_local.ahora_utc
    tokens: AlmacenDeTokens = field(default_factory=AlmacenDeTokens)


# ---------------------------------------------------------------------------
# Construccion del router
# ---------------------------------------------------------------------------


def crear_router(contexto: ContextoAdaptador) -> APIRouter:
    """El router del adaptador, atado a un contexto concreto."""

    router = APIRouter()
    settings = contexto.settings

    # -- Utilidades internas --------------------------------------------

    def abrir_sesiones():
        """Conexion al almacen de sesiones, con su esquema listo."""
        return almacen.abrir_almacen(
            settings.sqlite_path, espera_de_bloqueo_ms=settings.busy_timeout_ms
        )

    def fijar_cookie(respuesta: Response, identificador: str, segundos: int) -> None:
        """Entrega el identificador de sesion al navegador.

        ``max_age`` sale de los segundos que le quedan a la sesion en SQLite, no
        de una constante aparte: la cookie y la fila caducan por el mismo
        calculo y no pueden discrepar.

        ``httponly`` es lo que mantiene el identificador fuera del alcance de
        JavaScript. ``samesite='strict'`` impide que otro sitio provoque una
        peticion autenticada a este adaptador. ``secure`` es configurable
        porque el MVP se sirve por HTTP local y el navegador descartaria una
        cookie ``Secure``; no es una decision de despliegue cableada aqui.
        """
        respuesta.set_cookie(
            key=NOMBRE_DE_COOKIE,
            value=identificador,
            max_age=segundos,
            path="/",
            httponly=True,
            samesite="strict",
            secure=settings.cookie_secure,
        )

    def borrar_cookie(respuesta: Response) -> None:
        """Caduca la cookie. Es lo cosmetico del cierre de sesion.

        Lo que invalida de verdad es ``cerrada_en`` en SQLite: una copia de la
        cookie tomada antes seguiria existiendo, y deja de servir por aquello,
        no por esto.
        """
        respuesta.set_cookie(
            key=NOMBRE_DE_COOKIE,
            value="",
            max_age=0,
            path="/",
            httponly=True,
            samesite="strict",
            secure=settings.cookie_secure,
        )

    def identificador_de(peticion: Request) -> str:
        return peticion.cookies.get(NOMBRE_DE_COOKIE, "")

    def json(contenido: dict, estado: int = HTTPStatus.OK) -> JSONResponse:
        return JSONResponse(status_code=estado, content=contenido)

    def error(detalle: str, estado: int) -> JSONResponse:
        """Una negativa con un texto que este proyecto escribio.

        Nunca se interpola el correo intentado, ni la respuesta del servidor
        central, ni el nombre de un archivo.
        """
        return json({"detail": detalle}, estado)

    def revalidar_si_procede(conexion, identificador: str, sesion, ahora: datetime):
        """Renueva la ventana local si el servidor confirma la cuenta.

        Solo se intenta cuando **este proceso** conserva el token en memoria.
        Sin token no se toca la red: la sesion local sigue valiendo hasta que su
        ventana termine, que es justo lo que hace util el modo sin conexion.

        **Un rechazo no cierra la sesion local, y hay una razon concreta.**
        ``GET /api/v1/autenticacion/yo`` responde 401 tanto si el token expiro
        --lo normal, porque dura treinta minutos por omision-- como si la cuenta
        fue desactivada, y SCRUM-70 los hace deliberadamente indistinguibles.
        Cerrar la sesion ante un 401 expulsaria a toda paciente media hora
        despues de entrar, que es exactamente lo que este diseno existe para
        evitar. Asi que el token se olvida y la ventana local se respeta; las
        operaciones que necesiten al servidor volveran a pedir credenciales, y
        una cuenta desactivada seguira sin poder hacer nada contra el servidor,
        que es donde se aplica esa decision.
        """
        token = contexto.tokens.obtener(identificador)
        if token is None:
            return sesion

        respuesta = contexto.cliente_central.identidad(token)

        if respuesta.estado is EstadoRespuesta.RECHAZADO:
            contexto.tokens.olvidar(identificador)
            return sesion

        if respuesta.estado is not EstadoRespuesta.OK:
            # Sin conexion o error remoto: no se sabe nada nuevo, no se cambia
            # nada.
            return sesion

        if respuesta.id_usuario != sesion.id_usuario or respuesta.rol is not NombreRol.PACIENTE:
            # El token dejo de hablar por esta sesion. No se renueva y se
            # cierra: es la unica situacion en que el servidor dice algo
            # incompatible con lo que esta sesion afirma.
            contexto.tokens.olvidar(identificador)
            sesion_local.cerrar(conexion, identificador, ahora=ahora)
            return None

        renovada = sesion_local.renovar(
            conexion,
            identificador,
            ventana_segundos=settings.ventana_en_segundos,
            ahora=ahora,
        )
        return renovada or sesion

    # -- Archivos de la interfaz ----------------------------------------
    #
    # Se sirven como tres rutas explicitas y no como un directorio montado. Son
    # tres archivos conocidos, asi que no existe un nombre que el cliente pueda
    # elegir y, con ello, no existe recorrido de rutas que impedir.

    def archivo(nombre: str, tipo: str) -> FileResponse:
        return FileResponse(settings.frontend_dir / nombre, media_type=tipo)

    @router.get("/", include_in_schema=False)
    def pagina() -> FileResponse:
        return archivo("index.html", "text/html; charset=utf-8")

    @router.get("/styles.css", include_in_schema=False)
    def hoja_de_estilos() -> FileResponse:
        return archivo("styles.css", "text/css; charset=utf-8")

    @router.get("/app.js", include_in_schema=False)
    def guion() -> FileResponse:
        return archivo("app.js", "text/javascript; charset=utf-8")

    # -- Inicio de sesion ------------------------------------------------

    @router.post("/adaptador/iniciar-sesion")
    def iniciar_sesion(credenciales: CredencialesEntrada) -> JSONResponse:
        """Autentica contra el servidor central y abre una sesion local.

        El contrato del cuerpo es ``CredencialesEntrada``, el mismo que valida
        ``POST /api/v1/autenticacion/token``. Reutilizarlo --en vez de escribir
        otro aqui-- hace que el correo se normalice con la misma regla con la
        que esta guardado, y que no puedan separarse dos definiciones de una
        sola cosa.

        El orden importa: primero el servidor dice si las credenciales sirven,
        despues dice quien es y con que rol, y solo entonces se abre algo local.
        Un rol que no sea PACIENTE no deja rastro: no se crea sesion, no se
        guarda token y no se entrega cookie.
        """
        autenticacion = contexto.cliente_central.autenticar(
            email=credenciales.email,
            password=credenciales.password.get_secret_value(),
        )

        if autenticacion.estado is EstadoRespuesta.SIN_CONEXION:
            return error(MENSAJE_SIN_CONEXION, HTTPStatus.SERVICE_UNAVAILABLE)
        if autenticacion.estado is EstadoRespuesta.RECHAZADO:
            return error(MENSAJE_CREDENCIALES, HTTPStatus.UNAUTHORIZED)
        if autenticacion.estado is not EstadoRespuesta.OK or autenticacion.token is None:
            return error(MENSAJE_ERROR_REMOTO, HTTPStatus.BAD_GATEWAY)

        token = autenticacion.token
        identidad = contexto.cliente_central.identidad(token)

        if identidad.estado is EstadoRespuesta.SIN_CONEXION:
            return error(MENSAJE_SIN_CONEXION, HTTPStatus.SERVICE_UNAVAILABLE)
        if identidad.estado is not EstadoRespuesta.OK or identidad.id_usuario is None:
            return error(MENSAJE_ERROR_REMOTO, HTTPStatus.BAD_GATEWAY)

        if identidad.rol is not NombreRol.PACIENTE:
            # 403 y no 401: sabemos exactamente quien es, y su rol no entra
            # aqui. Nada local se crea.
            return error(MENSAJE_SOLO_PACIENTES, HTTPStatus.FORBIDDEN)

        ahora = contexto.reloj()
        with abrir_sesiones() as conexion:
            almacen.inicializar(conexion)
            identificador, sesion = sesion_local.abrir(
                conexion,
                id_usuario=identidad.id_usuario,
                rol=identidad.rol,
                ventana_segundos=settings.ventana_en_segundos,
                ahora=ahora,
            )

        contexto.tokens.guardar(identificador, token)

        respuesta = json(
            {
                "autenticada": True,
                "rol": sesion.rol.value,
                "expira_en": sesion.expira_en.isoformat(),
                "segundos_restantes": sesion.segundos_restantes(ahora),
            }
        )
        fijar_cookie(respuesta, identificador, sesion.segundos_restantes(ahora))
        return respuesta

    # -- Estado de la sesion ---------------------------------------------

    @router.get("/adaptador/sesion")
    def consultar_sesion(peticion: Request) -> JSONResponse:
        """Si esta sesion sigue valiendo, y hasta cuando.

        Responde 200 en los dos casos: es una consulta de estado, no un recurso
        protegido, y la pantalla de inicio de sesion la usa precisamente cuando
        todavia no hay sesion.

        Validar **no** alarga la ventana. Solo una revalidacion en linea
        correcta la mueve, y por eso abrir la interfaz sin conexion tantas veces
        como haga falta no anade ni un segundo.
        """
        identificador = identificador_de(peticion)
        ahora = contexto.reloj()

        with abrir_sesiones() as conexion:
            almacen.inicializar(conexion)
            sesion = sesion_local.validar(conexion, identificador, ahora=ahora)
            if sesion is None:
                return json({"autenticada": False})
            sesion = revalidar_si_procede(conexion, identificador, sesion, ahora)

        if sesion is None:
            respuesta = json({"autenticada": False})
            borrar_cookie(respuesta)
            return respuesta

        respuesta = json(
            {
                "autenticada": True,
                "rol": sesion.rol.value,
                "expira_en": sesion.expira_en.isoformat(),
                "segundos_restantes": sesion.segundos_restantes(ahora),
            }
        )
        # Se reemite para que el navegador conserve la cookie el tiempo que le
        # queda a la sesion, que es mas si acaba de revalidarse en linea.
        fijar_cookie(respuesta, identificador, sesion.segundos_restantes(ahora))
        return respuesta

    # -- Cierre de sesion -------------------------------------------------

    @router.post("/adaptador/cerrar-sesion")
    def cerrar_sesion(peticion: Request) -> JSONResponse:
        """Invalida la sesion en el servidor, olvida el token y caduca la cookie.

        En ese orden, y el primero es el que cuenta. Responde 200 aunque no
        hubiera sesion que cerrar: el resultado que le importa a quien llama --ya
        no hay sesion-- es el mismo, y distinguirlo solo serviria para averiguar
        si un identificador existia.
        """
        identificador = identificador_de(peticion)
        ahora = contexto.reloj()

        with abrir_sesiones() as conexion:
            almacen.inicializar(conexion)
            sesion_local.cerrar(conexion, identificador, ahora=ahora)

        contexto.tokens.olvidar(identificador)

        respuesta = json({"autenticada": False})
        borrar_cookie(respuesta)
        return respuesta

    # -- Conectividad -----------------------------------------------------

    @router.get("/adaptador/conectividad")
    def conectividad() -> JSONResponse:
        """Si el servidor central responde ahora mismo.

        Publica a proposito: la pantalla de inicio de sesion tiene que poder
        decir que el servidor no responde, y para eso todavia no hay sesion. No
        revela nada: ``/health`` ya es publico en la API central.
        """
        disponible = contexto.cliente_central.disponible()
        return json(
            {"api_central": ESTADO_DISPONIBLE if disponible else ESTADO_NO_DISPONIBLE}
        )

    # -- Estado local del nodo --------------------------------------------

    @router.get("/adaptador/estado-local")
    def estado_del_nodo(peticion: Request) -> JSONResponse:
        """Conteos de la outbox de este dispositivo. Exige sesion.

        Devuelve numeros y una frase, nunca un paquete, una clave de
        idempotencia ni un identificador remoto: lo que se lee viene de
        ``app.edge.outbox.resumen``, que solo cuenta.
        """
        identificador = identificador_de(peticion)
        ahora = contexto.reloj()

        with abrir_sesiones() as conexion:
            almacen.inicializar(conexion)
            sesion = sesion_local.validar(conexion, identificador, ahora=ahora)

        if sesion is None:
            return error(MENSAJE_SIN_SESION, HTTPStatus.UNAUTHORIZED)

        estado = estado_local.leer(
            _ruta_del_nodo(settings), espera_de_bloqueo_ms=settings.busy_timeout_ms
        )

        return json(
            {
                "inicializado": estado.inicializado,
                "pendientes": estado.pendientes,
                "enviados": estado.enviados,
                "fallidos_reintentables": estado.fallidos_reintentables,
                "fallidos_en_revision": estado.fallidos_en_revision,
                "total": estado.total,
                "ultima_sincronizacion": estado.ultima_sincronizacion,
                "detalle": estado.detalle,
            }
        )

    return router


def _ruta_del_nodo(settings: GestanteSettings):
    """Donde vive el almacenamiento del nodo edge, segun la propia configuracion del nodo.

    Se lee de ``EdgeSettings`` y no de una variable propia, para que el portal y
    el nodo no puedan apuntar a archivos distintos. Es una lectura de
    configuracion: no abre el archivo ni lo crea.

    La importacion es local a la funcion a proposito. ``app.edge.config`` no
    construye nada al importarse, pero mantener la dependencia dentro de la
    llamada deja claro que el adaptador consulta al nodo, y no al reves.
    """
    from app.edge.config import cargar_settings_edge

    return cargar_settings_edge().sqlite_path
