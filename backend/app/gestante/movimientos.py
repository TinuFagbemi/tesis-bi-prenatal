"""Registro y sincronizacion de sesiones de movimiento simuladas (SCRUM-72).

**Este modulo no reimplementa nada de `app.edge`.** Abre una conexion con
:func:`app.edge.conectar`, prepara el esquema con :func:`app.edge.inicializar`
y delega toda la logica de dominio a las funciones que ese paquete ya publica y
que SCRUM-64/65 ya probaron: :func:`app.edge.capturar` para guardar un paquete
sin red, :func:`app.edge.ejecutar_pasada` para intentar entregarlo, y
:func:`app.edge.resumen` --por medio de ``app.gestante.estado_local.leer``--
para contar el resultado. Ninguna regla de idempotencia, de reintento o de
validacion se vuelve a escribir aqui.

**Un archivo SQLite por cuenta, nunca uno compartido.** El nodo edge clasico
--el que ``scripts/edge_node.py`` opera-- modela el dispositivo de una clinica
entera, y con toda razon: alli las sesiones de muchas pacientes conviven en una
sola cola, porque una clinica tiene un dispositivo y muchas pacientes. Este
adaptador modela otra cosa: el telefono de **una** paciente. Dos pacientes que
abren este mismo portal en el mismo proceso no deben poder ver ni un conteo de
la cola de la otra, y la manera de garantizarlo sin tocar el esquema de
``app.edge`` es que cada cuenta tenga su propio archivo, nombrado por su
``id_usuario`` -- el mismo identificador que ya distingue una sesion local de
otra en ``app.gestante.sesion``.

**El token central nunca llega a SQLite.** :func:`sincronizar_cuenta` recibe el
token que ``app.gestante.rutas`` ya conserva en memoria, arma con el un
``httpx.Client`` de vida corta -- igual que ``scripts/edge_node.py`` hace con
``EDGE_API_TOKEN`` -- y lo cierra al terminar. Ninguna fila de
``captura_local``, ``outbox`` ni ``intento_sincronizacion`` tiene una columna
donde guardarlo, y esta funcion no le busca una.

Todos los datos son ficticios y simulados.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import httpx

from app.edge import (
    ClienteEdge,
    PoliticaDeReintentos,
    ResumenPasada,
    capturar,
    conectar,
    ejecutar_pasada,
    inicializar,
    preparar_directorio,
)
from app.edge.captura import CapturaRegistrada
from app.gestante import estado_local
from app.gestante.config import GestanteSettings
from app.gestante.simulacion import construir_paquete_simulado
from app.models.enums import TipoSesion

CABECERA_AUTORIZACION = "Authorization"
ESQUEMA_BEARER = "Bearer"


def ruta_para_la_cuenta(settings: GestanteSettings, id_usuario: int) -> Path:
    """Un archivo SQLite propio de esta cuenta, dentro de ``movimientos_dir``.

    Nombrado por ``id_usuario`` y nada mas: ni el correo ni el rol, que no
    tienen por que aparecer en un nombre de archivo.
    """
    return settings.movimientos_dir / f"cuenta-{int(id_usuario)}.sqlite3"


def registrar_sesion_simulada(
    settings: GestanteSettings,
    *,
    id_usuario: int,
    id_embarazo: int,
    tipo_sesion: TipoSesion,
    ahora: datetime,
) -> CapturaRegistrada:
    """Construye el paquete fijo y lo captura localmente, sin tocar la red.

    ``id_embarazo`` llega ya verificado por quien llama --esta funcion no
    vuelve a preguntarle al servidor central-- y por eso no recibe un token: no
    lo necesita para escribir en el disco de este dispositivo.
    """
    paquete = construir_paquete_simulado(
        id_embarazo=id_embarazo, tipo_sesion=tipo_sesion, ahora=ahora
    )

    ruta = ruta_para_la_cuenta(settings, id_usuario)
    preparar_directorio(ruta)
    with conectar(ruta, espera_de_bloqueo_ms=settings.busy_timeout_ms) as conexion:
        inicializar(conexion)
        return capturar(conexion, paquete)


def leer_estado_de_la_cuenta(
    settings: GestanteSettings, id_usuario: int
) -> estado_local.EstadoLocal:
    """Los mismos conteos que ``/adaptador/estado-local``, sobre el archivo de
    **esta** cuenta en lugar del nodo edge compartido.

    Reutiliza :func:`app.gestante.estado_local.leer` sin cambiarla: esa funcion
    ya sabe convertir «el archivo no existe todavia» y «el archivo no se pudo
    leer» en un ``EstadoLocal`` seguro, y una cuenta que nunca registro una
    sesion simulada cae exactamente en el primer caso.
    """
    ruta = ruta_para_la_cuenta(settings, id_usuario)
    return estado_local.leer(ruta, espera_de_bloqueo_ms=settings.busy_timeout_ms)


def _construir_cliente_edge_http(
    settings: GestanteSettings, token: str
) -> httpx.Client:
    """El mismo patron que ``scripts/edge_node.py``: la credencial como
    cabecera por omision del cliente HTTP, nunca dentro del paquete."""
    return httpx.Client(
        base_url=settings.api_base_url,
        timeout=settings.http_timeout,
        headers={CABECERA_AUTORIZACION: f"{ESQUEMA_BEARER} {token}"},
    )


def sincronizar_cuenta(
    settings: GestanteSettings,
    *,
    id_usuario: int,
    token: str,
    constructor_cliente_http: Callable[[GestanteSettings, str], httpx.Client]
    | None = None,
) -> ResumenPasada:
    """Una sola ronda de envio -- el mismo mecanismo que ``edge_node.py enviar``
    -- sobre el archivo de esta cuenta, usando el token que ya esta en memoria.

    ``constructor_cliente_http`` existe solo para que una prueba pueda
    sustituir el transporte real por un ``httpx.MockTransport``, igual que
    ``ContextoAdaptador.cliente_central`` se sustituye para no necesitar la API
    de verdad. En produccion nunca se pasa: se usa el cliente HTTP real.
    """
    constructor = constructor_cliente_http or _construir_cliente_edge_http
    ruta = ruta_para_la_cuenta(settings, id_usuario)
    preparar_directorio(ruta)

    with constructor(settings, token) as http:
        cliente = ClienteEdge(http)
        with conectar(ruta, espera_de_bloqueo_ms=settings.busy_timeout_ms) as conexion:
            inicializar(conexion)
            return ejecutar_pasada(
                conexion,
                cliente,
                politica=PoliticaDeReintentos(http_timeout=settings.http_timeout),
            )
