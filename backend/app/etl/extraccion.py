"""Extraction from the operational schema: read-only SQL, no decisions.

Every function here reads ``operacional`` through the connection it is given,
inside the caller's transaction, and returns plain rows. Nothing is classified,
derived or filtered by a business rule: that belongs to
:mod:`app.etl.transformacion`. The only filter is structural -- the anti-join
that selects the readings the fact does not hold yet.

**Why an anti-join and nothing else.** A reading is new when its
``id_lectura`` is not in the fact. That question does not depend on dates,
order or gaps: a late arrival with an old clinical date, an identifier lower
than the highest one loaded, or a hole in the sequence are all found, because
what is compared are sets of keys. ``MAX(id)`` loses a transaction that takes a
lower identifier and commits later; a watermark on ``fecha_hora_captura`` loses
a late arrival; ``fecha_hora_sincronizacion`` is NULL for what the edge sends;
and ``idempotencia_solicitud.fecha_hora`` is the claim time, not the commit,
and does not exist for rows loaded by SCRUM-61. None of them is used.

The session state is not a filter either: a reading of a PENDIENTE,
COMPLETADA, INTERRUMPIDA or PROCESADA session is a persisted reading, and the
grain of the fact is the reading.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.engine import Connection

from app.etl.modelos import FactLecturaBiometrica
from app.models import (
    Clinica,
    Embarazo,
    EmbarazoFactorRiesgo,
    Especialidad,
    FactorRiesgo,
    LecturaBiometrica,
    Medico,
    MedicoClinica,
    Paciente,
    SeguimientoClinico,
    Semaforo,
    SesionMonitoreo,
    TelefonoMedico,
    TelefonoPaciente,
    TiempoGestacional,
)
from app.models.enums import RolSeguimiento

Fila = dict[str, Any]

PARTES_DEL_NOMBRE = (
    "primer_nombre",
    "segundo_nombre",
    "apellido_paterno",
    "apellido_materno",
)


def _filas(conexion: Connection, consulta) -> list[Fila]:
    """Rows as dicts, with enum members turned into the text PostgreSQL stores.

    The operational enums are VARCHAR + CHECK and the ORM hands back Python enum
    members; the analytic schema and the rules speak in their text values.
    """
    return [
        {
            clave: valor.value if isinstance(valor, enum.Enum) else valor
            for clave, valor in fila.items()
        }
        for fila in conexion.execute(consulta).mappings()
    ]


@dataclass(frozen=True)
class OrigenDimensional:
    """Everything the dimensions, the bridge and the fact context are built from."""

    clinicas: list[Fila]
    semaforos: list[Fila]
    tiempos_gestacionales: list[Fila]
    factores_riesgo: list[Fila]
    medicos: list[Fila]
    telefonos_medico: list[Fila]
    afiliaciones_de_medico: list[Fila]
    pacientes: list[Fila]
    telefonos_paciente: list[Fila]
    embarazos: list[Fila]
    factores_de_embarazo: list[Fila]


def extraer_dimensiones(conexion: Connection) -> OrigenDimensional:
    """Read the catalogues and clinical entities the dimensions come from."""
    clinica = Clinica.__table__
    semaforo = Semaforo.__table__
    tiempo = TiempoGestacional.__table__
    factor = FactorRiesgo.__table__
    medico = Medico.__table__
    especialidad = Especialidad.__table__
    telefono_medico = TelefonoMedico.__table__
    medico_clinica = MedicoClinica.__table__
    paciente = Paciente.__table__
    telefono_paciente = TelefonoPaciente.__table__
    embarazo = Embarazo.__table__
    embarazo_factor = EmbarazoFactorRiesgo.__table__

    return OrigenDimensional(
        clinicas=_filas(
            conexion,
            select(
                clinica.c.id_clinica,
                clinica.c.nombre_clinica,
                clinica.c.provincia,
                clinica.c.distrito,
            ).order_by(clinica.c.id_clinica),
        ),
        semaforos=_filas(
            conexion,
            select(
                semaforo.c.id_semaforo,
                semaforo.c.codigo_nivel,
                semaforo.c.etiqueta_visual,
                semaforo.c.color_hex,
                semaforo.c.prioridad,
                semaforo.c.mensaje_app,
                semaforo.c.version_referencia,
            ).order_by(semaforo.c.id_semaforo),
        ),
        tiempos_gestacionales=_filas(
            conexion,
            select(
                tiempo.c.id_tiempo_gest,
                tiempo.c.semana_gestacion,
                tiempo.c.mes_gestacion,
                tiempo.c.trimestre,
                tiempo.c.descripcion,
            ).order_by(tiempo.c.id_tiempo_gest),
        ),
        factores_riesgo=_filas(
            conexion,
            select(
                factor.c.id_factor_riesgo,
                factor.c.clave_factor,
                factor.c.nombre_factor,
                factor.c.descripcion,
                factor.c.activo,
            ).order_by(factor.c.id_factor_riesgo),
        ),
        medicos=_filas(
            conexion,
            select(
                medico.c.id_medico,
                *(medico.c[parte] for parte in PARTES_DEL_NOMBRE),
                medico.c.email_med,
                especialidad.c.nombre_especialidad.label("especialidad"),
            )
            .join(
                especialidad,
                especialidad.c.id_especialidad == medico.c.id_especialidad,
            )
            .order_by(medico.c.id_medico),
        ),
        telefonos_medico=_filas(
            conexion,
            select(
                telefono_medico.c.id_medico,
                telefono_medico.c.tipo_contacto,
                telefono_medico.c.valor_contacto,
                telefono_medico.c.principal,
            ).order_by(telefono_medico.c.id_telefono_medico),
        ),
        # Every affiliation with its period, open or closed: ``activo`` is not
        # a filter, the dates are.
        afiliaciones_de_medico=_filas(
            conexion,
            select(
                medico_clinica.c.id_medico,
                medico_clinica.c.id_clinica,
                medico_clinica.c.fecha_inicio,
                medico_clinica.c.fecha_final,
            ).order_by(medico_clinica.c.id_medico, medico_clinica.c.id_clinica),
        ),
        pacientes=_filas(
            conexion,
            select(
                paciente.c.id_paciente,
                paciente.c.cedula,
                *(paciente.c[parte] for parte in PARTES_DEL_NOMBRE),
                paciente.c.fecha_nac,
            ).order_by(paciente.c.id_paciente),
        ),
        telefonos_paciente=_filas(
            conexion,
            select(
                telefono_paciente.c.id_paciente,
                telefono_paciente.c.tipo_contacto,
                telefono_paciente.c.valor_contacto,
                telefono_paciente.c.principal,
            ).order_by(telefono_paciente.c.id_telefono_paciente),
        ),
        embarazos=_filas(
            conexion,
            select(
                embarazo.c.id_embarazo,
                embarazo.c.id_paciente,
                embarazo.c.id_clinica,
                embarazo.c.numero_gestas,
                embarazo.c.numero_partos,
                embarazo.c.estado_embarazo,
                embarazo.c.fecha_inicio,
                embarazo.c.fecha_probable_parto,
                embarazo.c.fecha_cierre,
            ).order_by(embarazo.c.id_embarazo),
        ),
        factores_de_embarazo=_filas(
            conexion,
            select(
                embarazo_factor.c.id_embarazo,
                embarazo_factor.c.id_factor_riesgo,
                embarazo_factor.c.fecha_diagnostico,
                embarazo_factor.c.activo,
                embarazo_factor.c.observaciones,
            ).order_by(embarazo_factor.c.id_embarazo, embarazo_factor.c.id_factor_riesgo),
        ),
    )


def extraer_lecturas_nuevas(conexion: Connection) -> list[Fila]:
    """Operational readings whose ``id_lectura`` the fact does not hold yet.

    Each row carries what the fact needs from its session, its pregnancy and its
    gestational week, joined through the foreign keys the operational schema
    guarantees. The explicit ``ORDER BY`` is only there to make runs
    reproducible: nothing depends on it.
    """
    lectura = LecturaBiometrica.__table__
    sesion = SesionMonitoreo.__table__
    embarazo = Embarazo.__table__
    tiempo = TiempoGestacional.__table__
    hecho = FactLecturaBiometrica.__table__

    consulta = (
        select(
            lectura.c.id_lectura,
            lectura.c.id_sesion,
            lectura.c.id_tiempo_gest,
            lectura.c.id_semaforo,
            lectura.c.fecha_hora_captura,
            lectura.c.hr_valor,
            lectura.c.spo2_valor,
            lectura.c.mov_valor,
            sesion.c.id_embarazo,
            embarazo.c.id_paciente,
            embarazo.c.id_clinica,
            tiempo.c.semana_gestacion,
            tiempo.c.trimestre,
        )
        .select_from(
            lectura.join(sesion, sesion.c.id_sesion == lectura.c.id_sesion)
            .join(embarazo, embarazo.c.id_embarazo == sesion.c.id_embarazo)
            .join(tiempo, tiempo.c.id_tiempo_gest == lectura.c.id_tiempo_gest)
        )
        .where(~exists().where(hecho.c.id_lectura == lectura.c.id_lectura))
        .order_by(lectura.c.id_lectura)
    )
    return _filas(conexion, consulta)


def extraer_seguimientos_principales(
    conexion: Connection, ids_embarazo: Iterable[int]
) -> list[Fila]:
    """PRINCIPAL follow-up periods of the given pregnancies, whatever ``activo`` says."""
    ids = sorted(set(ids_embarazo))
    if not ids:
        return []
    seguimiento = SeguimientoClinico.__table__
    return _filas(
        conexion,
        select(
            seguimiento.c.id_seguimiento,
            seguimiento.c.id_embarazo,
            seguimiento.c.id_medico,
            seguimiento.c.fecha_asignacion,
            seguimiento.c.fecha_fin,
        )
        .where(
            seguimiento.c.id_embarazo.in_(ids),
            seguimiento.c.rol_seguimiento == RolSeguimiento.PRINCIPAL,
        )
        .order_by(seguimiento.c.id_seguimiento),
    )


def contar_hechos(conexion: Connection) -> int:
    hecho = FactLecturaBiometrica.__table__
    return conexion.execute(select(func.count()).select_from(hecho)).scalar_one()
