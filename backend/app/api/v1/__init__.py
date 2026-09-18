"""Version 1 of the FetalAlert API."""

from app.api.v1.autenticacion import router as router_autenticacion
from app.api.v1.clinico import router as router_clinico
from app.api.v1.cuentas import router as router_cuentas
from app.api.v1.sesiones import router as router_sesiones

__all__ = [
    "router_autenticacion",
    "router_clinico",
    "router_cuentas",
    "router_sesiones",
]
