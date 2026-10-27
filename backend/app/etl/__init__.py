"""Analytic schema and reproducible ETL of FetalAlert (SCRUM-69).

``operacional`` is read, ``analitico`` is written, and one run is one
transaction. The modules split the work the way the pipeline does:

* :mod:`app.etl.modelos` -- the nine structures of the star schema;
* :mod:`app.etl.reglas` -- the only place the screening thresholds and the
  derivation rules live, with no database access;
* :mod:`app.etl.extraccion` -- read-only SQL, including the anti-join of new
  readings;
* :mod:`app.etl.transformacion` -- operational rows into analytic rows;
* :mod:`app.etl.carga` -- type 1 upserts and insert-only facts;
* :mod:`app.etl.conciliacion` -- independent SQL checks between both schemas;
* :mod:`app.etl.ejecucion` -- the lock, the transaction, the summary and the
  read-only preview of the next load.

The entry point is ``scripts/etl_analitico.py``.
"""

from app.etl.carga import ResultadoUpsert, insertar_hechos, upsert_tipo_1
from app.etl.conciliacion import (
    CHEQUEOS,
    InformeDeConciliacion,
    Verificacion,
    conciliar,
)
from app.etl.ejecucion import (
    AISLAMIENTO,
    AVISO_DE_CANDIDATA,
    CLAVE_DEL_CANDADO,
    RESULTADO_EXITO,
    RESULTADO_FALLO,
    RESULTADO_PREVISUALIZACION,
    CandadoOcupado,
    CandidataDeCarga,
    CargaInconsistente,
    ConciliacionFallida,
    Previsualizacion,
    ResultadoEjecucion,
    candado_ocupado,
    conciliar_sin_escribir,
    describir_error_de_base,
    ejecutar_etl,
    formatear_conciliacion,
    formatear_previsualizacion,
    formatear_resumen,
    previsualizar_sin_escribir,
)
from app.etl.modelos import TABLAS_ANALITICAS
from app.etl.reglas import (
    VERSION_SIM_1_0,
    CatalogoIncoherente,
    DerivacionAmbigua,
    DerivacionImposible,
    DiscrepanciaDeSemaforo,
    ErrorDeDatos,
    ErrorDeEtl,
    LecturaInvalida,
    ReglaNoDefinida,
    VersionDeReglasDesconocida,
)

__all__ = [
    # Constantes
    "AISLAMIENTO",
    "AVISO_DE_CANDIDATA",
    "CHEQUEOS",
    "CLAVE_DEL_CANDADO",
    "RESULTADO_EXITO",
    "RESULTADO_FALLO",
    "RESULTADO_PREVISUALIZACION",
    "TABLAS_ANALITICAS",
    "VERSION_SIM_1_0",
    # Errores
    "CandadoOcupado",
    "CargaInconsistente",
    "CatalogoIncoherente",
    "ConciliacionFallida",
    "DerivacionAmbigua",
    "DerivacionImposible",
    "DiscrepanciaDeSemaforo",
    "ErrorDeDatos",
    "ErrorDeEtl",
    "LecturaInvalida",
    "ReglaNoDefinida",
    "VersionDeReglasDesconocida",
    # Carga, conciliación y previsualización
    "CandidataDeCarga",
    "InformeDeConciliacion",
    "Previsualizacion",
    "ResultadoEjecucion",
    "ResultadoUpsert",
    "Verificacion",
    "candado_ocupado",
    "conciliar",
    "conciliar_sin_escribir",
    "describir_error_de_base",
    "ejecutar_etl",
    "formatear_conciliacion",
    "formatear_previsualizacion",
    "formatear_resumen",
    "insertar_hechos",
    "previsualizar_sin_escribir",
    "upsert_tipo_1",
]
