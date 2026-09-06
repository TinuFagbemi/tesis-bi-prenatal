"""Recognising a resend: canonical form, fingerprint and durable claim (SCRUM-63).

Two packages sent under the same ``Idempotency-Key`` are «the same request» when
they would be stored identically. Deciding that is the first half of this module,
and it is done with pure functions: no session, no database, no HTTP, nothing
that has to be mocked to test them.

The second half -- from «The durable claim» below -- takes a ``Session`` and
talks to PostgreSQL. It keeps the same contract the SCRUM-61 loader and
``app.services.ingesta`` established: it ``execute``s, and it **never** commits
and never rolls back. The transaction belongs to the router, and so does the
decision of when a claim becomes real. There is no FastAPI import here and no
HTTP status code: the mapping from these failures to responses lives in the
router, where it is visible.

**Why the fingerprint is taken after Pydantic and not over the raw bytes.**
Because the question is not «did the client send the same bytes» but «would this
be stored the same way». Hashing the body as it arrived would report a conflict
for a resend that merely reordered its JSON properties, or wrote ``97`` where the
first attempt wrote ``97.00``, even though PostgreSQL cannot tell those apart
once stored. A legitimate resend would then get a 409, which is precisely the
failure this ticket exists to prevent. So the fingerprint is taken over the
*validated* package, normalised the same way the column would normalise it.

The rules, and what each one is protecting:

* **Effective defaults.** ``estado_sesion`` and ``origen_dato`` may be omitted,
  and then the model's ``default`` applies. Omitting the field and sending its
  default value produce the very same row, so they produce the very same
  fingerprint. Both constants come from ``app.schemas.monitoreo``, so there is
  one declaration of «what the database applies when the client says nothing».
* **Decimals.** ``hr_valor`` and ``spo2_valor`` are ``NUMERIC(5, 2)``: ``97``,
  ``97.0`` and ``97.00`` are stored identically, so they are quantised to two
  decimals and rendered as text. Text rather than a JSON number because a JSON
  float cannot carry the scale, and the scale is exactly what has to survive.
* **Instants.** The columns are ``TIMESTAMPTZ``, which stores the instant and
  not the offset it was written with, so every timestamp is converted to UTC
  first. It is the same rule ``normalizar_valor`` already applies in the SCRUM-61
  loader; writing a second, different one would mean the same reading counted as
  equal through one door and different through the other.
* **Enums** travel as their ``value``, which is what the VARCHAR column holds.
* **NULL stays NULL.** A metric that does not apply is never turned into zero or
  an empty string, so an omitted field and an explicit ``null`` -- identical to
  Pydantic and identical in the database -- also fingerprint identically.
* **Integers stay integers.** The contract already refuses ``true`` for 1 and
  ``"119"`` for 119, so nothing has to be re-checked here.
* **The order of the readings is preserved and never sorted.** ``ids_lectura``
  comes back in that order, so two packages listing the same readings in a
  different order produce different answers and must be different packages.
  ``sort_keys`` only sorts the keys of each object; ``json.dumps`` never
  reorders a list.
* **``fecha_hora_sincronizacion`` is included, like every other field.** It is
  persisted, so a package that differs in it is stored differently and cannot
  share a key. The consequence is a rule for the client, not an exception here:
  the key identifies an immutable package, so a resend repeats the package it
  first prepared -- including that timestamp -- instead of stamping a fresh one
  per attempt. Re-stamping it means it is a different package and needs a
  different key.

**What is excluded, and why.** ``id_sesion`` and ``id_lectura`` are not part of
the input at all -- PostgreSQL generates them -- so there is nothing to exclude
there beyond saying it out loud. The ``Idempotency-Key`` itself is excluded
because it is the name under which this content is filed, not part of the
content; including it would make every key trivially match itself.

All data handled here is fictitious and simulated.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as insert_postgresql
from sqlalchemy.orm import Session

from app.models.idempotencia import LONGITUD_CLAVE, IdempotenciaSolicitud
from app.schemas.monitoreo import (
    ESTADO_POR_OMISION,
    ORIGEN_POR_OMISION,
    LecturaBiometricaEntrada,
    SesionMonitoreoEntrada,
)
from app.services.ingesta import ResultadoIngesta, registrar_sesion

registrador = logging.getLogger(__name__)

# Endpoint the key is scoped to. The scope is decided before the body is read,
# so it can never depend on a field of the package being deduplicated.
RECURSO_SESIONES_MONITOREO = "POST /api/v1/sesiones-monitoreo"

# Scale of the NUMERIC(5, 2) biometric columns. Not a clinical criterion: it is
# how much of the value PostgreSQL actually keeps.
ESCALA_BIOMETRICA = Decimal("0.01")

# Hexadecimal SHA-256, from the standard library. No dependency is added for
# this, and none is needed.
ALGORITMO_DE_HUELLA = "sha256"
LONGITUD_DE_HUELLA = 64


def _instante(momento: datetime | None) -> str | None:
    """Same instant, always written in UTC. ``None`` stays ``None``."""
    if momento is None:
        return None
    return momento.astimezone(timezone.utc).isoformat()


def _decimal(valor: Decimal | None) -> str | None:
    """Biometric value with the exact scale the column keeps, as text."""
    if valor is None:
        return None
    cuantizado = valor.quantize(ESCALA_BIOMETRICA)
    if cuantizado.is_zero():
        # ``-0.00`` and ``0.00`` are the same number and are stored as the same
        # number; they must not produce two different fingerprints.
        cuantizado = abs(cuantizado)
    return format(cuantizado, "f")


def _lectura_canonica(lectura: LecturaBiometricaEntrada) -> dict[str, Any]:
    return {
        "id_tiempo_gest": lectura.id_tiempo_gest,
        "id_semaforo": lectura.id_semaforo,
        "fecha_hora_captura": _instante(lectura.fecha_hora_captura),
        "fecha_hora_sincronizacion": _instante(lectura.fecha_hora_sincronizacion),
        "hr_valor": _decimal(lectura.hr_valor),
        "spo2_valor": _decimal(lectura.spo2_valor),
        "mov_valor": lectura.mov_valor,
    }


def contenido_canonico(entrada: SesionMonitoreoEntrada) -> dict[str, Any]:
    """Everything that decides what gets stored, and nothing else.

    Returned as a structure rather than as text so a test can look at one field
    without parsing, and so the serialisation stays in a single place.
    """
    estado = entrada.estado_sesion if entrada.estado_sesion is not None else ESTADO_POR_OMISION
    origen = entrada.origen_dato if entrada.origen_dato is not None else ORIGEN_POR_OMISION

    return {
        "id_embarazo": entrada.id_embarazo,
        "id_dispositivo": entrada.id_dispositivo,
        "tipo_sesion": entrada.tipo_sesion.value,
        "fecha_inicio": _instante(entrada.fecha_inicio),
        "fecha_fin": _instante(entrada.fecha_fin),
        "estado_sesion": estado.value,
        "origen_dato": origen.value,
        # Orden conservado a propósito: nunca se ordena esta lista.
        "lecturas": [_lectura_canonica(lectura) for lectura in entrada.lecturas],
    }


def canonicalizar_paquete(entrada: SesionMonitoreoEntrada) -> str:
    """Canonical text of a package: one package, one string, always the same.

    ``sort_keys`` makes the order in which the client wrote its JSON properties
    irrelevant; the compact separators leave no whitespace that could differ;
    ``ensure_ascii=False`` keeps the text as it is instead of escaping it, so the
    encoding step below is the only place where bytes are decided.
    """
    return json.dumps(
        contenido_canonico(entrada),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def huella_del_paquete(entrada: SesionMonitoreoEntrada) -> str:
    """SHA-256 of the canonical text, in lowercase hexadecimal."""
    return hashlib.sha256(canonicalizar_paquete(entrada).encode("utf-8")).hexdigest()

# ---------------------------------------------------------------------------
# The key itself
# ---------------------------------------------------------------------------

# URL-safe alphabet, which covers a canonical UUID (36 characters, dashes
# included) and a base64url token. The lower bound refuses a key so short it
# could collide by accident. The upper one is not a number of its own: it is the
# width of the column, imported rather than repeated, so a key that passes this
# check always fits where it is going to be stored.
LONGITUD_MINIMA_DE_CLAVE = 8
LONGITUD_MAXIMA_DE_CLAVE = LONGITUD_CLAVE

# Anchored, and matched with ``fullmatch``: a key is valid as a whole or not at
# all. The router publishes this same pattern in OpenAPI instead of writing a
# second copy of it, so the documented contract cannot drift from the enforced
# one.
PATRON_DE_CLAVE = re.compile(
    rf"^[A-Za-z0-9_-]{{{LONGITUD_MINIMA_DE_CLAVE},{LONGITUD_MAXIMA_DE_CLAVE}}}$"
)

MENSAJE_CLAVE_INVALIDA = (
    "La cabecera 'Idempotency-Key' es obligatoria y debe tener entre 8 y 128 "
    "caracteres de [A-Za-z0-9_-]."
)

# Nunca nombra el campo que difiere: decirlo permitiria sondear el contenido ya
# guardado comparando respuestas.
MENSAJE_COLISION = (
    "La cabecera 'Idempotency-Key' ya identifica un paquete con contenido "
    "distinto. Un reenvío debe repetir exactamente el mismo paquete, y un "
    "paquete diferente necesita una clave diferente. No se guardó nada."
)

MENSAJE_RECLAMACION_INCOMPLETA = (
    "La reclamación existe sin su resultado; no hay una respuesta original que "
    "reproducir."
)
MENSAJE_GANADOR_AUSENTE = (
    "La reclamación estaba tomada y ya no existe; no hay resultado que reproducir."
)
MENSAJE_RECLAMACION_NO_COMPLETADA = (
    "La reclamación no pudo enlazarse con la sesión recién creada."
)


def clave_valida(clave: str | None) -> bool:
    """Whether the client's key is well formed. Says nothing about the database."""
    return clave is not None and PATRON_DE_CLAVE.fullmatch(clave) is not None


