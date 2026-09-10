"""The outbox and the attempt history: what is waiting, what happened, and when.

Every read and every state transition of the local store goes through here, and
the module follows the contract the rest of this repository established: it
``execute``s, and it **never commits and never rolls back**. The transaction
belongs to the caller -- the capture service for a write, the round for a claim
and a result, the reconciliation for a seal -- exactly as
``app.loader.postgres``, ``app.services.ingesta`` and ``app.services.idempotencia``
do on the server side.

**Why the transitions are conditional.** Two senders can run at the same time,
by mistake or by a test, and they can interleave in a way that is easy to miss:
sender A gets its 201 and marks the event ``ENVIADO``; sender B, which had been
waiting on a socket that eventually timed out, then writes ``FALLIDO`` on the
very same row. The package *was* delivered, PostgreSQL holds it exactly once,
and the local state now says it was not. Nothing about that is caught by a lock
timeout: it is not lock contention, it is two writers that both believe they are
right, one of them working from stale information.

So the guards travel **in the ``WHERE`` clause**, and SCRUM-65 adds a second one
next to the first::

    UPDATE outbox SET ... WHERE id_outbox = ? AND estado <> 'ENVIADO'
                                             AND NOT (estado = 'FALLIDO' AND reintentable = 0)

The first makes ``ENVIADO`` terminal in the database rather than by convention.
The second does the same for «requires review»: an exhausted or permanently
rejected event cannot be dragged back into the retry queue by a result that
arrives after the reconciliation already closed it, which is the interleaving
that would otherwise produce an attempt N+1. Both turn a race into a
compare-and-set, and ``rowcount`` answers a question the caller genuinely needs.

**One declaration of what «eligible» means.** :func:`predicado_elegible` renders
the clause, and the selection, the claim and the census all use it. Three
hand-written copies of a four-part condition is how one of them ends up missing a
pair of parentheses -- and with ``OR`` binding looser than ``AND``, a missing pair
would silently apply the attempt limit to only one of the two eligible states.

**The claim is the ordinal.** ``UPDATE ... RETURNING intentos`` performs the
compare-and-set and hands back the attempt's number in the same statement. The
alternative -- update, then ``SELECT`` the counter -- would be correct only
because ``BEGIN IMMEDIATE`` holds the write lock, which makes correctness an
argument about lock semantics instead of a property of one statement.

**The lease is a fact of the history, not a state.** «This event has an attempt
in flight» is expressed as «there is a row with no result and no reconciliation»,
which the partial unique index in :mod:`app.edge.almacenamiento` bounds to one
per event. There is still no ``EN_PROCESO``, no lock and no claim column.

All data handled here is fictitious and simulated.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.edge.estados import EstadoEntrega, MotivoRevision, ResultadoEntrega
from app.edge.politica import MAX_ATTEMPTS_POR_OMISION

# Truncation limit of the sanitised error kept with a failed event. Enough to
# tell one kind of failure from another when reading a summary, short enough
# that nothing long -- a body, a traceback, a statement -- could fit even if a
# caller tried to store one.
LONGITUD_MAXIMA_DE_ERROR = 200

# Texts this project writes for the two situations where there is no answer to
# report. They are stored **together with** ``ultimo_http = NULL``: the pair
# describes one attempt, and half of it left over from an earlier one -- a 503
# next to «the process was interrupted» -- would be a combination that never
# happened.
ERROR_SIN_RESULTADO = "intento sin resultado: proceso interrumpido"
ERROR_AGOTADO_SIN_RESULTADO = "agotado: el ultimo intento quedo sin resultado"


def ahora_utc() -> datetime:
    """Current instant in UTC. Injectable wherever a test needs determinism."""
    return datetime.now(timezone.utc)


def _texto(momento: datetime) -> str:
    """Instant as text, always in UTC, so ordering by it is ordering in time.

    Every timestamp in this file goes through here, which is what makes the
    lexicographic comparisons in the eligibility and reconciliation clauses
    chronological: one format, one offset, always ``+00:00``.
    """
    return momento.astimezone(timezone.utc).isoformat()


def _sumar(momento: datetime, segundos: float) -> datetime:
    return momento + timedelta(seconds=segundos)


# ---------------------------------------------------------------------------
# What «eligible» means. One declaration.
# ---------------------------------------------------------------------------

# The lease, as a correlated subquery.
#
# ``{p}`` must never be empty here, and that is not a style preference. The
# subquery's own table also has a column called ``id_outbox``, so an unqualified
# ``id_outbox`` resolves to the **inner** scope: the condition silently becomes
# ``i.id_outbox = i.id_outbox``, which is always true, and ``NOT EXISTS`` then
# answers «is there any open attempt at all, anywhere» instead of «does *this*
# event have one». Both callers therefore pass the outer table or its alias.
_INTENTOS_ABIERTOS_DEL_EVENTO = (
    " FROM intento_sincronizacion i"
    "  WHERE i.id_outbox = {p}id_outbox"
    "    AND i.finalizado_en IS NULL"
    "    AND i.reconciliado_en IS NULL"
)

_SIN_INTENTO_ABIERTO = "NOT EXISTS (SELECT 1" + _INTENTOS_ABIERTOS_DEL_EVENTO + ")"
_CON_INTENTO_ABIERTO = "EXISTS (SELECT 1" + _INTENTOS_ABIERTOS_DEL_EVENTO + ")"

# Vencimiento del lease mas proximo entre los intentos abiertos de un evento, o
# NULL si no tiene ninguno. Correlacionada como las anteriores, para que el censo
# pueda obtenerla en la misma sentencia que todo lo demas.
_PROXIMO_RECONCILIABLE = (
    "(SELECT MIN(i.reconciliable_en)" + _INTENTOS_ABIERTOS_DEL_EVENTO + ")"
)

# Qualifier used by the statements that do **not** declare an alias for the
# table -- the two ``UPDATE``s. SQLite would accept ``UPDATE outbox AS o``; these
# statements simply do not use one, so the outer reference inside the correlated
# subquery has to be spelled ``outbox.``.
PREFIJO_OUTBOX = "outbox."

# An event nobody is going to touch again automatically.
_TERMINAL = (
    "({p}estado = 'ENVIADO'"
    " OR ({p}estado = 'FALLIDO' AND {p}reintentable = 0))"
)


def predicado_elegible(prefijo: str, *, respetar_programacion: bool = True) -> str:
    """The clause that decides whether an event may be attempted right now.

    ``prefijo`` is the table qualifier: ``"o."`` inside the ``SELECT``, which
    aliases the table, and ``PREFIJO_OUTBOX`` inside the ``UPDATE``s, which do
    not declare an alias.

    The outer parentheses around the two eligible states are the whole point of
    rendering this in one place. ``AND`` binds tighter than ``OR``, so written
    without them the attempt limit and the schedule would apply only to the
    ``FALLIDO`` branch and a ``PENDIENTE`` could be retried without any limit at
    all.

    ``respetar_programacion=False`` drops the schedule clause and nothing else.
    That is what the explicit ``enviar`` command uses: a person forcing an
    attempt now should not be made to wait out a backoff, but the attempt limit
    is not negotiable -- a limit a command can step over is not a limit -- and
    neither is the lease, so a manual send still cannot compete with an attempt
    that is already in flight.
    """
    p = prefijo
    clausulas = [
        f"({p}estado = '{EstadoEntrega.PENDIENTE.value}'"
        f" OR ({p}estado = '{EstadoEntrega.FALLIDO.value}' AND {p}reintentable = 1))",
        f"{p}intentos < COALESCE({p}max_intentos_aplicado, :max_attempts)",
        _SIN_INTENTO_ABIERTO.format(p=p),
    ]
    if respetar_programacion:
        clausulas.append(
            f"({p}proximo_intento_en IS NULL OR {p}proximo_intento_en <= :ahora)"
        )
    return " AND ".join(clausulas)


def _guardas_de_transicion(prefijo: str = "") -> str:
    """``ENVIADO`` is terminal, and so is «requires review», for automatic paths."""
    p = prefijo
    return (
        f"{p}estado <> '{EstadoEntrega.ENVIADO.value}'"
        f" AND NOT ({p}estado = '{EstadoEntrega.FALLIDO.value}'"
        f"          AND {p}reintentable = 0)"
    )


# ---------------------------------------------------------------------------
# Values the callers exchange
# ---------------------------------------------------------------------------


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
    intentos_heredados: int
    max_intentos_aplicado: int | None


@dataclass(frozen=True)
class Reclamacion:
    """A claimed attempt: its ordinal, its row, and the policy it adopted."""

    id_intento: int
    numero: int
    max_intentos_aplicado: int


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


@dataclass(frozen=True)
class Censo:
    """The state of the queue, split the way the exit codes need it.

    ``ENVIADO`` and «requires review» are both terminal, and lumping them
    together as «terminal» is exactly the mistake that would let a run finish
    with exhausted events and still report success. They are counted apart.
    """

    elegibles_ahora: int
    programados: int
    proximo_programado: str | None
    con_intento_abierto: int
    proximo_reconciliable: str | None
    enviados: int
    requieren_revision: int
    bloqueados_por_configuracion: int
    total: int

    @property
    def hay_trabajo_futuro(self) -> bool:
        return self.programados > 0 or self.con_intento_abierto > 0


@dataclass(frozen=True)
class IntentoDeTraza:
    """One attempt as the trace shows it. Never a payload, never a header."""

    numero: int
    iniciado_en: str
    reconciliable_en: str
    finalizado_en: str | None
    reconciliado_en: str | None
    resultado: str | None
    codigo_http: int | None
    reproducido: int | None
    confirmo_transicion: int
    error: str | None
    demora_programada_s: float | None


@dataclass(frozen=True)
class Traza:
    """Everything known locally about one event, safe to print."""

    id_outbox: int
    correlation_id: str
    estado: str
    reintentable: int | None
    motivo_revision: str | None
    capturado_en: str
    intentos: int
    intentos_heredados: int
    max_intentos_aplicado: int | None
    proximo_intento_en: str | None
    enviado_en: str | None
    ultimo_http: int | None
    ultimo_error: str | None
    id_sesion_remota: int | None
    ids_lectura_remotos: str | None
    intentos_registrados: tuple[IntentoDeTraza, ...]

    @property
    def confirmado_en(self) -> str | None:
        """When the delivery was confirmed, derived from the attempt that did it.

        ``None`` for an event delivered before SCRUM-65: v1 stored no attempts,
        so the instant was never measured. The trace says so instead of reusing
        ``enviado_en``, which is a different clock reading of a different event.
        """
        for intento in self.intentos_registrados:
            if intento.confirmo_transicion == 1:
                return intento.finalizado_en
        return None

    @property
    def sincronizado_en(self) -> str | None:
        """Instant recorded for the local transition to ``ENVIADO``."""
        return self.enviado_en

    @property
    def reproducido(self) -> bool | None:
        for intento in self.intentos_registrados:
            if intento.confirmo_transicion == 1:
                return None if intento.reproducido is None else bool(intento.reproducido)
        return None

    @property
    def requiere_revision(self) -> bool:
        return self.estado == EstadoEntrega.FALLIDO.value and self.reintentable == 0


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


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

    The new row is ``PENDIENTE`` with ``reintentable`` null and no policy
    adopted: ``max_intentos_aplicado`` stays ``NULL`` until the event claims its
    first attempt, so a configuration change before that still reaches it.
    ``intentos_heredados`` is 0 and stays 0 for anything captured under v2.
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
        "    creado_en, actualizado_en, intentos_heredados"
        ") VALUES (?, ?, ?, NULL, 0, ?, ?, 0)",
        (id_captura, clave, EstadoEntrega.PENDIENTE.value, momento, momento),
    )
    return id_captura, int(cursor.lastrowid)


# ---------------------------------------------------------------------------
# Selection and claim
# ---------------------------------------------------------------------------


def seleccionar_elegibles(
    conexion: sqlite3.Connection,
    *,
    limite: int,
    max_attempts: int = MAX_ATTEMPTS_POR_OMISION,
    ahora: datetime | None = None,
    id_maximo: int | None = None,
    respetar_programacion: bool = True,
) -> tuple[EventoElegible, ...]:
    """Events a round may attempt, oldest first.

    Eligible means: never confirmed, not closed for review, still within its
    attempt limit, past its scheduled instant, and with no attempt already in
    flight. A ``FALLIDO`` that needs a person to look at it -- a 409, a rejected
    body, an exhausted retry budget -- is deliberately **not** selected:
    retrying it unchanged would ask the server the same question and get the
    same answer.

    ``id_maximo`` is the run's watermark. Restricting every read to it is what
    keeps a run finite even while another process keeps capturing: work that
    arrives after the run started belongs to the next one.

    ``ORDER BY id_outbox`` is the deterministic FIFO the ticket asks for, and it
    is the insertion order because the column is an alias of the rowid. The read
    is a single statement so the caller can let it finish and close before it
    touches the network: no SQLite lock is ever held across an HTTP call.
    """
    momento = _texto(ahora if ahora is not None else ahora_utc())
    parametros = {
        "max_attempts": int(max_attempts),
        "ahora": momento,
        "limite": int(limite),
        "id_maximo": _sin_tope(id_maximo),
    }

    filas = conexion.execute(
        "SELECT o.id_outbox, o.id_captura, o.clave_idempotencia, o.estado,"
        "       o.intentos, o.intentos_heredados, o.max_intentos_aplicado,"
        "       c.payload_json "
        "  FROM outbox o "
        "  JOIN captura_local c ON c.id_captura = o.id_captura "
        " WHERE o.id_outbox <= :id_maximo AND "
        + predicado_elegible("o.", respetar_programacion=respetar_programacion)
        + " ORDER BY o.id_outbox LIMIT :limite",
        parametros,
    ).fetchall()

    return tuple(
        EventoElegible(
            id_outbox=fila["id_outbox"],
            id_captura=fila["id_captura"],
            clave=fila["clave_idempotencia"],
            payload_json=fila["payload_json"],
            estado=EstadoEntrega(fila["estado"]),
            intentos=fila["intentos"],
            intentos_heredados=fila["intentos_heredados"],
            max_intentos_aplicado=fila["max_intentos_aplicado"],
        )
        for fila in filas
    )


def _sin_tope(id_maximo: int | None) -> int:
    """A watermark that excludes nothing, when the caller does not set one."""
    return (1 << 62) if id_maximo is None else int(id_maximo)


def reclamar_intento(
    conexion: sqlite3.Connection,
    id_outbox: int,
    *,
    max_attempts: int,
    duracion_lease: float,
    momento: datetime,
    respetar_programacion: bool = True,
) -> Reclamacion | None:
    """Take the next attempt of one event. **The caller owns the transaction.**

    Four things happen in one statement and they are inseparable: the policy is
    adopted if the event had none, the counter is incremented, the full
    eligibility clause is re-checked, and the resulting ordinal comes back. If
    another synchronizer got there first -- or the event stopped being eligible
    between the selection and here -- the ``UPDATE`` matches nothing, ``None``
    comes back, and no attempt row is written.

    Counting the attempt **when it starts** rather than when its result is saved
    is deliberate. A process that dies mid-request would otherwise leave no trace
    of having tried, and could repeat that forever without ever consuming its
    budget. The cost is that an attempt whose outcome is unknown still counts,
    which is the honest reading: it may well have reached the server.

    ``max_intentos_aplicado`` is frozen here and never changes again for this
    event, so lowering or raising the configured limit later cannot retroactively
    rewrite a policy an event is already living under.

    ``reconciliable_en`` is computed **now**, from the timeout in force **now**,
    and stored. Recomputing it later would let a configuration change make an
    attempt in flight look abandoned early, or delay its recovery for no reason.
    """
    instante = _texto(momento)
    fila = conexion.execute(
        "UPDATE outbox"
        "   SET max_intentos_aplicado ="
        "           COALESCE(max_intentos_aplicado, :max_attempts),"
        "       intentos = intentos + 1,"
        "       actualizado_en = :ahora"
        " WHERE id_outbox = :id_outbox AND "
        + predicado_elegible(
            PREFIJO_OUTBOX, respetar_programacion=respetar_programacion
        )
        + " RETURNING intentos, max_intentos_aplicado",
        {
            "id_outbox": int(id_outbox),
            "max_attempts": int(max_attempts),
            "ahora": instante,
        },
    ).fetchone()

    if fila is None:
        return None

    ordinal = int(fila["intentos"])
    cursor = conexion.execute(
        "INSERT INTO intento_sincronizacion"
        " (id_outbox, numero, iniciado_en, reconciliable_en)"
        " VALUES (?, ?, ?, ?)",
        (
            int(id_outbox),
            ordinal,
            instante,
            _texto(_sumar(momento, duracion_lease)),
        ),
    )
    return Reclamacion(
        id_intento=int(cursor.lastrowid),
        numero=ordinal,
        max_intentos_aplicado=int(fila["max_intentos_aplicado"]),
    )


# ---------------------------------------------------------------------------
# The attempt history
# ---------------------------------------------------------------------------


def finalizar_intento(
    conexion: sqlite3.Connection,
    id_intento: int,
    *,
    resultado: ResultadoEntrega,
    codigo_http: int | None,
    reproducido: bool | None,
    error: str,
    demora: float | None,
    momento: datetime,
) -> bool | None:
    """Record a real answer on one attempt. **The caller owns the transaction.**

    Returns ``True`` when the attempt had already been reconciled -- the answer
    is **late** -- ``False`` when it is the ordinary case, and ``None`` when
    nothing was written because the row was already finished.

    The only condition is ``finalizado_en IS NULL``, on purpose. If the process
    really received an answer, the history keeps it, even for an attempt the
    reconciliation had already given up on: discarding a real answer in memory
    would throw away the very evidence this work exists to produce. What the
    late flag then governs is whether the **outbox** may be touched, which is the
    caller's decision and not this function's.

    ``demora_programada_s`` is written with ``COALESCE`` so a delay the
    reconciliation already applied is not overwritten by one recomputed here.
    The wait that actually happened is the one that gets kept.

    ``None`` is the caller's signal to leave the outbox completely alone: with no
    history row backing it, a confirmation would have nothing to stand on.
    """
    fila = conexion.execute(
        "UPDATE intento_sincronizacion"
        "   SET finalizado_en = :ahora,"
        "       resultado = :resultado,"
        "       codigo_http = :codigo,"
        "       reproducido = :reproducido,"
        "       error = :error,"
        "       demora_programada_s = COALESCE(demora_programada_s, :demora)"
        " WHERE id_intento = :id_intento AND finalizado_en IS NULL"
        " RETURNING reconciliado_en",
        {
            "ahora": _texto(momento),
            "resultado": resultado.value,
            "codigo": None if codigo_http is None else int(codigo_http),
            "reproducido": None if reproducido is None else int(bool(reproducido)),
            "error": (error or None) and error[:LONGITUD_MAXIMA_DE_ERROR],
            "demora": None if demora is None else float(demora),
            "id_intento": int(id_intento),
        },
    ).fetchone()

    if fila is None:
        return None
    return fila["reconciliado_en"] is not None


def confirmar_transicion(conexion: sqlite3.Connection, id_intento: int) -> None:
    """Mark the attempt that actually moved the row to ``ENVIADO``.

    Called only when :func:`marcar_enviado` reports that it performed the
    transition, and in the same transaction. Several attempts of one event may
    end in ``ENTREGADO`` -- an initial acceptance and a replay, both true -- but
    only one of them moved the local row, and the partial unique index makes that
    a guarantee rather than a hope. It is also what lets the confirmation instant
    be derived from an attempt instead of copied onto the outbox, where no
    ``CHECK`` could keep two copies in step.
    """
    conexion.execute(
        "UPDATE intento_sincronizacion SET confirmo_transicion = 1"
        " WHERE id_intento = ?",
        (int(id_intento),),
    )


def anotar_demora(
    conexion: sqlite3.Connection, id_intento: int, demora: float
) -> None:
    """Record the wait scheduled after an attempt the reconciliation closed."""
    conexion.execute(
        "UPDATE intento_sincronizacion SET demora_programada_s = ?"
        " WHERE id_intento = ?",
        (float(demora), int(id_intento)),
    )


def intentos_abandonados(
    conexion: sqlite3.Connection, *, momento: datetime, id_maximo: int | None = None
) -> tuple[int, ...]:
    """Attempts whose lease expired and that nobody closed. **A preselection.**

    Nothing is decided from this list. It exists so the caller can iterate; each
    row is then re-checked with a compare-and-set inside its own transaction,
    because between this read and that write the original sender may perfectly
    well have recorded its result.

    It deliberately does **not** filter by the event's state. An attempt left
    open on an event that is already ``ENVIADO`` is a real situation -- a late
    success on an earlier attempt delivered it, and this one's process died --
    and it still has to be sealed. Leaving it open would keep it in the census
    forever, and the run would wait on an event that was delivered long ago.
    """
    filas = conexion.execute(
        "SELECT id_intento FROM intento_sincronizacion"
        " WHERE finalizado_en IS NULL"
        "   AND reconciliado_en IS NULL"
        "   AND reconciliable_en <= :ahora"
        "   AND id_outbox <= :id_maximo"
        " ORDER BY id_outbox, numero",
        {"ahora": _texto(momento), "id_maximo": _sin_tope(id_maximo)},
    ).fetchall()
    return tuple(int(fila["id_intento"]) for fila in filas)


def reclamar_reconciliacion(
    conexion: sqlite3.Connection, id_intento: int, *, momento: datetime
) -> tuple[int, int] | None:
    """Seal one abandoned attempt, atomically. **The caller owns the transaction.**

    Returns ``(id_outbox, numero)`` when this call sealed it, and ``None`` when
    somebody else finished or sealed it first -- in which case the caller must
    stop and touch nothing else.

    ``finalizado_en`` and ``resultado`` stay ``NULL`` forever. The attempt's
    outcome is genuinely unknown and is not invented; what is recorded is that we
    noticed, and when.
    """
    fila = conexion.execute(
        "UPDATE intento_sincronizacion SET reconciliado_en = :ahora"
        " WHERE id_intento = :id_intento"
        "   AND finalizado_en IS NULL"
        "   AND reconciliado_en IS NULL"
        " RETURNING id_outbox, numero",
        {"ahora": _texto(momento), "id_intento": int(id_intento)},
    ).fetchone()

    if fila is None:
        return None
    return int(fila["id_outbox"]), int(fila["numero"])


# ---------------------------------------------------------------------------
# Transitions of the outbox row
# ---------------------------------------------------------------------------


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
    the row was already ``ENVIADO`` -- another attempt got there first, which is
    a normal outcome and not a failure.

    This is the **one** transition that may override a review state. An event
    marked exhausted, or permanently rejected, whose late answer turns out to be
    a valid 201 really was delivered: PostgreSQL holds the session, and leaving
    the row saying otherwise would be a lie about a verified fact. So the guard
    here is only ``estado <> 'ENVIADO'``, and the review columns are cleared.
    """
    instante = _texto(momento)
    cursor = conexion.execute(
        "UPDATE outbox "
        "   SET estado = ?,"
        "       reintentable = NULL,"
        "       motivo_revision = NULL,"
        "       proximo_intento_en = NULL,"
        "       enviado_en = ?,"
        "       id_sesion_remota = ?,"
        "       ids_lectura_remotos = ?,"
        "       ultimo_http = ?,"
        "       ultimo_error = NULL,"
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
    motivo: MotivoRevision | None = None,
    proximo_intento_en: datetime | None = None,
    ordinal: int | None = None,
) -> bool:
    """Record a failed attempt. **The caller owns the transaction.**

    Returns ``True`` when this call performed the transition and ``False`` when
    a guard refused it. Two guards, and the second one is new in SCRUM-65:

    * ``estado <> 'ENVIADO'`` -- the interleaving where a sender that lost its
      answer would otherwise overwrite a delivery another attempt confirmed;
    * ``NOT (estado = 'FALLIDO' AND reintentable = 0)`` -- an exhausted or
      permanently rejected event cannot be dragged back into the retry queue by
      an answer that arrives after the reconciliation closed it. Without this,
      a late recoverable result would flip ``reintentable`` back to 1 and buy the
      event an attempt N+1 past its own limit.

    ``ordinal`` narrows the update to a row whose counter still matches the
    attempt being reported, which is what the reconciliation needs: an event that
    has since claimed a newer attempt must not be rewritten from an older one.

    ``ultimo_http`` and ``ultimo_error`` are written **together**, always, from
    the same attempt. A code left over from a previous attempt next to a message
    from this one would describe a result that never happened.

    ``motivo`` must be present exactly when ``reintentable`` is false -- the
    ``CHECK`` insists on it -- so the trace can tell an exhausted event from a
    rejected one without re-deriving it from a configuration that may have
    changed.
    """
    if reintentable and motivo is not None:
        raise ValueError("Un fallo reintentable no lleva motivo de revision.")
    if not reintentable and motivo is None:
        raise ValueError("Un fallo en revision tiene que declarar su motivo.")
    if reintentable is False and proximo_intento_en is not None:
        raise ValueError("Un fallo en revision no programa un siguiente intento.")

    instante = _texto(momento)
    condicion = " WHERE id_outbox = :id_outbox AND " + _guardas_de_transicion()
    if ordinal is not None:
        condicion += " AND intentos = :ordinal"

    cursor = conexion.execute(
        "UPDATE outbox "
        "   SET estado = :estado,"
        "       reintentable = :reintentable,"
        "       motivo_revision = :motivo,"
        "       proximo_intento_en = :proximo,"
        "       enviado_en = NULL,"
        "       id_sesion_remota = NULL,"
        "       ids_lectura_remotos = NULL,"
        "       ultimo_http = :codigo,"
        "       ultimo_error = :error,"
        "       actualizado_en = :ahora"
        + condicion,
        {
            "estado": EstadoEntrega.FALLIDO.value,
            "reintentable": 1 if reintentable else 0,
            "motivo": None if motivo is None else motivo.value,
            "proximo": (
                None if proximo_intento_en is None else _texto(proximo_intento_en)
            ),
            "codigo": None if codigo_http is None else int(codigo_http),
            "error": error[:LONGITUD_MAXIMA_DE_ERROR],
            "ahora": instante,
            "id_outbox": int(id_outbox),
            "ordinal": None if ordinal is None else int(ordinal),
        },
    )
    return cursor.rowcount == 1


