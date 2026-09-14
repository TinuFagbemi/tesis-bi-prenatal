"""El preflight de credencial y el 401/403 a mitad de vuelo (SCRUM-70).

**Por que existe el preflight.** ``outbox.reclamar_intento`` incrementa
``intentos`` en la misma sentencia que reclama el intento --antes de enviar
nada-- y la elegibilidad exige ``intentos < max_intentos_aplicado``. Sin una
comprobacion previa, una ejecucion con la credencial equivocada gastaria un
intento de **cada** paquete de la cola solo para descubrir un 401, y repetirla
agotaria el presupuesto de paquetes que nunca estuvieron mal. Ese es el defecto
que estas pruebas vigilan.

**Que ocurre si el token expira entre el preflight y el POST.** La ventana es de
segundos, pero existe. El evento queda ``FALLIDO`` y **reintentable**, con su
clave y su paquete intactos y sin programacion, y la corrida se detiene para no
gastar el presupuesto del resto. Si ese POST cae justo en el ultimo intento del
presupuesto, se aplica la regla de agotamiento de siempre: saltarsela seria subir
el limite en silencio.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import httpx
import pytest

from app.edge import almacenamiento as alm
from app.edge import outbox
from app.edge.cliente import (
    CABECERA_AUTORIZACION,
    CABECERA_REPLAY,
    ROL_REQUERIDO,
    RUTA_IDENTIDAD,
    ClienteEdge,
    clasificar,
    es_rechazo_de_credencial,
    es_resultado_desconocido,
)
from app.edge.emisor import ejecutar_pasada
from app.edge.estados import EstadoEntrega, MotivoRevision, ResultadoEntrega
from app.edge.politica import PoliticaDeReintentos
from tests.test_edge_captura import PAQUETE_DE_UNA_LECTURA
from tests.test_edge_emisor import capturar_uno, respuesta_creada

BASE = "http://nodo.invalid"


def cliente_con(manejador) -> ClienteEdge:
    return ClienteEdge(
        httpx.Client(transport=httpx.MockTransport(manejador), base_url=BASE)
    )


def identidad(cuerpo, codigo: int = 200):
    def manejador(peticion: httpx.Request) -> httpx.Response:
        return httpx.Response(codigo, json=cuerpo)

    return manejador


@pytest.fixture
def conexion(tmp_path):
    with alm.conectar(tmp_path / "nodo_edge.sqlite3") as conexion:
        alm.inicializar(conexion)
        yield conexion


# ---------------------------------------------------------------------------
# 1. verificar_credencial: un solo caso es exito
# ---------------------------------------------------------------------------


def test_una_identidad_paciente_es_el_unico_exito():
    resultado = cliente_con(identidad({"id_usuario": 130, "rol": ROL_REQUERIDO})).verificar_credencial()

    assert resultado.valido is True
    assert resultado.rol == ROL_REQUERIDO
    assert resultado.codigo_http == 200


@pytest.mark.parametrize("rol", ["ADMIN", "MEDICO"])
def test_un_rol_que_no_puede_ingerir_aborta(rol):
    resultado = cliente_con(identidad({"id_usuario": 1, "rol": rol})).verificar_credencial()

    assert resultado.valido is False
    assert resultado.rol == rol
    assert ROL_REQUERIDO in resultado.motivo


@pytest.mark.parametrize("codigo", [401, 403])
def test_una_credencial_rechazada_aborta(codigo):
    resultado = cliente_con(identidad({"detail": "x"}, codigo)).verificar_credencial()

    assert resultado.valido is False
    assert resultado.codigo_http == codigo


@pytest.mark.parametrize("codigo", [500, 502, 503, 301, 302, 202, 204, 418])
def test_cualquier_otra_respuesta_aborta(codigo):
    """5xx, 3xx y un 2xx inesperado: ninguno autoriza empezar."""
    resultado = cliente_con(identidad({}, codigo)).verificar_credencial()

    assert resultado.valido is False


def test_un_cuerpo_que_no_cumple_el_contrato_aborta():
    for cuerpo in ({}, {"rol": "PACIENTE"}, {"id_usuario": "x", "rol": "PACIENTE"}, []):
        resultado = cliente_con(identidad(cuerpo)).verificar_credencial()
        assert resultado.valido is False


def test_un_cuerpo_que_no_es_json_aborta():
    def manejador(peticion):
        return httpx.Response(200, content=b"<html>no soy json</html>")

    assert cliente_con(manejador).verificar_credencial().valido is False


def test_un_fallo_de_transporte_aborta():
    def manejador(peticion):
        raise httpx.ConnectError("sin ruta al host")

    resultado = cliente_con(manejador).verificar_credencial()

    assert resultado.valido is False
    assert "ConnectError" in resultado.motivo


def test_el_motivo_nunca_repite_texto_del_servidor():
    """El mensaje remoto no viaja a la salida del comando."""
    delator = "el-usuario-admin01-no-existe"
    resultado = cliente_con(identidad({"detail": delator}, 401)).verificar_credencial()

    assert delator not in resultado.motivo


def test_el_preflight_consulta_la_ruta_de_identidad():
    vistas = []

    def manejador(peticion):
        vistas.append(peticion.url.path)
        return httpx.Response(200, json={"id_usuario": 1, "rol": ROL_REQUERIDO})

    cliente_con(manejador).verificar_credencial()

    assert vistas == [RUTA_IDENTIDAD]


def test_el_preflight_no_toca_la_outbox(conexion):
    """No abre la base ni reclama nada: es solo una peticion."""
    capturar_uno(conexion)
    antes = outbox.censar(conexion, max_attempts=5, momento=outbox.ahora_utc())

    cliente_con(identidad({"detail": "x"}, 401)).verificar_credencial()

    despues = outbox.censar(conexion, max_attempts=5, momento=outbox.ahora_utc())
    assert despues.elegibles_ahora == antes.elegibles_ahora


# ---------------------------------------------------------------------------
# 2. La credencial viaja en la cabecera del cliente, no en el paquete
# ---------------------------------------------------------------------------


def test_la_cabecera_por_omision_llega_en_la_peticion(conexion):
    """Asi es como el CLI inyecta el token: sin tocar ``enviar``."""
    vistas = {}

    def manejador(peticion):
        vistas["auth"] = peticion.headers.get(CABECERA_AUTORIZACION)
        return httpx.Response(
            201,
            json={"id_sesion": 1, "lecturas_creadas": 1, "ids_lectura": [2]},
            headers={CABECERA_REPLAY: "false"},
        )

    http = httpx.Client(
        transport=httpx.MockTransport(manejador),
        base_url=BASE,
        headers={CABECERA_AUTORIZACION: "Bearer token-simulado"},
    )
    id_outbox = capturar_uno(conexion).id_outbox
    ejecutar_pasada(conexion, ClienteEdge(http), politica=PoliticaDeReintentos())

    assert vistas["auth"] == "Bearer token-simulado"
    assert outbox.leer_evento(conexion, id_outbox)["estado"] == EstadoEntrega.ENVIADO.value


def test_el_token_no_entra_en_sqlite(conexion, tmp_path):
    """Ni en el paquete, ni en la outbox, ni en el historial de intentos."""
    token = "token-que-no-debe-persistirse-jamas"

    def manejador(peticion):
        return httpx.Response(401, json={"detail": "no"})

    http = httpx.Client(
        transport=httpx.MockTransport(manejador),
        base_url=BASE,
        headers={CABECERA_AUTORIZACION: f"Bearer {token}"},
    )
    capturar_uno(conexion)
    ejecutar_pasada(conexion, ClienteEdge(http), politica=PoliticaDeReintentos())
    conexion.commit()

    crudo = (tmp_path / "nodo_edge.sqlite3").read_bytes()

    assert token.encode() not in crudo
    assert b"Bearer" not in crudo
    assert b"Authorization" not in crudo


def test_el_token_no_cambia_la_clave_de_idempotencia(conexion):
    """El paquete y su huella no dependen de quien lo envie."""
    id_outbox = capturar_uno(conexion).id_outbox
    clave_antes = outbox.leer_evento(conexion, id_outbox)["clave_idempotencia"]

    http = httpx.Client(
        transport=httpx.MockTransport(lambda p: httpx.Response(401, json={})),
        base_url=BASE,
        headers={CABECERA_AUTORIZACION: "Bearer otro-token-distinto"},
    )
    ejecutar_pasada(conexion, ClienteEdge(http), politica=PoliticaDeReintentos())

    assert outbox.leer_evento(conexion, id_outbox)["clave_idempotencia"] == clave_antes


# ---------------------------------------------------------------------------
# 3. Clasificacion de 401 y 403
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("codigo", [401, 403])
def test_401_y_403_son_reintentables_y_lo_declaran(codigo):
    entrega = clasificar(httpx.Response(codigo, json={"detail": "x"}), lecturas_enviadas=1)

    assert entrega.resultado is ResultadoEntrega.REINTENTABLE
    assert entrega.requiere_credencial is True
    assert es_rechazo_de_credencial(entrega) is True


@pytest.mark.parametrize("codigo", [401, 403])
def test_no_se_confunden_con_un_resultado_desconocido(codigo):
    """Un fallo de transporte no lleva codigo; estos si."""
    entrega = clasificar(httpx.Response(codigo, json={}), lecturas_enviadas=1)

    assert es_resultado_desconocido(entrega) is False
    assert entrega.codigo_http == codigo


@pytest.mark.parametrize("codigo", [400, 404, 409, 422, 408, 429])
def test_los_demas_4xx_conservan_su_comportamiento(codigo):
    """408 y 429 siguen siendo permanentes, como decidio SCRUM-65."""
    entrega = clasificar(httpx.Response(codigo, json={"detail": "x"}), lecturas_enviadas=1)

    assert entrega.resultado is ResultadoEntrega.RECHAZADO
    assert entrega.requiere_credencial is False


@pytest.mark.parametrize("codigo", [500, 502, 503, 599])
def test_los_5xx_conservan_su_comportamiento(codigo):
    entrega = clasificar(httpx.Response(codigo, json={"detail": "x"}), lecturas_enviadas=1)

    assert entrega.resultado is ResultadoEntrega.REINTENTABLE
    assert entrega.requiere_credencial is False


@pytest.mark.parametrize("codigo", [401, 403])
def test_el_mensaje_es_un_literal_local(codigo):
    delator = "el-embarazo-99-no-existe"
    entrega = clasificar(httpx.Response(codigo, json={"detail": delator}), lecturas_enviadas=1)

    assert entrega.error == f"autenticacion rechazada {codigo}"
    assert delator not in entrega.error


# ---------------------------------------------------------------------------
# 4. 401 a mitad de vuelo: el evento se conserva y la corrida se detiene
# ---------------------------------------------------------------------------


def rechazo_de_credencial(codigo: int = 401):
    return lambda peticion: httpx.Response(codigo, json={"detail": "no"})


@pytest.mark.parametrize("codigo", [401, 403])
def test_el_evento_queda_reintentable_y_sin_programacion(conexion, codigo):
    id_outbox = capturar_uno(conexion).id_outbox

    ejecutar_pasada(
        conexion, cliente_con(rechazo_de_credencial(codigo)), politica=PoliticaDeReintentos()
    )

    fila = outbox.leer_evento(conexion, id_outbox)
    assert fila["estado"] == EstadoEntrega.FALLIDO.value
    assert fila["reintentable"] == 1
    assert fila["proximo_intento_en"] is None
    assert fila["motivo_revision"] is None
    assert fila["ultimo_http"] == codigo


def test_no_se_programa_ninguna_demora_ficticia(conexion):
    """Esperar no arregla una credencial, asi que no se anota una espera."""
    id_outbox = capturar_uno(conexion).id_outbox

    ejecutar_pasada(
        conexion, cliente_con(rechazo_de_credencial()), politica=PoliticaDeReintentos()
    )

    traza = outbox.leer_traza(conexion, id_outbox=id_outbox)
    assert traza.intentos_registrados
    assert all(
        intento.demora_programada_s is None for intento in traza.intentos_registrados
    )
    assert traza.proximo_intento_en is None


def test_el_paquete_y_la_clave_quedan_intactos(conexion):
    id_outbox = capturar_uno(conexion).id_outbox
    antes = outbox.leer_evento(conexion, id_outbox)
    payload_antes = outbox.leer_payload(conexion, antes["id_captura"])

    ejecutar_pasada(
        conexion, cliente_con(rechazo_de_credencial()), politica=PoliticaDeReintentos()
    )

    despues = outbox.leer_evento(conexion, id_outbox)
    assert despues["clave_idempotencia"] == antes["clave_idempotencia"]
    assert outbox.leer_payload(conexion, despues["id_captura"]) == payload_antes


def test_el_evento_sigue_siendo_elegible_de_inmediato(conexion):
    """Corregida la credencial, la siguiente ejecucion lo recoge sin esperar."""
    capturar_uno(conexion)

    ejecutar_pasada(
        conexion, cliente_con(rechazo_de_credencial()), politica=PoliticaDeReintentos()
    )

    censo = outbox.censar(conexion, max_attempts=5, momento=outbox.ahora_utc())
    assert censo.elegibles_ahora == 1
    assert censo.requieren_revision == 0


def test_la_corrida_se_detiene_al_primer_rechazo(conexion):
    """Solo el evento ya reclamado paga el descubrimiento. El resto, ninguno."""
    for _ in range(4):
        capturar_uno(conexion)

    pasada = ejecutar_pasada(
        conexion, cliente_con(rechazo_de_credencial()), politica=PoliticaDeReintentos()
    )

    assert pasada.detenida_por_credencial is True
    assert pasada.reclamados == 1
    assert pasada.pausa_hasta is None


def test_los_demas_eventos_conservan_su_presupuesto(conexion):
    ids = [capturar_uno(conexion).id_outbox for _ in range(4)]

    ejecutar_pasada(
        conexion, cliente_con(rechazo_de_credencial()), politica=PoliticaDeReintentos()
    )

    intentos = [outbox.leer_evento(conexion, i)["intentos"] for i in ids]
    assert sorted(intentos) == [0, 0, 0, 1]


def test_tras_corregir_la_credencial_se_entrega_sin_duplicar(conexion):
    """El ciclo completo: rechazo, correccion y entrega con la misma clave."""
    id_outbox = capturar_uno(conexion).id_outbox
    clave = outbox.leer_evento(conexion, id_outbox)["clave_idempotencia"]

    ejecutar_pasada(
        conexion, cliente_con(rechazo_de_credencial()), politica=PoliticaDeReintentos()
    )

    claves_enviadas = []

    def con_buena_credencial(peticion):
        claves_enviadas.append(peticion.headers.get("Idempotency-Key"))
        return respuesta_creada(reproducido=False)(peticion)

    ejecutar_pasada(
        conexion, cliente_con(con_buena_credencial), politica=PoliticaDeReintentos()
    )

    fila = outbox.leer_evento(conexion, id_outbox)
    assert fila["estado"] == EstadoEntrega.ENVIADO.value
    assert claves_enviadas == [clave]


def test_el_ultimo_intento_del_presupuesto_se_agota_como_siempre(conexion):
    """No se salta el limite por ser un fallo de credencial.

    Con ``max_attempts=1`` el primer intento es tambien el ultimo. La regla de
    agotamiento se aplica igual que a cualquier otro resultado recuperable:
    pasarla por alto subiria el limite en silencio, que es exactamente lo que no
    se autoriza. El evento no se pierde --clave y paquete siguen ahi-- y su
    ``ultimo_http`` dice que fue un 401.
    """
    id_outbox = capturar_uno(conexion).id_outbox
    politica = PoliticaDeReintentos(max_attempts=1)

    ejecutar_pasada(conexion, cliente_con(rechazo_de_credencial()), politica=politica)

    fila = outbox.leer_evento(conexion, id_outbox)
    assert fila["estado"] == EstadoEntrega.FALLIDO.value
    assert fila["reintentable"] == 0
    assert fila["motivo_revision"] == MotivoRevision.AGOTAMIENTO.value
    assert fila["ultimo_http"] == 401
    assert fila["clave_idempotencia"]


def test_con_presupuesto_restante_no_se_agota(conexion):
    """El contraste del caso anterior: si quedan intentos, sigue reintentable."""
    id_outbox = capturar_uno(conexion).id_outbox

    ejecutar_pasada(
        conexion,
        cliente_con(rechazo_de_credencial()),
        politica=PoliticaDeReintentos(max_attempts=3),
    )

    fila = outbox.leer_evento(conexion, id_outbox)
    assert fila["reintentable"] == 1
    assert fila["motivo_revision"] is None


# ---------------------------------------------------------------------------
# 5. La captura sin conexion no sabe nada de credenciales
# ---------------------------------------------------------------------------


def test_capturar_no_necesita_token_ni_api(tmp_path):
    """La operacion que debe funcionar sin cuenta, sin API y sin conectividad."""
    from app.edge.captura import capturar

    with alm.conectar(tmp_path / "offline.sqlite3") as conexion:
        alm.inicializar(conexion)
        resultado = capturar(conexion, PAQUETE_DE_UNA_LECTURA)

        assert resultado.id_outbox
        fila = outbox.leer_evento(conexion, resultado.id_outbox)
        assert fila["estado"] == EstadoEntrega.PENDIENTE.value


def test_el_modulo_de_captura_no_conoce_la_autorizacion():
    """Propiedad estructural: la autenticacion es inalcanzable desde el camino offline."""
    from pathlib import Path

    from app.edge import captura

    fuente = Path(captura.__file__).read_text(encoding="utf-8")

    assert "httpx" not in fuente
    assert "Authorization" not in fuente
    assert "api_token" not in fuente


def test_la_configuracion_del_edge_no_admite_otras_credenciales():
    """Un solo campo de credencial, y solo para la red."""
    from app.edge.config import EdgeSettings

    campos = set(EdgeSettings.model_fields)

    assert "api_token" in campos
    assert not {"password", "database_url", "jwt_secret_key"} & campos


def test_el_token_del_edge_esta_enmascarado():
    from app.edge.config import EdgeSettings

    settings = EdgeSettings(api_token="token-que-no-debe-imprimirse")

    for texto in (repr(settings), str(settings)):
        assert "token-que-no-debe-imprimirse" not in texto