# ---------------------------------------------------------------------------
# The durable claim
# ---------------------------------------------------------------------------

_TABLA = IdempotenciaSolicitud.__table__

# Un UPDATE que enlaza la reclamación con su resultado toca una fila y solo una:
# la reclamación se acaba de tomar en esta misma transacción.
FILAS_ESPERADAS_AL_COMPLETAR = 1


class ErrorDeIdempotencia(Exception):
    """Base of the failures this half reports, each with a message of its own.

    ``detalle`` carries text written here, never text produced by a driver, so
    the router can decide what to publish without sanitising anything. It is the
    same shape ``ErrorDeIngesta`` uses, on purpose: two ways of carrying a safe
    message would be one too many.
    """

    def __init__(self, detalle: str) -> None:
        self.detalle = detalle
        super().__init__(detalle)


class ColisionDeIdempotencia(ErrorDeIdempotencia):
    """The key is taken by a *complete* package whose content is not this one."""


class AnomaliaDeIdempotencia(ErrorDeIdempotencia):
    """A state the design says cannot happen. Never answered as a replay.

    Three shapes, one meaning: a claim that exists without its result, a claim
    that vanished between being observed and being read, or an ``UPDATE`` that
    failed to bind a claim to the session just created. None is reachable while
    the code owns its transaction and ``ON DELETE RESTRICT`` protects the claim,
    so reaching one means something outside this flow is losing rows. The honest
    answer is an internal error -- never a retry, which would repeat the
    operation against a system that is losing data, and never an invented replay
    of a result nobody can read.

    ``detalle`` is safe text, but the router does **not** publish it: a client
    gets the fixed internal-error message, because none of this is its business.
    """


