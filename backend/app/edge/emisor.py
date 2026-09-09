"""One explicit pass over the outbox: finite, ordered, and it always ends.

This is the whole of SCRUM-64's delivery mechanism, and what it deliberately is
**not** matters as much as what it is. There is no loop waiting for work, no
timer, no connectivity probe, no backoff and no retry policy: a pass is invoked,
it attempts a bounded number of events once each, and it returns. Whatever gives
the system liveness -- a scheduler that keeps calling this -- belongs to
SCRUM-65 and is not anticipated here.

What SCRUM-64 does demonstrate is the property underneath that scheduler: **a
pass can always be run again safely**. Every event carries the key it was born
with, so a second attempt at a package the server already stored is recognised
as a replay and changes nothing in PostgreSQL.

**No SQLite lock is ever held across the network.** The eligible events are read
in one statement that finishes before the first request goes out, and each
result is written back in its own short transaction. A pass that hangs on a
socket therefore blocks nobody: the file stays writable throughout.

**Why the pass stops at a transport failure.** A refused connection or a timeout
says the API is unreachable *right now*, and every remaining event in the pass
would meet the same wall -- with a timeout each, turning a command into a long
wait for a foregone conclusion. So a transport failure ends the pass, and the
summary says so. Every other outcome is a property of one package -- a 409, a
rejected body, a 500 -- and does not stop the others, because a single bad
package must never hide the queue behind it.

All data handled here is fictitious and simulated.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.edge import outbox
from app.edge.almacenamiento import transaccion
from app.edge.cliente import ClienteEdge, Entrega, ResultadoEntrega

# Events one invocation will attempt at most. A bound rather than "everything"
# so a pass is a predictable unit of work; a caller that wants more runs another
# pass, explicitly.
LIMITE_POR_OMISION = 50


@dataclass(frozen=True)
class ResumenPasada:
    """What one pass did. Counts only: no keys, no payloads, no identifiers."""

    seleccionados: int
    entregados: int
    reintentables: int
    rechazados: int
    ya_entregados: int
    detenida_por_transporte: bool

    @property
    def intentados(self) -> int:
        return self.entregados + self.reintentables + self.rechazados


def _registrar_resultado(
    conexion: sqlite3.Connection,
    evento: outbox.EventoElegible,
    entrega: Entrega,
    momento: datetime,
) -> bool:
    """Persist one outcome in its own short transaction.

    Returns whether the transition actually happened. ``False`` means the row
    was already ``ENVIADO`` -- another sender confirmed it while this one was on
    the wire -- and the guard inside the repository refused to move it. That is
    the interleaving this design exists to survive, and it is reported rather
    than hidden.
    """
    with transaccion(conexion):
        if entrega.entregado:
            return outbox.marcar_enviado(
                conexion,
                evento.id_outbox,
                id_sesion=entrega.id_sesion,
                ids_lectura=entrega.ids_lectura or (),
                codigo_http=entrega.codigo_http,
                momento=momento,
            )
        return outbox.marcar_fallido(
            conexion,
            evento.id_outbox,
            reintentable=entrega.resultado is ResultadoEntrega.REINTENTABLE,
            codigo_http=entrega.codigo_http,
            error=entrega.error,
            momento=momento,
        )


def ejecutar_pasada(
    conexion: sqlite3.Connection,
    cliente: ClienteEdge,
    *,
    limite: int = LIMITE_POR_OMISION,
    reloj: Callable[[], datetime] = outbox.ahora_utc,
) -> ResumenPasada:
    """Attempt every eligible event once, oldest first, then stop.

    The four steps per event are always the same: take the key and the body that
    were stored at capture time, send them unchanged, classify the answer, and
    write the outcome back under the conditional guard that keeps ``ENVIADO``
    terminal.

    Nothing here recomputes a key, re-serialises a payload or edits a package.
    That is not an omission: those three are exactly what would turn a safe
    replay into a 409.
    """
    elegibles = outbox.seleccionar_elegibles(conexion, limite=limite)

    entregados = reintentables = rechazados = ya_entregados = 0
    detenida = False

    for evento in elegibles:
        entrega = cliente.enviar(clave=evento.clave, payload_json=evento.payload_json)

        if _registrar_resultado(conexion, evento, entrega, reloj()):
            if entrega.entregado:
                entregados += 1
            elif entrega.resultado is ResultadoEntrega.REINTENTABLE:
                reintentables += 1
            else:
                rechazados += 1
        else:
            # The row was already ENVIADO. Whatever this attempt concluded, it
            # is not allowed to contradict a confirmed delivery.
            ya_entregados += 1

        # A transport failure carries no status code, and that absence is
        # precisely what distinguishes "the API answered badly" from "the API
        # could not be reached". Checked outside the branch above on purpose:
        # the API is unreachable regardless of what the local row turned out to
        # say, so the pass ends either way.
        if (
            entrega.resultado is ResultadoEntrega.REINTENTABLE
            and entrega.codigo_http is None
        ):
            detenida = True
            break

    return ResumenPasada(
        seleccionados=len(elegibles),
        entregados=entregados,
        reintentables=reintentables,
        rechazados=rechazados,
        ya_entregados=ya_entregados,
        detenida_por_transporte=detenida,
    )
