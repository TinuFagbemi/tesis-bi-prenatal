"""Pruebas de la canonicalización y la huella de un paquete (SCRUM-63).

Sin FastAPI, sin base de datos y sin dobles: todo lo que se prueba aquí son
funciones puras. La pregunta que responden es una sola, y conviene tenerla
presente al leer cada caso: **¿estos dos paquetes se guardarían igual?** Si la
respuesta es sí, tienen que producir la misma huella; si es no, huellas
distintas. Cada prueba de abajo es una forma concreta de esa pregunta.

Las dos direcciones importan por igual y fallan distinto:

* dos paquetes que se guardan igual con huellas distintas convierten un reenvío
  legítimo en un ``409``;
* dos paquetes que se guardan distinto con la misma huella hacen que el segundo
  reciba como respuesta el resultado del primero, descartando en silencio lo que
  el cliente envió. Ése es el fallo grave.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.models.enums import EstadoSesion, OrigenDato, TipoSesion
from app.models.monitoreo import SesionMonitoreo
from app.schemas.monitoreo import (
    ESTADO_POR_OMISION,
    ORIGEN_POR_OMISION,
    SesionMonitoreoEntrada,
)
from app.services.idempotencia import (
    LONGITUD_DE_HUELLA,
    RECURSO_SESIONES_MONITOREO,
    canonicalizar_paquete,
    contenido_canonico,
    huella_del_paquete,
)
from tests.test_ingestion_schemas import lectura_hr, lectura_movimiento, paquete

HEXADECIMAL = "0123456789abcdef"


def entrada(cuerpo: dict[str, Any]) -> SesionMonitoreoEntrada:
    """Valida el cuerpo igual que lo haría el endpoint, y nada más."""
    return SesionMonitoreoEntrada.model_validate(cuerpo)


def huella(cuerpo: dict[str, Any]) -> str:
    return huella_del_paquete(entrada(cuerpo))


# ---------------------------------------------------------------------------
# Forma de la huella
# ---------------------------------------------------------------------------


def test_la_huella_es_sha256_en_hexadecimal_minusculo():
    resultado = huella(paquete())

    assert len(resultado) == LONGITUD_DE_HUELLA
    assert set(resultado) <= set(HEXADECIMAL)


def test_la_huella_es_el_sha256_del_texto_canonico():
    """No hay sal, ni truncamiento, ni codificación intermedia que adivinar."""
    cuerpo = paquete()
    texto = canonicalizar_paquete(entrada(cuerpo))

    assert huella(cuerpo) == hashlib.sha256(texto.encode("utf-8")).hexdigest()


def test_la_huella_es_determinista_entre_llamadas():
    cuerpo = paquete()

    assert huella(cuerpo) == huella(cuerpo) == huella(cuerpo)


def test_dos_paquetes_construidos_por_separado_dan_la_misma_huella():
    """Nada del estado del proceso entra en la huella."""
    assert huella(paquete()) == huella(paquete())


# ---------------------------------------------------------------------------
# Texto canónico
# ---------------------------------------------------------------------------


def test_el_texto_canonico_no_lleva_espacios_de_relleno():
    texto = canonicalizar_paquete(entrada(paquete()))

    assert ", " not in texto
    assert ": " not in texto


def test_el_texto_canonico_trae_las_claves_ordenadas():
    texto = canonicalizar_paquete(entrada(paquete()))
    objeto = json.loads(texto)

    assert list(objeto) == sorted(objeto)
    for lectura in objeto["lecturas"]:
        assert list(lectura) == sorted(lectura)


def test_el_orden_de_las_propiedades_del_json_no_cambia_la_huella():
    """Un cliente que reordene su JSON envía el mismo paquete."""
    original = paquete()
    reordenado = dict(reversed(list(original.items())))
    reordenado["lecturas"] = [
        dict(reversed(list(fila.items()))) for fila in original["lecturas"]
    ]

    assert list(reordenado) != list(original)
    assert huella(reordenado) == huella(original)


# ---------------------------------------------------------------------------
# Campos incluidos y excluidos
# ---------------------------------------------------------------------------


def test_el_contenido_canonico_declara_exactamente_los_campos_persistidos():
    contenido = contenido_canonico(entrada(paquete()))

    assert set(contenido) == {
        "id_embarazo",
        "id_dispositivo",
        "tipo_sesion",
        "fecha_inicio",
        "fecha_fin",
        "estado_sesion",
        "origen_dato",
        "lecturas",
    }
    assert set(contenido["lecturas"][0]) == {
        "id_tiempo_gest",
        "id_semaforo",
        "fecha_hora_captura",
        "fecha_hora_sincronizacion",
        "hr_valor",
        "spo2_valor",
        "mov_valor",
    }


@pytest.mark.parametrize("generado", ["id_sesion", "id_lectura", "id_idempotencia"])
def test_los_identificadores_que_genera_postgresql_no_entran_en_la_huella(generado):
    """Ni siquiera son parte de la entrada; que no aparezcan es la comprobación."""
    assert generado not in canonicalizar_paquete(entrada(paquete()))


def test_la_clave_de_idempotencia_no_entra_en_la_huella():
    """La clave nombra al contenido; incluirla haría que cada clave se validara sola."""
    texto = canonicalizar_paquete(entrada(paquete()))

    assert "Idempotency-Key" not in texto
    assert "idempotency" not in texto.lower()
    assert "clave" not in texto


def test_el_recurso_tampoco_entra_en_la_huella():
    """El recurso delimita el ámbito de la clave, no describe el contenido."""
    assert RECURSO_SESIONES_MONITOREO not in canonicalizar_paquete(entrada(paquete()))


# ---------------------------------------------------------------------------
# Un campo significativo cambia la huella
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("campo", "otro_valor"),
    [
        ("id_embarazo", 101),
        ("id_dispositivo", 101),
        ("fecha_inicio", "2026-03-01T13:00:00+00:00"),
        ("fecha_fin", "2026-03-01T15:00:00+00:00"),
        ("origen_dato", OrigenDato.CSV.value),
    ],
)
def test_cambiar_un_campo_de_la_sesion_cambia_la_huella(campo, otro_valor):
    assert huella(paquete()) != huella(paquete(**{campo: otro_valor}))


@pytest.mark.parametrize(
    ("campo", "otro_valor"),
    [
        ("id_tiempo_gest", 120),
        ("id_semaforo", 101),
        ("fecha_hora_captura", "2026-03-01T14:06:00+00:00"),
        ("fecha_hora_sincronizacion", "2026-03-01T14:41:00+00:00"),
        ("hr_valor", 88.6),
        ("spo2_valor", 96.0),
    ],
)
def test_cambiar_un_campo_de_una_lectura_cambia_la_huella(campo, otro_valor):
    otra = paquete(lecturas=[lectura_hr(**{campo: otro_valor})])

    assert huella(paquete()) != huella(otra)


def test_cambiar_el_tipo_de_sesion_cambia_la_huella():
    movimientos = paquete(
        tipo_sesion=TipoSesion.MOVIMIENTOS_FETALES.value,
        lecturas=[lectura_movimiento()],
    )

    assert huella(paquete()) != huella(movimientos)


def test_agregar_una_lectura_cambia_la_huella():
    dos = paquete(lecturas=[lectura_hr(), lectura_hr(id_semaforo=101)])

    assert huella(paquete()) != huella(dos)


def paquete_de_movimiento(**cambios: Any) -> dict[str, Any]:
    """Paquete válido de ``MOVIMIENTOS_FETALES`` con una sola lectura."""
    return paquete(
        tipo_sesion=TipoSesion.MOVIMIENTOS_FETALES.value,
        lecturas=[lectura_movimiento(**cambios)],
    )


def test_cambiar_solo_mov_valor_cambia_la_huella():
    """Dos paquetes válidos e idénticos salvo el conteo de movimientos.

    Nada más se toca: mismo tipo de sesión, mismas fechas, mismas referencias.
    Es la comprobación de que el conteo entra en la huella por sí mismo y no
    arrastrado por otro campo que hubiera cambiado con él.
    """
    doce = paquete_de_movimiento(mov_valor=12)
    trece = paquete_de_movimiento(mov_valor=13)

    assert huella(doce) != huella(trece)


# ---------------------------------------------------------------------------
# Valores por omisión efectivos
# ---------------------------------------------------------------------------


def test_las_constantes_por_omision_son_las_del_modelo():
    """Atadas a ``SesionMonitoreo``: es su ``default`` el que se aplica de verdad.

    ``_construir_sesion`` omite el atributo cuando el cliente no lo envía, así
    que el valor que acaba en la base sale del modelo. Si alguien lo cambiara
    allí y no aquí, la huella dejaría de describir lo que se guarda.
    """
    columnas = SesionMonitoreo.__table__.c

    assert columnas.estado_sesion.default.arg is ESTADO_POR_OMISION
    assert columnas.origen_dato.default.arg is ORIGEN_POR_OMISION
    assert ESTADO_POR_OMISION is EstadoSesion.PENDIENTE
    assert ORIGEN_POR_OMISION is OrigenDato.DISPOSITIVO


def test_omitir_el_origen_equivale_a_enviar_su_valor_por_omision():
    sin_origen = paquete()
    con_origen = paquete(origen_dato=ORIGEN_POR_OMISION.value)

    assert "origen_dato" not in sin_origen
    assert huella(sin_origen) == huella(con_origen)


def test_omitir_el_estado_equivale_a_enviar_su_valor_por_omision():
    """La sesión se deja abierta: PENDIENTE no admite ``fecha_fin``."""
    abierto = paquete()
    abierto.pop("fecha_fin")
    abierto.pop("estado_sesion")

    declarado = dict(abierto, estado_sesion=ESTADO_POR_OMISION.value)

    assert huella(abierto) == huella(declarado)


def test_el_estado_por_omision_no_se_confunde_con_otro_estado():
    abierto = paquete()
    abierto.pop("fecha_fin")
    abierto.pop("estado_sesion")

    interrumpido = dict(abierto, estado_sesion=EstadoSesion.INTERRUMPIDA.value)

    assert huella(abierto) != huella(interrumpido)


def test_el_contenido_canonico_resuelve_los_valores_por_omision():
    abierto = paquete()
    abierto.pop("fecha_fin")
    abierto.pop("estado_sesion")

    contenido = contenido_canonico(entrada(abierto))

    assert contenido["estado_sesion"] == ESTADO_POR_OMISION.value
    assert contenido["origen_dato"] == ORIGEN_POR_OMISION.value


# ---------------------------------------------------------------------------
# Decimales
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("equivalente", [97, 97.0, "97", "97.0", "97.00", Decimal("97.00")])
def test_los_decimales_equivalentes_dan_la_misma_huella(equivalente):
    """``NUMERIC(5, 2)`` guarda lo mismo para todos ellos."""
    referencia = paquete(lecturas=[lectura_hr(spo2_valor=97.0)])
    otro = paquete(lecturas=[lectura_hr(spo2_valor=equivalente)])

    assert huella(otro) == huella(referencia)


def test_un_decimal_distinto_en_el_segundo_decimal_cambia_la_huella():
    referencia = paquete(lecturas=[lectura_hr(spo2_valor=97.00)])
    otro = paquete(lecturas=[lectura_hr(spo2_valor=97.01)])

    assert huella(otro) != huella(referencia)


def test_el_cero_negativo_y_el_cero_dan_la_misma_huella():
    """``-0.00`` y ``0.00`` son el mismo número y se guardan como el mismo número."""
    cero = paquete(lecturas=[lectura_hr(spo2_valor="0.00")])
    cero_negativo = paquete(lecturas=[lectura_hr(spo2_valor="-0.00")])

    assert huella(cero_negativo) == huella(cero)


def test_los_decimales_viajan_como_texto_con_su_escala():
    """Un número JSON no puede llevar la escala, y la escala es lo que se guarda."""
    contenido = contenido_canonico(entrada(paquete(lecturas=[lectura_hr(hr_valor=88.5)])))

    assert contenido["lecturas"][0]["hr_valor"] == "88.50"


# ---------------------------------------------------------------------------
# Instantes
# ---------------------------------------------------------------------------


def test_el_mismo_instante_con_otro_offset_da_la_misma_huella():
    """``TIMESTAMPTZ`` guarda el instante, no el offset con que se escribió."""
    en_utc = paquete(
        fecha_inicio="2026-03-01T14:00:00+00:00",
        fecha_fin="2026-03-01T14:30:00+00:00",
        lecturas=[
            lectura_hr(
                fecha_hora_captura="2026-03-01T14:05:00+00:00",
                fecha_hora_sincronizacion="2026-03-01T14:40:00+00:00",
            )
        ],
    )
    en_panama = paquete(
        fecha_inicio="2026-03-01T09:00:00-05:00",
        fecha_fin="2026-03-01T09:30:00-05:00",
        lecturas=[
            lectura_hr(
                fecha_hora_captura="2026-03-01T09:05:00-05:00",
                fecha_hora_sincronizacion="2026-03-01T09:40:00-05:00",
            )
        ],
    )

    assert huella(en_panama) == huella(en_utc)


def test_un_instante_distinto_cambia_la_huella():
    un_minuto_despues = paquete(
        lecturas=[lectura_hr(fecha_hora_captura="2026-03-01T14:06:00+00:00")]
    )

    assert huella(un_minuto_despues) != huella(paquete())


def test_los_instantes_se_escriben_normalizados_a_utc():
    contenido = contenido_canonico(
        entrada(
            paquete(
                fecha_inicio="2026-03-01T09:00:00-05:00",
                fecha_fin="2026-03-01T09:30:00-05:00",
                lecturas=[
                    lectura_hr(
                        fecha_hora_captura="2026-03-01T09:05:00-05:00",
                        fecha_hora_sincronizacion="2026-03-01T09:40:00-05:00",
                    )
                ],
            )
        )
    )

    assert contenido["fecha_inicio"] == "2026-03-01T14:00:00+00:00"
    assert contenido["fecha_fin"] == "2026-03-01T14:30:00+00:00"
    assert contenido["lecturas"][0]["fecha_hora_captura"] == "2026-03-01T14:05:00+00:00"
    assert (
        datetime.fromisoformat(contenido["fecha_inicio"]).tzinfo == timezone.utc
    )


# ---------------------------------------------------------------------------
# fecha_hora_sincronizacion
# ---------------------------------------------------------------------------


def test_la_sincronizacion_entra_en_la_huella():
    """Se persiste, así que dos paquetes que difieren en ella no son el mismo.

    La consecuencia es una regla para el cliente, no una excepción aquí: la clave
    identifica un paquete inmutable, y un reenvío repite el paquete que preparó
    en vez de volver a sellarlo. Dejar esto probado evita que alguien lo lea
    como un defecto.
    """
    otra_sincronizacion = paquete(
        lecturas=[lectura_hr(fecha_hora_sincronizacion="2026-03-01T15:40:00+00:00")]
    )

    assert huella(otra_sincronizacion) != huella(paquete())


def test_una_sincronizacion_nula_no_se_confunde_con_una_presente():
    sin_sincronizar = paquete(lecturas=[lectura_hr(fecha_hora_sincronizacion=None)])

    assert huella(sin_sincronizar) != huella(paquete())


def test_omitir_la_sincronizacion_equivale_a_enviarla_nula():
    """Ambas llegan a PostgreSQL como NULL, así que son el mismo paquete."""
    omitida = lectura_hr()
    omitida.pop("fecha_hora_sincronizacion")

    explicita = paquete(lecturas=[lectura_hr(fecha_hora_sincronizacion=None)])

    assert huella(paquete(lecturas=[omitida])) == huella(explicita)


# ---------------------------------------------------------------------------
# NULL, enums, booleanos y enteros
# ---------------------------------------------------------------------------


def test_una_metrica_que_no_aplica_viaja_como_null_y_no_como_cero():
    contenido = contenido_canonico(entrada(paquete()))
    lectura = contenido["lecturas"][0]

    assert lectura["mov_valor"] is None
    assert '"mov_valor":null' in canonicalizar_paquete(entrada(paquete()))


def test_cambiar_el_conteo_de_movimiento_de_cero_a_doce_cambia_la_huella():
    """Un conteo de cero movimientos es un dato, y distinto de haber contado doce."""
    en_cero = paquete_de_movimiento(mov_valor=0)
    con_conteo = paquete_de_movimiento(mov_valor=12)

    assert huella(en_cero) != huella(con_conteo)


def test_un_conteo_de_cero_sigue_siendo_entero_y_no_se_vuelve_nulo():
    """«Se midió cero» no puede degradarse a «no se midió» al canonicalizar.

    Es la mitad que la prueba de arriba no cubre: allí se compara cero contra
    doce, que son dos conteos; aquí se mira el cero por dentro, porque si en
    algún punto se convirtiera en ``None`` sería indistinguible de una métrica
    que no aplica, y la ETL depende justamente de esa distinción.
    """
    contenido = contenido_canonico(entrada(paquete_de_movimiento(mov_valor=0)))
    valor = contenido["lecturas"][0]["mov_valor"]

    assert valor is not None
    assert valor == 0
    assert isinstance(valor, int) and not isinstance(valor, bool)
    assert '"mov_valor":0' in canonicalizar_paquete(
        entrada(paquete_de_movimiento(mov_valor=0))
    )


def test_un_conteo_de_cero_no_tiene_la_misma_huella_que_una_metrica_ausente():
    """Cero movimientos contra una lectura donde el movimiento no aplica."""
    en_cero = paquete_de_movimiento(mov_valor=0)

    assert huella(en_cero) != huella(paquete())


def test_omitir_fecha_fin_equivale_a_enviarla_nula():
    abierto = paquete()
    abierto.pop("fecha_fin")
    abierto["estado_sesion"] = EstadoSesion.PENDIENTE.value

    explicito = dict(abierto, fecha_fin=None)

    assert huella(abierto) == huella(explicito)


def test_los_enums_viajan_por_su_valor_de_texto():
    contenido = contenido_canonico(entrada(paquete()))

    assert contenido["tipo_sesion"] == TipoSesion.SIGNOS_MATERNOS.value
    assert contenido["estado_sesion"] == EstadoSesion.COMPLETADA.value
    assert isinstance(contenido["tipo_sesion"], str)


def test_los_enteros_viajan_como_enteros_y_no_como_texto():
    contenido = contenido_canonico(entrada(paquete()))

    assert isinstance(contenido["id_embarazo"], int)
    assert isinstance(contenido["lecturas"][0]["id_tiempo_gest"], int)
    assert '"id_embarazo":100' in canonicalizar_paquete(entrada(paquete()))


def test_un_booleano_no_puede_colarse_como_entero():
    """Lo garantiza ``StrictInt`` del contrato; se comprueba antes de la huella."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        entrada(paquete(id_embarazo=True))