def resolver_herencia_incompatible(
    conexion: sqlite3.Connection,
    *,
    max_attempts: int,
    momento: datetime,
    id_maximo: int | None = None,
) -> int:
    """Close events whose v1 attempts already exceed the policy they would adopt.

    **The caller owns the transaction.** Returns how many rows were closed.

    An event migrated from SCRUM-64 with four consumed attempts, meeting a
    configured limit of three, can never claim another attempt: the eligibility
    clause refuses it forever. Leaving it there would make it a permanent
    ``bloqueados`` entry that nothing ever resolves, which is not an answer.

    So it is resolved explicitly, **without creating an attempt**: the policy is
    adopted, the event is closed for review, and ``motivo_revision`` says
    exactly why -- ``AGOTAMIENTO_HEREDADO``, not the plain ``AGOTAMIENTO`` of an
    event that burned its budget under this version.

    ``ultimo_http`` and ``ultimo_error`` are **left untouched**. They are real
    evidence of a real v1 attempt, and the reason this row is being closed is not
    a result at all: it is a limit being adopted. Overwriting the diagnostic with
    a sentence about configuration would destroy the only clue the event has, and
    overwriting just one of the two would invent a pair that never existed.
    """
    cursor = conexion.execute(
        "UPDATE outbox"
        "   SET max_intentos_aplicado = :max_attempts,"
        "       estado = :fallido,"
        "       reintentable = 0,"
        "       motivo_revision = :motivo,"
        "       proximo_intento_en = NULL,"
        "       actualizado_en = :ahora"
        " WHERE id_outbox <= :id_maximo"
        "   AND max_intentos_aplicado IS NULL"
        "   AND intentos_heredados >= :max_attempts"
        "   AND " + _guardas_de_transicion()
        # ``PREFIJO_OUTBOX`` and not an empty prefix: inside the subquery an
        # unqualified ``id_outbox`` would bind to the attempt table and the
        # correlation would collapse. See ``_SIN_INTENTO_ABIERTO``.
        + "   AND " + _SIN_INTENTO_ABIERTO.format(p=PREFIJO_OUTBOX),
        {
            "max_attempts": int(max_attempts),
            "fallido": EstadoEntrega.FALLIDO.value,
            "motivo": MotivoRevision.AGOTAMIENTO_HEREDADO.value,
            "ahora": _texto(momento),
            "id_maximo": _sin_tope(id_maximo),
        },
    )
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


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


