"""El cliente HTTP del adaptador contra la API central (SCRUM-72).

Cierra un hueco concreto: las demas suites de la interfaz sustituyen el cliente
central por un doble, asi que la capa que de verdad habla HTTP --como se arma la
URL, donde va la cabecera, como se interpreta cada codigo, como se valida el
cuerpo-- no se ejercitaba en ninguna parte.

**Se prueba con ``httpx.MockTransport``**, que es el mecanismo que la propia
biblioteca ya instalada ofrece para esto: un transporte falso recibe la
``httpx.Request`` real que el cliente construyo y devuelve la
``httpx.Response`` que el caso necesita. Nada de red, nada de servidor, ninguna
dependencia nueva. Lo que se observa es la peticion autentica, no una
reconstruccion de ella.

**Ninguna prueba persiste nada.** Los tokens y las contrasenas de este modulo
son ficticios, viven en la memoria del caso y no tocan disco.

Todas las cuentas y los datos son ficticios y simulados.
"""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from app.gestante.central import (
    CABECERA_AUTORIZACION,
    RUTA_EMBARAZOS,
    RUTA_SALUD,
    RUTA_TOKEN,
    ClienteCentralHTTP,
    EstadoRespuesta,
    ruta_de_lecturas,
    ruta_de_sesiones,
)
from app.edge.cliente import RUTA_IDENTIDAD
from app.models.enums import NombreRol

BASE = "http://servidor-de-prueba.invalid"

TOKEN = "token-ficticio-de-la-suite-abcdefghijklmnop"
PASSWORD = "contrasena-ficticia-de-la-suite"
EMAIL = "paciente-de-prueba@example.com"

# Filas de ejemplo, con exactamente los campos de los contratos de SCRUM-98.
EMBARAZO = {
    "id_embarazo": 101,
    "fecha_inicio": "2026-01-06",
    "fecha_probable_parto": "2026-10-13",
    "estado_embarazo": "ACTIVO",
    "fecha_cierre": None,
}
SESION = {
    "id_sesion": 501,
    "tipo_sesion": "SIGNOS_MATERNOS",
    "estado_sesion": "COMPLETADA",
    "fecha_inicio": "2026-06-01T10:00:00+00:00",
    "fecha_fin": "2026-06-01T10:30:00+00:00",
}
LECTURA_SIGNOS = {
    "id_lectura": 9001,
    "fecha_hora_captura": "2026-06-01T10:15:00+00:00",
    "codigo_semaforo": "OK",
    "semana_gestacion": 24,
    "hr_valor": "78.00",
    "spo2_valor": "98.00",
    "mov_valor": None,
}
LECTURA_MOVIMIENTO = {
    "id_lectura": 9002,
    "fecha_hora_captura": "2026-06-02T09:00:00+00:00",
    "codigo_semaforo": "WARNING",
    "semana_gestacion": 24,
    "hr_valor": None,
    "spo2_valor": None,
    "mov_valor": 7,
}


class Grabadora:
    """Transporte falso que anota la peticion y devuelve lo que el caso diga."""

    def __init__(self, responder) -> None:
        self.responder = responder
        self.peticiones: list[httpx.Request] = []

    def __call__(self, peticion: httpx.Request) -> httpx.Response:
        self.peticiones.append(peticion)
        return self.responder(peticion)

    @property
    def ultima(self) -> httpx.Request:
        assert self.peticiones, "no se envio ninguna peticion"
        return self.peticiones[-1]


def construir(responder) -> tuple[ClienteCentralHTTP, Grabadora]:
    """Un cliente real sobre un transporte falso."""
    grabadora = Grabadora(responder)
    http = httpx.Client(base_url=BASE, transport=httpx.MockTransport(grabadora))
    return ClienteCentralHTTP(http), grabadora


def responde(estado: int, cuerpo=None, *, texto: str | None = None):
    """Un respondedor que siempre contesta lo mismo."""

    def responder(_peticion: httpx.Request) -> httpx.Response:
        if texto is not None:
            return httpx.Response(estado, text=texto)
        return httpx.Response(estado, json=cuerpo)

    return responder


def falla_transporte(excepcion: Exception):
    def responder(_peticion: httpx.Request) -> httpx.Response:
        raise excepcion

    return responder


# ---------------------------------------------------------------------------
# URL y metodo de las tres rutas clinicas
# ---------------------------------------------------------------------------


def test_embarazos_usa_la_ruta_y_el_metodo_del_contrato():
    cliente, grabadora = construir(responde(200, [EMBARAZO]))

    cliente.embarazos(TOKEN)

    assert grabadora.ultima.method == "GET"
    assert grabadora.ultima.url.path == "/api/v1/clinico/embarazos"


