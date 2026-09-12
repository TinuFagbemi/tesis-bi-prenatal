"""Simulador del nodo edge de FetalAlert: captura offline y sincronizacion diferida.

Uso desde la raiz del repositorio::

    python scripts/edge_node.py init                    # crea o actualiza el almacenamiento
    python scripts/edge_node.py capturar paquete.json   # guarda un paquete sin red
    python scripts/edge_node.py estado                  # resumen de la outbox
    python scripts/edge_node.py enviar                  # UNA pasada, sin esperas
    python scripts/edge_node.py sincronizar             # reintentos con espera incremental
    python scripts/edge_node.py traza <clave>           # que paso con un evento

``enviar`` y ``sincronizar`` no son lo mismo, y la diferencia importa:

* ``enviar`` ejecuta **una sola ronda** y termina. No espera, no reintenta y no
  respeta la fecha del proximo intento: es una accion manual, para forzar un
  envio ahora mismo durante una demostracion. Si respeta el maximo de intentos,
  porque un limite que un comando puede saltarse no es un limite.
* ``sincronizar`` repite rondas, **espera entre ellas** con espera incremental
  acotada, repara los intentos que quedaron sin resultado y termina cuando no
  queda nada por hacer. Tambien es finito: no hay demonio ni servicio residente.

La ruta del archivo SQLite, la URL de la API, los timeouts y la politica de
reintentos salen de la configuracion del entorno (``EDGE_SQLITE_PATH``,
``EDGE_API_BASE_URL``, ``EDGE_HTTP_TIMEOUT``, ``EDGE_BUSY_TIMEOUT_MS``,
``EDGE_MAX_ATTEMPTS``, ``EDGE_BASE_DELAY_SECONDS``, ``EDGE_MAX_DELAY_SECONDS``,
``EDGE_BATCH_LIMIT``). ``--base`` permite apuntar a otro archivo para una
demostracion, sin tocar la configuracion.

``--max-intentos`` **incluye el primer intento**: con 3 hay un intento inmediato
y dos reintentos, y no existe un cuarto automatico.

``--limite`` es el tamano de una **ronda**, no el maximo de intentos ni el total
de la ejecucion.

Este comando no abre ninguna conexion a PostgreSQL y no escribe ninguna fila
clinica: todo lo que llega al servidor pasa por la API. Tampoco imprime nunca el
contenido de un paquete, una credencial ni una URL con contrasena.

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
    CODIGO_ANOMALIA,
    CODIGO_REVISION,
    ClienteEdge,
    ConfiguracionInvalida,
    ErrorDeAlmacenamiento,
    ErrorDeCaptura,
    PoliticaDeReintentos,
    capturar,
    conectar,
    ejecutar_pasada,
    inicializar,
    leer_traza,
    leer_version,
    preparar_directorio,
    resumen,
    sincronizar,
)
from app.edge.config import (  # noqa: E402
    EdgeSettings,
    cargar_settings_edge,
)
from app.edge.almacenamiento import VERSION_ANTERIOR  # noqa: E402

CODIGO_DE_EXITO = 0
CODIGO_DE_ERROR = 1


def entero_positivo(texto: str) -> int:
    """Un entero >= 1, o un error de argumento antes de tocar la base.

    SCRUM-64 no validaba ``--limite``, y las consecuencias eran silenciosas: con
    0 la ronda no seleccionaba nada y parecia que la cola estaba vacia, y con un
    negativo el ``LIMIT`` de SQLite desaparecia y la ronda dejaba de estar
    acotada. Ninguna de las dos cosas fallaba: hacian algo distinto de lo que se
    habia pedido.
    """
    try:
        valor = int(texto)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{texto}' no es un numero entero.")
    if valor < 1:
        raise argparse.ArgumentTypeError(
            f"debe ser un entero mayor o igual que 1; se recibio {valor}."
        )
    return valor


def numero_positivo(texto: str) -> float:
    """Un numero finito y mayor que 0."""
    try:
        valor = float(texto)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{texto}' no es un numero.")
    # ``isfinite`` primero: 'nan' se convierte sin error y despues compara falso
    # contra todo, asi que una comprobacion de signo sola lo dejaria pasar.
    import math

    if not math.isfinite(valor) or valor <= 0:
        raise argparse.ArgumentTypeError(
            f"debe ser un numero finito mayor que 0; se recibio '{texto}'."
        )
    return valor


def construir_parser(settings: EdgeSettings) -> argparse.ArgumentParser:
    """Argumentos del comando, con los valores por omision de la configuracion.

    Recibe la configuracion en lugar de leer un global: los valores por omision
    que publica ``--help`` salen del entorno, asi que construir el parser puede
    fallar si el entorno esta mal, y eso tiene que ocurrir dentro del limite
    controlado de ``main()``.

    No existe una opcion para pasar credenciales ni una URL de base de datos: el
    nodo edge no las necesita y no debe poder recibirlas.
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
        default=settings.sqlite_path,
        help=(
            "Archivo SQLite del nodo. Por omision, el de EDGE_SQLITE_PATH. "
            "No debe versionarse."
        ),
    )

    ordenes = parser.add_subparsers(dest="orden", required=True)

    ordenes.add_parser(
        "init",
        help=(
            "Crea el almacenamiento local, o lo actualiza a la version actual "
            "conservando los eventos ya guardados. Nunca borra datos."
        ),
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
        help=(
            "Una sola ronda de envio, sin esperas ni reintentos. Ignora la fecha "
            "del proximo intento, pero respeta el maximo de intentos."
        ),
    )
    enviar_parser.add_argument(
        "--limite",
        type=entero_positivo,
        default=settings.batch_limit,
        help=(
            "Maximo de eventos que esta ronda intenta (por omision "
            f"{settings.batch_limit}). No es el maximo de intentos."
        ),
    )

    sincronizar_parser = ordenes.add_parser(
        "sincronizar",
        help=(
            "Sincronizacion resiliente y finita: reintentos con espera "
            "incremental, reparacion de intentos sin resultado y agotamiento."
        ),
    )
    sincronizar_parser.add_argument(
        "--limite",
        type=entero_positivo,
        default=settings.batch_limit,
        help="Maximo de eventos por ronda. No es el maximo de intentos.",
    )
    sincronizar_parser.add_argument(
        "--max-intentos",
        type=entero_positivo,
        default=settings.max_attempts,
        help=(
            "Total de intentos por evento, **incluido el primero** (por omision "
            f"{settings.max_attempts}). Solo se aplica a eventos que "
            "todavia no adoptaron una politica."
        ),
    )
    sincronizar_parser.add_argument(
        "--espera-base",
        type=numero_positivo,
        default=settings.base_delay_seconds,
        help=(
            "Segundos de la primera espera. Las siguientes se duplican hasta el "
            "techo."
        ),
    )
    sincronizar_parser.add_argument(
        "--espera-maxima",
        type=numero_positivo,
        default=settings.max_delay_seconds,
        help="Techo de la espera, en segundos.",
    )

    traza_parser = ordenes.add_parser(
        "traza",
        help=(
            "Recorrido completo de un evento: captura, cada intento, "
            "confirmacion y sincronizacion. Nunca imprime el paquete."
        ),
    )
    grupo = traza_parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument(
        "clave",
        nargs="?",
        help="Idempotency-Key del evento, que es tambien su id de correlacion.",
    )
    grupo.add_argument(
        "--id-outbox", type=entero_positivo, help="Identificador local del evento."
    )

    return parser


