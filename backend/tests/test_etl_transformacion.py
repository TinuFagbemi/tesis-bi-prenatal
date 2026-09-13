"""Transformation of the analytic ETL (SCRUM-69), over hand-built rows.

Offline: the operational rows are written here by hand, so each test states the
input and the analytic row it expects without a database in between.

All data is fictitious and simulated.
"""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.etl.extraccion import OrigenDimensional
from app.etl.reglas import (
    ERROR,
    OK,
    VERSION_SIM_1_0,
    WARNING,
    DerivacionAmbigua,
    DerivacionImposible,
    DiscrepanciaDeSemaforo,
    LecturaInvalida,
    ReglaNoDefinida,
    VersionDeReglasDesconocida,
)
from app.etl.transformacion import transformar_dimensiones, transformar_hechos

INICIO = date(2025, 1, 6)
ID_POR_CODIGO = {OK: 100, WARNING: 101, ERROR: 102}
NOMBRE_FICTICIO = ("Lucía", None, "Ficticia", "Simulada")
CEDULA_FICTICIA = "0-000-0001"
TELEFONO_FICTICIO = "6000-1111"

AFILIACIONES = [
    {"id_medico": 100, "id_clinica": 100, "fecha_inicio": date(2025, 1, 1), "fecha_final": None},
]


def _origen(**cambios) -> OrigenDimensional:
    base = OrigenDimensional(
        clinicas=[
            {"id_clinica": 100, "nombre_clinica": "Clínica A", "provincia": "P", "distrito": "D"},
            {"id_clinica": 101, "nombre_clinica": "Clínica B", "provincia": "P", "distrito": "D"},
        ],
        semaforos=[
            {"id_semaforo": 100, "codigo_nivel": OK, "etiqueta_visual": "Normal",
             "color_hex": "#008000", "prioridad": 1, "mensaje_app": "m",
             "version_referencia": VERSION_SIM_1_0},
            {"id_semaforo": 101, "codigo_nivel": WARNING, "etiqueta_visual": "Precaución",
             "color_hex": "#FFC107", "prioridad": 2, "mensaje_app": "m",
             "version_referencia": VERSION_SIM_1_0},
            {"id_semaforo": 102, "codigo_nivel": ERROR, "etiqueta_visual": "Alerta",
             "color_hex": "#D32F2F", "prioridad": 3, "mensaje_app": "m",
             "version_referencia": VERSION_SIM_1_0},
        ],
        tiempos_gestacionales=[
            {"id_tiempo_gest": 107, "semana_gestacion": 8, "mes_gestacion": 2,
             "trimestre": 1, "descripcion": "Semana gestacional 8"},
            {"id_tiempo_gest": 129, "semana_gestacion": 30, "mes_gestacion": 8,
             "trimestre": 3, "descripcion": "Semana gestacional 30"},
        ],
        factores_riesgo=[
            {"id_factor_riesgo": 100, "clave_factor": "HTA", "nombre_factor": "Hipertensión",
             "descripcion": None, "activo": True},
        ],
        medicos=[
            {"id_medico": 100, "primer_nombre": "Mario", "segundo_nombre": None,
             "apellido_paterno": "Ficticio", "apellido_materno": None,
             "email_med": "medico@ejemplo.test", "especialidad": "Ginecología"},
        ],
        telefonos_medico=[
            {"id_medico": 100, "tipo_contacto": "CELULAR", "valor_contacto": "6000-2222",
             "principal": True},
            {"id_medico": 100, "tipo_contacto": "TELEFONO_DOMICILIO",
             "valor_contacto": "200-0000", "principal": False},
        ],
        afiliaciones_de_medico=list(AFILIACIONES),
        pacientes=[
            {"id_paciente": 100, "cedula": CEDULA_FICTICIA,
             "primer_nombre": NOMBRE_FICTICIO[0], "segundo_nombre": NOMBRE_FICTICIO[1],
             "apellido_paterno": NOMBRE_FICTICIO[2], "apellido_materno": NOMBRE_FICTICIO[3],
             "fecha_nac": date(1995, 5, 5)},
        ],
        telefonos_paciente=[
            {"id_paciente": 100, "tipo_contacto": "CELULAR",
             "valor_contacto": TELEFONO_FICTICIO, "principal": True},
            {"id_paciente": 100, "tipo_contacto": "CORREO_ALTERNO",
             "valor_contacto": "alterno@ejemplo.test", "principal": False},
        ],
        embarazos=[
            {"id_embarazo": 100, "id_paciente": 100, "id_clinica": 100,
             "numero_gestas": 1, "numero_partos": 0, "estado_embarazo": "ACTIVO",
             "fecha_inicio": INICIO, "fecha_probable_parto": INICIO + timedelta(days=280),
             "fecha_cierre": None},
        ],
        factores_de_embarazo=[
            {"id_embarazo": 100, "id_factor_riesgo": 100,
             "fecha_diagnostico": date(2025, 2, 1), "activo": True, "observaciones": None},
        ],
    )
    return replace(base, **cambios)


