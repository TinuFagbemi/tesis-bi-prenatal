"""Local SQLite storage of the simulated edge node: schema, connections, guards.

This module owns the file. Everything that decides *what the local database
looks like* and *how a connection to it behaves* is here, and nothing else is.

**Three tables as of SCRUM-65, and why not fewer.** ``captura_local`` holds the
package exactly as it was validated, and never changes again. ``outbox`` holds
the delivery state, which changes on every attempt. ``intento_sincronizacion``
holds one row per attempt ever started, so «what happened to this event» stops
being a single ``ultimo_error`` that the next attempt overwrites. Collapsing any
two of them would mix records with different lifetimes in one row, and the
properties this work has to demonstrate -- «the payload and the key do not change
between attempts», «every attempt left evidence» -- would stop being visible in
the schema and become rules of the code.

**Integrity is the schema's job, not Python's.** A primary key per row, foreign
keys, ``UNIQUE`` where a value identifies something, ``NOT NULL`` where a value
is required, and a ``CHECK`` for every closed vocabulary and every invariant that
can be written down. In particular ``evidencia_remota_coherente`` states
something a naive constraint gets wrong: remote evidence is present **exactly**
when the state is ``ENVIADO``, and is *entirely absent* otherwise. A constraint
written as ``(estado = 'ENVIADO') = (a IS NOT NULL AND b IS NOT NULL AND ...)``
looks equivalent and is not: with the state anything but ``ENVIADO`` and only
*some* of the columns filled, the right-hand side is false, both sides agree,
and a half-written result is admitted. The ``CASE`` form says what was meant.

**The two partial indexes are load-bearing.** ``ux_intento_abierto`` is the local
lease: it makes «at most one open, unreconciled attempt per event» a guarantee of
the database rather than a convention of the code, so two synchronizers invoked
by mistake cannot both have an attempt in flight for the same package.
``ux_confirmo_transicion`` makes «exactly one attempt applied the local
transition» equally checkable -- which is what lets the confirmation timestamp be
*derived* from one attempt instead of duplicated onto the outbox row, where no
``CHECK`` could ever keep the two copies in step. Note what the first index does
**not** claim: it bounds open rows in SQLite, not HTTP requests physically alive.
Several attempts of the same event may legitimately end in ``ENTREGADO`` -- one
initial and one replay -- and all of them are kept.

**``PRAGMA foreign_keys`` is per connection and off by default**, so it is set on
every connection this module hands out -- and it must be set *outside* a
transaction, because SQLite ignores the pragma inside one, silently.

**Versioning without a second migration framework.** ``PRAGMA user_version``
holds a single integer. It is also transactional, so creating the tables and
stamping the version happen in one ``BEGIN IMMEDIATE`` ... ``COMMIT``, and a
failure leaves neither.

**The v1 -> v2 upgrade rebuilds ``outbox``, and the order matters.** SQLite cannot
add a ``CHECK`` to an existing table, so the five new invariants require a new
table. The rebuild renames the **old** table out of the way and creates the new
one **directly under its final name**, rather than creating ``outbox_v2`` and
renaming it into place. That is not a stylistic choice: ``ALTER TABLE ... RENAME
TO outbox`` makes SQLite store the definition as ``CREATE TABLE "outbox" (...)``,
with quotes, and :func:`verificar_esquema` compares the stored text against the
text this module would write. Renaming the new table would therefore produce a
database that the very next initialisation refuses -- a failure only reachable
with real data. Creating it under its final name makes a migrated file
byte-identical to a fresh one.

The only destructive statement in this module drops the **renamed copy of the old
table**, inside that transaction, after its rows have already been inserted into
the new one. There is no other ``DROP``, no ``TRUNCATE``, no ``DELETE``, and no
corrective write anywhere: when the file is not something this version
understands, initialisation **refuses** and says why.

All data stored here is fictitious and simulated.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.edge.estados import (
    VALORES_DE_ESTADO,
    VALORES_DE_MOTIVO,
    VALORES_DE_RESULTADO,
)

# Bump this when the local schema changes shape. A file stamped with an unknown
# version is refused rather than migrated, because a prototype that silently
# rewrites a database it does not recognise is worse than one that stops.
VERSION_DE_ESQUEMA = 2

# The version SCRUM-64 created. It is the only one with a defined upgrade path.
VERSION_ANTERIOR = 1

# Version of a database SQLite has just created and nobody has stamped.
VERSION_SIN_ESTRENAR = 0

# ``UPDATE ... RETURNING`` is how an attempt's ordinal is obtained atomically
# together with its compare-and-set. It landed in SQLite 3.35.0 (March 2021).
# Checked on every connection so that an old interpreter fails with a sentence a
# person can act on, instead of a syntax error in the middle of a sync.
VERSION_MINIMA_DE_SQLITE = (3, 35, 0)

TABLA_CAPTURA = "captura_local"
TABLA_OUTBOX = "outbox"
TABLA_INTENTO = "intento_sincronizacion"

# The old table only exists under this name inside the upgrade transaction.
TABLA_MIGRACION_V1 = "outbox_migracion_v1"

INDICE_INTENTO_ABIERTO = "ux_intento_abierto"
INDICE_CONFIRMO_TRANSICION = "ux_confirmo_transicion"

# Rendered from the enums instead of written by hand, so a value cannot be added
# in Python and stay unrepresented in the constraint that limits the column.
_LISTA_DE_ESTADOS = ", ".join(f"'{valor}'" for valor in VALORES_DE_ESTADO)
_LISTA_DE_RESULTADOS = ", ".join(f"'{valor}'" for valor in VALORES_DE_RESULTADO)
_LISTA_DE_MOTIVOS = ", ".join(f"'{valor}'" for valor in VALORES_DE_MOTIVO)

DDL_CAPTURA = f"""
CREATE TABLE {TABLA_CAPTURA} (
    id_captura   INTEGER PRIMARY KEY,
    payload_json TEXT    NOT NULL,
    capturado_en TEXT    NOT NULL
)
"""

# The schema SCRUM-64 created. Kept verbatim, and used for exactly one thing:
# proving a file really is an untouched v1 before the upgrade rewrites it.
DDL_OUTBOX_V1 = f"""
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