def censar(
    conexion: sqlite3.Connection,
    *,
    max_attempts: int,
    momento: datetime,
    id_maximo: int | None = None,
) -> Censo:
    """Classify the whole queue. Read-only, and the loop's only source of truth.

    Deciding what to do next from «what the last round selected» is wrong in a
    way that only shows after a restart: every retryable event may be scheduled
    for the future, the round selects nothing, and a run that should have waited
    would exit instead. So the loop asks the queue, not the round.

    **One statement, one snapshot.** This used to be two reads -- one over the
    outbox, one over the attempt table -- and the claim that a change between
    them «at worst costs one extra iteration» was wrong. The interleaving that
    breaks it is short and reachable: the first read sees an event whose only
    attempt is open, so it counts it as neither eligible nor scheduled; another
    process then closes that attempt and leaves the event ``FALLIDO`` with a
    future ``proximo_intento_en``; the second read finds no open attempts. The
    combined census reports **zero** of everything, ``_proximo_despertar``
    answers ``None``, and the run exits leaving a scheduled retry behind. Asking
    for everything in a single ``SELECT`` removes the window: SQLite evaluates
    one statement against one snapshot, so the two halves can no longer describe
    different instants.

    The counts over the attempt table therefore travel as correlated subqueries
    rather than as a second read. ``con_intento_abierto`` counts **events** with
    an open attempt, which is what the loop needs; the partial unique index bounds
    those to one per event, so it also equals the number of open attempts.

    Read-only: no ``BEGIN IMMEDIATE``, no write transaction, and nothing left
    open when it returns.
    """
    instante = _texto(momento)
    parametros = {
        "max_attempts": int(max_attempts),
        "ahora": instante,
        "id_maximo": _sin_tope(id_maximo),
    }

    elegible = predicado_elegible("o.")
    programado = (
        predicado_elegible("o.", respetar_programacion=False)
        + " AND o.proximo_intento_en > :ahora"
    )
    no_terminal = "NOT " + _TERMINAL.format(p="o.")

    con_abierto = _CON_INTENTO_ABIERTO.format(p="o.")
    proximo_reconciliable = _PROXIMO_RECONCILIABLE.format(p="o.")

    fila = conexion.execute(
        "SELECT"
        f"  SUM(CASE WHEN {elegible} THEN 1 ELSE 0 END) AS elegibles_ahora,"
        f"  SUM(CASE WHEN {programado} THEN 1 ELSE 0 END) AS programados,"
        f"  MIN(CASE WHEN {programado} THEN o.proximo_intento_en END)"
        "       AS proximo_programado,"
        # Un intento abierto solo es trabajo pendiente mientras su evento sigue
        # vivo: uno olvidado sobre un paquete ya entregado no debe mantener viva
        # una ejecucion.
        f"  SUM(CASE WHEN {no_terminal} AND {con_abierto} THEN 1 ELSE 0 END)"
        "       AS con_intento_abierto,"
        f"  MIN(CASE WHEN {no_terminal} THEN {proximo_reconciliable} END)"
        "       AS proximo_reconciliable,"
        "  SUM(CASE WHEN o.estado = 'ENVIADO' THEN 1 ELSE 0 END) AS enviados,"
        "  SUM(CASE WHEN o.estado = 'FALLIDO' AND o.reintentable = 0"
        "           THEN 1 ELSE 0 END) AS requieren_revision,"
        f"  SUM(CASE WHEN {no_terminal}"
        "            AND o.intentos >= COALESCE(o.max_intentos_aplicado,"
        "                                       :max_attempts)"
        "           THEN 1 ELSE 0 END) AS bloqueados,"
        "  COUNT(*) AS total "
        "  FROM outbox o WHERE o.id_outbox <= :id_maximo",
        parametros,
    ).fetchone()

    return Censo(
        elegibles_ahora=int(fila["elegibles_ahora"] or 0),
        programados=int(fila["programados"] or 0),
        proximo_programado=fila["proximo_programado"],
        con_intento_abierto=int(fila["con_intento_abierto"] or 0),
        proximo_reconciliable=fila["proximo_reconciliable"],
        enviados=int(fila["enviados"] or 0),
        requieren_revision=int(fila["requieren_revision"] or 0),
        bloqueados_por_configuracion=int(fila["bloqueados"] or 0),
        total=int(fila["total"] or 0),
    )


