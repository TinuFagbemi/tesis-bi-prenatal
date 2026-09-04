"""Idempotent insertion of the validated dataset into the operational schema.

The contract of this module is narrow on purpose:

* it never opens a transaction, never commits and never rolls back -- the caller
  owns the transaction and every error propagates untouched;
* it never creates the schema (``Base.metadata.create_all`` is not used here and
  must not be): the database is prepared with ``alembic upgrade head``;
* it never deletes, truncates, drops or overwrites anything. Rows that are
  already there and identical are left alone; rows that are there with different
  content stop the load.

The Alembic head is resolved dynamically from the migration scripts, so no
revision identifier is written into this code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Column, Table, func, insert, select, text, tuple_
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import ArgumentError

from app.loader.dataset import (
    ORDEN_DE_CARGA,
    TABLA_DE_LECTURAS,
    TABLA_DE_SESIONES,
    AmbienteNoPermitido,
    ConflictoDeDatos,
    DatasetNormalizado,
    ErrorDeCarga,
    EsquemaDesactualizado,
    MotorNoSoportado,
    clave_primaria,
    normalizar_fila,
    tabla_operacional,
)

# Environments the loader is allowed to write to. Anything else -- production
# above all -- is refused before a connection is even attempted.
AMBIENTES_PERMITIDOS = frozenset({"development", "test", "ci"})

MOTOR_REQUERIDO = "postgresql"

DIRECTORIO_BACKEND = Path(__file__).resolve().parents[2]
DIRECTORIO_ALEMBIC = DIRECTORIO_BACKEND / "alembic"

CREDENCIALES_REDACTADAS = "<credenciales>"

# A URL scheme, ``postgresql+psycopg://`` and friends. It survives redaction so
# an error still says which driver was involved.
_ESQUEMA_DE_URL = r"[A-Za-z][A-Za-z0-9+.\-]*://"

# One URL sitting inside free text: its scheme plus everything up to the end of
# the line or the start of the next URL, whichever comes first. Stopping at the
# next scheme is what keeps two URLs in the same message independent.
_URL_EMBEBIDA = re.compile(
    rf"(?P<esquema>{_ESQUEMA_DE_URL})(?P<resto>(?:(?!{_ESQUEMA_DE_URL})[^\r\n])*)"
)


def _redactar_url(coincidencia: re.Match[str]) -> str:
    """Hide the credential-bearing part of a single URL occurrence."""
    esquema = coincidencia.group("esquema")
    resto = coincidencia.group("resto")

    # No ``@`` means no userinfo section and nothing to hide, so a plain
    # ``https://sqlalche.me/e/20/e3q8`` back-reference stays readable.
    ultimo_arroba = resto.rfind("@")
    if ultimo_arroba == -1:
        return coincidencia.group(0)

    # Everything up to the *last* ``@`` is treated as credentials, because a
    # password may contain ``@`` and ``/`` unencoded and the earlier ``@`` would
    # then be part of it. The rest of that token goes too: in a truncated URL
    # what follows the last ``@`` is a password tail, not a host.
    fin = ultimo_arroba
    while fin < len(resto) and not resto[fin].isspace():
        fin += 1

    return f"{esquema}{CREDENCIALES_REDACTADAS}{resto[fin:]}"


def sanear_mensaje(mensaje: str) -> str:
    """Redact credentials from any URL embedded in ``mensaje``.

    The message is never assumed to be a parseable URL: drivers quote URLs
    inside free text, sometimes truncated or non canonical, so this scans for
    URL occurrences instead of parsing the whole string. Whenever one carries a
    userinfo section, everything between the scheme and the end of that section
    is replaced -- host, port and database included.

    Dropping the host too is deliberate, and it is why this is conservative
    rather than precise. Keeping it would mean deciding where the password
    ends, and that decision is not sound: ``user:p@ss@host`` and a truncated
    ``user:p@ss`` are indistinguishable, so any rule that preserves the tail
    leaks a password fragment in the second case. The dataset is fictitious,
    but a password never belongs in a log.
    """
    return _URL_EMBEBIDA.sub(_redactar_url, mensaje)


# ---------------------------------------------------------------------------
# Guardias previas a cualquier conexión
# ---------------------------------------------------------------------------


def verificar_ambiente(app_env: str) -> None:
    """Refuse to run outside a safe environment.

    Pure: it takes the configured value and opens nothing, so the CLI can call
    it before building an engine.
    """
    if app_env not in AMBIENTES_PERMITIDOS:
        raise AmbienteNoPermitido(
            f"APP_ENV='{app_env}' no es un ambiente donde este cargador pueda "
            f"escribir. Ambientes permitidos: {', '.join(sorted(AMBIENTES_PERMITIDOS))}. "
            "El dataset simulado nunca debe cargarse en producción."
        )


def verificar_url(url: str) -> None:
    """Refuse a target that is not PostgreSQL, without connecting to it.

    Checking the URL before the engine exists is what stops a mistyped SQLite
    target from creating a database file on disk before being rejected.
    The URL itself is never echoed back: it carries credentials.
    """
    try:
        analizada = make_url(url)
    except ArgumentError as error:
        raise MotorNoSoportado(
            f"La URL de conexión configurada no es válida: {sanear_mensaje(str(error))}"
        ) from error

    motor = analizada.get_backend_name()
    if motor != MOTOR_REQUERIDO:
        raise MotorNoSoportado(
            f"El cargador solo escribe en PostgreSQL, y la configuración apunta a "
            f"un motor '{motor}'. Revisa DATABASE_URL."
        )


# ---------------------------------------------------------------------------
# Preflight sobre la conexión
# ---------------------------------------------------------------------------


def verificar_dialecto(conexion: Connection) -> None:
    """Defensive re-check on the live connection.

    ``verificar_url`` already ran on the URL; this covers a connection built by
    someone else, which is exactly the case in the integration tests.
    """
    motor = conexion.dialect.name
    if motor != MOTOR_REQUERIDO:
        raise MotorNoSoportado(
            f"La conexión recibida es de tipo '{motor}' y este cargador solo "
            "opera contra PostgreSQL."
        )


def _config_alembic() -> Config:
    """Alembic ``Config`` that does not depend on the working directory."""
    config = Config()
    config.set_main_option("script_location", str(DIRECTORIO_ALEMBIC))
    return config


def obtener_head() -> str:
    """Current head of the migration scripts, read from Alembic itself."""
    head = ScriptDirectory.from_config(_config_alembic()).get_current_head()
    if head is None:
        raise EsquemaDesactualizado(
            "Alembic no declara ninguna revisión head; el repositorio de "
            "migraciones está vacío."
        )
    return head


def leer_revision_desplegada(conexion: Connection) -> str | None:
    """Revision stamped in the database, or ``None`` if it was never migrated.

    Alembic returns ``None`` when ``alembic_version`` does not exist, so this
    never creates the table and works on any backend.
    """
    return MigrationContext.configure(conexion).get_current_revision()


def verificar_revision(desplegada: str | None, head: str) -> None:
    """The deployed revision must be exactly the current head."""
    if desplegada is None:
        raise EsquemaDesactualizado(
            "La base no tiene tabla 'alembic_version': el esquema operacional "
            "todavía no fue desplegado. Ejecuta 'alembic upgrade head' desde "
            "backend/ antes de cargar el dataset. Este cargador no crea el esquema."
        )
    if desplegada != head:
        raise EsquemaDesactualizado(
            f"La base está en la revisión '{desplegada}' y las migraciones van por "
            f"'{head}'. Ejecuta 'alembic upgrade head' desde backend/ antes de "
            "cargar el dataset."
        )


def preflight(conexion: Connection) -> str:
    """Everything that must hold before a single row is written."""
    verificar_dialecto(conexion)
    head = obtener_head()
    verificar_revision(leer_revision_desplegada(conexion), head)
    return head


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResultadoTabla:
    """What one table contributed to the load."""

    tabla: str
    total: int
    insertados: int
    existentes: int


@dataclass(frozen=True)
class AjusteSecuencia:
    """What happened to one table's identity sequence."""

    tabla: str
    columna: str
    secuencia: str
    maximo: int
    proximo_antes: int
    proximo_despues: int
    ajustada: bool