def _abrir(ruta: Path, settings: EdgeSettings):
    """Conexion al archivo indicado, creando solo su carpeta."""
    preparar_directorio(ruta)
    return conectar(ruta, espera_de_bloqueo_ms=settings.busy_timeout_ms)


def orden_init(ruta: Path, settings: EdgeSettings) -> int:
    with _abrir(ruta, settings) as conexion:
        anterior = leer_version(conexion)
        escrito = inicializar(conexion)
    if not escrito:
        print("El almacenamiento local ya existia y es compatible.")
    elif anterior == VERSION_ANTERIOR:
        print(
            "Almacenamiento local actualizado a la version actual. Los eventos "
            "guardados se conservaron; los intentos anteriores se cuentan como "
            "heredados y no tienen historial detallado."
        )
    else:
        print("Almacenamiento local creado.")
    return CODIGO_DE_EXITO


def orden_capturar(ruta_base: Path, ruta_paquete: Path, settings: EdgeSettings) -> int:
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

    with _abrir(ruta_base, settings) as conexion:
        inicializar(conexion)
        registro = capturar(conexion, paquete)

    # Identificadores locales y la clave, que no es un dato clinico. Nunca el
    # paquete.
    print("Paquete capturado localmente. No se ha contactado a la API.")
    print(f"  id_outbox        : {registro.id_outbox}")
    print(f"  Idempotency-Key  : {registro.clave}")
    return CODIGO_DE_EXITO


