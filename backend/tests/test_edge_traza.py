"""La traza local de un evento: completa, correlacionada y segura de imprimir.

Dos cosas se comprueban aqui y son igual de importantes. La primera es que la
traza permita reconstruir el recorrido entero -- cuando se capturo, cuantos
intentos hubo, que paso en cada uno, que espera se aplico, por que se detuvo,
cuando se confirmo, cuando se marco sincronizado y que identificadores remotos le
corresponden --. La segunda es que **no** permita reconstruir el paquete: ni un
valor clinico, ni una credencial, ni una URL, ni una cabecera, ni SQL.

El identificador de correlacion se muestra a proposito. Es la
``Idempotency-Key``: un UUID4 opaco, sin nombre, sin valor clinico y sin
credencial, que ya viaja en cada peticion y ya esta persistido en PostgreSQL. No
se crea un segundo identificador para lo mismo.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.edge import almacenamiento as alm
from app.edge import captura as cap
from app.edge import outbox
from app.edge import sincronizacion as sincro
from app.edge.estados import EstadoEntrega, MotivoRevision
from tests.test_edge_captura import paquete_de_varias_lecturas
from tests.test_edge_sincronizacion import (
    RelojFalso,
    Sleeper,
    Transporte,
    cliente,
    creada,
    politica,
    reclamar,
)

# Valores clinicos que el paquete lleva y que la traza no debe repetir jamas.
HR = 137
SPO2 = 96


@pytest.fixture
def conexion(tmp_path):
    with alm.conectar(tmp_path / "nodo_edge.sqlite3") as conexion:
        alm.inicializar(conexion)
        yield conexion


def paquete_con_valores() -> dict:
    paquete = paquete_de_varias_lecturas(2)
    paquete["lecturas"][0]["hr_valor"] = HR
    paquete["lecturas"][0]["spo2_valor"] = SPO2
    return paquete


def texto_de(traza) -> str:
    """Todo lo que la traza expone, aplanado, para buscar filtraciones."""
    partes = [repr(traza)]
    for intento in traza.intentos_registrados:
        partes.append(repr(intento))
    return " ".join(partes)


# ---------------------------------------------------------------------------
# 1. La traza reconstruye el recorrido
# ---------------------------------------------------------------------------


def test_la_traza_se_consulta_por_la_clave_de_correlacion(conexion):
    registro = cap.capturar(conexion, paquete_con_valores())
    traza = outbox.leer_traza(conexion, clave=registro.clave)
    assert traza is not None
    assert traza.correlation_id == registro.clave
    assert traza.id_outbox == registro.id_outbox


def test_la_traza_tambien_se_consulta_por_el_id_local(conexion):
    registro = cap.capturar(conexion, paquete_con_valores())
    traza = outbox.leer_traza(conexion, id_outbox=registro.id_outbox)
    assert traza.correlation_id == registro.clave


def test_una_clave_inexistente_no_es_un_error(conexion):
    assert outbox.leer_traza(conexion, clave="no-existe-esta-clave") is None


def test_hay_que_indicar_exactamente_un_criterio(conexion):
    with pytest.raises(ValueError):
        outbox.leer_traza(conexion)
    with pytest.raises(ValueError):
        outbox.leer_traza(conexion, clave="x", id_outbox=1)


def test_la_traza_recorre_captura_intentos_confirmacion_y_sincronizacion(conexion):
    """Los cuatro momentos, con la semantica documentada."""
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    p = politica(max_attempts=4, base_delay_seconds=2.0)
    registro = cap.capturar(conexion, paquete_con_valores(), reloj=reloj)

    def guion(peticion, n):
        if n == 1:
            return httpx.Response(503, json={"detail": "no disponible"})
        return creada(reproducido=True, lecturas=2)

    sincro.sincronizar(
        conexion, cliente(Transporte(guion)), politica=p, reloj=reloj, dormir=dormir
    )
    traza = outbox.leer_traza(conexion, clave=registro.clave)

    # 1. Captura.
    assert traza.capturado_en == "2026-03-01T12:00:00+00:00"
    # 2. Un intento por fila, en orden, con su espera.
    assert [i.numero for i in traza.intentos_registrados] == [1, 2]
    assert traza.intentos_registrados[0].resultado == "REINTENTABLE"
    assert traza.intentos_registrados[0].demora_programada_s == 2.0
    assert traza.intentos_registrados[1].resultado == "ENTREGADO"
    # 3. Confirmacion: la del intento que aplico la transicion.
    assert traza.intentos_registrados[1].confirmo_transicion == 1
    assert traza.confirmado_en == traza.intentos_registrados[1].finalizado_en
    # 4. Sincronizacion.
    assert traza.sincronizado_en == traza.enviado_en
    assert traza.estado == EstadoEntrega.ENVIADO.value
    assert traza.reproducido is True
    assert traza.id_sesion_remota == 832
    assert json.loads(traza.ids_lectura_remotos) == [1280, 1281]


def test_un_intento_sin_resultado_se_ve_como_tal(conexion):
    reloj = RelojFalso()
    p = politica(max_attempts=5)
    registro = cap.capturar(conexion, paquete_con_valores(), reloj=reloj)
    reclamar(conexion, registro.id_outbox, p=p, momento=reloj())
    reloj.avanzar(p.duracion_del_lease + 1)
    sincro.reconciliar_abandonados(conexion, politica=p, momento=reloj())

    traza = outbox.leer_traza(conexion, clave=registro.clave)
    intento = traza.intentos_registrados[0]
    assert intento.resultado is None
    assert intento.finalizado_en is None
    assert intento.reconciliado_en is not None
    assert traza.confirmado_en is None
    assert traza.ultimo_http is None
    assert traza.ultimo_error == outbox.ERROR_SIN_RESULTADO


def test_la_traza_distingue_agotamiento_de_rechazo_permanente(conexion):
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    agotado = cap.capturar(conexion, paquete_con_valores(), reloj=reloj)
    rechazado = cap.capturar(conexion, paquete_con_valores(), reloj=reloj)

    def guion(peticion, n):
        if peticion.headers["Idempotency-Key"] == agotado.clave:
            return httpx.Response(503, json={"detail": "no disponible"})
        return httpx.Response(409, json={"detail": "colision"})

    sincro.sincronizar(
        conexion, cliente(Transporte(guion)),
        politica=politica(max_attempts=2, base_delay_seconds=1.0),
        reloj=reloj, dormir=dormir,
    )

    assert outbox.leer_traza(conexion, clave=agotado.clave).motivo_revision == (
        MotivoRevision.AGOTAMIENTO.value
    )
    assert outbox.leer_traza(conexion, clave=rechazado.clave).motivo_revision == (
        MotivoRevision.RECHAZO_PERMANENTE.value
    )
    for traza in (
        outbox.leer_traza(conexion, clave=agotado.clave),
        outbox.leer_traza(conexion, clave=rechazado.clave),
    ):
        assert traza.requiere_revision is True


def test_la_traza_declara_los_intentos_heredados_sin_inventarlos(conexion):
    """1-3 sin historial, 4 con detalle. El hueco es la senal honesta."""
    reloj = RelojFalso()
    registro = cap.capturar(conexion, paquete_con_valores(), reloj=reloj)
    with alm.transaccion(conexion):
        conexion.execute(
            "UPDATE outbox SET intentos = 3, intentos_heredados = 3,"
            " estado = 'FALLIDO', reintentable = 1 WHERE id_outbox = ?",
            (registro.id_outbox,),
        )
    reclamar(conexion, registro.id_outbox, p=politica(max_attempts=5), momento=reloj())

    traza = outbox.leer_traza(conexion, clave=registro.clave)
    assert traza.intentos == 4
    assert traza.intentos_heredados == 3
    assert [i.numero for i in traza.intentos_registrados] == [4]
    # La invariante de coherencia, sobre la traza misma.
    assert len(traza.intentos_registrados) == traza.intentos - traza.intentos_heredados


def test_un_enviado_heredado_declara_que_la_confirmacion_no_se_midio(conexion):
    """Sin intentos registrados no hay instante de confirmacion que mostrar."""
    registro = cap.capturar(conexion, paquete_con_valores())
    with alm.transaccion(conexion):
        conexion.execute(
            "UPDATE outbox SET estado = 'ENVIADO', intentos = 1,"
            " intentos_heredados = 1, enviado_en = '2026-03-01T09:00:00+00:00',"
            " id_sesion_remota = 832, ids_lectura_remotos = '[1280]',"
            " ultimo_http = 201 WHERE id_outbox = ?",
            (registro.id_outbox,),
        )
    traza = outbox.leer_traza(conexion, clave=registro.clave)
    assert traza.estado == EstadoEntrega.ENVIADO.value
    assert traza.sincronizado_en == "2026-03-01T09:00:00+00:00"
    assert traza.confirmado_en is None
    assert traza.reproducido is None


def test_los_intentos_salen_en_orden_determinista(conexion):
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    registro = cap.capturar(conexion, paquete_con_valores(), reloj=reloj)
    sincro.sincronizar(
        conexion,
        cliente(Transporte(lambda peticion, n: httpx.Response(503, json={"detail": "x"}))),
        politica=politica(max_attempts=4, base_delay_seconds=1.0),
        reloj=reloj, dormir=dormir,
    )
    traza = outbox.leer_traza(conexion, clave=registro.clave)
    numeros = [i.numero for i in traza.intentos_registrados]
    assert numeros == sorted(numeros) == [1, 2, 3, 4]


# ---------------------------------------------------------------------------
# 2. Lo que la traza nunca expone
# ---------------------------------------------------------------------------


def test_la_traza_no_contiene_el_paquete(conexion):
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    registro = cap.capturar(conexion, paquete_con_valores(), reloj=reloj)
    sincro.sincronizar(
        conexion,
        cliente(Transporte(lambda peticion, n: creada(reproducido=False, lecturas=2))),
        politica=politica(), reloj=reloj, dormir=dormir,
    )
    plano = texto_de(outbox.leer_traza(conexion, clave=registro.clave))

    payload = outbox.leer_payload(conexion, registro.id_captura)
    assert payload not in plano
    for prohibido in ("hr_valor", "spo2_valor", "lecturas", "id_embarazo"):
        assert prohibido not in plano
    for valor in (str(HR), str(SPO2)):
        assert f'"{valor}"' not in plano


def test_un_422_estructurado_no_deja_el_valor_rechazado_en_la_traza(conexion):
    """El ``detail`` de FastAPI lleva ``input``, y ese ``input`` es dato clinico."""
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    registro = cap.capturar(conexion, paquete_con_valores(), reloj=reloj)

    cuerpo = {
        "detail": [
            {
                "type": "less_than_equal",
                "loc": ["body", "lecturas", 0, "hr_valor"],
                "msg": "menor o igual",
                "input": HR,
            }
        ]
    }
    sincro.sincronizar(
        conexion,
        cliente(Transporte(lambda peticion, n: httpx.Response(422, json=cuerpo))),
        politica=politica(), reloj=reloj, dormir=dormir,
    )

    plano = texto_de(outbox.leer_traza(conexion, clave=registro.clave))
    assert str(HR) not in plano
    assert "hr_valor" not in plano
    assert "entrada" in plano or "entrada(s)" in plano or "detalle" in plano


def test_la_traza_no_contiene_la_url_ni_cabeceras_ni_sql(conexion):
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    registro = cap.capturar(conexion, paquete_con_valores(), reloj=reloj)
    sincro.sincronizar(
        conexion,
        cliente(Transporte(lambda peticion, n: httpx.Response(503, json={"detail": "x"}))),
        politica=politica(max_attempts=2, base_delay_seconds=1.0),
        reloj=reloj, dormir=dormir,
    )
    plano = texto_de(outbox.leer_traza(conexion, clave=registro.clave))
    for prohibido in (
        "http://", "https://", "Authorization", "Idempotency-Key:",
        "SELECT", "INSERT", "UPDATE", "Traceback", "password", "postgresql",
    ):
        assert prohibido not in plano


def test_el_error_guardado_se_trunca(conexion):
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    registro = cap.capturar(conexion, paquete_con_valores(), reloj=reloj)
    largo = "x" * 5000
    sincro.sincronizar(
        conexion,
        cliente(Transporte(lambda peticion, n: httpx.Response(503, json={"detail": largo}))),
        politica=politica(max_attempts=1), reloj=reloj, dormir=dormir,
    )
    traza = outbox.leer_traza(conexion, clave=registro.clave)
    assert len(traza.ultimo_error) <= outbox.LONGITUD_MAXIMA_DE_ERROR
    assert len(traza.intentos_registrados[0].error) <= outbox.LONGITUD_MAXIMA_DE_ERROR


def test_un_fallo_de_transporte_guarda_solo_la_clase(conexion):
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    registro = cap.capturar(conexion, paquete_con_valores(), reloj=reloj)

    def guion(peticion, n):
        raise httpx.ConnectError("no se pudo conectar a http://usuario:clave@host", request=peticion)

    sincro.sincronizar(
        conexion, cliente(Transporte(guion)), politica=politica(max_attempts=1),
        reloj=reloj, dormir=dormir,
    )
    traza = outbox.leer_traza(conexion, clave=registro.clave)
    assert traza.intentos_registrados[0].error == "fallo de transporte: ConnectError"
    assert "clave" not in traza.intentos_registrados[0].error
    assert "http" not in traza.intentos_registrados[0].error.lower().replace("fallo", "")
