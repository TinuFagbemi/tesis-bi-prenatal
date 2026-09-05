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
  semaphore level and never derives a gestational week: it receives both as
  references and checks that they exist. The single clinical rule it does
  enforce -- fetal movement is only meaningful from week 20 -- is the one the
  ``LecturaBiometrica`` model explicitly leaves to the service layer, and it
  reuses the existing threshold instead of writing a second copy of it.

All data handled here is fictitious and simulated.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

# Read-only reuse of the approved threshold. The constant is imported from the
# module that declares it so there is exactly one definition of "week 20" in the
# repository; the loader itself is never executed, and SCRUM-61 is not modified.
from app.loader.dataset import SEMANA_MINIMA_DE_MOVIMIENTO
from app.models.catalogos import Semaforo, TiempoGestacional
from app.models.clinico import Embarazo
from app.models.monitoreo import Dispositivo, LecturaBiometrica, SesionMonitoreo
from app.schemas.monitoreo import SesionMonitoreoEntrada


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


def _verificar_embarazo(sesion_bd: Session, id_embarazo: int) -> None:
    existe = sesion_bd.scalar(
        select(Embarazo.id_embarazo).where(Embarazo.id_embarazo == id_embarazo)
    )
    if existe is None:
        raise ReferenciaInexistente(
            f"No existe un embarazo con id_embarazo={id_embarazo}."
        )


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


def _verificar_semana_de_movimiento(
    entrada: SesionMonitoreoEntrada, semana_por_tiempo: dict[int, int]
) -> None:
    """Fetal movement is only clinically meaningful from week 20 onwards.

    The rule spans ``sesion_monitoreo`` and ``tiempo_gestacional``, so no
    row-level SQL CHECK can express it -- the ``LecturaBiometrica`` docstring
    says as much and leaves it to this layer. The threshold is the one already
    approved and declared elsewhere; it is read, not redefined.
    """
    for indice, lectura in enumerate(entrada.lecturas):
        if not lectura.es_de_movimiento:
            continue
        semana = semana_por_tiempo[lectura.id_tiempo_gest]
        if semana < SEMANA_MINIMA_DE_MOVIMIENTO:
            raise ReglaDeNegocioViolada(
                f"lecturas[{indice}]: registra movimiento fetal en la semana "
                f"{semana}; no se registran movimientos antes de la semana "
                f"{SEMANA_MINIMA_DE_MOVIMIENTO}."
            )


def verificar_referencias(
    sesion_bd: Session, entrada: SesionMonitoreoEntrada
) -> None:
    """Every reference must already exist, and the package must obey week 20.

    Four queries for a package of any size: the catalogue identifiers are
    gathered across all readings and looked up once each, not once per reading.

    This runs before a single row is written, so a package that names something
    that is not there fails with a precise message instead of an opaque foreign
    key error. It does **not** replace the constraints: between this check and
    the INSERT a referenced row could still disappear, and PostgreSQL remains
    the authority that catches it.
    """
    _verificar_embarazo(sesion_bd, entrada.id_embarazo)
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
    _verificar_semana_de_movimiento(entrada, semana_por_tiempo)


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