def test_sesiones_usa_la_ruta_del_contrato_con_el_embarazo_en_el_camino():
    cliente, grabadora = construir(responde(200, [SESION]))

    cliente.sesiones(TOKEN, 101)

    assert grabadora.ultima.method == "GET"
    assert grabadora.ultima.url.path == "/api/v1/clinico/embarazos/101/sesiones"


def test_lecturas_usa_la_ruta_del_contrato_con_la_sesion_en_el_camino():
    cliente, grabadora = construir(responde(200, [LECTURA_SIGNOS]))

    cliente.lecturas(TOKEN, 501)

    assert grabadora.ultima.method == "GET"
    assert grabadora.ultima.url.path == "/api/v1/clinico/sesiones/501/lecturas"


@pytest.mark.parametrize(
    "constructor, esperado",
    [
        (lambda: ruta_de_sesiones(101), "/api/v1/clinico/embarazos/101/sesiones"),
        (lambda: ruta_de_lecturas(501), "/api/v1/clinico/sesiones/501/lecturas"),
    ],
)
def test_los_constructores_de_ruta_producen_el_camino_publicado(constructor, esperado):
    assert constructor() == esperado


def test_un_identificador_no_entero_no_acaba_interpolado_en_la_url():
    with pytest.raises((TypeError, ValueError)):
        ruta_de_sesiones("101 OR 1=1")


# ---------------------------------------------------------------------------
# La credencial viaja en el servidor, y solo ahi
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "llamada",
    [
        lambda c: c.embarazos(TOKEN),
        lambda c: c.sesiones(TOKEN, 101),
        lambda c: c.lecturas(TOKEN, 501),
    ],
)
def test_toda_lectura_clinica_presenta_el_token_como_bearer(llamada):
    cliente, grabadora = construir(responde(200, []))

    llamada(cliente)

    assert grabadora.ultima.headers[CABECERA_AUTORIZACION] == f"Bearer {TOKEN}"


def test_la_consulta_de_salud_no_lleva_credencial():
    """``/health`` es publico: mandar un token seria exponerlo sin necesidad."""
    cliente, grabadora = construir(responde(200, {"status": "ok"}))

    cliente.disponible()

    assert grabadora.ultima.url.path == RUTA_SALUD
    assert CABECERA_AUTORIZACION not in grabadora.ultima.headers


def test_el_login_envia_las_credenciales_una_vez_y_en_el_cuerpo():
    cliente, grabadora = construir(
        responde(200, {"access_token": TOKEN, "token_type": "bearer", "expires_in": 1800})
    )

    resultado = cliente.autenticar(email=EMAIL, password=PASSWORD)

    peticion = grabadora.ultima
    assert peticion.method == "POST"
    assert peticion.url.path == RUTA_TOKEN
    assert json.loads(peticion.content) == {"email": EMAIL, "password": PASSWORD}
    # La contrasena no viaja en la URL ni en una cabecera.
    assert PASSWORD not in str(peticion.url)
    assert not any(PASSWORD in valor for valor in peticion.headers.values())
    assert resultado.token == TOKEN


def test_el_cliente_no_conserva_la_credencial_despues_de_usarla():
    """No hay atributo donde quede: el token se devuelve y se olvida."""
    cliente, _ = construir(
        responde(200, {"access_token": TOKEN, "token_type": "bearer", "expires_in": 1800})
    )

    cliente.autenticar(email=EMAIL, password=PASSWORD)

    interno = json.dumps(
        {k: str(v) for k, v in vars(cliente).items()}, ensure_ascii=False
    )
    assert PASSWORD not in interno
    assert TOKEN not in interno


# ---------------------------------------------------------------------------
# Parseo correcto
# ---------------------------------------------------------------------------


def test_los_embarazos_se_validan_con_el_contrato_de_scrum_98():
    cliente, _ = construir(responde(200, [EMBARAZO]))

    resultado = cliente.embarazos(TOKEN)

    assert resultado.estado is EstadoRespuesta.OK
    assert resultado.disponible is True
    assert len(resultado.datos) == 1
    embarazo = resultado.datos[0]
    assert embarazo.id_embarazo == 101
    assert embarazo.estado_embarazo == "ACTIVO"
    assert embarazo.fecha_cierre is None
    assert embarazo.fecha_inicio.isoformat() == "2026-01-06"


def test_las_sesiones_se_validan_con_el_contrato():
    cliente, _ = construir(responde(200, [SESION]))

    resultado = cliente.sesiones(TOKEN, 101)

    assert resultado.estado is EstadoRespuesta.OK
    assert resultado.datos[0].id_sesion == 501
    assert resultado.datos[0].tipo_sesion == "SIGNOS_MATERNOS"


