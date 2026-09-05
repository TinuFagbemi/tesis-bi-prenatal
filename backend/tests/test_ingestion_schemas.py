"""Pruebas del contrato Pydantic del endpoint de ingesta (SCRUM-62).

No necesitan FastAPI ni una base de datos: aquí solo se valida el mensaje. Lo
que el contrato *no* debe comprobar también se prueba, porque dejar los rangos
de valor en manos de PostgreSQL es una decisión, no un olvido: un ``spo2_valor``
de 150 tiene que pasar por este contrato para que el CHECK de la base sea quien
lo rechace.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.models.enums import EstadoSesion, OrigenDato, TipoSesion
from app.schemas.monitoreo import (
    SMALLINT_MAXIMO,
    SMALLINT_MINIMO,
    LecturaBiometricaEntrada,
    SesionMonitoreoCreada,
    SesionMonitoreoEntrada,
)

INICIO = "2026-03-01T14:00:00+00:00"
FIN = "2026-03-01T14:30:00+00:00"
CAPTURA = "2026-03-01T14:05:00+00:00"
# Dentro de la sesión y posterior a CAPTURA: sirve para probar el orden entre
# captura y sincronización sin salirse del intervalo.
CAPTURA_TARDIA = "2026-03-01T14:20:00+00:00"
# Deliberadamente posterior a FIN: sincronizar después de que la sesión terminó
# es el caso normal en un sistema con conectividad intermitente.
SINCRONIZACION = "2026-03-01T14:40:00+00:00"
ANTES_DEL_INICIO = "2026-03-01T13:59:00+00:00"
DESPUES_DEL_FIN = "2026-03-01T14:31:00+00:00"

# Identificadores ficticios; ninguno se consulta aquí.
ID_EMBARAZO = 100
ID_DISPOSITIVO = 100
ID_TIEMPO_GEST = 119
ID_SEMAFORO = 100


def lectura_hr(**cambios: Any) -> dict[str, Any]:
    """Lectura de signos maternos: HR y SpO2 presentes, movimiento en null."""
    fila: dict[str, Any] = {
        "id_tiempo_gest": ID_TIEMPO_GEST,
        "id_semaforo": ID_SEMAFORO,
        "fecha_hora_captura": CAPTURA,
        "fecha_hora_sincronizacion": SINCRONIZACION,
        "hr_valor": 88.5,
        "spo2_valor": 97.0,
        "mov_valor": None,
    }
    fila.update(cambios)
    return fila


def lectura_movimiento(**cambios: Any) -> dict[str, Any]:
    """Lectura de movimiento fetal: conteo presente, HR y SpO2 en null."""
    fila: dict[str, Any] = {
        "id_tiempo_gest": ID_TIEMPO_GEST,
        "id_semaforo": ID_SEMAFORO,
        "fecha_hora_captura": CAPTURA,
        "fecha_hora_sincronizacion": SINCRONIZACION,
        "hr_valor": None,
        "spo2_valor": None,
        "mov_valor": 12,
    }
    fila.update(cambios)
    return fila


def paquete(
    *,
    tipo_sesion: str = TipoSesion.SIGNOS_MATERNOS.value,
    lecturas: list[dict[str, Any]] | None = None,
    **cambios: Any,
) -> dict[str, Any]:
    """Paquete válido de referencia; cada prueba altera solo lo que le interesa.

    Trae ``fecha_fin``, así que declara ``COMPLETADA``: desde que el estado y el
    cierre deben coincidir, una sesión con fin y sin estado sería incoherente.
    """
    cuerpo: dict[str, Any] = {
        "id_embarazo": ID_EMBARAZO,
        "id_dispositivo": ID_DISPOSITIVO,
        "tipo_sesion": tipo_sesion,
        "fecha_inicio": INICIO,
        "fecha_fin": FIN,
        "estado_sesion": EstadoSesion.COMPLETADA.value,
        "lecturas": [lectura_hr()] if lecturas is None else lecturas,
    }
    cuerpo.update(cambios)
    return cuerpo


def paquete_abierto(**cambios: Any) -> dict[str, Any]:
    """Sesión que sigue abierta: sin ``fecha_fin`` y, por omisión, sin estado.

    Los campos se retiran **antes** de aplicar ``cambios``, para que una prueba
    pueda volver a poner ``estado_sesion`` y seguir sin ``fecha_fin``.
    """
    cuerpo = paquete()
    cuerpo.pop("fecha_fin", None)
    cuerpo.pop("estado_sesion", None)
    cuerpo.update(cambios)
    return cuerpo


# ---------------------------------------------------------------------------
# Paquete válido
# ---------------------------------------------------------------------------


def test_el_paquete_minimo_valido_se_acepta():
    """Sin fecha_fin, sin estado y sin origen: lo mínimo que el contrato exige."""
    entrada = SesionMonitoreoEntrada.model_validate(
        {
            "id_embarazo": ID_EMBARAZO,
            "id_dispositivo": ID_DISPOSITIVO,
            "tipo_sesion": "SIGNOS_MATERNOS",
            "fecha_inicio": INICIO,
            "lecturas": [
                {
                    "id_tiempo_gest": ID_TIEMPO_GEST,
                    "id_semaforo": ID_SEMAFORO,
                    "fecha_hora_captura": CAPTURA,
                    "hr_valor": 88.5,
                    "spo2_valor": 97.0,
                }
            ],
        }
    )

    assert entrada.tipo_sesion is TipoSesion.SIGNOS_MATERNOS
    assert len(entrada.lecturas) == 1
    assert entrada.fecha_fin is None


def test_los_campos_opcionales_omitidos_quedan_en_none():
    """Omitirlos deja que el modelo ORM aplique su propio valor por omisión."""
    entrada = SesionMonitoreoEntrada.model_validate(paquete_abierto())

    assert entrada.estado_sesion is None
    assert entrada.origen_dato is None
    assert entrada.fecha_fin is None


def test_los_campos_opcionales_enviados_se_conservan():
    entrada = SesionMonitoreoEntrada.model_validate(
        paquete(estado_sesion="COMPLETADA", origen_dato="CSV")
    )

    assert entrada.estado_sesion is EstadoSesion.COMPLETADA
    assert entrada.origen_dato is OrigenDato.CSV


def test_una_sesion_admite_varias_lecturas():
    """El 1:N vive en el contrato, no solo en la base."""
    entrada = SesionMonitoreoEntrada.model_validate(
        paquete(lecturas=[lectura_hr() for _ in range(5)])
    )

    assert len(entrada.lecturas) == 5


# ---------------------------------------------------------------------------
# Lista de lecturas
# ---------------------------------------------------------------------------


def test_una_lista_de_lecturas_vacia_se_rechaza():
    with pytest.raises(ValidationError) as error:
        SesionMonitoreoEntrada.model_validate(paquete(lecturas=[]))

    assert any(
        detalle["loc"] == ("lecturas",) for detalle in error.value.errors()
    )


def test_una_sesion_sin_el_campo_lecturas_se_rechaza():
    cuerpo = paquete()
    del cuerpo["lecturas"]

    with pytest.raises(ValidationError):
        SesionMonitoreoEntrada.model_validate(cuerpo)


# ---------------------------------------------------------------------------
# Campos obligatorios y tipos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "campo",
    ["id_embarazo", "id_dispositivo", "tipo_sesion", "fecha_inicio"],
)
def test_falta_un_campo_obligatorio_de_la_sesion(campo):
    cuerpo = paquete()
    del cuerpo[campo]

    with pytest.raises(ValidationError) as error:
        SesionMonitoreoEntrada.model_validate(cuerpo)

    assert any(detalle["loc"] == (campo,) for detalle in error.value.errors())


@pytest.mark.parametrize(
    "campo",
    ["id_tiempo_gest", "id_semaforo", "fecha_hora_captura"],
)
def test_falta_un_campo_obligatorio_de_la_lectura(campo):
    fila = lectura_hr()
    del fila[campo]

    with pytest.raises(ValidationError):
        SesionMonitoreoEntrada.model_validate(paquete(lecturas=[fila]))


def test_un_tipo_invalido_se_rechaza():
    with pytest.raises(ValidationError):
        SesionMonitoreoEntrada.model_validate(paquete(id_embarazo="no soy un entero"))


@pytest.mark.parametrize("campo", ["id_embarazo", "id_dispositivo"])
def test_un_booleano_no_se_acepta_como_entero_en_la_sesion(campo):
    """``true`` no es 1. El cargador de SCRUM-61 traza la misma línea."""
    with pytest.raises(ValidationError):
        SesionMonitoreoEntrada.model_validate(paquete(**{campo: True}))


@pytest.mark.parametrize("campo", ["id_tiempo_gest", "id_semaforo", "mov_valor"])
def test_un_booleano_no_se_acepta_como_entero_en_la_lectura(campo):
    fila = lectura_movimiento(**{campo: True})

    with pytest.raises(ValidationError):
        SesionMonitoreoEntrada.model_validate(
            paquete(tipo_sesion="MOVIMIENTOS_FETALES", lecturas=[fila])
        )


def test_un_entero_como_texto_no_se_acepta():
    """``"119"`` no es 119: el identificador viaja como número JSON."""
    with pytest.raises(ValidationError):
        SesionMonitoreoEntrada.model_validate(
            paquete(lecturas=[lectura_hr(id_tiempo_gest="119")])
        )


def test_un_booleano_no_se_acepta_como_valor_biometrico():
    with pytest.raises(ValidationError):
        SesionMonitoreoEntrada.model_validate(
            paquete(lecturas=[lectura_hr(hr_valor=True)])
        )


def test_un_numero_en_texto_si_se_acepta_como_valor_biometrico():
    """Igual que el cargador: para NUMERIC, una cadena numérica es válida."""
    entrada = SesionMonitoreoEntrada.model_validate(
        paquete(lecturas=[lectura_hr(hr_valor="88.50")])
    )

    assert entrada.lecturas[0].hr_valor == Decimal("88.50")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


def test_un_tipo_de_sesion_invalido_se_rechaza():
    with pytest.raises(ValidationError):
        SesionMonitoreoEntrada.model_validate(paquete(tipo_sesion="SIGNOS_FETALES"))


def test_un_estado_de_sesion_invalido_se_rechaza():
    with pytest.raises(ValidationError):
        SesionMonitoreoEntrada.model_validate(paquete(estado_sesion="TERMINADA"))


def test_un_origen_de_dato_invalido_se_rechaza():
    """``API`` existió en un borrador del dataset y ya no pertenece al enum."""
    with pytest.raises(ValidationError):
        SesionMonitoreoEntrada.model_validate(paquete(origen_dato="API"))


# ---------------------------------------------------------------------------
# Campos desconocidos
# ---------------------------------------------------------------------------


def test_un_campo_desconocido_en_la_sesion_se_rechaza():
    with pytest.raises(ValidationError) as error:
        SesionMonitoreoEntrada.model_validate(paquete(id_sesion=733))

    assert any(
        detalle["type"] == "extra_forbidden" for detalle in error.value.errors()
    )


def test_un_campo_desconocido_en_la_lectura_se_rechaza():
    """Incluye ``id_lectura``: la PK la genera PostgreSQL, no el cliente."""
    with pytest.raises(ValidationError) as error:
        SesionMonitoreoEntrada.model_validate(
            paquete(lecturas=[lectura_hr(id_lectura=1181)])
        )

    assert any(
        detalle["type"] == "extra_forbidden" for detalle in error.value.errors()
    )


# ---------------------------------------------------------------------------
# Fechas, horas y zona horaria
# ---------------------------------------------------------------------------


def test_una_fecha_invalida_se_rechaza():
    with pytest.raises(ValidationError):
        SesionMonitoreoEntrada.model_validate(paquete(fecha_inicio="2026-13-45T99:00:00Z"))


@pytest.mark.parametrize("campo", ["fecha_inicio", "fecha_fin"])
def test_un_datetime_sin_offset_se_rechaza_en_la_sesion(campo):
    with pytest.raises(ValidationError):
        SesionMonitoreoEntrada.model_validate(paquete(**{campo: "2026-03-01T14:00:00"}))


@pytest.mark.parametrize(
    "campo", ["fecha_hora_captura", "fecha_hora_sincronizacion"]
)
def test_un_datetime_sin_offset_se_rechaza_en_la_lectura(campo):
    with pytest.raises(ValidationError):
        SesionMonitoreoEntrada.model_validate(
            paquete(lecturas=[lectura_hr(**{campo: "2026-03-01T14:05:00"})])
        )


def test_el_offset_se_conserva_sin_convertirse_a_utc():
    """Panamá es UTC-5: el instante y su offset deben llegar intactos."""
    panama = timezone(timedelta(hours=-5))
    entrada = SesionMonitoreoEntrada.model_validate(
        paquete(
            fecha_inicio="2026-03-01T09:00:00-05:00",
            fecha_fin="2026-03-01T09:30:00-05:00",
            lecturas=[lectura_hr(fecha_hora_captura="2026-03-01T09:05:00-05:00")],
        )
    )

    assert entrada.fecha_inicio.utcoffset() == panama.utcoffset(None)
    assert entrada.fecha_inicio == datetime(2026, 3, 1, 14, 0, tzinfo=timezone.utc)
    assert entrada.lecturas[0].fecha_hora_captura.utcoffset() == panama.utcoffset(None)


def test_una_sincronizacion_anterior_a_la_captura_se_rechaza():
    """Ambos instantes caen dentro de la sesión: lo único inválido es su orden."""
    with pytest.raises(ValidationError) as error:
        SesionMonitoreoEntrada.model_validate(
            paquete(
                lecturas=[
                    lectura_hr(
                        fecha_hora_captura=CAPTURA_TARDIA,
                        fecha_hora_sincronizacion=CAPTURA,
                    )
                ]
            )
        )

    assert "fecha_hora_sincronizacion" in str(error.value)


def test_una_sincronizacion_igual_a_la_captura_se_acepta():
    entrada = SesionMonitoreoEntrada.model_validate(
        paquete(
            lecturas=[
                lectura_hr(
                    fecha_hora_captura=CAPTURA, fecha_hora_sincronizacion=CAPTURA
                )
            ]
        )
    )

    assert entrada.lecturas[0].fecha_hora_sincronizacion is not None


def test_una_fecha_fin_anterior_al_inicio_se_rechaza():
    with pytest.raises(ValidationError):
        SesionMonitoreoEntrada.model_validate(
            paquete(fecha_inicio=FIN, fecha_fin=INICIO)
        )


# ---------------------------------------------------------------------------
# Cada lectura tiene que haberse capturado durante su sesión
# ---------------------------------------------------------------------------


def test_una_captura_justo_en_el_inicio_es_valida():
    """Los extremos cuentan como dentro."""
    entrada = SesionMonitoreoEntrada.model_validate(
        paquete(lecturas=[lectura_hr(fecha_hora_captura=INICIO)])
    )

    assert entrada.lecturas[0].fecha_hora_captura == entrada.fecha_inicio


def test_una_captura_justo_en_el_fin_es_valida():
    entrada = SesionMonitoreoEntrada.model_validate(
        paquete(lecturas=[lectura_hr(fecha_hora_captura=FIN)])
    )

    assert entrada.lecturas[0].fecha_hora_captura == entrada.fecha_fin


def test_una_captura_anterior_al_inicio_de_la_sesion_se_rechaza():
    with pytest.raises(ValidationError) as error:
        SesionMonitoreoEntrada.model_validate(
            paquete(lecturas=[lectura_hr(fecha_hora_captura=ANTES_DEL_INICIO)])
        )

    assert "anterior al inicio de la sesión" in str(error.value)


def test_una_captura_posterior_al_fin_de_la_sesion_se_rechaza():
    with pytest.raises(ValidationError) as error:
        SesionMonitoreoEntrada.model_validate(
            paquete(
                lecturas=[
                    lectura_hr(
                        fecha_hora_captura=DESPUES_DEL_FIN,
                        fecha_hora_sincronizacion="2026-03-01T15:30:00+00:00",
                    )
                ]
            )
        )

    assert "posterior al fin de la sesión" in str(error.value)


def test_la_captura_senala_la_lectura_concreta_que_esta_fuera():
    with pytest.raises(ValidationError) as error:
        SesionMonitoreoEntrada.model_validate(
            paquete(
                lecturas=[
                    lectura_hr(),
                    lectura_hr(fecha_hora_captura=ANTES_DEL_INICIO),
                    lectura_hr(),
                ]
            )
        )

    assert "lecturas[1]" in str(error.value)


def test_una_sesion_abierta_solo_acota_por_su_inicio():
    """Sin ``fecha_fin`` no hay límite superior que comprobar."""
    entrada = SesionMonitoreoEntrada.model_validate(
        paquete_abierto(lecturas=[lectura_hr(fecha_hora_captura=DESPUES_DEL_FIN)])
    )

    assert entrada.fecha_fin is None


def test_una_sesion_abierta_sigue_rechazando_una_captura_previa():
    with pytest.raises(ValidationError):
        SesionMonitoreoEntrada.model_validate(
            paquete_abierto(lecturas=[lectura_hr(fecha_hora_captura=ANTES_DEL_INICIO)])
        )


def test_sincronizar_despues_del_fin_de_la_sesion_es_valido():
    """Offline-first: la sincronización llega cuando vuelve la conectividad."""
    entrada = SesionMonitoreoEntrada.model_validate(
        paquete(
            lecturas=[
                lectura_hr(fecha_hora_sincronizacion="2026-03-04T08:00:00+00:00")
            ]
        )
    )

    assert entrada.lecturas[0].fecha_hora_sincronizacion > entrada.fecha_fin


# ---------------------------------------------------------------------------
# Coherencia entre estado_sesion y fecha_fin
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("estado", ["PENDIENTE", "INTERRUMPIDA"])
def test_un_estado_sin_cierre_no_admite_fecha_fin(estado):
    with pytest.raises(ValidationError) as error:
        SesionMonitoreoEntrada.model_validate(paquete(estado_sesion=estado))

    assert "no puede traer 'fecha_fin'" in str(error.value)


@pytest.mark.parametrize("estado", ["PENDIENTE", "INTERRUMPIDA"])
def test_un_estado_sin_cierre_se_acepta_sin_fecha_fin(estado):
    entrada = SesionMonitoreoEntrada.model_validate(
        paquete_abierto(estado_sesion=estado)
    )

    assert entrada.estado_sesion is EstadoSesion(estado)
    assert entrada.fecha_fin is None


@pytest.mark.parametrize("estado", ["COMPLETADA", "PROCESADA"])
def test_un_estado_terminado_exige_fecha_fin(estado):
    with pytest.raises(ValidationError) as error:
        SesionMonitoreoEntrada.model_validate(paquete_abierto(estado_sesion=estado))

    assert "tiene que traer 'fecha_fin'" in str(error.value)


@pytest.mark.parametrize("estado", ["COMPLETADA", "PROCESADA"])
def test_un_estado_terminado_se_acepta_con_fecha_fin(estado):
    entrada = SesionMonitoreoEntrada.model_validate(paquete(estado_sesion=estado))

    assert entrada.estado_sesion is EstadoSesion(estado)
    assert entrada.fecha_fin is not None


def test_omitir_el_estado_equivale_a_pendiente_y_prohibe_fecha_fin():
    """El default real de la base es PENDIENTE, y se valida como tal."""
    cuerpo = paquete()
    del cuerpo["estado_sesion"]

    with pytest.raises(ValidationError) as error:
        SesionMonitoreoEntrada.model_validate(cuerpo)

    assert "PENDIENTE" in str(error.value)


# ---------------------------------------------------------------------------
# Forma biométrica
# ---------------------------------------------------------------------------


def test_la_forma_hr_spo2_es_valida():
    lectura = LecturaBiometricaEntrada.model_validate(lectura_hr())

    assert lectura.es_de_movimiento is False
    assert lectura.mov_valor is None


def test_la_forma_de_movimiento_es_valida():
    lectura = LecturaBiometricaEntrada.model_validate(lectura_movimiento())

    assert lectura.es_de_movimiento is True
    assert lectura.hr_valor is None
    assert lectura.spo2_valor is None


@pytest.mark.parametrize(
    ("etiqueta", "cambios"),
    [
        ("las tres métricas presentes", {"mov_valor": 12}),
        ("solo hr", {"spo2_valor": None}),
        ("solo spo2", {"hr_valor": None}),
        ("ninguna métrica", {"hr_valor": None, "spo2_valor": None}),
        ("hr con movimiento", {"spo2_valor": None, "mov_valor": 12}),
    ],
)
def test_una_forma_biometrica_ambigua_o_incompleta_se_rechaza(etiqueta, cambios):
    with pytest.raises(ValidationError):
        LecturaBiometricaEntrada.model_validate(lectura_hr(**cambios))


def test_una_metrica_que_no_aplica_se_conserva_como_none():
    """Nunca cero, nunca cadena vacía: ``None`` llega a SQL como NULL."""
    lectura = LecturaBiometricaEntrada.model_validate(lectura_movimiento())

    assert lectura.hr_valor is None
    assert lectura.spo2_valor is None
    assert lectura.hr_valor != 0
    assert lectura.model_dump()["hr_valor"] is None


def test_omitir_una_metrica_equivale_a_enviarla_en_null():
    fila = lectura_movimiento()
    del fila["hr_valor"]
    del fila["spo2_valor"]

    lectura = LecturaBiometricaEntrada.model_validate(fila)

    assert lectura.hr_valor is None
    assert lectura.spo2_valor is None


def test_un_cero_explicito_no_sustituye_a_null():
    """``hr_valor = 0`` no es "no aplica": es una forma inválida y se rechaza."""
    with pytest.raises(ValidationError):
        LecturaBiometricaEntrada.model_validate(
            lectura_movimiento(hr_valor=0, spo2_valor=0)
        )


# ---------------------------------------------------------------------------
# Límites físicos de almacenamiento, no criterios clínicos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("valor", [SMALLINT_MINIMO - 1, SMALLINT_MAXIMO + 1])
def test_un_movimiento_fuera_del_rango_smallint_se_rechaza(valor):
    """Fuera del SMALLINT la base no podría guardarlo: mejor un 422 claro."""
    with pytest.raises(ValidationError):
        LecturaBiometricaEntrada.model_validate(lectura_movimiento(mov_valor=valor))


def test_un_valor_biometrico_con_demasiados_digitos_se_rechaza():
    """NUMERIC(5, 2) no admite 1234.56."""
    with pytest.raises(ValidationError):
        LecturaBiometricaEntrada.model_validate(lectura_hr(hr_valor="1234.56"))


def test_un_valor_biometrico_con_demasiados_decimales_se_rechaza():
    with pytest.raises(ValidationError):
        LecturaBiometricaEntrada.model_validate(lectura_hr(hr_valor="88.567"))


def test_un_spo2_fuera_de_rango_clinico_pasa_el_contrato():
    """Decisión explícita: el rango 0-100 es un CHECK, y lo aplica PostgreSQL.

    Si este contrato lo rechazara, el endpoint tendría una segunda copia del
    criterio clínico. La prueba existe para que esa decisión no se pierda por
    descuido en un cambio futuro.
    """
    lectura = LecturaBiometricaEntrada.model_validate(lectura_hr(spo2_valor=150))

    assert lectura.spo2_valor == Decimal("150")


def test_una_hr_no_positiva_pasa_el_contrato():
    """Mismo motivo: ``hr_valor > 0`` es un CHECK de la base."""
    lectura = LecturaBiometricaEntrada.model_validate(lectura_hr(hr_valor="-5.00"))

    assert lectura.hr_valor == Decimal("-5.00")


def test_un_movimiento_negativo_dentro_del_smallint_pasa_el_contrato():
    """``mov_valor >= 0`` también es un CHECK, no una regla del contrato."""
    lectura = LecturaBiometricaEntrada.model_validate(lectura_movimiento(mov_valor=-1))

    assert lectura.mov_valor == -1


# ---------------------------------------------------------------------------
# Coherencia entre el tipo de sesión y la forma de sus lecturas
# ---------------------------------------------------------------------------


def test_signos_maternos_con_lecturas_hr_spo2_se_acepta():
    entrada = SesionMonitoreoEntrada.model_validate(
        paquete(tipo_sesion="SIGNOS_MATERNOS", lecturas=[lectura_hr() for _ in range(5)])
    )

    assert all(not lectura.es_de_movimiento for lectura in entrada.lecturas)


def test_movimientos_fetales_con_lectura_de_movimiento_se_acepta():
    entrada = SesionMonitoreoEntrada.model_validate(
        paquete(tipo_sesion="MOVIMIENTOS_FETALES", lecturas=[lectura_movimiento()])
    )

    assert entrada.lecturas[0].es_de_movimiento


def test_signos_maternos_con_lectura_de_movimiento_se_rechaza():
    with pytest.raises(ValidationError) as error:
        SesionMonitoreoEntrada.model_validate(
            paquete(tipo_sesion="SIGNOS_MATERNOS", lecturas=[lectura_movimiento()])
        )

    assert "SIGNOS_MATERNOS" in str(error.value)


def test_movimientos_fetales_con_lectura_hr_spo2_se_rechaza():
    with pytest.raises(ValidationError) as error:
        SesionMonitoreoEntrada.model_validate(
            paquete(tipo_sesion="MOVIMIENTOS_FETALES", lecturas=[lectura_hr()])
        )

    assert "MOVIMIENTOS_FETALES" in str(error.value)


def test_una_sesion_con_lecturas_mezcladas_se_rechaza():
    with pytest.raises(ValidationError) as error:
        SesionMonitoreoEntrada.model_validate(
            paquete(
                tipo_sesion="SIGNOS_MATERNOS",
                lecturas=[lectura_hr(), lectura_movimiento(), lectura_hr()],
            )
        )

    # Señala la posición exacta de la lectura que no corresponde.
    assert "lecturas[1]" in str(error.value)


# ---------------------------------------------------------------------------
# Respuesta
# ---------------------------------------------------------------------------


def test_la_respuesta_serializa_los_identificadores_creados():
    respuesta = SesionMonitoreoCreada(
        id_sesion=733, lecturas_creadas=3, ids_lectura=[1181, 1182, 1183]
    )

    assert respuesta.model_dump() == {
        "id_sesion": 733,
        "lecturas_creadas": 3,
        "ids_lectura": [1181, 1182, 1183],
    }


def test_la_respuesta_no_declara_ningun_campo_sensible():
    """El contrato de salida es cerrado: identificadores y un conteo."""
    assert set(SesionMonitoreoCreada.model_fields) == {
        "id_sesion",
        "lecturas_creadas",
        "ids_lectura",
    }
