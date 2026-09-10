"""Talking to the ingestion endpoint, and deciding what its answer meant.

The hard part of this module is not the request -- it is one POST -- but the
classification of what comes back. Three outcomes exist locally, and every
possible answer has to map onto exactly one of them:

``ENTREGADO``
    The API confirmed the package. The event becomes ``ENVIADO``.
``REINTENTABLE``
    The outcome is unknown or the server said it failed on its side. The event
    stays available for another **explicit** pass, with the same key and the
    same body.
``RECHAZADO``
    The server refused the package, or answered something this client cannot
    accept as a confirmation. A person has to look at it; another identical
    attempt would only produce the same answer.

**A 201 is not enough on its own.** Five conditions have to hold together, and
the fifth is the one that is easy to forget:

1. the status is exactly 201 -- no other 2xx is a documented success here;
2. ``Idempotency-Replayed`` is present and is exactly ``false`` or ``true``;
3. the body validates as ``SesionMonitoreoCreada``;
4. ``len(ids_lectura) == lecturas_creadas`` -- the answer agrees with itself;
5. ``lecturas_creadas`` equals the number of readings **in the package that was
   sent**. Without this, a server that stored a different package under our key
   could be accepted as our confirmation.

Condition 5 is checked against the stored payload rather than against a counter
column, on the same reasoning that kept ``lecturas_creadas`` out of the server's
``idempotencia_solicitud``: a second copy of a number can only ever end up
disagreeing with the first. Derived at the moment of use, it cannot drift.

**The retryable family is exactly 5xx (SCRUM-65).** ``408`` and ``429`` are
**not** retried: this endpoint implements neither request timeouts nor rate
limiting, so treating them as recoverable would add a path no test could
exercise against the real server, and would raise the question of honouring
``Retry-After``, which is not implemented. It is a decision, recorded in the
decision log, not an omission. A code at or below 499 is refused, a code at or
above 600 is not a member of any documented family and is refused too.

**A replay is a success, and a 409 never is.** ``201`` with
``Idempotency-Replayed: true`` means the package is in PostgreSQL exactly once
and these are the identifiers it got; that is delivery. ``409`` means the key
names something else, and it is **not** retried and **not** confirmed -- the
event is kept for review with its key and its payload intact.

**Why a 409 is never diagnosed further.** The endpoint answers 409 for two
different situations: an idempotency collision, and a reference that vanished
mid-processing. There is no machine-readable error code that separates them, and
the only thing that differs is Spanish prose. Parsing that prose would make this
client break the day somebody improves a sentence, so both are treated the same
conservative way: keep everything, confirm nothing, ask for a human.

**What is stored about a failure.** Never the request body, never a URL, never a
raw server payload. A ``detail`` written by the API is safe text this project
wrote and is kept truncated; a ``detail`` that is a *list* -- what FastAPI
produces for an automatic 422 -- is deliberately **not** stored at all, because
its entries carry an ``input`` field that echoes the value that was rejected,
and for this contract that value is clinical data.

All data handled here is fictitious and simulated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError

from app.edge.estados import ResultadoEntrega
from app.schemas.monitoreo import SesionMonitoreoCreada

# Route and headers of the contract merged in SCRUM-63. They are written here
# rather than imported from ``app.api.v1.sesiones`` so the edge does not pull in
# the FastAPI application -- and its database engine -- just to name a string. A
# test asserts they are identical to the server's own constants, so the
# decoupling cannot turn into a drift.
RUTA_SESIONES = "/api/v1/sesiones-monitoreo"
CABECERA_IDEMPOTENCIA = "Idempotency-Key"
CABECERA_REPLAY = "Idempotency-Replayed"
REPLAY_SI = "true"
REPLAY_NO = "false"

CODIGO_CREADO = 201

# Truncation of any server-provided text before it is stored. The endpoint's own
# messages are shorter than this; the bound exists so that nothing unexpected
# can arrive and be kept whole.
LONGITUD_MAXIMA_DE_DETALLE = 160


# ``ResultadoEntrega`` is declared in :mod:`app.edge.estados` and re-exported
# here, where every caller already looks for it. The declaration had to move so
# that the storage module -- which renders a CHECK constraint from this
# vocabulary -- could read it without importing httpx and the Pydantic
# contracts. One declaration, two readers.
__all__ = [
    "CABECERA_IDEMPOTENCIA",
    "CABECERA_REPLAY",
    "CODIGO_CREADO",
    "ClienteEdge",
    "Entrega",
    "ResultadoEntrega",
    "RUTA_SESIONES",
    "clasificar",
    "contar_lecturas",
    "es_resultado_desconocido",
]


@dataclass(frozen=True)
class Entrega:
    """The outcome of one attempt, already classified and already sanitised."""

    resultado: ResultadoEntrega
    codigo_http: int | None = None
    reproducido: bool | None = None
    id_sesion: int | None = None
    ids_lectura: tuple[int, ...] | None = None
    error: str = ""

    @property
    def entregado(self) -> bool:
        return self.resultado is ResultadoEntrega.ENTREGADO


def es_resultado_desconocido(entrega: "Entrega") -> bool:
    """Whether this attempt ended without any answer from the server at all.

    A transport failure carries no status code, and that absence is the only
    thing that separates «the API answered badly» from «the API was never
    reached». Both are ``REINTENTABLE``, and they must stay one member: the edge
    does not know whether the request was committed, and a fourth member would
    claim it did.

    The distinction is given a name here because three callers need it -- the
    round, which ends early when the API is unreachable; the pause that keeps
    the rest of the queue from hitting the same wall; and the trace -- and three
    copies of ``codigo_http is None`` is how one of them ends up drifting.
    """
    return (
        entrega.resultado is ResultadoEntrega.REINTENTABLE
        and entrega.codigo_http is None
    )


def contar_lecturas(payload_json: str) -> int:
    """How many readings the stored package carries.

    Raises :class:`ValueError` when the stored text is not a package this client
    can read at all -- a corrupted or truncated file. That is a local fault, and
    the sender turns it into a rejection rather than putting an unreadable body
    on the wire.
    """
    try:
        paquete = json.loads(payload_json)
        lecturas = paquete["lecturas"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError("El paquete almacenado localmente no es legible.") from error
    if not isinstance(lecturas, list):
        raise ValueError("El paquete almacenado localmente no es legible.")
    return len(lecturas)


def _detalle_seguro(respuesta: httpx.Response) -> str:
    """A short, safe summary of an error answer.

    A string ``detail`` is text the API authored for a caller to read, and it is
    kept truncated. Anything else -- notably the list FastAPI builds for an
    automatic 422, whose entries echo the rejected input -- is reduced to its
    shape, never its content.
    """
    try:
        cuerpo: Any = respuesta.json()
    except ValueError:
        return "respuesta sin cuerpo JSON"

    if not isinstance(cuerpo, dict) or "detail" not in cuerpo:
        return "respuesta sin campo 'detail'"

    detalle = cuerpo["detail"]
    if isinstance(detalle, str):
        return detalle[:LONGITUD_MAXIMA_DE_DETALLE]
    if isinstance(detalle, list):
        return f"detalle estructurado con {len(detalle)} entrada(s) (no se registra)"
    return "detalle no textual (no se registra)"


def clasificar(respuesta: httpx.Response, *, lecturas_enviadas: int) -> Entrega:
    """Turn one HTTP answer into one local outcome.

    Pure: it reads a response and returns a verdict, opens nothing and writes
    nothing, so every branch below is reachable in a test with a fabricated
    response.
    """
    codigo = respuesta.status_code

    if codigo == CODIGO_CREADO:
        return _clasificar_creado(respuesta, lecturas_enviadas=lecturas_enviadas)

    if codigo == httpx.codes.CONFLICT:
        # Collision or a reference lost in a race -- indistinguishable by
        # contract. Neither is retried automatically and neither is a delivery.
        return Entrega(
            resultado=ResultadoEntrega.RECHAZADO,
            codigo_http=codigo,
            error=f"conflicto 409: {_detalle_seguro(respuesta)}",
        )

    if 500 <= codigo < 600:
        # A server-side failure. Kept eligible for another explicit pass, with
        # the same key -- never labelled "temporary", because the endpoint also
        # answers 500 for an incomplete claim, which is not transient at all.
        #
        # The upper bound is not decoration. The contract promises retries for
        # the **5xx family**, and a non-standard 600 or 999 is not a member of
        # it: nothing about such a code promises that trying again would help,
        # so it falls through to the catch-all below and is refused.
        return Entrega(
            resultado=ResultadoEntrega.REINTENTABLE,
            codigo_http=codigo,
            error=f"error del servidor {codigo}: {_detalle_seguro(respuesta)}",
        )

    if 400 <= codigo < 500:
        return Entrega(
            resultado=ResultadoEntrega.RECHAZADO,
            codigo_http=codigo,
            error=f"rechazo {codigo}: {_detalle_seguro(respuesta)}",
        )

    # Any other 2xx, a 3xx, or a non-standard code at or above 600. The only
    # documented success is 201, so an unexpected code is never taken as one,
    # and nothing about a code outside the standard families promises that
    # trying again would produce a different answer.
    return Entrega(
        resultado=ResultadoEntrega.RECHAZADO,
        codigo_http=codigo,
        error=f"respuesta inesperada {codigo}: no es el 201 del contrato",
    )


def _clasificar_creado(respuesta: httpx.Response, *, lecturas_enviadas: int) -> Entrega:
    """The five conditions a 201 has to satisfy before it counts as delivery."""
    cabecera = respuesta.headers.get(CABECERA_REPLAY)
    if cabecera not in (REPLAY_NO, REPLAY_SI):
        return Entrega(
            resultado=ResultadoEntrega.RECHAZADO,
            codigo_http=respuesta.status_code,
            error=(
                f"201 sin cabecera '{CABECERA_REPLAY}' valida; no se puede "
                "confirmar la entrega"
            ),
        )

    try:
        cuerpo = SesionMonitoreoCreada.model_validate(respuesta.json())
    except (ValueError, ValidationError):
        return Entrega(
            resultado=ResultadoEntrega.RECHAZADO,
            codigo_http=respuesta.status_code,
            error="201 con un cuerpo que no cumple 'SesionMonitoreoCreada'",
        )

    if len(cuerpo.ids_lectura) != cuerpo.lecturas_creadas:
        return Entrega(
            resultado=ResultadoEntrega.RECHAZADO,
            codigo_http=respuesta.status_code,
            error=(
                "201 incoherente: 'lecturas_creadas' no coincide con la "
                "cantidad de 'ids_lectura'"
            ),
        )

    if cuerpo.lecturas_creadas != lecturas_enviadas:
        return Entrega(
            resultado=ResultadoEntrega.RECHAZADO,
            codigo_http=respuesta.status_code,
            error=(
                f"201 con {cuerpo.lecturas_creadas} lectura(s) creada(s) frente "
                f"a {lecturas_enviadas} enviada(s); no confirma este paquete"
            ),
        )

    return Entrega(
        resultado=ResultadoEntrega.ENTREGADO,
        codigo_http=respuesta.status_code,
        reproducido=cabecera == REPLAY_SI,
        id_sesion=cuerpo.id_sesion,
        ids_lectura=tuple(cuerpo.ids_lectura),
    )


class ClienteEdge:
    """The edge's view of the ingestion API: one operation, already classified.

    Built around an injected ``httpx.Client`` rather than creating one, for two
    reasons. A test can hand it an ``httpx.MockTransport`` to script any answer,
    or a ``TestClient`` -- which *is* an ``httpx.Client`` -- to drive the real
    FastAPI application against a real PostgreSQL, with no production code that
    exists only for tests. And the caller keeps ownership of the connection
    pool, so closing it is not this class's decision.
    """

    def __init__(self, cliente_http: httpx.Client, *, ruta: str = RUTA_SESIONES) -> None:
        self._http = cliente_http
        self._ruta = ruta

    def enviar(self, *, clave: str, payload_json: str) -> Entrega:
        """One attempt for one package, with the key and body exactly as stored.

        The body is sent as raw bytes with ``content=``, not re-encoded from a
        parsed object with ``json=``. Re-encoding would risk a different byte
        sequence for the same package -- and while the server's fingerprint is
        semantic and would forgive that, sending precisely what was stored means
        the property does not depend on the server being forgiving.

        Every transport failure -- refused connection, timeout, DNS -- becomes
        ``REINTENTABLE``. The remote outcome in that case is genuinely
        **unknown**: the request may well have been committed and only the
        answer lost, which is exactly why the key is preserved and the next pass
        can safely replay it.
        """
        try:
            lecturas = contar_lecturas(payload_json)
        except ValueError as error:
            return Entrega(
                resultado=ResultadoEntrega.RECHAZADO,
                error=str(error),
            )

        try:
            respuesta = self._http.post(
                self._ruta,
                content=payload_json.encode("utf-8"),
                headers={
                    CABECERA_IDEMPOTENCIA: clave,
                    "Content-Type": "application/json",
                },
            )
        except httpx.TransportError as error:
            # Only the class name: an httpx message can carry the URL, and a URL
            # is configuration. The class is enough to tell a timeout from a
            # refused connection.
            return Entrega(
                resultado=ResultadoEntrega.REINTENTABLE,
                error=f"fallo de transporte: {type(error).__name__}",
            )

        return clasificar(respuesta, lecturas_enviadas=lecturas)