def test_las_lecturas_se_validan_y_conservan_el_semaforo_del_servidor():
    cliente, _ = construir(responde(200, [LECTURA_SIGNOS]))

    lectura = cliente.lecturas(TOKEN, 501).datos[0]

    assert lectura.codigo_semaforo == "OK"
    assert lectura.semana_gestacion == 24
    assert lectura.hr_valor == Decimal("78.00")
    assert lectura.spo2_valor == Decimal("98.00")


def test_una_lista_vacia_es_una_respuesta_valida_y_no_un_fallo():
    """Distinta de «no hay conexion»: el servidor contesto, y no hay filas."""
    cliente, _ = construir(responde(200, []))

    resultado = cliente.embarazos(TOKEN)

    assert resultado.estado is EstadoRespuesta.OK
    assert resultado.disponible is True
    assert resultado.datos == ()


# ---------------------------------------------------------------------------
# Los valores biometricos nulos se conservan
# ---------------------------------------------------------------------------


def test_una_lectura_de_movimiento_conserva_hr_y_spo2_en_nulo():
    """El nulo tiene que llegar como nulo: no es cero, y no se rellena."""
    cliente, _ = construir(responde(200, [LECTURA_MOVIMIENTO]))

    lectura = cliente.lecturas(TOKEN, 501).datos[0]

    assert lectura.mov_valor == 7
    assert lectura.hr_valor is None
    assert lectura.spo2_valor is None


def test_una_lectura_de_signos_conserva_movimiento_en_nulo():
    cliente, _ = construir(responde(200, [LECTURA_SIGNOS]))

    lectura = cliente.lecturas(TOKEN, 501).datos[0]

    assert lectura.mov_valor is None
    assert lectura.hr_valor is not None


def test_un_cero_de_movimiento_se_conserva_como_cero():
    """Cero movimientos es un dato; ausencia de dato es otra cosa."""
    cliente, _ = construir(responde(200, [{**LECTURA_MOVIMIENTO, "mov_valor": 0}]))

    lectura = cliente.lecturas(TOKEN, 501).datos[0]

    assert lectura.mov_valor == 0
    assert lectura.mov_valor is not None


# ---------------------------------------------------------------------------
# Red caida y tiempo agotado
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "excepcion",
    [
        httpx.ConnectError("sin ruta al servidor"),
        httpx.ReadTimeout("agotado"),
        httpx.ConnectTimeout("agotado al conectar"),
    ],
)
@pytest.mark.parametrize(
    "llamada",
    [
        lambda c: c.embarazos(TOKEN),
        lambda c: c.sesiones(TOKEN, 101),
        lambda c: c.lecturas(TOKEN, 501),
    ],
)
def test_un_fallo_de_transporte_es_sin_conexion(excepcion, llamada):
    """Nunca se confunde con un rechazo: no dice nada sobre la credencial."""
    cliente, _ = construir(falla_transporte(excepcion))

    resultado = llamada(cliente)

    assert resultado.estado is EstadoRespuesta.SIN_CONEXION
    assert resultado.disponible is False
    assert resultado.datos == ()


def test_la_salud_es_falsa_cuando_no_hay_red():
    cliente, _ = construir(falla_transporte(httpx.ConnectError("caido")))

    assert cliente.disponible() is False


# ---------------------------------------------------------------------------
# Codigos del servidor
# ---------------------------------------------------------------------------


def test_401_central_es_rechazo_de_credencial():
    """El token ya no sirve. Lleva a pedir autenticacion, no a otra cosa."""
    cliente, _ = construir(responde(401, {"detail": "no"}))

    resultado = cliente.embarazos(TOKEN)

    assert resultado.estado is EstadoRespuesta.RECHAZADO
    assert resultado.datos == ()


def test_403_central_no_se_confunde_con_un_401():
    """Son cosas distintas y el adaptador las mantiene separadas.

    Un 401 dice «vuelve a autenticarte»; un 403 dice «tu cuenta no puede hacer
    esto». Tratarlos igual invitaria a reintentar unas credenciales que
    funcionan perfectamente, y ocultaria una incoherencia real: en este portal
    solo entra PACIENTE.
    """
    cliente, _ = construir(responde(403, {"detail": "no"}))

    resultado = cliente.embarazos(TOKEN)

    assert resultado.estado is EstadoRespuesta.PROHIBIDO
    assert resultado.estado is not EstadoRespuesta.RECHAZADO
    assert resultado.datos == ()


def test_404_es_recurso_no_disponible_y_no_una_lista_vacia():
    """SCRUM-98 responde 404 igual para un episodio ajeno y uno inexistente.

    Tratarlo como lista vacia diria «este embarazo no tiene sesiones», que es
    una afirmacion clinica que nadie hizo.
    """
    cliente, _ = construir(responde(404, {"detail": "No existe un embarazo..."}))

    resultado = cliente.sesiones(TOKEN, 999)

    assert resultado.estado is EstadoRespuesta.NO_ENCONTRADO
    assert resultado.datos == ()


