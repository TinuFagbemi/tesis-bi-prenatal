"""Reference checking and transactional persistence of a monitoring package.

The contract of this module mirrors the one SCRUM-61 established for its loader,
for the same reason: **the transaction belongs to the caller**. Nothing here
commits, nothing here rolls back, and every error propagates untouched. What it
does is ``add`` and ``flush``, so the router can own a single commit per request
and undo everything with one ``rollback``.

Two more boundaries, both deliberate:

* **Nothing is created as a side effect.** A package may only point at rows that
  already exist. There is no code path that inserts a clinic, a pregnancy, a
  device, a gestational week or a semaphore level, and the input schema has no
  field that could describe one.
* **No clinical criterion is reimplemented.** The endpoint never decides a
  semaphore level: it receives ``id_semaforo`` already classified and only
  checks that it exists. The single clinical rule it enforces -- fetal movement
  is only meaningful from week 20 -- is the one the ``LecturaBiometrica`` model
  explicitly leaves to the service layer, and it reuses the existing threshold
  instead of writing a second copy of it.

What this module *does* decide are the invariants that no single table can
express, because each of them spans several rows:

* the device must have been **assigned to that pregnancy** over the period of
  the session, judged by dates and not by the ``activo`` flag;
* the ``id_tiempo_gest`` a reading points at must be the week the pregnancy was
  **actually in** on the capture date, so the week-20 rule cannot be sidestepped
  by pointing at a different catalogue row.

All data handled here is fictitious and simulated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

# Read-only reuse of the approved threshold. The constant is imported from the
# module that declares it so there is exactly one definition of "week 20" in the
# repository; the loader itself is never executed, and SCRUM-61 is not modified.
from app.loader.dataset import SEMANA_MINIMA_DE_MOVIMIENTO
from app.models.catalogos import Semaforo, TiempoGestacional
from app.models.clinico import Embarazo
from app.models.monitoreo import (
    AsignacionDispositivo,
    Dispositivo,
    LecturaBiometrica,
    SesionMonitoreo,
)
from app.schemas.monitoreo import SesionMonitoreoEntrada

# Gestational weeks the catalogue can hold at all, mirroring
# ``ck_tiempo_gestacional_semana_rango``. A capture that lands outside this
# range cannot match any row, and saying so plainly beats reporting a
# mismatch against a catalogue entry that could never have existed.
SEMANA_GESTACIONAL_MINIMA = 1
SEMANA_GESTACIONAL_MAXIMA = 42

DIAS_POR_SEMANA = 7


def semana_gestacional(fecha_inicio_embarazo: date, captura: datetime) -> int:
    """Gestational week a pregnancy is in on a given capture instant.

    Same arithmetic the dataset generator uses for the very same purpose:
    whole weeks elapsed since the pregnancy started, counting the first week as
    week 1. Keeping the two in agreement is what makes a generated dataset and
    an ingested package describe the same thing.
    """
    dias = (captura.date() - fecha_inicio_embarazo).days
    return (dias // DIAS_POR_SEMANA) + 1


class ErrorDeIngesta(Exception):
    """Base of every failure this module reports with a message of its own.

    ``detalle`` carries text written here, never text produced by a driver, so
    the router can put it in an HTTP body without sanitising anything.
    """

    def __init__(self, detalle: str) -> None:
        self.detalle = detalle
        super().__init__(detalle)


class ReferenciaInexistente(ErrorDeIngesta):
    """The package points at a row that is not in the database."""


class ReglaDeNegocioViolada(ErrorDeIngesta):
    """The package is well formed but breaks a rule of the monitoring domain."""


@dataclass(frozen=True)
class ResultadoIngesta:
    """Identifiers PostgreSQL assigned to the package, before the commit."""

    id_sesion: int
    ids_lectura: tuple[int, ...]


def _leer_embarazo(sesion_bd: Session, id_embarazo: int) -> date:
    """Start date of the pregnancy, or refuse because it is not there.

    The date is read in the same query that proves the row exists: every
    gestational week in the package is computed against it, and fetching it
    twice would be a round trip for something already on its way.
    """
    fila = sesion_bd.execute(
        select(Embarazo.fecha_inicio).where(Embarazo.id_embarazo == id_embarazo)
    ).one_or_none()
    if fila is None:
        raise ReferenciaInexistente(
            f"No existe un embarazo con id_embarazo={id_embarazo}."
        )
    return fila[0]


def _verificar_dispositivo(sesion_bd: Session, id_dispositivo: int) -> None:
    existe = sesion_bd.scalar(
        select(Dispositivo.id_dispositivo).where(
            Dispositivo.id_dispositivo == id_dispositivo
        )
    )
    if existe is None:
        raise ReferenciaInexistente(
            f"No existe un dispositivo con id_dispositivo={id_dispositivo}."
        )


def _verificar_asignacion(
    sesion_bd: Session, entrada: SesionMonitoreoEntrada
) -> None:
    """The device must have been lent to *this* pregnancy over the session.

    Two existing foreign keys only prove that the pregnancy exists and that the
    device exists. ``AsignacionDispositivo`` is what ties them together, and it
    ties them over a period, so the check is temporal.

    ``activo`` is deliberately ignored. It describes the assignment *now*, and
    judging a past session by it would retroactively invalidate every reading
    taken during a lending period that has since been closed. What matters is
    whether the assignment covered the session when the session happened.

    The period compared is the session's own: from ``fecha_inicio`` to
    ``fecha_fin`` when the session declares one, and otherwise just the starting
    instant, because a session still open has no end to cover yet. An assignment
    with no ``fecha_fin`` is still running and covers anything from its start.

    One query per package -- never one per reading.
    """
    inicio_sesion = entrada.fecha_inicio.date()
    fin_sesion = (
        entrada.fecha_fin.date() if entrada.fecha_fin is not None else inicio_sesion
    )

    asignacion = sesion_bd.scalar(
        select(AsignacionDispositivo.id_asignacion)
        .where(
            AsignacionDispositivo.id_embarazo == entrada.id_embarazo,
            AsignacionDispositivo.id_dispositivo == entrada.id_dispositivo,
            AsignacionDispositivo.fecha_inicio <= inicio_sesion,
            or_(
                AsignacionDispositivo.fecha_fin.is_(None),
                AsignacionDispositivo.fecha_fin >= fin_sesion,
            ),
        )
        .limit(1)
    )
    if asignacion is None:
        raise ReglaDeNegocioViolada(
            f"El dispositivo id_dispositivo={entrada.id_dispositivo} no tiene "
            f"una asignación al embarazo id_embarazo={entrada.id_embarazo} que "
            f"cubra la sesión del {inicio_sesion.isoformat()} al "
            f"{fin_sesion.isoformat()}."
        )


def _leer_semanas_gestacionales(
    sesion_bd: Session, identificadores: set[int]
) -> dict[int, int]:
    """Gestational week of each requested catalogue row, in a single query.

    The week comes back alongside the existence check because the week-20 rule
    needs it: asking twice for the same rows would be a second round trip for
    data already on its way.
    """
    filas = sesion_bd.execute(
        select(TiempoGestacional.id_tiempo_gest, TiempoGestacional.semana_gestacion).where(
            TiempoGestacional.id_tiempo_gest.in_(identificadores)
        )
    ).all()
    return {id_tiempo_gest: semana for id_tiempo_gest, semana in filas}


def _verificar_semaforos(sesion_bd: Session, identificadores: set[int]) -> None:
    encontrados = set(
        sesion_bd.scalars(
            select(Semaforo.id_semaforo).where(Semaforo.id_semaforo.in_(identificadores))
        ).all()
    )
    faltantes = sorted(identificadores - encontrados)
    if faltantes:
        raise ReferenciaInexistente(
            f"No existe(n) el/los semáforo(s) con id_semaforo={faltantes}."
        )


def _verificar_semanas_gestacionales(
    entrada: SesionMonitoreoEntrada,
    fecha_inicio_embarazo: date,
    semana_por_tiempo: dict[int, int],
) -> None:
    """``id_tiempo_gest`` must name the week the pregnancy was really in.

    Checking only that the catalogue row exists proves nothing: it says which
    week the *client claimed*, not which week the pregnancy was in when the
    reading was captured. Two rules ride on that difference.

    The first is truthfulness of the dimension itself -- a reading filed under
    week 31 that was captured in week 12 would corrupt every gestational
    grouping the ETL builds later.

    The second is the week-20 rule for fetal movement. Applied to the declared
    week it was trivially avoidable: point ``id_tiempo_gest`` at any week 20 or
    later and a movement captured in week 12 went straight in. Applied to the
    computed week, the escape hatch closes, because the week now comes from the
    pregnancy and the capture date rather than from the payload.

    Purely in memory: the pregnancy's start date and the catalogue weeks were
    both fetched already, so no reading costs a query.
    """
    for indice, lectura in enumerate(entrada.lecturas):
        esperada = semana_gestacional(fecha_inicio_embarazo, lectura.fecha_hora_captura)

        if esperada < SEMANA_GESTACIONAL_MINIMA:
            raise ReglaDeNegocioViolada(
                f"lecturas[{indice}]: la captura es anterior al inicio del "
                f"embarazo ({fecha_inicio_embarazo.isoformat()})."
            )
        if esperada > SEMANA_GESTACIONAL_MAXIMA:
            raise ReglaDeNegocioViolada(
                f"lecturas[{indice}]: la captura corresponde a la semana "
                f"gestacional {esperada}, fuera del rango "
                f"{SEMANA_GESTACIONAL_MINIMA}-{SEMANA_GESTACIONAL_MAXIMA}."
            )

        declarada = semana_por_tiempo[lectura.id_tiempo_gest]
        if declarada != esperada:
            raise ReglaDeNegocioViolada(
                f"lecturas[{indice}]: 'id_tiempo_gest' corresponde a la semana "
                f"{declarada}, pero la captura cae en la semana {esperada} de "
                "este embarazo."
            )

        if lectura.es_de_movimiento and esperada < SEMANA_MINIMA_DE_MOVIMIENTO:
            raise ReglaDeNegocioViolada(
                f"lecturas[{indice}]: registra movimiento fetal en la semana "
                f"{esperada}; no se registran movimientos antes de la semana "
                f"{SEMANA_MINIMA_DE_MOVIMIENTO}."
            )


def verificar_referencias(
    sesion_bd: Session, entrada: SesionMonitoreoEntrada
) -> None:
    """Every reference must exist, and the package must hold together.

    Five queries for a package of any size, regardless of how many readings it
    carries: the pregnancy, the device, the assignment that ties them, and one
    batched lookup for each catalogue. The identifiers are gathered across all
    readings and looked up once each, never one query per reading.

    Existence is checked first and coherence second, so the answer names the
    most specific thing that is wrong: a missing row is a 404, and a package
    whose parts do not fit together is a 422.

    This runs before a single row is written, so a package that names something
    that is not there fails with a precise message instead of an opaque foreign
    key error. It does **not** replace the constraints: between this check and
    the INSERT a referenced row could still disappear, and PostgreSQL remains
    the authority that catches it.
    """
    fecha_inicio_embarazo = _leer_embarazo(sesion_bd, entrada.id_embarazo)
    _verificar_dispositivo(sesion_bd, entrada.id_dispositivo)

    tiempos_pedidos = {lectura.id_tiempo_gest for lectura in entrada.lecturas}
    semana_por_tiempo = _leer_semanas_gestacionales(sesion_bd, tiempos_pedidos)
    faltantes = sorted(tiempos_pedidos - set(semana_por_tiempo))
    if faltantes:
        raise ReferenciaInexistente(
            f"No existe(n) el/los tiempo(s) gestacional(es) con "
            f"id_tiempo_gest={faltantes}."
        )

    _verificar_semaforos(
        sesion_bd, {lectura.id_semaforo for lectura in entrada.lecturas}
    )

    _verificar_asignacion(sesion_bd, entrada)
    _verificar_semanas_gestacionales(entrada, fecha_inicio_embarazo, semana_por_tiempo)


def _construir_sesion(entrada: SesionMonitoreoEntrada) -> SesionMonitoreo:
    """ORM session row, letting the model supply the defaults it declares.

    ``estado_sesion`` and ``origen_dato`` are only set when the client sent
    them. Omitting the attribute is what lets the value declared on the model
    apply, so the default lives in exactly one place.
    """
    argumentos: dict[str, object] = {
        "id_embarazo": entrada.id_embarazo,
        "id_dispositivo": entrada.id_dispositivo,
        "tipo_sesion": entrada.tipo_sesion,
        "fecha_inicio": entrada.fecha_inicio,
        "fecha_fin": entrada.fecha_fin,
    }
    if entrada.estado_sesion is not None:
        argumentos["estado_sesion"] = entrada.estado_sesion
    if entrada.origen_dato is not None:
        argumentos["origen_dato"] = entrada.origen_dato
    return SesionMonitoreo(**argumentos)


def registrar_sesion(
    sesion_bd: Session, entrada: SesionMonitoreoEntrada
) -> ResultadoIngesta:
    """Persist one session and all of its readings, without committing.

    The order is references -> session -> flush -> readings -> flush, and each
    flush is there for a reason:

    * the first one makes PostgreSQL assign ``id_sesion``, which every reading
      needs before it can be inserted;
    * the second one sends all the readings at once and forces every CHECK and
      foreign key to be evaluated **before** the caller commits. That is what
      makes a package atomic: if the third reading of five is rejected, the
      session inserted a moment earlier was never confirmed.

    ``SessionLocal`` is configured with ``autoflush=False``, so nothing travels
    to the server until one of those two calls; the moment is explicit, not
    incidental.

    No commit and no rollback: whoever owns the transaction decides. Every error
    -- including the ones PostgreSQL raises on flush -- propagates untouched.
    """
    verificar_referencias(sesion_bd, entrada)

    sesion = _construir_sesion(entrada)
    sesion_bd.add(sesion)
    sesion_bd.flush()

    lecturas = [
        LecturaBiometrica(
            id_sesion=sesion.id_sesion,
            id_tiempo_gest=lectura.id_tiempo_gest,
            id_semaforo=lectura.id_semaforo,
            fecha_hora_captura=lectura.fecha_hora_captura,
            fecha_hora_sincronizacion=lectura.fecha_hora_sincronizacion,
            hr_valor=lectura.hr_valor,
            spo2_valor=lectura.spo2_valor,
            mov_valor=lectura.mov_valor,
        )
        for lectura in entrada.lecturas
    ]
    sesion_bd.add_all(lecturas)
    sesion_bd.flush()

    return ResultadoIngesta(
        id_sesion=sesion.id_sesion,
        ids_lectura=tuple(lectura.id_lectura for lectura in lecturas),
    )