@dataclass(frozen=True)
class ResultadoCarga:
    """Everything a run of the loader observed."""

    revision: str
    tablas: tuple[ResultadoTabla, ...]
    secuencias: tuple[AjusteSecuencia, ...]
    sesiones_verificadas: int
    lecturas_verificadas: int
    ruta: Path | None = None

    @property
    def insertados(self) -> int:
        return sum(tabla.insertados for tabla in self.tablas)

    @property
    def existentes(self) -> int:
        return sum(tabla.existentes for tabla in self.tablas)

    @property
    def total(self) -> int:
        return sum(tabla.total for tabla in self.tablas)

    @property
    def secuencias_ajustadas(self) -> int:
        return sum(1 for secuencia in self.secuencias if secuencia.ajustada)


def formatear_resumen(resultado: ResultadoCarga, *, confirmada: bool) -> str:
    """Human-readable summary.

    ``confirmada`` says whether the caller already committed; the loader itself
    never does, so it cannot work that out on its own.
    """
    origen = resultado.ruta if resultado.ruta is not None else "(dataset en memoria)"
    cierre = (
        "Carga completada en una única transacción (commit confirmado)."
        if confirmada
        else "Carga preparada dentro de la transacción del llamador (sin commit)."
    )
    return "\n".join(
        [
            f"Dataset procesado: {origen}",
            f"Dataset validado y base en la revisión de Alembic head: {resultado.revision}",
            f"Tablas procesadas: {len(resultado.tablas)}",
            f"Registros insertados: {resultado.insertados}",
            f"Registros existentes sin cambios: {resultado.existentes}",
            f"Registros del dataset verificados en la base: {resultado.total}",
            f"Sesiones verificadas: {resultado.sesiones_verificadas}",
            f"Lecturas verificadas: {resultado.lecturas_verificadas}",
            (
                f"Secuencias ajustadas: {resultado.secuencias_ajustadas} "
                f"de {len(resultado.secuencias)} evaluadas"
            ),
            "Sin duplicados: cada llave primaria del dataset aparece una sola vez.",
            cierre,
        ]
    )


