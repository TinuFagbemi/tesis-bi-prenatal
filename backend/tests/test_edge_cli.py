"""El comando del simulador: codigos de salida, salida en pantalla y privacidad.

El CLI se carga por ruta, igual que hace ``test_load_mock_data.py`` con el
cargador de SCRUM-61: la logica vive en ``app.edge`` y se prueba directamente,
asi que lo unico que queda por comprobar aqui es la capa de comando --que los
argumentos se interpretan, que los errores se convierten en un codigo de salida
y que nada sensible llega a la pantalla--.

Las seis ordenes entran por ``main``. Las dos que trae SCRUM-65 --``sincronizar``
y ``traza``-- son ademas las unicas con frontera propia: ``sincronizar`` traduce
cuatro argumentos a una ``PoliticaDeReintentos`` y traduce un ``ResumenSincronizacion``
a un codigo de salida, y ``traza`` es la unica orden que imprime el historial de
un evento, de modo que es tambien la que mas puede filtrar. Nada de eso se veia
llamando a ``app.edge`` directamente.

Ninguna prueba de este archivo abre un subproceso ni toca la base configurada en
el entorno: todas usan ``--base`` sobre ``tmp_path``.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from app.edge import almacenamiento as alm
from app.edge import outbox
from app.edge.cliente import CABECERA_REPLAY
from app.edge.config import (
    ESPERA_DE_BLOQUEO_POR_OMISION,
    RUTA_SQLITE_POR_OMISION,
    TIMEOUT_HTTP_POR_OMISION,
    URL_API_POR_OMISION,
    cargar_settings_edge,
)
from app.edge.politica import (
    LIMITE_OPERACIONAL_SEGUNDOS,
    RESOLUCION_MINIMA_SEGUNDOS,
    ConfiguracionInvalida,
)
from app.edge.estados import EstadoEntrega, MotivoRevision
from tests.test_edge_captura import PAQUETE_DE_UNA_LECTURA

RAIZ = Path(__file__).resolve().parents[2]
RUTA_CLI = RAIZ / "scripts" / "edge_node.py"


@pytest.fixture(scope="module")
def cli():
    """El script cargado como modulo, sin lanzar un proceso aparte."""
    spec = importlib.util.spec_from_file_location("edge_node", RUTA_CLI)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture
def base(tmp_path):
    return tmp_path / "demo" / "nodo_edge.sqlite3"


@pytest.fixture
def paquete(tmp_path):
    ruta = tmp_path / "paquete.json"
    ruta.write_text(json.dumps(PAQUETE_DE_UNA_LECTURA), encoding="utf-8")
    return ruta


def ejecutar(cli, base, *argumentos):
    return cli.main(["--base", str(base), *argumentos])


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_crea_el_almacenamiento_y_su_carpeta(cli, base, capsys):
    assert not base.parent.exists()
    assert ejecutar(cli, base, "init") == 0
    assert base.exists()
    assert "creado" in capsys.readouterr().out


def test_init_es_idempotente(cli, base, capsys):
    ejecutar(cli, base, "init")
    capsys.readouterr()
    assert ejecutar(cli, base, "init") == 0
    assert "ya existia" in capsys.readouterr().out


def test_init_sobre_un_archivo_incompatible_devuelve_error(cli, base, capsys):
    base.parent.mkdir(parents=True)
    with alm.conectar(base) as conexion:
        conexion.execute("CREATE TABLE agenda (id INTEGER PRIMARY KEY)")

    assert ejecutar(cli, base, "init") == 1
    salida = capsys.readouterr()
    assert "Error:" in salida.err
    assert "ruta dedicada" in salida.err


# ---------------------------------------------------------------------------
# capturar
# ---------------------------------------------------------------------------


def test_capturar_guarda_el_paquete_sin_contactar_la_api(cli, base, paquete, capsys):
    assert ejecutar(cli, base, "capturar", str(paquete)) == 0

    salida = capsys.readouterr().out
    assert "No se ha contactado a la API" in salida

    with alm.conectar(base) as conexion:
        assert outbox.resumen(conexion).pendientes == 1


def test_capturar_imprime_la_clave_pero_nunca_el_paquete(cli, base, paquete, capsys):
    """La clave sí se imprime --no es un dato clínico--; el paquete nunca.

    La comprobación evita a propósito buscar valores numéricos sueltos como
    "90" o "97": la salida contiene el UUID de la clave, y dos dígitos
    hexadecimales cualesquiera aparecen dentro de él por azar en una parte
    apreciable de las ejecuciones, lo que haría inestable la prueba sin
    demostrar nada. Lo que se comprueba es lo que sí identifica al paquete: los
    nombres de sus campos, su vocabulario y su estructura JSON.
    """
    ejecutar(cli, base, "capturar", str(paquete))
    salida = capsys.readouterr().out

    assert "Idempotency-Key" in salida
    for valor in (
        "hr_valor",
        "spo2_valor",
        "id_embarazo",
        "id_tiempo_gest",
        "fecha_hora_captura",
        "SIGNOS_MATERNOS",
        "COMPLETADA",
    ):
        assert valor not in salida

    # Y no se filtra el cuerpo por otra vía: no hay JSON en la salida.
    assert "{" not in salida
    assert "lecturas" not in salida


def test_capturar_inicializa_el_almacenamiento_si_hace_falta(cli, base, paquete):
    assert not base.exists()
    assert ejecutar(cli, base, "capturar", str(paquete)) == 0


def test_capturar_un_archivo_inexistente_devuelve_error(cli, base, tmp_path, capsys):
    assert ejecutar(cli, base, "capturar", str(tmp_path / "no_existe.json")) == 1
    assert "no existe el archivo" in capsys.readouterr().err


def test_capturar_un_json_roto_no_imprime_su_contenido(cli, base, tmp_path, capsys):
    roto = tmp_path / "roto.json"
    roto.write_text('{"id_embarazo": 100, "hr_valor": 987654', encoding="utf-8")

    assert ejecutar(cli, base, "capturar", str(roto)) == 1
    error = capsys.readouterr().err
    assert "no contiene un JSON valido" in error
    assert "987654" not in error


def test_capturar_un_paquete_invalido_devuelve_error_saneado(cli, base, tmp_path, capsys):
    invalido = tmp_path / "invalido.json"
    paquete = json.loads(json.dumps(PAQUETE_DE_UNA_LECTURA))
    paquete["lecturas"][0]["hr_valor"] = 1234567
    invalido.write_text(json.dumps(paquete), encoding="utf-8")

    assert ejecutar(cli, base, "capturar", str(invalido)) == 1
    error = capsys.readouterr().err
    assert "hr_valor" in error
    assert "1234567" not in error


# ---------------------------------------------------------------------------
# estado
# ---------------------------------------------------------------------------


def test_estado_resume_sin_exponer_paquetes(cli, base, paquete, capsys):
    ejecutar(cli, base, "capturar", str(paquete))
    capsys.readouterr()

    assert ejecutar(cli, base, "estado") == 0
    salida = capsys.readouterr().out
    assert "PENDIENTE" in salida
    assert "ENVIADO" in salida
    for valor in ("hr_valor", "SIGNOS_MATERNOS", "Idempotency-Key"):
        assert valor not in salida


def test_estado_sobre_un_nodo_vacio_funciona(cli, base, capsys):
    assert ejecutar(cli, base, "estado") == 0
    assert "total" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# enviar
# ---------------------------------------------------------------------------


def _con_transporte(monkeypatch, cli, manejador):
    """Sustituye el cliente HTTP del comando por uno guionado."""

    class ClienteGuionado(httpx.Client):
        def __init__(self, **argumentos):
            argumentos["transport"] = httpx.MockTransport(manejador)
            super().__init__(**argumentos)

    monkeypatch.setattr(cli.httpx, "Client", ClienteGuionado)


def test_enviar_entrega_y_reporta(cli, base, paquete, capsys, monkeypatch):
    ejecutar(cli, base, "capturar", str(paquete))
    capsys.readouterr()

    _con_transporte(
        monkeypatch,
        cli,
        lambda peticion: httpx.Response(
            201,
            json={"id_sesion": 832, "lecturas_creadas": 1, "ids_lectura": [1280]},
            headers={CABECERA_REPLAY: "false"},
        ),
    )

    assert ejecutar(cli, base, "enviar") == 0
    salida = capsys.readouterr().out
    assert "entregados                : 1" in salida

    with alm.conectar(base) as conexion:
        assert outbox.resumen(conexion).enviados == 1


def test_enviar_con_la_api_caida_avisa_y_conserva_el_evento(
    cli, base, paquete, capsys, monkeypatch
):
    ejecutar(cli, base, "capturar", str(paquete))
    capsys.readouterr()

    def caido(peticion):
        raise httpx.ConnectError("sin ruta", request=peticion)

    _con_transporte(monkeypatch, cli, caido)

    assert ejecutar(cli, base, "enviar") == 0
    salida = capsys.readouterr().out
    assert "La ronda se detuvo" in salida
    assert "conservan su clave" in salida

    with alm.conectar(base) as conexion:
        assert outbox.resumen(conexion).fallidos_reintentables == 1


def test_enviar_respeta_el_limite(cli, base, paquete, capsys, monkeypatch):
    for _ in range(3):
        ejecutar(cli, base, "capturar", str(paquete))
    capsys.readouterr()

    _con_transporte(
        monkeypatch,
        cli,
        lambda peticion: httpx.Response(
            201,
            json={"id_sesion": 1, "lecturas_creadas": 1, "ids_lectura": [2]},
            headers={CABECERA_REPLAY: "false"},
        ),
    )

    assert ejecutar(cli, base, "enviar", "--limite", "2") == 0
    assert "seleccionados             : 2" in capsys.readouterr().out


def test_enviar_sobre_un_nodo_vacio_no_falla(cli, base, capsys, monkeypatch):
    _con_transporte(monkeypatch, cli, lambda peticion: httpx.Response(500))
    assert ejecutar(cli, base, "enviar") == 0
    assert "seleccionados             : 0" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# sincronizar
#
# Es la orden con mas frontera propia: traduce cuatro argumentos a una
# ``PoliticaDeReintentos`` y traduce un ``ResumenSincronizacion`` a un codigo de
# salida. Ninguna de las dos traducciones se ve llamando a ``app.edge``
# directamente, y las dos pueden equivocarse en silencio --un argumento que no
# se propaga, un codigo 2 que sale como 0--.
# ---------------------------------------------------------------------------


def _espiar_politica(monkeypatch, cli):
    """Observa la politica que el CLI construye, sin cambiar lo que hace.

    El espia delega en la funcion real: lo unico que anade es la lista de
    politicas recibidas, de modo que la prueba puede afirmar sobre la traduccion
    de los argumentos sin dejar de ejercer la sincronizacion de verdad.
    """
    politicas = []
    real = cli.sincronizar

    def espia(conexion, cliente, **resto):
        politicas.append(resto.get("politica"))
        return real(conexion, cliente, **resto)

    monkeypatch.setattr(cli, "sincronizar", espia)
    return politicas


def _respuesta_creada(reproducido: bool = False):
    return lambda peticion: httpx.Response(
        201,
        json={"id_sesion": 832, "lecturas_creadas": 1, "ids_lectura": [1280]},
        headers={CABECERA_REPLAY: "true" if reproducido else "false"},
    )


def _clave_de(base) -> str:
    with alm.conectar(base) as conexion:
        fila = conexion.execute(
            "SELECT clave_idempotencia FROM outbox ORDER BY id_outbox LIMIT 1"
        ).fetchone()
    return fila["clave_idempotencia"]


# --- El parser y la construccion de la politica ----------------------------


def test_sincronizar_traduce_sus_cuatro_argumentos_a_la_politica(
    cli, base, capsys, monkeypatch
):
    """Cada bandera llega a su campo, y ninguna se queda por el camino.

    Sobre un nodo vacio a proposito: lo que se comprueba es la traduccion de
    argumentos, no la sincronizacion, y asi no hay una sola espera real.
    """
    politicas = _espiar_politica(monkeypatch, cli)
    _con_transporte(monkeypatch, cli, _respuesta_creada())

    codigo = ejecutar(
        cli,
        base,
        "sincronizar",
        "--limite", "7",
        "--max-intentos", "4",
        "--espera-base", "0.5",
        "--espera-maxima", "9.5",
    )
    capsys.readouterr()

    assert codigo == 0
    assert len(politicas) == 1
    politica = politicas[0]
    assert politica.batch_limit == 7
    assert politica.max_attempts == 4
    assert politica.base_delay_seconds == 0.5
    assert politica.max_delay_seconds == 9.5
    # El timeout no es un argumento: sale de la configuracion, y de el sale el
    # lease.
    assert politica.http_timeout == TIMEOUT_HTTP_POR_OMISION
    assert politica.duracion_del_lease == 4 * TIMEOUT_HTTP_POR_OMISION + 30.0


def test_sincronizar_sin_argumentos_usa_los_valores_de_la_configuracion(
    cli, base, capsys, monkeypatch
):
    """Los valores por omision de ``--help`` y los de la politica son los mismos."""
    politicas = _espiar_politica(monkeypatch, cli)
    _con_transporte(monkeypatch, cli, _respuesta_creada())

    assert ejecutar(cli, base, "sincronizar") == 0
    capsys.readouterr()

    assert politicas[0] == cargar_settings_edge().politica()


@pytest.mark.parametrize(
    "argumento, valor",
    [
        ("--espera-base", "5e-324"),
        ("--espera-base", "0.0000001"),
        ("--espera-base", "86401"),
        ("--espera-maxima", "1e308"),
        ("--espera-maxima", "86400.000000001"),
    ],
)
def test_sincronizar_rechaza_una_duracion_no_programable_con_codigo_uno(
    cli, base, capsys, monkeypatch, argumento, valor
):
    """``numero_positivo`` los admite; la politica no, y eso llega hasta aqui.

    Argparse solo exige finito y mayor que 0, asi que ``5e-324`` y ``1e308``
    atraviesan el parser. Los para ``PoliticaDeReintentos``, y como se construye
    dentro del ``try`` de ``main`` el resultado es una frase y un codigo 1, no un
    traceback. La base **no llega a crearse**: la politica se construye antes de
    abrirla.
    """
    _con_transporte(monkeypatch, cli, _respuesta_creada())

    assert ejecutar(cli, base, "sincronizar", argumento, valor) == 1

    capturado = capsys.readouterr()
    assert capturado.out == ""
    assert capturado.err.startswith("Error: ")
    assert "Traceback" not in capturado.err
    assert not base.exists()


def test_sincronizar_rechaza_un_timeout_cuyo_lease_no_cabe(
    cli, base, capsys, monkeypatch
):
    """El timeout no es un argumento, asi que este caso entra por el entorno."""
    monkeypatch.setenv("EDGE_HTTP_TIMEOUT", "30000")
    _con_transporte(monkeypatch, cli, _respuesta_creada())

    assert ejecutar(cli, base, "sincronizar") == 1

    error = capsys.readouterr().err
    assert "duracion_del_lease" in error
    assert "http_timeout" in error
    assert not base.exists()


def test_sincronizar_acepta_los_dos_bordes_del_intervalo(
    cli, base, capsys, monkeypatch
):
    """Y los bordes inclusivos si pasan, hasta construir la politica."""
    politicas = _espiar_politica(monkeypatch, cli)
    _con_transporte(monkeypatch, cli, _respuesta_creada())

    assert (
        ejecutar(
            cli,
            base,
            "sincronizar",
            "--espera-base", repr(RESOLUCION_MINIMA_SEGUNDOS),
            "--espera-maxima", repr(LIMITE_OPERACIONAL_SEGUNDOS),
        )
        == 0
    )
    capsys.readouterr()

    assert politicas[0].base_delay_seconds == RESOLUCION_MINIMA_SEGUNDOS
    assert politicas[0].max_delay_seconds == LIMITE_OPERACIONAL_SEGUNDOS


@pytest.mark.parametrize(
    "argumento, valor",
    [
        ("--limite", "0"),
        ("--limite", "-1"),
        ("--limite", "dos"),
        ("--max-intentos", "0"),
        ("--espera-base", "0"),
        ("--espera-base", "-1"),
        ("--espera-base", "nan"),
        ("--espera-maxima", "inf"),
    ],
)
def test_sincronizar_conserva_el_comportamiento_de_argparse(
    cli, base, argumento, valor
):
    """Lo que rechaza el parser sigue saliendo por ``SystemExit`` y codigo 2.

    Son dos fronteras distintas y conviene que se noten distintas: un argumento
    mal formado es un error de uso --argparse, codigo 2, ayuda por stderr-- y una
    duracion imposible es un error de configuracion --codigo 1, una frase--.
    """
    with pytest.raises(SystemExit) as salida:
        ejecutar(cli, base, "sincronizar", argumento, valor)
    assert salida.value.code == 2


# --- El despacho, los codigos de salida y el informe ------------------------


def test_sincronizar_entrega_y_devuelve_cero(cli, base, paquete, capsys, monkeypatch):
    ejecutar(cli, base, "capturar", str(paquete))
    capsys.readouterr()
    _con_transporte(monkeypatch, cli, _respuesta_creada())

    assert ejecutar(cli, base, "sincronizar") == 0

    salida = capsys.readouterr().out
    assert "Sincronizacion terminada." in salida
    assert "entregados                : 1" in salida
    assert "ENVIADO                   : 1" in salida

    with alm.conectar(base) as conexion:
        assert outbox.resumen(conexion).enviados == 1


def test_sincronizar_imprime_el_informe_completo(cli, base, capsys, monkeypatch):
    """Las quince lineas del informe, sobre un nodo vacio.

    Se comprueban aqui, sin eventos, porque lo que se afirma es la **forma** del
    informe: que ninguna linea desaparezca al cambiar el resumen.
    """
    _con_transporte(monkeypatch, cli, _respuesta_creada())
    assert ejecutar(cli, base, "sincronizar") == 0

    salida = capsys.readouterr().out
    for etiqueta in (
        "eventos de esta ejecucion",
        "rondas",
        "esperas",
        "pausas por transporte",
        "intentos reconciliados",
        "entregados",
        "reintentables",
        "rechazados",
        "agotados",
        "resultados tardios",
        "Estado final de la cola:",
        "ENVIADO",
        "requieren revision",
        "bloqueados",
        "elegibles ahora",
        "programados",
    ):
        assert etiqueta in salida, etiqueta


def test_sincronizar_agota_los_intentos_y_devuelve_codigo_dos(
    cli, base, paquete, capsys, monkeypatch
):
    """Un 500 constante: se reintenta, se agota, y el codigo pide una persona.

    Las esperas se dejan en un milisegundo para que la prueba las gaste de
    verdad en lugar de simularlas: lo que se esta comprobando es que el CLI
    espera, no cuanto.
    """
    ejecutar(cli, base, "capturar", str(paquete))
    capsys.readouterr()
    _con_transporte(monkeypatch, cli, lambda peticion: httpx.Response(500))

    codigo = ejecutar(
        cli,
        base,
        "sincronizar",
        "--max-intentos", "3",
        "--espera-base", "0.001",
        "--espera-maxima", "0.001",
    )

    assert codigo == 2
    salida = capsys.readouterr().out
    assert "agotados                  : 1" in salida
    assert "requieren revision        : 1" in salida
    assert "revision humana" in salida
    assert "traza <clave>" in salida

    with alm.conectar(base) as conexion:
        evento = outbox.leer_evento(conexion, 1)
    assert evento["estado"] == EstadoEntrega.FALLIDO.value
    assert evento["motivo_revision"] == MotivoRevision.AGOTAMIENTO.value
    assert evento["intentos"] == 3


def test_sincronizar_con_un_rechazo_permanente_no_gasta_reintentos(
    cli, base, paquete, capsys, monkeypatch
):
    """Un 422 se cierra en el primer intento, y aun asi el codigo es 2."""
    ejecutar(cli, base, "capturar", str(paquete))
    capsys.readouterr()
    _con_transporte(monkeypatch, cli, lambda peticion: httpx.Response(422, json={}))

    assert ejecutar(cli, base, "sincronizar") == 2

    salida = capsys.readouterr().out
    assert "rechazados                : 1" in salida
    assert "esperas                   : 0" in salida

    with alm.conectar(base) as conexion:
        evento = outbox.leer_evento(conexion, 1)
    assert evento["intentos"] == 1
    assert evento["motivo_revision"] == MotivoRevision.RECHAZO_PERMANENTE.value


def test_sincronizar_sobre_un_nodo_vacio_devuelve_cero(
    cli, base, capsys, monkeypatch
):
    _con_transporte(monkeypatch, cli, lambda peticion: httpx.Response(500))

    assert ejecutar(cli, base, "sincronizar") == 0
    salida = capsys.readouterr().out
    assert "rondas                    : 0" in salida
    assert "elegibles ahora           : 0" in salida


def test_sincronizar_inicializa_el_almacenamiento_si_hace_falta(
    cli, base, capsys, monkeypatch
):
    _con_transporte(monkeypatch, cli, _respuesta_creada())
    assert not base.exists()

    assert ejecutar(cli, base, "sincronizar") == 0
    capsys.readouterr()
    assert base.exists()


def test_sincronizar_nunca_imprime_el_paquete(
    cli, base, paquete, capsys, monkeypatch
):
    """Ni al entregar, ni al fallar: el informe es de conteos, no de contenido."""
    ejecutar(cli, base, "capturar", str(paquete))
    capsys.readouterr()
    _con_transporte(monkeypatch, cli, lambda peticion: httpx.Response(422, json={}))

    ejecutar(cli, base, "sincronizar")
    capturado = capsys.readouterr()

    for valor in (
        "hr_valor",
        "spo2_valor",
        "id_embarazo",
        "id_tiempo_gest",
        "fecha_hora_captura",
        "SIGNOS_MATERNOS",
    ):
        assert valor not in capturado.out
        assert valor not in capturado.err
    assert "{" not in capturado.out


# ---------------------------------------------------------------------------
# traza
#
# La orden que mas puede filtrar: es la unica que imprime el historial completo
# de un evento --cada intento, cada error, cada instante--. Lo que sigue
# comprueba las dos formas de seleccionarlo, los tres desenlaces del comando y
# que ni el paquete ni el detalle remoto llegan a la pantalla.
# ---------------------------------------------------------------------------


def test_traza_por_clave_recorre_el_evento(cli, base, paquete, capsys, monkeypatch):
    ejecutar(cli, base, "capturar", str(paquete))
    _con_transporte(monkeypatch, cli, _respuesta_creada())
    ejecutar(cli, base, "sincronizar")
    clave = _clave_de(base)
    capsys.readouterr()

    assert ejecutar(cli, base, "traza", clave) == 0

    salida = capsys.readouterr().out
    assert f"correlation_id      : {clave}" in salida
    assert "estado              : ENVIADO" in salida
    assert "situacion           : confirmado en su primera aceptacion" in salida
    assert "secuencia de intentos:" in salida
    assert "ENTREGADO" in salida
    assert "aplico la transicion local" in salida


def test_traza_por_id_outbox_da_el_mismo_recorrido(
    cli, base, paquete, capsys, monkeypatch
):
    """Las dos formas de nombrar un evento devuelven exactamente lo mismo."""
    ejecutar(cli, base, "capturar", str(paquete))
    _con_transporte(monkeypatch, cli, _respuesta_creada())
    ejecutar(cli, base, "sincronizar")
    clave = _clave_de(base)
    capsys.readouterr()

    assert ejecutar(cli, base, "traza", clave) == 0
    por_clave = capsys.readouterr().out

    assert ejecutar(cli, base, "traza", "--id-outbox", "1") == 0
    por_id = capsys.readouterr().out

    assert por_clave == por_id


def test_traza_de_un_evento_recien_capturado_lo_dice(
    cli, base, paquete, capsys
):
    ejecutar(cli, base, "capturar", str(paquete))
    clave = _clave_de(base)
    capsys.readouterr()

    assert ejecutar(cli, base, "traza", clave) == 0

    salida = capsys.readouterr().out
    assert "estado              : PENDIENTE" in salida
    assert "situacion           : pendiente, todavia no intentado" in salida
    assert "(sin politica adoptada)" in salida
    assert "(todavia no se ha intentado ningun envio)" in salida
    assert "confirmado_en       : no medido" in salida


def test_traza_muestra_la_espera_programada_de_un_reintento(
    cli, base, paquete, capsys, monkeypatch
):
    """El historial dice cuanto se espero, que es la mitad de la trazabilidad."""
    ejecutar(cli, base, "capturar", str(paquete))
    _con_transporte(monkeypatch, cli, lambda peticion: httpx.Response(500))
    ejecutar(
        cli, base, "sincronizar",
        "--max-intentos", "2", "--espera-base", "0.001", "--espera-maxima", "0.001",
    )
    clave = _clave_de(base)
    capsys.readouterr()

    assert ejecutar(cli, base, "traza", clave) == 0

    salida = capsys.readouterr().out
    assert "estado              : FALLIDO" in salida
    assert "situacion           : agotado: consumio todos sus intentos" in salida
    assert "intentos            : 2 de 2" in salida
    assert "espera 0.001 s" in salida
    assert "http 500" in salida


@pytest.mark.parametrize("identificador", ["no-existe", "--id-outbox 99"])
def test_traza_de_un_evento_inexistente_devuelve_codigo_uno(
    cli, base, capsys, identificador
):
    assert ejecutar(cli, base, "traza", *identificador.split()) == 1

    capturado = capsys.readouterr()
    assert capturado.out == ""
    assert "No hay ningun evento con ese identificador." in capturado.err


def test_traza_exige_exactamente_un_identificador(cli, base):
    """Ni ninguno ni los dos: el grupo es excluyente y obligatorio."""
    with pytest.raises(SystemExit) as sin_ninguno:
        ejecutar(cli, base, "traza")
    assert sin_ninguno.value.code == 2

    with pytest.raises(SystemExit) as con_los_dos:
        ejecutar(cli, base, "traza", "alguna-clave", "--id-outbox", "1")
    assert con_los_dos.value.code == 2


def test_traza_nunca_imprime_el_paquete_ni_el_detalle_remoto(
    cli, base, paquete, capsys, monkeypatch
):
    """La orden que mas historial imprime es la que mas tiene que callar.

    El servidor devuelve un ``detail`` que menciona el paquete; la traza tiene
    que poder contar que hubo un 422 sin repetir una palabra de el. Se buscan
    los nombres de los campos y el vocabulario del paquete, no digitos sueltos:
    la salida lleva un UUID y varios instantes, y una cifra corta aparece dentro
    de ellos por azar en una parte apreciable de las ejecuciones.
    """
    ejecutar(cli, base, "capturar", str(paquete))
    _con_transporte(
        monkeypatch,
        cli,
        lambda peticion: httpx.Response(
            422,
            json={
                "detail": (
                    "No existe un embarazo con id_embarazo=999999. "
                    "hr_valor fuera de rango en SIGNOS_MATERNOS; "
                    "SELECT * FROM operacional.embarazo"
                )
            },
        ),
    )
    ejecutar(cli, base, "sincronizar")
    clave = _clave_de(base)
    capsys.readouterr()

    assert ejecutar(cli, base, "traza", clave) == 0
    capturado = capsys.readouterr()

    for valor in (
        "id_embarazo",
        "hr_valor",
        "spo2_valor",
        "id_tiempo_gest",
        "fecha_hora_captura",
        "SIGNOS_MATERNOS",
        "SELECT",
        "operacional.embarazo",
        "999999",
    ):
        assert valor not in capturado.out, valor
        assert valor not in capturado.err, valor

    # Y lo util si esta: el codigo, la clasificacion y la forma del detalle.
    assert "http 422" in capturado.out
    assert "RECHAZADO" in capturado.out
    assert "rechazado permanentemente por la API" in capturado.out


def test_traza_inicializa_el_almacenamiento_si_hace_falta(cli, base, capsys):
    """Sobre un nodo que no existe: crea el archivo y responde que no hay nada."""
    assert not base.exists()

    assert ejecutar(cli, base, "traza", "cualquier-clave") == 1
    capsys.readouterr()
    assert base.exists()


# ---------------------------------------------------------------------------
# Forma del comando
# ---------------------------------------------------------------------------


def test_el_comando_exige_una_orden(cli):
    with pytest.raises(SystemExit):
        cli.main([])


# ---------------------------------------------------------------------------
# La configuracion se carga dentro del limite controlado
#
# Habia un ``settings_edge = EdgeSettings()`` a nivel de modulo. Con
# ``EDGE_MAX_ATTEMPTS=abc``, Pydantic lanzaba ValidationError **durante el
# import**: antes de que ``main()`` existiera, antes de su ``try``, y por tanto
# antes de que nada pudiera convertirlo en un mensaje y un codigo de salida. Lo
# que veia la usuaria era un traceback.
# ---------------------------------------------------------------------------

VARIABLES_INVALIDAS = [
    "EDGE_MAX_ATTEMPTS",
    "EDGE_BASE_DELAY_SECONDS",
    "EDGE_BUSY_TIMEOUT_MS",
    "EDGE_HTTP_TIMEOUT",
]


@pytest.mark.parametrize("variable", VARIABLES_INVALIDAS)
def test_una_variable_de_entorno_mal_tipada_devuelve_codigo_1(
    cli, base, variable, monkeypatch, capsys
):
    monkeypatch.setenv(variable, "abc")

    assert ejecutar(cli, base, "estado") == 1

    error = capsys.readouterr().err
    assert error.startswith("Error: ")
    assert variable in error
    assert "EDGE_*" in error


@pytest.mark.parametrize("variable", VARIABLES_INVALIDAS)
def test_una_configuracion_invalida_no_filtra_el_diagnostico_de_pydantic(
    cli, base, variable, monkeypatch, capsys
):
    """Ni traceback, ni el valor rechazado, ni el informe completo de Pydantic.

    El mensaje se reconstruye con el campo y el tipo de error --lo unico que es
    esquema y no dato--, porque una variable de entorno puede llevar cualquier
    cosa: una ruta con el nombre de alguien, un token pegado por error.
    """
    monkeypatch.setenv(variable, "valor-secreto-que-no-debe-verse")

    assert ejecutar(cli, base, "estado") == 1

    error = capsys.readouterr().err
    assert "valor-secreto-que-no-debe-verse" not in error
    for prohibido in (
        "Traceback",
        "validation error",
        "input_value",
        "input_type",
        "For further information",
        "pydantic",
    ):
        assert prohibido not in error
    # Una sola linea: nada de volcados.
    assert len(error.strip().splitlines()) == 1


def test_importar_el_paquete_no_construye_la_configuracion(monkeypatch):
    """Con el entorno roto, importar sigue siendo seguro.

    Se recargan los modulos a proposito: si alguno volviera a construir un
    ``EdgeSettings`` al importarse, esto fallaria aqui y no en mitad de un
    comando.
    """
    import importlib

    import app.edge
    import app.edge.config

    monkeypatch.setenv("EDGE_MAX_ATTEMPTS", "abc")
    importlib.reload(app.edge.config)
    importlib.reload(app.edge)

    assert not hasattr(app.edge.config, "settings_edge")
    assert not hasattr(app.edge, "settings_edge")
    assert "cargar_settings_edge" in app.edge.__all__

    # Y la carga explicita si falla, de forma controlada.
    with pytest.raises(ConfiguracionInvalida):
        app.edge.config.cargar_settings_edge()

    monkeypatch.delenv("EDGE_MAX_ATTEMPTS")
    importlib.reload(app.edge.config)
    importlib.reload(app.edge)


def test_una_configuracion_valida_conserva_los_valores_por_omision():
    """El comportamiento normal no cambia al mover la carga."""
    from app.edge.politica import (
        BASE_DELAY_POR_OMISION,
        BATCH_LIMIT_POR_OMISION,
        MAX_ATTEMPTS_POR_OMISION,
        MAX_DELAY_POR_OMISION,
    )

    settings = cargar_settings_edge()
    assert settings.max_attempts == MAX_ATTEMPTS_POR_OMISION
    assert settings.base_delay_seconds == BASE_DELAY_POR_OMISION
    assert settings.max_delay_seconds == MAX_DELAY_POR_OMISION
    assert settings.batch_limit == BATCH_LIMIT_POR_OMISION
    assert settings.http_timeout == TIMEOUT_HTTP_POR_OMISION
    assert settings.busy_timeout_ms == ESPERA_DE_BLOQUEO_POR_OMISION
    assert settings.sqlite_path == RUTA_SQLITE_POR_OMISION
    assert settings.api_base_url == URL_API_POR_OMISION
    # Y produce una politica utilizable.
    assert settings.politica().max_attempts == MAX_ATTEMPTS_POR_OMISION


def test_un_argumento_mal_escrito_conserva_el_comportamiento_de_argparse(cli, base):
    """``SystemExit`` no deriva de ``Exception``: atraviesa el try intacto."""
    with pytest.raises(SystemExit) as salida:
        ejecutar(cli, base, "orden-que-no-existe")
    assert salida.value.code == 2


def test_el_comando_no_acepta_credenciales_ni_url_de_base_de_datos(cli):
    ayuda = cli.construir_parser(cargar_settings_edge()).format_help().lower()
    for prohibido in ("password", "contrasena", "database_url", "--url", "token"):
        assert prohibido not in ayuda


def test_el_comando_no_abre_conexiones_a_postgresql():
    """El edge llega al servidor por la API; nunca por el motor de la base."""
    import ast
    import inspect

    fuente = RUTA_CLI.read_text(encoding="utf-8")
    arbol = ast.parse(fuente)
    importados = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            importados.update(alias.name.split(".")[0] for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            importados.add(nodo.module.split(".")[0])

    assert "sqlalchemy" not in importados
    assert "psycopg" not in importados
    assert "app.db" not in fuente


# ---------------------------------------------------------------------------
# Nada de esto queda versionado
# ---------------------------------------------------------------------------


def test_la_ruta_por_omision_esta_ignorada_por_git():
    """La base de demostracion es un artefacto local, no contenido del repositorio."""
    from app.edge.config import RUTA_SQLITE_POR_OMISION

    reglas = (RAIZ / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "data/edge/" in reglas

    relativa = RUTA_SQLITE_POR_OMISION.relative_to(RAIZ).as_posix()
    assert relativa.startswith("data/edge/")


PATRONES_SQLITE = ("*.sqlite3", "*.sqlite", "*.db")


def _bases_en_el_repositorio() -> set:
    """Archivos SQLite que hay ahora mismo bajo la raiz del repositorio."""
    encontradas = set()
    for patron in PATRONES_SQLITE:
        encontradas.update(
            ruta
            for ruta in RAIZ.rglob(patron)
            if ".venv" not in ruta.parts and ".git" not in ruta.parts
        )
    return encontradas


def test_el_cli_no_crea_bases_sqlite_en_el_repositorio(cli, base, paquete):
    """Ejercer el comando no crea ninguna base dentro del repositorio.

    **Corrige un defecto de aislamiento heredado de SCRUM-64.** La version
    anterior exigia que no existiera *ningun* archivo SQLite bajo la raiz, y eso
    contradice lo que el propio proyecto documenta: ``data/edge/`` esta en
    ``.gitignore`` precisamente porque ahi viven las bases locales de
    demostracion. La prueba fallaba en cuanto alguien ejecutaba la demostracion
    manual del README --con `data/edge/demo_scrum64.sqlite3`, por ejemplo-- y
    pasaba solo sobre un checkout recien clonado. Es decir, castigaba usar el
    proyecto como esta documentado.

    Lo que de verdad importa son dos cosas, y las dos se comprueban aqui y en la
    prueba siguiente --``test_ninguna_base_sqlite_esta_versionada``--: que el
    comando escriba **donde se le dice** y no siembre bases por el
    repositorio, y que ninguna base acabe versionada.

    Se compara el conjunto **antes y despues**, en vez de exigir el conjunto
    vacio, asi que un artefacto local preexistente y correctamente ignorado no
    afecta al resultado, pero uno nuevo creado por el comando si.
    """
    antes = _bases_en_el_repositorio()

    ejecutar(cli, base, "capturar", str(paquete))

    # El comando escribio de verdad, pero fuera del repositorio.
    assert base.exists(), "la base indicada con --base deberia haberse creado"
    assert RAIZ not in base.parents

    nuevas = _bases_en_el_repositorio() - antes
    assert nuevas == set(), f"el comando creo bases dentro del repositorio: {nuevas}"


def test_ninguna_base_sqlite_esta_versionada(cli, base, paquete):
    """La otra mitad: lo que Git sigue. Aqui el conjunto vacio si es exigible.

    Un archivo local ignorado es un artefacto; uno *versionado* seria contenido
    del repositorio, y una base SQLite nunca lo es. Se le pregunta a Git en vez
    de reimplementar las reglas de ``.gitignore``, que es donde una comprobacion
    casera se equivocaria.
    """
    import subprocess

    ejecutar(cli, base, "capturar", str(paquete))

    seguidos = subprocess.run(
        ["git", "ls-files", "--", *PATRONES_SQLITE],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert seguidos == [], f"hay bases SQLite versionadas: {seguidos}"

    # Y todo lo que exista localmente esta efectivamente ignorado.
    for ruta in _bases_en_el_repositorio():
        relativa = ruta.relative_to(RAIZ).as_posix()
        ignorado = subprocess.run(
            ["git", "check-ignore", "-q", relativa], cwd=RAIZ
        ).returncode
        assert ignorado == 0, f"{relativa} no esta ignorado por Git"
