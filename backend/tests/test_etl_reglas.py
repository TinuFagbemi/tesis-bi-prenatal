"""Pure rules of the analytic ETL (SCRUM-69): SIM-1.0 thresholds and derivations.

Offline: nothing here opens a connection. The expected state of every boundary
is written by hand from Table 4 of Chapter III, so these tests cannot pass by
repeating what the implementation computes.

All data is fictitious and simulated.
"""

import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.etl import reglas
from app.etl.reglas import (
    ERROR,
    OK,
    VERSION_SIM_1_0,
    WARNING,
    AfiliacionClinica,
    CatalogoIncoherente,
    Contacto,
    DerivacionAmbigua,
    DerivacionImposible,
    LecturaInvalida,
    NivelDeSemaforo,
    PeriodoDeSeguimiento,
    ReglaNoDefinida,
    VersionDeReglasDesconocida,
    clasificar_hr,
    clasificar_lectura,
    clasificar_movimiento,
    clasificar_spo2,
    clinica_contextual,
    duracion_estimada_semanas,
    fecha_clinica,
    medico_responsable,
    nombre_completo,
    severidad_global,
    telefono_principal,
    validar_catalogo_de_semaforo,
    verificar_afiliacion,
)
from tests.test_generate_mock_data import cargar_generador

PANAMA = timezone(timedelta(hours=-5))

# ---------------------------------------------------------------------------
# HR -- Tabla 4: alerta < 55, precaución 55-59.99, normal 60-99.99,
# precaución 100-110, alerta > 110
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        ("54", ERROR),
        ("54.99", ERROR),
        ("55", WARNING),
        ("57", WARNING),
        ("59", WARNING),
        ("59.99", WARNING),
        ("60", OK),
        ("99", OK),
        ("99.99", OK),
        ("100", WARNING),  # listed as normal and as caution: higher severity wins
        ("110", WARNING),
        ("110.01", ERROR),
        ("111", ERROR),
        ("0.01", ERROR),
        ("999.99", ERROR),
    ],
)
def test_frontera_hr(valor, esperado):
    assert clasificar_hr(Decimal(valor)) == esperado


def test_la_regla_de_hr_es_exhaustiva_y_no_deja_ningun_intervalo_sin_clasificar():
    """Ningún valor detiene la ejecución: la tabla cubre la recta continua."""
    valor = Decimal("40")
    while valor <= Decimal("130"):
        assert clasificar_hr(valor) in {OK, WARNING, ERROR}
        valor += Decimal("0.01")


def test_hr_acepta_enteros_y_rechaza_float_y_bool():
    assert clasificar_hr(80) == OK
    with pytest.raises(LecturaInvalida):
        clasificar_hr(80.0)
    with pytest.raises(LecturaInvalida):
        clasificar_hr(True)


# ---------------------------------------------------------------------------
# SpO2 -- Tabla 4: normal >= 95, precaución 92-94, alerta < 92; frontera 95
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        ("0", ERROR),
        ("91", ERROR),
        ("91.99", ERROR),
        ("92", WARNING),
        ("94", WARNING),
        ("94.5", WARNING),  # continuous value: no gap between 94 and 95
        ("94.99", WARNING),
        ("95", OK),
        ("99", OK),
        ("100", OK),
    ],
)
def test_frontera_spo2(valor, esperado):
    assert clasificar_spo2(Decimal(valor)) == esperado


# ---------------------------------------------------------------------------
# Movimiento fetal -- SIM-1.0: umbral 10, 50 % = 5
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [(0, ERROR), (4, ERROR), (5, WARNING), (9, WARNING), (10, OK), (14, OK), (40, OK)],
)
@pytest.mark.parametrize(("semana", "trimestre"), [(20, 2), (27, 2), (28, 3), (40, 3)])
def test_frontera_movimiento(valor, esperado, semana, trimestre):
    assert (
        clasificar_movimiento(valor, semana=semana, trimestre=trimestre) == esperado
    )


def test_movimiento_antes_de_la_semana_20_es_invalido():
    with pytest.raises(LecturaInvalida, match="semana 19"):
        clasificar_movimiento(12, semana=19, trimestre=2)


def test_movimiento_en_un_trimestre_sin_umbral_no_tiene_regla():
    with pytest.raises(ReglaNoDefinida, match="trimestre 1"):
        clasificar_movimiento(12, semana=20, trimestre=1)