# ---------------------------------------------------------------------------
# Dimensiones
# ---------------------------------------------------------------------------


def test_dimensiones_derivadas():
    dimensiones = transformar_dimensiones(_origen())

    assert dimensiones.id_semaforo_por_codigo == ID_POR_CODIGO
    assert dimensiones.medicos == [
        {"id_medico": 100, "id_clinica": 100, "nombre_completo": "Mario Ficticio",
         "especialidad": "Ginecología", "email_med": "medico@ejemplo.test",
         "telefono_med": "6000-2222"}
    ]
    assert dimensiones.pacientes == [
        {"id_paciente": 100, "id_clinica": 100, "cedula": CEDULA_FICTICIA,
         "nombre_completo": "Lucía Ficticia Simulada",
         "telefono_pac": TELEFONO_FICTICIO, "fecha_nac": date(1995, 5, 5)}
    ]
    [embarazo] = dimensiones.embarazos
    assert embarazo["duracion_est_semanas"] == 40
    assert embarazo["clasificacion_embarazo"] is None


def test_el_bridge_conserva_exactamente_los_atributos_aprobados():
    [relacion] = transformar_dimensiones(_origen()).bridge

    assert set(relacion) == {
        "id_embarazo", "id_factor_riesgo", "fecha_diagnostico", "activo", "observaciones",
    }


def test_paciente_con_embarazos_en_dos_clinicas_se_rechaza():
    segundo = {**_origen().embarazos[0], "id_embarazo": 101, "id_clinica": 101}
    origen = _origen(embarazos=[*_origen().embarazos, segundo])

    with pytest.raises(DerivacionAmbigua, match="id_paciente=100"):
        transformar_dimensiones(origen)


def test_paciente_sin_embarazo_queda_sin_clinica_y_no_detiene_la_carga():
    """No pregnancy registered yet: an absent relation, not an invented clinic."""
    [paciente] = transformar_dimensiones(
        _origen(embarazos=[], factores_de_embarazo=[])
    ).pacientes

    assert paciente["id_clinica"] is None


def test_medico_sin_afiliacion_queda_sin_clinica_y_no_detiene_la_carga():
    [medico] = transformar_dimensiones(_origen(afiliaciones_de_medico=[])).medicos

    assert medico["id_clinica"] is None


def test_medico_con_afiliaciones_en_dos_clinicas_se_rechaza():
    origen = _origen(
        afiliaciones_de_medico=[
            *AFILIACIONES,
            {"id_medico": 100, "id_clinica": 101, "fecha_inicio": date(2025, 1, 1),
             "fecha_final": None},
        ]
    )

    with pytest.raises(DerivacionAmbigua, match="id_medico=100"):
        transformar_dimensiones(origen)


