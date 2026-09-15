"""Pydantic contracts of the FetalAlert HTTP API.

These schemas describe request and response *messages*. They are deliberately
kept apart from the SQLAlchemy models in ``app.models``: an ORM instance is
never accepted as a request body and never returned as a response.
"""

from app.schemas.autenticacion import (
    CredencialesEntrada,
    IdentidadActual,
    TokenEmitido,
)
from app.schemas.monitoreo import (
    LecturaBiometricaEntrada,
    SesionMonitoreoCreada,
    SesionMonitoreoEntrada,
)

__all__ = [
    # Autenticación
    "CredencialesEntrada",
    "IdentidadActual",
    "TokenEmitido",
    # Monitoreo
    "LecturaBiometricaEntrada",
    "SesionMonitoreoCreada",
    "SesionMonitoreoEntrada",
]
