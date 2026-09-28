"""Captura y envio automatico de sesiones de movimiento simuladas (SCRUM-72).

**Este modulo no reimplementa nada de `app.edge`.** Abre una conexion con
:func:`app.edge.conectar`, prepara el esquema con :func:`app.edge.inicializar`
y delega toda la logica de dominio a las funciones que ese paquete ya publica y
que SCRUM-64/65 ya probaron: :func:`app.edge.capturar` para guardar un paquete
sin red, :func:`app.edge.censar` y :meth:`app.edge.ClienteEdge.verificar_credencial`
para decidir si merece la pena intentar, :func:`app.edge.ejecutar_pasada` para
entregarlo, y :func:`app.edge.resumen` --por medio de
``app.gestante.estado_local.leer``-- para contar el resultado. Ninguna regla de
idempotencia, de reintento o de validacion se vuelve a escribir aqui.

**Nadie pulsa «capturar» ni «enviar».** La captura la produce el dispositivo
--en este MVP, ``scripts/dispositivo_gestante.py``, que llama a
:func:`registrar_sesion_simulada` con la cuenta y el embarazo del
aprovisionamiento--, y la entrega la dispara
:mod:`app.gestante.envio_automatico` en segundo plano, cuenta por cuenta, con
:func:`enviar_pendientes_de_la_cuenta`. El navegador no participa.

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

**El token central nunca llega a SQLite.** :func:`enviar_pendientes_de_la_cuenta`
recibe el token que ``app.gestante.rutas`` ya conserva en memoria, arma con el un
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
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

import httpx

from app.edge import (
    ClienteEdge,
    PoliticaDeReintentos,
    ResumenPasada,
    ahora_utc,
    capturar,
    censar,
    conectar,
    ejecutar_pasada,
    inicializar,
    preparar_directorio,
    reconciliar_abandonados,
    resolver_herencia,
)
from app.edge.captura import CapturaRegistrada
from app.edge.cliente import CODIGOS_DE_CREDENCIAL
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


@dataclass(frozen=True)
class EnvioDeLaCuenta:
    """Lo que hizo un intento automatico sobre la cola de una cuenta.

    ``pasada`` solo existe si se llego a ejecutar una ronda. Los otros dos
    desenlaces no reclaman ningun evento, y por eso no gastan intentos:

    * ``sin_pendientes``: no habia nada elegible ahora; no se toco la red;
    * ``sin_credencial_util``: la verificacion previa no paso --sin red, API
      caida, token vencido--; la cola queda exactamente como estaba.
    """

    sin_pendientes: bool = False
    sin_credencial_util: bool = False
    credencial_rechazada: bool = False
    pasada: ResumenPasada | None = None
    # Intentos abandonados --reclamados por un proceso que murio antes de
    # registrar su resultado-- que este ciclo sello al vencer su lease.
    intentos_reconciliados: int = 0


def enviar_pendientes_de_la_cuenta(
    settings: GestanteSettings,
    *,
    id_usuario: int,
    token: str,
    constructor_cliente_http: Callable[[GestanteSettings, str], httpx.Client]
    | None = None,
    reloj: Callable[[], datetime] = ahora_utc,
) -> EnvioDeLaCuenta:
    """Una ronda automatica sobre el archivo de esta cuenta, si tiene sentido.

    Cuatro pasos, todos de ``app.edge``:

    0. **Reparacion local**, sin red ni credencial, igual que hace el
       sincronizador de SCRUM-65 al principio de cada iteracion:
       :func:`app.edge.reconciliar_abandonados` sella los intentos cuyo lease
       vencio sin resultado --un portal que se apago en mitad de un envio-- y
       los devuelve al reintento programado (o al agotamiento, si era el
       ultimo), y :func:`app.edge.resolver_herencia` cierra los eventos
       heredados de SCRUM-64 que ya agotaron el limite. Sin esto, un intento
       abierto dejaria el evento fuera de los elegibles para siempre.
    1. **Censo** de la cola, sin red. Si no hay nada elegible ahora --vacia,
       todo entregado, un intento todavia en su lease, o un reintento
       programado para mas tarde--, se termina aqui. Un archivo que no existe
       no se crea.
    2. **Verificacion previa** de la credencial contra ``/yo``, la misma que usa
       ``edge_node.py``. Existe por la misma razon: reclamar un evento gasta un
       intento antes de enviar, asi que sin red o con un token vencido cada
       ciclo del emisor consumiria el presupuesto de paquetes que no tienen
       nada malo. Si no pasa, no se reclama nada.
    3. **Una ronda** con ``respetar_programacion=True``, igual que el
       sincronizador de SCRUM-65: la espera incremental que dejo un fallo se
       respeta, y la clave y los bytes guardados en la captura se reenvian sin
       cambios, asi que un reintento nunca crea una segunda sesion remota.

    ``constructor_cliente_http`` y ``reloj`` existen para las pruebas; en
    produccion se usan el cliente HTTP real y el reloj real.
    """
    ruta = ruta_para_la_cuenta(settings, id_usuario)
    if not ruta.exists():
        return EnvioDeLaCuenta(sin_pendientes=True)

    politica = PoliticaDeReintentos(http_timeout=settings.http_timeout)
    constructor = constructor_cliente_http or _construir_cliente_edge_http

    with conectar(ruta, espera_de_bloqueo_ms=settings.busy_timeout_ms) as conexion:
        inicializar(conexion)
        ahora = reloj()
        reconciliados = reconciliar_abandonados(conexion, politica=politica, momento=ahora)
        resolver_herencia(conexion, politica=politica, momento=ahora)
        censo = censar(conexion, max_attempts=politica.max_attempts, momento=reloj())
        if censo.elegibles_ahora == 0:
            return EnvioDeLaCuenta(sin_pendientes=True, intentos_reconciliados=reconciliados)

        with constructor(settings, token) as http:
            cliente = ClienteEdge(http)
            verificacion = cliente.verificar_credencial()
            if not verificacion.valido:
                return EnvioDeLaCuenta(
                    sin_credencial_util=True,
                    credencial_rechazada=verificacion.codigo_http
                    in CODIGOS_DE_CREDENCIAL,
                    intentos_reconciliados=reconciliados,
                )

            pasada = ejecutar_pasada(
                conexion,
                cliente,
                politica=politica,
                reloj=reloj,
                respetar_programacion=True,
            )

    return EnvioDeLaCuenta(
        pasada=pasada,
        credencial_rechazada=pasada.detenida_por_credencial,
        intentos_reconciliados=reconciliados,
    )
