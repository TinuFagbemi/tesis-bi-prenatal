"""Lectura del estado local del nodo edge, para mostrarlo en la interfaz.

Este modulo es una **ventana de solo lectura** sobre el almacenamiento que
gestiona ``app.edge``. Llama a funciones que ese paquete ya publica y no escribe
una sola fila.

**No inicializa el almacenamiento, y la omision es deliberada.** ``sqlite3``
crea el archivo al conectarse aunque no exista, asi que la comprobacion de
existencia va **antes** de abrir la conexion. Un portal que creara en silencio
la base del nodo edge estaria tomando una decision que pertenece a quien opera
ese nodo, y lo haria por el camino mas facil de pasar por alto: el de no hacer
nada visible. Cuando el archivo no esta, esta interfaz lo dice y explica con que
comando se crea.

**Lo que sale de aqui son conteos, y solo conteos.** Ni un paquete, ni una clave
de idempotencia, ni un identificador remoto, ni un mensaje de error del
servidor. La interfaz tiene que poder decir «tienes tres registros sin enviar»
sin que ningun dato clinico llegue al navegador para lograrlo, y el modo de
garantizarlo es que este modulo nunca lea esos campos.

**Los estados son los que ya existen.** ``PENDIENTE``, ``ENVIADO`` y
``FALLIDO`` --este ultimo separado entre reintentable y «requiere revision»--
son exactamente los que declara ``app.edge.estados`` y los que cuenta
``app.edge.outbox.resumen``. Este adaptador no inventa un cuarto ni renombra
ninguno.

**Un cero es un cero.** Cuando el almacenamiento existe y esta vacio, los
conteos valen 0 y eso es un dato verdadero. Cuando el almacenamiento no existe,
los conteos son ``None`` y la interfaz muestra «—». Las dos situaciones son
distintas y no deben mostrarse igual: es la misma regla que gobierna las
metricas clinicas.

Todos los datos que maneja esta interfaz son ficticios y simulados.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.edge.almacenamiento import (
    TABLA_OUTBOX,
    ErrorDeAlmacenamiento,
    conectar,
    tablas_presentes,
)
from app.edge.outbox import resumen

registrador = logging.getLogger(__name__)

COMANDO_DE_INICIALIZACION = "python scripts/edge_node.py init"

MENSAJE_SIN_ALMACENAMIENTO = (
    "El almacenamiento local de este dispositivo todavia no existe. Se crea con "
    f"'{COMANDO_DE_INICIALIZACION}'."
)

MENSAJE_ILEGIBLE = (
    "No se pudo leer el almacenamiento local de este dispositivo. Los registros "
    "guardados no se han perdido: revisa el nodo edge."
)


@dataclass(frozen=True)
class EstadoLocal:
    """Lo que la interfaz muestra del nodo de este dispositivo.

    Cuando ``inicializado`` es ``False`` los cuatro conteos son ``None`` y
    ``detalle`` explica por que. Cuando es ``True`` los cuatro son enteros, el
    cero incluido.

    ``ultima_sincronizacion`` es ``None`` **siempre** en esta version. El dato
    existe en el almacenamiento, pero ``app.edge`` no publica hoy ninguna
    funcion que lo devuelva, y este adaptador no consulta las tablas del nodo
    por su cuenta: todas las lecturas de esa outbox viven en
    ``app.edge.outbox``, y abrir una excepcion aqui seria la primera grieta en
    esa regla. El campo se declara igualmente para que el contrato sea explicito
    --el dato no esta disponible-- en lugar de faltar sin mas.
    """

    inicializado: bool
    pendientes: int | None = None
    enviados: int | None = None
    fallidos_reintentables: int | None = None
    fallidos_en_revision: int | None = None
    total: int | None = None
    ultima_sincronizacion: str | None = None
    detalle: str | None = None


def _sin_almacenamiento(detalle: str) -> EstadoLocal:
    return EstadoLocal(inicializado=False, detalle=detalle)


def leer(ruta: Path, *, espera_de_bloqueo_ms: int = 5000) -> EstadoLocal:
    """Los conteos de la outbox del nodo, sin crear ni modificar nada.

    Cualquier problema --el archivo no esta, no es una base de datos, tiene otro
    esquema, o SQLite es demasiado antiguo para lo que el nodo exige-- se
    convierte en un estado «no inicializado» con una frase, nunca en una
    excepcion que tumbe la peticion: el resto de la interfaz tiene que seguir
    funcionando aunque esta tarjeta no pueda rellenarse.
    """
    if not ruta.exists():
        # Antes de conectar, y esa es toda la razon de que esta linea exista:
        # ``sqlite3.connect`` crearia el archivo.
        return _sin_almacenamiento(MENSAJE_SIN_ALMACENAMIENTO)

    try:
        with conectar(ruta, espera_de_bloqueo_ms=espera_de_bloqueo_ms) as conexion:
            if TABLA_OUTBOX not in tablas_presentes(conexion):
                return _sin_almacenamiento(MENSAJE_SIN_ALMACENAMIENTO)
            actual = resumen(conexion)
    except (ErrorDeAlmacenamiento, sqlite3.Error) as error:
        # Solo la clase de la excepcion. El mensaje del driver puede llevar la
        # ruta del archivo, y la ruta no tiene por que salir al navegador.
        registrador.warning(
            "Estado local: no se pudo leer el almacenamiento (%s).",
            type(error).__name__,
        )
        return _sin_almacenamiento(MENSAJE_ILEGIBLE)

    return EstadoLocal(
        inicializado=True,
        pendientes=actual.pendientes,
        enviados=actual.enviados,
        fallidos_reintentables=actual.fallidos_reintentables,
        fallidos_en_revision=actual.fallidos_en_revision,
        total=actual.total,
        ultima_sincronizacion=None,
    )
