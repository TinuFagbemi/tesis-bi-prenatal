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


# ---------------------------------------------------------------------------
# restaurar ante fallos a mitad de camino (revision del PR #24)
# ---------------------------------------------------------------------------
#
# ClusterFalso lleva las bases por nombre, con su oid y su ``datallowconn``, y
# aplica de verdad los CREATE, DROP y ALTER que recibe. Un fallo se provoca en
# la primera sentencia que contiene un fragmento: antes de aplicarla (el
# servidor la rechazo) o despues (el servidor la aplico y la respuesta se
# perdio, como un ``TimeoutExpired`` de ``docker exec``). Los archivos locales
# son reales.

import re
import subprocess

BASE = "fetalalert_demo_baseline"
OID_ACTIVA = "16384"
MARCA = "20261026t120000z"
PREVIA = f"{BASE}__previa_{MARCA}"
TEMPORAL = f"{BASE}__restaurando"
SEGUNDO_RENAME = f'RENAME TO "{BASE}"'


class ClusterFalso:
    def __init__(self, ed):
        self.ed = ed
        self.base = BASE
        self.usuario = "fetalalert_ci"
        self.bases = {BASE: {"oid": OID_ACTIVA, "conexiones": True}}
        self.sentencias: list[str] = []
        self.fallos: list[dict] = []
        self.caido = False
        self._oid = 20000

    def fallar(self, fragmento, excepcion=None, aplicar=False, veces=1, caer=False):
        self.fallos.append({"fragmento": fragmento, "excepcion": excepcion or self.ed.ErrorDeEntorno("fallo simulado"),
                            "aplicar": aplicar, "veces": veces, "caer": caer})

    def existe(self, base):
        return base in self.bases

    def docker(self, *argumentos, **opciones):
        if self.caido:
            raise self.ed.ErrorDeEntorno("Fallo docker en el contenedor: simulado")
        self.sentencias.append(" ".join(argumentos))
        return ""

    def psql(self, sql, base="postgres", solo_lectura=False):
        self.sentencias.append(sql)
        if self.caido:
            raise self.ed.ErrorDeEntorno("Fallo psql en el contenedor: simulado")
        fallo = next((f for f in self.fallos if f["fragmento"] in sql and f["veces"] > 0), None)
        if fallo:
            fallo["veces"] -= 1
            if fallo["aplicar"]:
                self._ejecutar(sql)
            self.caido = fallo["caer"]
            raise fallo["excepcion"]
        return self._ejecutar(sql)

    def _ejecutar(self, sql):
        if "pg_stat_activity" in sql:
            return []
        nombre = re.search(r"WHERE datname = '([^']+)'", sql)
        if sql.startswith("SELECT count(*) FROM pg_database"):
            return [["1" if nombre.group(1) in self.bases else "0"]]
        if sql.startswith("SELECT datallowconn"):
            base = self.bases.get(nombre.group(1))
            return [["t" if base["conexiones"] else "f"]] if base else []
        if sql.startswith("SELECT datname, oid"):
            return [[n, self.bases[n]["oid"]] for n in re.findall(r"'([^']+)'", sql) if n in self.bases]
        if m := re.fullmatch(r'CREATE DATABASE "([^"]+)" OWNER "[^"]+"', sql):
            self._oid += 1
            self.bases[m.group(1)] = {"oid": str(self._oid), "conexiones": True}
            return []
        if m := re.fullmatch(r'DROP DATABASE IF EXISTS "([^"]+)"', sql):
            self.bases.pop(m.group(1), None)
            return []
        if m := re.fullmatch(r'ALTER DATABASE "([^"]+)" RENAME TO "([^"]+)"', sql):
            if m.group(1) not in self.bases or m.group(2) in self.bases:
                raise self.ed.ErrorDeEntorno("Fallo psql en el contenedor: ERROR: rename imposible")
            self.bases[m.group(2)] = self.bases.pop(m.group(1))
            return []
        if m := re.fullmatch(r'ALTER DATABASE "([^"]+)" ALLOW_CONNECTIONS false', sql):
            self.bases[m.group(1)]["conexiones"] = False
            return []
        raise AssertionError(f"sentencia no prevista: {sql}")

    @property
    def renames(self):
        return [s for s in self.sentencias if "RENAME" in s]