@pytest.mark.parametrize("valor", [-1, True, 3.0])
def test_movimiento_rechaza_valores_que_no_son_un_conteo(valor):
    with pytest.raises(LecturaInvalida):
        clasificar_movimiento(valor, semana=30, trimestre=3)


def test_version_desconocida_se_rechaza_en_cada_metrica():
    for clasificar in (
        lambda: clasificar_hr(Decimal("80"), version="SIM-9"),
        lambda: clasificar_spo2(Decimal("97"), version="SIM-9"),
        lambda: clasificar_movimiento(12, semana=30, trimestre=3, version="SIM-9"),
    ):
        with pytest.raises(VersionDeReglasDesconocida):
            clasificar()


def test_el_umbral_sim_1_0_es_el_del_generador_del_dataset():
    """One number, two places: the rules own it, the generator must agree."""
    generador = cargar_generador()
    umbrales = reglas.umbrales_de(VERSION_SIM_1_0)

    assert set(umbrales.movimiento_por_trimestre.values()) == {
        generador.UMBRAL_MOVIMIENTOS_SIMULACION
    }


def test_los_valores_que_fabrica_el_generador_caen_en_su_propio_estado():
    """The generator draws values for a level; the rules must give that level back."""
    generador = cargar_generador()
    estado_original = random.getstate()
    try:
        random.seed(20260810)
        for _ in range(500):
            for estado in (OK, WARNING, ERROR):
                hr, spo2 = generador.generar_valores_hr_spo2(estado)
                assert (
                    severidad_global((clasificar_hr(hr), clasificar_spo2(spo2)))
                    == estado
                )
                movimiento = generador.generar_movimientos(estado)
                assert (
                    clasificar_movimiento(movimiento, semana=30, trimestre=3) == estado
                )
    finally:
        random.setstate(estado_original)


# ---------------------------------------------------------------------------
# Severidad global y lectura completa
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("estados", "esperado"),
    [
        ((OK, OK, None), OK),
        ((OK, WARNING, None), WARNING),
        ((WARNING, OK, None), WARNING),
        ((ERROR, OK, None), ERROR),
        ((WARNING, ERROR, None), ERROR),
        ((None, None, WARNING), WARNING),
    ],
)
def test_severidad_global_es_la_mayor(estados, esperado):
    assert severidad_global(estados) == esperado


def test_severidad_global_sin_metricas_aplicables_se_rechaza():
    with pytest.raises(LecturaInvalida):
        severidad_global((None, None, None))


def test_lectura_de_signos_deja_el_estado_de_movimiento_en_null():
    estados = clasificar_lectura(
        hr_valor=Decimal("104"),
        spo2_valor=Decimal("97"),
        mov_valor=None,
        semana=8,
        trimestre=1,
    )

    assert (estados.estado_hr, estados.estado_spo2, estados.estado_mov) == (
        WARNING,
        OK,
        None,
    )
    assert estados.codigo_global == WARNING


def test_lectura_de_movimiento_deja_los_estados_de_signos_en_null():
    estados = clasificar_lectura(
        hr_valor=None, spo2_valor=None, mov_valor=0, semana=25, trimestre=2
    )

    # Zero movements is a real count, classified; it never becomes NULL.
    assert (estados.estado_hr, estados.estado_spo2, estados.estado_mov) == (
        None,
        None,
        ERROR,
    )
    assert estados.codigo_global == ERROR


@pytest.mark.parametrize(
    ("hr", "spo2", "mov"),
    [
        (Decimal("80"), None, None),
        (None, Decimal("97"), None),
        (Decimal("80"), Decimal("97"), 10),
        (None, None, None),
    ],
)
def test_forma_invalida_se_rechaza(hr, spo2, mov):
    with pytest.raises(LecturaInvalida, match="hr_valor y spo2_valor"):
        clasificar_lectura(
            hr_valor=hr, spo2_valor=spo2, mov_valor=mov, semana=30, trimestre=3
        )


# ---------------------------------------------------------------------------
# Catálogo del semáforo
# ---------------------------------------------------------------------------


def _niveles(**cambios):
    base = {
        OK: NivelDeSemaforo(100, OK, 1, VERSION_SIM_1_0),
        WARNING: NivelDeSemaforo(101, WARNING, 2, VERSION_SIM_1_0),
        ERROR: NivelDeSemaforo(102, ERROR, 3, VERSION_SIM_1_0),
    }
    base.update(cambios)
    return list(base.values())


def test_catalogo_valido_devuelve_el_id_de_cada_codigo():
    assert validar_catalogo_de_semaforo(_niveles()) == {OK: 100, WARNING: 101, ERROR: 102}