@dataclass(frozen=True)
class ResultadoIdempotente:
    """What one request produced, and whether it produced it now.

    ``reproducido`` is what tells the router which ending to choose: a commit
    for a package that was created, a rollback for one that already existed.
    """

    resultado: ResultadoIngesta
    reproducido: bool


def _buscar_reclamacion(sesion_bd: Session, *, recurso: str, clave: str):
    """The claim filed under this key, or ``None``.

    Only the three columns the answer needs. Read with an ordinary ``SELECT``:
    under READ COMMITTED every statement takes a fresh snapshot, which is what
    lets this see a claim another transaction confirmed a moment ago.

    This query is a **fast path, not the guarantee**. It saves a sequential
    resend from writing anything at all, and that is its whole purpose; the
    thing that actually prevents two packages from being accepted under one key
    is the UNIQUE constraint, exercised by :func:`_reclamar_clave`.
    """
    return sesion_bd.execute(
        select(_TABLA.c.huella, _TABLA.c.id_sesion, _TABLA.c.ids_lectura).where(
            _TABLA.c.recurso == recurso,
            _TABLA.c.clave == clave,
        )
    ).one_or_none()


def _reclamar_clave(
    sesion_bd: Session, *, recurso: str, clave: str, huella: str
) -> int | None:
    """Take the key, or report that somebody else holds it. Atomically.

    ``ON CONFLICT (recurso, clave) DO NOTHING`` is the whole mechanism, and the
    reason it is this and not a caught ``IntegrityError``:

    * it raises no ``23505``, so the SQLSTATE map of SCRUM-62 keeps meaning what
      it meant -- a duplicate key on these tables is still a desynchronised
      sequence and still a 500;
    * it leaves the transaction usable, so the caller can read the winner
      without a rollback first;
    * the mutual exclusion is PostgreSQL's speculative insertion lock, not a
      lock in this process.

    Returns the id of the claim when this request took it, and ``None`` when it
    did not. ``None`` has exactly one meaning: somebody else's claim is
    committed. A competitor that *aborted* leaves no live row, and PostgreSQL
    re-evaluates within the same statement, so this insert would succeed
    instead. That is why there is no retry anywhere in this module.

    The result columns are left NULL: the session does not exist yet. They are
    filled in by :func:`_completar_reclamacion`, in this same transaction.
    """
    return sesion_bd.execute(
        insert_postgresql(_TABLA)
        .values(recurso=recurso, clave=clave, huella=huella)
        .on_conflict_do_nothing(index_elements=["recurso", "clave"])
        .returning(_TABLA.c.id_idempotencia)
    ).scalar_one_or_none()