def _escribir(ruta: Path, texto: str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(texto, encoding="utf-8")


@pytest.fixture
def escena(ed, tmp_path, monkeypatch):
    """Un respaldo con archivos locales nuevos y una carpeta activa con los del ensayo."""
    respaldo = tmp_path / "respaldo"
    _escribir(respaldo / ed.CARPETA_LOCAL / "provision.json", '{"embarazo": 130}')
    _escribir(respaldo / ed.CARPETA_LOCAL / "dispositivos" / "DEMO-SCRUM72-01.json", "{}")
    (respaldo / ed.ARCHIVO_DUMP).write_bytes(b"dump")
    (respaldo / ed.ARCHIVO_MANIFIESTO).write_text(json.dumps({
        "base": BASE, "huella": HUELLA,
        "archivos_locales": ed.huella_de_archivos(respaldo / ed.CARPETA_LOCAL),
    }), encoding="utf-8")
    ed._escribir_sumas(respaldo)

    datos = tmp_path / "gestante"
    _escribir(datos / "provision.json", '{"embarazo": "del ensayo"}')
    _sqlite(datos / "movimientos" / "cuenta-107.sqlite3", 3)

    monkeypatch.setattr(ed, "huella_de_la_base", lambda destino, base=None: _copia())
    cluster = ClusterFalso(ed)

    def restaurar():
        return ed.restaurar(cluster, respaldo, datos, portal_en_marcha=False, marca=MARCA)

    return type("Escena", (), {"cluster": cluster, "datos": datos, "restaurar": staticmethod(restaurar),
                               "apartados": tmp_path / f"gestante__previa_{MARCA}",
                               "fallida": tmp_path / f"gestante__fallida_{MARCA}"})


def _archivos_del_ensayo(carpeta: Path) -> bool:
    """La carpeta es la original: su provision.json y su cola con 3 capturas."""
    with closing(sqlite3.connect(carpeta / "movimientos" / "cuenta-107.sqlite3")) as cola:
        filas = cola.execute("SELECT count(*) FROM outbox").fetchone()[0]
    return (carpeta / "provision.json").read_text(encoding="utf-8") == '{"embarazo": "del ensayo"}' and filas == 3


def _archivos_del_respaldo(carpeta: Path) -> bool:
    return ((carpeta / "provision.json").read_text(encoding="utf-8") == '{"embarazo": 130}'
            and not (carpeta / "movimientos").exists())


def _base_activa_original(cluster) -> bool:
    return (cluster.bases.get(BASE, {}).get("oid") == OID_ACTIVA
            and PREVIA not in cluster.bases and TEMPORAL not in cluster.bases)


def test_restauracion_completa_aisla_la_previa_y_aparta_los_archivos(escena):
    resultado = escena.restaurar()

    assert resultado["diferencias"] == [] and resultado["aislamiento_pendiente"] is None
    bases = escena.cluster.bases
    assert bases[PREVIA] == {"oid": OID_ACTIVA, "conexiones": False}
    assert bases[BASE]["oid"] != OID_ACTIVA and TEMPORAL not in bases
    assert _archivos_del_respaldo(escena.datos) and _archivos_del_ensayo(escena.apartados)
    assert not escena.fallida.exists()


def test_los_archivos_se_preparan_antes_de_tocar_la_base_activa(escena, ed, monkeypatch):
    sql_al_apartar = []
    original = Path.rename

    def rename(self, destino):
        if self == escena.datos:
            sql_al_apartar.append(list(escena.cluster.sentencias))
        return original(self, destino)

    monkeypatch.setattr(ed.Path, "rename", rename)
    escena.restaurar()

    assert len(sql_al_apartar) == 1, "la carpeta se aparta una sola vez"
    previas = sql_al_apartar[0]
    assert not [s for s in previas if s.startswith("ALTER")], "se aparto despues de tocar la base activa"
    assert any(s.startswith("ALTER") for s in escena.cluster.sentencias)


def test_si_no_se_puede_apartar_la_carpeta_no_cambia_nada(escena, ed, monkeypatch):
    original = Path.rename

    def rename(self, destino):
        if self == escena.datos:
            raise PermissionError(13, "El proceso no tiene acceso al archivo", str(self))
        return original(self, destino)

    monkeypatch.setattr(ed.Path, "rename", rename)
    with pytest.raises(ed.ErrorDeRestauracion) as error:
        escena.restaurar()

    assert "PermissionError" in error.value.motivo
    assert not escena.cluster.renames and _base_activa_original(escena.cluster)
    assert _archivos_del_ensayo(escena.datos) and not escena.apartados.exists()
    assert any("no se toco" in e for e in error.value.estado)


def test_si_falla_la_copia_los_archivos_vuelven_y_la_copia_se_aparta(escena, ed, monkeypatch):
    def copytree(origen, destino):
        Path(destino).mkdir()
        (Path(destino) / "a_medias.json").write_text("{}", encoding="utf-8")
        raise OSError(28, "No queda espacio en el disco", str(destino))

    monkeypatch.setattr(ed.shutil, "copytree", copytree)
    with pytest.raises(ed.ErrorDeRestauracion) as error:
        escena.restaurar()

    assert "No queda espacio" in error.value.motivo
    assert not escena.cluster.renames and _base_activa_original(escena.cluster)
    assert _archivos_del_ensayo(escena.datos) and not escena.apartados.exists()
    assert (escena.fallida / "a_medias.json").exists()


def test_si_falla_el_segundo_rename_la_activa_recupera_su_identidad(escena, ed):
    escena.cluster.fallar(SEGUNDO_RENAME)

    with pytest.raises(ed.ErrorDeRestauracion) as error:
        escena.restaurar()

    assert "NO se realizo" in error.value.motivo
    assert _base_activa_original(escena.cluster)
    assert any(f"oid {OID_ACTIVA}" in e for e in error.value.estado)
    assert _archivos_del_ensayo(escena.datos) and not escena.apartados.exists()
    assert _archivos_del_respaldo(escena.fallida)


def test_si_el_segundo_rename_se_aplico_aunque_fallara_la_respuesta_se_completa(escena, ed):
    escena.cluster.fallar(SEGUNDO_RENAME, subprocess.TimeoutExpired("docker exec", 600), aplicar=True)

    resultado = escena.restaurar()

    assert resultado["aislamiento_pendiente"] is None and resultado["diferencias"] == []
    assert escena.cluster.bases[PREVIA] == {"oid": OID_ACTIVA, "conexiones": False}
    assert sum(1 for s in escena.cluster.renames if SEGUNDO_RENAME in s) == 1, "no se repite la operacion"


def test_si_la_compensacion_falla_no_se_anuncia_exito_y_se_dan_pasos_manuales(escena, ed):
    escena.cluster.fallar(SEGUNDO_RENAME, veces=2)

    with pytest.raises(ed.ErrorDeRestauracion) as error:
        escena.restaurar()

    estado = " ".join(error.value.estado)
    assert "NO HAY BASE ACTIVA" in estado and "compensacion tambien fallo" in estado
    assert any(f'ALTER DATABASE "{PREVIA}" RENAME TO "{BASE}"' in p for p in error.value.pasos_manuales)
    assert escena.cluster.bases[PREVIA]["oid"] == OID_ACTIVA and TEMPORAL in escena.cluster.bases
    assert _archivos_del_ensayo(escena.apartados), "lo anterior se conserva, no se borra"


def test_si_el_cluster_deja_de_responder_el_estado_es_desconocido(escena, ed):
    escena.cluster.fallar(SEGUNDO_RENAME, subprocess.TimeoutExpired("docker exec", 600), caer=True)

    with pytest.raises(ed.ErrorDeRestauracion) as error:
        escena.restaurar()

    assert any("DESCONOCIDO" in e for e in error.value.estado)
    assert error.value.pasos_manuales
    assert _archivos_del_ensayo(escena.apartados)


def test_si_no_se_puede_aislar_la_previa_la_restauracion_no_es_conforme(escena, ed):
    escena.cluster.fallar("ALLOW_CONNECTIONS false")

    resultado = escena.restaurar()

    assert resultado["aislamiento_pendiente"] and PREVIA in resultado["aislamiento_pendiente"]
    assert escena.cluster.bases[PREVIA]["conexiones"] is True
    assert escena.cluster.bases[BASE]["oid"] != OID_ACTIVA


def test_si_allow_connections_se_aplico_aunque_fallara_la_respuesta_no_queda_pendiente(escena, ed):
    escena.cluster.fallar("ALLOW_CONNECTIONS false", subprocess.TimeoutExpired("docker exec", 600), aplicar=True)

    assert escena.restaurar()["aislamiento_pendiente"] is None


def test_archivos_auxiliares_de_sqlite_detienen_la_restauracion_sin_cambios(escena, ed):
    (escena.datos / "movimientos" / "cuenta-107.sqlite3-wal").write_bytes(b"x")

    with pytest.raises(ed.ErrorDeEntorno, match="auxiliares de SQLite"):
        escena.restaurar()

    assert not [s for s in escena.cluster.sentencias if s.startswith(("CREATE", "ALTER", "DROP"))]
    assert _archivos_del_ensayo(escena.datos)


# ---------------------------------------------------------------------------
# main(): cada desenlace con su mensaje y su codigo, sin traza
# ---------------------------------------------------------------------------


def _main(ed, tmp_path, monkeypatch, restaurar):
    monkeypatch.setattr(ed, "restaurar", restaurar)
    return ed.main(["restaurar", "--carpeta", str(tmp_path / "r"), "--base", BASE, "--contenedor", "c",
                    "--usuario-admin", "fetalalert_ci", "--datos", str(tmp_path / "datos")])


def test_main_informa_un_error_de_archivos_sin_traza_ni_confundirlo_con_docker(ed, tmp_path, monkeypatch, capsys):
    def restaurar(*a, **k):
        raise PermissionError(13, "El proceso no tiene acceso al archivo", "data/gestante")

    assert _main(ed, tmp_path, monkeypatch, restaurar) == ed.CODIGO_DE_ERROR
    salida = capsys.readouterr()
    assert "Error de archivos: PermissionError" in salida.err
    assert "Docker" not in salida.err and "Traceback" not in salida.err + salida.out


def test_main_ante_restauracion_incompleta_muestra_estado_y_pasos(ed, tmp_path, monkeypatch, capsys):
    def restaurar(*a, **k):
        raise ed.ErrorDeRestauracion("Fallo el intercambio.", estado=["PostgreSQL: NO HAY BASE ACTIVA."],
                                     pasos_manuales=["ALTER DATABASE ..."])

    assert _main(ed, tmp_path, monkeypatch, restaurar) == ed.CODIGO_DE_ERROR
    salida = capsys.readouterr()
    assert "RESTAURACION NO COMPLETADA" in salida.err and "NO HAY BASE ACTIVA" in salida.err
    assert "Pasos manuales" in salida.err and "Restaurada" not in salida.out


def test_main_con_aislamiento_pendiente_no_devuelve_exito(ed, tmp_path, monkeypatch, capsys):
    def restaurar(*a, **k):
        return {"previa": PREVIA, "archivos_apartados": None, "diferencias": [],
                "aislamiento_pendiente": f"no se pudo bloquear el acceso a '{PREVIA}'"}

    assert _main(ed, tmp_path, monkeypatch, restaurar) == ed.CODIGO_AISLAMIENTO_PENDIENTE
    salida = capsys.readouterr().out
    assert "COMPLETADA CON AISLAMIENTO PENDIENTE" in salida and "SIN AISLAR" in salida
    assert f'ALTER DATABASE "{PREVIA}" ALLOW_CONNECTIONS false' in salida


def test_un_docker_ausente_es_un_error_de_entorno_no_de_archivos(ed, monkeypatch):
    def run(*a, **k):
        raise FileNotFoundError(2, "No such file or directory", "docker")

    monkeypatch.setattr(ed.subprocess, "run", run)
    destino = ed.Destino(base=BASE, contenedor="c", usuario="fetalalert_ci")
    with pytest.raises(ed.ErrorDeEntorno, match="ejecutable de Docker"):
        destino.psql("SELECT 1")


# ---------------------------------------------------------------------------
# Las carpetas que aparta restaurar no se versionan
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ruta", [
    "data/gestante__previa_20261017t235239z/movimientos/cuenta-107.sqlite3",
    "data/gestante__previa_20261017t235239z/sesion_local.sqlite3",
    "data/gestante__fallida_20261026t120000z/movimientos/cuenta-107.sqlite3",
])
def test_las_carpetas_apartadas_estan_ignoradas_por_el_gitignore_versionado(ruta):
    raiz = RUTA_SCRIPT.parents[1]
    resultado = subprocess.run(["git", "check-ignore", "--no-index", "-v", ruta],
                               cwd=raiz, capture_output=True, text=True)
    if resultado.returncode == 128:
        pytest.skip("git no disponible")
    assert resultado.returncode == 0, f"{ruta} no esta ignorado"
    # La regla tiene que venir del .gitignore versionado, no de .git/info/exclude
    # ni de una configuracion local que solo existe en una maquina.
    assert resultado.stdout.split(":", 1)[0] == ".gitignore", resultado.stdout


