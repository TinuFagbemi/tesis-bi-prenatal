"""Endurecimiento de la autenticacion tras el code review de SCRUM-70.

Tres defectos que el review encontro y que las pruebas anteriores no cubrian:

1. **Un fallo de base de datos en el login o al resolver el principal** llegaba
   sin capturar hasta uvicorn, y el traceback de un error del driver lleva la
   sentencia y sus parametros: en el login, el correo tecleado.
2. **El 422 del login dejaba de sanearse si la API se servia con ``root_path``**,
   porque el filtro comparaba la URL y no la ruta resuelta.
3. **Un ``sub`` desmesurado** producia un 500 en lugar de un 401.

**Sobre los marcadores.** Una prueba que afirma «el log no contiene X» sin
demostrar que X habria aparecido si se filtrara no prueba nada. Por eso el error
fabricado lleva un correo, un parametro SQL, un mensaje de driver y una URL
reconocibles, y hay pruebas de control que comprueban que ese mismo error, si se
formatea, **si** los muestra y el detector **si** los encuentra.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from starlette.requests import Request

from app.api.dependencias import (
    CONTEXTO_AUTENTICACION,
    CONTEXTO_RESOLUCION_DEL_PRINCIPAL,
    MENSAJE_ERROR_INTERNO,
    get_db_auditoria,
    obtener_configuracion_jwt,
    usuario_actual,
)
from app.api.v1.autenticacion import (
    RUTAS_CON_CREDENCIALES,
    lleva_credenciales,
    sanear_errores_de_validacion,
)
from app.config import ConfiguracionJWT
from app.db.session import get_db
from app.main import app
from app.services.tokens import ID_USUARIO_MAXIMO, emitir
from tests.conftest import principal_de_prueba
from tests.test_autenticacion_api import SesionDeCuentas, bearer
from tests.test_ingestion_schemas import paquete
from tests.test_tokens import forjar, payload_valido

RUTA_TOKEN = "/api/v1/autenticacion/token"
RUTA_YO = "/api/v1/autenticacion/yo"
RUTA_INGESTA = "/api/v1/sesiones-monitoreo"

SECRETO = "secreto-ficticio-del-endurecimiento-con-longitud-sobrada"
CONFIGURACION = ConfiguracionJWT(secreto=SECRETO, expiracion=timedelta(minutes=30))

# --- Marcadores -------------------------------------------------------------
MARCADOR_EMAIL = "marcador.filtracion.7731@example.com"
MARCADOR_PARAMETRO = "PARAMETRO-SQL-MARCADOR-7731"
MARCADOR_DRIVER = "MENSAJE-DEL-DRIVER-MARCADOR-7731"
MARCADOR_SERVIDOR = "servidor-marcador-7731"
MARCADOR_URL = f"postgresql+psycopg://usuario:clave@{MARCADOR_SERVIDOR}:5432/base"
MARCADOR_PASSWORD = "CONTRASENA-MARCADOR-7731"
SQL_MARCADOR = (
    "SELECT operacional.usuario.id_usuario FROM operacional.usuario "
    "WHERE operacional.usuario.email = %(email_1)s"
)

PROHIBIDOS = (
    MARCADOR_EMAIL,
    MARCADOR_PARAMETRO,
    MARCADOR_DRIVER,
    MARCADOR_SERVIDOR,
    MARCADOR_PASSWORD,
    "SELECT",
    "operacional.usuario.email",
    "[SQL",
    "[parameters",
    "Traceback",
)


def error_de_base(sufijo: str = "") -> OperationalError:
    """Un error del driver con todo lo que no debe salir de la aplicacion."""
    return OperationalError(
        SQL_MARCADOR,
        {"email_1": MARCADOR_EMAIL, "extra": MARCADOR_PARAMETRO + sufijo},
        Exception(f"{MARCADOR_DRIVER}{sufijo} {MARCADOR_URL}"),
    )


def filtraciones(texto: str) -> list[str]:
    return [marcador for marcador in PROHIBIDOS if marcador in texto]


class SesionRota:
    """Doble de Session cuya consulta falla como fallaria el driver."""

    def __init__(self, *, falla_al_revertir: bool = False) -> None:
        self.falla_al_revertir = falla_al_revertir
        self.consultas = 0
        self.rollbacks = 0

    def execute(self, *args, **kwargs):
        self.consultas += 1
        raise error_de_base()

    def rollback(self) -> None:
        self.rollbacks += 1
        if self.falla_al_revertir:
            raise error_de_base("-AL-REVERTIR")

    def add(self, entidad) -> None:  # pragma: no cover -- no se alcanza
        pass

    def flush(self) -> None:  # pragma: no cover
        pass

    def commit(self) -> None:  # pragma: no cover
        pass

    def close(self) -> None:  # pragma: no cover
        pass


@pytest.fixture
def instalar():
    """Instala dobles en la aplicacion y los retira siempre."""

    def _instalar(sesion, *, identidad=None):
        app.dependency_overrides[get_db] = lambda: sesion
        app.dependency_overrides[get_db_auditoria] = lambda: SesionDeCuentas()
        app.dependency_overrides[obtener_configuracion_jwt] = lambda: CONFIGURACION
        if identidad is not None:
            app.dependency_overrides[usuario_actual] = identidad
        # ``raise_server_exceptions=True``: si un error del driver escapara hasta
        # la capa del servidor, la prueba reventaria en lugar de ver un 500.
        return TestClient(app, raise_server_exceptions=True)

    try:
        yield _instalar
    finally:
        app.dependency_overrides.clear()


def afirmar_log_saneado(caplog, contexto: str) -> None:
    texto = caplog.text
    assert filtraciones(texto) == [], f"Filtrado al log: {filtraciones(texto)}"
    assert all(registro.exc_info is None for registro in caplog.records)
    mensajes = [registro.getMessage() for registro in caplog.records]
    assert any(f"Fallo de base de datos en {contexto}:" in m for m in mensajes)
    assert not any("registrar la sesi" in m for m in mensajes)


# ---------------------------------------------------------------------------
# Controles: los marcadores se detectarian si se filtraran
# ---------------------------------------------------------------------------


def test_control_el_error_fabricado_si_contiene_los_marcadores():
    """Sin esto, las afirmaciones de ausencia de abajo no demostrarian nada."""
    texto = str(error_de_base())

    for marcador in (
        MARCADOR_EMAIL,
        MARCADOR_PARAMETRO,
        MARCADOR_DRIVER,
        MARCADOR_SERVIDOR,
        "SELECT",
        "[SQL",
        "[parameters",
    ):
        assert marcador in texto


def test_control_el_detector_encuentra_un_error_formateado_en_el_log(caplog):
    """El mismo canal de log que las pruebas vigilan, con el error mal usado."""
    caplog.set_level(logging.DEBUG)

    logging.getLogger("control.de.filtracion").error("%s", error_de_base())

    assert MARCADOR_EMAIL in filtraciones(caplog.text)
    assert MARCADOR_PARAMETRO in filtraciones(caplog.text)
    assert "[SQL" in filtraciones(caplog.text)


# ---------------------------------------------------------------------------
# 1. Fallo de base de datos en el login
# ---------------------------------------------------------------------------


def test_un_fallo_de_base_en_el_login_es_un_500_saneado(instalar, caplog):
    caplog.set_level(logging.DEBUG)
    sesion = SesionRota()
    cliente = instalar(sesion)

    respuesta = cliente.post(
        RUTA_TOKEN, json={"email": MARCADOR_EMAIL, "password": MARCADOR_PASSWORD}
    )

    assert respuesta.status_code == 500
    assert respuesta.json() == {"detail": MENSAJE_ERROR_INTERNO}
    assert filtraciones(respuesta.text) == []
    assert "access_token" not in respuesta.text


def test_un_fallo_de_base_en_el_login_no_filtra_al_log(instalar, caplog):
    caplog.set_level(logging.DEBUG)
    sesion = SesionRota()
    cliente = instalar(sesion)

    cliente.post(RUTA_TOKEN, json={"email": MARCADOR_EMAIL, "password": MARCADOR_PASSWORD})

    afirmar_log_saneado(caplog, CONTEXTO_AUTENTICACION)


def test_un_fallo_de_base_en_el_login_revierte_la_sesion(instalar):
    sesion = SesionRota()
    instalar(sesion).post(
        RUTA_TOKEN, json={"email": MARCADOR_EMAIL, "password": MARCADOR_PASSWORD}
    )

    assert sesion.consultas == 1
    assert sesion.rollbacks == 1


def test_un_rollback_que_tambien_falla_sigue_saneado(instalar, caplog):
    """La reversion sobre una conexion rota puede fallar; tampoco escapa."""
    caplog.set_level(logging.DEBUG)
    cliente = instalar(SesionRota(falla_al_revertir=True))

    respuesta = cliente.post(
        RUTA_TOKEN, json={"email": MARCADOR_EMAIL, "password": MARCADOR_PASSWORD}
    )

    assert respuesta.status_code == 500
    assert respuesta.json() == {"detail": MENSAJE_ERROR_INTERNO}
    afirmar_log_saneado(caplog, CONTEXTO_AUTENTICACION)
    assert "AL-REVERTIR" not in caplog.text


# ---------------------------------------------------------------------------
# 2. Fallo de base de datos al resolver el principal
# ---------------------------------------------------------------------------


def test_un_fallo_de_base_en_yo_es_un_500_saneado(instalar, caplog):
    caplog.set_level(logging.DEBUG)
    sesion = SesionRota()
    cliente = instalar(sesion)
    token = emitir(130, CONFIGURACION).access_token

    respuesta = cliente.get(RUTA_YO, headers=bearer(token))

    assert respuesta.status_code == 500
    assert respuesta.json() == {"detail": MENSAJE_ERROR_INTERNO}
    assert "www-authenticate" not in {k.lower() for k in respuesta.headers}
    assert filtraciones(respuesta.text) == []
    assert token not in caplog.text
    assert sesion.rollbacks == 1
    afirmar_log_saneado(caplog, CONTEXTO_RESOLUCION_DEL_PRINCIPAL)


def test_un_fallo_de_base_al_resolver_el_principal_de_la_ingesta(instalar, caplog):
    """La misma dependencia protege la operacion de negocio."""
    caplog.set_level(logging.DEBUG)
    sesion = SesionRota()
    cliente = instalar(sesion)
    token = emitir(130, CONFIGURACION).access_token

    respuesta = cliente.post(
        RUTA_INGESTA,
        json=paquete(),
        headers={**bearer(token), "Idempotency-Key": "endurecimiento-0001"},
    )

    assert respuesta.status_code == 500
    assert respuesta.json() == {"detail": MENSAJE_ERROR_INTERNO}
    afirmar_log_saneado(caplog, CONTEXTO_RESOLUCION_DEL_PRINCIPAL)


def test_el_500_de_la_identidad_no_habla_de_la_ingesta():
    """El mensaje del 500 de la ingesta dice que no se registro la sesion y que la
    transaccion se revirtio; nada de eso es verdad en un login ni en ``/yo``."""
    from app.services.errores import MENSAJE_INESPERADO

    assert MENSAJE_ERROR_INTERNO != MENSAJE_INESPERADO
    for palabra in ("sesi", "transacci", "revert", "registrar"):
        assert palabra not in MENSAJE_ERROR_INTERNO.lower()


def test_un_fallo_de_base_no_se_confunde_con_un_401(instalar):
    """Una credencial valida no debe oir que es mala porque la base fallo."""
    token = emitir(130, CONFIGURACION).access_token

    respuesta = instalar(SesionRota()).get(RUTA_YO, headers=bearer(token))

    assert respuesta.status_code != 401


# ---------------------------------------------------------------------------
# 3. El 422 del login se decide por la ruta resuelta
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefijo", ["", "/prefijo", "/api-detras-de-un-proxy"])
def test_el_422_del_login_se_sanea_con_y_sin_root_path(prefijo):
    cliente = TestClient(app, root_path=prefijo)

    respuesta = cliente.post(f"{prefijo}{RUTA_TOKEN}", json={"password": MARCADOR_PASSWORD})

    assert respuesta.status_code == 422
    assert MARCADOR_PASSWORD not in respuesta.text
    assert all("input" not in error for error in respuesta.json()["detail"])


@pytest.mark.parametrize("prefijo", ["", "/prefijo"])
def test_el_422_de_la_ingesta_conserva_su_contrato(prefijo):
    """El filtro no se amplia: la ingesta sigue devolviendo ``input``.

    El valor marcador aparece en la respuesta a proposito: es la prueba de que
    el contrato 422 que SCRUM-62 definio sigue exactamente igual.
    """
    # Desde SCRUM-98 la ruta resuelve ademas el contexto clinico contra
    # PostgreSQL. Aqui no hay base, y lo que esta prueba mide es el cuerpo del
    # 422, asi que se entrega un contexto ya resuelto; el cuerpo se valida
    # despues de las dependencias, que es justo el orden que se quiere fijar.
    from app.api.v1 import sesiones as router_sesiones
    from tests.conftest import contexto_de_prueba

    app.dependency_overrides[usuario_actual] = principal_de_prueba
    app.dependency_overrides[router_sesiones.EXIGIR_PACIENTE] = contexto_de_prueba
    try:
        cliente = TestClient(app, root_path=prefijo)
        respuesta = cliente.post(
            f"{prefijo}{RUTA_INGESTA}",
            json={"id_embarazo": "VALOR-DE-ENTRADA-MARCADOR"},
            headers={"Idempotency-Key": "endurecimiento-0002"},
        )
    finally:
        app.dependency_overrides.clear()

    assert respuesta.status_code == 422
    assert any("input" in error for error in respuesta.json()["detail"])
    assert "VALOR-DE-ENTRADA-MARCADOR" in respuesta.text


def peticion_con(ruta) -> Request:
    alcance = {
        "type": "http",
        "method": "POST",
        "path": "/cualquiera",
        "root_path": "",
        "headers": [],
        "query_string": b"",
    }
    if ruta is not None:
        alcance["route"] = ruta
    return Request(alcance)


def excepcion_con_eco() -> RequestValidationError:
    return RequestValidationError(
        [
            {
                "type": "missing",
                "loc": ("body", "email"),
                "msg": "Field required",
                "input": {"password": MARCADOR_PASSWORD},
            }
        ]
    )


def test_la_decision_usa_la_plantilla_de_la_ruta_no_la_url():
    ruta_login = SimpleNamespace(path=next(iter(RUTAS_CON_CREDENCIALES)))
    ruta_ingesta = SimpleNamespace(path=RUTA_INGESTA)

    assert lleva_credenciales(peticion_con(ruta_login)) is True
    assert lleva_credenciales(peticion_con(ruta_ingesta)) is False


def test_sin_ruta_resuelta_se_sanea_por_seguridad():
    """Situacion no prevista: el lado seguro es no devolver valores."""
    respuesta = asyncio.run(
        sanear_errores_de_validacion(peticion_con(None), excepcion_con_eco())
    )

    assert respuesta.status_code == 422
    assert MARCADOR_PASSWORD.encode() not in respuesta.body


def test_una_ruta_resuelta_ajena_conserva_el_eco():
    respuesta = asyncio.run(
        sanear_errores_de_validacion(
            peticion_con(SimpleNamespace(path=RUTA_INGESTA)), excepcion_con_eco()
        )
    )

    assert MARCADOR_PASSWORD.encode() in respuesta.body


# ---------------------------------------------------------------------------
# 4. Un sub fuera del dominio es un 401, nunca un 500
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sujeto",
    [str(ID_USUARIO_MAXIMO + 1), "9" * 5000, "0", "0137"],
    ids=["maximo-mas-uno", "miles-de-digitos", "cero", "ceros-iniciales"],
)
def test_un_sub_fuera_del_dominio_es_401_sin_consultar_la_base(instalar, sujeto):
    sesion = SesionDeCuentas()
    cliente = instalar(sesion)
    token = forjar(payload_valido(sub=sujeto), secreto=SECRETO)

    respuesta = cliente.get(RUTA_YO, headers=bearer(token))

    assert respuesta.status_code == 401
    assert respuesta.headers["www-authenticate"] == 'Bearer error="invalid_token"'
    assert sesion.consultas == 0
