"""Adaptador local de la interfaz de la gestante (SCRUM-72).

El componente que esta pieza simula es el que un despliegue rural necesitaria y
esta tesis no construye en hardware: algo que corre **en el dispositivo de la
paciente**, le sirve la interfaz, la autentica contra el servidor central cuando
hay conexion, y sigue en pie cuando no la hay.

Ocho modulos, cada uno con un trabajo:

:mod:`app.gestante.config`
    Donde esta el archivo, a que API se llama, cuanto dura la sesion local.
:mod:`app.gestante.almacen`
    El SQLite propio de las sesiones: esquema, conexiones y transacciones.
:mod:`app.gestante.sesion`
    Abrir, validar, renovar y cerrar una sesion local.
:mod:`app.gestante.central`
    Las conversaciones con la API central, por HTTP: autenticacion, identidad,
    salud, y la lectura clinica de SCRUM-98 (embarazos, sesiones, lecturas).
:mod:`app.gestante.clinico`
    Reglas de presentacion sobre esa lectura clinica: cual episodio esta en
    curso y cual es la ultima lectura de una serie de sesiones.
:mod:`app.gestante.estado_local`
    Ventana de solo lectura sobre la outbox del nodo edge.
:mod:`app.gestante.rutas`
    Las diez rutas que ve el navegador.
:mod:`app.gestante.aplicacion`
    El ensamblado FastAPI.

El punto de entrada es ``scripts/gestante_web.py``.

**Lo que este paquete no hace, dicho con precision.** No clasifica lecturas --el
semaforo lo decide la fuente autorizada--, no escribe en PostgreSQL, no escribe
en el almacenamiento del nodo edge, no reimplementa la idempotencia ni los
reintentos, y no guarda credenciales: la contrasena no se persiste en ninguna
forma, y el token del servidor central vive unicamente en memoria del proceso.

**Lo que todavia no hace, y por que.** El registro de sesiones de movimientos
esta preparado en la interfaz pero deshabilitado: requiere el flujo de captura
simulada de ``app.edge`` y una decision explicita sobre como representarlo
desde este portal, que este ticket no da por sentada.

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
    RespuestaClinica,
    RespuestaIdentidad,
    RespuestaToken,
)
from app.gestante.clinico import (
    Episodios,
    SesionConLecturas,
    clasificar_episodios,
    en_curso,
    sesion_de_la_lectura,
    ultima_lectura,
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
    "Episodios",
    "ErrorDeAlmacenamientoLocal",
    "EstadoLocal",
    "EstadoRespuesta",
    "GestanteSettings",
    "NOMBRE_DE_COOKIE",
    "ROL_PERMITIDO",
    "RespuestaClinica",
    "RespuestaIdentidad",
    "RespuestaToken",
    "RolNoAutorizado",
    "SesionConLecturas",
    "SesionLocal",
    "TABLA_SESION",
    "VENTANA_POR_OMISION_HORAS",
    "VERSION_DE_ESQUEMA",
    "abrir",
    "abrir_almacen",
    "ahora_utc",
    "cargar_settings_gestante",
    "cerrar",
    "clasificar_episodios",
    "conectar",
    "crear_aplicacion",
    "crear_cliente_http",
    "crear_router",
    "digest",
    "en_curso",
    "generar_identificador",
    "inicializar",
    "leer_estado_local",
    "leer_version",
    "preparar_directorio",
    "renovar",
    "sesion_de_la_lectura",
    "tablas_presentes",
    "transaccion",
    "ultima_lectura",
    "validar",
]
