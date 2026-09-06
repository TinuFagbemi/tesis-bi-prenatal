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
from app.services.idempotencia import (
    MENSAJE_CLAVE_INVALIDA,
    MENSAJE_COLISION,
    RECURSO_SESIONES_MONITOREO,
    AnomaliaDeIdempotencia,
    ColisionDeIdempotencia,
    ErrorDeIdempotencia,
    ResultadoIdempotente,
    canonicalizar_paquete,
    clave_valida,
    contenido_canonico,
    huella_del_paquete,
    procesar_ingesta_idempotente,
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
    # Idempotencia. Solo lo que se usa fuera del módulo: el flujo completo, sus
    # dos fallos, y las funciones puras que las pruebas de canonicalización
    # ejercen. Los pasos internos -- buscar, reclamar, completar, recuperar --
    # son privados y se prueban a través del endpoint.
    "MENSAJE_CLAVE_INVALIDA",
    "MENSAJE_COLISION",
    "RECURSO_SESIONES_MONITOREO",
    "AnomaliaDeIdempotencia",
    "ColisionDeIdempotencia",
    "ErrorDeIdempotencia",
    "ResultadoIdempotente",
    "canonicalizar_paquete",
    "clave_valida",
    "contenido_canonico",
    "huella_del_paquete",
    "procesar_ingesta_idempotente",
    # Ingesta
    "ErrorDeIngesta",
    "ReferenciaInexistente",
    "ReglaDeNegocioViolada",
    "ResultadoIngesta",
    "registrar_sesion",
    "verificar_referencias",
]