def _completar_reclamacion(
    sesion_bd: Session, id_idempotencia: int, resultado: ResultadoIngesta
) -> None:
    """Bind the claim to the answer it produced, without committing.

    ``ids_lectura`` is stored in the order the package listed its readings, so a
    replay reproduces the first answer instead of recomputing an order.
    ``lecturas_creadas`` is not stored: it is the length of this list.

    The row count is checked rather than assumed. If this ``UPDATE`` touched no
    row, the claim it was meant to complete is not there, and letting the commit
    go through would confirm a session whose claim says nothing -- a row that
    whoever resends would then read as an anomaly. Failing here turns a silent
    inconsistency into a rollback.
    """
    filas = sesion_bd.execute(
        update(_TABLA)
        .where(_TABLA.c.id_idempotencia == id_idempotencia)
        .values(id_sesion=resultado.id_sesion, ids_lectura=list(resultado.ids_lectura))
    ).rowcount
    if filas != FILAS_ESPERADAS_AL_COMPLETAR:
        raise AnomaliaDeIdempotencia(MENSAJE_RECLAMACION_NO_COMPLETADA)


def _resultado_de_la_reclamacion(fila, huella_esperada: str) -> ResultadoIngesta:
    """The original answer, or a refusal saying which of the two things is wrong.

    **Completeness is checked before the fingerprint, and the order matters.** A
    409 asserts something specific: your key already names a *different* package,
    and that package's answer stands. A row without its result cannot support
    that assertion -- there is no completed operation to point at -- so comparing
    fingerprints first would let an incomplete row be reported as an ordinary
    collision and blame the client for a state that is the server's fault. An
    incomplete claim is an anomaly whatever the incoming package says.
    """
    if fila.id_sesion is None or not fila.ids_lectura:
        raise AnomaliaDeIdempotencia(MENSAJE_RECLAMACION_INCOMPLETA)
    if fila.huella != huella_esperada:
        raise ColisionDeIdempotencia(MENSAJE_COLISION)
    return ResultadoIngesta(
        id_sesion=fila.id_sesion,
        ids_lectura=tuple(fila.ids_lectura),
    )