def orden_estado(ruta: Path, settings: EdgeSettings) -> int:
    with _abrir(ruta, settings) as conexion:
        inicializar(conexion)
        actual = resumen(conexion)
    print("Outbox del nodo edge:")
    print(f"  PENDIENTE                 : {actual.pendientes}")
    print(f"  ENVIADO                   : {actual.enviados}")
    print(f"  FALLIDO (reintentable)    : {actual.fallidos_reintentables}")
    print(f"  FALLIDO (requiere revision): {actual.fallidos_en_revision}")
    print(f"  total                     : {actual.total}")
    return CODIGO_DE_EXITO


def _cliente_http(settings: EdgeSettings):
    return httpx.Client(
        base_url=settings.api_base_url, timeout=settings.http_timeout
    )


def orden_enviar(ruta: Path, limite: int, settings: EdgeSettings) -> int:
    politica = settings.politica()
    with _abrir(ruta, settings) as conexion:
        inicializar(conexion)
        with _cliente_http(settings) as http:
            pasada = ejecutar_pasada(
                conexion, ClienteEdge(http), limite=limite, politica=politica
            )

    print("Ronda de envio terminada.")
    print(f"  seleccionados             : {pasada.seleccionados}")
    print(f"  intentos reclamados       : {pasada.reclamados}")
    print(f"  entregados                : {pasada.entregados}")
    print(f"  reintentables             : {pasada.reintentables}")
    print(f"  rechazados                : {pasada.rechazados}")
    print(f"  agotados                  : {pasada.agotados}")
    print(f"  ya resueltos por otro     : {pasada.ya_entregados}")
    if pasada.detenida_por_transporte:
        print(
            "  La ronda se detuvo: la API no esta accesible. Los eventos "
            "conservan su clave y pueden reintentarse."
        )
    return CODIGO_DE_EXITO


