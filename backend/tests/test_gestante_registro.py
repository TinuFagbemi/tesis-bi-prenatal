"""«Crear cuenta» en el adaptador de la gestante (SCRUM-110, R2).

Dos capas, y **ninguna con el backend real**:

* el cliente HTTP (:class:`ClienteCentralHTTP`) contra un ``httpx.MockTransport``
  que responde lo que cada caso necesita, incluidas las excepciones de
  transporte. Es lo que fija la diferencia entre «no se envio» y «no se sabe»;
* las rutas del adaptador con el doble del servidor central de
  ``test_gestante_rutas``, ampliado con las dos operaciones del registro.

La prueba contra la API y PostgreSQL reales esta descrita en
``docs/autorregistro_pacientes.md`` y no forma parte de esta suite.

Todas las cuentas y los datos son ficticios y simulados.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta

import httpx
import pytest

from app.gestante.central import (
    CAMPOS_DEL_REGISTRO,
    ClienteCentralHTTP,
    EstadoRegistro,
    EstadoRespuesta,
    RespuestaDocumentos,
    RespuestaRegistro,
    RUTA_REGISTRO_DOCUMENTOS,
    RUTA_REGISTRO_PACIENTES,
)
from app.gestante.config import NOMBRE_DE_COOKIE
from app.legal import DOCUMENTOS_VIGENTES, VERSION_POLITICA_PRIVACIDAD, VERSION_TERMINOS_CONDICIONES
from app.schemas.registro import DocumentoPublicado
from tests.test_gestante_rutas import ClienteCentralDoble, construir_cliente

CLAVE = "clave ficticia con espacios "
CEDULA = "SIM-PAC-R2-001"
CELULAR = "6000-0000"
CORREO = "  Paciente9201@Example.com "


def cuerpo_valido(**cambios) -> dict:
    datos = {
        "primer_nombre": "Ana",
        "segundo_nombre": None,
        "apellido_paterno": "Pérez",
        "apellido_materno": None,
        "cedula": CEDULA,
        "fecha_nac": "1995-04-12",
        "email": CORREO,
        "celular": CELULAR,
        "password": CLAVE,
        "confirmacion_password": CLAVE,
        "acepta_politica_privacidad": True,
        "version_politica_privacidad": VERSION_POLITICA_PRIVACIDAD,
        "acepta_terminos_condiciones": True,
        "version_terminos_condiciones": VERSION_TERMINOS_CONDICIONES,
    }
    datos.update(cambios)
    return datos


# ---------------------------------------------------------------------------
# 1. El cliente HTTP
# ---------------------------------------------------------------------------


def cliente_con(manejador) -> tuple[ClienteCentralHTTP, list[httpx.Request]]:
    peticiones: list[httpx.Request] = []

    def transporte(peticion: httpx.Request) -> httpx.Response:
        peticiones.append(peticion)
        return manejador(peticion)

    http = httpx.Client(base_url="http://central.invalid", transport=httpx.MockTransport(transporte))
    return ClienteCentralHTTP(http), peticiones


def lanzar(excepcion: type[httpx.HTTPError]):
    def manejador(peticion: httpx.Request) -> httpx.Response:
        raise excepcion("simulado", request=peticion)

    return manejador


@pytest.mark.parametrize(
    "codigo, estado",
    [
        (201, EstadoRegistro.CREADA),
        (409, EstadoRegistro.DUPLICADA),
        (500, EstadoRegistro.ERROR_REMOTO),
        (502, EstadoRegistro.ERROR_REMOTO),
        (200, EstadoRegistro.ERROR_REMOTO),
    ],
)
def test_cada_codigo_del_servidor_tiene_su_desenlace(codigo, estado):
    cliente, peticiones = cliente_con(lambda p: httpx.Response(codigo, json={}))

    resultado = cliente.autorregistrar(cuerpo_valido())

    assert resultado.estado is estado
    assert len(peticiones) == 1
    assert peticiones[0].method == "POST"
    assert peticiones[0].url.path == RUTA_REGISTRO_PACIENTES
    # Sin token: la paciente todavia no tiene cuenta.
    assert "authorization" not in peticiones[0].headers


def test_un_201_con_un_cuerpo_ilegible_sigue_siendo_una_cuenta_creada():
    """R1 responde 201 solo despues del commit."""
    cliente, _ = cliente_con(lambda p: httpx.Response(201, content=b"no es json"))

    assert cliente.autorregistrar(cuerpo_valido()).estado is EstadoRegistro.CREADA


@pytest.mark.parametrize(
    "excepcion", [httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout]
)
def test_si_la_peticion_no_salio_es_sin_conexion(excepcion):
    cliente, peticiones = cliente_con(lanzar(excepcion))

    resultado = cliente.autorregistrar(cuerpo_valido())

    assert resultado.estado is EstadoRegistro.SIN_CONEXION
    assert len(peticiones) == 1


@pytest.mark.parametrize(
    "excepcion",
    [httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError, httpx.WriteError, httpx.WriteTimeout],
)
def test_si_la_peticion_pudo_llegar_el_resultado_es_desconocido_y_no_se_reintenta(excepcion):
    cliente, peticiones = cliente_con(lanzar(excepcion))

    resultado = cliente.autorregistrar(cuerpo_valido())

    assert resultado.estado is EstadoRegistro.DESCONOCIDO
    assert len(peticiones) == 1


def test_un_fallo_no_deja_la_contrasena_ni_los_datos_en_los_registros(caplog):
    caplog.set_level(logging.DEBUG)
    for manejador in (lanzar(httpx.ReadTimeout), lanzar(httpx.ConnectError), lambda p: httpx.Response(500)):
        cliente, _ = cliente_con(manejador)
        cliente.autorregistrar(cuerpo_valido())

    for valor in (CLAVE.strip(), CEDULA, CELULAR, "paciente9201"):
        assert valor not in caplog.text.lower(), valor


def test_el_422_solo_entrega_nombres_de_campo_conocidos():
    detalle = [
        {"loc": ["body", "cedula"], "msg": "too long", "input": CEDULA},
        {"loc": ["body", "celular"], "msg": "x", "input": CELULAR},
        {"loc": ["body", "cedula"], "msg": "repetido"},
        {"loc": ["body", "campo_inventado"], "msg": "x"},
        {"loc": ["body"], "msg": "La contraseña y su confirmación no coinciden."},
        "no es un diccionario",
    ]
    cliente, _ = cliente_con(lambda p: httpx.Response(422, json={"detail": detalle}))

    resultado = cliente.autorregistrar(cuerpo_valido())

    assert resultado == RespuestaRegistro(
        EstadoRegistro.DATOS_RECHAZADOS, campos=("cedula", "celular")
    )


@pytest.mark.parametrize(
    "cabecera, esperado",
    [("120", 120), ("0", None), ("-5", None), ("abc", None), ("99999", None), (None, None)],
)
def test_el_429_solo_cree_un_retry_after_razonable(cabecera, esperado):
    cabeceras = {} if cabecera is None else {"Retry-After": cabecera}
    cliente, _ = cliente_con(lambda p: httpx.Response(429, headers=cabeceras, json={}))

    resultado = cliente.autorregistrar(cuerpo_valido())

    assert resultado.estado is EstadoRegistro.LIMITE
    assert resultado.reintentar_en_segundos == esperado


def _cuerpo_de_documentos() -> dict:
    return {
        "documentos": [
            {
                "documento": d.documento.value,
                "version": d.version,
                "titulo": d.titulo,
                "texto": d.texto,
                "sha256": d.sha256,
            }
            for d in DOCUMENTOS_VIGENTES.values()
        ]
    }


def test_los_documentos_se_validan_con_el_contrato_de_r1():
    cliente, peticiones = cliente_con(lambda p: httpx.Response(200, json=_cuerpo_de_documentos()))

    resultado = cliente.documentos_registro()

    assert resultado.estado is EstadoRespuesta.OK
    assert [d.version for d in resultado.documentos] == [
        VERSION_POLITICA_PRIVACIDAD,
        VERSION_TERMINOS_CONDICIONES,
    ]
    assert peticiones[0].url.path == RUTA_REGISTRO_DOCUMENTOS


@pytest.mark.parametrize(
    "manejador, estado",
    [
        (lambda p: httpx.Response(200, json={"documentos": [{"documento": "OTRO"}]}), EstadoRespuesta.ERROR_REMOTO),
        (lambda p: httpx.Response(200, content=b"<html>"), EstadoRespuesta.ERROR_REMOTO),
        (lambda p: httpx.Response(500), EstadoRespuesta.ERROR_REMOTO),
        (lanzar(httpx.ConnectError), EstadoRespuesta.SIN_CONEXION),
    ],
)
def test_documentos_ilegibles_o_inalcanzables_no_se_entregan_a_medias(manejador, estado):
    cliente, _ = cliente_con(manejador)

    resultado = cliente.documentos_registro()

    assert resultado.estado is estado
    assert resultado.documentos == ()


# ---------------------------------------------------------------------------
# 2. Las rutas del adaptador
# ---------------------------------------------------------------------------


class CentralConRegistro(ClienteCentralDoble):
    """El doble de ``test_gestante_rutas`` con las dos operaciones del registro."""

    def __init__(self, *, registro: RespuestaRegistro | None = None,
                 documentos: RespuestaDocumentos | None = None, **opciones) -> None:
        super().__init__(**opciones)
        self.registro = registro or RespuestaRegistro(EstadoRegistro.CREADA)
        self.documentos = documentos or RespuestaDocumentos(
            EstadoRespuesta.OK,
            documentos=tuple(
                DocumentoPublicado(
                    documento=d.documento, version=d.version, titulo=d.titulo,
                    texto=d.texto, sha256=d.sha256,
                )
                for d in DOCUMENTOS_VIGENTES.values()
            ),
        )
        self.cuerpos: list[dict] = []
        self.llamadas_documentos = 0

    def documentos_registro(self) -> RespuestaDocumentos:
        self.llamadas_documentos += 1
        return self.documentos

    def autorregistrar(self, cuerpo: dict) -> RespuestaRegistro:
        self.cuerpos.append(cuerpo)
        return self.registro


def adaptador(tmp_path, **opciones):
    central = CentralConRegistro(**opciones)
    cliente, _, _, _ = construir_cliente(tmp_path, central=central)
    return cliente, central


def test_se_sirve_registro_js(tmp_path):
    cliente, _ = adaptador(tmp_path)

    respuesta = cliente.get("/registro.js")

    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/javascript")
    assert "/adaptador/registro" in respuesta.text
    assert respuesta.headers["cache-control"] == "no-cache"


def test_los_documentos_llegan_sin_huella_y_sin_sesion(tmp_path):
    cliente, central = adaptador(tmp_path)

    respuesta = cliente.get("/adaptador/registro/documentos")

    assert respuesta.status_code == 200
    documentos = respuesta.json()["documentos"]
    assert [d["documento"] for d in documentos] == ["POLITICA_PRIVACIDAD", "TERMINOS_CONDICIONES"]
    assert all(set(d) == {"documento", "version", "titulo", "texto"} for d in documentos)
    assert central.llamadas_documentos == 1


@pytest.mark.parametrize(
    "estado, codigo",
    [(EstadoRespuesta.SIN_CONEXION, 503), (EstadoRespuesta.ERROR_REMOTO, 502)],
)
def test_documentos_no_disponibles(tmp_path, estado, codigo):
    cliente, _ = adaptador(tmp_path, documentos=RespuestaDocumentos(estado))

    respuesta = cliente.get("/adaptador/registro/documentos")

    assert respuesta.status_code == codigo
    assert set(respuesta.json()) == {"detail"}


def test_el_registro_se_reenvia_una_vez_con_el_contrato_de_r1(tmp_path):
    cliente, central = adaptador(tmp_path)

    respuesta = cliente.post("/adaptador/registro", json=cuerpo_valido())

    assert respuesta.status_code == 201
    assert respuesta.json() == {"creada": True}
    assert len(central.cuerpos) == 1
    enviado = central.cuerpos[0]
    assert set(enviado) == CAMPOS_DEL_REGISTRO
    # La contraseña, intacta; el correo, en la forma con que se guarda y se
    # compara al iniciar sesion.
    assert enviado["password"] == CLAVE
    assert enviado["confirmacion_password"] == CLAVE
    assert enviado["email"] == "paciente9201@example.com"
    assert enviado["fecha_nac"] == "1995-04-12"
    json.dumps(enviado)  # serializable tal cual para httpx


def test_crear_la_cuenta_no_abre_una_sesion_local(tmp_path):
    """La paciente entra despues por el inicio de sesion de siempre."""
    cliente, central = adaptador(tmp_path)

    respuesta = cliente.post("/adaptador/registro", json=cuerpo_valido())

    assert respuesta.status_code == 201
    assert NOMBRE_DE_COOKIE not in respuesta.cookies
    assert cliente.get("/adaptador/sesion").json().get("autenticada") is not True
    assert central.llamadas_autenticar == 0


@pytest.mark.parametrize(
    "cambios, campo",
    [
        ({"cedula": "   "}, "cedula"),
        ({"fecha_nac": (date.today() + timedelta(days=2)).isoformat()}, "fecha_nac"),
        ({"confirmacion_password": "otra"}, "confirmacion_password"),
        ({"acepta_terminos_condiciones": False}, "acepta_terminos_condiciones"),
        ({"version_politica_privacidad": "2020-01-v0"}, "version_politica_privacidad"),
        ({"celular": "6" * 121}, "celular"),
    ],
)
def test_un_formulario_invalido_no_gasta_el_cupo_ni_devuelve_valores(tmp_path, cambios, campo):
    cliente, central = adaptador(tmp_path)

    respuesta = cliente.post("/adaptador/registro", json=cuerpo_valido(**cambios))

    assert respuesta.status_code == 422
    cuerpo = respuesta.json()
    assert cuerpo["campos"] == [campo]
    assert central.cuerpos == []
    for valor in (CLAVE, CEDULA, "otra", "6" * 121):
        assert valor not in respuesta.text


@pytest.mark.parametrize(
    "contenido",
    [b"{no es json", json.dumps(["lista"]).encode(), json.dumps({**cuerpo_valido(), "rol": "ADMIN"}).encode()],
)
def test_un_cuerpo_con_otra_forma_es_422_sin_eco(tmp_path, contenido):
    cliente, central = adaptador(tmp_path)

    respuesta = cliente.post(
        "/adaptador/registro", content=contenido, headers={"Content-Type": "application/json"}
    )

    assert respuesta.status_code == 422
    assert CLAVE not in respuesta.text
    assert '"input"' not in respuesta.text
    assert central.cuerpos == []


@pytest.mark.parametrize(
    "resultado, codigo, claves",
    [
        (RespuestaRegistro(EstadoRegistro.DUPLICADA), 409, {"detail"}),
        (RespuestaRegistro(EstadoRegistro.DATOS_RECHAZADOS, campos=("cedula",)), 422, {"detail", "campos"}),
        (RespuestaRegistro(EstadoRegistro.LIMITE, reintentar_en_segundos=90), 429, {"detail", "reintentar_en_segundos"}),
        (RespuestaRegistro(EstadoRegistro.SIN_CONEXION), 503, {"detail", "enviado"}),
        (RespuestaRegistro(EstadoRegistro.DESCONOCIDO), 504, {"detail", "resultado"}),
        (RespuestaRegistro(EstadoRegistro.ERROR_REMOTO), 502, {"detail"}),
    ],
)
def test_cada_desenlace_tiene_un_codigo_y_ningun_dato_de_la_paciente(tmp_path, resultado, codigo, claves):
    cliente, central = adaptador(tmp_path, registro=resultado)

    respuesta = cliente.post("/adaptador/registro", json=cuerpo_valido())

    assert respuesta.status_code == codigo
    assert set(respuesta.json()) == claves
    assert len(central.cuerpos) == 1
    for valor in (CLAVE, CEDULA, CELULAR, "paciente9201"):
        assert valor not in respuesta.text.lower()


def test_el_429_lleva_retry_after_y_el_504_dice_que_no_se_sabe(tmp_path):
    cliente, _ = adaptador(tmp_path, registro=RespuestaRegistro(EstadoRegistro.LIMITE, reintentar_en_segundos=90))
    respuesta = cliente.post("/adaptador/registro", json=cuerpo_valido())
    assert respuesta.headers["Retry-After"] == "90"
    assert respuesta.json()["reintentar_en_segundos"] == 90

    cliente, _ = adaptador(tmp_path, registro=RespuestaRegistro(EstadoRegistro.DESCONOCIDO))
    respuesta = cliente.post("/adaptador/registro", json=cuerpo_valido())
    assert respuesta.json()["resultado"] == "desconocido"
    assert "no se sabe" in respuesta.json()["detail"]

    cliente, _ = adaptador(tmp_path, registro=RespuestaRegistro(EstadoRegistro.SIN_CONEXION))
    respuesta = cliente.post("/adaptador/registro", json=cuerpo_valido())
    assert respuesta.json()["enviado"] is False
