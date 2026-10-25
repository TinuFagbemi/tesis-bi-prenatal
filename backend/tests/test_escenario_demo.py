"""Escenarios de demostracion: validacion y negativas, sin PostgreSQL ni red.

El recorrido completo --captura, sincronizacion real contra la API, ETL y
verificacion-- se ejerce contra PostgreSQL en el entorno desechable. Aqui se
prueba lo que debe fallar antes de escribir nada: un escenario mal formado, dos
dispositivos sobre la misma cuenta y una cola con capturas sin entregar.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app.gestante import movimientos, provision
from app.models.enums import TipoSesion
from tests.test_gestante_movimientos import AHORA, escribir_provision
from tests.test_gestante_rutas import ID_USUARIO_DE_PRUEBA, construir_settings

RUTA_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "escenario_demo.py"


@pytest.fixture(scope="module")
def esc():
    spec = importlib.util.spec_from_file_location("escenario_demo_bajo_prueba", RUTA_SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def _escenario(tmp_path, datos) -> Path:
    ruta = tmp_path / "escenario.json"
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    return ruta


def _gestante(**cambios):
    return {"dispositivo": "DEMO-GESTANTE-02", "sesiones": 2, "tipo": "SIGNOS_MATERNOS", **cambios}


def test_un_escenario_valido_se_carga(esc, tmp_path):
    escenario = esc.cargar_escenario(_escenario(tmp_path, {
        "nombre": "C-varias", "concurrente": True,
        "gestantes": [_gestante(), _gestante(dispositivo="DEMO-SCRUM72-01", sesiones=1)],
    }))

    assert escenario.nombre == "C-varias"
    assert escenario.concurrente
    assert [g.dispositivo for g in escenario.gestantes] == ["DEMO-GESTANTE-02", "DEMO-SCRUM72-01"]


@pytest.mark.parametrize(
    "archivo, gestantes, sesiones",
    [("A.json", 1, 1), ("B.json", 1, 4), ("C.json", 3, 10)],
)
def test_los_escenarios_de_la_sustentacion_son_validos(esc, archivo, gestantes, sesiones):
    escenario = esc.cargar_escenario(RUTA_SCRIPT.parent / "escenarios_demo" / archivo)

    assert len(escenario.gestantes) == gestantes
    assert sum(g.sesiones for g in escenario.gestantes) == sesiones


def test_los_tipos_se_alternan_por_sesion(esc, tmp_path):
    escenario = esc.cargar_escenario(_escenario(tmp_path, {"gestantes": [
        _gestante(sesiones=3, tipo=["SIGNOS_MATERNOS", "MOVIMIENTOS_FETALES"])
    ]}))
    gestante = escenario.gestantes[0]

    assert [gestante.tipo_de_la_sesion(i) for i in range(3)] == [
        TipoSesion.SIGNOS_MATERNOS, TipoSesion.MOVIMIENTOS_FETALES, TipoSesion.SIGNOS_MATERNOS
    ]
    # Sin "lecturas": una por sesion, como siempre.
    assert [gestante.lecturas_de_la_sesion(i) for i in range(3)] == [1, 1, 1]


def test_lecturas_se_aplica_a_signos_y_movimientos_sigue_con_un_conteo(esc, tmp_path):
    escenario = esc.cargar_escenario(_escenario(tmp_path, {"gestantes": [
        _gestante(sesiones=3, tipo=["SIGNOS_MATERNOS", "MOVIMIENTOS_FETALES"], lecturas=5)
    ]}))

    assert [escenario.gestantes[0].lecturas_de_la_sesion(i) for i in range(3)] == [5, 1, 5]


def test_el_escenario_de_scrum_109_captura_cinco_lecturas_por_sesion_de_signos(esc):
    escenario = esc.cargar_escenario(RUTA_SCRIPT.parent / "escenarios_demo" / "S109.json")

    assert all(g.lecturas == 5 for g in escenario.gestantes)


@pytest.mark.parametrize(
    "datos",
    [
        {},
        {"gestantes": []},
        {"gestantes": ["DEMO-GESTANTE-02"]},
        {"gestantes": [_gestante(dispositivo="../provision")]},
        {"gestantes": [_gestante(dispositivo="DEMO-GESTANTE-2")]},
        {"gestantes": [_gestante(sesiones=0)]},
        {"gestantes": [_gestante(sesiones=51)]},
        {"gestantes": [_gestante(sesiones=True)]},
        {"gestantes": [_gestante(sesiones="2")]},
        {"gestantes": [_gestante(tipo="DESCONOCIDO")]},
        {"gestantes": [_gestante(tipo=[])]},
        {"gestantes": [_gestante(), _gestante()]},
        {"gestantes": [_gestante(lecturas=0)]},
        {"gestantes": [_gestante(lecturas=11)]},
        {"gestantes": [_gestante(lecturas=True)]},
        {"gestantes": [_gestante(lecturas="5")]},
        {"nombre": "../fuera", "gestantes": [_gestante()]},
    ],
)
def test_un_escenario_mal_formado_se_rechaza(esc, tmp_path, datos):
    with pytest.raises(esc.ErrorDeEscenario):
        esc.cargar_escenario(_escenario(tmp_path, datos))


def test_la_contrasena_simulada_se_lee_sin_ejecutar_el_generador(esc):
    clave = esc.password_simulada()
    assert isinstance(clave, str) and clave


# ---------------------------------------------------------------------------
# capturar se niega antes de escribir y antes de conectarse
# ---------------------------------------------------------------------------


def _aprovisionar(tmp_path, codigo, id_usuario):
    carpeta = tmp_path / "dispositivos"
    carpeta.mkdir(exist_ok=True)
    escribir_provision(carpeta, id_usuario=id_usuario).rename(carpeta / f"{codigo}.json")


def _sin_base(esc, monkeypatch):
    def prohibido(*args, **kwargs):
        raise AssertionError("no debe conectarse a la base")

    monkeypatch.setattr(esc, "_conexion_de_lectura", prohibido)


def test_dos_dispositivos_sobre_la_misma_cuenta_no_capturan_nada(esc, tmp_path, monkeypatch):
    _aprovisionar(tmp_path, "DEMO-GESTANTE-02", ID_USUARIO_DE_PRUEBA)
    _aprovisionar(tmp_path, "DEMO-GESTANTE-03", ID_USUARIO_DE_PRUEBA)
    settings = construir_settings(tmp_path, dispositivos_dir=tmp_path / "dispositivos")
    escenario = esc.cargar_escenario(_escenario(tmp_path, {"gestantes": [
        _gestante(), _gestante(dispositivo="DEMO-GESTANTE-03")
    ]}))
    _sin_base(esc, monkeypatch)

    with pytest.raises(esc.ErrorDeEscenario, match="misma cuenta"):
        esc.capturar(escenario, settings)
    assert not settings.movimientos_dir.exists()


def test_una_cola_con_capturas_sin_entregar_detiene_el_escenario(esc, tmp_path, monkeypatch):
    _aprovisionar(tmp_path, "DEMO-GESTANTE-02", ID_USUARIO_DE_PRUEBA)
    settings = construir_settings(tmp_path, dispositivos_dir=tmp_path / "dispositivos")
    aprovisionamiento = provision.cargar(tmp_path / "dispositivos" / "DEMO-GESTANTE-02.json")
    movimientos.registrar_sesion_simulada(
        settings, id_usuario=ID_USUARIO_DE_PRUEBA, provision=aprovisionamiento,
        tipo_sesion=TipoSesion.SIGNOS_MATERNOS, ahora=AHORA,
    )
    escenario = esc.cargar_escenario(_escenario(tmp_path, {"gestantes": [_gestante()]}))
    _sin_base(esc, monkeypatch)

    with pytest.raises(esc.ErrorDeEscenario, match="sin entregar"):
        esc.capturar(escenario, settings)


def test_un_dispositivo_sin_aprovisionar_detiene_el_escenario(esc, tmp_path, monkeypatch):
    settings = construir_settings(tmp_path, dispositivos_dir=tmp_path / "dispositivos")
    escenario = esc.cargar_escenario(_escenario(tmp_path, {"gestantes": [_gestante()]}))
    _sin_base(esc, monkeypatch)

    with pytest.raises(esc.ErrorDeEscenario, match="DEMO-GESTANTE-02"):
        esc.capturar(escenario, settings)
    assert not settings.movimientos_dir.exists()