# ---------------------------------------------------------------------------
# Comparación idempotente e inserción
# ---------------------------------------------------------------------------


def _criterio_de_pk(tabla: Table, filas: Sequence[dict[str, Any]]):
    """Restrict a query to exactly the primary keys the dataset carries."""
    columnas = list(tabla.primary_key.columns)
    nombres = [columna.name for columna in columnas]
    if len(columnas) == 1:
        return columnas[0].in_([fila[nombres[0]] for fila in filas])
    return tuple_(*columnas).in_(
        [tuple(fila[nombre] for nombre in nombres) for fila in filas]
    )


def _leer_existentes(
    conexion: Connection, tabla: Table, filas: Sequence[dict[str, Any]]
) -> dict[tuple, dict[str, Any]]:
    """Rows already in the database for the dataset's primary keys, normalised."""
    if not filas:
        return {}

    nombres = clave_primaria(tabla)
    encontradas = conexion.execute(
        select(tabla).where(_criterio_de_pk(tabla, filas))
    ).mappings()

    return {
        tuple(normalizada[nombre] for nombre in nombres): normalizada
        for normalizada in (
            normalizar_fila(tabla, dict(encontrada)) for encontrada in encontradas
        )
    }


def _cargar_tabla(
    conexion: Connection, nombre: str, filas: Sequence[dict[str, Any]]
) -> ResultadoTabla:
    """Insert what is missing, keep what is identical, stop on a real conflict."""
    tabla = tabla_operacional(nombre)
    nombres_pk = clave_primaria(tabla)
    existentes = _leer_existentes(conexion, tabla, filas)

    ausentes: list[dict[str, Any]] = []
    iguales = 0
    conflictos: list[tuple[tuple, list[str]]] = []

    for fila in filas:
        clave = tuple(fila[nombre_pk] for nombre_pk in nombres_pk)
        actual = existentes.get(clave)
        if actual is None:
            ausentes.append(fila)
            continue
        divergentes = sorted(
            columna for columna, valor in fila.items() if actual[columna] != valor
        )
        if divergentes:
            conflictos.append((clave, divergentes))
        else:
            iguales += 1

    if conflictos:
        clave, divergentes = conflictos[0]
        descripcion = ", ".join(
            f"{columna}={valor!r}" for columna, valor in zip(nombres_pk, clave)
        )
        # Only the names of the diverging fields are reported, never their values.
        raise ConflictoDeDatos(
            f"Conflicto en la tabla '{nombre}': la llave primaria ({descripcion}) "
            f"ya existe en la base con distinto contenido en el/los campo(s) "
            f"{divergentes}. Se aborta la carga sin sobrescribir "
            f"({len(conflictos)} fila(s) en conflicto en esta tabla)."
        )

    if ausentes:
        conexion.execute(insert(tabla), ausentes)

    return ResultadoTabla(
        tabla=nombre,
        total=len(filas),
        insertados=len(ausentes),
        existentes=iguales,
    )


