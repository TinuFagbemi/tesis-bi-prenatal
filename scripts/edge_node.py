"""Simulador del nodo edge de FetalAlert: captura offline y envio explicito.

Uso desde la raiz del repositorio::

    python scripts/edge_node.py init                        # crea el almacenamiento local
    python scripts/edge_node.py capturar paquete.json       # guarda un paquete sin red
    python scripts/edge_node.py estado                      # resumen de la outbox
    python scripts/edge_node.py enviar                      # una sola pasada de envio

La ruta del archivo SQLite, la URL de la API, el timeout HTTP y el timeout de
bloqueo de SQLite salen de la configuracion del entorno (``EDGE_SQLITE_PATH``,
``EDGE_API_BASE_URL``, ``EDGE_HTTP_TIMEOUT``, ``EDGE_BUSY_TIMEOUT_MS``). ``--base``
permite apuntar a otro archivo para una demostracion, sin tocar la configuracion.

Este comando no abre ninguna conexion a PostgreSQL y no escribe ninguna fila
clinica: todo lo que llega al servidor pasa por la API. Tampoco imprime nunca el
contenido de un paquete, una credencial ni una URL con contrasena.

``enviar`` ejecuta **una** pasada finita y termina. No hay demonio, ni
reintentos automaticos, ni deteccion de conectividad: eso pertenece a SCRUM-65.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RAIZ_DEL_REPOSITORIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_DEL_REPOSITORIO / "backend"))

import httpx  # noqa: E402  -- tras ajustar sys.path

from app.edge import (  # noqa: E402
    ClienteEdge,
    ErrorDeAlmacenamiento,
    ErrorDeCaptura,
    capturar,
    conectar,
    ejecutar_pasada,
    inicializar,
    preparar_directorio,
    resumen,
    settings_edge,
)
from app.edge.emisor import LIMITE_POR_OMISION  # noqa: E402

CODIGO_DE_EXITO = 0
CODIGO_DE_ERROR = 1


def construir_parser() -> argparse.ArgumentParser:
    """Argumentos del comando.

    No existe una opcion para pasar credenciales ni una URL de base de datos:
    el nodo edge no las necesita y no debe poder recibirlas.
    """
    parser = argparse.ArgumentParser(
        prog="edge_node.py",
        description=(
            "Nodo edge simulado de FetalAlert: captura paquetes de monitoreo sin "
            "conexion y los entrega despues a la API, sin duplicarlos."
        ),
    )
    parser.add_argument(
        "--base",
        type=Path,
        default=settings_edge.sqlite_path,
        help=(
            "Archivo SQLite del nodo. Por omision, el de EDGE_SQLITE_PATH. "
            "No debe versionarse."
        ),
    )

    ordenes = parser.add_subparsers(dest="orden", required=True)

    ordenes.add_parser(
        "init", help="Crea o verifica el almacenamiento local. Nunca borra datos."
    )

    capturar_parser = ordenes.add_parser(
        "capturar",
        help="Valida un paquete simulado y lo guarda localmente, sin usar la red.",
    )
    capturar_parser.add_argument(
        "ruta",
        type=Path,
        help="Archivo JSON con una sesion de monitoreo y sus lecturas.",
    )

    ordenes.add_parser(
        "estado", help="Resumen de la outbox por estado. No imprime paquetes."
    )

    enviar_parser = ordenes.add_parser(
        "enviar",
        help="Ejecuta una sola pasada de envio de los eventos elegibles.",
    )
    enviar_parser.add_argument(
        "--limite",
        type=int,
        default=LIMITE_POR_OMISION,
        help=f"Maximo de eventos a intentar en esta pasada (por omision {LIMITE_POR_OMISION}).",
    )

    return parser


def _abrir(ruta: Path):
    """Conexion al archivo indicado, creando solo su carpeta."""
    preparar_directorio(ruta)
    return conectar(ruta, espera_de_bloqueo_ms=settings_edge.busy_timeout_ms)


def orden_init(ruta: Path) -> int:
    with _abrir(ruta) as conexion:
        creado = inicializar(conexion)
    print(
        "Almacenamiento local creado."
        if creado
        else "El almacenamiento local ya existia y es compatible."
    )
    return CODIGO_DE_EXITO


def orden_capturar(ruta_base: Path, ruta_paquete: Path) -> int:
    try:
        paquete = json.loads(ruta_paquete.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"Error: no existe el archivo {ruta_paquete}.", file=sys.stderr)
        return CODIGO_DE_ERROR
    except json.JSONDecodeError:
        # Sin el contenido del archivo: puede traer datos del paquete.
        print(
            f"Error: {ruta_paquete} no contiene un JSON valido.",
            file=sys.stderr,
        )
        return CODIGO_DE_ERROR

    with _abrir(ruta_base) as conexion:
        inicializar(conexion)
        registro = capturar(conexion, paquete)

    # Identificadores locales y la clave, que no es un dato clinico. Nunca el
    # paquete.
    print("Paquete capturado localmente. No se ha contactado a la API.")
    print(f"  id_outbox        : {registro.id_outbox}")
    print(f"  Idempotency-Key  : {registro.clave}")
    return CODIGO_DE_EXITO


def orden_estado(ruta: Path) -> int:
    with _abrir(ruta) as conexion:
        inicializar(conexion)
        actual = resumen(conexion)
    print("Outbox del nodo edge:")
    print(f"  PENDIENTE                 : {actual.pendientes}")
    print(f"  ENVIADO                   : {actual.enviados}")
    print(f"  FALLIDO (reintentable)    : {actual.fallidos_reintentables}")
    print(f"  FALLIDO (requiere revision): {actual.fallidos_en_revision}")
    print(f"  total                     : {actual.total}")
    return CODIGO_DE_EXITO


def orden_enviar(ruta: Path, limite: int) -> int:
    with _abrir(ruta) as conexion:
        inicializar(conexion)
        with httpx.Client(
            base_url=settings_edge.api_base_url,
            timeout=settings_edge.http_timeout,
        ) as http:
            pasada = ejecutar_pasada(
                conexion, ClienteEdge(http), limite=limite
            )

    print("Pasada de envio terminada.")
    print(f"  seleccionados             : {pasada.seleccionados}")
    print(f"  entregados                : {pasada.entregados}")
    print(f"  reintentables             : {pasada.reintentables}")
    print(f"  rechazados                : {pasada.rechazados}")
    print(f"  ya entregados por otro    : {pasada.ya_entregados}")
    if pasada.detenida_por_transporte:
        print(
            "  La pasada se detuvo: la API no esta accesible. Los eventos "
            "conservan su clave y pueden reintentarse en otra pasada."
        )
    return CODIGO_DE_EXITO


def main(argv: list[str] | None = None) -> int:
    argumentos = construir_parser().parse_args(argv)

    try:
        if argumentos.orden == "init":
            return orden_init(argumentos.base)
        if argumentos.orden == "capturar":
            return orden_capturar(argumentos.base, argumentos.ruta)
        if argumentos.orden == "estado":
            return orden_estado(argumentos.base)
        return orden_enviar(argumentos.base, argumentos.limite)

    except (ErrorDeAlmacenamiento, ErrorDeCaptura) as error:
        # ``detalle`` es texto escrito por este proyecto: no lleva valores del
        # paquete, ni rutas ajenas, ni mensajes del driver.
        print(f"Error: {error.detalle}", file=sys.stderr)
        return CODIGO_DE_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
