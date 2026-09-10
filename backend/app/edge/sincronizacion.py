"""Deferred synchronisation: retries, backoff, reconciliation and an exit code.

This is what SCRUM-64 deliberately left out. A round attempts everything once;
this module is what runs rounds until the policy says stop, waits between them,
and repairs what a process that died halfway left behind.

**The loop asks the queue, not the last round.** Deciding to stop because «the
round selected nothing» is wrong in a way that only shows after a restart: with
every retryable event scheduled for the future, the round selects nothing, and a
run that should have waited would exit instead, leaving the queue untouched. So
each iteration takes a census -- eligible now, scheduled, with an attempt open,
delivered, awaiting review, blocked -- and chooses from that.

**Three outcomes per iteration, and only one of them counts against the guard.**
A durable modification resets it. A **positive** wait resets it too: sleeping is
normal temporal progress, not stagnation, and counting it would kill a run whose
ceiling is one second while its lease is seventy. Only an iteration that modified
nothing, claimed nothing and did not wait increments the counter, and four of
those in a row mean something is inconsistent, not that there is work pending.

**A transport failure pauses the whole run, not one round.** The round reports
until when; the pause lives here, in memory, for this execution. While it holds,
no round starts and therefore no request goes out, however many events are
eligible -- otherwise fifty pending packages against a down API would mean fifty
consecutive connection failures, one per round, which is exactly the behaviour
SCRUM-64's early break existed to prevent. It is memory and not a column on
purpose: it is what *this process* learned about the API a moment ago, not a
durable fact about any event, and persisting it would create global state that
outlives the run and needs its own invalidation.

The pause never gates the reconciliation. That part is pure SQLite, and it has to
keep running during a pause so a lease can expire and be sealed.

**A watermark keeps a run finite.** The highest ``id_outbox`` at the start bounds
every read, so packages captured while the run is going belong to the next one.
Without it «the execution is finite» would depend on nobody else working.

Termination, then, rests on two monotonic facts: a round consumes at least one
attempt and the attempt budget within the watermark is finite and decreasing, and
a wait always advances the clock to an instant the census computed as strictly
future. The inert-iteration guard is a third, defensive line -- finiteness should
be a property of the code, not only of an argument about it.

All data handled here is fictitious and simulated.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.edge import outbox
from app.edge.almacenamiento import transaccion
from app.edge.cliente import ClienteEdge
from app.edge.emisor import ejecutar_pasada
from app.edge.estados import EstadoEntrega, MotivoRevision
from app.edge.politica import PoliticaDeReintentos

# Exit codes. They describe the **state of the queue** when the run ended, not
# how many errors were seen along the way: a run that retried, recovered and
# delivered everything succeeded, however noisy it was.
CODIGO_EXITO = 0
CODIGO_ERROR = 1
CODIGO_REVISION = 2
CODIGO_ANOMALIA = 3

# Consecutive iterations that modify nothing, claim nothing and do not wait,
# before the run gives up and reports an internal inconsistency. Normal pending
# work never reaches this: it always produces either a claim or a positive wait.
COTA_INERTE = 3


@dataclass(frozen=True)
class ResumenSincronizacion:
    """What a whole run did. Counts and instants; never a key or a payload."""

    id_maximo: int
    rondas: int
    esperas: int
    segundos_esperados: float
    pausas_por_transporte: int
    intentos_reconciliados: int
    herencias_resueltas: int
    entregados: int
    reintentables: int
    rechazados: int
    agotados: int
    ya_entregados: int
    tardios_registrados: int
    anomalias: int
    censo: outbox.Censo
    codigo_de_salida: int


def _instante(texto: str | None) -> datetime | None:
    """Parse one of our own timestamps back. Always UTC, always ISO-8601."""
    return None if texto is None else datetime.fromisoformat(texto)


# ---------------------------------------------------------------------------
# Repair, before deciding anything
# ---------------------------------------------------------------------------


def reconciliar_abandonados(
    conexion: sqlite3.Connection,
    *,
    politica: PoliticaDeReintentos,
    momento: datetime,
    id_maximo: int | None = None,
) -> int:
    """Close attempts whose lease expired and whose process never came back.

    Returns how many were sealed.

    The list from :func:`app.edge.outbox.intentos_abandonados` is **only a
    preselection**. Nothing is decided from it: each attempt is re-checked with a
    compare-and-set inside its own transaction, because between the read and the
    write the original sender may perfectly well have recorded its real result.
    When the compare-and-set matches nothing, this stops and touches neither the
    attempt nor the event.

    Only when it matches is the outbox row **re-read inside the same
    transaction** and its fate decided from those values -- never from the ones
    the preselection saw. And the update carries the ordinal, so an event that
    has since claimed a newer attempt cannot be rewritten from an older one.

    Four outcomes, and two of them touch nothing but the attempt:

    * the event is already ``ENVIADO`` -- a late success on another attempt
      delivered it -- so the attempt is sealed and the row is left alone;
    * the event is already closed for review, same;
    * the counter has moved on, same;
    * otherwise the attempt consumed either the last of the budget, and the event
      is exhausted, or not, and the next attempt is scheduled with the ordinary
      formula.

    In no case is a result invented. ``finalizado_en`` and ``resultado`` stay
    ``NULL`` forever on a sealed attempt: what the edge knows is that it tried and
    never learned the outcome, and the trace says exactly that.
    """
    sellados = 0

    for id_intento in outbox.intentos_abandonados(
        conexion, momento=momento, id_maximo=id_maximo
    ):
        with transaccion(conexion):
            reclamada = outbox.reclamar_reconciliacion(
                conexion, id_intento, momento=momento
            )
            if reclamada is None:
                continue

            sellados += 1
            id_outbox, numero = reclamada

            fila = outbox.leer_evento(conexion, id_outbox)
            if fila is None:
                continue
            if fila["estado"] == EstadoEntrega.ENVIADO.value:
                continue
            if fila["estado"] == EstadoEntrega.FALLIDO.value and (
                fila["reintentable"] == 0
            ):
                continue
            if fila["intentos"] != numero:
                continue

            limite = politica.limite_de(fila["max_intentos_aplicado"])

            if numero >= limite:
                outbox.marcar_fallido(
                    conexion,
                    id_outbox,
                    reintentable=False,
                    motivo=MotivoRevision.AGOTAMIENTO,
                    codigo_http=None,
                    error=outbox.ERROR_AGOTADO_SIN_RESULTADO,
                    momento=momento,
                    ordinal=numero,
                )
            else:
                demora = politica.demora(numero)
                aplicado = outbox.marcar_fallido(
                    conexion,
                    id_outbox,
                    reintentable=True,
                    codigo_http=None,
                    error=outbox.ERROR_SIN_RESULTADO,
                    momento=momento,
                    proximo_intento_en=momento + timedelta(seconds=demora),
                    ordinal=numero,
                )
                if aplicado:
                    outbox.anotar_demora(conexion, id_intento, demora)

    return sellados


def resolver_herencia(
    conexion: sqlite3.Connection,
    *,
    politica: PoliticaDeReintentos,
    momento: datetime,
    id_maximo: int | None = None,
) -> int:
    """Close migrated events whose v1 attempts already meet the adopted limit."""
    with transaccion(conexion):
        return outbox.resolver_herencia_incompatible(
            conexion,
            max_attempts=politica.max_attempts,
            momento=momento,
            id_maximo=id_maximo,
        )


# ---------------------------------------------------------------------------
# When to wake up
# ---------------------------------------------------------------------------


def _proximo_despertar(
    censo: outbox.Censo, pausa: datetime | None
) -> datetime | None:
    """Earliest instant at which anything could change. ``None`` means never.

    Three candidates, and the pause only counts while there is work it is
    holding back -- waiting out a pause with nothing to send would keep a
    finished run alive for no reason.
    """
    candidatos = [
        _instante(censo.proximo_programado),
        _instante(censo.proximo_reconciliable),
    ]
    if pausa is not None and censo.elegibles_ahora > 0:
        candidatos.append(pausa)

    reales = [instante for instante in candidatos if instante is not None]
    return min(reales) if reales else None


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def sincronizar(
    conexion: sqlite3.Connection,
    cliente: ClienteEdge,
    *,
    politica: PoliticaDeReintentos | None = None,
    reloj: Callable[[], datetime] = outbox.ahora_utc,
    dormir: Callable[[float], None] = time.sleep,
    id_maximo: int | None = None,
) -> ResumenSincronizacion:
    """Run rounds, wait, repair, and stop. Finite by construction.

    ``reloj`` and ``dormir`` are injected so a test can observe the exact
    sequence of waits without spending them. Production passes the real clock and
    :func:`time.sleep`, which is the only place this package sleeps and it sits
    behind that boundary, inside a command that ends.
    """
    politica = politica or PoliticaDeReintentos()
    tope = outbox.marca_de_agua(conexion) if id_maximo is None else int(id_maximo)

    pausa: datetime | None = None
    inertes = 0
    codigo: int | None = None

    rondas = esperas = pausas = 0
    segundos = 0.0
    sellados = herencias = 0
    entregados = reintentables = rechazados = agotados = 0
    ya_entregados = tardios = anomalias = 0

    censo = outbox.censar(
        conexion,
        max_attempts=politica.max_attempts,
        momento=reloj(),
        id_maximo=tope,
    )

    while True:
        modifico = False
        espero = False

        # --- A. Saneamiento durable. Nunca hace HTTP: corre incluso en pausa ---
        ahora = reloj()
        cerrados = reconciliar_abandonados(
            conexion, politica=politica, momento=ahora, id_maximo=tope
        )
        adoptadas = resolver_herencia(
            conexion, politica=politica, momento=ahora, id_maximo=tope
        )
        sellados += cerrados
        herencias += adoptadas
        modifico = bool(cerrados or adoptadas)

        # --- B. Censo ---
        censo = outbox.censar(
            conexion,
            max_attempts=politica.max_attempts,
            momento=reloj(),
            id_maximo=tope,
        )
        en_pausa = pausa is not None and reloj() < pausa

        # --- C. Trabajo inmediato, salvo pausa global por transporte ---
        if censo.elegibles_ahora > 0 and not en_pausa:
            ronda = ejecutar_pasada(
                conexion,
                cliente,
                politica=politica,
                reloj=reloj,
                id_maximo=tope,
                respetar_programacion=True,
            )
            rondas += 1
            entregados += ronda.entregados
            reintentables += ronda.reintentables
            rechazados += ronda.rechazados
            agotados += ronda.agotados
            ya_entregados += ronda.ya_entregados
            tardios += ronda.tardios_registrados
            anomalias += ronda.anomalias
            modifico = modifico or ronda.reclamados > 0

            if ronda.pausa_hasta is not None:
                pausa = (
                    ronda.pausa_hasta
                    if pausa is None
                    else max(pausa, ronda.pausa_hasta)
                )
                pausas += 1

        # --- D. Espera ---
        else:
            despertar = _proximo_despertar(censo, pausa)
            if despertar is None:
                break

            espera = max(
                0.0,
                min(
                    (despertar - reloj()).total_seconds(),
                    politica.max_delay_seconds,
                ),
            )
            if espera > 0:
                dormir(espera)
                espero = True
                esperas += 1
                segundos += espera

        # --- E. Unico punto de contabilidad de la guarda ---
        if modifico or espero:
            inertes = 0
        else:
            inertes += 1
            if inertes > COTA_INERTE:
                codigo = CODIGO_ANOMALIA
                break

    censo = outbox.censar(
        conexion,
        max_attempts=politica.max_attempts,
        momento=reloj(),
        id_maximo=tope,
    )

    if codigo is None:
        necesita_persona = (
            censo.requieren_revision > 0 or censo.bloqueados_por_configuracion > 0
        )
        codigo = CODIGO_REVISION if necesita_persona else CODIGO_EXITO

    return ResumenSincronizacion(
        id_maximo=tope,
        rondas=rondas,
        esperas=esperas,
        segundos_esperados=segundos,
        pausas_por_transporte=pausas,
        intentos_reconciliados=sellados,
        herencias_resueltas=herencias,
        entregados=entregados,
        reintentables=reintentables,
        rechazados=rechazados,
        agotados=agotados,
        ya_entregados=ya_entregados,
        tardios_registrados=tardios,
        anomalias=anomalias,
        censo=censo,
        codigo_de_salida=codigo,
    )
