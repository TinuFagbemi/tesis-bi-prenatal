"""One round over the outbox: finite, ordered, and it always ends.

A round attempts a bounded number of eligible events **once each** and returns.
It is the unit SCRUM-64 called a pass, and it is still the whole of the explicit
``enviar`` command; SCRUM-65 does not replace it, it wraps it. What repeats a
round, waits between rounds and decides when to stop lives in
:mod:`app.edge.sincronizacion`, so that «try everything once» and «keep trying
until the policy says stop» stay two separate, separately testable things.

**No SQLite lock is ever held across the network.** The eligible events are read
in one statement that finishes before the first request goes out; the claim is
one short transaction; the result is another. A round that hangs on a socket
blocks nobody: the file stays writable throughout.

**Three transactions per event, and the middle one is not a transaction.**
Claim, send, record. The claim writes the counter, adopts the policy and opens
the attempt's history row; the send happens with nothing open; the record closes
the history row and moves the outbox. A process that dies between the first and
the third leaves an attempt with no result -- which is the truth, and which the
reconciliation later turns into either a scheduled retry or an exhaustion,
without inventing what the server did.

**Why a transport failure ends the round.** A refused connection or a timeout
says the API is unreachable *right now*, and every remaining event would meet the
same wall -- with a timeout each, turning a command into a long wait for a
foregone conclusion. So the round stops and reports until when the caller should
hold off. Every other outcome is a property of one package -- a 409, a rejected
body, a 500 -- and does not stop the others, because a single bad package must
never hide the queue behind it.

Returning ``pausa_hasta`` instead of just a flag is the difference between
protecting one round and protecting the queue. SCRUM-64 broke out of the loop and
the caller immediately started another pass over the other pending events, so
fifty events meant fifty consecutive connection failures. The instant travels
back with the summary and the synchronizer holds the whole run to it.

All data handled here is fictitious and simulated.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.edge import outbox
from app.edge.almacenamiento import transaccion
from app.edge.cliente import ClienteEdge, es_resultado_desconocido
from app.edge.estados import MotivoRevision, ResultadoEntrega
from app.edge.politica import BATCH_LIMIT_POR_OMISION, PoliticaDeReintentos

# Kept under its SCRUM-64 name because the CLI and the package's public surface
# already import it. It is the size of one **round**, never the total of a run:
# what bounds a run is the watermark, not this number.
LIMITE_POR_OMISION = BATCH_LIMIT_POR_OMISION


@dataclass(frozen=True)
class ResumenPasada:
    """What one round did. Counts only: no keys, no payloads, no identifiers."""

    seleccionados: int
    reclamados: int
    entregados: int
    reintentables: int
    rechazados: int
    agotados: int
    ya_entregados: int
    tardios_registrados: int
    anomalias: int
    detenida_por_transporte: bool
    pausa_hasta: datetime | None

    @property
    def intentados(self) -> int:
        return self.entregados + self.reintentables + self.rechazados + self.agotados


def ejecutar_pasada(
    conexion: sqlite3.Connection,
    cliente: ClienteEdge,
    *,
    limite: int | None = None,
    politica: PoliticaDeReintentos | None = None,
    reloj: Callable[[], datetime] = outbox.ahora_utc,
    id_maximo: int | None = None,
    respetar_programacion: bool = False,
) -> ResumenPasada:
    """Attempt every eligible event once, oldest first, then stop.

    The four steps per event are always the same: claim the attempt, take the key
    and the body that were stored at capture time, send them unchanged, and write
    the outcome back under the guards that keep ``ENVIADO`` and «requires review»
    terminal.

    Nothing here recomputes a key, re-serialises a payload or edits a package.
    That is not an omission: those three are exactly what would turn a safe
    replay into a 409.

    ``respetar_programacion`` defaults to ``False`` because this function is what
    the explicit ``enviar`` command runs, and a person forcing a send now should
    not have to wait out a backoff. The attempt limit still applies -- it is not
    a limit otherwise -- and so does the lease. The synchronizer passes ``True``.
    """
    politica = politica or PoliticaDeReintentos()
    tamano = politica.batch_limit if limite is None else limite

    elegibles = outbox.seleccionar_elegibles(
        conexion,
        limite=tamano,
        max_attempts=politica.max_attempts,
        ahora=reloj(),
        id_maximo=id_maximo,
        respetar_programacion=respetar_programacion,
    )

    reclamados = entregados = reintentables = rechazados = agotados = 0
    ya_entregados = tardios = anomalias = 0
    pausa_hasta: datetime | None = None
    detenida = False

    for evento in elegibles:
        momento = reloj()

        # --- 1. Reclamacion: contador, politica, ordinal y lease, atomicos ---
        with transaccion(conexion):
            reclamacion = outbox.reclamar_intento(
                conexion,
                evento.id_outbox,
                max_attempts=politica.max_attempts,
                duracion_lease=politica.duracion_del_lease,
                momento=momento,
                respetar_programacion=respetar_programacion,
            )

        if reclamacion is None:
            # Otro sincronizador se lo llevo, lo entrego, o el evento dejo de ser
            # elegible entre la seleccion y ahora. No se envia nada.
            ya_entregados += 1
            continue

        reclamados += 1

        # --- 2. Red. Sin transaccion, sin bloqueo ---
        entrega = cliente.enviar(
            clave=evento.clave, payload_json=evento.payload_json
        )

        # --- 3. Resultado ---
        momento = reloj()
        quedan = reclamacion.numero < reclamacion.max_intentos_aplicado
        demora = (
            politica.demora(reclamacion.numero)
            if entrega.resultado is ResultadoEntrega.REINTENTABLE and quedan
            else None
        )

        with transaccion(conexion):
            tardio = outbox.finalizar_intento(
                conexion,
                reclamacion.id_intento,
                resultado=entrega.resultado,
                codigo_http=entrega.codigo_http,
                reproducido=entrega.reproducido,
                error=entrega.error,
                demora=demora,
                momento=momento,
            )

            if tardio is None:
                # La fila ya estaba finalizada: no hay historial que sustente
                # ninguna transicion, asi que la outbox no se toca.
                anomalias += 1
            elif tardio and not entrega.entregado:
                # Resultado tardio que no es una entrega: queda registrado en el
                # historial y no altera un evento que la reconciliacion cerro.
                tardios += 1
            elif entrega.entregado:
                aplicado = outbox.marcar_enviado(
                    conexion,
                    evento.id_outbox,
                    id_sesion=entrega.id_sesion,
                    ids_lectura=entrega.ids_lectura or (),
                    codigo_http=entrega.codigo_http,
                    momento=momento,
                )
                if aplicado:
                    outbox.confirmar_transicion(conexion, reclamacion.id_intento)
                    entregados += 1
                else:
                    ya_entregados += 1
            elif entrega.resultado is ResultadoEntrega.RECHAZADO:
                aplicado = outbox.marcar_fallido(
                    conexion,
                    evento.id_outbox,
                    reintentable=False,
                    motivo=MotivoRevision.RECHAZO_PERMANENTE,
                    codigo_http=entrega.codigo_http,
                    error=entrega.error,
                    momento=momento,
                )
                rechazados += aplicado
                ya_entregados += not aplicado
            elif demora is not None:
                aplicado = outbox.marcar_fallido(
                    conexion,
                    evento.id_outbox,
                    reintentable=True,
                    codigo_http=entrega.codigo_http,
                    error=entrega.error,
                    momento=momento,
                    proximo_intento_en=momento + timedelta(seconds=demora),
                )
                reintentables += aplicado
                ya_entregados += not aplicado
            else:
                # Recuperable, pero este intento consumio el limite. Se cierra
                # aqui: no existe un intento N+1 automatico.
                aplicado = outbox.marcar_fallido(
                    conexion,
                    evento.id_outbox,
                    reintentable=False,
                    motivo=MotivoRevision.AGOTAMIENTO,
                    codigo_http=entrega.codigo_http,
                    error=entrega.error,
                    momento=momento,
                )
                agotados += aplicado
                ya_entregados += not aplicado

        # --- 4. Fin anticipado de la ronda ---
        #
        # Un fallo de transporte no lleva codigo, y esa ausencia es lo que
        # distingue "la API respondio mal" de "no se pudo llegar a la API". La
        # pausa se calcula con la misma formula, este el evento reprogramado o
        # agotado: habla de la API, no del evento.
        if es_resultado_desconocido(entrega):
            detenida = True
            pausa_hasta = momento + timedelta(
                seconds=politica.demora(reclamacion.numero)
            )
            break

    return ResumenPasada(
        seleccionados=len(elegibles),
        reclamados=reclamados,
        entregados=entregados,
        reintentables=reintentables,
        rechazados=rechazados,
        agotados=agotados,
        ya_entregados=ya_entregados,
        tardios_registrados=tardios,
        anomalias=anomalias,
        detenida_por_transporte=detenida,
        pausa_hasta=pausa_hasta,
    )