def test_si_tras_compensar_la_base_no_se_pueden_devolver_los_archivos_se_informan_ambos(escena, ed, monkeypatch):
    escena.cluster.fallar(SEGUNDO_RENAME)
    original = Path.rename

    def rename(self, destino):
        if Path(destino) == escena.fallida:
            raise PermissionError(13, "El proceso no tiene acceso al archivo", str(self))
        return original(self, destino)

    monkeypatch.setattr(ed.Path, "rename", rename)
    with pytest.raises(ed.ErrorDeRestauracion) as error:
        escena.restaurar()

    estado = " ".join(error.value.estado)
    assert f"oid {OID_ACTIVA}" in estado, "se conserva lo sabido de PostgreSQL"
    assert "Fallo el intercambio" in error.value.motivo and "No se pudieron devolver" in error.value.motivo
    assert _base_activa_original(escena.cluster) and error.value.pasos_manuales
    assert _archivos_del_ensayo(escena.apartados), "la carpeta original sigue apartada, no se borro"


def test_si_falla_la_copia_y_tambien_devolver_los_archivos_se_conserva_la_causa(escena, ed, monkeypatch):
    def copytree(origen, destino):
        raise OSError(28, "No queda espacio en el disco", str(destino))

    original = Path.rename

    def rename(self, destino):
        if self == escena.apartados:
            raise PermissionError(13, "El proceso no tiene acceso al archivo", str(self))
        return original(self, destino)

    monkeypatch.setattr(ed.shutil, "copytree", copytree)
    monkeypatch.setattr(ed.Path, "rename", rename)
    with pytest.raises(ed.ErrorDeRestauracion) as error:
        escena.restaurar()

    assert "No queda espacio" in error.value.motivo and "No se pudieron devolver" in error.value.motivo
    assert not escena.cluster.renames and _base_activa_original(escena.cluster)
    assert _archivos_del_ensayo(escena.apartados)


def test_si_la_verificacion_final_falla_se_dice_que_la_restauracion_se_aplico(escena, ed, monkeypatch):
    def verificar(*a, **k):
        raise ed.ErrorDeEntorno("Fallo psql en el contenedor: simulado")

    monkeypatch.setattr(ed, "verificar", verificar)
    with pytest.raises(ed.ErrorDeRestauracion) as error:
        escena.restaurar()

    assert "se aplico, pero no se pudo verificar" in error.value.motivo
    assert any(PREVIA in e for e in error.value.estado)
    assert escena.cluster.bases[PREVIA]["conexiones"] is False