def _contar_presentes(
    conexion: Connection, tabla: Table, filas: Sequence[dict[str, Any]]
) -> int:
    if not filas:
        return 0
    return conexion.execute(
        select(func.count())
        .select_from(tabla)
        .where(_criterio_de_pk(tabla, filas))
    ).scalar_one()


def _verificar_poscarga(
    conexion: Connection, dataset: DatasetNormalizado
) -> tuple[int, int]:
    """Confirm every dataset row is in the database, exactly once.

    The counts are restricted to the dataset's own primary keys: a global
    ``count(*)`` would be wrong on a development database that already holds
    other rows.
    """
    for nombre in ORDEN_DE_CARGA:
        tabla = tabla_operacional(nombre)
        filas = dataset[nombre]
        presentes = _contar_presentes(conexion, tabla, filas)
        if presentes != len(filas):
            raise ErrorDeCarga(
                f"Verificación poscarga fallida en '{nombre}': el dataset trae "
                f"{len(filas)} filas y la base reporta {presentes} para esas mismas "
                "llaves primarias."
            )

    return (
        _contar_presentes(
            conexion, tabla_operacional(TABLA_DE_SESIONES), dataset[TABLA_DE_SESIONES]
        ),
        _contar_presentes(
            conexion, tabla_operacional(TABLA_DE_LECTURAS), dataset[TABLA_DE_LECTURAS]
        ),
    )


# ---------------------------------------------------------------------------
# Secuencias
# ---------------------------------------------------------------------------


def secuencia_de_tabla(
    conexion: Connection, tabla: Table, columna: Column
) -> tuple[str, str] | None:
    """Schema and name of the sequence backing ``columna``, or ``None``.

    ``pg_get_serial_sequence`` is what decides: a composite primary key, or a
    primary key that is also a foreign key, owns no sequence and returns NULL.
    The table and column travel as bind parameters, never interpolated.
    """
    preparador = conexion.dialect.identifier_preparer
    encontrada = conexion.execute(
        text(
            "SELECT n.nspname, c.relname "
            "FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.oid = pg_get_serial_sequence(:tabla, :columna)::regclass"
        ),
        {"tabla": preparador.format_table(tabla), "columna": columna.name},
    ).one_or_none()

    return None if encontrada is None else (encontrada[0], encontrada[1])


def citar_secuencia(conexion: Connection, esquema: str, nombre: str) -> str:
    """Quote a sequence identifier with the dialect's own preparer.

    ``ALTER SEQUENCE`` is DDL and PostgreSQL accepts no parameters there, so the
    identifier has to be interpolated. It comes from the catalogue -- never from
    the dataset -- and is quoted by SQLAlchemy rather than by hand.
    """
    preparador = conexion.dialect.identifier_preparer
    return f"{preparador.quote_schema(esquema)}.{preparador.quote(nombre)}"


