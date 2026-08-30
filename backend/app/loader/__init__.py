"""Idempotent loader of the simulated FetalAlert dataset into PostgreSQL.

Two modules, one boundary: :mod:`app.loader.dataset` reads, validates and
normalises the JSON without ever touching a database, and
:mod:`app.loader.postgres` writes it through a connection whose transaction
belongs to the caller.

The entry point is ``scripts/load_mock_data.py``.
"""

from app.loader.dataset import (
    COMANDO_DEL_GENERADOR,
    MAPA_SECCIONES,
    METADATA_APROBADA,
    ORDEN_DE_CARGA,
    RUTA_POR_DEFECTO,
    SECCION_POR_TABLA,
    SECCIONES_INFORMATIVAS,
    AmbienteNoPermitido,
    ConflictoDeDatos,
    DatasetInvalido,
    DatasetNormalizado,
    ErrorDeCarga,
    EsquemaDesactualizado,
    MotorNoSoportado,
    clave_primaria,
    leer_dataset,
    normalizar_fila,
    normalizar_valor,
    tabla_operacional,
    validar_dataset,
)
from app.loader.postgres import (
    AMBIENTES_PERMITIDOS,
    AjusteSecuencia,
    ResultadoCarga,
    ResultadoTabla,
    ajustar_secuencias,
    cargar_dataset,
    citar_secuencia,
    formatear_resumen,
    leer_estado_de_secuencia,
    leer_revision_desplegada,
    obtener_head,
    preflight,
    proximo_valor,
    sanear_mensaje,
    secuencia_de_tabla,
    verificar_ambiente,
    verificar_dialecto,
    verificar_revision,
    verificar_url,
)

__all__ = [
    # Constantes
    "AMBIENTES_PERMITIDOS",
    "COMANDO_DEL_GENERADOR",
    "MAPA_SECCIONES",
    "METADATA_APROBADA",
    "ORDEN_DE_CARGA",
    "RUTA_POR_DEFECTO",
    "SECCIONES_INFORMATIVAS",
    "SECCION_POR_TABLA",
    # Errores
    "AmbienteNoPermitido",
    "ConflictoDeDatos",
    "DatasetInvalido",
    "ErrorDeCarga",
    "EsquemaDesactualizado",
    "MotorNoSoportado",
    # Dataset
    "DatasetNormalizado",
    "clave_primaria",
    "leer_dataset",
    "normalizar_fila",
    "normalizar_valor",
    "tabla_operacional",
    "validar_dataset",
    # Carga
    "AjusteSecuencia",
    "ResultadoCarga",
    "ResultadoTabla",
    "ajustar_secuencias",
    "cargar_dataset",
    "citar_secuencia",
    "formatear_resumen",
    "leer_estado_de_secuencia",
    "leer_revision_desplegada",
    "obtener_head",
    "preflight",
    "proximo_valor",
    "sanear_mensaje",
    "secuencia_de_tabla",
    "verificar_ambiente",
    "verificar_dialecto",
    "verificar_revision",
    "verificar_url",
]
