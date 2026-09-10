"""Local SQLite storage of the simulated edge node: schema, connections, guards.

This module owns the file. Everything that decides *what the local database
looks like* and *how a connection to it behaves* is here, and nothing else is.

**Two tables, and why not one.** ``captura_local`` holds the package exactly as
it was validated, and never changes again. ``outbox`` holds the delivery state,
which changes on every attempt. Collapsing them would mix an immutable record
with a mutable one in the same row, and the property this ticket has to
demonstrate -- «the payload and the key do not change between attempts» --
would stop being visible in the schema and become a rule of the code.

**Integrity is the schema's job, not Python's.** A primary key per row, a
foreign key from the outbox to its capture, ``UNIQUE`` on both the capture
reference and the idempotency key, ``NOT NULL`` where a value is required, and
five ``CHECK`` constraints. In particular ``evidencia_remota_coherente`` states
something a naive constraint gets wrong: remote evidence is present **exactly**
when the state is ``ENVIADO``, and is *entirely absent* otherwise. A constraint
written as ``(estado = 'ENVIADO') = (a IS NOT NULL AND b IS NOT NULL AND ...)``
looks equivalent and is not: with the state anything but ``ENVIADO`` and only
*some* of the columns filled, the right-hand side is false, both sides agree,
and a half-written result is admitted. The ``CASE`` form says what was meant.

**``PRAGMA foreign_keys`` is per connection and off by default**, so it is set
on every connection this module hands out -- and it must be set *outside* a
transaction, because SQLite ignores the pragma inside one, silently.

**Versioning without a second migration framework.** ``PRAGMA user_version``
holds a single integer, and that is enough for a schema with one version. It is
also transactional: creating the tables and stamping the version happen in one
``BEGIN IMMEDIATE`` ... ``COMMIT``, so a failure leaves neither. There is no
path here that runs ``DROP``, ``TRUNCATE`` or any corrective write: when the
file is not what this version expects, initialisation **refuses** and says why.

All data stored here is fictitious and simulated.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.edge.estados import VALORES_DE_ESTADO

# Bump this when the local schema changes shape. There is exactly one version so
# far, and no upgrade path: a file stamped with anything else is refused rather
# than migrated, because a prototype that silently rewrites a database it does
# not recognise is worse than one that stops.
VERSION_DE_ESQUEMA = 1

# Version of a database SQLite has just created and nobody has stamped.
VERSION_SIN_ESTRENAR = 0

TABLA_CAPTURA = "captura_local"
TABLA_OUTBOX = "outbox"

# Rendered from the enum instead of written by hand, so a state cannot be added
# in Python and stay unrepresented in the constraint that limits the column.
_LISTA_DE_ESTADOS = ", ".join(f"'{valor}'" for valor in VALORES_DE_ESTADO)

DDL_CAPTURA = f"""
CREATE TABLE {TABLA_CAPTURA} (
    id_captura   INTEGER PRIMARY KEY,
    payload_json TEXT    NOT NULL,
    capturado_en TEXT    NOT NULL
)
"""

DDL_OUTBOX = f"""
CREATE TABLE {TABLA_OUTBOX} (
    id_outbox           INTEGER PRIMARY KEY,
    id_captura          INTEGER NOT NULL UNIQUE REFERENCES {TABLA_CAPTURA}(id_captura),
    clave_idempotencia  TEXT    NOT NULL UNIQUE,
    estado              TEXT    NOT NULL,
    reintentable        INTEGER,
    intentos            INTEGER NOT NULL DEFAULT 0,
    creado_en           TEXT    NOT NULL,
    actualizado_en      TEXT    NOT NULL,
    enviado_en          TEXT,
    ultimo_http         INTEGER,
    ultimo_error        TEXT,
    id_sesion_remota    INTEGER,
    ids_lectura_remotos TEXT,
    CONSTRAINT estado_valido
        CHECK (estado IN ({_LISTA_DE_ESTADOS})),
    CONSTRAINT intentos_no_negativo
        CHECK (intentos >= 0),
    CONSTRAINT reintentable_binario
        CHECK (reintentable IS NULL OR reintentable IN (0, 1)),
    CONSTRAINT reintentable_solo_en_fallido
        CHECK ((estado = 'FALLIDO') = (reintentable IS NOT NULL)),
    CONSTRAINT evidencia_remota_coherente
        CHECK (
            CASE estado
                WHEN 'ENVIADO' THEN
                    enviado_en          IS NOT NULL
                    AND id_sesion_remota    IS NOT NULL
                    AND ids_lectura_remotos IS NOT NULL
                ELSE
                    enviado_en          IS NULL
                    AND id_sesion_remota    IS NULL
                    AND ids_lectura_remotos IS NULL
            END
        )
)
"""


class ErrorDeAlmacenamiento(Exception):
    """Base of the failures this module reports with a message of its own.

    ``detalle`` carries text written here -- never a driver message, never a
    path with somebody's name in it, never the contents of a row -- so a caller
    can show it without sanitising anything.
    """

    def __init__(self, detalle: str) -> None:
        self.detalle = detalle
        super().__init__(detalle)


class EsquemaIncompatible(ErrorDeAlmacenamiento):
    """The file exists but is not a local store this version can use."""


# ---------------------------------------------------------------------------
# Connections and transactions
# ---------------------------------------------------------------------------


@contextmanager
def conectar(
    ruta: Path | str, *, espera_de_bloqueo_ms: int = 5000
) -> Iterator[sqlite3.Connection]:
    """One connection, with referential integrity actually turned on.

    ``isolation_level=None`` disables the driver's implicit transaction
    handling. That is not a detail: with the default, ``sqlite3`` opens a
    transaction behind your back before a DML statement and leaves DDL outside
    it, and «this is atomic» becomes a claim about driver internals. Here every
    transaction is opened by :func:`transaccion` with an explicit
    ``BEGIN IMMEDIATE`` and closed by an explicit ``COMMIT`` or ``ROLLBACK``, so
    the boundaries are the ones written in the code.

    ``PRAGMA foreign_keys`` is set here, before any transaction exists, because
    SQLite ignores it inside one and reports no error when it does.

    ``busy_timeout`` is insurance against a *lock*, and nothing more: it makes a
    second sender invoked by mistake fail with a readable error instead of
    hanging. It does not, and cannot, protect a state transition -- that is what
    the conditional ``UPDATE`` in :mod:`app.edge.outbox` is for.
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
    """An explicit unit of work: everything, or nothing at all.

    ``BEGIN IMMEDIATE`` rather than the default deferred begin, so the write
    lock is taken when the transaction opens instead of at the first write. A
    second writer then finds out immediately -- within ``busy_timeout`` -- rather
    than half-way through a unit of work it would have to undo.
    """
    conexion.execute("BEGIN IMMEDIATE")
    try:
        yield conexion
    except BaseException:
        conexion.execute("ROLLBACK")
        raise
    else:
        conexion.execute("COMMIT")


