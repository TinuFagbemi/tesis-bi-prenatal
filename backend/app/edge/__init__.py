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
liveness half -- something that keeps invoking the pass -- is SCRUM-65.

The edge never opens a PostgreSQL connection and never writes a clinical row: it
sends packages to the API and reads its answers. All data is fictitious and
simulated.
"""

from app.edge.almacenamiento import (
    DDL_CAPTURA,
    DDL_OUTBOX,
    ESQUEMA_ESPERADO,
    TABLA_CAPTURA,
    TABLA_OUTBOX,
    VERSION_DE_ESQUEMA,
    ErrorDeAlmacenamiento,
    EsquemaIncompatible,
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
)
from app.edge.config import EdgeSettings, settings_edge
from app.edge.emisor import LIMITE_POR_OMISION, ResumenPasada, ejecutar_pasada
from app.edge.estados import EstadoEntrega, VALORES_DE_ESTADO
from app.edge.outbox import (
    EventoElegible,
    ResumenOutbox,
    ahora_utc,
    leer_evento,
    leer_payload,
    marcar_enviado,
    marcar_fallido,
    registrar,
    resumen,
    seleccionar_elegibles,
)

__all__ = [
    "CABECERA_IDEMPOTENCIA",
    "CABECERA_REPLAY",
    "CapturaRegistrada",
    "ClienteEdge",
    "DDL_CAPTURA",
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
    "PaqueteInvalido",
    "RUTA_SESIONES",
    "ResultadoEntrega",
    "ResumenOutbox",
    "ResumenPasada",
    "TABLA_CAPTURA",
    "TABLA_OUTBOX",
    "VALORES_DE_ESTADO",
    "VERSION_DE_ESQUEMA",
    "ahora_utc",
    "capturar",
    "clasificar",
    "conectar",
    "contar_lecturas",
    "ejecutar_pasada",
    "generar_clave",
    "inicializar",
    "leer_evento",
    "leer_payload",
    "leer_version",
    "marcar_enviado",
    "marcar_fallido",
    "preparar_directorio",
    "registrar",
    "resumen",
    "seleccionar_elegibles",
    "settings_edge",
    "tablas_presentes",
    "transaccion",
    "validar_paquete",
    "verificar_esquema",
]