@pytest.mark.parametrize("codigo", [500, 502, 503, 418, 301])
def test_cualquier_otro_codigo_es_error_remoto(codigo):
    """Un fallo del servidor se llama fallo del servidor.

    En particular **no** se convierte en un resultado vacio ni en un desenlace
    que la interfaz pudiera confundir con «no hay datos»: la ruta del adaptador
    lo traducira a un 502, que es lo que de verdad ocurrio.
    """
    cliente, _ = construir(responde(codigo, {"detail": "x"}))

    resultado = cliente.embarazos(TOKEN)

    assert resultado.estado is EstadoRespuesta.ERROR_REMOTO
    assert resultado.disponible is False
    assert resultado.datos == ()


@pytest.mark.parametrize(
    "estado_prohibido",
    [EstadoRespuesta.OK, EstadoRespuesta.SIN_CONEXION, EstadoRespuesta.NO_ENCONTRADO],
)
def test_un_500_no_se_disfraza_de_ningun_otro_desenlace(estado_prohibido):
    cliente, _ = construir(responde(500, {"detail": "x"}))

    assert cliente.embarazos(TOKEN).estado is not estado_prohibido


# ---------------------------------------------------------------------------
# Respuestas que no cumplen el contrato
# ---------------------------------------------------------------------------


def test_un_cuerpo_que_no_es_json_es_error_remoto():
    cliente, _ = construir(responde(200, texto="<html>no soy json</html>"))

    assert cliente.embarazos(TOKEN).estado is EstadoRespuesta.ERROR_REMOTO


def test_un_cuerpo_que_no_es_lista_es_error_remoto():
    cliente, _ = construir(responde(200, {"embarazos": []}))

    assert cliente.embarazos(TOKEN).estado is EstadoRespuesta.ERROR_REMOTO


def test_una_fila_a_la_que_le_falta_un_campo_obligatorio_es_error_remoto():
    """No se devuelve media lista: un panel con huecos es peor que decir que no."""
    incompleto = {k: v for k, v in EMBARAZO.items() if k != "estado_embarazo"}
    cliente, _ = construir(responde(200, [incompleto]))

    resultado = cliente.embarazos(TOKEN)

    assert resultado.estado is EstadoRespuesta.ERROR_REMOTO
    assert resultado.datos == ()


def test_una_fila_con_un_tipo_imposible_es_error_remoto():
    cliente, _ = construir(
        responde(200, [{**LECTURA_SIGNOS, "semana_gestacion": "veinticuatro"}])
    )

    assert cliente.lecturas(TOKEN, 501).estado is EstadoRespuesta.ERROR_REMOTO


def test_un_error_de_validacion_no_registra_valores_biometricos(caplog):
    """El detalle de Pydantic lleva el valor que fallo, y aqui eso es clinico."""
    cliente, _ = construir(
        responde(200, [{**LECTURA_SIGNOS, "semana_gestacion": "no-es-un-numero"}])
    )

    with caplog.at_level("WARNING"):
        cliente.lecturas(TOKEN, 501)

    registrado = caplog.text
    assert "no-es-un-numero" not in registrado
    assert "78.00" not in registrado


def test_el_token_no_aparece_en_los_registros(caplog):
    cliente, _ = construir(falla_transporte(httpx.ConnectError("caido")))

    with caplog.at_level("WARNING"):
        cliente.embarazos(TOKEN)

    assert TOKEN not in caplog.text


def test_la_contrasena_no_aparece_en_los_registros(caplog):
    cliente, _ = construir(falla_transporte(httpx.ConnectError("caido")))

    with caplog.at_level("WARNING"):
        cliente.autenticar(email=EMAIL, password=PASSWORD)

    assert PASSWORD not in caplog.text
    assert EMAIL not in caplog.text


# ---------------------------------------------------------------------------
# Identidad, que ya existia y no tenia cobertura HTTP
# ---------------------------------------------------------------------------


def test_la_identidad_usa_la_ruta_publicada_y_parsea_el_rol():
    cliente, grabadora = construir(
        responde(200, {"id_usuario": 4242, "rol": "PACIENTE"})
    )

    resultado = cliente.identidad(TOKEN)

    assert grabadora.ultima.url.path == RUTA_IDENTIDAD
    assert resultado.estado is EstadoRespuesta.OK
    assert resultado.id_usuario == 4242
    assert resultado.rol is NombreRol.PACIENTE


def test_un_rol_desconocido_no_se_adivina():
    cliente, _ = construir(responde(200, {"id_usuario": 1, "rol": "SUPERVISOR"}))

    assert cliente.identidad(TOKEN).estado is EstadoRespuesta.ERROR_REMOTO


def test_la_ruta_de_embarazos_publicada_es_la_del_contrato():
    assert RUTA_EMBARAZOS == "/api/v1/clinico/embarazos"
