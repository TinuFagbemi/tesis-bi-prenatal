"""Clasificacion de respuestas, transiciones y concurrencia local del emisor.

Aqui vive la parte del ticket que decide **que significo la respuesta del
servidor** y **que se escribe entonces en la outbox**. Todo se ejerce con
``httpx.MockTransport``, que permite guionar cualquier respuesta --y cualquier
fallo de transporte-- sin levantar un servidor y sin agregar una dependencia: el
transporte de pruebas viene dentro de ``httpx``, que ya es dependencia directa
del backend.

El ciclo real contra la aplicacion FastAPI y PostgreSQL 16 vive en
``test_edge_postgresql.py``. Lo que se prueba aqui es la logica, no la
integracion.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.edge import almacenamiento as alm
from app.edge import captura as cap
from app.edge import outbox
from app.edge.cliente import (
    CABECERA_IDEMPOTENCIA,
    CABECERA_REPLAY,
    RUTA_SESIONES,
    ClienteEdge,
    ResultadoEntrega,
)
from app.edge.emisor import ejecutar_pasada
from app.edge.estados import EstadoEntrega, MotivoRevision
from tests.test_edge_captura import (
    PAQUETE_DE_UNA_LECTURA,
    paquete_con_sincronizacion,
    paquete_de_varias_lecturas,
)

BASE = "http://nodo-edge.invalid"


@pytest.fixture
def conexion(tmp_path):
    with alm.conectar(tmp_path / "nodo_edge.sqlite3") as conexion:
        alm.inicializar(conexion)
        yield conexion


def cliente_con(manejador) -> ClienteEdge:
    """Un ``ClienteEdge`` sobre un transporte guionado."""
    return ClienteEdge(
        httpx.Client(transport=httpx.MockTransport(manejador), base_url=BASE)
    )


def respuesta_creada(*, reproducido: bool, lecturas: int = 1, id_sesion: int = 832):
    """Una respuesta 201 valida, de primera ejecucion o de replay."""

    def manejador(peticion: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={
                "id_sesion": id_sesion,
                "lecturas_creadas": lecturas,
                "ids_lectura": [1280 + indice for indice in range(lecturas)],
            },
            headers={CABECERA_REPLAY: "true" if reproducido else "false"},
        )

    return manejador


def respuesta_fija(codigo: int, cuerpo=None, cabeceras=None):
    def manejador(peticion: httpx.Request) -> httpx.Response:
        return httpx.Response(
            codigo, json=cuerpo if cuerpo is not None else {}, headers=cabeceras or {}
        )

    return manejador


def transporte_caido(excepcion: type[httpx.TransportError]):
    def manejador(peticion: httpx.Request):
        raise excepcion("simulado", request=peticion)

    return manejador


def capturar_uno(conexion, paquete=None):
    return cap.capturar(conexion, paquete or PAQUETE_DE_UNA_LECTURA)


def estado_de(conexion, id_outbox) -> str:
    return outbox.leer_evento(conexion, id_outbox)["estado"]


# ---------------------------------------------------------------------------
# 1. Las constantes del contrato no pueden separarse del servidor
# ---------------------------------------------------------------------------


def test_la_ruta_y_las_cabeceras_coinciden_con_las_del_servidor():
    """El edge las declara por su cuenta para no importar la app; esto las ata."""
    from app.api.v1.sesiones import (
        CABECERA_IDEMPOTENCIA as CABECERA_SERVIDOR,
        CABECERA_REPLAY as REPLAY_SERVIDOR,
    )
    from app.main import app

    assert CABECERA_IDEMPOTENCIA == CABECERA_SERVIDOR
    assert CABECERA_REPLAY == REPLAY_SERVIDOR

    # Se mira el esquema OpenAPI y no ``app.routes``: esta version de FastAPI
    # deja los routers incluidos anidados en lugar de aplanarlos, y ademas el
    # esquema es el contrato que el servidor publica de verdad.
    esquema = app.openapi()
    assert RUTA_SESIONES in esquema["paths"]
    assert "post" in esquema["paths"][RUTA_SESIONES]


def test_la_clave_generada_por_el_edge_la_acepta_el_servidor():
    from app.services.idempotencia import clave_valida

    for _ in range(20):
        assert clave_valida(cap.generar_clave())


# ---------------------------------------------------------------------------
# 2. Clasificacion de respuestas
# ---------------------------------------------------------------------------


def test_una_primera_aceptacion_marca_enviado(conexion):
    registro = capturar_uno(conexion)
    resumen = ejecutar_pasada(conexion, cliente_con(respuesta_creada(reproducido=False)))

    assert resumen.entregados == 1
    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.ENVIADO.value
    assert fila["id_sesion_remota"] == 832
    assert json.loads(fila["ids_lectura_remotos"]) == [1280]
    assert fila["enviado_en"] is not None
    assert fila["reintentable"] is None


def test_un_replay_equivalente_tambien_marca_enviado(conexion):
    registro = capturar_uno(conexion)
    ejecutar_pasada(conexion, cliente_con(respuesta_creada(reproducido=True)))

    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.ENVIADO.value
    assert fila["id_sesion_remota"] == 832


def test_el_resultado_remoto_almacenado_coincide_con_la_respuesta(conexion):
    registro = capturar_uno(conexion, paquete_de_varias_lecturas(3))
    ejecutar_pasada(conexion, cliente_con(respuesta_creada(reproducido=False, lecturas=3)))

    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert json.loads(fila["ids_lectura_remotos"]) == [1280, 1281, 1282]


@pytest.mark.parametrize("codigo", [500, 502, 503])
def test_un_error_del_servidor_queda_reintentable(conexion, codigo):
    registro = capturar_uno(conexion)
    resumen = ejecutar_pasada(conexion, cliente_con(respuesta_fija(codigo, {"detail": "x"})))

    assert resumen.reintentables == 1
    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.FALLIDO.value
    assert fila["reintentable"] == 1
    assert fila["ultimo_http"] == codigo


def test_un_409_no_se_confunde_con_replay_y_queda_para_revision(conexion):
    registro = capturar_uno(conexion)
    resumen = ejecutar_pasada(
        conexion,
        cliente_con(respuesta_fija(409, {"detail": "La cabecera ya identifica otro paquete."})),
    )

    assert resumen.rechazados == 1
    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.FALLIDO.value
    assert fila["reintentable"] == 0
    assert fila["id_sesion_remota"] is None


def test_un_409_conserva_clave_y_payload_intactos(conexion):
    registro = capturar_uno(conexion)
    antes = outbox.leer_payload(conexion, registro.id_captura)
    ejecutar_pasada(conexion, cliente_con(respuesta_fija(409, {"detail": "colision"})))

    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["clave_idempotencia"] == registro.clave
    assert outbox.leer_payload(conexion, registro.id_captura) == antes


def test_un_409_no_se_reintenta_en_la_siguiente_pasada(conexion):
    """Repetir la misma operacion esperando otro resultado no es una politica."""
    capturar_uno(conexion)
    ejecutar_pasada(conexion, cliente_con(respuesta_fija(409, {"detail": "colision"})))

    segunda = ejecutar_pasada(conexion, cliente_con(respuesta_fija(409, {"detail": "colision"})))
    assert segunda.seleccionados == 0


@pytest.mark.parametrize("codigo", [400, 404, 422])
def test_un_rechazo_de_validacion_no_es_exito_ni_reintentable(conexion, codigo):
    registro = capturar_uno(conexion)
    resumen = ejecutar_pasada(conexion, cliente_con(respuesta_fija(codigo, {"detail": "x"})))

    assert resumen.rechazados == 1
    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.FALLIDO.value
    assert fila["reintentable"] == 0


# ---------------------------------------------------------------------------
# Los limites de la familia reintentable
#
# El contrato promete reintentos para la **familia 5xx**, ni un codigo menos ni
# uno mas. Estas pruebas fijan los dos bordes y la decision sobre 408 y 429, que
# son los casos que una lectura descuidada movería de lado.
# ---------------------------------------------------------------------------


def test_un_599_sigue_siendo_reintentable(conexion):
    """El borde superior de la familia, con su desenlace completo.

    ``599`` es 5xx y por tanto se reintenta: queda ``FALLIDO`` reintentable, sin
    motivo de revision --no requiere una persona-- y con su proxima fecha ya
    programada.
    """
    registro = capturar_uno(conexion)
    resumen = ejecutar_pasada(conexion, cliente_con(respuesta_fija(599, {"detail": "x"})))

    assert resumen.reintentables == 1
    assert resumen.rechazados == 0
    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.FALLIDO.value
    assert fila["reintentable"] == 1
    assert fila["motivo_revision"] is None
    assert fila["proximo_intento_en"] is not None
    assert fila["ultimo_http"] == 599


@pytest.mark.parametrize("codigo", [600, 699, 999])
def test_un_codigo_por_encima_de_la_familia_5xx_no_se_reintenta(conexion, codigo):
    """La cota superior de ``5xx`` no es decoracion.

    Escrita como ``500 <= codigo``, la condicion se traga cualquier codigo no
    estandar por arriba y el nodo insistiria contra una respuesta que no
    pertenece a ninguna familia documentada y que nada promete que vaya a
    mejorar. Aqui se comprueba lo contrario: cae en «respuesta inesperada»,
    gasta un solo intento y se cierra para revision.
    """
    registro = capturar_uno(conexion)
    resumen = ejecutar_pasada(conexion, cliente_con(respuesta_fija(codigo, {"detail": "x"})))

    assert resumen.rechazados == 1
    assert resumen.reintentables == 0
    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.FALLIDO.value
    assert fila["reintentable"] == 0
    assert fila["motivo_revision"] == MotivoRevision.RECHAZO_PERMANENTE.value
    assert fila["proximo_intento_en"] is None
    assert fila["ultimo_http"] == codigo


@pytest.mark.parametrize("codigo", [408, 429])
def test_408_y_429_siguen_siendo_permanentes(conexion, codigo):
    """Una decision consciente, no un olvido.

    Los dos codigos *suenan* transitorios --tiempo agotado y demasiadas
    peticiones-- y en otro servicio lo serian. En este no: el endpoint no
    implementa ni timeouts de peticion ni limitacion de tasa, asi que tratarlos
    como recuperables anadiria un camino que ninguna prueba podria ejercer
    contra el servidor real y abriria la cuestion de honrar ``Retry-After``, que
    **no se implementa**. Si algun dia el servidor los emitiera, esta prueba es
    el sitio donde la decision tendria que revisarse a proposito.
    """
    registro = capturar_uno(conexion)
    resumen = ejecutar_pasada(conexion, cliente_con(respuesta_fija(codigo, {"detail": "x"})))

    assert resumen.rechazados == 1
    assert resumen.reintentables == 0
    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.FALLIDO.value
    assert fila["reintentable"] == 0
    assert fila["motivo_revision"] == MotivoRevision.RECHAZO_PERMANENTE.value
    assert fila["proximo_intento_en"] is None
    assert fila["ultimo_http"] == codigo


@pytest.mark.parametrize(
    "excepcion", [httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout]
)
def test_un_fallo_de_transporte_conserva_el_evento(conexion, excepcion):
    registro = capturar_uno(conexion)
    antes = outbox.leer_payload(conexion, registro.id_captura)

    resumen = ejecutar_pasada(conexion, cliente_con(transporte_caido(excepcion)))

    assert resumen.reintentables == 1
    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.FALLIDO.value
    assert fila["reintentable"] == 1
    assert fila["ultimo_http"] is None
    assert fila["clave_idempotencia"] == registro.clave
    assert outbox.leer_payload(conexion, registro.id_captura) == antes


@pytest.mark.parametrize("codigo", [200, 202, 204, 301])
def test_un_codigo_exitoso_inesperado_no_marca_enviado(conexion, codigo):
    registro = capturar_uno(conexion)
    ejecutar_pasada(conexion, cliente_con(respuesta_fija(codigo, {})))
    assert estado_de(conexion, registro.id_outbox) == EstadoEntrega.FALLIDO.value


def test_un_201_sin_la_cabecera_de_replay_no_marca_enviado(conexion):
    registro = capturar_uno(conexion)
    ejecutar_pasada(
        conexion,
        cliente_con(
            respuesta_fija(
                201, {"id_sesion": 1, "lecturas_creadas": 1, "ids_lectura": [2]}
            )
        ),
    )
    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.FALLIDO.value
    assert fila["reintentable"] == 0


def test_un_201_con_cuerpo_invalido_no_marca_enviado(conexion):
    registro = capturar_uno(conexion)
    ejecutar_pasada(
        conexion,
        cliente_con(
            respuesta_fija(201, {"no_es": "el contrato"}, {CABECERA_REPLAY: "false"})
        ),
    )
    assert estado_de(conexion, registro.id_outbox) == EstadoEntrega.FALLIDO.value


def test_un_201_internamente_incoherente_no_marca_enviado(conexion):
    """``lecturas_creadas`` tiene que coincidir con la cantidad de ``ids_lectura``."""
    registro = capturar_uno(conexion)
    ejecutar_pasada(
        conexion,
        cliente_con(
            respuesta_fija(
                201,
                {"id_sesion": 1, "lecturas_creadas": 3, "ids_lectura": [2]},
                {CABECERA_REPLAY: "false"},
            )
        ),
    )
    assert estado_de(conexion, registro.id_outbox) == EstadoEntrega.FALLIDO.value


def test_un_201_que_no_confirma_este_paquete_no_marca_enviado(conexion):
    """La quinta condicion: el conteo tiene que coincidir con lo que se envio.

    Un 201 impecable puede describir *otro* paquete. Sin esta comprobacion, una
    respuesta con una sola lectura confirmaria un paquete de tres.
    """
    registro = capturar_uno(conexion, paquete_de_varias_lecturas(3))
    ejecutar_pasada(conexion, cliente_con(respuesta_creada(reproducido=False, lecturas=1)))

    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.FALLIDO.value
    assert fila["reintentable"] == 0
    assert "3" in fila["ultimo_error"] and "1" in fila["ultimo_error"]


def test_un_payload_local_corrupto_se_detecta_sin_llamar_a_la_red(conexion):
    registro = capturar_uno(conexion)
    with alm.transaccion(conexion):
        conexion.execute(
            "UPDATE captura_local SET payload_json = 'esto no es json' WHERE id_captura = ?",
            (registro.id_captura,),
        )

    llamadas = []

    def manejador(peticion):
        llamadas.append(1)
        return httpx.Response(201)

    ejecutar_pasada(conexion, cliente_con(manejador))

    assert llamadas == []
    assert estado_de(conexion, registro.id_outbox) == EstadoEntrega.FALLIDO.value


# ---------------------------------------------------------------------------
# 3. Lo que viaja por el cable
# ---------------------------------------------------------------------------


def test_se_envia_la_clave_persistida_y_el_cuerpo_almacenado(conexion):
    registro = capturar_uno(conexion, paquete_de_varias_lecturas(2))
    guardado = outbox.leer_payload(conexion, registro.id_captura)
    vistas = []

    def manejador(peticion: httpx.Request) -> httpx.Response:
        vistas.append(
            (
                peticion.headers[CABECERA_IDEMPOTENCIA],
                peticion.content.decode("utf-8"),
                peticion.url.path,
            )
        )
        return respuesta_creada(reproducido=False, lecturas=2)(peticion)

    ejecutar_pasada(conexion, cliente_con(manejador))

    clave, cuerpo, ruta = vistas[0]
    assert clave == registro.clave
    assert cuerpo == guardado
    assert ruta == RUTA_SESIONES


def test_la_clave_y_el_cuerpo_no_cambian_entre_intentos(conexion):
    """La propiedad de la que depende que un reenvio sea replay."""
    capturar_uno(conexion, paquete_de_varias_lecturas(2))
    vistas = []

    def registrar(peticion: httpx.Request):
        vistas.append((peticion.headers[CABECERA_IDEMPOTENCIA], peticion.content))

    def falla(peticion):
        registrar(peticion)
        return httpx.Response(503, json={"detail": "x"})

    def logra(peticion):
        registrar(peticion)
        return respuesta_creada(reproducido=True, lecturas=2)(peticion)

    ejecutar_pasada(conexion, cliente_con(falla))
    ejecutar_pasada(conexion, cliente_con(falla))
    ejecutar_pasada(conexion, cliente_con(logra))

    assert len(vistas) == 3
    assert len({clave for clave, _ in vistas}) == 1
    assert len({cuerpo for _, cuerpo in vistas}) == 1


def test_una_sincronizacion_informada_no_cambia_entre_intentos(conexion):
    """El campo que participa de la huella viaja congelado, no recalculado.

    Es la razon por la que el edge puede aceptar un valor no nulo sin poner en
    riesgo la idempotencia: lo que importa no es que el campo este vacio, sino
    que el segundo intento envie exactamente el mismo instante que el primero.
    """
    capturar_uno(conexion, paquete_con_sincronizacion())
    sincronizaciones = []

    def registrar(peticion: httpx.Request):
        cuerpo = json.loads(peticion.content)
        sincronizaciones.append(cuerpo["lecturas"][0]["fecha_hora_sincronizacion"])

    def falla(peticion):
        registrar(peticion)
        return httpx.Response(503, json={"detail": "x"})

    def logra(peticion):
        registrar(peticion)
        return respuesta_creada(reproducido=True)(peticion)

    ejecutar_pasada(conexion, cliente_con(falla))
    ejecutar_pasada(conexion, cliente_con(falla))
    ejecutar_pasada(conexion, cliente_con(logra))

    assert len(sincronizaciones) == 3
    assert all(valor is not None for valor in sincronizaciones)
    assert len(set(sincronizaciones)) == 1


# ---------------------------------------------------------------------------
# 4. Seleccion y orden
# ---------------------------------------------------------------------------


def test_los_eventos_se_seleccionan_en_orden_determinista(conexion):
    claves = [capturar_uno(conexion).clave for _ in range(5)]
    elegibles = outbox.seleccionar_elegibles(conexion, limite=10)
    assert [evento.clave for evento in elegibles] == claves


def test_un_evento_enviado_no_vuelve_a_seleccionarse(conexion):
    capturar_uno(conexion)
    ejecutar_pasada(conexion, cliente_con(respuesta_creada(reproducido=False)))

    assert outbox.seleccionar_elegibles(conexion, limite=10) == ()


def test_un_fallido_en_revision_no_vuelve_a_seleccionarse(conexion):
    capturar_uno(conexion)
    ejecutar_pasada(conexion, cliente_con(respuesta_fija(422, {"detail": "x"})))
    assert outbox.seleccionar_elegibles(conexion, limite=10) == ()


def test_un_fallido_reintentable_si_vuelve_a_seleccionarse(conexion):
    """Sigue en la cola, y desde SCRUM-65 tambien se sabe *cuando*.

    Un fallo recuperable programa su proximo intento, asi que la seleccion que
    respeta esa fecha no lo devuelve todavia; la que la ignora --la del comando
    manual ``enviar``-- si. Las dos mitades se afirman aqui para que la politica
    no pueda desaparecer sin que una de ellas falle.
    """
    capturar_uno(conexion)
    ejecutar_pasada(conexion, cliente_con(respuesta_fija(503, {"detail": "x"})))

    assert outbox.seleccionar_elegibles(conexion, limite=10) == ()
    assert (
        len(
            outbox.seleccionar_elegibles(
                conexion, limite=10, respetar_programacion=False
            )
        )
        == 1
    )

    despues = outbox.ahora_utc() + timedelta(hours=1)
    assert len(outbox.seleccionar_elegibles(conexion, limite=10, ahora=despues)) == 1


def test_el_limite_acota_la_pasada(conexion):
    for _ in range(5):
        capturar_uno(conexion)
    resumen = ejecutar_pasada(
        conexion, cliente_con(respuesta_creada(reproducido=False)), limite=2
    )
    assert resumen.seleccionados == 2
    assert resumen.entregados == 2
    assert len(outbox.seleccionar_elegibles(conexion, limite=10)) == 3


def test_un_fallo_individual_no_impide_procesar_los_demas(conexion):
    registros = [capturar_uno(conexion) for _ in range(3)]
    segundo = registros[1].id_outbox

    def manejador(peticion: httpx.Request) -> httpx.Response:
        if peticion.headers[CABECERA_IDEMPOTENCIA] == registros[1].clave:
            return httpx.Response(409, json={"detail": "colision"})
        return respuesta_creada(reproducido=False)(peticion)

    resumen = ejecutar_pasada(conexion, cliente_con(manejador))

    assert resumen.entregados == 2
    assert resumen.rechazados == 1
    assert estado_de(conexion, segundo) == EstadoEntrega.FALLIDO.value
    for registro in (registros[0], registros[2]):
        assert estado_de(conexion, registro.id_outbox) == EstadoEntrega.ENVIADO.value


def test_la_pasada_se_detiene_ante_un_fallo_de_transporte(conexion):
    """La API no esta accesible: insistir con los demas seria esperar por nada."""
    for _ in range(4):
        capturar_uno(conexion)
    llamadas = []

    def manejador(peticion):
        llamadas.append(1)
        raise httpx.ConnectError("sin ruta", request=peticion)

    resumen = ejecutar_pasada(conexion, cliente_con(manejador))

    assert resumen.detenida_por_transporte is True
    assert len(llamadas) == 1
    assert resumen.seleccionados == 4


def test_un_5xx_no_detiene_la_pasada(conexion):
    """Un fallo del servidor es del paquete, no del enlace."""
    for _ in range(3):
        capturar_uno(conexion)
    resumen = ejecutar_pasada(conexion, cliente_con(respuesta_fija(500, {"detail": "x"})))

    assert resumen.detenida_por_transporte is False
    assert resumen.reintentables == 3


def test_la_pasada_termina_siempre_y_no_es_un_bucle(conexion):
    capturar_uno(conexion)
    for _ in range(3):
        ejecutar_pasada(conexion, cliente_con(respuesta_fija(503, {"detail": "x"})))
    fila = outbox.leer_evento(conexion, 1)
    assert fila["intentos"] == 3


# ---------------------------------------------------------------------------
# 5. Transiciones
# ---------------------------------------------------------------------------


def test_un_fallido_reintentable_llega_a_enviado_en_otra_pasada(conexion):
    registro = capturar_uno(conexion)
    ejecutar_pasada(conexion, cliente_con(respuesta_fija(503, {"detail": "x"})))
    assert estado_de(conexion, registro.id_outbox) == EstadoEntrega.FALLIDO.value

    ejecutar_pasada(conexion, cliente_con(respuesta_creada(reproducido=True)))
    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.ENVIADO.value
    assert fila["reintentable"] is None
    assert fila["ultimo_error"] is None


def test_un_fallido_que_vuelve_a_fallar_sigue_fallido(conexion):
    registro = capturar_uno(conexion)
    ejecutar_pasada(conexion, cliente_con(respuesta_fija(503, {"detail": "x"})))
    ejecutar_pasada(conexion, cliente_con(respuesta_fija(500, {"detail": "y"})))

    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.FALLIDO.value
    assert fila["intentos"] == 2


def test_un_evento_enviado_nunca_se_elimina(conexion):
    registro = capturar_uno(conexion)
    ejecutar_pasada(conexion, cliente_con(respuesta_creada(reproducido=False)))

    assert outbox.leer_evento(conexion, registro.id_outbox) is not None
    assert outbox.leer_payload(conexion, registro.id_captura) is not None


# ---------------------------------------------------------------------------
# 6. Concurrencia local: ENVIADO es terminal
# ---------------------------------------------------------------------------

INSTANTE = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


def test_secuencia_exito_y_luego_fallo_tardio_conserva_enviado(conexion):
    """Emisor A entrega y marca ENVIADO; el emisor B, retrasado, intenta FALLIDO.

    Es la carrera que ningun ``busy_timeout`` resuelve: no hay contencion de
    bloqueo, hay dos escritores y uno trabaja con informacion vieja. La guarda
    ``estado <> 'ENVIADO'`` del UPDATE es lo que impide que la verdad se pierda.
    """
    registro = capturar_uno(conexion)

    # Emisor A: entrega confirmada.
    ejecutar_pasada(conexion, cliente_con(respuesta_creada(reproducido=False)))
    assert estado_de(conexion, registro.id_outbox) == EstadoEntrega.ENVIADO.value

    # Emisor B: su intento termino en timeout y ahora quiere escribir FALLIDO.
    with alm.transaccion(conexion):
        aplicado = outbox.marcar_fallido(
            conexion,
            registro.id_outbox,
            reintentable=True,
            codigo_http=None,
            error="fallo de transporte: ReadTimeout",
            momento=INSTANTE,
        )

    assert aplicado is False
    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.ENVIADO.value
    assert fila["id_sesion_remota"] == 832
    assert fila["reintentable"] is None
    assert fila["ultimo_error"] is None


def test_secuencia_fallo_y_luego_exito_termina_en_enviado(conexion):
    """El orden inverso: primero un fallo, despues la entrega. Gana ENVIADO."""
    registro = capturar_uno(conexion)

    ejecutar_pasada(conexion, cliente_con(transporte_caido(httpx.ReadTimeout)))
    assert estado_de(conexion, registro.id_outbox) == EstadoEntrega.FALLIDO.value

    ejecutar_pasada(conexion, cliente_con(respuesta_creada(reproducido=True)))

    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.ENVIADO.value
    assert fila["reintentable"] is None
    assert fila["ultimo_error"] is None
    assert fila["enviado_en"] is not None


def test_una_pasada_sobre_un_evento_ya_entregado_lo_reporta_sin_degradarlo(conexion):
    """El emisor B corre una pasada completa con una seleccion vieja."""
    registro = capturar_uno(conexion)

    # B selecciona mientras todavia estaba PENDIENTE.
    seleccion_vieja = outbox.seleccionar_elegibles(conexion, limite=10)

    # A entrega.
    ejecutar_pasada(conexion, cliente_con(respuesta_creada(reproducido=False)))

    # B sigue adelante con su seleccion obsoleta y su intento fracasa.
    import app.edge.emisor as emisor_modulo

    original = emisor_modulo.outbox.seleccionar_elegibles
    try:
        emisor_modulo.outbox.seleccionar_elegibles = (
            lambda conexion_bd, **argumentos: seleccion_vieja
        )
        resumen = ejecutar_pasada(conexion, cliente_con(transporte_caido(httpx.ReadTimeout)))
    finally:
        emisor_modulo.outbox.seleccionar_elegibles = original

    # Desde SCRUM-65 ni siquiera se llega a la red: la reclamacion vuelve a
    # comprobar el predicado completo y no encuentra nada que reclamar.
    assert resumen.ya_entregados == 1
    assert resumen.reclamados == 0
    assert resumen.reintentables == 0
    assert estado_de(conexion, registro.id_outbox) == EstadoEntrega.ENVIADO.value


def test_un_segundo_exito_tampoco_reescribe_un_enviado(conexion):
    """Dos entregas confirmadas del mismo evento: la primera es la que queda."""
    registro = capturar_uno(conexion)
    ejecutar_pasada(conexion, cliente_con(respuesta_creada(reproducido=False, id_sesion=832)))

    with alm.transaccion(conexion):
        aplicado = outbox.marcar_enviado(
            conexion,
            registro.id_outbox,
            id_sesion=999,
            ids_lectura=(1,),
            codigo_http=201,
            momento=INSTANTE,
        )

    assert aplicado is False
    assert outbox.leer_evento(conexion, registro.id_outbox)["id_sesion_remota"] == 832


def test_los_intentos_se_incrementan_en_sql_y_no_se_pierden(conexion):
    registro = capturar_uno(conexion)
    for _ in range(4):
        ejecutar_pasada(conexion, cliente_con(respuesta_fija(503, {"detail": "x"})))
    assert outbox.leer_evento(conexion, registro.id_outbox)["intentos"] == 4


# ---------------------------------------------------------------------------
# 7. Saneamiento
# ---------------------------------------------------------------------------


def test_un_422_estructurado_no_guarda_el_valor_rechazado(conexion):
    """FastAPI incluye ``input`` en cada entrada, y ese valor es el dato clinico."""
    registro = capturar_uno(conexion)
    detalle = [
        {
            "type": "decimal_max_places",
            "loc": ["body", "lecturas", 0, "hr_valor"],
            "msg": "Decimal input should have no more than 2 decimal places",
            "input": 123.456789,
        }
    ]
    ejecutar_pasada(conexion, cliente_con(respuesta_fija(422, {"detail": detalle})))

    guardado = outbox.leer_evento(conexion, registro.id_outbox)["ultimo_error"]
    assert "123.456789" not in guardado
    assert "hr_valor" not in guardado
    assert "1 entrada" in guardado


def test_el_error_guardado_no_contiene_la_url_ni_el_paquete(conexion):
    registro = capturar_uno(conexion)
    ejecutar_pasada(conexion, cliente_con(transporte_caido(httpx.ConnectError)))

    guardado = outbox.leer_evento(conexion, registro.id_outbox)["ultimo_error"]
    assert BASE not in guardado
    assert "nodo-edge.invalid" not in guardado
    for valor in ("id_embarazo", "hr_valor", "SIGNOS_MATERNOS"):
        assert valor not in guardado


def test_el_error_guardado_se_trunca(conexion):
    registro = capturar_uno(conexion)
    ejecutar_pasada(
        conexion, cliente_con(respuesta_fija(500, {"detail": "x" * 5000}))
    )

    guardado = outbox.leer_evento(conexion, registro.id_outbox)["ultimo_error"]
    assert len(guardado) <= outbox.LONGITUD_MAXIMA_DE_ERROR


def test_el_resumen_no_expone_claves_ni_paquetes(conexion):
    for _ in range(3):
        capturar_uno(conexion)
    resumen = outbox.resumen(conexion)

    texto = repr(resumen)
    assert "clave" not in texto
    assert "hr_valor" not in texto
    assert resumen.pendientes == 3
    assert resumen.total == 3


def test_el_resumen_separa_los_fallidos_por_reintentabilidad(conexion):
    capturar_uno(conexion)
    capturar_uno(conexion)
    ejecutar_pasada(
        conexion,
        cliente_con(respuesta_fija(409, {"detail": "colision"})),
        limite=1,
    )
    ejecutar_pasada(conexion, cliente_con(respuesta_fija(503, {"detail": "x"})), limite=1)

    resumen = outbox.resumen(conexion)
    assert resumen.fallidos_en_revision == 1
    assert resumen.fallidos_reintentables == 1


# ---------------------------------------------------------------------------
# 8. Aislamiento entre pruebas
# ---------------------------------------------------------------------------


def test_cada_prueba_usa_su_propio_archivo(conexion, tmp_path):
    capturar_uno(conexion)
    archivos = list(tmp_path.glob("*.sqlite3"))
    assert len(archivos) == 1
    assert archivos[0].parent == tmp_path


def test_ninguna_transaccion_queda_abierta_tras_una_pasada(conexion):
    capturar_uno(conexion)
    ejecutar_pasada(conexion, cliente_con(respuesta_creada(reproducido=False)))
    assert conexion.in_transaction is False


def test_no_se_mantiene_un_bloqueo_durante_la_llamada_de_red(conexion, tmp_path):
    """Otra conexion puede escribir mientras el emisor esta en la red."""
    capturar_uno(conexion)
    ruta = tmp_path / "nodo_edge.sqlite3"
    escrituras = []

    def manejador(peticion: httpx.Request) -> httpx.Response:
        # En pleno "viaje por la red", una segunda conexion escribe.
        #
        # La espera es holgada a proposito. Si el emisor mantuviera el bloqueo
        # durante la llamada, esta escritura no podria completarse por mucho que
        # esperara --el emisor no avanza hasta que el manejador retorne--, asi
        # que un margen amplio no debilita la prueba; solo evita que se vuelva
        # inestable cuando la maquina va cargada.
        with alm.conectar(ruta, espera_de_bloqueo_ms=5000) as otra:
            with alm.transaccion(otra):
                otra.execute(
                    "INSERT INTO captura_local (payload_json, capturado_en)"
                    " VALUES ('{}', 'ahora')"
                )
            escrituras.append(1)
        return respuesta_creada(reproducido=False)(peticion)

    ejecutar_pasada(conexion, cliente_con(manejador))
    assert escrituras == [1]
