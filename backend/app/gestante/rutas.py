"""Las rutas del adaptador local de la interfaz de la gestante (SCRUM-72).

Trece rutas: tres sirven los archivos de la interfaz, cinco atienden sesion y
estado local, dos exponen la lectura clinica minima de SCRUM-98 --embarazos
de la cuenta y el monitoreo de un episodio-- traducida desde
``app.gestante.central`` y ``app.gestante.clinico``, y tres registran y
sincronizan sesiones de movimiento simuladas por medio de
``app.gestante.movimientos``, que a su vez delega en ``app.edge``. No hay mas:
ninguna ruta reenvia un cuerpo ni una ruta arbitraria de la API central, y
ninguna calcula un semaforo o un estado clinico por su cuenta.

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

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus

import httpx
from fastapi import APIRouter, Path, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict

from app.edge.captura import PaqueteInvalido
from app.gestante import (
    almacen,
    estado_local,
    movimientos,
    provision,
    sesion as sesion_local,
)
from app.gestante.central import ClienteCentral, EstadoRespuesta, RespuestaClinica
from app.gestante.clinico import (
    SesionConLecturas,
    clasificar_episodios,
    sesion_de_la_lectura,
    ultima_lectura,
)
from app.gestante.config import NOMBRE_DE_COOKIE, GestanteSettings
from app.gestante.simulacion import SimulacionNoAplicable
from app.models.enums import NombreRol, TipoSesion
from app.schemas.autenticacion import CredencialesEntrada

registrador = logging.getLogger(__name__)

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
# El contrato de las lecturas clinicas
# ---------------------------------------------------------------------------
#
# Dos situaciones --y solo dos-- se responden 200 con ``disponible: false``,
# porque en ambas la sesion **local** sigue siendo valida y el portal debe
# permanecer abierto:
#
#   reautenticacion_requerida : no hay token central utilizable. O el proceso se
#                               reinicio y el token se perdio --porque nunca se
#                               persiste, que es lo correcto--, o el servidor
#                               respondio 401.
#   sin_conexion              : no se pudo preguntar. Estado operativo esperado
#                               de un diseno pensado para conectividad
#                               intermitente, no un error.
#
# Todo lo demas conserva su codigo HTTP:
#
#   401 : **solo** cuando la sesion LOCAL no vale. Es lo unico que devuelve al
#         login, y por eso ninguna otra situacion puede emitirlo.
#   403 : el servidor central reconoce la cuenta y su rol no puede leer esto.
#         No se disfraza de reautenticacion: reintentar credenciales que
#         funcionan no arreglaria nada.
#   404 : el episodio no existe o no esta al alcance. SCRUM-98 los hace
#         indistinguibles a proposito y el adaptador no deshace esa propiedad.
#   502 : el servidor contesto algo que no cumple el contrato, o fallo. Un
#         problema del upstream se dice como tal.
MOTIVO_REAUTENTICACION = "reautenticacion_requerida"
MOTIVO_SIN_CONEXION = "sin_conexion"

MENSAJE_NO_DISPONIBLE = "Ese episodio no está disponible."
MENSAJE_ACCESO_DENEGADO = "Tu cuenta no puede consultar esta información."
MENSAJE_UPSTREAM = (
    "El servidor no pudo responder correctamente. Inténtalo de nuevo más tarde."
)

# ---------------------------------------------------------------------------
# Sesiones de movimiento simuladas
# ---------------------------------------------------------------------------

MENSAJE_ERROR_INTERNO = (
    "No se pudo registrar la sesión simulada. Inténtalo de nuevo más tarde."
)


class SesionSimuladaEntrada(BaseModel):
    """Lo único que el navegador elige: qué tipo de sesión simular.

    Ningún valor clínico viaja en este cuerpo. El paquete completo lo arma
    ``app.gestante.simulacion`` con sus propios valores fijos.
    """

    model_config = ConfigDict(extra="forbid")

    tipo_sesion: TipoSesion


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
    ni PostgreSQL. ``constructor_cliente_edge`` es la misma idea aplicada a la
    sincronizacion de movimientos: por omision construye un ``httpx.Client``
    real contra ``settings.api_base_url``, y una prueba puede sustituirlo por
    uno con ``httpx.MockTransport`` sin tocar la red.
    """

    settings: GestanteSettings
    cliente_central: ClienteCentral
    reloj: Callable[[], datetime] = sesion_local.ahora_utc
    tokens: AlmacenDeTokens = field(default_factory=AlmacenDeTokens)
    constructor_cliente_edge: (
        Callable[[GestanteSettings, str], httpx.Client] | None
    ) = None


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

    # -- Lectura clinica (consume SCRUM-98) -------------------------------

    def sesion_vigente(peticion: Request):
        """La sesion local de esta peticion, o ``None``.

        Valida contra SQLite en cada peticion, como el resto del adaptador. No
        renueva nada: leer informacion clinica no alarga la ventana local, y un
        fallo del servidor central tampoco la acorta.
        """
        identificador = identificador_de(peticion)
        ahora = contexto.reloj()
        with abrir_sesiones() as conexion:
            almacen.inicializar(conexion)
            return identificador, sesion_local.validar(
                conexion, identificador, ahora=ahora
            )

    def no_disponible(motivo: str) -> JSONResponse:
        """200 con la razon. Solo para los dos motivos que dejan el portal abierto."""
        return json({"disponible": False, "motivo": motivo})

    def traducir_fallo(resultado: RespuestaClinica) -> JSONResponse:
        """Convierte un desenlace que no es ``OK`` en la respuesta acordada.

        Cada rama conserva la semantica del servidor en lugar de aplanarla:
        solo las dos que dejan la sesion local en pie responden 200.
        """
        if resultado.estado is EstadoRespuesta.RECHAZADO:
            # 401 central. No se puede saber si el token vencio o si la cuenta
            # fue desactivada --SCRUM-70 los hace indistinguibles--, asi que no
            # se cierra la sesion local. Esto NO renueva la ventana, NO afirma
            # que la cuenta siga autorizada y NO da acceso a dato central
            # alguno: solo evita destruir una sesion offline todavia vigente.
            # Debe reevaluarse antes de habilitar escritura offline.
            return no_disponible(MOTIVO_REAUTENTICACION)

        if resultado.estado is EstadoRespuesta.SIN_CONEXION:
            return no_disponible(MOTIVO_SIN_CONEXION)

        if resultado.estado is EstadoRespuesta.PROHIBIDO:
            return error(MENSAJE_ACCESO_DENEGADO, HTTPStatus.FORBIDDEN)

        if resultado.estado is EstadoRespuesta.NO_ENCONTRADO:
            return error(MENSAJE_NO_DISPONIBLE, HTTPStatus.NOT_FOUND)

        return error(MENSAJE_UPSTREAM, HTTPStatus.BAD_GATEWAY)

    def _episodio(embarazo) -> dict:
        """Un episodio tal como lo ve el navegador. Solo campos del contrato."""
        return {
            "id_embarazo": embarazo.id_embarazo,
            "fecha_inicio": embarazo.fecha_inicio.isoformat(),
            "fecha_probable_parto": (
                embarazo.fecha_probable_parto.isoformat()
                if embarazo.fecha_probable_parto is not None
                else None
            ),
            "estado_embarazo": embarazo.estado_embarazo,
            "fecha_cierre": (
                embarazo.fecha_cierre.isoformat()
                if embarazo.fecha_cierre is not None
                else None
            ),
        }

    def _lectura(lectura) -> dict:
        """Una lectura tal como la ve el navegador.

        Los tres valores biometricos viajan como ``None`` cuando no aplican.
        Nunca como cero: la interfaz tiene que poder distinguir «no se midio» de
        «se midio y dio cero», y esa distincion empieza aqui.

        ``codigo_semaforo`` se reenvia sin tocar. El adaptador no clasifica.
        """
        return {
            "id_lectura": lectura.id_lectura,
            "fecha_hora_captura": lectura.fecha_hora_captura.isoformat(),
            "codigo_semaforo": lectura.codigo_semaforo,
            "semana_gestacion": lectura.semana_gestacion,
            "hr_valor": None if lectura.hr_valor is None else str(lectura.hr_valor),
            "spo2_valor": None if lectura.spo2_valor is None else str(lectura.spo2_valor),
            "mov_valor": lectura.mov_valor,
        }

    def _sesion(sesion) -> dict:
        return {
            "id_sesion": sesion.id_sesion,
            "tipo_sesion": sesion.tipo_sesion,
            "estado_sesion": sesion.estado_sesion,
            "fecha_inicio": sesion.fecha_inicio.isoformat(),
            "fecha_fin": (
                sesion.fecha_fin.isoformat() if sesion.fecha_fin is not None else None
            ),
        }

    @router.get("/adaptador/embarazos")
    def listar_embarazos(peticion: Request) -> JSONResponse:
        """Los episodios de la paciente, ya clasificados.

        El servidor central decide **cuales** son --aqui no se envia ningun
        filtro, porque un filtro del cliente no prueba autorizacion--; este
        adaptador solo decide cual llamar «actual», con la regla de
        ``app.gestante.clinico``.
        """
        identificador, sesion = sesion_vigente(peticion)
        if sesion is None:
            return error(MENSAJE_SIN_SESION, HTTPStatus.UNAUTHORIZED)

        token = contexto.tokens.obtener(identificador)
        if token is None:
            # El proceso se reinicio y el token se perdio, que es lo que tiene
            # que pasar: no se persiste. La sesion local sigue viva.
            return no_disponible(MOTIVO_REAUTENTICACION)

        resultado = contexto.cliente_central.embarazos(token)
        if not resultado.disponible:
            if resultado.estado is EstadoRespuesta.RECHAZADO:
                contexto.tokens.olvidar(identificador)
            return traducir_fallo(resultado)

        episodios = clasificar_episodios(resultado.datos)

        return json(
            {
                "disponible": True,
                "datos": {
                    "actual": None if episodios.actual is None else _episodio(episodios.actual),
                    "anteriores": [_episodio(e) for e in episodios.anteriores],
                    "todos": [_episodio(e) for e in episodios.todos],
                    # Cuando es ``True``, la interfaz no debe llamar «actual» a
                    # ningun episodio, aunque permita consultarlos todos.
                    "ambiguo": episodios.ambiguo,
                },
            }
        )

    @router.get("/adaptador/embarazos/{id_embarazo}/monitoreo")
    def monitoreo_del_embarazo(
        peticion: Request, id_embarazo: int = Path(ge=1)
    ) -> JSONResponse:
        """Las sesiones de un episodio, sus lecturas, y cual es la ultima.

        Una sola ruta para el panel y para el historial, y no por comodidad: la
        ultima lectura se calcula sobre **todas** las lecturas del episodio, asi
        que hay que recorrerlas de todos modos. Partirlo en dos rutas duplicaria
        exactamente el mismo trabajo.

        El identificador que llega aqui procede siempre de
        ``/adaptador/embarazos``. Si aun asi nombrara un episodio ajeno, el
        servidor central responde 404 por sus politicas y esta ruta lo reenvia
        como 404: la autoridad sigue siendo el servidor, no esta comprobacion.
        """
        identificador, sesion = sesion_vigente(peticion)
        if sesion is None:
            return error(MENSAJE_SIN_SESION, HTTPStatus.UNAUTHORIZED)

        token = contexto.tokens.obtener(identificador)
        if token is None:
            return no_disponible(MOTIVO_REAUTENTICACION)

        respuesta_sesiones = contexto.cliente_central.sesiones(token, id_embarazo)
        if not respuesta_sesiones.disponible:
            if respuesta_sesiones.estado is EstadoRespuesta.RECHAZADO:
                contexto.tokens.olvidar(identificador)
            return traducir_fallo(respuesta_sesiones)

        con_lecturas: list[SesionConLecturas] = []
        for sesion_remota in respuesta_sesiones.datos:
            respuesta_lecturas = contexto.cliente_central.lecturas(
                token, sesion_remota.id_sesion
            )
            if not respuesta_lecturas.disponible:
                # Un fallo a mitad no se completa con lo que ya se tenia: un
                # historial parcial que no se anuncia como parcial es peor que
                # decir que no se pudo leer.
                if respuesta_lecturas.estado is EstadoRespuesta.RECHAZADO:
                    contexto.tokens.olvidar(identificador)
                return traducir_fallo(respuesta_lecturas)
            con_lecturas.append(
                SesionConLecturas(
                    sesion=sesion_remota, lecturas=respuesta_lecturas.datos
                )
            )

        ultima = ultima_lectura(con_lecturas)
        sesion_de_la_ultima = (
            None if ultima is None else sesion_de_la_lectura(con_lecturas, ultima)
        )

        return json(
            {
                "disponible": True,
                "datos": {
                    "id_embarazo": id_embarazo,
                    "sesiones": [
                        {
                            **_sesion(entrada.sesion),
                            "lecturas": [_lectura(l) for l in entrada.lecturas],
                        }
                        for entrada in con_lecturas
                    ],
                    # Una sola lectura coherente: sus metricas, su instante y su
                    # semaforo son los de la misma captura. Nunca se compone a
                    # partir de lecturas distintas.
                    "ultima_lectura": None if ultima is None else _lectura(ultima),
                    "id_sesion_de_la_ultima": (
                        None if sesion_de_la_ultima is None else sesion_de_la_ultima.id_sesion
                    ),
                },
            }
        )

    # -- Sesiones de movimiento simuladas (consume app.edge) --------------

    @router.post("/adaptador/embarazos/{id_embarazo}/sesiones-simuladas")
    def registrar_sesion_simulada(
        peticion: Request,
        cuerpo: SesionSimuladaEntrada,
        id_embarazo: int = Path(ge=1),
    ) -> JSONResponse:
        """Captura localmente una sesión simulada. **Funciona sin conexión.**

        Que funcione sin conexión es el requisito, no una concesión: un
        dispositivo que necesitara preguntarle algo al servidor para capturar
        no serviría justo cuando hace falta. Por eso la autorización de este
        paso se apoya en el aprovisionamiento del dispositivo -- a qué cuenta y
        a qué embarazo sirve -- que se escribió con la credencial de
        mantenimiento y que el navegador no puede alterar.

        Tres comprobaciones, y ninguna la decide el navegador:

        1. la sesión local vale (401 si no);
        2. este dispositivo está aprovisionado para **esta cuenta y este
           embarazo** (404 si el identificador de la ruta nombra otro);
        3. cuando además hay token en memoria, se contrasta con la lista que el
           servidor central entrega para la cuenta. Si el servidor dice que ese
           embarazo no es suyo, se rechaza; si no se puede preguntar, se sigue
           con el aprovisionamiento, que es el caso sin conexión.

        La autoridad final sigue siendo el servidor: ``POST
        /api/v1/sesiones-monitoreo`` vuelve a comprobar la propiedad del
        embarazo y la asignación del dispositivo cuando el paquete se
        sincroniza.
        """
        identificador, sesion = sesion_vigente(peticion)
        if sesion is None:
            return error(MENSAJE_SIN_SESION, HTTPStatus.UNAUTHORIZED)

        try:
            aprovisionamiento = provision.cargar(settings.provision_path)
        except provision.ProvisionInvalida as fallo:
            # 503: el dispositivo no está listo todavía. No es culpa de la
            # cuenta ni de la red, y decirlo con precisión evita que parezca
            # un fallo del servidor central.
            return error(fallo.detalle, HTTPStatus.SERVICE_UNAVAILABLE)

        if not aprovisionamiento.sirve_a(sesion.id_usuario, id_embarazo):
            return error(MENSAJE_NO_DISPONIBLE, HTTPStatus.NOT_FOUND)

        token = contexto.tokens.obtener(identificador)
        if token is not None:
            # Comprobación adicional cuando sí hay con qué preguntar. No
            # sustituye a la del aprovisionamiento: la refuerza.
            resultado = contexto.cliente_central.embarazos(token)
            if resultado.disponible:
                if not clasificar_episodios(resultado.datos).contiene(id_embarazo):
                    return error(MENSAJE_NO_DISPONIBLE, HTTPStatus.NOT_FOUND)
            elif resultado.estado is EstadoRespuesta.RECHAZADO:
                contexto.tokens.olvidar(identificador)

        ahora = contexto.reloj()
        try:
            registro = movimientos.registrar_sesion_simulada(
                settings,
                id_usuario=sesion.id_usuario,
                provision=aprovisionamiento,
                tipo_sesion=cuerpo.tipo_sesion,
                ahora=ahora,
            )
        except SimulacionNoAplicable as fallo:
            # El dominio dice que esta sesión no existe para este embarazo hoy
            # --semana fuera del catálogo, movimiento antes de la semana 20--.
            # 422, y con la frase exacta: no se guarda algo que el servidor
            # rechazaría después.
            return error(fallo.detalle, HTTPStatus.UNPROCESSABLE_ENTITY)
        except PaqueteInvalido as fallo:
            # No deberia pasar: el paquete lo arma este adaptador con valores
            # fijos. Si ocurre, el contrato cambio y esto es un fallo interno,
            # no algo que la paciente hizo mal.
            registrador.error(
                "Paquete de sesion simulada rechazado por su propio contrato (%s).",
                fallo.detalle,
            )
            return error(MENSAJE_ERROR_INTERNO, HTTPStatus.INTERNAL_SERVER_ERROR)

        return json(
            {
                "registrado": True,
                "estado": "local",
                "tipo_sesion": cuerpo.tipo_sesion.value,
                "clave": registro.clave,
                "capturado_en": ahora.isoformat(),
            },
            HTTPStatus.CREATED,
        )

    @router.post("/adaptador/movimientos/sincronizar")
    def sincronizar_movimientos(peticion: Request) -> JSONResponse:
        """Una sola ronda de envío de la cola de esta cuenta, ahora mismo.

        Reutiliza ``app.edge.ejecutar_pasada`` con el token que ya está en
        memoria de esta sesión. Nunca inventa un resultado: lo que responde es
        exactamente lo que la API central contestó en esta ronda.
        """
        identificador, sesion = sesion_vigente(peticion)
        if sesion is None:
            return error(MENSAJE_SIN_SESION, HTTPStatus.UNAUTHORIZED)

        token = contexto.tokens.obtener(identificador)
        if token is None:
            return no_disponible(MOTIVO_REAUTENTICACION)

        pasada = movimientos.sincronizar_cuenta(
            settings,
            id_usuario=sesion.id_usuario,
            token=token,
            constructor_cliente_http=contexto.constructor_cliente_edge,
        )

        if pasada.detenida_por_credencial:
            contexto.tokens.olvidar(identificador)

        return json(
            {
                "seleccionados": pasada.seleccionados,
                "entregados": pasada.entregados,
                "reintentables": pasada.reintentables,
                "rechazados": pasada.rechazados,
                "agotados": pasada.agotados,
                "ya_entregados": pasada.ya_entregados,
                "detenida_por_transporte": pasada.detenida_por_transporte,
                "detenida_por_credencial": pasada.detenida_por_credencial,
            }
        )

    @router.get("/adaptador/movimientos/estado")
    def estado_de_movimientos(peticion: Request) -> JSONResponse:
        """Conteos de la cola de **esta** cuenta. Nunca un paquete ni una clave.

        El mismo contrato que ``/adaptador/estado-local``, pero sobre el
        archivo propio de la cuenta que hizo la petición en lugar del nodo edge
        compartido de todo el dispositivo.
        """
        identificador, sesion = sesion_vigente(peticion)
        if sesion is None:
            return error(MENSAJE_SIN_SESION, HTTPStatus.UNAUTHORIZED)

        estado = movimientos.leer_estado_de_la_cuenta(settings, sesion.id_usuario)

        return json(
            {
                "inicializado": estado.inicializado,
                "pendientes": estado.pendientes,
                "enviados": estado.enviados,
                "fallidos_reintentables": estado.fallidos_reintentables,
                "fallidos_en_revision": estado.fallidos_en_revision,
                "total": estado.total,
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