def leer_estado_de_secuencia(
    conexion: Connection, secuencia_citada: str
) -> tuple[int, bool]:
    """``(last_value, is_called)`` read without consuming the sequence."""
    fila = conexion.execute(
        text(f"SELECT last_value, is_called FROM {secuencia_citada}")
    ).one()
    return int(fila[0]), bool(fila[1])


def proximo_valor(last_value: int, is_called: bool) -> int:
    """What the next ``nextval`` would return, worked out without calling it."""
    return last_value + 1 if is_called else last_value


def ajustar_secuencias(
    conexion: Connection, tablas: Iterable[str]
) -> tuple[AjusteSecuencia, ...]:
    """Move each identity sequence past the explicit ids the dataset brought.

    Only simple autoincrement primary keys are considered, and a sequence that
    is already ahead is left alone: sequences move forward, never backwards.
    A table with no rows is skipped -- there is no maximum to align to.
    """
    ajustes: list[AjusteSecuencia] = []

    for nombre in tablas:
        tabla = tabla_operacional(nombre)
        columna = tabla.autoincrement_column
        if columna is None:
            continue

        ubicacion = secuencia_de_tabla(conexion, tabla, columna)
        if ubicacion is None:
            continue

        maximo = conexion.execute(select(func.max(tabla.c[columna.name]))).scalar()
        if maximo is None:
            continue

        secuencia = citar_secuencia(conexion, *ubicacion)
        last_value, is_called = leer_estado_de_secuencia(conexion, secuencia)
        antes = proximo_valor(last_value, is_called)
        objetivo = int(maximo) + 1

        if objetivo > antes:
            # The only interpolated value is an int() derived from max(): it
            # cannot carry anything but digits. ALTER SEQUENCE is transactional,
            # unlike setval(), so a rollback undoes this too.
            conexion.execute(
                text(f"ALTER SEQUENCE {secuencia} RESTART WITH {objetivo}")
            )
            despues, ajustada = objetivo, True
        else:
            despues, ajustada = antes, False

        ajustes.append(
            AjusteSecuencia(
                tabla=nombre,
                columna=columna.name,
                secuencia=secuencia,
                maximo=int(maximo),
                proximo_antes=antes,
                proximo_despues=despues,
                ajustada=ajustada,
            )
        )

    return tuple(ajustes)


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------


def cargar_dataset(
    conexion: Connection,
    dataset: DatasetNormalizado,
    *,
    revision: str,
    ruta: Path | None = None,
) -> ResultadoCarga:
    """Load a validated dataset through ``conexion``.

    The approved order is validation -> preflight -> transaction -> load, and
    the first two steps are the caller's responsibility:

    * ``dataset`` is what :func:`app.loader.dataset.validar_dataset` returns;
    * ``revision`` is what :func:`preflight` must have returned on a connection
      opened **outside** this transaction. That is a contract, not a guarantee
      enforced here: the value is only recorded in the result and never
      re-checked, so a caller that fabricates it skips the schema verification.
      Making the argument mandatory keeps the step from being omitted silently,
      and moving it to the caller is what keeps the Alembic check out of the
      write transaction.

    The only thing re-checked here is the dialect: it reads
    ``conexion.dialect`` in memory, issues no SQL and does not touch Alembic,
    and it protects the PostgreSQL-specific statements below from a connection
    built by someone else.

    This function does not commit and does not roll back, and it lets every
    error propagate: the caller owns the transaction. In the CLI that caller is
    ``engine.begin()``; in the tests it is a transaction that is always rolled
    back.
    """
    verificar_dialecto(conexion)

    tablas = tuple(
        _cargar_tabla(conexion, nombre, dataset[nombre]) for nombre in ORDEN_DE_CARGA
    )
    sesiones, lecturas = _verificar_poscarga(conexion, dataset)
    secuencias = ajustar_secuencias(conexion, ORDEN_DE_CARGA)

    return ResultadoCarga(
        revision=revision,
        tablas=tablas,
        secuencias=secuencias,
        sesiones_verificadas=sesiones,
        lecturas_verificadas=lecturas,
        ruta=ruta,
    )