def orden_sincronizar(
    ruta: Path,
    limite: int,
    max_intentos: int,
    espera_base: float,
    espera_maxima: float,
    settings: EdgeSettings,
) -> int:
    politica = PoliticaDeReintentos(
        max_attempts=max_intentos,
        base_delay_seconds=espera_base,
        max_delay_seconds=espera_maxima,
        batch_limit=limite,
        http_timeout=settings.http_timeout,
    )

    with _abrir(ruta, settings) as conexion:
        inicializar(conexion)
        with _cliente_http(settings) as http:
            informe = sincronizar(conexion, ClienteEdge(http), politica=politica)

    censo = informe.censo
    print("Sincronizacion terminada.")
    print(f"  eventos de esta ejecucion : hasta id_outbox {informe.id_maximo}")
    print(f"  rondas                    : {informe.rondas}")
    print(
        f"  esperas                   : {informe.esperas} "
        f"({informe.segundos_esperados:.1f} s en total)"
    )
    print(f"  pausas por transporte     : {informe.pausas_por_transporte}")
    print(f"  intentos reconciliados    : {informe.intentos_reconciliados}")
    print(f"  entregados                : {informe.entregados}")
    print(f"  reintentables             : {informe.reintentables}")
    print(f"  rechazados                : {informe.rechazados}")
    print(f"  agotados                  : {informe.agotados}")
    print(f"  resultados tardios        : {informe.tardios_registrados}")
    print("Estado final de la cola:")
    print(f"  ENVIADO                   : {censo.enviados}")
    print(f"  requieren revision        : {censo.requieren_revision}")
    print(f"  bloqueados                : {censo.bloqueados_por_configuracion}")
    print(f"  elegibles ahora           : {censo.elegibles_ahora}")
    print(f"  programados               : {censo.programados}")

    if informe.codigo_de_salida == CODIGO_REVISION:
        print(
            "Quedan eventos que necesitan una revision humana. Consulta "
            "'traza <clave>' para ver que ocurrio con cada uno."
        )
    elif informe.codigo_de_salida == CODIGO_ANOMALIA:
        print(
            "La ejecucion se detuvo por una condicion interna inesperada: habia "
            "trabajo elegible que no pudo intentarse.",
            file=sys.stderr,
        )
    return informe.codigo_de_salida


def _situacion(traza) -> str:
    """Una frase que distingue los siete desenlaces posibles."""
    if traza.estado == "ENVIADO":
        reproducido = traza.reproducido
        if reproducido is None:
            return "confirmado antes de SCRUM-65 (sin historial de intentos)"
        return (
            "confirmado mediante replay"
            if reproducido
            else "confirmado en su primera aceptacion"
        )
    if traza.requiere_revision:
        return {
            "AGOTAMIENTO": "agotado: consumio todos sus intentos",
            "AGOTAMIENTO_HEREDADO": (
                "agotado por herencia: sus intentos de SCRUM-64 alcanzan el limite"
            ),
            "RECHAZO_PERMANENTE": "rechazado permanentemente por la API",
        }.get(traza.motivo_revision or "", "requiere revision")
    if traza.proximo_intento_en is not None:
        return "en espera de un nuevo intento"
    if traza.intentos == 0:
        return "pendiente, todavia no intentado"
    return "pendiente de un nuevo intento"