def test_catalogo_con_prioridad_fuera_de_orden_se_rechaza():
    with pytest.raises(CatalogoIncoherente, match="prioridad"):
        validar_catalogo_de_semaforo(
            _niveles(**{WARNING: NivelDeSemaforo(101, WARNING, 3, VERSION_SIM_1_0),
                        ERROR: NivelDeSemaforo(102, ERROR, 2, VERSION_SIM_1_0)})
        )


def test_catalogo_incompleto_se_rechaza():
    with pytest.raises(CatalogoIncoherente):
        validar_catalogo_de_semaforo(_niveles()[:2])


def test_catalogo_de_otra_version_se_rechaza():
    with pytest.raises(VersionDeReglasDesconocida, match="SIM-2.0"):
        validar_catalogo_de_semaforo(
            _niveles(**{OK: NivelDeSemaforo(100, OK, 1, "SIM-2.0")})
        )


# ---------------------------------------------------------------------------
# Fecha clínica y médico responsable
# ---------------------------------------------------------------------------


def test_fecha_clinica_usa_el_dia_de_panama_y_no_el_de_utc():
    instante = datetime(2025, 3, 1, 3, 30, tzinfo=timezone.utc)

    assert fecha_clinica(instante) == date(2025, 2, 28)
    assert instante.date() == date(2025, 3, 1)


def test_fecha_clinica_con_offset_explicito_conserva_el_instante():
    instante = datetime(2025, 2, 28, 23, 30, tzinfo=PANAMA)

    assert fecha_clinica(instante) == date(2025, 2, 28)
    assert fecha_clinica(instante.astimezone(timezone.utc)) == date(2025, 2, 28)


def test_fecha_clinica_rechaza_instantes_sin_zona():
    with pytest.raises(LecturaInvalida):
        fecha_clinica(datetime(2025, 3, 1, 3, 30))


MEDICO_A = PeriodoDeSeguimiento(1, 501, date(2025, 1, 1), date(2025, 2, 28))
MEDICO_B = PeriodoDeSeguimiento(2, 502, date(2025, 3, 1), None)


def test_un_solo_principal_vigente_es_el_medico():
    assert medico_responsable([MEDICO_A, MEDICO_B], date(2025, 2, 28), id_lectura=9) == 501
    assert medico_responsable([MEDICO_A, MEDICO_B], date(2025, 3, 1), id_lectura=9) == 502


def test_el_caso_de_medianoche_elige_por_el_dia_de_panama():
    dia = fecha_clinica(datetime(2025, 3, 1, 3, 30, tzinfo=timezone.utc))

    assert medico_responsable([MEDICO_A, MEDICO_B], dia, id_lectura=9) == 501


def test_sin_principal_vigente_es_una_derivacion_imposible():
    with pytest.raises(DerivacionImposible, match="id_lectura=9"):
        medico_responsable([MEDICO_A], date(2025, 3, 5), id_lectura=9)


def test_dos_principales_vigentes_son_una_ambiguedad_y_no_se_elige():
    solapado = PeriodoDeSeguimiento(3, 503, date(2025, 2, 1), None)

    with pytest.raises(DerivacionAmbigua, match=r"id_seguimiento=\[1, 3\]"):
        medico_responsable([MEDICO_A, solapado], date(2025, 2, 10), id_lectura=9)


# ---------------------------------------------------------------------------
# Clínica, teléfono, nombre y duración
# ---------------------------------------------------------------------------


def test_clinica_contextual_cero_una_y_varias():
    # Sin relación todavía: NULL, nunca una clínica inventada.
    assert clinica_contextual([], entidad="paciente id_paciente=1") is None
    assert clinica_contextual([100, 100], entidad="paciente id_paciente=1") == 100
    with pytest.raises(DerivacionAmbigua, match=r"id_clinica=\[100, 101\]"):
        clinica_contextual([101, 100], entidad="paciente id_paciente=1")


AFILIACION_ABIERTA = AfiliacionClinica(100, date(2025, 1, 1), None)
DIA = date(2025, 2, 24)


def test_una_afiliacion_que_cubre_el_dia_y_coincide_con_la_clinica_es_valida():
    assert (
        verificar_afiliacion(
            [AFILIACION_ABIERTA], DIA, id_medico=1, id_clinica_embarazo=100, id_lectura=9
        )
        is None
    )