# ---------------------------------------------------------------------------
# What the schema is expected to guarantee
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnaEsperada:
    """One column and the guarantees it must carry."""

    nombre: str
    tipo: str
    no_nula: bool
    es_pk: bool


@dataclass(frozen=True)
class ForaneaEsperada:
    """One foreign key and where it must point."""

    columna: str
    tabla_destino: str
    columna_destino: str


@dataclass(frozen=True)
class TablaEsperada:
    """Everything :func:`verificar_esquema` insists on for one table."""

    nombre: str
    ddl: str
    columnas: tuple[ColumnaEsperada, ...]
    unicos: frozenset[frozenset[str]]
    foraneas: tuple[ForaneaEsperada, ...]


# These expectations are written out by hand rather than read back from a
# freshly created database, and that is the point: derived from the DDL they
# would agree with it by construction and prove nothing. Declared independently,
# a test can assert that the DDL satisfies them, and the two can disagree --
# which is what makes the check worth running.
ESQUEMA_ESPERADO: tuple[TablaEsperada, ...] = (
    TablaEsperada(
        nombre=TABLA_CAPTURA,
        ddl=DDL_CAPTURA,
        columnas=(
            ColumnaEsperada("id_captura", "INTEGER", no_nula=False, es_pk=True),
            ColumnaEsperada("payload_json", "TEXT", no_nula=True, es_pk=False),
            ColumnaEsperada("capturado_en", "TEXT", no_nula=True, es_pk=False),
        ),
        unicos=frozenset(),
        foraneas=(),
    ),
    TablaEsperada(
        nombre=TABLA_OUTBOX,
        ddl=DDL_OUTBOX,
        columnas=(
            ColumnaEsperada("id_outbox", "INTEGER", no_nula=False, es_pk=True),
            ColumnaEsperada("id_captura", "INTEGER", no_nula=True, es_pk=False),
            ColumnaEsperada("clave_idempotencia", "TEXT", no_nula=True, es_pk=False),
            ColumnaEsperada("estado", "TEXT", no_nula=True, es_pk=False),
            ColumnaEsperada("reintentable", "INTEGER", no_nula=False, es_pk=False),
            ColumnaEsperada("intentos", "INTEGER", no_nula=True, es_pk=False),
            ColumnaEsperada("creado_en", "TEXT", no_nula=True, es_pk=False),
            ColumnaEsperada("actualizado_en", "TEXT", no_nula=True, es_pk=False),
            ColumnaEsperada("enviado_en", "TEXT", no_nula=False, es_pk=False),
            ColumnaEsperada("ultimo_http", "INTEGER", no_nula=False, es_pk=False),
            ColumnaEsperada("ultimo_error", "TEXT", no_nula=False, es_pk=False),
            ColumnaEsperada("id_sesion_remota", "INTEGER", no_nula=False, es_pk=False),
            ColumnaEsperada("ids_lectura_remotos", "TEXT", no_nula=False, es_pk=False),
        ),
        # One outbox row per capture, and one capture per key. The first is what
        # keeps a package from being queued twice; the second is what keeps two
        # different packages from sharing an Idempotency-Key locally, which the
        # server would answer with a 409.
        unicos=frozenset(
            {frozenset({"id_captura"}), frozenset({"clave_idempotencia"})}
        ),
        foraneas=(ForaneaEsperada("id_captura", TABLA_CAPTURA, "id_captura"),),
    ),
)