# Columns of v1, in order, so the upgrade can copy them by name rather than by
# position -- a positional copy is how a column ends up in the wrong slot.
COLUMNAS_HEREDADAS: tuple[str, ...] = (
    "id_outbox",
    "id_captura",
    "clave_idempotencia",
    "estado",
    "reintentable",
    "intentos",
    "creado_en",
    "actualizado_en",
    "enviado_en",
    "ultimo_http",
    "ultimo_error",
    "id_sesion_remota",
    "ids_lectura_remotos",
)

# v2. Every v1 line is preserved character for character -- including
# ``evidencia_remota_coherente``, which needed no change once the confirmation
# timestamp was left in the attempt history instead of being copied here.
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
    proximo_intento_en  TEXT,
    motivo_revision     TEXT,
    intentos_heredados  INTEGER NOT NULL DEFAULT 0,
    max_intentos_aplicado INTEGER,
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
        ),
    CONSTRAINT motivo_revision_valido
        CHECK (motivo_revision IS NULL OR motivo_revision IN ({_LISTA_DE_MOTIVOS})),
    CONSTRAINT motivo_revision_solo_en_revision
        CHECK ((estado = 'FALLIDO' AND reintentable = 0)
               = (motivo_revision IS NOT NULL)),
    CONSTRAINT intentos_heredados_coherentes
        CHECK (intentos_heredados >= 0 AND intentos_heredados <= intentos),
    CONSTRAINT max_intentos_aplicado_valido
        CHECK (max_intentos_aplicado IS NULL OR max_intentos_aplicado >= 1),
    CONSTRAINT proximo_intento_solo_si_reintentable
        CHECK (proximo_intento_en IS NULL
               OR (estado = 'FALLIDO' AND reintentable = 1))
)
"""

DDL_INTENTO = f"""
CREATE TABLE {TABLA_INTENTO} (
    id_intento          INTEGER PRIMARY KEY,
    id_outbox           INTEGER NOT NULL REFERENCES {TABLA_OUTBOX}(id_outbox),
    numero              INTEGER NOT NULL,
    iniciado_en         TEXT    NOT NULL,
    reconciliable_en    TEXT    NOT NULL,
    finalizado_en       TEXT,
    reconciliado_en     TEXT,
    resultado           TEXT,
    codigo_http         INTEGER,
    reproducido         INTEGER,
    confirmo_transicion INTEGER NOT NULL DEFAULT 0,
    error               TEXT,
    demora_programada_s REAL,
    CONSTRAINT numero_unico_por_evento
        UNIQUE (id_outbox, numero),
    CONSTRAINT numero_positivo
        CHECK (numero >= 1),
    CONSTRAINT resultado_valido
        CHECK (resultado IS NULL OR resultado IN ({_LISTA_DE_RESULTADOS})),
    CONSTRAINT resultado_con_fin
        CHECK ((finalizado_en IS NULL) = (resultado IS NULL)),
    CONSTRAINT reproducido_binario
        CHECK (reproducido IS NULL OR reproducido IN (0, 1)),
    CONSTRAINT reproducido_solo_si_entregado
        CHECK (reproducido IS NULL OR resultado = 'ENTREGADO'),
    CONSTRAINT confirmo_transicion_binario
        CHECK (confirmo_transicion IN (0, 1)),
    CONSTRAINT confirmo_transicion_solo_si_entregado
        CHECK (confirmo_transicion = 0 OR resultado = 'ENTREGADO'),
    CONSTRAINT demora_no_negativa
        CHECK (demora_programada_s IS NULL OR demora_programada_s >= 0),
    CONSTRAINT ventana_posterior_al_inicio
        CHECK (reconciliable_en >= iniciado_en)
)
"""

# The lease. One open, unreconciled attempt per event, enforced by SQLite.
DDL_INDICE_ABIERTO = f"""
CREATE UNIQUE INDEX {INDICE_INTENTO_ABIERTO}
    ON {TABLA_INTENTO} (id_outbox)
 WHERE finalizado_en IS NULL AND reconciliado_en IS NULL