def _recuperar_al_ganador(
    sesion_bd: Session, *, recurso: str, clave: str, huella: str
) -> ResultadoIngesta:
    """Read the answer of whoever took the key, in the caller's transaction.

    Called only after :func:`_reclamar_clave` returned ``None``, which means the
    winner is already committed, so this ``SELECT`` finds it. No rollback
    precedes it -- ``DO NOTHING`` did not abort anything -- and none is issued
    here: opening a second transaction just to read would leave it hanging until
    the session closes.
    """
    fila = _buscar_reclamacion(sesion_bd, recurso=recurso, clave=clave)
    if fila is None:
        raise AnomaliaDeIdempotencia(MENSAJE_GANADOR_AUSENTE)
    return _resultado_de_la_reclamacion(fila, huella)


def procesar_ingesta_idempotente(
    sesion_bd: Session,
    entrada: SesionMonitoreoEntrada,
    *,
    recurso: str,
    clave: str,
) -> ResultadoIdempotente:
    """One package under one key: create it, or hand back what it already was.

    The whole flow lives here so the router does not have to know it. What the
    router keeps is the part only it can decide -- the commit, the rollback and
    the HTTP answer -- and what it gets back is a result and a flag.

    **No commit, no rollback, nothing swallowed.** Every exception leaves
    untouched: the domain errors of ``app.services.ingesta``, the two of this
    module, and anything SQLAlchemy raises. The transaction belongs to whoever
    called.

    The order is the design:

    1. **Fast path.** Look the key up; a sequential resend is answered from what
       is stored, without writing a row.
    2. **Claim**, atomically, *before* references are checked and before
       anything is written. Coming first is what makes "the key was taken and
       then the work failed" a real, testable situation, and it is what lets a
       duplicate stop before doing work it would throw away.
    3. If the claim was lost, the winner is committed: read its answer here, in
       this same transaction.
    4. Otherwise do the work and bind the claim to its result.
    """
    huella = huella_del_paquete(entrada)

    # 1. Via rapida.
    fila = _buscar_reclamacion(sesion_bd, recurso=recurso, clave=clave)
    if fila is not None:
        return ResultadoIdempotente(
            resultado=_resultado_de_la_reclamacion(fila, huella), reproducido=True
        )

    # 2. Reclamación atómica: la primera escritura del paquete.
    reclamacion = _reclamar_clave(
        sesion_bd, recurso=recurso, clave=clave, huella=huella
    )
    if reclamacion is None:
        # 3. Otro la tomó y ya confirmó. Misma transacción, sin rollback.
        return ResultadoIdempotente(
            resultado=_recuperar_al_ganador(
                sesion_bd, recurso=recurso, clave=clave, huella=huella
            ),
            reproducido=True,
        )

    # 4. Paquete nuevo.
    resultado = registrar_sesion(sesion_bd, entrada)
    _completar_reclamacion(sesion_bd, reclamacion, resultado)
    return ResultadoIdempotente(resultado=resultado, reproducido=False)


# ---------------------------------------------------------------------------
# Traceability
# ---------------------------------------------------------------------------

# The key is written by the client and may carry anything, so it never reaches a
# log in the clear. What is recorded is a short prefix of its digest: enough to
# follow one key across several lines, useless for reading it back. The field is
# named ``clave_hash`` and not ``clave`` because that is what it is -- a reader
# must never mistake the recorded value for the key itself.
LONGITUD_DE_HASH_DE_CLAVE = 12


def hash_de_clave(clave: str) -> str:
    """Short, non-reversible handle for a key, safe to write to a log."""
    digest = hashlib.sha256(clave.encode("utf-8")).hexdigest()
    return digest[:LONGITUD_DE_HASH_DE_CLAVE]


# Neither the fingerprint of the package nor any part of it appears here. The
# fingerprint is left out on purpose: publishing it would let anyone with the
# log confirm a package's content by comparing hashes.
_FORMATO = "Idempotencia: evento=%s recurso=%s clave_hash=%s%s"


def registrar_replay(recurso: str, clave: str, id_sesion: int) -> None:
    registrador.info(
        _FORMATO, "replay", recurso, hash_de_clave(clave), f" id_sesion={id_sesion}"
    )


def registrar_colision(recurso: str, clave: str) -> None:
    registrador.warning(_FORMATO, "colision", recurso, hash_de_clave(clave), "")


def registrar_anomalia(recurso: str, clave: str) -> None:
    registrador.error(_FORMATO, "anomalia", recurso, hash_de_clave(clave), "")