# Constraint names the definition must still carry. Checked by name because
# ``PRAGMA`` does not expose CHECK constraints at all: the only place they exist
# to be inspected is the stored ``CREATE TABLE`` text.
RESTRICCIONES_CHECK_ESPERADAS: dict[str, tuple[str, ...]] = {
    TABLA_OUTBOX: (
        "estado_valido",
        "intentos_no_negativo",
        "reintentable_binario",
        "reintentable_solo_en_fallido",
        "evidencia_remota_coherente",
    ),
}


def _normalizar(sql: str) -> str:
    """Collapse every run of whitespace, so formatting is not a difference."""
    return " ".join(sql.split())


# ---------------------------------------------------------------------------
# Reading what is actually there
# ---------------------------------------------------------------------------


def leer_version(conexion: sqlite3.Connection) -> int:
    """Value of ``PRAGMA user_version``."""
    return int(conexion.execute("PRAGMA user_version").fetchone()[0])


def tablas_presentes(conexion: sqlite3.Connection) -> frozenset[str]:
    """Names of the tables in the file, excluding SQLite's own."""
    filas = conexion.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return frozenset(fila["name"] for fila in filas)


def _definicion_almacenada(conexion: sqlite3.Connection, tabla: str) -> str | None:
    fila = conexion.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (tabla,)
    ).fetchone()
    return None if fila is None else fila["sql"]


def _columnas_reales(
    conexion: sqlite3.Connection, tabla: str
) -> tuple[ColumnaEsperada, ...]:
    return tuple(
        ColumnaEsperada(
            nombre=fila["name"],
            tipo=fila["type"],
            no_nula=bool(fila["notnull"]),
            es_pk=bool(fila["pk"]),
        )
        for fila in conexion.execute(f"PRAGMA table_info({tabla})").fetchall()
    )


def _unicos_reales(
    conexion: sqlite3.Connection, tabla: str
) -> frozenset[frozenset[str]]:
    """Column sets SQLite actually enforces as unique, from its own indexes."""
    conjuntos = set()
    for indice in conexion.execute(f"PRAGMA index_list({tabla})").fetchall():
        if not indice["unique"]:
            continue
        columnas = conexion.execute(f"PRAGMA index_info({indice['name']})").fetchall()
        conjuntos.add(frozenset(columna["name"] for columna in columnas))
    return frozenset(conjuntos)


def _foraneas_reales(
    conexion: sqlite3.Connection, tabla: str
) -> tuple[ForaneaEsperada, ...]:
    return tuple(
        ForaneaEsperada(
            columna=fila["from"],
            tabla_destino=fila["table"],
            columna_destino=fila["to"],
        )
        for fila in conexion.execute(f"PRAGMA foreign_key_list({tabla})").fetchall()
    )


