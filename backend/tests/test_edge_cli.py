"""El comando del simulador: codigos de salida, salida en pantalla y privacidad.

El CLI se carga por ruta, igual que hace ``test_load_mock_data.py`` con el
cargador de SCRUM-61: la logica vive en ``app.edge`` y se prueba directamente,
asi que lo unico que queda por comprobar aqui es la capa de comando --que los
argumentos se interpretan, que los errores se convierten en un codigo de salida
y que nada sensible llega a la pantalla--.

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
from app.edge.politica import ConfiguracionInvalida
from app.edge.estados import EstadoEntrega
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
