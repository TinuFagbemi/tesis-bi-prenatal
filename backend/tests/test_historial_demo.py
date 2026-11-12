"""Pruebas sin servidor de ``scripts/historial_demo.py`` (historial de paciente01).

Comprueban el plan, que es puro: forma de las sesiones según el contrato,
semanas y fechas válidas, movimientos fetales solo desde la semana mínima,
cobertura mensual, reglas del sistema (semana y semáforo) y reproducibilidad.
La carga real contra la API y PostgreSQL se valida aparte, en una base
exclusiva.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.etl.reglas import ZONA_HORARIA_CLINICA, clasificar_lectura
from app.gestante.provision import Provision, SemanaGestacional
from app.loader.dataset import SEMANA_MINIMA_DE_MOVIMIENTO
from app.models.enums import TipoSesion
from app.schemas.monitoreo import SesionMonitoreoEntrada
from app.services.idempotencia import clave_valida
from app.services.ingesta import semana_gestacional

RUTA_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "historial_demo.py"
INICIO = date(2026, 5, 2)
SEMAFOROS = {"OK": 100, "WARNING": 101, "ERROR": 102}
# Corte: primer día de la semana 28, como en la validación local.
CORTE = datetime(2026, 11, 7, 18, 0, tzinfo=ZONA_HORARIA_CLINICA)


@pytest.fixture(scope="module")
def hist():
    spec = importlib.util.spec_from_file_location("historial_demo_bajo_prueba", RUTA_SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def _trimestre(semana: int) -> int:
    return 1 if semana <= 13 else 2 if semana <= 27 else 3


@pytest.fixture(scope="module")
def prov() -> Provision:
    return Provision(
        id_usuario=107,
        id_embarazo=130,
        fecha_inicio_embarazo=INICIO,
        id_dispositivo=130,
        semaforos=dict(SEMAFOROS),
        semanas={s: SemanaGestacional(id_tiempo_gest=1000 + s, trimestre=_trimestre(s)) for s in range(1, 41)},
    )


@pytest.fixture(scope="module")
def plan(hist, prov):
    return hist.construir_plan(prov, hist.SEMILLA_POR_OMISION, CORTE)


def test_solo_semanas_completas_anteriores_a_la_actual(plan):
    assert semana_gestacional(INICIO, CORTE) == 28
    assert {s.semana for s in plan} == set(range(1, 28))


def test_ninguna_fecha_futura_ni_fuera_del_embarazo(plan):
    for s in plan:
        for lectura in s.paquete["lecturas"]:
            instante = datetime.fromisoformat(lectura["fecha_hora_captura"])
            assert instante.date() >= INICIO
            assert instante < CORTE
        assert s.paquete["fecha_inicio"] <= s.paquete["fecha_fin"]


def test_signos_maternos_con_cinco_lecturas_de_fc_y_spo2(hist, plan):
    signos = [s for s in plan if s.tipo is TipoSesion.SIGNOS_MATERNOS]
    assert signos
    for s in signos:
        assert len(s.paquete["lecturas"]) == hist.LECTURAS_POR_SESION_DE_SIGNOS == 5
        for lectura in s.paquete["lecturas"]:
            assert lectura["hr_valor"] is not None and lectura["spo2_valor"] is not None
            assert lectura["mov_valor"] is None


def test_movimientos_con_un_conteo_consolidado_por_sesion(plan):
    movimientos = [s for s in plan if s.tipo is TipoSesion.MOVIMIENTOS_FETALES]
    assert movimientos
    for s in movimientos:
        [lectura] = s.paquete["lecturas"]
        assert lectura["hr_valor"] is None and lectura["spo2_valor"] is None
        assert isinstance(lectura["mov_valor"], int) and lectura["mov_valor"] > 0


def test_ningun_movimiento_antes_de_la_semana_minima_ni_en_el_primer_trimestre(plan):
    semanas = {s.semana for s in plan if s.tipo is TipoSesion.MOVIMIENTOS_FETALES}
    assert SEMANA_MINIMA_DE_MOVIMIENTO == 20
    assert min(semanas) == SEMANA_MINIMA_DE_MOVIMIENTO
    assert all(_trimestre(s) > 1 for s in semanas)


def test_al_menos_dos_sesiones_por_mes_de_cada_tipo(plan):
    por_mes = Counter((s.inicio.strftime("%Y-%m"), s.tipo) for s in plan)
    meses_signos = {m for m, t in por_mes if t is TipoSesion.SIGNOS_MATERNOS}
    meses_mov = {m for m, t in por_mes if t is TipoSesion.MOVIMIENTOS_FETALES}
    assert meses_signos == {"2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10", "2026-11"}
    assert meses_mov == {"2026-09", "2026-10", "2026-11"}
    assert all(n >= 2 for n in por_mes.values()), por_mes


def test_la_semana_y_el_semaforo_son_los_del_sistema(prov, plan):
    codigo = {v: k for k, v in SEMAFOROS.items()}
    for s in plan:
        for lectura in s.paquete["lecturas"]:
            semana = semana_gestacional(INICIO, datetime.fromisoformat(lectura["fecha_hora_captura"]))
            assert semana == s.semana
            assert lectura["id_tiempo_gest"] == prov.semanas[semana].id_tiempo_gest
            esperado = clasificar_lectura(
                hr_valor=None if lectura["hr_valor"] is None else Decimal(lectura["hr_valor"]),
                spo2_valor=None if lectura["spo2_valor"] is None else Decimal(lectura["spo2_valor"]),
                mov_valor=lectura["mov_valor"],
                semana=semana,
                trimestre=_trimestre(semana),
            )
            assert codigo[lectura["id_semaforo"]] == esperado.codigo_global


def test_cada_paquete_cumple_el_contrato_de_ingesta(plan):
    for s in plan:
        SesionMonitoreoEntrada.model_validate_json(json.dumps(s.paquete))


def test_las_claves_son_validas_y_unicas(plan):
    claves = [s.clave for s in plan]
    assert len(set(claves)) == len(claves)
    assert all(clave_valida(c) for c in claves)


def test_el_plan_es_reproducible(hist, prov, plan):
    otra = hist.construir_plan(prov, hist.SEMILLA_POR_OMISION, CORTE)
    assert [(s.clave, s.paquete) for s in otra] == [(s.clave, s.paquete) for s in plan]


def test_otra_semilla_cambia_valores_y_claves(hist, prov, plan):
    otra = hist.construir_plan(prov, hist.SEMILLA_POR_OMISION + 1, CORTE)
    assert {s.clave for s in otra}.isdisjoint({s.clave for s in plan})


def test_una_semana_mas_no_cambia_las_anteriores(hist, prov, plan):
    mas_tarde = hist.construir_plan(prov, hist.SEMILLA_POR_OMISION, CORTE + timedelta(days=7))
    assert [(s.clave, s.paquete) for s in mas_tarde[: len(plan)]] == [(s.clave, s.paquete) for s in plan]
    assert {s.semana for s in mas_tarde[len(plan):]} == {28}


def test_valores_plausibles(plan):
    hr = [float(l["hr_valor"]) for s in plan for l in s.paquete["lecturas"] if l["hr_valor"]]
    spo2 = [float(l["spo2_valor"]) for s in plan for l in s.paquete["lecturas"] if l["spo2_valor"]]
    assert 55 <= min(hr) and max(hr) <= 115
    assert 92 <= min(spo2) and max(spo2) <= 100
