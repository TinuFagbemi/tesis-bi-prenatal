"""ORM models of the FetalAlert operational schema.

Importing this package registers all 22 operational tables on ``Base.metadata``,
which is what Alembic autogeneration and the DDL tests rely on.
"""

from app.db.base import Base
from app.models.catalogos import (
    Especialidad,
    FactorRiesgo,
    Rol,
    Semaforo,
    TiempoGestacional,
)
from app.models.clinico import (
    Clinica,
    Embarazo,
    EmbarazoFactorRiesgo,
    Medico,
    MedicoClinica,
    Paciente,
    SeguimientoClinico,
    TelefonoMedico,
    TelefonoPaciente,
)
from app.models.monitoreo import (
    AsignacionDispositivo,
    Dispositivo,
    LecturaBiometrica,
    SesionMonitoreo,
)
from app.models.seguridad import (
    AuditoriaLog,
    Usuario,
    UsuarioMedico,
    UsuarioPaciente,
)

__all__ = [
    "Base",
    # Catálogos
    "Especialidad",
    "FactorRiesgo",
    "TiempoGestacional",
    "Semaforo",
    "Rol",
    # Dominio clínico
    "Clinica",
    "Paciente",
    "TelefonoPaciente",
    "Medico",
    "TelefonoMedico",
    "MedicoClinica",
    "Embarazo",
    "SeguimientoClinico",
    "EmbarazoFactorRiesgo",
    # Monitoreo
    "Dispositivo",
    "AsignacionDispositivo",
    "SesionMonitoreo",
    "LecturaBiometrica",
    # Seguridad
    "Usuario",
    "UsuarioPaciente",
    "UsuarioMedico",
    "AuditoriaLog",
]
