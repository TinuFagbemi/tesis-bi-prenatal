"""Adaptador local de la interfaz de la gestante (SCRUM-72).

El componente que esta pieza simula es el que un despliegue rural necesitaria y
esta tesis no construye en hardware: algo que corre **en el dispositivo de la
paciente**, le sirve la interfaz, la autentica contra el servidor central cuando
hay conexion, y sigue en pie cuando no la hay.

Diez modulos, cada uno con un trabajo:

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
    Ventana de solo lectura sobre la outbox del nodo edge compartido.
:mod:`app.gestante.simulacion`
    El paquete fijo y reproducible de una sesion de movimiento simulada, sin
    tocar el dataset canonico.
:mod:`app.gestante.movimientos`
    Captura y sincroniza esas sesiones por cuenta, delegando todo en
    ``app.edge`` -- un archivo SQLite propio por paciente, nunca compartido.
:mod:`app.gestante.rutas`
    Las trece rutas que ve el navegador.
:mod:`app.gestante.aplicacion`
    El ensamblado FastAPI.

El punto de entrada es ``scripts/gestante_web.py``.

**Lo que este paquete no hace, dicho con precision.** No clasifica lecturas --el
semaforo lo decide la fuente autorizada--, no escribe en PostgreSQL, no
reimplementa la idempotencia ni los reintentos del nodo edge, y no guarda
credenciales: la contrasena no se persiste en ninguna forma, y el token del
servidor central vive unicamente en memoria del proceso.

**Lo que el registro de movimientos simulados hace, y lo que no.** Captura un
paquete fijo localmente -- eso siempre funciona, sin red -- y puede intentar
sincronizarlo con una sola ronda real contra la API central. Esa sincronizacion
depende de que el dispositivo y el tiempo gestacional del paquete existan y
esten asignados en la base de datos que reciba el intento; este portal no tiene
forma de conocer esos datos de antemano, asi que puede fallar honestamente sin
que eso sea un defecto del registro local. No es un contador de movimientos que
la paciente perciba ni introduce, y ningun valor biometrico se genera al azar
en el navegador.

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
from app.gestante.movimientos import (
    leer_estado_de_la_cuenta,
    registrar_sesion_simulada,
    ruta_para_la_cuenta,
    sincronizar_cuenta,
)
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
    "leer_estado_de_la_cuenta",
    "leer_estado_local",
    "leer_version",
    "preparar_directorio",
    "registrar_sesion_simulada",
    "renovar",
    "ruta_para_la_cuenta",
    "sesion_de_la_lectura",
    "sincronizar_cuenta",
    "tablas_presentes",
    "transaccion",
    "ultima_lectura",
    "validar",
]