"""

# One attempt, and only one, applied the local transition to ENVIADO. Several
# attempts may legitimately be ENTREGADO; only one of them moved the row.
DDL_INDICE_CONFIRMO = f"""
CREATE UNIQUE INDEX {INDICE_CONFIRMO_TRANSICION}
    ON {TABLA_INTENTO} (id_outbox)
 WHERE confirmo_transicion = 1
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


class SoporteInsuficiente(ErrorDeAlmacenamiento):
    """The SQLite library underneath is older than this design needs."""


def exigir_soporte_de_returning() -> None:
    """Refuse an SQLite too old for ``UPDATE ... RETURNING``.

    The ordinal of an attempt is read back from the same statement that claims
    it, because a ``SELECT`` afterwards would make correctness depend on an
    argument about lock semantics rather than on one statement. That needs
    3.35.0. Failing here costs one tuple comparison and turns an
    ``OperationalError`` about syntax into a sentence naming the real problem.
    """
    if sqlite3.sqlite_version_info < VERSION_MINIMA_DE_SQLITE:
        minima = ".".join(str(parte) for parte in VERSION_MINIMA_DE_SQLITE)
        raise SoporteInsuficiente(
            f"El nodo edge necesita SQLite {minima} o superior para 'UPDATE ... "
            f"RETURNING'; esta instalacion tiene {sqlite3.sqlite_version}."
        )


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
    the conditional ``UPDATE``s in :mod:`app.edge.outbox` are for.
    """
    exigir_soporte_de_returning()
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


@dataclass(frozen=True)
class IndiceParcialEsperado:
    """One partial unique index, checked apart from the plain ones.

    Partial indexes need their own treatment for a reason worth stating:
    ``PRAGMA index_list`` reports ``ux_intento_abierto`` as a unique index over
    ``id_outbox``, and folding that into the set of unique column groups would
    make the verification assert that ``id_outbox`` is unique in the attempt
    table -- which is false, and would then be «verified» forever. So
    :func:`_unicos_reales` skips anything flagged ``partial`` and these are
    matched here instead, by name, uniqueness, columns and stored definition.
    """

    nombre: str
    tabla: str
    columnas: tuple[str, ...]
    ddl: str


ESQUEMA_ESPERADO_V1: tuple[TablaEsperada, ...] = (
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
        ddl=DDL_OUTBOX_V1,
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
        unicos=frozenset(
            {frozenset({"id_captura"}), frozenset({"clave_idempotencia"})}
        ),
        foraneas=(ForaneaEsperada("id_captura", TABLA_CAPTURA, "id_captura"),),
    ),
)

RESTRICCIONES_CHECK_ESPERADAS_V1: dict[str, tuple[str, ...]] = {
    TABLA_OUTBOX: (
        "estado_valido",
        "intentos_no_negativo",
        "reintentable_binario",
        "reintentable_solo_en_fallido",
        "evidencia_remota_coherente",
    ),
}

# These expectations are written out by hand rather than read back from a
# freshly created database, and that is the point: derived from the DDL they
# would agree with it by construction and prove nothing. Declared independently,
# a test can assert that the DDL satisfies them, and the two can disagree --
# which is what makes the check worth running.
ESQUEMA_ESPERADO: tuple[TablaEsperada, ...] = (
    ESQUEMA_ESPERADO_V1[0],
    TablaEsperada(
        nombre=TABLA_OUTBOX,
        ddl=DDL_OUTBOX,
        columnas=ESQUEMA_ESPERADO_V1[1].columnas
        + (
            ColumnaEsperada("proximo_intento_en", "TEXT", no_nula=False, es_pk=False),
            ColumnaEsperada("motivo_revision", "TEXT", no_nula=False, es_pk=False),
            ColumnaEsperada("intentos_heredados", "INTEGER", no_nula=True, es_pk=False),
            ColumnaEsperada(
                "max_intentos_aplicado", "INTEGER", no_nula=False, es_pk=False
            ),
        ),
        # One outbox row per capture, and one capture per key. The first is what
        # keeps a package from being queued twice; the second is what keeps two
        # different packages from sharing an Idempotency-Key locally, which the
        # server would answer with a 409.
        unicos=ESQUEMA_ESPERADO_V1[1].unicos,
        foraneas=ESQUEMA_ESPERADO_V1[1].foraneas,
    ),
    TablaEsperada(
        nombre=TABLA_INTENTO,
        ddl=DDL_INTENTO,
        columnas=(
            ColumnaEsperada("id_intento", "INTEGER", no_nula=False, es_pk=True),
            ColumnaEsperada("id_outbox", "INTEGER", no_nula=True, es_pk=False),
            ColumnaEsperada("numero", "INTEGER", no_nula=True, es_pk=False),
            ColumnaEsperada("iniciado_en", "TEXT", no_nula=True, es_pk=False),
            ColumnaEsperada("reconciliable_en", "TEXT", no_nula=True, es_pk=False),
            ColumnaEsperada("finalizado_en", "TEXT", no_nula=False, es_pk=False),
            ColumnaEsperada("reconciliado_en", "TEXT", no_nula=False, es_pk=False),
            ColumnaEsperada("resultado", "TEXT", no_nula=False, es_pk=False),
            ColumnaEsperada("codigo_http", "INTEGER", no_nula=False, es_pk=False),
            ColumnaEsperada("reproducido", "INTEGER", no_nula=False, es_pk=False),
            ColumnaEsperada(
                "confirmo_transicion", "INTEGER", no_nula=True, es_pk=False
            ),
            ColumnaEsperada("error", "TEXT", no_nula=False, es_pk=False),
            ColumnaEsperada("demora_programada_s", "REAL", no_nula=False, es_pk=False),
        ),
        # One row per ordinal per event. It is also the last line of defence for
        # the ordinal itself: a collision becomes an integrity error instead of
        # two attempts quietly sharing a number.
        unicos=frozenset({frozenset({"id_outbox", "numero"})}),
        foraneas=(ForaneaEsperada("id_outbox", TABLA_OUTBOX, "id_outbox"),),
    ),
)

# Constraint names the definition must still carry. Checked by name because
# ``PRAGMA`` does not expose CHECK constraints at all: the only place they exist
# to be inspected is the stored ``CREATE TABLE`` text.
RESTRICCIONES_CHECK_ESPERADAS: dict[str, tuple[str, ...]] = {
    TABLA_OUTBOX: RESTRICCIONES_CHECK_ESPERADAS_V1[TABLA_OUTBOX]
    + (
        "motivo_revision_valido",
        "motivo_revision_solo_en_revision",
        "intentos_heredados_coherentes",
        "max_intentos_aplicado_valido",
        "proximo_intento_solo_si_reintentable",
    ),
    TABLA_INTENTO: (
        "numero_unico_por_evento",
        "numero_positivo",
        "resultado_valido",
        "resultado_con_fin",
        "reproducido_binario",
        "reproducido_solo_si_entregado",
        "confirmo_transicion_binario",
        "confirmo_transicion_solo_si_entregado",
        "demora_no_negativa",
        "ventana_posterior_al_inicio",
    ),
}

INDICES_PARCIALES_ESPERADOS: tuple[IndiceParcialEsperado, ...] = (
    IndiceParcialEsperado(
        nombre=INDICE_INTENTO_ABIERTO,
        tabla=TABLA_INTENTO,
        columnas=("id_outbox",),
        ddl=DDL_INDICE_ABIERTO,
    ),
    IndiceParcialEsperado(
        nombre=INDICE_CONFIRMO_TRANSICION,
        tabla=TABLA_INTENTO,
        columnas=("id_outbox",),
        ddl=DDL_INDICE_CONFIRMO,
    ),
)


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


def _definicion_almacenada(
    conexion: sqlite3.Connection, nombre: str, tipo: str = "table"
) -> str | None:
    fila = conexion.execute(
        "SELECT sql FROM sqlite_master WHERE type = ? AND name = ?", (tipo, nombre)
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
    """Column sets SQLite really enforces as unique **over every row**.

    Partial indexes are skipped. They constrain a subset of the rows, so
    reporting them here would claim a uniqueness that does not hold in general
    -- see :class:`IndiceParcialEsperado`.
    """
    conjuntos = set()
    for indice in conexion.execute(f"PRAGMA index_list({tabla})").fetchall():
        if not indice["unique"] or indice["partial"]:
            continue
        columnas = conexion.execute(f"PRAGMA index_info({indice['name']})").fetchall()
        conjuntos.add(frozenset(columna["name"] for columna in columnas))
    return frozenset(conjuntos)


def _indice_real(conexion: sqlite3.Connection, tabla: str, nombre: str) -> dict | None:
    """One index of a table as ``PRAGMA index_list`` reports it."""
    for indice in conexion.execute(f"PRAGMA index_list({tabla})").fetchall():
        if indice["name"] == nombre:
            return dict(indice)
    return None


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


def verificar_esquema(
    conexion: sqlite3.Connection,
    *,
    esquema: tuple[TablaEsperada, ...] = ESQUEMA_ESPERADO,
    restricciones: dict[str, tuple[str, ...]] | None = None,
    indices: tuple[IndiceParcialEsperado, ...] = INDICES_PARCIALES_ESPERADOS,
) -> None:
    """Refuse anything that is not exactly the schema of this version.

    Names are the cheap half. What this insists on is the set of *guarantees*:
    every column with its type and its ``NOT NULL``, the primary keys, the
    unique constraints SQLite is really enforcing (read from its own indexes,
    not from the text), the foreign keys and where they point, the partial
    unique indexes, and finally the stored ``CREATE`` text -- which is the only
    place a ``CHECK`` constraint can be inspected, because no ``PRAGMA`` reports
    one.

    The structural checks run before the textual one so that a missing column or
    a dropped foreign key produces a message naming it, and the text comparison
    is left as the catch-all for everything else, a weakened ``CHECK`` above all.

    The keyword arguments exist for one caller: the upgrade, which has to prove a
    file really is an untouched v1 before rewriting it. Everything else uses the
    defaults and verifies the current version.

    Raises :class:`EsquemaIncompatible` on the first disagreement. Never writes.
    """
    if restricciones is None:
        restricciones = (
            RESTRICCIONES_CHECK_ESPERADAS
            if esquema is ESQUEMA_ESPERADO
            else RESTRICCIONES_CHECK_ESPERADAS_V1
        )

    presentes = tablas_presentes(conexion)

    for esperada in esquema:
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
        for restriccion in restricciones.get(esperada.nombre, ()):
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

    for indice in indices:
        real = _indice_real(conexion, indice.tabla, indice.nombre)
        if real is None:
            raise EsquemaIncompatible(
                f"Falta el indice '{indice.nombre}' sobre '{indice.tabla}'."
            )
        if not real["unique"] or not real["partial"]:
            raise EsquemaIncompatible(
                f"El indice '{indice.nombre}' dejo de ser un indice unico parcial."
            )
        columnas = tuple(
            fila["name"]
            for fila in conexion.execute(
                f"PRAGMA index_info({indice.nombre})"
            ).fetchall()
        )
        if columnas != indice.columnas:
            raise EsquemaIncompatible(
                f"El indice '{indice.nombre}' no cubre las columnas esperadas."
            )
        definicion = _definicion_almacenada(conexion, indice.nombre, tipo="index") or ""
        if _normalizar(definicion) != _normalizar(indice.ddl):
            raise EsquemaIncompatible(
                f"La definicion del indice '{indice.nombre}' difiere de la que "
                "esta version crea; su condicion parcial pudo cambiar."
            )

    sobrantes = presentes - {tabla.nombre for tabla in esquema}
    if sobrantes:
        raise EsquemaIncompatible(
            "El archivo contiene tablas ajenas a este almacenamiento: "
            f"{sorted(sobrantes)}."
        )


# ---------------------------------------------------------------------------
# Initialisation and upgrade
# ---------------------------------------------------------------------------


def _crear_esquema(conexion: sqlite3.Connection) -> None:
    """Every table and index of this version. Caller owns the transaction."""
    conexion.execute(DDL_CAPTURA)
    conexion.execute(DDL_OUTBOX)
    conexion.execute(DDL_INTENTO)
    conexion.execute(DDL_INDICE_ABIERTO)
    conexion.execute(DDL_INDICE_CONFIRMO)
    conexion.execute(f"PRAGMA user_version = {VERSION_DE_ESQUEMA}")


def _migrar_v1_a_v2(conexion: sqlite3.Connection) -> None:
    """Rebuild ``outbox`` with the v2 invariants, keeping every row.

    **The caller owns the transaction**, and it must be one: half of this would
    leave a file claiming a version it does not have.

    The old table is renamed out of the way and the new one is created *directly
    under its final name*. Doing it the other way round -- build ``outbox_v2``,
    rename it into place -- makes SQLite store ``CREATE TABLE "outbox" (...)``
    with quotes, which no longer matches the text this module writes, and
    :func:`verificar_esquema` would refuse every migrated file on the next run.

    What each new column is seeded with, and why:

    ``proximo_intento_en``
        ``NULL``. A retryable event inherited from SCRUM-64 is eligible right
        away; it was never scheduled, and inventing a schedule would delay it
        for no reason.
    ``motivo_revision``
        ``RECHAZO_PERMANENTE`` for the rows already in review, ``NULL``
        elsewhere. That is what a non-retryable failure meant in v1, so it is a
        translation and not a guess.
    ``intentos_heredados``
        ``= intentos``. v1 counted attempts but stored no history for them, and
        no ``iniciado_en`` or ``resultado`` can be reconstructed for something
        that was never recorded. Keeping the total and declaring how much of it
        has no detail is the only honest option: the counter is not reset, and
        the trace says «3 heredados, sin historial detallado» instead of
        fabricating three rows.
    ``max_intentos_aplicado``
        ``NULL``. The event has not adopted a policy yet; it will adopt one when
        it claims its first attempt under SCRUM-65.

    The attempt table is created **after** the swap so its foreign key points at
    the final ``outbox``, and the two partial indexes after that.
    """
    columnas = ", ".join(COLUMNAS_HEREDADAS)

    conexion.execute(f"ALTER TABLE {TABLA_OUTBOX} RENAME TO {TABLA_MIGRACION_V1}")
    conexion.execute(DDL_OUTBOX)
    conexion.execute(
        f"INSERT INTO {TABLA_OUTBOX} ({columnas}, proximo_intento_en,"
        "  motivo_revision, intentos_heredados, max_intentos_aplicado) "
        f"SELECT {columnas}, NULL,"
        "       CASE WHEN estado = 'FALLIDO' AND reintentable = 0"
        "            THEN 'RECHAZO_PERMANENTE' ELSE NULL END,"
        "       intentos, NULL "
        f"  FROM {TABLA_MIGRACION_V1}"
    )
    conexion.execute(f"DROP TABLE {TABLA_MIGRACION_V1}")
    conexion.execute(DDL_INTENTO)
    conexion.execute(DDL_INDICE_ABIERTO)
    conexion.execute(DDL_INDICE_CONFIRMO)
    conexion.execute(f"PRAGMA user_version = {VERSION_DE_ESQUEMA}")


def inicializar(conexion: sqlite3.Connection) -> bool:
    """Create the schema, upgrade it, or verify it. Refusing is a normal outcome.

    Idempotent. Seven situations, and only two of them write anything:

    ============================== ==========================================
    Estado del archivo             Accion
    ============================== ==========================================
    version 0, sin tablas          crear todo y estampar la version, atomico
    version 0, tablas propias      rechazar: esquema previo al versionado
    version 0, tablas ajenas       rechazar: el archivo es de otra base
    version 1 intacta              migrar a v2 sin perder filas, atomico
    version 1 divergente           rechazar: no se migra lo que no se reconoce
    version esperada, correcto     no hacer nada
    version distinta               rechazar: archivo de otra version
    ============================== ==========================================

    Returns ``True`` when it wrote to the file -- created or upgraded -- and
    ``False`` when it found the current version already in place, so a caller can
    tell the cases apart without inspecting the file itself.

    A v1 file is verified against the v1 expectations **before** it is touched.
    Migrating a file whose shape is not the one v1 produced would be rewriting a
    database nobody recognises, which is the thing this module refuses to do.
    """
    version = leer_version(conexion)
    presentes = tablas_presentes(conexion)

    if version == VERSION_SIN_ESTRENAR:
        if not presentes:
            with transaccion(conexion):
                _crear_esquema(conexion)
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

    if version == VERSION_ANTERIOR:
        verificar_esquema(
            conexion,
            esquema=ESQUEMA_ESPERADO_V1,
            restricciones=RESTRICCIONES_CHECK_ESPERADAS_V1,
            indices=(),
        )
        with transaccion(conexion):
            _migrar_v1_a_v2(conexion)
        verificar_esquema(conexion)
        return True

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
