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

import dataclasses
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
    # 102: la clinica de Darien del dataset canonico; roles y especialidad canonicos.
    cuentas = [p.id_usuario for p in d.PACIENTES if p.con_cuenta] + [d.MEDICO_DE_LA_DEMO.id_usuario]
    return d.Referencias(
        id_clinica=102,
        id_rol_paciente=102,
        id_rol_medico=101,
        id_especialidad=100,
        semanas={s: (99 + s, generador.calcular_trimestre(s)) for s in range(1, 41)},
        semaforos={"OK": 100, "WARNING": 101, "ERROR": 102},
        password_hash={cuenta: "$argon2id$ficticio" for cuenta in cuentas},
    )


def _codigos(d, referencias, embarazo):
    """(lectura, semaforo) de cada lectura del embarazo, en orden de captura."""
    resultado = []
    for o in d.observaciones_del_embarazo(embarazo):
        for lectura in o.lecturas:
            _, _, estados = d.clasificar(lectura, embarazo, referencias)
            resultado.append((lectura, estados.codigo_global))
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
        # Originarias de Darien por definicion de la demo: prefijo provincial 5,
        # tomo y asiento. No es la clinica la que fija la cedula.
        assert re.fullmatch(r"5-\d{1,4}-\d{1,6}", p.cedula)
        assert not p.cedula.startswith("5-900-")  # el tomo de las del dataset canonico
        assert re.fullmatch(r"6\d{3}-\d{4}", p.celular)
        if p.contacto_secundario and p.contacto_secundario[0] is TipoContacto.CORREO_ALTERNO:
            assert p.contacto_secundario[1].endswith("@example.com")
    for campo in ("cedula", "correo", "celular"):
        assert len({getattr(p, campo) for p in d.PACIENTES}) == len(d.PACIENTES)


def test_la_protagonista_tiene_su_identidad_completa(d):
    elena = d.paciente(10901)
    assert elena.nombre_completo == "Elena Sofía Araúz Quiel"
    assert elena.correo == "elena.arauz94@example.com"
    assert elena.cedula == "5-708-1834"
    assert elena.contacto_secundario == (TipoContacto.TELEFONO_DOMICILIO, "299-6408")


def test_la_demo_es_de_darien_con_un_medico_propio(d):
    assert d.PROVINCIA_DE_LA_DEMO == "Darién"
    assert d.PREFIJO_DE_CEDULA == "5"
    m = d.MEDICO_DE_LA_DEMO
    assert m.nombre_completo == "Itzel Marina Barría Cortés"
    assert m.correo == "itzel.barria@example.com" == m.correo.lower()
    assert d.BLOQUE_ENTIDADES[0] <= m.id_medico <= d.BLOQUE_ENTIDADES[1]
    assert m.id_usuario not in {p.id_usuario for p in d.PACIENTES}
    # Afiliado antes del primer embarazo del escenario (2025-01-13).
    assert m.inicio_afiliacion == date(2025, 1, 1) <= min(e.fecha_inicio for e in d.EMBARAZOS)
    # Ningun medico canonico: su cuenta no esta en el patron del dataset.
    assert not re.fullmatch(r"medico\d+@example\.com", m.correo)


def test_el_medico_de_la_demo_sigue_los_seis_embarazos_y_solo_ellos(d, referencias):
    filas = d.filas(referencias, None)
    seguimientos = filas["seguimiento_clinico"]
    assert {s["id_embarazo"] for s in seguimientos} == {e.id_embarazo for e in d.EMBARAZOS}
    assert {s["id_medico"] for s in seguimientos} == {d.MEDICO_DE_LA_DEMO.id_medico}
    assert all(s["rol_seguimiento"] == "PRINCIPAL" for s in seguimientos)
    for e in d.EMBARAZOS:
        s = next(x for x in seguimientos if x["id_embarazo"] == e.id_embarazo)
        assert s["fecha_asignacion"] == e.fecha_inicio
        assert (s["fecha_fin"], s["activo"]) == ((e.fecha_cierre, False) if e.cerrado else (None, True))
    # Cinco pacientes con embarazo en curso; Paula sin cuenta ni embarazo.
    en_curso = {e.id_paciente for e in d.EMBARAZOS if not e.cerrado}
    assert en_curso == {10901, 10902, 10903, 10904, 10905}
    assert filas["medico_clinica"] == [{
        "id_medico": d.MEDICO_DE_LA_DEMO.id_medico, "id_clinica": 102,
        "fecha_inicio": date(2025, 1, 1), "fecha_final": None, "activo": True,
    }]
    assert filas["usuario_medico"] == [{"id_usuario": 10950, "id_medico": 10950}]
    assert [u["id_rol"] for u in filas["usuario"] if u["id_usuario"] == 10950] == [101]


