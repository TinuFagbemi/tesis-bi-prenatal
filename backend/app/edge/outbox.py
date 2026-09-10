"""The outbox: what is waiting to be delivered, and what already was (SCRUM-64).

Every read and every state transition of the local store goes through here, and
the module follows the contract the rest of this repository established: it
``execute``s, and it **never commits and never rolls back**. The transaction
belongs to the caller -- the capture service for a write, the sender for an
update -- exactly as ``app.loader.postgres``, ``app.services.ingesta`` and
``app.services.idempotencia`` do on the server side.

**Why the transitions are conditional.** Two senders can run at the same time,
by mistake or by a test, and they can interleave in a way that is easy to miss:
sender A gets its 201 and marks the event ``ENVIADO``; sender B, which had been
waiting on a socket that eventually timed out, then writes ``FALLIDO`` on the
very same row. The package *was* delivered, PostgreSQL holds it exactly once,
and the local state now says it was not. Nothing about that is caught by a lock
timeout: it is not lock contention, it is two writers that both believe they are
right, one of them working from stale information.

So the guard travels **in the ``WHERE`` clause**::

    UPDATE outbox SET ... WHERE id_outbox = ? AND estado <> 'ENVIADO'

which makes ``ENVIADO`` terminal in the database rather than by convention, and
turns the race into a compare-and-set. ``rowcount`` then answers a question the
caller genuinely needs: ``0`` means «somebody else already delivered this», which
is not an error and is reported as such. This is also why there is no
``EN_PROCESO``, no lease and no lock: they would be machinery for a problem two
words in a ``WHERE`` clause already solve.

For the same reason ``intentos`` is incremented as ``intentos = intentos + 1``
in SQL rather than read into Python and written back: a read-modify-write across
two statements is how counters lose increments.

All data handled here is fictitious and simulated.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from app.edge.estados import EstadoEntrega

# Truncation limit of the sanitised error kept with a failed event. Enough to
# tell one kind of failure from another when reading a summary, short enough
# that nothing long -- a body, a traceback, a statement -- could fit even if a
# caller tried to store one.
LONGITUD_MAXIMA_DE_ERROR = 200


def ahora_utc() -> datetime:
    """Current instant in UTC. Injectable wherever a test needs determinism."""
    return datetime.now(timezone.utc)


def _texto(momento: datetime) -> str:
    """Instant as text, always in UTC, so ordering by it is ordering in time."""
    return momento.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class EventoElegible:
    """One package the sender may attempt, with everything a retry needs.

    ``payload_json`` and ``clave`` come straight out of the rows written at
    capture time and are never recomputed: that is the whole reason a resend can
    be recognised by the server as the same package.
    """

    id_outbox: int
    id_captura: int
    clave: str
    payload_json: str
    estado: EstadoEntrega
    intentos: int


@dataclass(frozen=True)
class ResumenOutbox:
    """Counts by state. No payload, no keys, nothing clinical."""

    pendientes: int
    enviados: int
    fallidos_reintentables: int
    fallidos_en_revision: int

    @property
    def total(self) -> int:
        return (
            self.pendientes
            + self.enviados
            + self.fallidos_reintentables
            + self.fallidos_en_revision
        )


def registrar(
    conexion: sqlite3.Connection,
    *,
    payload_json: str,
    clave: str,
    capturado_en: datetime,
) -> tuple[int, int]:
    """Write the capture and its outbox row. **The caller owns the transaction.**

    Returns ``(id_captura, id_outbox)``. Two statements, and they are only ever
    meaningful together: a capture with no outbox row would never be sent, and
    an outbox row with no capture has nothing to send. Whoever calls this wraps
    it in :func:`app.edge.almacenamiento.transaccion`, so a failure between the
    two leaves neither.

    The new row is ``PENDIENTE`` with ``reintentable`` null -- the constraint
    ``reintentable_solo_en_fallido`` insists on that -- and with no remote
    evidence, which ``evidencia_remota_coherente`` insists on too.
    """
    momento = _texto(capturado_en)

    cursor = conexion.execute(
        "INSERT INTO captura_local (payload_json, capturado_en) VALUES (?, ?)",
        (payload_json, momento),
    )
    id_captura = int(cursor.lastrowid)

    cursor = conexion.execute(
        "INSERT INTO outbox ("
        "    id_captura, clave_idempotencia, estado, reintentable, intentos,"
        "    creado_en, actualizado_en"
        ") VALUES (?, ?, ?, NULL, 0, ?, ?)",
        (id_captura, clave, EstadoEntrega.PENDIENTE.value, momento, momento),
    )
    return id_captura, int(cursor.lastrowid)


def seleccionar_elegibles(
    conexion: sqlite3.Connection, *, limite: int
) -> tuple[EventoElegible, ...]:
    """Events a pass may attempt, oldest first.

    Eligible means never confirmed: still ``PENDIENTE``, or ``FALLIDO`` with the
    failure marked retryable. A ``FALLIDO`` that needs a person to look at it --
    a 409, a rejected body -- is deliberately **not** selected: retrying it
    unchanged would ask the server the same question and get the same answer.

    ``ORDER BY id_outbox`` is the deterministic FIFO the ticket asks for, and it
    is the insertion order because the column is an alias of the rowid. The read
    is a single statement so the caller can let it finish and close before it
    touches the network: no SQLite lock is ever held across an HTTP call.
    """
    filas = conexion.execute(
        "SELECT o.id_outbox, o.id_captura, o.clave_idempotencia, o.estado,"
        "       o.intentos, c.payload_json "
        "  FROM outbox o "
        "  JOIN captura_local c ON c.id_captura = o.id_captura "
        " WHERE o.estado = ? "
        "    OR (o.estado = ? AND o.reintentable = 1) "
        " ORDER BY o.id_outbox "
        " LIMIT ?",
        (EstadoEntrega.PENDIENTE.value, EstadoEntrega.FALLIDO.value, int(limite)),
    ).fetchall()

    return tuple(
        EventoElegible(
            id_outbox=fila["id_outbox"],
            id_captura=fila["id_captura"],
            clave=fila["clave_idempotencia"],
            payload_json=fila["payload_json"],
            estado=EstadoEntrega(fila["estado"]),
            intentos=fila["intentos"],
        )
        for fila in filas
    )


def marcar_enviado(
    conexion: sqlite3.Connection,
    id_outbox: int,
    *,
    id_sesion: int,
    ids_lectura: tuple[int, ...],
    codigo_http: int,
    momento: datetime,
) -> bool:
    """Record a confirmed delivery. **The caller owns the transaction.**

    Returns ``True`` when this call performed the transition and ``False`` when
    the row was already ``ENVIADO`` -- another sender got there first, which is
    a normal outcome and not a failure.

    The guard ``estado <> 'ENVIADO'`` is redundant on this path and kept anyway:
    writing the same guard on both transitions is what makes «``ENVIADO`` is
    terminal» a property of the statements rather than of the order in which
    they happen to be called.
    """
    instante = _texto(momento)
    cursor = conexion.execute(
        "UPDATE outbox "
        "   SET estado = ?,"
        "       reintentable = NULL,"
        "       enviado_en = ?,"
        "       id_sesion_remota = ?,"
        "       ids_lectura_remotos = ?,"
        "       ultimo_http = ?,"
        "       ultimo_error = NULL,"
        "       intentos = intentos + 1,"
        "       actualizado_en = ? "
        " WHERE id_outbox = ? AND estado <> ?",
        (
            EstadoEntrega.ENVIADO.value,
            instante,
            int(id_sesion),
            json.dumps(list(ids_lectura)),
            int(codigo_http),
            instante,
            int(id_outbox),
            EstadoEntrega.ENVIADO.value,
        ),
    )
    return cursor.rowcount == 1


def marcar_fallido(
    conexion: sqlite3.Connection,
    id_outbox: int,
    *,
    reintentable: bool,
    codigo_http: int | None,
    error: str,
    momento: datetime,
) -> bool:
    """Record a failed attempt. **The caller owns the transaction.**

    Returns ``True`` when this call performed the transition and ``False`` when
    the row was already ``ENVIADO``. The second case is the one that matters: it
    is exactly the interleaving where a sender that lost its answer would
    otherwise overwrite a delivery another sender had already confirmed. The
    guard makes that impossible, so a late failure is dropped instead of
    destroying the truth.

    The remote evidence columns are set to ``NULL`` explicitly rather than left
    alone. They are already null on every path that reaches here, and saying so
    in the statement means the row satisfies ``evidencia_remota_coherente`` by
    construction instead of by argument.

    ``error`` is stored truncated and is expected to be already sanitised by
    :mod:`app.edge.cliente`: no payload, no credentials, no raw server body.
    """
    instante = _texto(momento)
    cursor = conexion.execute(
        "UPDATE outbox "
        "   SET estado = ?,"
        "       reintentable = ?,"
        "       enviado_en = NULL,"
        "       id_sesion_remota = NULL,"
        "       ids_lectura_remotos = NULL,"
        "       ultimo_http = ?,"
        "       ultimo_error = ?,"
        "       intentos = intentos + 1,"
        "       actualizado_en = ? "
        " WHERE id_outbox = ? AND estado <> ?",
        (
            EstadoEntrega.FALLIDO.value,
            1 if reintentable else 0,
            None if codigo_http is None else int(codigo_http),
            error[:LONGITUD_MAXIMA_DE_ERROR],
            instante,
            int(id_outbox),
            EstadoEntrega.ENVIADO.value,
        ),
    )
    return cursor.rowcount == 1


def leer_evento(conexion: sqlite3.Connection, id_outbox: int) -> sqlite3.Row | None:
    """One outbox row as stored. Used by the CLI summary and by the tests."""
    return conexion.execute(
        "SELECT * FROM outbox WHERE id_outbox = ?", (int(id_outbox),)
    ).fetchone()


def leer_payload(conexion: sqlite3.Connection, id_captura: int) -> str | None:
    """The stored package of one capture, exactly as it was written."""
    fila = conexion.execute(
        "SELECT payload_json FROM captura_local WHERE id_captura = ?",
        (int(id_captura),),
    ).fetchone()
    return None if fila is None else fila["payload_json"]


def resumen(conexion: sqlite3.Connection) -> ResumenOutbox:
    """Counts by state, and nothing that could identify a package.

    ``FALLIDO`` is split by ``reintentable`` because the two mean different
    things to whoever reads the summary: one will be attempted again by the next
    pass, the other is waiting for a decision.
    """
    filas = conexion.execute(
        "SELECT estado, reintentable, count(*) AS n FROM outbox "
        "GROUP BY estado, reintentable"
    ).fetchall()

    conteos = {
        EstadoEntrega.PENDIENTE.value: 0,
        EstadoEntrega.ENVIADO.value: 0,
    }
    reintentables = 0
    en_revision = 0

    for fila in filas:
        if fila["estado"] == EstadoEntrega.FALLIDO.value:
            if fila["reintentable"] == 1:
                reintentables += fila["n"]
            else:
                en_revision += fila["n"]
        else:
            conteos[fila["estado"]] = conteos.get(fila["estado"], 0) + fila["n"]

    return ResumenOutbox(
        pendientes=conteos[EstadoEntrega.PENDIENTE.value],
        enviados=conteos[EstadoEntrega.ENVIADO.value],
        fallidos_reintentables=reintentables,
        fallidos_en_revision=en_revision,
    )