@pytest.mark.parametrize(
    "afiliacion",
    [
        AfiliacionClinica(100, date(2025, 2, 24), date(2025, 2, 24)),  # bordes inclusivos
        AfiliacionClinica(100, date(2024, 1, 1), date(2025, 12, 31)),  # cerrada, cubre
    ],
)
def test_una_afiliacion_historica_cerrada_sigue_cubriendo_sus_dias(afiliacion):
    assert (
        verificar_afiliacion(
            [afiliacion], DIA, id_medico=1, id_clinica_embarazo=100, id_lectura=9
        )
        is None
    )


@pytest.mark.parametrize(
    "afiliaciones",
    [
        [],
        [AfiliacionClinica(100, date(2025, 3, 1), None)],  # empieza después
        [AfiliacionClinica(100, date(2025, 1, 1), date(2025, 2, 23))],  # terminó antes
    ],
)
def test_sin_afiliacion_que_cubra_el_dia_se_rechaza(afiliaciones):
    with pytest.raises(DerivacionImposible, match="ninguna afiliación"):
        verificar_afiliacion(
            afiliaciones, DIA, id_medico=1, id_clinica_embarazo=100, id_lectura=9
        )


def test_una_afiliacion_a_otra_clinica_se_rechaza():
    with pytest.raises(DerivacionImposible, match="no a la del embarazo"):
        verificar_afiliacion(
            [AFILIACION_ABIERTA], DIA, id_medico=1, id_clinica_embarazo=101, id_lectura=9
        )


def test_dos_afiliaciones_aplicables_son_una_ambiguedad_y_no_se_elige():
    afiliaciones = [AFILIACION_ABIERTA, AfiliacionClinica(101, date(2025, 1, 1), None)]

    with pytest.raises(DerivacionAmbigua, match=r"id_clinica=\[100, 101\]"):
        verificar_afiliacion(
            afiliaciones, DIA, id_medico=1, id_clinica_embarazo=100, id_lectura=9
        )


CELULAR = "CELULAR"


def test_telefono_principal_cero_uno_y_varios():
    uno = [
        Contacto(CELULAR, "6000-0001", True),
        Contacto("CORREO_ALTERNO", "alterno@ejemplo.test", False),
    ]
    assert telefono_principal(uno, entidad="paciente id_paciente=1") == "6000-0001"

    ninguno = [
        Contacto(CELULAR, "6000-0002", False),
        Contacto("CORREO_ALTERNO", "alterno@ejemplo.test", True),
    ]
    assert telefono_principal(ninguno, entidad="paciente id_paciente=1") is None

    dos = [Contacto(CELULAR, "6000-0003", True), Contacto(CELULAR, "6000-0004", True)]
    with pytest.raises(DerivacionAmbigua) as error:
        telefono_principal(dos, entidad="paciente id_paciente=1")
    assert "6000-0003" not in error.value.detalle
    assert "6000-0004" not in error.value.detalle


def test_nombre_completo_sin_partes_nulas_ni_dobles_espacios():
    assert nombre_completo(["Ana", None, "Pérez", "Ruiz"], entidad="x") == "Ana Pérez Ruiz"
    assert nombre_completo(["Ana", "", "Pérez", None], entidad="x") == "Ana Pérez"
    # Content is never trimmed or rewritten.
    assert nombre_completo(["María José", None, "de la Cruz", None], entidad="x") == (
        "María José de la Cruz"
    )
    with pytest.raises(DerivacionImposible) as error:
        nombre_completo([None, "", None, None], entidad="paciente id_paciente=7")
    assert "id_paciente=7" in error.value.detalle


@pytest.mark.parametrize(("dias", "semanas"), [(280, 40), (0, 0), (7, 1)])
def test_duracion_estimada_exacta(dias, semanas):
    inicio = date(2025, 1, 6)
    fin = inicio + timedelta(days=dias)

    assert duracion_estimada_semanas(inicio, fin, id_embarazo=1) == semanas


def test_duracion_no_multiplo_de_7_no_se_redondea():
    inicio = date(2025, 1, 6)

    with pytest.raises(ReglaNoDefinida, match="id_embarazo=1"):
        duracion_estimada_semanas(inicio, inicio + timedelta(days=281), id_embarazo=1)


def test_duracion_negativa_es_imposible():
    with pytest.raises(DerivacionImposible):
        duracion_estimada_semanas(date(2025, 2, 1), date(2025, 1, 1), id_embarazo=1)


def test_clasificacion_embarazo_queda_pendiente_y_no_se_inventa():
    assert reglas.clasificacion_embarazo() is None
