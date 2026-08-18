"""Controlled vocabularies for the FetalAlert operational model.

Every enum is stored as a checked ``VARCHAR`` rather than a native PostgreSQL
``ENUM`` type: ``native_enum=False`` keeps values readable in CSV/JSON exports
and lets Alembic evolve a value list by rewriting a CHECK constraint instead of
issuing ``ALTER TYPE``.
"""

import enum

from sqlalchemy import Enum


class CodigoSemaforo(str, enum.Enum):
    """Risk level published by the semaphore classification."""

    OK = "OK"
    WARNING = "WARNING"
    ERROR = "ERROR"


class NombreRol(str, enum.Enum):
    """Application roles backing RBAC."""

    ADMIN = "ADMIN"
    MEDICO = "MEDICO"
    PACIENTE = "PACIENTE"


class TipoContacto(str, enum.Enum):
    """Kind of contact stored for a patient or physician.

    These are the internal values persisted in Python and PostgreSQL. A user
    interface is free to render them as "Celular", "Teléfono de casa" and
    "Correo alterno" respectively.
    """

    CELULAR = "CELULAR"
    TELEFONO_DOMICILIO = "TELEFONO_DOMICILIO"
    CORREO_ALTERNO = "CORREO_ALTERNO"


class EstadoEmbarazo(str, enum.Enum):
    """Lifecycle of a pregnancy record."""

    ACTIVO = "ACTIVO"
    FINALIZADO = "FINALIZADO"
    SUSPENDIDO = "SUSPENDIDO"


class RolSeguimiento(str, enum.Enum):
    """Role a physician holds over a pregnancy follow-up."""

    PRINCIPAL = "PRINCIPAL"
    APOYO = "APOYO"
    REEMPLAZO = "REEMPLAZO"


class EstadoDispositivo(str, enum.Enum):
    """Availability of a monitoring device."""

    DISPONIBLE = "DISPONIBLE"
    ASIGNADO = "ASIGNADO"
    MANTENIMIENTO = "MANTENIMIENTO"
    INACTIVO = "INACTIVO"


class TipoSesion(str, enum.Enum):
    """What a monitoring session measures.

    A session is either maternal vitals (HR + SpO2) or fetal movement counting;
    the two never share a consolidated reading.
    """

    SIGNOS_MATERNOS = "SIGNOS_MATERNOS"
    MOVIMIENTOS_FETALES = "MOVIMIENTOS_FETALES"


class EstadoSesion(str, enum.Enum):
    """Progress of a monitoring session through the synchronisation pipeline."""

    PENDIENTE = "PENDIENTE"
    COMPLETADA = "COMPLETADA"
    INTERRUMPIDA = "INTERRUMPIDA"
    PROCESADA = "PROCESADA"


class OrigenDato(str, enum.Enum):
    """Channel the session data arrived through."""

    DISPOSITIVO = "DISPOSITIVO"
    CSV = "CSV"


def enum_column(python_enum: type[enum.Enum], *, name: str, length: int = 30) -> Enum:
    """Build the SQLAlchemy type for ``python_enum``.

    A fresh :class:`~sqlalchemy.Enum` is returned per call so that each column
    owns its CHECK constraint and the naming convention can derive a distinct
    name from ``name`` and the owning table.
    """
    return Enum(
        python_enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        length=length,
        values_callable=lambda members: [member.value for member in members],
    )
