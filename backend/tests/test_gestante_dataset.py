"""Del dataset canónico a la respuesta del portal, sin perder un valor (SCRUM-72).

La cuenta que el portal de demostración usa --``id_usuario`` 107, paciente
100-- tiene en el dataset canónico un embarazo FINALIZADO, el 100, cuya
**última** lectura (la 679) solo midió movimientos. Sus lecturas de signos
maternos, anteriores, sí tienen frecuencia cardíaca y saturación. El embarazo
en curso de esa cuenta, el 130, no es del dataset: lo agrega
``scripts/provisionar_demo.py``.

Este módulo regenera el dataset con su semilla fija --en un directorio
temporal, sin tocar ``data/generated``--, sirve las filas del embarazo 100 a
través del doble del cliente central y comprueba que la ruta de monitoreo:

* elige como última la 679 por ``(fecha_hora_captura, id_lectura)``, con FC y
  SpO₂ nulos, sin completarla con valores de otra lectura;
* entrega **todas** las lecturas del episodio, y cada valor de FC y SpO₂ igual
  al de origen;
* no mezcla lecturas de otros embarazos.

La representación de esas mismas lecturas en el navegador la comprueba
``frontend/gestante/pruebas/app.comportamiento.test.js``.

Todos los datos son ficticios y simulados.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.gestante.central import EstadoRespuesta, RespuestaClinica
from app.schemas.clinico import LecturaResumen, SesionResumen
from tests.test_gestante_clinico import ClienteClinicoDoble, portal
from tests.test_generate_mock_data import cargar_generador

ID_USUARIO_DEMO = 107
ID_EMBARAZO_CANONICO = 100
ID_ULTIMA_LECTURA = 679
CENTESIMA = Decimal("0.01")


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    generador = cargar_generador()
    carpeta = tmp_path_factory.mktemp("dataset")
    with pytest.MonkeyPatch.context() as parche:
        parche.setattr(generador, "CARPETA_SALIDA", carpeta)
        generador.main()
    return json.loads((carpeta / "dataset_fetalalert.json").read_text(encoding="utf-8"))


def _numerico(valor) -> Decimal | None:
    """Como lo devuelve PostgreSQL: NUMERIC(5, 2)."""
    if valor is None or valor == "":
        return None
    return Decimal(str(valor)).quantize(CENTESIMA)


def _fecha(valor) -> datetime:
    return datetime.fromisoformat(str(valor))


@pytest.fixture(scope="module")
def episodio_canonico(dataset):
    """Sesiones y lecturas del embarazo 100, tal como las tiene el dataset."""
    semanas = {t["id_tiempo_gest"]: t["semana_gestacion"] for t in dataset["tiempo_gestacional"]}
    niveles = {s["id_semaforo"]: s["codigo_nivel"] for s in dataset["semaforos"]}

    sesiones = [
        s for s in dataset["sesiones_monitoreo"] if s["id_embarazo"] == ID_EMBARAZO_CANONICO
    ]
    ids_de_sesion = {s["id_sesion"] for s in sesiones}
    lecturas = [
        l for l in dataset["lecturas_biometricas"] if l["id_sesion"] in ids_de_sesion
    ]

    lecturas_por_sesion: dict[int, list[LecturaResumen]] = {}
    for l in lecturas:
        lecturas_por_sesion.setdefault(l["id_sesion"], []).append(
            LecturaResumen(
                id_lectura=l["id_lectura"],
                fecha_hora_captura=_fecha(l["fecha_hora_captura"]),
                codigo_semaforo=niveles[l["id_semaforo"]],
                semana_gestacion=semanas[l["id_tiempo_gest"]],
                hr_valor=_numerico(l["hr_valor"]),
                spo2_valor=_numerico(l["spo2_valor"]),
                mov_valor=None if l["mov_valor"] in (None, "") else int(l["mov_valor"]),
            )
        )

    resumenes = tuple(
        SesionResumen(
            id_sesion=s["id_sesion"],
            tipo_sesion=s["tipo_sesion"],
            estado_sesion=s["estado_sesion"],
            fecha_inicio=_fecha(s["fecha_inicio"]),
            fecha_fin=None if not s["fecha_fin"] else _fecha(s["fecha_fin"]),
        )
        for s in sesiones
    )
    return {"lecturas": lecturas, "sesiones": resumenes, "por_sesion": lecturas_por_sesion}


@pytest.fixture
def respuesta(tmp_path, episodio_canonico):
    central = ClienteClinicoDoble(
        respuesta_sesiones=RespuestaClinica(
            EstadoRespuesta.OK, datos=episodio_canonico["sesiones"]
        ),
        lecturas_por_sesion=episodio_canonico["por_sesion"],
    )
    cliente = portal(tmp_path, central)
    cuerpo = cliente.get(f"/adaptador/embarazos/{ID_EMBARAZO_CANONICO}/monitoreo").json()
    assert cuerpo["disponible"] is True
    return cuerpo["datos"]


def test_la_cuenta_demo_tiene_el_embarazo_100_finalizado_en_el_dataset(dataset):
    paciente = next(
        u["id_paciente"] for u in dataset["usuario_paciente"] if u["id_usuario"] == ID_USUARIO_DEMO
    )
    embarazos = [e for e in dataset["embarazos"] if e["id_paciente"] == paciente]

    assert [e["id_embarazo"] for e in embarazos] == [ID_EMBARAZO_CANONICO]
    assert embarazos[0]["estado_embarazo"] == "FINALIZADO"
    assert date.fromisoformat(str(embarazos[0]["fecha_inicio"])) == date(2025, 1, 6)
    # El embarazo en curso (130) no existe en el dataset: lo agrega el aprovisionamiento.
    assert all(e["id_embarazo"] != 130 for e in dataset["embarazos"])


def test_la_ultima_lectura_es_la_679_y_solo_midio_movimientos(respuesta, episodio_canonico):
    origen = max(
        episodio_canonico["lecturas"],
        key=lambda l: (_fecha(l["fecha_hora_captura"]), l["id_lectura"]),
    )
    assert origen["id_lectura"] == ID_ULTIMA_LECTURA

    ultima = respuesta["ultima_lectura"]
    assert ultima["id_lectura"] == ID_ULTIMA_LECTURA
    assert ultima["mov_valor"] == int(origen["mov_valor"])
    # No se completa con FC ni SpO2 de otra lectura.
    assert ultima["hr_valor"] is None
    assert ultima["spo2_valor"] is None


def test_todas_las_lecturas_con_fc_y_spo2_llegan_con_su_valor_de_origen(
    respuesta, episodio_canonico
):
    entregadas = {
        l["id_lectura"]: l for s in respuesta["sesiones"] for l in s["lecturas"]
    }
    origen = episodio_canonico["lecturas"]

    assert set(entregadas) == {l["id_lectura"] for l in origen}, "ni sobran ni faltan"
    assert len(origen) == 41
    assert len(respuesta["sesiones"]) == 25

    con_signos = [l for l in origen if l["hr_valor"] not in (None, "")]
    assert len(con_signos) == 20
    for l in con_signos:
        entregada = entregadas[l["id_lectura"]]
        assert Decimal(entregada["hr_valor"]) == Decimal(str(l["hr_valor"]))
        assert Decimal(entregada["spo2_valor"]) == Decimal(str(l["spo2_valor"]))
        assert entregada["mov_valor"] is None


def test_la_lectura_110_del_dataset_se_entrega_intacta(respuesta):
    """La lectura concreta que se revisa en la interfaz: 2025-09-08, FC 86, SpO₂ 97."""
    lectura = next(
        l for s in respuesta["sesiones"] for l in s["lecturas"] if l["id_lectura"] == 110
    )

    assert lectura["fecha_hora_captura"] == "2025-09-08T17:13:00+00:00"
    assert lectura["hr_valor"] == "86.00"
    assert lectura["spo2_valor"] == "97.00"
    assert lectura["mov_valor"] is None
    assert lectura["semana_gestacion"] == 36
    assert lectura["codigo_semaforo"] == "OK"