def orden_traza(
    ruta: Path, clave: str | None, id_outbox: int | None, settings: EdgeSettings
) -> int:
    with _abrir(ruta, settings) as conexion:
        inicializar(conexion)
        traza = leer_traza(conexion, clave=clave, id_outbox=id_outbox)

    if traza is None:
        print("No hay ningun evento con ese identificador.", file=sys.stderr)
        return CODIGO_DE_ERROR

    limite = traza.max_intentos_aplicado
    heredados = traza.intentos_heredados

    print(f"correlation_id      : {traza.correlation_id}")
    print(f"id_outbox           : {traza.id_outbox}")
    print(f"estado              : {traza.estado}")
    print(f"situacion           : {_situacion(traza)}")
    print(f"capturado_en        : {traza.capturado_en}")
    print(
        "intentos            : "
        + f"{traza.intentos}"
        + (f" de {limite}" if limite is not None else " (sin politica adoptada)")
        + (
            f"  ({heredados} heredados de SCRUM-64, sin historial detallado)"
            if heredados
            else ""
        )
    )
    print(f"proximo_intento_en  : {traza.proximo_intento_en or '-'}")
    print(f"confirmado_en       : {traza.confirmado_en or 'no medido'}")
    print(f"sincronizado_en     : {traza.sincronizado_en or '-'}")
    print(f"id_sesion remoto    : {traza.id_sesion_remota if traza.id_sesion_remota is not None else '-'}")
    print(f"ids_lectura remotos : {traza.ids_lectura_remotos or '-'}")

    if traza.ultimo_error:
        codigo = (
            f"http {traza.ultimo_http}"
            if traza.ultimo_http is not None
            else "sin codigo HTTP: el intento no obtuvo respuesta"
        )
        print(f"ultimo resultado    : {codigo}")
        print(f"                      {traza.ultimo_error}")

    print("secuencia de intentos:")
    if heredados:
        print(
            f"  1-{heredados}".ljust(22)
            + "sin historial disponible: capturados antes de SCRUM-65"
        )
    if not traza.intentos_registrados:
        if not heredados:
            print("  (todavia no se ha intentado ningun envio)")
    for intento in traza.intentos_registrados:
        partes = [intento.iniciado_en]
        if intento.resultado is None:
            partes.append(
                "sin resultado (proceso interrumpido)"
                if intento.reconciliado_en
                else "en curso"
            )
        else:
            partes.append(intento.resultado)
            if intento.codigo_http is not None:
                partes.append(f"http {intento.codigo_http}")
            if intento.reproducido is not None:
                partes.append("replay" if intento.reproducido else "primera aceptacion")
        if intento.reconciliado_en:
            partes.append(f"reconciliado {intento.reconciliado_en}")
        if intento.confirmo_transicion:
            partes.append("aplico la transicion local")
        if intento.demora_programada_s is not None:
            partes.append(f"espera {intento.demora_programada_s:g} s")
        if intento.error:
            partes.append(intento.error)
        print(f"  {intento.numero}".ljust(22) + " | ".join(partes))

    return CODIGO_DE_EXITO


def main(argv: list[str] | None = None) -> int:
    """El limite controlado del comando.

    **La configuracion se carga aqui dentro, y el parser se construye aqui
    dentro.** Antes, ``EdgeSettings`` se instanciaba al importar el modulo y el
    parser se construia antes del ``try``, asi que un ``EDGE_MAX_ATTEMPTS=abc``
    reventaba con un ``ValidationError`` de Pydantic --con su traceback-- en un
    punto donde nadie podia convertirlo en un mensaje y un codigo de salida. Los
    valores por omision que publica ``--help`` salen de esa configuracion, de
    modo que construir el parser tambien puede fallar por el entorno y tambien
    tiene que estar cubierto.

    Solo se capturan las tres excepciones propias del proyecto; nunca
    ``Exception``. Y ``SystemExit`` --lo que argparse lanza ante un argumento mal
    escrito-- no deriva de ``Exception``, asi que atraviesa este bloque intacto y
    argparse conserva su comportamiento y su codigo 2 de siempre.
    """
    try:
        settings = cargar_settings_edge()
        argumentos = construir_parser(settings).parse_args(argv)

        if argumentos.orden == "init":
            return orden_init(argumentos.base, settings)
        if argumentos.orden == "capturar":
            return orden_capturar(argumentos.base, argumentos.ruta, settings)
        if argumentos.orden == "estado":
            return orden_estado(argumentos.base, settings)
        if argumentos.orden == "enviar":
            return orden_enviar(argumentos.base, argumentos.limite, settings)
        if argumentos.orden == "sincronizar":
            return orden_sincronizar(
                argumentos.base,
                argumentos.limite,
                argumentos.max_intentos,
                argumentos.espera_base,
                argumentos.espera_maxima,
                settings,
            )
        return orden_traza(
            argumentos.base, argumentos.clave, argumentos.id_outbox, settings
        )

    except (ErrorDeAlmacenamiento, ErrorDeCaptura, ConfiguracionInvalida) as error:
        # ``detalle`` es texto escrito por este proyecto: no lleva valores del
        # paquete, ni rutas ajenas, ni mensajes del driver, ni el diagnostico
        # completo de Pydantic.
        print(f"Error: {error.detalle}", file=sys.stderr)
        return CODIGO_DE_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