def test_un_seguimiento_principal_superpuesto_se_rechaza(d, monkeypatch):
    """Control negativo: dos PRINCIPAL el mismo dia es la ambiguedad que el ETL rechaza."""
    originales = d.seguimientos()
    otro = reglas.PeriodoDeSeguimiento(10902, 104, date(2026, 9, 1), None)
    monkeypatch.setattr(d, "seguimientos", lambda: originales + [otro])
    with pytest.raises(d.ErrorDeDefinicion, match="DerivacionAmbigua"):
        d.validar_definicion()
    with pytest.raises(reglas.DerivacionAmbigua):
        reglas.medico_responsable(
            [s for s in d.seguimientos() if s.id_seguimiento == 10902], date(2026, 10, 1), id_lectura=0
        )


def test_una_afiliacion_que_empieza_tarde_se_rechaza(d, monkeypatch):
    tarde = dataclasses.replace(d.MEDICO_DE_LA_DEMO, inicio_afiliacion=date(2025, 2, 1))
    monkeypatch.setattr(d, "MEDICO_DE_LA_DEMO", tarde)
    with pytest.raises(d.ErrorDeDefinicion, match="embarazo 10901, 2025-01-13"):
        d.validar_definicion()


def test_los_dos_embarazos_de_elena_no_se_solapan_y_son_coherentes(d):
    """El intervalo es plausible pero corto: se documenta, no se impone."""
    anterior, actual = sorted(
        (e for e in d.EMBARAZOS if e.id_paciente == 10901), key=lambda e: e.fecha_inicio
    )
    assert anterior.fecha_cierre < actual.fecha_inicio
    assert (actual.fecha_inicio - anterior.fecha_cierre).days == 196
    # fecha_inicio es el inicio del computo gestacional: FPP = inicio + 280 dias.
    assert anterior.fecha_probable_parto - anterior.fecha_inicio == timedelta(days=280)
    # Cierre a termino: semana 40 por la aritmetica del sistema (39+0).
    assert semana_gestacional(anterior.fecha_inicio, datetime(2025, 10, 13, tzinfo=d.ZONA)) == 40
    assert max(o.dia for o in d.observaciones_del_embarazo(anterior)) < anterior.fecha_cierre
    assert (anterior.numero_gestas, anterior.numero_partos) == (1, 0)
    assert (actual.numero_gestas, actual.numero_partos) == (2, 1)
    elena = d.paciente(10901)
    edad = lambda dia: dia.year - elena.fecha_nac.year - ((dia.month, dia.day) < (elena.fecha_nac.month, elena.fecha_nac.day))  # noqa: E731
    assert (edad(anterior.fecha_inicio), edad(actual.fecha_inicio)) == (30, 31)


def test_una_cedula_de_otra_provincia_no_pasa_la_validacion(d, monkeypatch):
    elena = d.paciente(10901)
    otra = dataclasses.replace(elena, cedula="4-708-1834")
    monkeypatch.setattr(d, "PACIENTES", (otra,) + d.PACIENTES[1:])
    with pytest.raises(d.ErrorDeDefinicion, match="cedula"):
        d.validar_definicion()


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
        for lectura in o.lecturas:
            if o.tipo is TipoSesion.MOVIMIENTOS_FETALES:
                assert lectura.mov is not None and lectura.mov > 0
                assert lectura.hr is None and lectura.spo2 is None
            else:
                assert lectura.mov is None and lectura.hr is not None and lectura.spo2 is not None


def test_cada_sesion_de_signos_tiene_cinco_lecturas_conjuntas_y_movimientos_una(d, referencias):
    filas = d.filas(referencias, None)
    por_sesion = defaultdict(list)
    for lectura in filas["lectura_biometrica"]:
        por_sesion[lectura["id_sesion"]].append(lectura)
    sesiones = filas["sesion_monitoreo"]
    assert {s["id_sesion"] for s in sesiones} == set(por_sesion)
    for s in sesiones:
        lecturas = sorted(por_sesion[s["id_sesion"]], key=lambda f: f["fecha_hora_captura"])
        capturas = [f["fecha_hora_captura"] for f in lecturas]
        assert capturas[0] == s["fecha_inicio"] and capturas[-1] == s["fecha_fin"]
        if s["tipo_sesion"] == TipoSesion.SIGNOS_MATERNOS.value:
            assert len(lecturas) == 5, s["id_sesion"]
            # Conjuntas: FC y SpO2 en la misma fila, nunca cinco de cada una.
            assert all(f["hr_valor"] is not None and f["spo2_valor"] is not None for f in lecturas)
            assert all(f["mov_valor"] is None for f in lecturas)
            assert [b - a for a, b in zip(capturas, capturas[1:])] == [timedelta(minutes=1)] * 4
            # Valores sinteticos que varian, no cinco copias de una fila.
            assert len({(f["hr_valor"], f["spo2_valor"]) for f in lecturas}) == 5
        else:
            assert len(lecturas) == 1 and lecturas[0]["mov_valor"] is not None, s["id_sesion"]
            assert s["fecha_inicio"] == s["fecha_fin"]
    ids = [f["id_lectura"] for f in filas["lectura_biometrica"]]
    assert len(ids) == len(set(ids))


