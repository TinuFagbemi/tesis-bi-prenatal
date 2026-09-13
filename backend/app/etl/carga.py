"""Loading into the analytic schema: type 1 upserts and insert-only facts.

**Dimensions and bridge** are upserted by their primary key -- the operational
identifier. A row that already exists is updated only when at least one value
really differs (``IS DISTINCT FROM``, which also compares NULLs), so a second
run over the same source writes nothing and reports every row as unchanged.

**Facts are immutable, and they are inserted with a plain INSERT.** The
anti-join of the extraction selects exactly the readings the fact does not
hold, and the transaction lock makes the run the only writer. A primary key
that nevertheless already exists means one of those two guarantees failed: it
is an anomaly, the INSERT fails, and the whole run is rolled back. There is no
``ON CONFLICT DO NOTHING`` -- it would turn that anomaly into a silently skipped
row. A reading that changed after being loaded is not «fixed» either: the
reconciliation detects it and the run is rolled back.

Nothing is deleted and nothing is reloaded: there is no ``DELETE``, no
``TRUNCATE`` and no drop-and-recreate anywhere in this module.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from sqlalchemy import Table, literal_column, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from app.etl.extraccion import Fila
from app.etl.modelos import FactLecturaBiometrica
from app.etl.reglas import ErrorDeEtl

# Rows per statement. Fifteen columns times a thousand rows stays far below the
# 65,535 bind parameters a PostgreSQL statement accepts.
TAMANO_DE_LOTE = 1000


class CargaInconsistente(ErrorDeEtl):
    """The fact did not accept exactly the rows the anti-join selected."""


@dataclass(frozen=True)
class ResultadoUpsert:
    """What one table received in a run."""

    tabla: str
    total: int
    insertadas: int
    actualizadas: int

    @property
    def sin_cambios(self) -> int:
        return self.total - self.insertadas - self.actualizadas


def _lotes(filas: Sequence[Fila]) -> Iterator[Sequence[Fila]]:
    for inicio in range(0, len(filas), TAMANO_DE_LOTE):
        yield filas[inicio : inicio + TAMANO_DE_LOTE]


def upsert_tipo_1(
    conexion: Connection, tabla: Table, filas: Sequence[Fila]
) -> ResultadoUpsert:
    """Insert new keys and overwrite changed rows; leave identical rows alone.

    ``RETURNING (xmax = 0)`` tells the two outcomes apart: a freshly inserted
    row version has no deleting transaction, an updated one does. A row whose
    values did not change is not touched, so it is not returned at all.
    """
    if not filas:
        return ResultadoUpsert(tabla=tabla.name, total=0, insertadas=0, actualizadas=0)

    llave = [columna.name for columna in tabla.primary_key.columns]
    resto = [columna.name for columna in tabla.columns if columna.name not in llave]

    insertadas = actualizadas = 0
    for lote in _lotes(filas):
        sentencia = insert(tabla).values(list(lote))
        excluida = sentencia.excluded
        sentencia = sentencia.on_conflict_do_update(
            index_elements=llave,
            set_={nombre: excluida[nombre] for nombre in resto},
            where=tuple_(*(tabla.c[nombre] for nombre in resto)).is_distinct_from(
                tuple_(*(excluida[nombre] for nombre in resto))
            ),
        ).returning(literal_column("(xmax = 0)").label("insertada"))

        for (insertada,) in conexion.execute(sentencia):
            if insertada:
                insertadas += 1
            else:
                actualizadas += 1

    return ResultadoUpsert(
        tabla=tabla.name,
        total=len(filas),
        insertadas=insertadas,
        actualizadas=actualizadas,
    )


def insertar_hechos(conexion: Connection, hechos: Sequence[Fila]) -> int:
    """Insert the new fact rows with a plain INSERT; return how many went in.

    Any integrity violation -- a key that already exists, a dimension that is
    missing -- becomes :class:`CargaInconsistente`, and the caller's
    transaction is rolled back with everything else.
    """
    tabla = FactLecturaBiometrica.__table__
    insertadas = 0
    for lote in _lotes(hechos):
        sentencia = insert(tabla).values(list(lote)).returning(tabla.c.id_lectura)
        try:
            insertadas += len(conexion.execute(sentencia).all())
        except IntegrityError as error:
            sqlstate = getattr(error.orig, "sqlstate", None) or "desconocido"
            # The driver message would name the key and its values: only the
            # SQLSTATE is kept.
            raise CargaInconsistente(
                "El hecho rechazó una fila nueva por una restricción de integridad "
                f"(SQLSTATE {sqlstate}): una lectura seleccionada como nueva ya "
                "estaba cargada o no encontró su dimensión. Con un único escritor "
                "protegido por el candado es una anomalía: la ejecución completa "
                "se revierte."
            ) from error
    return insertadas
