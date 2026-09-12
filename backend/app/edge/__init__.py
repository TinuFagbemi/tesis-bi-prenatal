"""Simulated edge node: durable local capture and explicit delivery (SCRUM-64).

The component this package simulates is the one a rural deployment would need
and this thesis does not build in hardware: something that accepts a monitoring
session **while the API is unreachable**, keeps it across a restart, and hands
it over later without creating it twice.

Six modules, each with one job:

:mod:`app.edge.estados`
    The three delivery states, declared once.
:mod:`app.edge.config`
    Where the file is, which API to call, how long to wait.
:mod:`app.edge.almacenamiento`
    The SQLite schema, its guarantees, connections and transactions.
:mod:`app.edge.outbox`
    Reads and state transitions, with ``ENVIADO`` terminal by construction.
:mod:`app.edge.captura`
    Validating a package and storing it atomically, with no network.
:mod:`app.edge.cliente` and :mod:`app.edge.emisor`
    One request, its classification, and one finite pass over the queue.

The entry point is ``scripts/edge_node.py``.

**What this package guarantees, stated precisely.** Not «exactly once
delivery» -- no client can promise that over a network where an answer can be
lost. What it guarantees is that a package survives locally until it is
confirmed, that a retry repeats the *same* key and the *same* bytes, and that
the server's idempotency therefore makes the business effect happen once. The
liveness half -- the loop that keeps invoking rounds, waits between them and
repairs an interrupted attempt -- is :mod:`app.edge.sincronizacion` (SCRUM-65).

The edge never opens a PostgreSQL connection and never writes a clinical row: it
sends packages to the API and reads its answers. All data is fictitious and
simulated.
"""

from app.edge.almacenamiento import (
    DDL_CAPTURA,
    DDL_INTENTO,
    DDL_OUTBOX,
    ESQUEMA_ESPERADO,
    TABLA_CAPTURA,
    TABLA_INTENTO,
    TABLA_OUTBOX,
    VERSION_ANTERIOR,
    VERSION_DE_ESQUEMA,
    ErrorDeAlmacenamiento,
    EsquemaIncompatible,
    SoporteInsuficiente,
    conectar,
    inicializar,
    leer_version,
    preparar_directorio,
    tablas_presentes,
    transaccion,
    verificar_esquema,
)
from app.edge.captura import (
    CapturaRegistrada,
    ErrorDeCaptura,
    PaqueteInvalido,
    capturar,
    generar_clave,
    validar_paquete,
)
from app.edge.cliente import (
    CABECERA_IDEMPOTENCIA,
    CABECERA_REPLAY,
    RUTA_SESIONES,
    ClienteEdge,
    Entrega,
    ResultadoEntrega,
    clasificar,
    contar_lecturas,
    es_resultado_desconocido,
)
from app.edge.config import EdgeSettings, cargar_settings_edge
from app.edge.emisor import LIMITE_POR_OMISION, ResumenPasada, ejecutar_pasada
from app.edge.estados import (
    VALORES_DE_ESTADO,
    VALORES_DE_MOTIVO,
    VALORES_DE_RESULTADO,
    EstadoEntrega,
    MotivoRevision,
)
from app.edge.outbox import (
    Censo,
    EventoElegible,
    Reclamacion,
    ResumenOutbox,
    Traza,
    ahora_utc,
    anotar_demora,
    censar,
    confirmar_transicion,
    finalizar_intento,
    intentos_abandonados,
    leer_evento,
    leer_payload,
    leer_traza,
    marca_de_agua,
    marcar_enviado,
    marcar_fallido,
    predicado_elegible,
    reclamar_intento,
    reclamar_reconciliacion,
    registrar,
    resolver_herencia_incompatible,
    resumen,
    seleccionar_elegibles,
)
from app.edge.politica import (
    BATCH_LIMIT_POR_OMISION,
    MAX_ATTEMPTS_POR_OMISION,
    ConfiguracionInvalida,
    PoliticaDeReintentos,
)
from app.edge.sincronizacion import (
    CODIGO_ANOMALIA,
    CODIGO_ERROR,
    CODIGO_EXITO,
    CODIGO_REVISION,
    ResumenSincronizacion,
    reconciliar_abandonados,
    resolver_herencia,
    sincronizar,
)

__all__ = [
    "BATCH_LIMIT_POR_OMISION",
    "CABECERA_IDEMPOTENCIA",
    "CABECERA_REPLAY",
    "CODIGO_ANOMALIA",
    "CODIGO_ERROR",
    "CODIGO_EXITO",
    "CODIGO_REVISION",
    "CapturaRegistrada",
    "Censo",
    "ClienteEdge",
    "ConfiguracionInvalida",
    "DDL_CAPTURA",
    "DDL_INTENTO",
    "DDL_OUTBOX",
    "ESQUEMA_ESPERADO",
    "EdgeSettings",
    "Entrega",
    "ErrorDeAlmacenamiento",
    "ErrorDeCaptura",
    "EsquemaIncompatible",
    "EstadoEntrega",
    "EventoElegible",
    "LIMITE_POR_OMISION",
    "MAX_ATTEMPTS_POR_OMISION",
    "MotivoRevision",
    "PaqueteInvalido",
    "PoliticaDeReintentos",
    "RUTA_SESIONES",
    "Reclamacion",
    "ResultadoEntrega",
    "ResumenOutbox",
    "ResumenPasada",
    "ResumenSincronizacion",
    "SoporteInsuficiente",
    "TABLA_CAPTURA",
    "TABLA_INTENTO",
    "TABLA_OUTBOX",
    "Traza",
    "VALORES_DE_ESTADO",
    "VALORES_DE_MOTIVO",
    "VALORES_DE_RESULTADO",
    "VERSION_ANTERIOR",
    "VERSION_DE_ESQUEMA",
    "ahora_utc",
    "anotar_demora",
    "capturar",
    "cargar_settings_edge",
    "censar",
    "clasificar",
    "conectar",
    "confirmar_transicion",
    "contar_lecturas",
    "ejecutar_pasada",
    "es_resultado_desconocido",
    "finalizar_intento",
    "generar_clave",
    "inicializar",
    "intentos_abandonados",
    "leer_evento",
    "leer_payload",
    "leer_traza",
    "leer_version",
    "marca_de_agua",
    "marcar_enviado",
    "marcar_fallido",
    "predicado_elegible",
    "preparar_directorio",
    "reclamar_intento",
    "reclamar_reconciliacion",
    "reconciliar_abandonados",
    "registrar",
    "resolver_herencia",
    "resolver_herencia_incompatible",
    "resumen",
    "seleccionar_elegibles",
    "sincronizar",
    "tablas_presentes",
    "transaccion",
    "validar_paquete",
    "verificar_esquema",
]
