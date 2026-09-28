"""Almacenamiento local de las sesiones de la interfaz de la gestante (SCRUM-72).

Un archivo SQLite propio, con una sola tabla, **separado del almacenamiento del
nodo edge**. La separacion no es organizativa, es una precaucion concreta:
``app.edge.almacenamiento.verificar_esquema`` compara la definicion guardada con
la que ese paquete crearia, y una tabla ajena dentro de su archivo convertiria
una comprobacion que hoy pasa en una que falla. El nodo edge conserva su
esquema, su version y sus garantias intactos; este adaptador no le anade nada.

**Que se guarda, y por que tan poco.** Un identificador de sesion --su digest,
no el identificador--, a quien pertenece, su rol, y tres instantes. Nada mas.

No se guarda la contrasena, ni cifrada ni de ninguna otra forma. No se guarda el
token del servidor central: vive en memoria del proceso y desaparece con el. No
se guarda el correo, porque para validar una sesion no hace falta y almacenarlo
convertiria este archivo en una lista de cuentas. No se guarda ningun dato
clinico: este archivo no sabe nada de embarazos ni de lecturas.

**El identificador se guarda como digest.** Lo que viaja en la cookie es un
valor aleatorio; lo que queda en disco es su SHA-256. Quien pueda leer el
archivo no obtiene con ello una cookie utilizable, del mismo modo que un digest
de contrasena no devuelve la contrasena. Es una precaucion barata para un
archivo que vive en el dispositivo de la paciente.

**Los instantes se guardan como texto ISO-8601 en UTC**, la misma convencion que
``app.edge.outbox``: un formato, un desfase, siempre ``+00:00``. Ordenar por la
columna es ordenar en el tiempo, y comparar ``expira_en`` con el instante actual
es una comparacion lexicografica correcta.

Este modulo ``execute``, y no decide nada. La transaccion pertenece a quien
llama, igual que en ``app.edge.outbox`` y en ``app.services.ingesta``.

Todos los datos que maneja esta interfaz son ficticios y simulados.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from app.models.enums import NombreRol

VERSION_DE_ESQUEMA = 1

TABLA_SESION = "sesion_local"

# El unico rol que puede abrir una sesion en esta interfaz. Se renderiza desde
# el enum en lugar de escribirse a mano, igual que ``app.edge.almacenamiento``
# renderiza sus CHECK desde ``EstadoEntrega``: un rol que cambie en Python no
# puede quedar sin representar en la base.
#
# Es defensa en profundidad, no el control de acceso. La autoridad es el
# servidor central, que resuelve el rol contra PostgreSQL en cada peticion; esta
# restriccion solo garantiza que un fallo de este lado no pueda materializar una
# sesion local de un rol que no deberia tenerla.
ROL_PERMITIDO = NombreRol.PACIENTE

DDL_SESION = f"""
CREATE TABLE {TABLA_SESION} (
    id_sesion   INTEGER PRIMARY KEY,
    hash_sesion TEXT    NOT NULL UNIQUE,
    id_usuario  INTEGER NOT NULL,
    rol         TEXT    NOT NULL CHECK (rol = '{ROL_PERMITIDO.value}'),
    validada_en TEXT    NOT NULL,
    expira_en   TEXT    NOT NULL,
    cerrada_en  TEXT
)
"""

# No hay mas indices que el UNIQUE de ``hash_sesion``, y es el unico que hace
# falta: todas las consultas de este adaptador buscan una sesion por su digest.
# Un indice sobre ``expira_en`` solo serviria para un barrido de sesiones
# vencidas, que este ticket no implementa; crearlo ahora seria peso muerto.


class ErrorDeAlmacenamientoLocal(Exception):
    """Base de los fallos que este modulo reporta con un mensaje propio."""

    def __init__(self, detalle: str) -> None:
        self.detalle = detalle
        super().__init__(detalle)


def preparar_directorio(ruta: Path) -> None:
    """Crea la carpeta donde vive la base, y solo eso.

    Nunca crea ni borra el archivo de base de datos: la carpeta es
    infraestructura, el archivo es dato. Misma frontera que traza
    ``app.edge.almacenamiento.preparar_directorio``.
    """
    ruta.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def conectar(
    ruta: Path | str, *, espera_de_bloqueo_ms: int = 5000
) -> Iterator[sqlite3.Connection]:
    """Una conexion, con las mismas garantias que usa el nodo edge.

    ``isolation_level=None`` desactiva el manejo implicito de transacciones del
    driver: cada transaccion la abre :func:`transaccion` con un ``BEGIN
    IMMEDIATE`` explicito y la cierra un ``COMMIT`` o un ``ROLLBACK`` explicito,
    de modo que los limites son los que estan escritos en el codigo.

    ``PRAGMA foreign_keys`` se fija antes de que exista ninguna transaccion,
    porque SQLite lo ignora dentro de una y no avisa cuando lo hace.
    """
    conexion = sqlite3.connect(ruta, isolation_level=None)
    try:
        conexion.row_factory = sqlite3.Row
        conexion.execute("PRAGMA foreign_keys = ON")
        conexion.execute(f"PRAGMA busy_timeout = {int(espera_de_bloqueo_ms)}")
        yield conexion
    finally:
        conexion.close()


@contextmanager
def transaccion(conexion: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Una unidad de trabajo explicita: todo, o nada."""
    conexion.execute("BEGIN IMMEDIATE")
    try:
        yield conexion
    except BaseException:
        conexion.execute("ROLLBACK")
        raise
    else:
        conexion.execute("COMMIT")