def verificar_esquema(conexion: sqlite3.Connection) -> None:
    """Refuse anything that is not exactly the schema of this version.

    Names are the cheap half. What this insists on is the set of *guarantees*:
    every column with its type and its ``NOT NULL``, the primary keys, the
    unique constraints SQLite is really enforcing (read from its own indexes,
    not from the text), the foreign keys and where they point, and finally the
    stored ``CREATE TABLE`` text -- which is the only place a ``CHECK``
    constraint can be inspected, because no ``PRAGMA`` reports one.

    The structural checks run before the textual one so that a missing column or
    a dropped foreign key produces a message naming it, and the text comparison
    is left as the catch-all for everything else, a weakened ``CHECK`` above all.

    Raises :class:`EsquemaIncompatible` on the first disagreement. Never writes.
    """
    presentes = tablas_presentes(conexion)

    for esperada in ESQUEMA_ESPERADO:
        if esperada.nombre not in presentes:
            raise EsquemaIncompatible(
                f"El almacenamiento local declara la version {VERSION_DE_ESQUEMA} "
                f"pero le falta la tabla '{esperada.nombre}'."
            )

        reales = _columnas_reales(conexion, esperada.nombre)
        if reales != esperada.columnas:
            raise EsquemaIncompatible(
                f"La tabla '{esperada.nombre}' no tiene las columnas, tipos o "
                "restricciones NOT NULL/PK que esta version espera."
            )

        unicos = _unicos_reales(conexion, esperada.nombre)
        if not esperada.unicos <= unicos:
            faltantes = sorted(
                ",".join(sorted(conjunto)) for conjunto in (esperada.unicos - unicos)
            )
            raise EsquemaIncompatible(
                f"La tabla '{esperada.nombre}' no garantiza como UNIQUE: {faltantes}."
            )

        foraneas = _foraneas_reales(conexion, esperada.nombre)
        if foraneas != esperada.foraneas:
            raise EsquemaIncompatible(
                f"La tabla '{esperada.nombre}' no declara las llaves foraneas "
                "que esta version espera."
            )

        almacenada = _definicion_almacenada(conexion, esperada.nombre) or ""
        for restriccion in RESTRICCIONES_CHECK_ESPERADAS.get(esperada.nombre, ()):
            if restriccion not in almacenada:
                raise EsquemaIncompatible(
                    f"La tabla '{esperada.nombre}' no conserva la restriccion "
                    f"'{restriccion}'."
                )

        if _normalizar(almacenada) != _normalizar(esperada.ddl):
            raise EsquemaIncompatible(
                f"La definicion de '{esperada.nombre}' difiere de la que esta "
                "version crea."
            )

    sobrantes = presentes - {tabla.nombre for tabla in ESQUEMA_ESPERADO}
    if sobrantes:
        raise EsquemaIncompatible(
            "El archivo contiene tablas ajenas a este almacenamiento: "
            f"{sorted(sobrantes)}."
        )


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


def inicializar(conexion: sqlite3.Connection) -> bool:
    """Create the schema when it is safe to, verify it when it is already there.

    Idempotent, and refusing is one of its normal outcomes. Six situations, and
    only the first one writes anything:

    ============================== ==========================================
    Estado del archivo             Accion
    ============================== ==========================================
    version 0, sin tablas          crear todo y estampar la version, atomico
    version 0, tablas propias      rechazar: esquema previo al versionado
    version 0, tablas ajenas       rechazar: el archivo es de otra base
    version esperada, correcto     no hacer nada
    version esperada, divergente   rechazar: la version miente sobre el esquema
    version distinta               rechazar: archivo de otra version
    ============================== ==========================================

    Returns ``True`` when it created the schema and ``False`` when it found it
    already in place, so a caller can tell the two apart without inspecting the
    file itself.

    Creation is one ``BEGIN IMMEDIATE`` ... ``COMMIT``: both tables and the
    version stamp, or neither. ``PRAGMA user_version`` takes part in the
    transaction like any other write, so a failure half-way cannot leave a file
    that claims to be version 1 with one table in it.
    """
    version = leer_version(conexion)
    presentes = tablas_presentes(conexion)

    if version == VERSION_SIN_ESTRENAR:
        if not presentes:
            with transaccion(conexion):
                conexion.execute(DDL_CAPTURA)
                conexion.execute(DDL_OUTBOX)
                conexion.execute(f"PRAGMA user_version = {VERSION_DE_ESQUEMA}")
            return True

        propias = presentes & {tabla.nombre for tabla in ESQUEMA_ESPERADO}
        if propias:
            raise EsquemaIncompatible(
                "El archivo ya contiene tablas de este almacenamiento pero no "
                "declara ninguna version. No se completa ni se sobrescribe un "
                "esquema parcial o anterior al versionado; revisa el archivo o "
                "usa una ruta nueva."
            )
        raise EsquemaIncompatible(
            "El archivo indicado ya es una base SQLite con otras tablas. No se "
            "agregan las tablas del nodo edge a una base ajena; usa una ruta "
            "dedicada."
        )

    if version != VERSION_DE_ESQUEMA:
        raise EsquemaIncompatible(
            f"El almacenamiento local declara la version {version} y esta "
            f"instalacion solo entiende la {VERSION_DE_ESQUEMA}. No se modifica "
            "un archivo de otra version."
        )

    verificar_esquema(conexion)
    return False


def preparar_directorio(ruta: Path) -> None:
    """Create the folder the database lives in, and only that.

    Never creates or removes a database file: the folder is infrastructure, the
    file is data.
    """
    ruta.parent.mkdir(parents=True, exist_ok=True)