# ---------------------------------------------------------------------------
# Orden de las lecturas
# ---------------------------------------------------------------------------


def test_el_orden_de_las_lecturas_se_conserva():
    primera = lectura_hr(id_semaforo=100)
    segunda = lectura_hr(id_semaforo=101)

    contenido = contenido_canonico(entrada(paquete(lecturas=[primera, segunda])))

    assert [fila["id_semaforo"] for fila in contenido["lecturas"]] == [100, 101]


def test_permutar_las_lecturas_cambia_la_huella():
    """``ids_lectura`` vuelve en ese orden: dos órdenes son dos respuestas."""
    primera = lectura_hr(id_semaforo=100)
    segunda = lectura_hr(id_semaforo=101)

    en_orden = paquete(lecturas=[primera, segunda])
    al_reves = paquete(lecturas=[segunda, primera])

    assert huella(al_reves) != huella(en_orden)


def test_la_serializacion_no_ordena_la_lista_de_lecturas():
    """``sort_keys`` ordena claves de objetos, nunca elementos de una lista."""
    texto = canonicalizar_paquete(
        entrada(
            paquete(lecturas=[lectura_hr(id_semaforo=101), lectura_hr(id_semaforo=100)])
        )
    )
    posiciones = [
        texto.index('"id_semaforo":101'),
        texto.index('"id_semaforo":100'),
    ]

    assert posiciones == sorted(posiciones)


# ---------------------------------------------------------------------------
# El recurso que delimita el ámbito de la clave
# ---------------------------------------------------------------------------


def test_el_recurso_nombra_la_ruta_y_el_metodo_reales():
    assert RECURSO_SESIONES_MONITOREO == "POST /api/v1/sesiones-monitoreo"


def test_el_recurso_cabe_en_su_columna():
    from app.models.idempotencia import LONGITUD_RECURSO

    assert len(RECURSO_SESIONES_MONITOREO) <= LONGITUD_RECURSO
