"""Contrato HTTP de la autenticacion: login, Bearer, 401 y 403 (SCRUM-70).

Aqui se prueba **nuestro** contrato, no el de FastAPI. La aplicacion usa
``HTTPBearer(auto_error=False)`` precisamente para que cada 401 lo construya
codigo de este proyecto, con su mensaje y su desafio; comprobar lo que hace
``auto_error=True`` seria fijar el comportamiento de una implementacion que
deliberadamente no se usa.

La distincion que recorre el archivo entero: **401 es "no se quien eres"** --no
hay cabecera, el esquema no es Bearer, el token no verifica, o la cuenta ya no
existe o esta desactivada-- y **403 es "se exactamente quien eres y este rol no
puede"**. Solo el 401 lleva desafio.

Todas las cuentas son ficticias y completamente simuladas.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.dependencias import (
    DESAFIO_SIN_CREDENCIAL,
    DESAFIO_TOKEN_INVALIDO,
    MENSAJE_NO_AUTENTICADO,
    get_db_auditoria,
    obtener_configuracion_jwt,
)
from app.api.v1.autenticacion import MENSAJE_CREDENCIALES_INVALIDAS
from app.config import ConfiguracionJWT
from app.db.session import get_db
from app.main import app
from app.models.enums import NombreRol
from app.api.dependencias import MENSAJE_ERROR_INTERNO
from app.services.passwords import hashear
from app.services.tokens import emitir

RUTA_TOKEN = "/api/v1/autenticacion/token"
RUTA_YO = "/api/v1/autenticacion/yo"

SECRETO = "secreto-ficticio-de-las-pruebas-http-con-longitud-sobrada"
CONFIGURACION = ConfiguracionJWT(secreto=SECRETO, expiracion=timedelta(minutes=30))

EMAIL = "paciente01@example.com"
PASSWORD = "clave-simulada-de-la-prueba"
ID_USUARIO = 130


# ---------------------------------------------------------------------------
# Dobles
# ---------------------------------------------------------------------------


class ResultadoFalso:
    def __init__(self, fila) -> None:
        self._fila = fila

    def one_or_none(self):
        return self._fila


class SesionDeCuentas:
    """Doble de Session que responde la consulta de cuenta con una fila guionada."""

    def __init__(self, fila=None) -> None:
        self.fila = fila
        self.consultas = 0
        self.agregados: list = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sentencia, *args, **kwargs):
        self.consultas += 1
        return ResultadoFalso(self.fila)

    def add(self, entidad) -> None:
        self.agregados.append(entidad)

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:  # pragma: no cover -- la fixture es la duena
        pass


def cuenta(
    *,
    id_usuario: int = ID_USUARIO,
    password: str = PASSWORD,
    activo: bool = True,
    rol: NombreRol = NombreRol.PACIENTE,
):
    """Fila tal como la lee ``app.services.principal``, con un hash autentico."""
    return SimpleNamespace(
        id_usuario=id_usuario,
        password_hash=hashear(password),
        activo=activo,
        nombre_rol=rol,
    )


@pytest.fixture
def sesion() -> SesionDeCuentas:
    return SesionDeCuentas()


@pytest.fixture
def auditoria() -> SesionDeCuentas:
    return SesionDeCuentas()


@pytest.fixture
def cliente(sesion, auditoria) -> TestClient:
    app.dependency_overrides[get_db] = lambda: sesion
    app.dependency_overrides[get_db_auditoria] = lambda: auditoria
    app.dependency_overrides[obtener_configuracion_jwt] = lambda: CONFIGURACION
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def token_de(id_usuario: int = ID_USUARIO, *, reloj=None) -> str:
    extra = {} if reloj is None else {"reloj": reloj}
    return emitir(id_usuario, CONFIGURACION, **extra).access_token


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1. Login correcto
# ---------------------------------------------------------------------------


def test_credenciales_validas_devuelven_un_token(cliente, sesion):
    sesion.fila = cuenta()

    respuesta = cliente.post(RUTA_TOKEN, json={"email": EMAIL, "password": PASSWORD})

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["token_type"] == "bearer"
    assert cuerpo["expires_in"] == 30 * 60
    assert cuerpo["access_token"]


def test_la_respuesta_no_lleva_nada_mas(cliente, sesion):
    """Ni identificador, ni rol, ni correo, ni hash, ni refresh token."""
    sesion.fila = cuenta()

    cuerpo = cliente.post(
        RUTA_TOKEN, json={"email": EMAIL, "password": PASSWORD}
    ).json()

    assert set(cuerpo) == {"access_token", "token_type", "expires_in"}


def test_el_token_emitido_sirve_para_identificarse(cliente, sesion):
    sesion.fila = cuenta()
    token = cliente.post(
        RUTA_TOKEN, json={"email": EMAIL, "password": PASSWORD}
    ).json()["access_token"]

    respuesta = cliente.get(RUTA_YO, headers=bearer(token))

    assert respuesta.status_code == 200
    assert respuesta.json() == {"id_usuario": ID_USUARIO, "rol": "PACIENTE"}


# ---------------------------------------------------------------------------
# 2. Credenciales invalidas: una sola respuesta
# ---------------------------------------------------------------------------


def respuestas_de_fallo(cliente, sesion):
    """Las tres formas de fallar, en el mismo orden siempre."""
    resultados = []

    sesion.fila = None
    resultados.append(
        cliente.post(RUTA_TOKEN, json={"email": "nadie@example.com", "password": PASSWORD})
    )

    sesion.fila = cuenta()
    resultados.append(
        cliente.post(RUTA_TOKEN, json={"email": EMAIL, "password": "incorrecta"})
    )

    sesion.fila = cuenta(activo=False)
    resultados.append(
        cliente.post(RUTA_TOKEN, json={"email": EMAIL, "password": PASSWORD})
    )

    return resultados


def test_las_tres_formas_de_fallar_dan_401(cliente, sesion):
    for respuesta in respuestas_de_fallo(cliente, sesion):
        assert respuesta.status_code == 401


def test_las_tres_son_indistinguibles(cliente, sesion):
    """Mismo codigo, mismo cuerpo, misma cabecera. Sin oraculo de cuentas."""
    respuestas = respuestas_de_fallo(cliente, sesion)

    cuerpos = {r.text for r in respuestas}
    desafios = {r.headers.get("www-authenticate") for r in respuestas}

    assert len(cuerpos) == 1
    assert len(desafios) == 1
    assert cuerpos.pop() == f'{{"detail":"{MENSAJE_CREDENCIALES_INVALIDAS}"}}'


def test_la_cuenta_inactiva_pasa_por_argon2id(cliente, sesion, monkeypatch):
    """No hay retorno temprano: el estado se mira despues de verificar el hash.

    Es la misma regla que protege al usuario inexistente. Si la cuenta
    desactivada respondiera antes de verificar, seria medible y confirmaria que
    el correo existe.
    """
    from app.services import principal as modulo_principal

    llamadas = []
    original = modulo_principal.verificar

    def espia(digest, clave):
        llamadas.append(digest)
        return original(digest, clave)

    monkeypatch.setattr(modulo_principal, "verificar", espia)

    sesion.fila = cuenta(activo=False)
    respuesta = cliente.post(RUTA_TOKEN, json={"email": EMAIL, "password": PASSWORD})

    assert respuesta.status_code == 401
    assert len(llamadas) == 1


def test_el_usuario_inexistente_tambien_pasa_por_argon2id(cliente, sesion, monkeypatch):
    from app.services import principal as modulo_principal

    llamadas = []
    original = modulo_principal.verificar
    monkeypatch.setattr(
        modulo_principal,
        "verificar",
        lambda d, c: (llamadas.append(d), original(d, c))[1],
    )

    sesion.fila = None
    cliente.post(RUTA_TOKEN, json={"email": "nadie@example.com", "password": PASSWORD})

    assert llamadas == [None]


def test_el_fallo_no_devuelve_el_correo_introducido(cliente, sesion):
    sesion.fila = None
    correo = "sondeo@example.com"

    respuesta = cliente.post(RUTA_TOKEN, json={"email": correo, "password": PASSWORD})

    assert correo not in respuesta.text


def test_el_fallo_lleva_desafio_bearer(cliente, sesion):
    sesion.fila = None

    respuesta = cliente.post(RUTA_TOKEN, json={"email": EMAIL, "password": PASSWORD})

    assert respuesta.headers["www-authenticate"] == DESAFIO_SIN_CREDENCIAL


def test_la_respuesta_nunca_lleva_el_hash(cliente, sesion):
    fila = cuenta()
    sesion.fila = fila

    for cuerpo in (
        {"email": EMAIL, "password": PASSWORD},
        {"email": EMAIL, "password": "incorrecta"},
    ):
        respuesta = cliente.post(RUTA_TOKEN, json=cuerpo)
        assert fila.password_hash not in respuesta.text
        assert "$argon2id$" not in respuesta.text


# ---------------------------------------------------------------------------
# 3. Cuerpo invalido
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cuerpo",
    [
        {},
        {"email": EMAIL},
        {"password": PASSWORD},
        {"email": "", "password": PASSWORD},
        {"email": EMAIL, "password": ""},
        {"email": EMAIL, "password": PASSWORD, "rol": "ADMIN"},
        {"email": "x" * 200, "password": PASSWORD},
    ],
    ids=[
        "vacio",
        "sin-password",
        "sin-email",
        "email-vacio",
        "password-vacia",
        "campo-desconocido",
        "email-demasiado-largo",
    ],
)
def test_un_cuerpo_invalido_es_422(cliente, sesion, cuerpo):
    sesion.fila = cuenta()

    assert cliente.post(RUTA_TOKEN, json=cuerpo).status_code == 422


def test_un_campo_de_rol_en_el_cuerpo_no_eleva_nada(cliente, sesion):
    """El cliente no decide su rol, ni siquiera intentandolo."""
    sesion.fila = cuenta()

    respuesta = cliente.post(
        RUTA_TOKEN, json={"email": EMAIL, "password": PASSWORD, "rol": "ADMIN"}
    )

    assert respuesta.status_code == 422


def test_la_contrasena_no_aparece_en_el_error_de_validacion(cliente, sesion):
    """Pydantic hace eco de los valores rechazados; ``SecretStr`` lo impide."""
    sesion.fila = cuenta()
    secreta = "contrasena-que-no-debe-aparecer"

    respuesta = cliente.post(RUTA_TOKEN, json={"password": secreta})

    assert respuesta.status_code == 422
    assert secreta not in respuesta.text


# ---------------------------------------------------------------------------
# 4. El contrato Bearer de las rutas protegidas
# ---------------------------------------------------------------------------


def test_sin_cabecera_es_401_con_desafio(cliente):
    respuesta = cliente.get(RUTA_YO)

    assert respuesta.status_code == 401
    assert respuesta.headers["www-authenticate"] == DESAFIO_SIN_CREDENCIAL
    assert respuesta.json()["detail"] == MENSAJE_NO_AUTENTICADO


@pytest.mark.parametrize(
    "cabecera",
    ["Basic YWJjOmRlZg==", "Token abc", "bearer", "Bearer", "", "JWT abc"],
    ids=["basic", "token", "bearer-sin-valor", "Bearer-sin-valor", "vacia", "jwt"],
)
def test_un_esquema_que_no_es_bearer_es_401(cliente, cabecera):
    respuesta = cliente.get(RUTA_YO, headers={"Authorization": cabecera})

    assert respuesta.status_code == 401
    assert respuesta.headers["www-authenticate"] == DESAFIO_SIN_CREDENCIAL


@pytest.mark.parametrize(
    "token", ["no.es.un.token", "abc", "a.b.c.d", "", "   "]
)
def test_un_token_malformado_es_401_invalid_token(cliente, token):
    respuesta = cliente.get(RUTA_YO, headers=bearer(token))

    assert respuesta.status_code == 401
    assert respuesta.headers["www-authenticate"] in (
        DESAFIO_SIN_CREDENCIAL,
        DESAFIO_TOKEN_INVALIDO,
    )


def test_una_firma_invalida_es_401_invalid_token(cliente, sesion):
    sesion.fila = cuenta()
    token = token_de()
    cabecera, cuerpo, firma = token.split(".")
    alterado = f"{cabecera}.{cuerpo}.{'A' if firma[0] != 'A' else 'B'}{firma[1:]}"

    respuesta = cliente.get(RUTA_YO, headers=bearer(alterado))

    assert respuesta.status_code == 401
    assert respuesta.headers["www-authenticate"] == DESAFIO_TOKEN_INVALIDO


def test_un_token_expirado_es_401_invalid_token(cliente, sesion):
    sesion.fila = cuenta()
    pasado = datetime.now(timezone.utc) - timedelta(hours=2)

    respuesta = cliente.get(RUTA_YO, headers=bearer(token_de(reloj=lambda: pasado)))

    assert respuesta.status_code == 401
    assert respuesta.headers["www-authenticate"] == DESAFIO_TOKEN_INVALIDO


def test_una_cuenta_borrada_es_401_aunque_el_token_valga(cliente, sesion):
    """El token verifica perfectamente; la cuenta ya no esta."""
    sesion.fila = None

    respuesta = cliente.get(RUTA_YO, headers=bearer(token_de()))

    assert respuesta.status_code == 401
    assert respuesta.headers["www-authenticate"] == DESAFIO_TOKEN_INVALIDO


def test_una_cuenta_desactivada_es_401_aunque_el_token_valga(cliente, sesion):
    sesion.fila = cuenta(activo=False)

    respuesta = cliente.get(RUTA_YO, headers=bearer(token_de()))

    assert respuesta.status_code == 401


def test_el_401_no_filtra_nada(cliente, sesion):
    sesion.fila = cuenta()
    token = token_de()

    respuesta = cliente.get(RUTA_YO, headers=bearer(token + "x"))

    for prohibido in (SECRETO, token, "jwt", "Signature", "argon2", "Traceback"):
        assert prohibido not in respuesta.text


# ---------------------------------------------------------------------------
# 5. /yo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rol", list(NombreRol))
def test_yo_devuelve_el_rol_vigente(cliente, sesion, rol):
    sesion.fila = cuenta(rol=rol)

    respuesta = cliente.get(RUTA_YO, headers=bearer(token_de()))

    assert respuesta.status_code == 200
    assert respuesta.json() == {"id_usuario": ID_USUARIO, "rol": rol.value}


def test_yo_devuelve_exactamente_dos_campos(cliente, sesion):
    sesion.fila = cuenta()

    cuerpo = cliente.get(RUTA_YO, headers=bearer(token_de())).json()

    assert set(cuerpo) == {"id_usuario", "rol"}


def test_yo_no_devuelve_el_hash_ni_el_estado(cliente, sesion):
    fila = cuenta()
    sesion.fila = fila

    texto = cliente.get(RUTA_YO, headers=bearer(token_de())).text

    assert fila.password_hash not in texto
    assert "activo" not in texto
    assert "email" not in texto


def test_el_rol_sale_de_la_base_no_del_token(cliente, sesion):
    """El mismo token, dos roles distintos: manda PostgreSQL."""
    token = token_de()

    sesion.fila = cuenta(rol=NombreRol.PACIENTE)
    primero = cliente.get(RUTA_YO, headers=bearer(token)).json()["rol"]

    sesion.fila = cuenta(rol=NombreRol.ADMIN)
    segundo = cliente.get(RUTA_YO, headers=bearer(token)).json()["rol"]

    assert primero == "PACIENTE"
    assert segundo == "ADMIN"


# ---------------------------------------------------------------------------
# 6. Rutas publicas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ruta", ["/health", "/openapi.json", "/docs", "/redoc"])
def test_las_rutas_publicas_siguen_siendo_publicas(cliente, ruta):
    assert cliente.get(ruta).status_code == 200


def test_el_login_es_publico(cliente, sesion):
    """Sin el, nadie podria autenticarse nunca."""
    sesion.fila = cuenta()

    assert cliente.post(RUTA_TOKEN, json={"email": EMAIL, "password": PASSWORD}).status_code == 200


def test_openapi_declara_el_esquema_bearer(cliente):
    esquemas = cliente.get("/openapi.json").json()["components"]["securitySchemes"]

    assert "BearerJWT" in esquemas
    assert esquemas["BearerJWT"]["scheme"] == "bearer"


def test_openapi_no_publica_ningun_secreto(cliente):
    texto = cliente.get("/openapi.json").text

    assert SECRETO not in texto
    assert "JWT_SECRET_KEY" not in texto


# ---------------------------------------------------------------------------
# 7. Fallo de auditoria en el login
# ---------------------------------------------------------------------------


def test_si_la_auditoria_del_login_falla_no_se_emite_token(cliente, sesion, auditoria):
    """Fallo cerrado: un acceso concedido sin rastro es lo que se quiere evitar."""
    from sqlalchemy.exc import SQLAlchemyError

    sesion.fila = cuenta()

    def estallar():
        raise SQLAlchemyError("fallo simulado")

    auditoria.commit = estallar

    respuesta = cliente.post(RUTA_TOKEN, json={"email": EMAIL, "password": PASSWORD})

    assert respuesta.status_code == 500
    assert respuesta.json()["detail"] == MENSAJE_ERROR_INTERNO
    assert "access_token" not in respuesta.text


def test_si_la_auditoria_del_fallo_falla_la_respuesta_sigue_siendo_401(
    cliente, sesion, auditoria
):
    """El 401 ya es la respuesta cerrada: no hay nada mas que cerrar."""
    from sqlalchemy.exc import SQLAlchemyError

    sesion.fila = None

    def estallar():
        raise SQLAlchemyError("fallo simulado")

    auditoria.commit = estallar

    respuesta = cliente.post(RUTA_TOKEN, json={"email": EMAIL, "password": PASSWORD})

    assert respuesta.status_code == 401