def marca_de_agua(conexion: sqlite3.Connection) -> int:
    """Highest ``id_outbox`` at the moment a run starts.

    Every read of that run is restricted to it, so packages captured while it is
    running belong to the next one. Without it a run could be kept alive
    indefinitely by a process that keeps capturing, and «the execution is finite»
    would depend on nobody else working.
    """
    fila = conexion.execute("SELECT COALESCE(MAX(id_outbox), 0) AS tope FROM outbox")
    return int(fila.fetchone()["tope"])


def resumen(conexion: sqlite3.Connection) -> ResumenOutbox:
    """Counts by state, and nothing that could identify a package.

    ``FALLIDO`` is split by ``reintentable`` because the two mean different
    things to whoever reads the summary: one will be attempted again by the next
    round, the other is waiting for a decision.
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


def leer_traza(
    conexion: sqlite3.Connection,
    *,
    clave: str | None = None,
    id_outbox: int | None = None,
) -> Traza | None:
    """Everything known locally about one event. Never a payload, never a secret.

    Selected by its correlation identifier -- which is the ``Idempotency-Key``,
    not a second identity -- or by its local row id. Returns ``None`` when
    nothing matches.

    What comes back carries counts, states, instants, sanitised error categories
    and the remote identifiers. It does not carry the package, a header, a URL, a
    statement or a stack trace, and there is no option to ask for them: a trace
    that could print clinical data would be a second way to leak it.
    """
    if (clave is None) == (id_outbox is None):
        raise ValueError("Indica exactamente una clave o un id_outbox.")

    if clave is not None:
        fila = conexion.execute(
            "SELECT o.*, c.capturado_en FROM outbox o"
            "  JOIN captura_local c ON c.id_captura = o.id_captura"
            " WHERE o.clave_idempotencia = ?",
            (clave,),
        ).fetchone()
    else:
        fila = conexion.execute(
            "SELECT o.*, c.capturado_en FROM outbox o"
            "  JOIN captura_local c ON c.id_captura = o.id_captura"
            " WHERE o.id_outbox = ?",
            (int(id_outbox),),
        ).fetchone()

    if fila is None:
        return None

    intentos = tuple(
        IntentoDeTraza(
            numero=registro["numero"],
            iniciado_en=registro["iniciado_en"],
            reconciliable_en=registro["reconciliable_en"],
            finalizado_en=registro["finalizado_en"],
            reconciliado_en=registro["reconciliado_en"],
            resultado=registro["resultado"],
            codigo_http=registro["codigo_http"],
            reproducido=registro["reproducido"],
            confirmo_transicion=registro["confirmo_transicion"],
            error=registro["error"],
            demora_programada_s=registro["demora_programada_s"],
        )
        for registro in conexion.execute(
            "SELECT * FROM intento_sincronizacion WHERE id_outbox = ?"
            " ORDER BY numero",
            (int(fila["id_outbox"]),),
        ).fetchall()
    )

    return Traza(
        id_outbox=fila["id_outbox"],
        correlation_id=fila["clave_idempotencia"],
        estado=fila["estado"],
        reintentable=fila["reintentable"],
        motivo_revision=fila["motivo_revision"],
        capturado_en=fila["capturado_en"],
        intentos=fila["intentos"],
        intentos_heredados=fila["intentos_heredados"],
        max_intentos_aplicado=fila["max_intentos_aplicado"],
        proximo_intento_en=fila["proximo_intento_en"],
        enviado_en=fila["enviado_en"],
        ultimo_http=fila["ultimo_http"],
        ultimo_error=fila["ultimo_error"],
        id_sesion_remota=fila["id_sesion_remota"],
        ids_lectura_remotos=fila["ids_lectura_remotos"],
        intentos_registrados=intentos,
    )
