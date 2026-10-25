"""Respaldo y restauracion del entorno de demostracion, sin PostgreSQL ni Docker.

Lo que aqui se prueba es la parte que decide: que la comparacion de huellas
detecta cualquier diferencia, que un respaldo alterado se rechaza, que las SQLite
se comparan por contenido y que ``restaurar`` se niega --sin emitir una sola
sentencia que cambie algo-- cuando falta una precondicion. La restauracion
contra PostgreSQL real esta en ``test_entorno_demo_postgresql.py``.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

RUTA_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "entorno_demo.py"


@pytest.fixture(scope="module")
def ed():
    spec = importlib.util.spec_from_file_location("entorno_demo_bajo_prueba", RUTA_SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


HUELLA = {
    "alembic": "9c1d7f2ab4e8",
    "propietario": "fetalalert_dev",
    "permisos": "a",
    "tablas": {"operacional.lectura_biometrica": [1180, "x"], "analitico.fact_lectura_biometrica": [1180, "y"]},
    "secuencias": {"operacional.lectura_biometrica_id_lectura_seq": "sin_usar"},
}


def _copia(**cambios):
    huella = json.loads(json.dumps(HUELLA))
    for clave, valor in cambios.items():
        huella[clave] = valor
    return huella


def _sqlite(ruta: Path, filas: int) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(ruta)) as conexion:
        conexion.execute("CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY, estado TEXT)")
        conexion.execute("DELETE FROM outbox")
        conexion.executemany("INSERT INTO outbox (estado) VALUES (?)", [("ENVIADO",)] * filas)
        conexion.commit()


def _respaldo_falso(ed, carpeta: Path, base="fetalalert_demo_baseline") -> None:
    carpeta.mkdir(parents=True)
    (carpeta / ed.ARCHIVO_DUMP).write_bytes(b"dump")
    (carpeta / ed.ARCHIVO_MANIFIESTO).write_text(
        json.dumps({"base": base, "huella": HUELLA, "archivos_locales": {}}), encoding="utf-8"
    )
    ed._escribir_sumas(carpeta)


# ---------------------------------------------------------------------------
# Huellas
# ---------------------------------------------------------------------------


def test_huellas_iguales_no_tienen_diferencias(ed):
    assert ed.comparar_huellas(HUELLA, _copia()) == []


@pytest.mark.parametrize(
    "cambio, esperado",
    [
        ({"permisos": "b"}, "permisos distinto"),
        ({"alembic": "otra"}, "alembic distinto"),
        ({"tablas": {**HUELLA["tablas"], "analitico.fact_lectura_biometrica": [1181, "z"]}},
         "analitico.fact_lectura_biometrica: 1180 -> 1181 filas"),
        ({"tablas": {**HUELLA["tablas"], "analitico.fact_lectura_biometrica": [1180, "z"]}},
         "analitico.fact_lectura_biometrica: mismas filas, contenido distinto"),
        ({"secuencias": {"operacional.lectura_biometrica_id_lectura_seq": "1281"}},
         "operacional.lectura_biometrica_id_lectura_seq: sin_usar -> 1281"),
        ({"tablas": {**HUELLA["tablas"], "operacional.residuo": [1, "r"]}}, "operacional.residuo: ausente -> [1, 'r']"),
    ],
)
def test_cualquier_diferencia_se_informa(ed, cambio, esperado):
    assert esperado in ed.comparar_huellas(HUELLA, _copia(**cambio))


def test_una_sqlite_se_compara_por_contenido_y_no_por_bytes(ed, tmp_path):
    origen, copia = tmp_path / "a", tmp_path / "b"
    _sqlite(origen / "movimientos" / "cuenta-107.sqlite3", 2)
    ed.copiar_archivos_locales(origen, copia)

    assert ed.huella_de_archivos(origen) == ed.huella_de_archivos(copia)

    _sqlite(copia / "movimientos" / "cuenta-107.sqlite3", 3)
    assert ed.comparar_archivos(ed.huella_de_archivos(origen), copia) == [
        "archivo distinto o ausente: movimientos/cuenta-107.sqlite3"
    ]


def test_un_archivo_sobrante_o_ausente_se_informa(ed, tmp_path):
    (tmp_path / "provision.json").write_text("{}", encoding="utf-8")
    esperados = ed.huella_de_archivos(tmp_path)
    (tmp_path / "dispositivos").mkdir()
    (tmp_path / "dispositivos" / "DEMO-GESTANTE-02.json").write_text("{}", encoding="utf-8")
    (tmp_path / "provision.json").unlink()

    assert ed.comparar_archivos(esperados, tmp_path) == [
        "archivo distinto o ausente: provision.json",
        "archivo sobrante: dispositivos/DEMO-GESTANTE-02.json",
    ]


def test_los_auxiliares_de_sqlite_no_forman_parte_de_la_huella(ed, tmp_path):
    _sqlite(tmp_path / "cola.sqlite3", 1)
    (tmp_path / "cola.sqlite3-wal").write_bytes(b"x")
    (tmp_path / "cola.sqlite3-shm").write_bytes(b"x")

    assert list(ed.huella_de_archivos(tmp_path)) == ["cola.sqlite3"]


# ---------------------------------------------------------------------------
# Integridad del respaldo
# ---------------------------------------------------------------------------


def test_un_respaldo_intacto_se_acepta(ed, tmp_path):
    _respaldo_falso(ed, tmp_path / "r")
    assert ed.comprobar_sumas(tmp_path / "r")["base"] == "fetalalert_demo_baseline"


def test_un_respaldo_alterado_se_rechaza(ed, tmp_path):
    _respaldo_falso(ed, tmp_path / "r")
    (tmp_path / "r" / ed.ARCHIVO_DUMP).write_bytes(b"otro")
    with pytest.raises(ed.ErrorDeEntorno, match="alterado"):
        ed.comprobar_sumas(tmp_path / "r")


def test_un_archivo_agregado_al_respaldo_se_rechaza(ed, tmp_path):
    _respaldo_falso(ed, tmp_path / "r")
    (tmp_path / "r" / "intruso.sql").write_text("DROP DATABASE x;", encoding="utf-8")
    with pytest.raises(ed.ErrorDeEntorno, match="no estan en SHA256SUMS"):
        ed.comprobar_sumas(tmp_path / "r")


def test_una_carpeta_sin_sumas_no_es_un_respaldo(ed, tmp_path):
    with pytest.raises(ed.ErrorDeEntorno, match="SHA256SUMS"):
        ed.comprobar_sumas(tmp_path)


def test_las_sumas_se_escriben_con_fin_de_linea_lf(ed, tmp_path):
    _respaldo_falso(ed, tmp_path / "r")
    assert b"\r\n" not in (tmp_path / "r" / ed.ARCHIVO_SUMAS).read_bytes()


@pytest.mark.parametrize("nombre", ["x; DROP DATABASE y", 'a"b', "Mayus", "", "1base", "a" * 64])
def test_un_nombre_de_base_inseguro_se_rechaza(ed, nombre):
    with pytest.raises(ed.ErrorDeEntorno):
        ed._nombre_seguro(nombre)


# ---------------------------------------------------------------------------
# restaurar se niega antes de cambiar nada
# ---------------------------------------------------------------------------


class DestinoFalso:
    """Registra cada sentencia; nada de lo que se niega debe escribir."""

    def __init__(self, base="fetalalert_demo_baseline", sesiones=()):
        self.base = base
        self.usuario = "fetalalert_ci"
        self.sesiones = list(sesiones)
        self.sentencias: list[str] = []

    def psql(self, sql, base="postgres", solo_lectura=False):
        self.sentencias.append(sql)
        if "pg_stat_activity" in sql:
            return [[s] for s in self.sesiones]
        if "pg_database" in sql:
            return [["0"]]
        return []

    def existe(self, base):
        return False

    def docker(self, *argumentos, **opciones):
        self.sentencias.append(" ".join(argumentos))
        return ""

    @property
    def escrituras(self):
        return [s for s in self.sentencias if s.split()[0].upper() in ("CREATE", "ALTER", "DROP", "PG_RESTORE")]


def test_restaurar_se_niega_con_sesiones_abiertas(ed, tmp_path):
    _respaldo_falso(ed, tmp_path / "r")
    destino = DestinoFalso(sesiones=["fetalalert_api (sin nombre)"])
    with pytest.raises(ed.ErrorDeEntorno, match="sesiones abiertas.*Deten la API"):
        ed.restaurar(destino, tmp_path / "r", tmp_path / "datos", portal_en_marcha=False)
    assert destino.escrituras == []


def test_restaurar_se_niega_con_el_portal_en_marcha(ed, tmp_path):
    _respaldo_falso(ed, tmp_path / "r")
    (tmp_path / "datos").mkdir()
    destino = DestinoFalso()
    with pytest.raises(ed.ErrorDeEntorno, match="portal"):
        ed.restaurar(destino, tmp_path / "r", tmp_path / "datos", portal_en_marcha=True)
    assert destino.escrituras == []
    assert (tmp_path / "datos").exists()


def test_restaurar_se_niega_si_el_respaldo_es_de_otra_base(ed, tmp_path):
    _respaldo_falso(ed, tmp_path / "r", base="fetalalert_demo_sustentacion")
    destino = DestinoFalso()
    with pytest.raises(ed.ErrorDeEntorno, match="no de 'fetalalert_demo_baseline'"):
        ed.restaurar(destino, tmp_path / "r", tmp_path / "datos", portal_en_marcha=False)
    assert destino.escrituras == []


def test_el_respaldo_de_otra_base_solo_se_acepta_si_se_declara(ed, tmp_path, monkeypatch):
    _respaldo_falso(ed, tmp_path / "r", base="fetalalert_demo_baseline")
    (tmp_path / "datos").mkdir()
    destino = DestinoFalso(base="fetalalert_demo_sustentacion")
    monkeypatch.setattr(ed, "huella_de_la_base", lambda d, base=None: _copia(permisos="otro"))

    with pytest.raises(ed.ErrorDeEntorno, match="--respaldo-de"):
        ed.restaurar(destino, tmp_path / "r", tmp_path / "datos", portal_en_marcha=False,
                     respaldo_de="otra_base")
    assert destino.escrituras == []

    # Declarado, pasa la comprobacion de nombre y llega a restaurar en la temporal.
    with pytest.raises(ed.ErrorDeEntorno, match="no coincide"):
        ed.restaurar(destino, tmp_path / "r", tmp_path / "datos", portal_en_marcha=False,
                     respaldo_de="fetalalert_demo_baseline")
    assert 'CREATE DATABASE "fetalalert_demo_sustentacion__restaurando"' in destino.escrituras[0]


def test_restaurar_se_niega_si_el_respaldo_esta_alterado(ed, tmp_path):
    _respaldo_falso(ed, tmp_path / "r")
    (tmp_path / "r" / ed.ARCHIVO_DUMP).write_bytes(b"otro")
    destino = DestinoFalso()
    with pytest.raises(ed.ErrorDeEntorno, match="alterado"):
        ed.restaurar(destino, tmp_path / "r", tmp_path / "datos", portal_en_marcha=False)
    assert destino.sentencias == []


def test_si_la_restaurada_no_coincide_se_descarta_y_la_activa_no_se_toca(ed, tmp_path, monkeypatch):
    _respaldo_falso(ed, tmp_path / "r")
    (tmp_path / "datos").mkdir()
    destino = DestinoFalso()
    monkeypatch.setattr(ed, "huella_de_la_base", lambda d, base=None: _copia(permisos="otro"))

    with pytest.raises(ed.ErrorDeEntorno, match="no coincide"):
        ed.restaurar(destino, tmp_path / "r", tmp_path / "datos", portal_en_marcha=False)

    assert not [s for s in destino.escrituras if s.startswith("ALTER")]
    assert destino.escrituras[-1] == 'DROP DATABASE IF EXISTS "fetalalert_demo_baseline__restaurando"'
    assert (tmp_path / "datos").exists()


def test_respaldar_no_sobrescribe_un_respaldo_existente(ed, tmp_path):
    _respaldo_falso(ed, tmp_path / "r")
    with pytest.raises(ed.ErrorDeEntorno, match="no esta vacia"):
        ed.respaldar(DestinoFalso(), tmp_path / "r", tmp_path / "datos")
