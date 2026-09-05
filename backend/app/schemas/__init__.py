"""Pydantic contracts of the FetalAlert HTTP API.

These schemas describe request and response *messages*. They are deliberately
kept apart from the SQLAlchemy models in ``app.models``: an ORM instance is
never accepted as a request body and never returned as a response.
"""

from app.schemas.monitoreo import (
    LecturaBiometricaEntrada,
    SesionMonitoreoCreada,
    SesionMonitoreoEntrada,
)

__all__ = [
    "LecturaBiometricaEntrada",
    "SesionMonitoreoCreada",
    "SesionMonitoreoEntrada",
]