def leer_version(conexion: sqlite3.Connection) -> int:
    """Valor de ``PRAGMA user_version``."""
    return int(conexion.execute("PRAGMA user_version").fetchone()[0])


def tablas_presentes(conexion: sqlite3.Connection) -> frozenset[str]:
    """Nombres de las tablas del archivo, sin las propias de SQLite."""
    filas = conexion.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return frozenset(fila["name"] for fila in filas)


def inicializar(conexion: sqlite3.Connection) -> bool:
    """Crea el esquema si el archivo esta sin estrenar. Devuelve si escribio.

    A diferencia del almacenamiento del nodo edge, este archivo **si** pertenece
    al adaptador, asi que crearlo es su trabajo y no una intromision.

    Un archivo que ya tiene la version actual se deja intacto. Un archivo con
    una version distinta se rechaza en lugar de repararse: una sesion local es
    estado desechable --lo peor que ocurre al borrarlo es volver a iniciar
    sesion-- y adivinar como migrar algo asi tendria mas riesgo que valor.
    """
    version = leer_version(conexion)

    if version == VERSION_DE_ESQUEMA and TABLA_SESION in tablas_presentes(conexion):
        return False

    if version not in (0, VERSION_DE_ESQUEMA):
        raise ErrorDeAlmacenamientoLocal(
            f"El almacenamiento local de sesiones tiene la version {version} y "
            f"este programa usa la {VERSION_DE_ESQUEMA}. Cierra sesion y borra "
            "el archivo para volver a empezar; no contiene datos clinicos."
        )

    with transaccion(conexion):
        conexion.execute(DDL_SESION)
        conexion.execute(f"PRAGMA user_version = {VERSION_DE_ESQUEMA}")

    return True


def abrir_almacen(
    ruta: Path, *, espera_de_bloqueo_ms: int = 5000
) -> AbstractContextManager[sqlite3.Connection]:
    """Conexion al archivo indicado, creando antes su carpeta.

    Envoltorio de conveniencia para las rutas, que necesitan las dos cosas en
    ese orden en cada peticion.

    No lleva ``@contextmanager`` y no usa ``yield``: **devuelve** el gestor que
    construye :func:`conectar`, en lugar de ser uno. Por eso su anotacion es
    ``AbstractContextManager`` y no ``Iterator`` --el objeto que entrega tiene
    ``__enter__`` y ``__exit__``, y no tiene ``__iter__``--.
    """
    preparar_directorio(ruta)
    return conectar(ruta, espera_de_bloqueo_ms=espera_de_bloqueo_ms)
