"""Sesiones de movimiento simuladas: el fixture, la captura y la sincronizacion.

Tres capas, como en el resto de la interfaz de la gestante:

* :mod:`app.gestante.simulacion` es puro -- se prueba con datos fabricados,
  validando el paquete contra el contrato real ``SesionMonitoreoEntrada``;
* :mod:`app.gestante.movimientos` abre SQLite de verdad, en ``tmp_path``, y
  ejercita :mod:`app.edge` sin ningun doble -- exactamente lo que SCRUM-64/65
  ya prueban se reutiliza aqui, no se repite;
* las tres rutas se prueban por HTTP, con el cliente central sustituido como en
  el resto de la suite y el cliente de sincronizacion sustituido por uno con
  ``httpx.MockTransport``, para no necesitar la API central de verdad.

No hace falta PostgreSQL ni la API central en ningun caso.

Todas las cuentas y los datos son ficticios y simulados.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
from fastapi.testclient import TestClient

from app.gestante import movimientos
from app.gestante.central import EstadoRespuesta, RespuestaClinica
from app.gestante.simulacion import construir_paquete_simulado
from app.models.enums import TipoSesion
from app.schemas.monitoreo import SesionMonitoreoEntrada
from tests.test_gestante_clinico import (
    ClienteClinicoDoble,
    ClienteClinicoMultiCuenta,
    embarazo,
    portal,
)
from tests.test_gestante_rutas import construir_cliente, construir_settings, iniciar_sesion

UTC = timezone.utc
AHORA = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)

RUTA_SESIONES_MONITOREO = "/api/v1/sesiones-monitoreo"


# ===========================================================================
# EL PAQUETE FIJO (puro)
# ===========================================================================


def test_el_paquete_de_signos_maternos_cumple_el_contrato_real():
    paquete = construir_paquete_simulado(
        id_embarazo=101, tipo_sesion=TipoSesion.SIGNOS_MATERNOS, ahora=AHORA
    )

    validado = SesionMonitoreoEntrada.model_validate(paquete)

    assert validado.id_embarazo == 101
    assert validado.tipo_sesion is TipoSesion.SIGNOS_MATERNOS
    assert len(validado.lecturas) == 1
    lectura = validado.lecturas[0]
    assert lectura.hr_valor is not None
    assert lectura.spo2_valor is not None
    assert lectura.mov_valor is None


def test_el_paquete_de_movimiento_cumple_el_contrato_real():
    paquete = construir_paquete_simulado(
        id_embarazo=202, tipo_sesion=TipoSesion.MOVIMIENTOS_FETALES, ahora=AHORA
    )

    validado = SesionMonitoreoEntrada.model_validate(paquete)

    assert validado.tipo_sesion is TipoSesion.MOVIMIENTOS_FETALES
    lectura = validado.lecturas[0]
    assert lectura.mov_valor is not None
    assert lectura.hr_valor is None
    assert lectura.spo2_valor is None


def test_dos_llamadas_con_la_misma_hora_producen_el_mismo_paquete():
    """Reproducible: nada al azar, nada que dependa del reloj del sistema."""
    a = construir_paquete_simulado(
        id_embarazo=1, tipo_sesion=TipoSesion.SIGNOS_MATERNOS, ahora=AHORA
    )
    b = construir_paquete_simulado(
        id_embarazo=1, tipo_sesion=TipoSesion.SIGNOS_MATERNOS, ahora=AHORA
    )
    assert a == b


# ===========================================================================
# CAPTURA Y ESTADO LOCAL (SQLite real, app.edge real, sin dobles)
# ===========================================================================


def test_registrar_una_sesion_simulada_queda_pendiente(tmp_path):
    settings = construir_settings(tmp_path)

    movimientos.registrar_sesion_simulada(
        settings,
        id_usuario=1,
        id_embarazo=101,
        tipo_sesion=TipoSesion.SIGNOS_MATERNOS,
        ahora=AHORA,
    )

    estado = movimientos.leer_estado_de_la_cuenta(settings, 1)
    assert estado.inicializado is True
    assert estado.pendientes == 1
    assert estado.total == 1


def test_una_cuenta_sin_registros_no_tiene_almacenamiento(tmp_path):
    settings = construir_settings(tmp_path)

    estado = movimientos.leer_estado_de_la_cuenta(settings, 999)

    assert estado.inicializado is False
    assert estado.pendientes is None


def test_el_registro_sobrevive_a_una_nueva_conexion(tmp_path):
    """«Reiniciar el componente» es exactamente esto: una conexion nueva."""
    settings = construir_settings(tmp_path)
    movimientos.registrar_sesion_simulada(
        settings,
        id_usuario=7,
        id_embarazo=101,
        tipo_sesion=TipoSesion.SIGNOS_MATERNOS,
        ahora=AHORA,
    )

    primera_lectura = movimientos.leer_estado_de_la_cuenta(settings, 7)
    segunda_lectura = movimientos.leer_estado_de_la_cuenta(settings, 7)

    assert primera_lectura.total == 1
    assert segunda_lectura.total == 1


def test_dos_cuentas_tienen_archivos_distintos_y_no_se_mezclan(tmp_path):
    settings = construir_settings(tmp_path)

    movimientos.registrar_sesion_simulada(
        settings,
        id_usuario=1,
        id_embarazo=101,
        tipo_sesion=TipoSesion.SIGNOS_MATERNOS,
        ahora=AHORA,
    )

    assert movimientos.ruta_para_la_cuenta(settings, 1) != movimientos.ruta_para_la_cuenta(
        settings, 2
    )
    assert movimientos.leer_estado_de_la_cuenta(settings, 1).total == 1
    # La cuenta 2 nunca registro nada: su almacenamiento ni siquiera existe.
    assert movimientos.leer_estado_de_la_cuenta(settings, 2).inicializado is False


def test_registrar_para_un_embarazo_ajeno_no_afecta_el_archivo_de_otra_cuenta(tmp_path):
    """No hay forma de que una captura de la cuenta 1 aparezca en la 2."""
    settings = construir_settings(tmp_path)
    for id_embarazo in (101, 202, 303):
        movimientos.registrar_sesion_simulada(
            settings,
            id_usuario=1,
            id_embarazo=id_embarazo,
            tipo_sesion=TipoSesion.SIGNOS_MATERNOS,
            ahora=AHORA,
        )

    assert movimientos.leer_estado_de_la_cuenta(settings, 1).total == 3
    assert movimientos.leer_estado_de_la_cuenta(settings, 2).inicializado is False


# ===========================================================================
# LA RUTA DE REGISTRO
# ===========================================================================


def test_registrar_sin_sesion_local_responde_401(tmp_path):
    cliente, _, _, _ = construir_cliente(tmp_path, central=ClienteClinicoDoble())

    respuesta = cliente.post(
        "/adaptador/embarazos/101/sesiones-simuladas",
        json={"tipo_sesion": "SIGNOS_MATERNOS"},
    )

    assert respuesta.status_code == 401


def test_registrar_sin_token_en_memoria_pide_reautenticacion(tmp_path):
    cliente = portal(tmp_path, ClienteClinicoDoble())
    # Se vacia el almacen de tokens en memoria, como haria un reinicio.
    cliente.app.state.contexto.tokens = type(cliente.app.state.contexto.tokens)()

    respuesta = cliente.post(
        "/adaptador/embarazos/101/sesiones-simuladas",
        json={"tipo_sesion": "SIGNOS_MATERNOS"},
    )

    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "disponible": False,
        "motivo": "reautenticacion_requerida",
    }


def test_registrar_para_un_embarazo_ajeno_responde_404(tmp_path):
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(EstadoRespuesta.OK, datos=(embarazo(101),))
    )
    cliente = portal(tmp_path, central)

    respuesta = cliente.post(
        "/adaptador/embarazos/999/sesiones-simuladas",
        json={"tipo_sesion": "SIGNOS_MATERNOS"},
    )

    assert respuesta.status_code == 404


def test_registrar_un_embarazo_propio_queda_local_y_pendiente(tmp_path):
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(EstadoRespuesta.OK, datos=(embarazo(101),))
    )
    cliente = portal(tmp_path, central)

    respuesta = cliente.post(
        "/adaptador/embarazos/101/sesiones-simuladas",
        json={"tipo_sesion": "SIGNOS_MATERNOS"},
    )

    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo["registrado"] is True
    assert cuerpo["estado"] == "local"
    assert cuerpo["tipo_sesion"] == "SIGNOS_MATERNOS"
    assert "clave" in cuerpo

    estado = cliente.get("/adaptador/movimientos/estado").json()
    assert estado["inicializado"] is True
    assert estado["pendientes"] == 1
    assert estado["total"] == 1


def test_un_tipo_de_sesion_invalido_es_422(tmp_path):
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(EstadoRespuesta.OK, datos=(embarazo(101),))
    )
    cliente = portal(tmp_path, central)

    respuesta = cliente.post(
        "/adaptador/embarazos/101/sesiones-simuladas",
        json={"tipo_sesion": "ALGO_QUE_NO_EXISTE"},
    )

    assert respuesta.status_code == 422


def test_un_cuerpo_con_un_campo_extra_es_422(tmp_path):
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(EstadoRespuesta.OK, datos=(embarazo(101),))
    )
    cliente = portal(tmp_path, central)

    respuesta = cliente.post(
        "/adaptador/embarazos/101/sesiones-simuladas",
        json={"tipo_sesion": "SIGNOS_MATERNOS", "hr_valor": "999.00"},
    )

    assert respuesta.status_code == 422


def test_dos_cuentas_registran_en_archivos_separados_por_http(tmp_path):
    """El mismo id_embarazo (101) es propio de las dos cuentas en este doble;
    lo que aisla el registro no es ese numero, es el id_usuario que cada
    sesion local trae desde su propio inicio de sesion -- que el navegador no
    elige."""
    correo_a, correo_b = "paciente-a@example.com", "paciente-b@example.com"
    token_a, token_b = "token-cuenta-a", "token-cuenta-b"
    central = ClienteClinicoMultiCuenta(
        tokens_por_correo={correo_a: token_a, correo_b: token_b},
        ids_por_token={token_a: 11, token_b: 22},
        datos_por_token={
            token_a: RespuestaClinica(EstadoRespuesta.OK, datos=(embarazo(101),)),
            token_b: RespuestaClinica(EstadoRespuesta.OK, datos=(embarazo(101),)),
        },
    )
    cliente_a, _, _, _ = construir_cliente(tmp_path, central=central)
    cliente_b = TestClient(cliente_a.app)
    iniciar_sesion(cliente_a, email=correo_a, password="da-igual-a")
    iniciar_sesion(cliente_b, email=correo_b, password="da-igual-b")

    cliente_a.post(
        "/adaptador/embarazos/101/sesiones-simuladas",
        json={"tipo_sesion": "SIGNOS_MATERNOS"},
    )

    estado_a = cliente_a.get("/adaptador/movimientos/estado").json()
    estado_b = cliente_b.get("/adaptador/movimientos/estado").json()

    assert estado_a["total"] == 1
    # La cuenta B nunca registro nada propio: su almacenamiento ni existe,
    # aunque comparta el mismo id_embarazo con la cuenta A.
    assert estado_b["inicializado"] is False


# ===========================================================================
# LA RUTA DE SINCRONIZACION (mecanismo real de app.edge, transporte con doble)
# ===========================================================================


def _cliente_http_con_respuesta(codigo: int, cuerpo: dict | None = None, cabeceras=None):
    def manejador(peticion: httpx.Request) -> httpx.Response:
        assert peticion.url.path == RUTA_SESIONES_MONITOREO
        assert peticion.headers.get("authorization", "").startswith("Bearer ")
        return httpx.Response(codigo, json=cuerpo or {}, headers=cabeceras or {})

    def constructor(settings, token):
        return httpx.Client(
            base_url=settings.api_base_url,
            transport=httpx.MockTransport(manejador),
            headers={"Authorization": f"Bearer {token}"},
        )

    return constructor


def test_sincronizar_sin_nada_pendiente_no_llama_a_la_red(tmp_path):
    def constructor(settings, token):
        def manejador(peticion: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("no deberia llamarse: la cola esta vacia")

        return httpx.Client(
            base_url=settings.api_base_url, transport=httpx.MockTransport(manejador)
        )

    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(EstadoRespuesta.OK, datos=(embarazo(101),))
    )
    cliente, _, _, _ = construir_cliente(
        tmp_path, central=central, constructor_cliente_edge=constructor
    )
    iniciar_sesion(cliente)

    respuesta = cliente.post("/adaptador/movimientos/sincronizar")

    assert respuesta.status_code == 200
    assert respuesta.json()["seleccionados"] == 0


def test_sincronizar_entrega_correctamente_marca_enviado(tmp_path):
    constructor = _cliente_http_con_respuesta(
        201,
        {"id_sesion": 555, "lecturas_creadas": 1, "ids_lectura": [999]},
        {"Idempotency-Replayed": "false"},
    )
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(EstadoRespuesta.OK, datos=(embarazo(101),))
    )
    cliente, _, _, _ = construir_cliente(
        tmp_path, central=central, constructor_cliente_edge=constructor
    )
    iniciar_sesion(cliente)
    cliente.post(
        "/adaptador/embarazos/101/sesiones-simuladas",
        json={"tipo_sesion": "SIGNOS_MATERNOS"},
    )

    respuesta = cliente.post("/adaptador/movimientos/sincronizar")

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["seleccionados"] == 1
    assert cuerpo["entregados"] == 1
    assert cuerpo["rechazados"] == 0

    estado = cliente.get("/adaptador/movimientos/estado").json()
    assert estado["pendientes"] == 0
    assert estado["enviados"] == 1


def test_recuperar_sincronizacion_no_reenvia_lo_ya_confirmado(tmp_path):
    """Volver a sincronizar tras un exito no reintenta el mismo evento.

    ``ENVIADO`` es terminal para la eligibilidad de app.edge (ver
    predicado_elegible), asi que una segunda ronda -- por ejemplo, tras un
    reinicio del navegador o un doble clic en «Sincronizar ahora» -- no debe
    ni siquiera tocar la red para el evento que ya se confirmo. Es la garantia
    de «recuperacion sin duplicados» que este ticket pide, ya provista por el
    mecanismo que se reutiliza sin cambios.
    """
    llamadas_de_red = []

    def manejador(peticion: httpx.Request) -> httpx.Response:
        llamadas_de_red.append(peticion)
        return httpx.Response(
            201,
            json={"id_sesion": 555, "lecturas_creadas": 1, "ids_lectura": [999]},
            headers={"Idempotency-Replayed": "false"},
        )

    def constructor(settings, token):
        return httpx.Client(
            base_url=settings.api_base_url,
            transport=httpx.MockTransport(manejador),
            headers={"Authorization": f"Bearer {token}"},
        )

    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(EstadoRespuesta.OK, datos=(embarazo(101),))
    )
    cliente, _, _, _ = construir_cliente(
        tmp_path, central=central, constructor_cliente_edge=constructor
    )
    iniciar_sesion(cliente)
    cliente.post(
        "/adaptador/embarazos/101/sesiones-simuladas",
        json={"tipo_sesion": "SIGNOS_MATERNOS"},
    )

    primera = cliente.post("/adaptador/movimientos/sincronizar").json()
    segunda = cliente.post("/adaptador/movimientos/sincronizar").json()

    assert primera["entregados"] == 1
    assert len(llamadas_de_red) == 1  # una sola llamada real a la API

    # La segunda ronda no encuentra nada elegible: el evento ya es ENVIADO.
    assert segunda["seleccionados"] == 0
    assert segunda["entregados"] == 0
    assert len(llamadas_de_red) == 1  # sigue en una: no hubo un segundo POST

    estado = cliente.get("/adaptador/movimientos/estado").json()
    assert estado["enviados"] == 1
    assert estado["total"] == 1  # nunca dos filas para la misma sesion


def test_sincronizar_un_rechazo_real_no_finge_exito(tmp_path):
    """El caso documentado en app.gestante.simulacion: el servidor puede
    rechazar el paquete de verdad -- aqui, un 404 de dispositivo o embarazo
    inexistente para esa base -- y la interfaz debe decirlo, no disfrazarlo."""
    constructor = _cliente_http_con_respuesta(404, {"detail": "no existe"})
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(EstadoRespuesta.OK, datos=(embarazo(101),))
    )
    cliente, _, _, _ = construir_cliente(
        tmp_path, central=central, constructor_cliente_edge=constructor
    )
    iniciar_sesion(cliente)
    cliente.post(
        "/adaptador/embarazos/101/sesiones-simuladas",
        json={"tipo_sesion": "SIGNOS_MATERNOS"},
    )

    respuesta = cliente.post("/adaptador/movimientos/sincronizar")

    cuerpo = respuesta.json()
    assert cuerpo["entregados"] == 0
    assert cuerpo["rechazados"] == 1

    estado = cliente.get("/adaptador/movimientos/estado").json()
    assert estado["pendientes"] == 0
    assert estado["fallidos_en_revision"] == 1
    assert estado["enviados"] == 0


def test_sincronizar_sin_sesion_local_responde_401(tmp_path):
    cliente, _, _, _ = construir_cliente(tmp_path, central=ClienteClinicoDoble())

    respuesta = cliente.post("/adaptador/movimientos/sincronizar")

    assert respuesta.status_code == 401


def test_estado_de_movimientos_sin_sesion_local_responde_401(tmp_path):
    cliente, _, _, _ = construir_cliente(tmp_path, central=ClienteClinicoDoble())

    respuesta = cliente.get("/adaptador/movimientos/estado")

    assert respuesta.status_code == 401