def test_las_lecturas_de_una_sesion_comparten_el_semaforo_del_caso(d, referencias):
    """La variacion dentro de la sesion no cambia la banda de SIM-1.0 de su valor base."""
    for e in d.EMBARAZOS:
        for o in d.observaciones_del_embarazo(e):
            codigos = {d.clasificar(lectura, e, referencias)[2].codigo_global for lectura in o.lecturas}
            assert len(codigos) == 1, (e.id_embarazo, o.id_sesion, codigos)


def test_los_identificadores_de_lectura_siguen_la_captura_dentro_del_tramo(d):
    for e in d.EMBARAZOS:
        lecturas = [lectura for o in d.observaciones_del_embarazo(e) for lectura in o.lecturas]
        base = d.BASE_MONITOREO + (e.id_embarazo - 10900) * d.TRAMO_POR_EMBARAZO
        assert [lectura.id_lectura for lectura in lecturas] == list(range(base + 1, base + 1 + len(lecturas)))
        assert [lectura.captura for lectura in lecturas] == sorted(lectura.captura for lectura in lecturas)
        assert len(lecturas) < d.TRAMO_POR_EMBARAZO


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
    afiliacion = [reglas.AfiliacionClinica(id_clinica=102, fecha_inicio=date(2025, 1, 1), fecha_final=None)]
    seguimientos = defaultdict(list)
    for s in filas["seguimiento_clinico"]:
        seguimientos[s["id_embarazo"]].append(
            reglas.PeriodoDeSeguimiento(s["id_seguimiento"], s["id_medico"], s["fecha_asignacion"], s["fecha_fin"])
        )
    asignaciones = {a["id_embarazo"]: a for a in filas["asignacion_dispositivo"]}
    for sesion in filas["sesion_monitoreo"]:
        dia = reglas.fecha_clinica(sesion["fecha_inicio"])
        medico = reglas.medico_responsable(seguimientos[sesion["id_embarazo"]], dia, id_lectura=sesion["id_sesion"])
        reglas.verificar_afiliacion(afiliacion, dia, id_medico=medico, id_clinica_embarazo=102, id_lectura=0)
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
    assert pares[-1][1] == "OK"


def test_ines_termina_en_ok_con_el_corte_de_la_base_inicial(d, referencias):
    """La ultima lectura cargada de Ines es OK tras sus WARNING de agosto-septiembre."""
    corte = datetime(2026, 10, 24, 16, 49, 54, tzinfo=PANAMA)
    pares = [(lectura, c) for lectura, c in _codigos(d, referencias, _embarazo(d, 10905)) if lectura.captura <= corte]
    assert pares[-1][1] == "OK" and any(c == "WARNING" for _, c in pares)


def test_rosa_termina_en_warning_con_el_corte_de_la_base_inicial(d, referencias):
    corte = datetime(2026, 10, 24, 16, 49, 54, tzinfo=PANAMA)
    pares = [(lectura, c) for lectura, c in _codigos(d, referencias, _embarazo(d, 10904)) if lectura.captura <= corte]
    assert pares[-1][1] == "WARNING" and pares[-1][0].dia >= date(2026, 10, 5)


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
    # Cada cuenta del escenario tiene exactamente un vinculo: de paciente o de medico.
    pacientes = {f["id_usuario"] for f in filas["usuario_paciente"]}
    medicos = {f["id_usuario"] for f in filas["usuario_medico"]}
    assert not pacientes & medicos
    assert pacientes | medicos == {f["id_usuario"] for f in filas["usuario"]}


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


def test_un_corte_a_mitad_de_sesion_no_la_parte(d, referencias, cargador):
    """La sesion de Elena del lunes 2026-10-19 va de 09:30 a 09:34: a las 09:32 no entra."""
    corte = cargador.interpretar_corte("2026-10-19T09:32", AHORA)
    filas = d.filas(referencias, corte)
    sesion = next(
        o for o in d.observaciones_del_embarazo(_embarazo(d, 10902))
        if o.dia == date(2026, 10, 19) and o.tipo is TipoSesion.SIGNOS_MATERNOS
    )
    assert sesion.captura <= corte < sesion.fin
    assert sesion.id_sesion not in {s["id_sesion"] for s in filas["sesion_monitoreo"]}
    cargadas = {s["id_sesion"] for s in filas["sesion_monitoreo"]}
    assert all(f["id_sesion"] in cargadas for f in filas["lectura_biometrica"])
    assert not {lectura.id_lectura for lectura in sesion.lecturas} & {f["id_lectura"] for f in filas["lectura_biometrica"]}


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