def test_telefono_principal_cero_uno_y_varios_en_la_dimension():
    sin_principal = _origen(
        telefonos_paciente=[
            {"id_paciente": 100, "tipo_contacto": "CELULAR",
             "valor_contacto": TELEFONO_FICTICIO, "principal": False},
        ]
    )
    [paciente] = transformar_dimensiones(sin_principal).pacientes
    assert paciente["telefono_pac"] is None

    [paciente] = transformar_dimensiones(_origen()).pacientes
    assert paciente["telefono_pac"] == TELEFONO_FICTICIO

    dos_principales = _origen(
        telefonos_paciente=[
            {"id_paciente": 100, "tipo_contacto": "CELULAR",
             "valor_contacto": TELEFONO_FICTICIO, "principal": True},
            {"id_paciente": 100, "tipo_contacto": "CELULAR",
             "valor_contacto": "6000-9999", "principal": True},
        ]
    )
    with pytest.raises(DerivacionAmbigua) as error:
        transformar_dimensiones(dos_principales)
    assert TELEFONO_FICTICIO not in error.value.detalle
    assert CEDULA_FICTICIA not in error.value.detalle
    assert "Lucía" not in error.value.detalle


def test_duracion_no_multiplo_de_7_detiene_la_transformacion():
    embarazo = {**_origen().embarazos[0], "fecha_probable_parto": INICIO + timedelta(days=281)}

    with pytest.raises(ReglaNoDefinida, match="id_embarazo=100"):
        transformar_dimensiones(_origen(embarazos=[embarazo]))


def test_catalogo_de_otra_version_detiene_la_transformacion():
    semaforos = [{**fila, "version_referencia": "SIM-2.0"} for fila in _origen().semaforos]

    with pytest.raises(VersionDeReglasDesconocida):
        transformar_dimensiones(_origen(semaforos=semaforos))


# ---------------------------------------------------------------------------
# Hechos
# ---------------------------------------------------------------------------

SEGUIMIENTOS = [
    {"id_seguimiento": 1, "id_embarazo": 100, "id_medico": 100,
     "fecha_asignacion": INICIO, "fecha_fin": None},
]
CAPTURA = datetime(2025, 2, 24, 14, 21, tzinfo=timezone.utc)


def _lectura(**cambios):
    base = {
        "id_lectura": 500, "id_sesion": 50, "id_tiempo_gest": 107, "id_semaforo": 100,
        "fecha_hora_captura": CAPTURA, "hr_valor": Decimal("88.00"),
        "spo2_valor": Decimal("97.00"), "mov_valor": None, "id_embarazo": 100,
        "id_paciente": 100, "id_clinica": 100, "semana_gestacion": 8, "trimestre": 1,
    }
    base.update(cambios)
    return base


def _hechos(lecturas, seguimientos=SEGUIMIENTOS, afiliaciones=AFILIACIONES):
    return transformar_hechos(lecturas, seguimientos, afiliaciones, ID_POR_CODIGO)


def test_hecho_de_signos_maternos():
    [hecho] = _hechos([_lectura()])

    assert hecho == {
        "id_lectura": 500, "id_sesion": 50, "id_paciente": 100, "id_medico": 100,
        "id_clinica": 100, "id_tiempo_gestacional": 107, "id_embarazo": 100,
        "id_semaforo": 100, "hr_valor": Decimal("88.00"), "spo2_valor": Decimal("97.00"),
        "mov_valor": None, "estado_hr": OK, "estado_spo2": OK, "estado_mov": None,
        "fecha_hora": CAPTURA,
    }


def test_hecho_de_movimiento_con_cero_movimientos():
    lectura = _lectura(
        hr_valor=None, spo2_valor=None, mov_valor=0, id_semaforo=102,
        id_tiempo_gest=129, semana_gestacion=30, trimestre=3,
    )
    [hecho] = _hechos([lectura])

    assert (hecho["hr_valor"], hecho["spo2_valor"], hecho["mov_valor"]) == (None, None, 0)
    assert (hecho["estado_hr"], hecho["estado_spo2"], hecho["estado_mov"]) == (None, None, ERROR)
    assert hecho["id_semaforo"] == 102


def test_hr_y_spo2_decimales_en_la_frontera_se_clasifican_con_decimal():
    [hecho] = _hechos(
        [_lectura(hr_valor=Decimal("99.99"), spo2_valor=Decimal("94.99"), id_semaforo=101)]
    )

    assert (hecho["estado_hr"], hecho["estado_spo2"]) == (OK, WARNING)
    assert hecho["hr_valor"] == Decimal("99.99")


