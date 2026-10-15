"""Varias gestantes de demostracion y la restauracion, contra PostgreSQL real.

Se omite salvo que este definida ``ENTORNO_DEMO_TEST_DATABASE_URL`` (el
migrador), y esa variable debe apuntar a una base **desechable** cuyo nombre
empiece por ``entorno_demo_``, migrada y con el dataset simulado recien cargado.
Las pruebas de restauracion necesitan ademas el contenedor que sirve esa base y
su superusuario (``ENTORNO_DEMO_TEST_CONTENEDOR``, ``ENTORNO_DEMO_TEST_ADMIN``):
``pg_dump`` y ``pg_restore`` se ejecutan dentro de el, con su misma version.

**Lo que se demuestra.** Que ``provisionar --gestantes N`` agrega N episodios
sobre cuentas distintas sin tocar una fila canonica y sin duplicar al repetirse;
y que ``entorno_demo.py restaurar`` devuelve la base y los archivos locales al
estado del respaldo --sin filas, secuencias, permisos ni archivos residuales--
conservando el estado anterior en vez de borrarlo.

Todos los datos y credenciales implicados son ficticios y efimeros.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from tests.test_provisionar_demo_postgresql import (
    FILAS_DEL_EPISODIO,
    _huella_de_embarazos,
    _huellas_por_fila,
    cargar_script,
)

VARIABLE_DE_ENTORNO = "ENTORNO_DEMO_TEST_DATABASE_URL"
PATRON_DE_BASE = re.compile(r"^entorno_demo_[a-z0-9_]{1,20}$")
GESTANTES = 4

RAIZ = Path(__file__).resolve().parents[2]
PROVISIONAR = RAIZ / "scripts" / "provisionar_demo.py"
ENTORNO = RAIZ / "scripts" / "entorno_demo.py"

pytestmark = pytest.mark.skipif(
    not os.environ.get(VARIABLE_DE_ENTORNO),
    reason=(
        f"Define {VARIABLE_DE_ENTORNO} apuntando a una base PostgreSQL desechable "
        "'entorno_demo_*', migrada y con el dataset simulado. Esta suite escribe filas."
    ),
)


@pytest.fixture(scope="module")
def url() -> str:
    valor = os.environ[VARIABLE_DE_ENTORNO]
    if not PATRON_DE_BASE.fullmatch(make_url(valor).database or ""):
        pytest.fail(f"{VARIABLE_DE_ENTORNO} debe apuntar a una base desechable 'entorno_demo_*'.")
    return valor


@pytest.fixture(scope="module")
def engine(url):
    motor = create_engine(url, poolclass=NullPool)
    yield motor
    motor.dispose()


@pytest.fixture(scope="module")
def script():
    return cargar_script()


def _puerto_libre() -> int:
    with closing(socket.socket()) as sondeo:
        sondeo.bind(("127.0.0.1", 0))
        return sondeo.getsockname()[1]


def _entorno(carpeta: Path, url: str) -> dict:
    entorno = dict(os.environ)
    entorno.update({
        # Un puerto sin portal: el de la maquina de quien ejecuta podria estar en
        # marcha, y restaurar se negaria con razon a reemplazar sus archivos.
        "GESTANTE_PORT": str(_puerto_libre()),
        "ALEMBIC_DATABASE_URL": url,
        "GESTANTE_PROVISION_PATH": str(carpeta / "provision.json"),
        "GESTANTE_DISPOSITIVOS_DIR": str(carpeta / "dispositivos"),
        "GESTANTE_MOVIMIENTOS_DIR": str(carpeta / "movimientos"),
        "GESTANTE_SQLITE_PATH": str(carpeta / "sesion_local.sqlite3"),
        "PYTHONPATH": str(RAIZ / "backend"),
        "PYTHONIOENCODING": "utf-8",
    })
    return entorno


def _provisionar(url: str, carpeta: Path, *argumentos: str):
    return subprocess.run(
        [sys.executable, str(PROVISIONAR), "provisionar", *argumentos],
        capture_output=True, text=True, env=_entorno(carpeta, url), cwd=str(RAIZ),
    )


@pytest.fixture(scope="module")
def estado_inicial(engine, script):
    with engine.connect() as conexion:
        if conexion.execute(
            text("SELECT count(*) FROM operacional.dispositivo WHERE codigo_dispositivo LIKE 'DEMO-%'")
        ).scalar_one():
            pytest.fail("La base ya tiene episodios de demostracion; necesita una recien cargada.")
        return {"filas": _huellas_por_fila(conexion), "embarazos": _huella_de_embarazos(conexion)}


@pytest.fixture(scope="module")
def provisionada(url, estado_inicial, tmp_path_factory):
    carpeta = tmp_path_factory.mktemp("gestante")
    resultado = _provisionar(url, carpeta, "--gestantes", str(GESTANTES))
    assert resultado.returncode == 0, resultado.stderr
    return {"carpeta": carpeta, "salida": resultado.stdout}


def _archivos_de_dispositivo(carpeta: Path) -> dict[str, dict]:
    return {
        ruta.stem: json.loads(ruta.read_text(encoding="utf-8"))
        for ruta in sorted((carpeta / "dispositivos").glob("*.json"))
    }


# ---------------------------------------------------------------------------
# Varias gestantes
# ---------------------------------------------------------------------------


def test_cada_gestante_tiene_su_cuenta_su_embarazo_y_su_dispositivo(engine, script, provisionada):
    archivos = _archivos_de_dispositivo(provisionada["carpeta"])

    assert sorted(archivos) == sorted(script.codigo_de_dispositivo(n) for n in range(1, GESTANTES + 1))
    cuentas = [a["cuenta"]["id_usuario"] for a in archivos.values()]
    embarazos = [a["embarazo"]["id_embarazo"] for a in archivos.values()]
    assert len(set(cuentas)) == len(set(embarazos)) == GESTANTES

    with engine.connect() as conexion:
        for codigo, archivo in archivos.items():
            fila = conexion.execute(
                text(
                    "SELECT up.id_usuario, e.estado_embarazo, d.codigo_dispositivo"
                    "  FROM operacional.embarazo e"
                    "  JOIN operacional.usuario_paciente up ON up.id_paciente = e.id_paciente"
                    "  JOIN operacional.asignacion_dispositivo a ON a.id_embarazo = e.id_embarazo"
                    "  JOIN operacional.dispositivo d ON d.id_dispositivo = a.id_dispositivo"
                    " WHERE e.id_embarazo = :e AND d.id_dispositivo = :d"
                ),
                {"e": archivo["embarazo"]["id_embarazo"], "d": archivo["dispositivo"]["id_dispositivo"]},
            ).one()
            assert fila.id_usuario == archivo["cuenta"]["id_usuario"]
            assert fila.estado_embarazo == "ACTIVO"
            assert fila.codigo_dispositivo == codigo


def test_provision_json_sigue_siendo_el_del_primer_dispositivo(script, provisionada):
    carpeta = provisionada["carpeta"]
    primero = _archivos_de_dispositivo(carpeta)[script.codigo_de_dispositivo(1)]
    provision = json.loads((carpeta / "provision.json").read_text(encoding="utf-8"))
    # Se escriben uno tras otro: solo la marca de generacion puede diferir.
    provision.pop("generado_en")
    primero.pop("generado_en")
    assert provision == primero


def test_las_gestantes_adicionales_no_tenian_un_embarazo_en_curso(engine, estado_inicial, script, provisionada):
    """Salvo la primera (la de SCRUM-72), cada paciente elegida solo tenia embarazos cerrados."""
    archivos = _archivos_de_dispositivo(provisionada["carpeta"])
    adicionales = {a["embarazo"]["id_embarazo"] for c, a in archivos.items() if c != script.codigo_de_dispositivo(1)}
    with engine.connect() as conexion:
        pacientes = set(conexion.execute(
            text("SELECT id_paciente FROM operacional.embarazo WHERE id_embarazo = ANY(:e)"),
            {"e": sorted(adicionales)},
        ).scalars())
    previos = [fila for fila in estado_inicial["embarazos"] if fila[1] in pacientes]
    assert previos, "cada paciente adicional tenia historia previa en el dataset"
    assert {fila[5] for fila in previos} == {"FINALIZADO"}


def test_solo_se_agregan_las_filas_de_los_episodios(engine, estado_inicial, provisionada):
    with engine.connect() as conexion:
        ahora = _huellas_por_fila(conexion)
    antes = estado_inicial["filas"]
    for tabla in [t for t in antes if t != "secuencias"]:
        assert not antes[tabla] - ahora[tabla], f"{tabla}: una fila canonica cambio o falta"
        assert sum((ahora[tabla] - antes[tabla]).values()) == GESTANTES * FILAS_DEL_EPISODIO.get(tabla, 0), tabla


def test_repetir_no_duplica_ni_cambia_los_identificadores(engine, url, provisionada):
    with engine.connect() as conexion:
        antes = _huellas_por_fila(conexion)
    archivos_antes = _archivos_de_dispositivo(provisionada["carpeta"])

    resultado = _provisionar(url, provisionada["carpeta"], "--gestantes", str(GESTANTES))

    assert resultado.returncode == 0, resultado.stderr
    with engine.connect() as conexion:
        assert _huellas_por_fila(conexion) == antes
    despues = _archivos_de_dispositivo(provisionada["carpeta"])
    for codigo, archivo in archivos_antes.items():
        for parte in ("cuenta", "embarazo", "dispositivo"):
            assert despues[codigo][parte] == archivo[parte]


@pytest.mark.parametrize("valor", ["0", "9", "-1", "dos"])
def test_un_numero_de_gestantes_fuera_de_rango_se_rechaza(engine, url, provisionada, valor, tmp_path):
    with engine.connect() as conexion:
        antes = _huellas_por_fila(conexion)
    resultado = _provisionar(url, tmp_path, "--gestantes", valor)
    assert resultado.returncode == 2
    with engine.connect() as conexion:
        assert _huellas_por_fila(conexion) == antes
    assert not (tmp_path / "dispositivos").exists()


# ---------------------------------------------------------------------------
# Restauracion
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def contenedor():
    nombre, admin = os.environ.get("ENTORNO_DEMO_TEST_CONTENEDOR"), os.environ.get("ENTORNO_DEMO_TEST_ADMIN")
    if not nombre or not admin:
        pytest.skip("Define ENTORNO_DEMO_TEST_CONTENEDOR y ENTORNO_DEMO_TEST_ADMIN para probar la restauracion.")
    return nombre, admin


def _admin(contenedor, sql: str, base: str = "postgres") -> str:
    nombre, admin = contenedor
    resultado = subprocess.run(
        ["docker", "exec", nombre, "psql", "-X", "-U", admin, "-d", base, "-v", "ON_ERROR_STOP=1", "-Atc", sql],
        capture_output=True, text=True,
    )
    assert resultado.returncode == 0, resultado.stderr
    return resultado.stdout.strip()


def _orden(url, contenedor, carpeta: Path, datos: Path, orden: str):
    nombre, admin = contenedor
    return subprocess.run(
        [sys.executable, str(ENTORNO), orden, "--carpeta", str(carpeta), "--base", make_url(url).database,
         "--contenedor", nombre, "--usuario-admin", admin, "--datos", str(datos)],
        capture_output=True, text=True, env=_entorno(datos, url), cwd=str(RAIZ),
    )


@pytest.fixture(scope="module")
def respaldo(url, contenedor, provisionada, tmp_path_factory):
    carpeta = tmp_path_factory.mktemp("respaldo") / "baseline"
    datos = provisionada["carpeta"]
    with closing(sqlite3.connect(datos / "movimientos.sqlite3")) as cola:
        cola.execute("CREATE TABLE outbox (id INTEGER PRIMARY KEY, estado TEXT)")
        cola.execute("INSERT INTO outbox (estado) VALUES ('ENVIADO')")
        cola.commit()
    resultado = _orden(url, contenedor, carpeta, datos, "respaldar")
    assert resultado.returncode == 0, resultado.stderr
    yield {"carpeta": carpeta, "datos": datos}
    base = make_url(url).database
    for previa in _admin(contenedor, f"SELECT datname FROM pg_database WHERE datname LIKE '{base}\\_\\_%'").split():
        _admin(contenedor, f'DROP DATABASE "{previa}"')


def test_el_respaldo_coincide_con_la_base_recien_respaldada(url, contenedor, respaldo):
    resultado = _orden(url, contenedor, respaldo["carpeta"], respaldo["datos"], "verificar")
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert "IDENTICO" in resultado.stdout


def test_el_respaldo_no_contiene_contrasenas(respaldo):
    globales = (respaldo["carpeta"] / "roles_y_membresias_sin_claves.sql").read_text(encoding="utf-8")
    assert "PASSWORD" not in globales.upper().replace("NO ROLE PASSWORDS", "")
    assert "SCRAM-SHA-256" not in globales


def test_restaurar_se_niega_con_una_sesion_abierta_y_no_cambia_nada(url, contenedor, engine, respaldo):
    base = make_url(url).database
    with engine.connect() as abierta:
        abierta.execute(text("SELECT 1"))
        resultado = _orden(url, contenedor, respaldo["carpeta"], respaldo["datos"], "restaurar")
    assert resultado.returncode == 1
    assert "sesiones abiertas" in resultado.stderr
    assert _admin(contenedor, f"SELECT count(*) FROM pg_database WHERE datname LIKE '{base}\\_\\_%'") == "0"


def test_restaurar_elimina_todo_residuo_y_conserva_el_estado_anterior(url, contenedor, respaldo):
    base = make_url(url).database
    datos = respaldo["datos"]
    # Residuos de toda clase: una fila borrada y otra cambiada, una secuencia
    # avanzada, un permiso revocado, una cola SQLite distinta y un archivo extra.
    _admin(contenedor, (
        "DELETE FROM operacional.seguimiento_clinico WHERE ctid = (SELECT max(ctid) FROM operacional.seguimiento_clinico);"
        " UPDATE operacional.dispositivo SET codigo_dispositivo = codigo_dispositivo || 'X'"
        "  WHERE id_dispositivo = (SELECT max(id_dispositivo) FROM operacional.dispositivo);"
        " SELECT nextval('operacional.lectura_biometrica_id_lectura_seq');"
        " REVOKE SELECT ON publicacion.v_lectura FROM fetalalert_powerbi;"
    ), base)
    with closing(sqlite3.connect(datos / "movimientos.sqlite3")) as cola:
        cola.execute("INSERT INTO outbox (estado) VALUES ('PENDIENTE')")
        cola.commit()
    (datos / "dispositivos" / "DEMO-GESTANTE-08.json").write_text("{}", encoding="utf-8")

    alterada = _orden(url, contenedor, respaldo["carpeta"], datos, "verificar")
    assert alterada.returncode == 3
    for esperado in ("operacional.seguimiento_clinico", "operacional.dispositivo: mismas filas",
                     "lectura_biometrica_id_lectura_seq", "permisos distinto",
                     "movimientos.sqlite3", "sobrante: dispositivos/DEMO-GESTANTE-08.json"):
        assert esperado in alterada.stdout, esperado

    restaurada = _orden(url, contenedor, respaldo["carpeta"], datos, "restaurar")
    assert restaurada.returncode == 0, restaurada.stdout + restaurada.stderr
    assert "IDENTICO" in restaurada.stdout

    # Una verificacion independiente, en otro proceso.
    otra = _orden(url, contenedor, respaldo["carpeta"], datos, "verificar")
    assert otra.returncode == 0 and "IDENTICO" in otra.stdout

    # El estado anterior no se borro: queda como base sin conexiones y carpeta aparte.
    previas = _admin(contenedor, f"SELECT datname, datallowconn FROM pg_database WHERE datname LIKE '{base}\\_\\_previa%'")
    assert previas.count("|f") == 1 and "|t" not in previas
    apartadas = list(datos.parent.glob(f"{datos.name}__previa_*"))
    assert len(apartadas) == 1
    assert (apartadas[0] / "dispositivos" / "DEMO-GESTANTE-08.json").exists()
    assert not (datos / "dispositivos" / "DEMO-GESTANTE-08.json").exists()
    with closing(sqlite3.connect(datos / "movimientos.sqlite3")) as cola:
        assert cola.execute("SELECT estado FROM outbox").fetchall() == [("ENVIADO",)]


def test_tras_restaurar_la_api_y_el_rls_siguen_funcionando(url, contenedor, respaldo):
    """La base restaurada conserva RLS: sin contexto clinico, el rol de la API no ve embarazos."""
    base = make_url(url).database
    visibles = _admin(contenedor, "SET ROLE fetalalert_api; SELECT count(*) FROM operacional.embarazo;", base)
    assert visibles.splitlines()[-1] == "0"
    assert _admin(contenedor, "SELECT count(*) FROM operacional.embarazo", base) != "0"
