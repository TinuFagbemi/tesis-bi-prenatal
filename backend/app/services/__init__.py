"""Business logic of the FetalAlert API, kept out of the routers.

Everything here is plain Python and SQLAlchemy: no FastAPI import, no HTTP
status code, no request object. That is what lets the same functions be tested
directly, and what keeps the routers thin.
"""

from app.services.errores import (
    DiagnosticoSeguro,
    RespuestaDeError,
    clasificar_error_de_base,
    diagnostico_seguro,
    extraer_sqlstate,
    registrar_fallo,
)
from app.services.ingesta import (
    ErrorDeIngesta,
    ReferenciaInexistente,
    ReglaDeNegocioViolada,
    ResultadoIngesta,
    registrar_sesion,
    verificar_referencias,
)

__all__ = [
    # Traducción de errores
    "DiagnosticoSeguro",
    "RespuestaDeError",
    "clasificar_error_de_base",
    "diagnostico_seguro",
    "extraer_sqlstate",
    "registrar_fallo",
    # Ingesta
    "ErrorDeIngesta",
    "ReferenciaInexistente",
    "ReglaDeNegocioViolada",
    "ResultadoIngesta",
    "registrar_sesion",
    "verificar_referencias",
]
