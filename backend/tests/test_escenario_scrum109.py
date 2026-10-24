"""Escenario de demostracion de SCRUM-109: definicion y corte, sin PostgreSQL.

Lo que aqui se prueba no necesita base: que la historia cumple las reglas del
dominio (semana 20, forma de cada lectura, cobertura de asignaciones y
seguimientos, regla de afiliacion del ETL), que el corte nunca deja pasar una
observacion futura, que los semaforos los decide ``clasificar_lectura`` y que
las filas tienen exactamente las columnas del modelo existente. La carga, los
conflictos, el rollback y la preservacion del dataset canonico se prueban
contra PostgreSQL en ``test_escenario_scrum109_postgresql.py``.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.etl import reglas
from app.loader.dataset import (
    SEMANA_MINIMA_DE_MOVIMIENTO,
    _validar_columnas,
    normalizar_fila,
    tabla_operacional,
)
from app.models.enums import EstadoEmbarazo, TipoContacto, TipoSesion
from app.services.ingesta import semana_gestacional

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
PANAMA = ZoneInfo("America/Panama")


def _cargar(nombre: str):
    if nombre in sys.modules:
        return sys.modules[nombre]
    spec = importlib.util.spec_from_file_location(nombre, SCRIPTS / f"{nombre}.py")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = modulo
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture(scope="module")
def d():
    return _cargar("escenario_scrum109_definicion")


@pytest.fixture(scope="module")
def cargador():
    return _cargar("cargar_escenario_scrum109")


@pytest.fixture(scope="module")
def referencias(d):
    """Catalogos con la misma forma que el canonico: semanas 1-40 y tres niveles."""
    generador = _cargar("generate_mock_data")
    return d.Referencias(
        id_clinica=100,
        id_medico=100,
        id_rol_paciente=102,
        semanas={s: (99 + s, generador.calcular_trimestre(s)) for s in range(1, 41)},
        semaforos={"OK": 100, "WARNING": 101, "ERROR": 102},
        password_hash={p.id_usuario: "$argon2id$ficticio" for p in d.PACIENTES if p.con_cuenta},
    )


def _codigos(d, referencias, embarazo):
    resultado = []
    for o in d.observaciones_del_embarazo(embarazo):
        _, _, estados = d.clasificar(o, embarazo, referencias)
        resultado.append((o, estados.codigo_global))
    return resultado


def _embarazo(d, id_embarazo):
    return next(e for e in d.EMBARAZOS if e.id_embarazo == id_embarazo)


# ---------------------------------------------------------------------------
# El grupo de pacientes del escenario
# ---------------------------------------------------------------------------


def test_cinco_pacientes_con_cuenta_y_un_perfil_sin_cuenta(d):
    con_cuenta = [p for p in d.PACIENTES if p.con_cuenta]
    sin_cuenta = [p for p in d.PACIENTES if not p.con_cuenta]
    assert len(con_cuenta) == 5
    assert len(sin_cuenta) == 1
    perfil = sin_cuenta[0]
    assert perfil.id_usuario is None
    assert not [e for e in d.EMBARAZOS if e.id_paciente == perfil.id_paciente]


def test_los_datos_personales_son_completos_y_el_correo_no_puede_ser_de_nadie(d):
    for p in d.PACIENTES:
        assert p.primer_nombre and p.segundo_nombre and p.apellido_paterno and p.apellido_materno
        assert p.correo.endswith("@example.com")
        assert p.correo == p.correo.lower() and " " not in p.correo and p.correo.isascii()
        assert re.fullmatch(r"4-\d{3}-\d{4}", p.cedula)  # Chiriqui, como la clinica 100
        assert re.fullmatch(r"6\d{3}-\d{4}", p.celular)
        if p.contacto_secundario and p.contacto_secundario[0] is TipoContacto.CORREO_ALTERNO:
            assert p.contacto_secundario[1].endswith("@example.com")
    for campo in ("cedula", "correo", "celular"):
        assert len({getattr(p, campo) for p in d.PACIENTES}) == len(d.PACIENTES)


def test_la_protagonista_tiene_su_identidad_completa(d):
    elena = d.paciente(10901)
    assert elena.nombre_completo == "Elena Sofía Araúz Quiel"
    assert elena.correo == "elena.arauz94@example.com"
    assert elena.contacto_secundario == (TipoContacto.TELEFONO_DOMICILIO, "775-4062")


def test_cada_contacto_tiene_un_principal_y_los_secundarios_quedan_en_el_bloque(d, referencias):
    telefonos = d.filas(referencias, corte=d.corte_minimo())["telefono_paciente"]
    for p in d.PACIENTES:
        suyos = [t for t in telefonos if t["id_paciente"] == p.id_paciente]
        assert [t["principal"] for t in suyos].count(True) == 1
        assert len(suyos) == 1 + (p.contacto_secundario is not None)
    ids = [t["id_telefono_paciente"] for t in telefonos]
    assert len(set(ids)) == len(ids)
    assert all(d.BLOQUE_ENTIDADES[0] <= i <= d.BLOQUE_ENTIDADES[1] for i in ids)


def test_la_protagonista_tiene_dos_embarazos_con_dispositivos_distintos(d):
    protagonista = d.PACIENTES[0]
    suyos = sorted((e for e in d.EMBARAZOS if e.id_paciente == protagonista.id_paciente), key=lambda e: e.fecha_inicio)
    assert [e.estado for e in suyos] == [EstadoEmbarazo.FINALIZADO, EstadoEmbarazo.ACTIVO]
    anterior, actual = suyos
    assert anterior.codigo_dispositivo != actual.codigo_dispositivo
    assert actual.codigo_dispositivo == d.CODIGO_DISPOSITIVO_PRINCIPAL
    # Antecedentes coherentes: el actual cuenta el embarazo y el parto anteriores.
    assert (anterior.numero_gestas, anterior.numero_partos) == (1, 0)
    assert (actual.numero_gestas, actual.numero_partos) == (2, 1)
    assert anterior.fecha_cierre < actual.fecha_inicio


def test_el_dispositivo_principal_es_el_que_reconocen_las_herramientas(d, cargador):
    assert d.CODIGO_DISPOSITIVO_PRINCIPAL == cargador.provisionar_demo.CODIGO_DISPOSITIVO_DEMO
    assert [e.codigo_dispositivo for e in d.EMBARAZOS].count(d.CODIGO_DISPOSITIVO_PRINCIPAL) == 1
    patron = _cargar("dispositivo_gestante").PATRON_DE_DISPOSITIVO
    assert all(patron.fullmatch(e.codigo_dispositivo) for e in d.EMBARAZOS)


def test_el_embarazo_anterior_tiene_unos_nueve_meses_de_seguimiento(d):
    anterior = _embarazo(d, 10901)
    dias = (anterior.fecha_cierre - anterior.fecha_inicio).days
    assert 255 <= dias <= 290
    obs = d.observaciones_del_embarazo(anterior)
    assert (obs[-1].dia - obs[0].dia).days >= 240


@pytest.mark.parametrize("dia", [date(2026, 11, 20), date(2026, 11, 23)])
def test_el_embarazo_actual_va_por_el_septimo_mes_durante_la_sustentacion(d, dia):
    actual = _embarazo(d, 10902)
    semana = semana_gestacional(actual.fecha_inicio, datetime.combine(dia, time(12), PANAMA))
    assert 28 <= semana <= 32


def test_cada_paciente_tiene_como_mucho_un_embarazo_en_curso(d):
    activos = Counter(e.id_paciente for e in d.EMBARAZOS if e.estado is EstadoEmbarazo.ACTIVO)
    assert max(activos.values()) == 1


# ---------------------------------------------------------------------------
# Las observaciones y las reglas del dominio
# ---------------------------------------------------------------------------


def test_no_hay_movimientos_antes_de_la_semana_20_y_empiezan_en_ella(d):
    for e in d.EMBARAZOS:
        semanas = [
            semana_gestacional(e.fecha_inicio, o.captura)
            for o in d.observaciones_del_embarazo(e)
            if o.tipo is TipoSesion.MOVIMIENTOS_FETALES
        ]
        assert semanas, e.id_embarazo
        assert min(semanas) == SEMANA_MINIMA_DE_MOVIMIENTO


def test_ausencia_de_movimiento_no_es_cero(d):
    for o in d.todas_las_observaciones():
        if o.tipo is TipoSesion.MOVIMIENTOS_FETALES:
            assert o.mov is not None and o.mov > 0 and o.hr is None and o.spo2 is None
        else:
            assert o.mov is None and o.hr is not None and o.spo2 is not None


def test_al_menos_dos_registros_de_signos_por_mes_completo(d):
    for e in d.EMBARAZOS:
        signos = [o.dia for o in d.observaciones_del_embarazo(e) if o.tipo is TipoSesion.SIGNOS_MATERNOS]
        por_mes = Counter((dia.year, dia.month) for dia in signos)
        primero, ultimo = signos[0], signos[-1]
        mes = date(primero.year, primero.month, 1)
        while mes <= ultimo:
            siguiente = (mes + timedelta(days=32)).replace(day=1)
            completo = mes >= primero.replace(day=1) and primero <= mes and siguiente - timedelta(days=1) <= ultimo
            if completo:
                assert por_mes[(mes.year, mes.month)] >= 2, (e.id_embarazo, mes)
            mes = siguiente


def test_las_observaciones_caen_dentro_del_periodo_y_en_el_catalogo(d):
    for e in d.EMBARAZOS:
        for o in d.observaciones_del_embarazo(e):
            assert e.fecha_inicio <= o.dia < e.fin_de_observaciones
            assert 1 <= semana_gestacional(e.fecha_inicio, o.captura) <= 40
            # Mismo dia en UTC y en Panama: ninguna lectura cambia de dia
            # clinico segun la zona con la que se mire.
            assert o.captura.astimezone(timezone.utc).date() == o.dia
    assert max(o.dia for o in d.todas_las_observaciones()) <= d.FIN_DEL_ESCENARIO


def test_las_reglas_de_medico_y_afiliacion_del_etl_aceptan_cada_dia(d, referencias):
    filas = d.filas(referencias, None)
    afiliacion = [reglas.AfiliacionClinica(id_clinica=100, fecha_inicio=date(2025, 1, 1), fecha_final=None)]
    seguimientos = defaultdict(list)
    for s in filas["seguimiento_clinico"]:
        seguimientos[s["id_embarazo"]].append(
            reglas.PeriodoDeSeguimiento(s["id_seguimiento"], s["id_medico"], s["fecha_asignacion"], s["fecha_fin"])
        )
    asignaciones = {a["id_embarazo"]: a for a in filas["asignacion_dispositivo"]}
    for sesion in filas["sesion_monitoreo"]:
        dia = reglas.fecha_clinica(sesion["fecha_inicio"])
        medico = reglas.medico_responsable(seguimientos[sesion["id_embarazo"]], dia, id_lectura=sesion["id_sesion"])
        reglas.verificar_afiliacion(afiliacion, dia, id_medico=medico, id_clinica_embarazo=100, id_lectura=0)
        a = asignaciones[sesion["id_embarazo"]]
        assert a["id_dispositivo"] == sesion["id_dispositivo"]
        assert a["fecha_inicio"] <= dia and (a["fecha_fin"] is None or dia <= a["fecha_fin"])


def test_la_duracion_estimada_es_un_numero_entero_de_semanas(d):
    for e in d.EMBARAZOS:
        assert reglas.duracion_estimada_semanas(e.fecha_inicio, e.fecha_probable_parto, id_embarazo=e.id_embarazo) == 40


# ---------------------------------------------------------------------------
# Semaforos: los decide el ETL, el escenario solo los disena
# ---------------------------------------------------------------------------


def test_ningun_error_y_solo_categorias_existentes(d, referencias):
    codigos = Counter(c for e in d.EMBARAZOS for _, c in _codigos(d, referencias, e))
    assert set(codigos) == {"OK", "WARNING"}


@pytest.mark.parametrize("id_embarazo", [10903, 10906])
def test_pacientes_con_lecturas_ok(d, referencias, id_embarazo):
    assert {c for _, c in _codigos(d, referencias, _embarazo(d, id_embarazo))} == {"OK"}


def test_warning_recientes_hasta_el_final(d, referencias):
    pares = _codigos(d, referencias, _embarazo(d, 10904))
    desde = date(2026, 10, 5)
    assert all(c == "WARNING" for o, c in pares if o.dia >= desde)
    assert all(c == "OK" for o, c in pares if o.dia < desde)
    assert pares[-1][1] == "WARNING"


@pytest.mark.parametrize(
    "id_embarazo, desde, hasta",
    [(10905, date(2026, 8, 17), date(2026, 9, 6)), (10902, date(2026, 7, 13), date(2026, 7, 26)),
     (10901, date(2025, 7, 14), date(2025, 7, 27))],
)
def test_warning_historicos_seguidos_de_ok(d, referencias, id_embarazo, desde, hasta):
    pares = _codigos(d, referencias, _embarazo(d, id_embarazo))
    warning = [o for o, c in pares if c == "WARNING"]
    assert warning and all(desde <= o.dia <= hasta for o in warning)
    assert all(c == "OK" for o, c in pares if o.dia > hasta)


def test_hay_meses_con_cinco_embarazos_ok_para_la_vista_administrativa(d, referencias):
    """El minimo de la vista es 5 embarazos distintos por provincia, mes y semaforo."""
    por_mes = defaultdict(set)
    for e in d.EMBARAZOS:
        for o, c in _codigos(d, referencias, e):
            if c == "OK":
                por_mes[(o.dia.year, o.dia.month)].add(e.id_embarazo)
    publicables = sorted(m for m, embarazos in por_mes.items() if len(embarazos) >= 5)
    assert {(2026, 7), (2026, 8), (2026, 9)} <= set(publicables)


# ---------------------------------------------------------------------------
# Traduccion al modelo existente
# ---------------------------------------------------------------------------


def test_las_filas_tienen_exactamente_las_columnas_del_modelo(d, referencias):
    filas = d.filas(referencias, None)
    for tabla, lista in filas.items():
        modelo = tabla_operacional(tabla)
        _validar_columnas(tabla, modelo, lista)
        for fila in lista:
            normalizar_fila(modelo, fila)  # enumeraciones y tipos del modelo


def test_identificadores_dentro_de_sus_bloques_y_sin_repetir(d, referencias):
    filas = d.filas(referencias, None)
    for tabla, (columna, (primero, ultimo)) in d.BLOQUES.items():
        ids = [f[columna] for f in filas[tabla]]
        assert len(ids) == len(set(ids)), tabla
        assert all(primero <= i <= ultimo for i in ids), tabla
    assert {f["id_usuario"] for f in filas["usuario_paciente"]} == {f["id_usuario"] for f in filas["usuario"]}


def test_una_sola_asignacion_por_dispositivo_y_un_seguimiento_principal_por_embarazo(d, referencias):
    filas = d.filas(referencias, None)
    assert Counter(a["id_dispositivo"] for a in filas["asignacion_dispositivo"]).most_common(1)[0][1] == 1
    assert Counter(s["id_embarazo"] for s in filas["seguimiento_clinico"]).most_common(1)[0][1] == 1
    cerrado = next(s for s in filas["seguimiento_clinico"] if s["id_embarazo"] == 10901)
    assert cerrado["fecha_fin"] == _embarazo(d, 10901).fecha_cierre and not cerrado["activo"]


def test_ningun_embarazo_finalizado_conserva_un_seguimiento_vigente(d, referencias):
    """El acceso del medico exige un seguimiento activo y vigente hoy.

    Dejar abierto el de un embarazo cerrado daria al medico el episodio
    anterior eludiendo esa regla; el escenario no lo hace (ver la seccion
    «Por que el medico no ve el embarazo anterior» de la guia).
    """
    filas = d.filas(referencias, None)
    cierres = {e.id_embarazo: e.fecha_cierre for e in d.EMBARAZOS if e.cerrado}
    assert cierres
    for s in filas["seguimiento_clinico"]:
        if s["id_embarazo"] in cierres:
            assert not s["activo"]
            assert s["fecha_fin"] is not None and s["fecha_fin"] <= cierres[s["id_embarazo"]]


def test_la_precarga_no_inventa_trazabilidad_de_sincronizacion(d, referencias):
    filas = d.filas(referencias, None)
    assert all(f["fecha_hora_sincronizacion"] is None for f in filas["lectura_biometrica"])


def test_la_definicion_es_determinista(d, referencias):
    assert d.filas(referencias, None) == d.filas(referencias, None)


# ---------------------------------------------------------------------------
# El corte
# ---------------------------------------------------------------------------

AHORA = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)


def test_el_corte_por_omision_es_ahora_en_panama(cargador):
    corte = cargador.interpretar_corte(None, AHORA)
    assert corte == AHORA and corte.utcoffset() == timedelta(hours=-5)


def test_una_fecha_es_el_final_de_ese_dia_en_panama(cargador):
    corte = cargador.interpretar_corte("2026-11-10", AHORA)
    assert corte == datetime(2026, 11, 10, 23, 59, 59, 999999, tzinfo=PANAMA)


def test_un_instante_sin_zona_se_interpreta_en_panama(cargador):
    assert cargador.interpretar_corte("2026-11-10T08:00", AHORA) == datetime(2026, 11, 10, 8, 0, tzinfo=PANAMA)
    assert cargador.interpretar_corte("2026-11-10T13:00+00:00", AHORA) == datetime(2026, 11, 10, 8, 0, tzinfo=PANAMA)


@pytest.mark.parametrize("texto", ["2026-11-20", "2026-11-19T15:00:01+00:00", "2027-01-01"])
def test_un_corte_futuro_se_rechaza(cargador, texto):
    with pytest.raises(cargador.ErrorDeCorte):
        cargador.interpretar_corte(texto, AHORA)


@pytest.mark.parametrize("texto", ["2026-05-31", "2025-12-01", "no-es-fecha"])
def test_un_corte_anterior_a_la_estructura_o_invalido_se_rechaza(cargador, texto):
    with pytest.raises(cargador.ErrorDeCorte):
        cargador.interpretar_corte(texto, AHORA)


def test_ninguna_observacion_posterior_al_corte(d, referencias, cargador):
    corte = cargador.interpretar_corte("2026-10-10", AHORA)
    filas = d.filas(referencias, corte)
    assert filas["lectura_biometrica"]
    assert all(f["fecha_hora_captura"] <= corte for f in filas["lectura_biometrica"])
    assert all(f["fecha_inicio"] <= corte for f in filas["sesion_monitoreo"])


def test_avanzar_el_corte_solo_agrega_filas(d, referencias, cargador):
    antes = d.filas(referencias, cargador.interpretar_corte("2026-10-10", AHORA))
    despues = d.filas(referencias, cargador.interpretar_corte("2026-11-18", AHORA))
    for tabla in antes:
        assert all(fila in despues[tabla] for fila in antes[tabla]), tabla
    assert len(despues["lectura_biometrica"]) > len(antes["lectura_biometrica"])
    # Estructura completa con cualquier corte.
    for tabla in ("paciente", "usuario", "embarazo", "asignacion_dispositivo"):
        assert antes[tabla] == despues[tabla]


def test_el_cli_rechaza_un_corte_futuro_sin_tocar_la_base(cargador, capsys):
    assert cargador.main(["cargar", "--corte", "2099-01-01"]) == cargador.CODIGO_DE_ERROR
    assert "posterior al momento" in capsys.readouterr().err
