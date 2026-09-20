"""Adaptador local de la interfaz de la gestante (SCRUM-72).

El componente que esta pieza simula es el que un despliegue rural necesitaria y
esta tesis no construye en hardware: algo que corre **en el dispositivo de la
paciente**, le sirve la interfaz, la autentica contra el servidor central cuando
hay conexion, y sigue en pie cuando no la hay.

Siete modulos, cada uno con un trabajo:

:mod:`app.gestante.config`
    Donde esta el archivo, a que API se llama, cuanto dura la sesion local.
:mod:`app.gestante.almacen`
    El SQLite propio de las sesiones: esquema, conexiones y transacciones.
:mod:`app.gestante.sesion`
    Abrir, validar, renovar y cerrar una sesion local.
:mod:`app.gestante.central`
    Las tres unicas conversaciones con la API central, por HTTP.
:mod:`app.gestante.estado_local`
    Ventana de solo lectura sobre la outbox del nodo edge.
:mod:`app.gestante.rutas`
    Las ocho rutas que ve el navegador.
:mod:`app.gestante.aplicacion`
    El ensamblado FastAPI.

El punto de entrada es ``scripts/gestante_web.py``.

**Lo que este paquete no hace, dicho con precision.** No clasifica lecturas --el
semaforo lo decide la fuente autorizada--, no escribe en PostgreSQL, no escribe
en el almacenamiento del nodo edge, no reimplementa la idempotencia ni los
reintentos, y no guarda credenciales: la contrasena no se persiste en ninguna
forma, y el token del servidor central vive unicamente en memoria del proceso.

**Lo que todavia no hace, y por que.** No muestra el embarazo, las lecturas, el
semaforo ni el historial, y el registro de sesiones de movimientos esta
preparado pero deshabilitado. Todo eso necesita el contexto clinico autorizado
--la correlacion ``usuario -> paciente -> embarazo`` y las referencias que un
paquete de monitoreo exige-- que corresponde a SCRUM-98/SCRUM-71 y todavia no
existe en el repositorio. La interfaz muestra «No disponible» en vez de un
valor inventado.

Todos los datos que maneja esta interfaz son ficticios y simulados.
"""

from app.gestante.almacen import (
    ROL_PERMITIDO,
    TABLA_SESION,
    VERSION_DE_ESQUEMA,
    ErrorDeAlmacenamientoLocal,
    abrir_almacen,
    conectar,
    inicializar,
    leer_version,
    preparar_directorio,
    tablas_presentes,
    transaccion,
)
from app.gestante.aplicacion import crear_aplicacion, crear_cliente_http
from app.gestante.central import (
    ClienteCentral,
    ClienteCentralHTTP,
    EstadoRespuesta,
    RespuestaIdentidad,
    RespuestaToken,
)
from app.gestante.config import (
    NOMBRE_DE_COOKIE,
    VENTANA_POR_OMISION_HORAS,
    ConfiguracionGestanteInvalida,
    GestanteSettings,
    cargar_settings_gestante,
)
from app.gestante.estado_local import EstadoLocal
from app.gestante.estado_local import leer as leer_estado_local
from app.gestante.rutas import AlmacenDeTokens, ContextoAdaptador, crear_router
from app.gestante.sesion import (
    RolNoAutorizado,
    SesionLocal,
    abrir,
    ahora_utc,
    cerrar,
    digest,
    generar_identificador,
    renovar,
    validar,
)

__all__ = [
    "AlmacenDeTokens",
    "ClienteCentral",
    "ClienteCentralHTTP",
    "ConfiguracionGestanteInvalida",
    "ContextoAdaptador",
    "ErrorDeAlmacenamientoLocal",
    "EstadoLocal",
    "EstadoRespuesta",
    "GestanteSettings",
    "NOMBRE_DE_COOKIE",
    "ROL_PERMITIDO",
    "RespuestaIdentidad",
    "RespuestaToken",
    "RolNoAutorizado",
    "SesionLocal",
    "TABLA_SESION",
    "VENTANA_POR_OMISION_HORAS",
    "VERSION_DE_ESQUEMA",
    "abrir",
    "abrir_almacen",
    "ahora_utc",
    "cargar_settings_gestante",
    "cerrar",
    "conectar",
    "crear_aplicacion",
    "crear_cliente_http",
    "crear_router",
    "digest",
    "generar_identificador",
    "inicializar",
    "leer_estado_local",
    "leer_version",
    "preparar_directorio",
    "renovar",
    "tablas_presentes",
    "transaccion",
    "validar",
]
