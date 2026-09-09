"""Accepting a package locally, with no network involved at all (SCRUM-64).

This is the operation a rural node has to be able to perform when there is no
connectivity: take a monitoring session with its readings, decide it is valid,
give it a durable identity, and store it so that nothing short of losing the
file can lose it. **No connectivity check happens first**, because a check would
be a reason to refuse, and refusing is precisely what must not happen offline.

**The unit is the package, never a single reading.** One
``SesionMonitoreoEntrada`` -- a session and every reading captured during it --
is what the endpoint treats as one idempotent operation, so it is what the edge
treats as one event. Splitting readings into separate rows would let a session
arrive at PostgreSQL half sent, and no amount of retrying would put it back
together.

**The contract is reused, not copied.** The package is validated with the very
``SesionMonitoreoEntrada`` the API validates it with, and what is stored is that
model's own ``model_dump_json()``. Two properties follow, and both were measured
rather than assumed: the text re-validates against the same contract after a
restart, and the server's fingerprint of the re-read package is identical to the
fingerprint of the original -- so a resend is recognised as a replay instead of
being refused as a collision. Writing a second, edge-local schema would have put
those two properties at the mercy of two definitions staying in step.

**The key is created once, here, and never again.** A canonical UUID4: 36
characters that satisfy the server's ``^[A-Za-z0-9_-]{8,128}$`` with room to
spare, carrying no name, no clinical value and no credential. It is written in
the same transaction as the package it names, so there is no instant at which a
stored package lacks its key or a key names a package that was not stored.

**``fecha_hora_sincronizacion``: frozen, not forbidden.** That field takes part
in the server's fingerprint, so the danger is not that it carries a value -- it
is that the value could *change between attempts*, which would turn a legitimate
resend into a 409 exactly when it matters most, right after a lost response.
Freezing it is what removes the danger, and storing the validated package once
freezes it by construction.

So there are two cases and neither needs a rule of its own:

* **The edge never stamps it.** A package captured offline has not been
  synchronised yet, so the field stays ``null`` -- which is also what the
  simulator produces and what the examples show. The instant a delivery was
  actually confirmed is recorded in ``outbox.enviado_en``, where that fact
  belongs.
* **An incoming file may already carry one**, and if the contract accepts it, so
  does this module: the value is stored exactly as received and sent back
  unchanged on every attempt. Refusing it here would mean the edge enforced a
  *stricter* contract than the API it feeds -- a second set of rules, free to
  drift from the first, rejecting packages the server would have accepted.

What is validated is what the contract already validates: a synchronisation
earlier than its own capture is refused, and so is one without a timezone
offset. Those rules live in ``SesionMonitoreoEntrada``, and this module does not
restate them.

All data handled here is fictitious and simulated.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from app.edge import outbox
from app.edge.almacenamiento import transaccion
from app.schemas.monitoreo import SesionMonitoreoEntrada

class ErrorDeCaptura(Exception):
    """Base of the failures this module reports with a message of its own."""

    def __init__(self, detalle: str) -> None:
        self.detalle = detalle
        super().__init__(detalle)


class PaqueteInvalido(ErrorDeCaptura):
    """The package does not satisfy the contract the API publishes."""


@dataclass(frozen=True)
class CapturaRegistrada:
    """What the caller gets back: local identifiers and the key. Never a promise.

    Nothing here says the server received anything, because at this point it has
    not been told. That is the whole design: the capture succeeds offline.
    """

    id_captura: int
    id_outbox: int
    clave: str


def generar_clave() -> str:
    """A fresh idempotency key: one canonical UUID4, created exactly once."""
    return str(uuid.uuid4())


def _detalle_saneado(error: ValidationError) -> str:
    """Which fields are wrong and why, without echoing a single value.

    Pydantic's own rendering of a ``ValidationError`` embeds ``input_value``,
    and for this contract those values are the clinical payload -- a heart rate,
    an oxygen saturation, a capture timestamp. So the message is rebuilt from
    the two parts of each error that are schema and not data: the location of
    the field and the machine-readable error type. Same rule ``DiagnosticoSeguro``
    follows on the server side: pick the safe fields one by one, never format the
    exception.
    """
    partes = []
    for detalle in error.errors():
        ubicacion = ".".join(str(tramo) for tramo in detalle.get("loc", ()))
        partes.append(f"{ubicacion or '<paquete>'}: {detalle.get('type', 'invalido')}")
    return "; ".join(partes)


def validar_paquete(
    paquete: SesionMonitoreoEntrada | dict[str, Any],
) -> SesionMonitoreoEntrada:
    """The package as the API would read it, or a refusal that names no value.

    Accepts an already-validated model unchanged -- re-validating one would be
    work with no question behind it -- and validates a mapping with the shared
    contract.

    **The contract is the only authority.** There is no extra rule here, and the
    absence is deliberate: a package this function refused but the endpoint
    would have accepted would mean the edge had quietly grown a second, stricter
    contract, and two contracts drift.
    """
    if isinstance(paquete, SesionMonitoreoEntrada):
        return paquete
    try:
        return SesionMonitoreoEntrada.model_validate(paquete)
    except ValidationError as error:
        raise PaqueteInvalido(_detalle_saneado(error)) from error


def capturar(
    conexion: sqlite3.Connection,
    paquete: SesionMonitoreoEntrada | dict[str, Any],
    *,
    generador_de_clave: Callable[[], str] = generar_clave,
    reloj: Callable[[], datetime] = outbox.ahora_utc,
) -> CapturaRegistrada:
    """Validate, name and store one package. Atomically, and without the network.

    The order is deliberate and each step depends on the one before it:

    1. **Validate.** An invalid package must not reach SQLite at all, so this
       happens before a transaction exists. A refusal leaves the file untouched.
    2. **Name it.** One UUID4, generated once. A retry later reuses this exact
       string; it is never recomputed, which is what makes the resend a replay.
    3. **Serialise.** ``model_dump_json`` of the validated model -- the text that
       will be sent verbatim as the request body, not rebuilt from parts.
    4. **One transaction**, holding the capture and its outbox row. Either both
       rows exist or neither does: no orphan capture, no outbox row pointing at
       nothing.

    ``generador_de_clave`` and ``reloj`` are injectable so a test can make the
    key and the timestamps deterministic. They default to real ones, so nothing
    in production depends on a test seam.

    Raises :class:`PaqueteInvalido` for a package the contract refuses. Any
    database failure propagates after the transaction has been rolled back.
    """
    validado = validar_paquete(paquete)

    clave = generador_de_clave()
    payload_json = validado.model_dump_json()
    capturado_en = reloj()

    with transaccion(conexion):
        id_captura, id_outbox = outbox.registrar(
            conexion,
            payload_json=payload_json,
            clave=clave,
            capturado_en=capturado_en,
        )

    return CapturaRegistrada(
        id_captura=id_captura, id_outbox=id_outbox, clave=clave
    )
