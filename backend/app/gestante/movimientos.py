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

import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import replace
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
from app.gestante.provision import Provision
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
    provision: Provision,
    tipo_sesion: TipoSesion,
    ahora: datetime,
) -> CapturaRegistrada:
    """Construye el paquete con referencias reales y lo captura sin tocar la red.

    ``provision`` llega ya comprobado por quien llama --que esta cuenta y ese
    embarazo son a los que este dispositivo sirve-- y por eso esta funcion no
    recibe un token: no lo necesita para escribir en el disco de este
    dispositivo, y ese es justamente el punto de que la captura funcione sin
    conexion.
    """
    paquete = construir_paquete_simulado(
        provision=provision, tipo_sesion=tipo_sesion, ahora=ahora
    )

    ruta = ruta_para_la_cuenta(settings, id_usuario)
    preparar_directorio(ruta)
    with conectar(ruta, espera_de_bloqueo_ms=settings.busy_timeout_ms) as conexion:
        inicializar(conexion)
        return capturar(conexion, paquete)


MENSAJE_SIN_REGISTROS = "Todavia no has registrado ninguna sesion simulada."


def ultimo_envio_confirmado(settings: GestanteSettings, id_usuario: int) -> str | None:
    """Cuándo aceptó el servidor por última vez un registro de esta cuenta, o ``None``.

    Es ``enviado_en`` de la outbox: lo escribe ``app.edge`` solo cuando el
    servidor confirmó la entrega. Un ``/health`` correcto o una lectura clínica
    no lo mueven, y por eso es la única fuente de «último envío». Se abre en
    solo lectura y no se crea el archivo si no existe.
    """
    ruta = ruta_para_la_cuenta(settings, id_usuario)
    if not ruta.exists():
        return None
    try:
        with closing(
            sqlite3.connect(f"{ruta.resolve().as_uri()}?mode=ro", uri=True)
        ) as conexion:
            fila = conexion.execute(
                "SELECT max(enviado_en) FROM outbox WHERE estado = 'ENVIADO'"
            ).fetchone()
    except sqlite3.Error:
        return None
    return fila[0] if fila else None


def leer_estado_de_la_cuenta(
    settings: GestanteSettings, id_usuario: int
) -> estado_local.EstadoLocal:
    """Los mismos conteos que ``/adaptador/estado-local``, sobre el archivo de
    **esta** cuenta en lugar del nodo edge compartido.

    Reutiliza :func:`app.gestante.estado_local.leer` sin cambiarla: esa funcion
    ya sabe convertir «el archivo no existe todavia» y «el archivo no se pudo
    leer» en un ``EstadoLocal`` seguro, y una cuenta que nunca registro una
    sesion simulada cae exactamente en el primer caso.

    Lo unico que se sustituye es la frase de ese primer caso. La de
    ``estado_local`` dice como se crea el almacenamiento del **nodo edge**
    compartido --``edge_node.py init``--, y aqui eso seria una instruccion
    equivocada: el archivo de esta cuenta lo crea el propio portal cuando ella
    registra su primera sesion, sin que nadie ejecute nada.

    La frase del segundo caso --el archivo esta pero no se pudo leer-- se
    respeta tal cual: describe un problema real, y taparla con «todavia no has
    registrado nada» convertiria un fallo en un silencio.
    """
    ruta = ruta_para_la_cuenta(settings, id_usuario)
    estado = estado_local.leer(ruta, espera_de_bloqueo_ms=settings.busy_timeout_ms)
    if estado.inicializado or ruta.exists():
        return estado
    return replace(estado, detalle=MENSAJE_SIN_REGISTROS)


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