def test_la_semana_del_hecho_es_la_validada_y_no_se_recalcula():
    """The capture date alone would say week 8; the catalogue row says 30."""
    lectura = _lectura(
        hr_valor=None, spo2_valor=None, mov_valor=12,
        id_tiempo_gest=129, semana_gestacion=30, trimestre=3,
    )
    [hecho] = _hechos([lectura])

    assert hecho["id_tiempo_gestacional"] == 129


def test_el_instante_se_conserva_con_su_zona():
    captura = datetime(2025, 2, 28, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    [hecho] = _hechos([_lectura(fecha_hora_captura=captura)])

    assert hecho["fecha_hora"] == captura
    assert hecho["fecha_hora"].utcoffset() is not None


def test_semaforo_derivado_distinto_del_origen_detiene_todo():
    lecturas = [_lectura(), _lectura(id_lectura=501, id_semaforo=101)]

    with pytest.raises(DiscrepanciaDeSemaforo) as error:
        _hechos(lecturas)
    assert "id_lectura=[501]" in error.value.detalle
    assert "88" not in error.value.detalle


def test_hr_entre_55_y_60_es_warning_y_se_carga_con_el_semaforo_de_origen():
    """Bradicardia que no llegó al umbral de alerta: precaución, no rechazo."""
    [hecho] = _hechos([_lectura(hr_valor=Decimal("57.00"), id_semaforo=101)])

    assert (hecho["estado_hr"], hecho["estado_spo2"]) == (WARNING, OK)
    assert hecho["id_semaforo"] == 101
    assert hecho["hr_valor"] == Decimal("57.00")


def test_movimiento_antes_de_semana_20_detiene_todo():
    lectura = _lectura(hr_valor=None, spo2_valor=None, mov_valor=12)

    with pytest.raises(LecturaInvalida, match="id_lectura=500"):
        _hechos([lectura])


def test_medico_ambiguo_detiene_todo():
    seguimientos = [*SEGUIMIENTOS, {**SEGUIMIENTOS[0], "id_seguimiento": 2, "id_medico": 101}]

    with pytest.raises(DerivacionAmbigua, match="id_lectura=500"):
        _hechos([_lectura()], seguimientos=seguimientos)


def test_sin_seguimiento_principal_detiene_todo():
    with pytest.raises(DerivacionImposible, match="id_lectura=500"):
        _hechos([_lectura()], seguimientos=[])


# --- Afiliación del médico del hecho --------------------------------------------


def test_afiliacion_historica_cerrada_que_cubre_el_dia_es_valida():
    """``activo`` is not consulted: a closed affiliation covers the days it was open."""
    cerrada = [{**AFILIACIONES[0], "fecha_final": date(2025, 2, 24)}]

    [hecho] = _hechos([_lectura()], afiliaciones=cerrada)

    assert hecho["id_medico"] == 100


def test_medico_sin_afiliacion_que_cubra_el_dia_detiene_todo():
    vencida = [{**AFILIACIONES[0], "fecha_final": date(2025, 2, 23)}]

    with pytest.raises(DerivacionImposible, match="ninguna afiliación"):
        _hechos([_lectura()], afiliaciones=vencida)
    with pytest.raises(DerivacionImposible, match="ninguna afiliación"):
        _hechos([_lectura()], afiliaciones=[])


def test_medico_afiliado_a_otra_clinica_ese_dia_detiene_todo():
    otra = [{**AFILIACIONES[0], "id_clinica": 101}]

    with pytest.raises(DerivacionImposible, match="no a la del embarazo"):
        _hechos([_lectura()], afiliaciones=otra)


def test_medico_con_dos_afiliaciones_aplicables_detiene_todo():
    dos = [*AFILIACIONES, {**AFILIACIONES[0], "id_clinica": 101}]

    with pytest.raises(DerivacionAmbigua, match=r"id_clinica=\[100, 101\]"):
        _hechos([_lectura()], afiliaciones=dos)
