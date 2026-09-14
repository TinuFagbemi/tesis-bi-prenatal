"""Configuracion de firma: fallo cerrado en la API, y en ningun sitio mas (SCRUM-70).

Dos propiedades que tiran en direcciones opuestas y las dos tienen que cumplirse:

* **la API no arranca** sin ``JWT_SECRET_KEY``, o con uno inservible;
* **Alembic, el ETL y el cargador si funcionan** sin esa variable, porque ni
  emiten ni verifican tokens y hacerlos depender de una credencial que no usan
  romperia las migraciones por un motivo ajeno.

Las dos se comprueban en subprocesos con el entorno limpio. Tiene que ser asi:
``app.config`` construye ``settings`` al importarse y ``app.main`` valida al
construir la aplicacion, de modo que dentro de esta sesion de pytest --donde
``conftest.py`` ya definio una clave ficticia y los modulos ya estan importados--
no hay forma honesta de observar el arranque en frio.

Todos los valores son ficticios y completamente simulados.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import timedelta

import pytest
from pydantic import SecretStr, ValidationError

from app.config import (
    EXPIRACION_MAXIMA_MINUTOS,
    EXPIRACION_MINIMA_MINUTOS,
    EXPIRACION_POR_OMISION_MINUTOS,
    LONGITUD_MINIMA_DEL_SECRETO,
    MENSAJE_SECRETO_AUSENTE,
    MENSAJE_SECRETO_INVALIDO,
    ConfiguracionJWT,
    ConfiguracionJWTInvalida,
    Settings,
    exigir_configuracion_jwt,
)
from tests.conftest import DIRECTORIO_BACKEND

SECRETO_VALIDO = "secreto-ficticio-de-pruebas-con-longitud-mas-que-suficiente"
URL_FICTICIA = "postgresql+psycopg://usuario:clave_ficticia_de_prueba@localhost:5432/base"


def entorno_sin_jwt() -> dict:
    """Copia del entorno con la variable de firma retirada.

    ``PYTHONPATH`` apunta a ``backend/`` para que el subproceso importe ``app``
    igual que lo hace pytest. ``.env`` del repositorio podria traer la variable,
    asi que se comprueba y la prueba se omite en vez de mentir si la trae.
    """
    entorno = dict(os.environ)
    entorno.pop("JWT_SECRET_KEY", None)
    entorno["PYTHONPATH"] = str(DIRECTORIO_BACKEND)
    entorno["PYTHONIOENCODING"] = "utf-8"
    return entorno


# Renderiza la cadena de migraciones sin importar ``tests/conftest.py``. Importarlo
# seria hacer trampa: ese archivo define ``JWT_SECRET_KEY`` antes de importar
# ``app``, y la prueba dejaria de comprobar lo que dice. Antes de renderizar se
# confirma, dentro del propio subproceso, que la configuracion no tiene secreto.
RENDERIZAR_ALEMBIC_SIN_CONFTEST = (
    "from app.config import settings;"
    "assert settings.jwt_secret_key is None, 'el subproceso tiene secreto';"
    "from alembic.config import Config;"
    "from alembic import command;"
    "c = Config(); c.set_main_option('script_location', 'alembic');"
    "command.upgrade(c, 'head', sql=True)"
)


def ejecutar(codigo: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", codigo],
        capture_output=True,
        text=True,
        env=entorno_sin_jwt(),
        cwd=str(DIRECTORIO_BACKEND),
    )


@pytest.fixture(scope="module", autouse=True)
def sin_secreto_en_el_env_file():
    """Si el ``.env`` local define la variable, estas pruebas no pueden afirmar nada."""
    archivo = DIRECTORIO_BACKEND.parent / ".env"
    if archivo.exists() and "JWT_SECRET_KEY" in archivo.read_text(encoding="utf-8"):
        pytest.skip("El .env local define JWT_SECRET_KEY; no se puede probar su ausencia.")


# ---------------------------------------------------------------------------
# 1. La API falla cerrado
# ---------------------------------------------------------------------------


def test_la_api_no_arranca_sin_secreto():
    resultado = ejecutar("import app.main")

    assert resultado.returncode != 0
    assert "ConfiguracionJWTInvalida" in resultado.stderr


def test_el_mensaje_de_arranque_explica_que_hacer():
    resultado = ejecutar("import app.main")

    assert "JWT_SECRET_KEY" in resultado.stderr
    assert "token_urlsafe" in resultado.stderr


def test_la_api_arranca_con_un_secreto_valido():
    entorno = entorno_sin_jwt()
    entorno["JWT_SECRET_KEY"] = SECRETO_VALIDO

    resultado = subprocess.run(
        [sys.executable, "-c", "from app.main import app; print(len(app.routes))"],
        capture_output=True,
        text=True,
        env=entorno,
        cwd=str(DIRECTORIO_BACKEND),
    )

    assert resultado.returncode == 0, resultado.stderr


# ---------------------------------------------------------------------------
# 2. Lo que no es la API sigue funcionando
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "modulo",
    ["app.config", "app.models", "app.etl.modelos", "app.loader", "app.edge"],
)
def test_los_modulos_ajenos_a_la_autenticacion_importan_sin_secreto(modulo):
    resultado = ejecutar(f"import {modulo}")

    assert resultado.returncode == 0, resultado.stderr


def test_alembic_renderiza_la_migracion_sin_secreto():
    """``alembic upgrade head --sql`` es lo que la suite de migraciones ejerce.

    Importa ``alembic/env.py``, que trae ``app.config``, ``app.models`` y
    ``app.etl.modelos``. Si la variable fuese obligatoria en la configuracion
    compartida, las migraciones dejarian de poder renderizarse.
    """
    resultado = ejecutar(RENDERIZAR_ALEMBIC_SIN_CONFTEST)

    assert resultado.returncode == 0, resultado.stderr


def test_el_etl_y_el_cargador_se_importan_sin_secreto():
    resultado = ejecutar(
        "import app.etl, app.loader; print(bool(app.etl and app.loader))"
    )

    assert resultado.returncode == 0, resultado.stderr


# ---------------------------------------------------------------------------
# 3. Validacion del secreto
# ---------------------------------------------------------------------------


def configuracion_con(secreto: str | None, **extra) -> Settings:
    return Settings(
        jwt_secret_key=None if secreto is None else SecretStr(secreto),
        database_url=extra.pop("database_url", URL_FICTICIA),
        **extra,
    )


def test_sin_secreto_se_rechaza():
    with pytest.raises(ConfiguracionJWTInvalida) as capturado:
        exigir_configuracion_jwt(configuracion_con(None))

    assert str(capturado.value) == MENSAJE_SECRETO_AUSENTE


# Sin la longitud 0: desde el code review de SCRUM-70 una cadena vacia no es un
# secreto corto sino un secreto ausente, y la cubren las pruebas «en blanco».
@pytest.mark.parametrize("longitud", [1, 8, 31])
def test_un_secreto_corto_se_rechaza(longitud):
    with pytest.raises(ConfiguracionJWTInvalida) as capturado:
        exigir_configuracion_jwt(configuracion_con("x" * longitud))

    assert str(capturado.value) == MENSAJE_SECRETO_INVALIDO


def test_el_minimo_se_mide_en_bytes_no_en_caracteres():
    """Treinta y un caracteres de tres bytes pasan; treinta y uno ASCII no."""
    ancho = "€" * 11  # 33 bytes en UTF-8
    assert len(ancho) < LONGITUD_MINIMA_DEL_SECRETO
    assert len(ancho.encode("utf-8")) >= LONGITUD_MINIMA_DEL_SECRETO

    assert exigir_configuracion_jwt(configuracion_con(ancho)).secreto == ancho


def test_el_minimo_exacto_se_acepta():
    justo = "y" * LONGITUD_MINIMA_DEL_SECRETO

    assert exigir_configuracion_jwt(configuracion_con(justo)).secreto == justo


def test_reutilizar_la_contrasena_de_postgresql_se_rechaza():
    """Una fuga no debe convertirse en dos."""
    with pytest.raises(ConfiguracionJWTInvalida) as capturado:
        exigir_configuracion_jwt(
            configuracion_con(
                "clave_ficticia_de_prueba".ljust(LONGITUD_MINIMA_DEL_SECRETO, "0"),
                database_url="postgresql+psycopg://u:"
                + "clave_ficticia_de_prueba".ljust(LONGITUD_MINIMA_DEL_SECRETO, "0")
                + "@localhost:5432/base",
            )
        )

    assert str(capturado.value) == MENSAJE_SECRETO_INVALIDO


def test_el_mensaje_de_rechazo_es_generico():
    """No dice cual de las dos reglas fallo, ni muestra ningun valor.

    Distinguir "es corto" de "es igual a la contrasena de PostgreSQL" pondria en
    un log una afirmacion util sobre el secreto. La ausencia si se nombra con
    precision, porque "no esta definida" no revela nada.
    """
    corto = pytest.raises(ConfiguracionJWTInvalida)
    reutilizado = pytest.raises(ConfiguracionJWTInvalida)

    with corto:
        exigir_configuracion_jwt(configuracion_con("corto"))
    with reutilizado:
        exigir_configuracion_jwt(
            configuracion_con(
                "clave_ficticia_de_prueba".ljust(40, "0"),
                database_url="postgresql+psycopg://u:"
                + "clave_ficticia_de_prueba".ljust(40, "0")
                + "@h:5432/b",
            )
        )

    assert str(corto.excinfo.value) == str(reutilizado.excinfo.value)


def test_una_url_ilegible_no_impide_arrancar():
    """Si la URL no se puede leer, no hay contrasena que comparar."""
    configuracion = configuracion_con(SECRETO_VALIDO, database_url="esto-no-es-una-url")

    assert exigir_configuracion_jwt(configuracion).secreto == SECRETO_VALIDO


# ---------------------------------------------------------------------------
# 4. Vigencia del token
# ---------------------------------------------------------------------------


def test_la_expiracion_por_omision_es_de_treinta_minutos():
    configuracion = exigir_configuracion_jwt(configuracion_con(SECRETO_VALIDO))

    assert configuracion.expiracion == timedelta(minutes=EXPIRACION_POR_OMISION_MINUTOS)
    assert EXPIRACION_POR_OMISION_MINUTOS == 30


@pytest.mark.parametrize("minutos", [EXPIRACION_MINIMA_MINUTOS, 30, EXPIRACION_MAXIMA_MINUTOS])
def test_los_valores_del_rango_se_aceptan(minutos):
    configuracion = configuracion_con(SECRETO_VALIDO, jwt_expiration_minutes=minutos)

    assert exigir_configuracion_jwt(configuracion).expiracion == timedelta(minutes=minutos)


@pytest.mark.parametrize("minutos", [0, -1, EXPIRACION_MAXIMA_MINUTOS + 1, 100_000])
def test_los_valores_fuera_del_rango_se_rechazan(minutos):
    """Un token nacido expirado, o uno que vive mas de un dia sin revocacion."""
    with pytest.raises(ValidationError):
        configuracion_con(SECRETO_VALIDO, jwt_expiration_minutes=minutos)


# ---------------------------------------------------------------------------
# 5. El secreto no se imprime
# ---------------------------------------------------------------------------


def test_el_secreto_no_aparece_en_el_repr_de_la_configuracion():
    configuracion = configuracion_con(SECRETO_VALIDO)

    for texto in (repr(configuracion), str(configuracion), str(configuracion.model_dump())):
        assert SECRETO_VALIDO not in texto


def test_el_secreto_esta_enmascarado():
    assert "**" in repr(configuracion_con(SECRETO_VALIDO).jwt_secret_key)


def test_el_secreto_no_aparece_en_el_mensaje_de_rechazo():
    with pytest.raises(ConfiguracionJWTInvalida) as capturado:
        exigir_configuracion_jwt(configuracion_con("corto"))

    assert "corto" not in str(capturado.value)


def test_el_secreto_no_aparece_en_la_salida_de_arranque():
    """Ni en stdout ni en stderr, aunque el proceso arranque correctamente."""
    entorno = entorno_sin_jwt()
    entorno["JWT_SECRET_KEY"] = SECRETO_VALIDO

    resultado = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.config import settings; from app.main import app; print(settings)",
        ],
        capture_output=True,
        text=True,
        env=entorno,
        cwd=str(DIRECTORIO_BACKEND),
    )

    assert resultado.returncode == 0, resultado.stderr
    assert SECRETO_VALIDO not in resultado.stdout
    assert SECRETO_VALIDO not in resultado.stderr


def test_el_env_de_ejemplo_no_trae_un_secreto_copiable():
    """``.env.example`` documenta la variable sin ofrecer un valor utilizable."""
    ejemplo = DIRECTORIO_BACKEND.parent / ".env.example"
    contenido = ejemplo.read_text(encoding="utf-8")

    assert "JWT_SECRET_KEY" in contenido

    for linea in contenido.splitlines():
        if linea.startswith("JWT_SECRET_KEY="):
            valor = linea.split("=", 1)[1].strip()
            assert len(valor.encode("utf-8")) < LONGITUD_MINIMA_DEL_SECRETO, (
                "El ejemplo no debe traer un secreto que funcione."
            )


# ---------------------------------------------------------------------------
# 6. ConfiguracionJWT no muestra el secreto (code review de SCRUM-70)
# ---------------------------------------------------------------------------

MARCADOR_SECRETO = "MARCADOR-DE-SECRETO-QUE-NO-DEBE-VERSE-0123456789"


def test_el_repr_de_la_configuracion_validada_no_muestra_el_secreto():
    """Antes de la correccion, el ``repr`` del dataclass lo imprimia entero."""
    configuracion = ConfiguracionJWT(
        secreto=MARCADOR_SECRETO, expiracion=timedelta(minutes=30)
    )

    assert MARCADOR_SECRETO not in repr(configuracion)
    assert MARCADOR_SECRETO not in str(configuracion)
    assert "expiracion" in repr(configuracion)


def test_la_configuracion_validada_sigue_entregando_el_secreto_a_quien_lo_pide():
    """Fuera del ``repr``, no fuera del objeto: firmar sigue necesitandolo."""
    configuracion = exigir_configuracion_jwt(configuracion_con(MARCADOR_SECRETO))

    assert configuracion.secreto == MARCADOR_SECRETO
    assert MARCADOR_SECRETO not in repr(configuracion)


@pytest.mark.parametrize(
    "secreto",
    ["corto-MARCADOR", "   ", ""],
    ids=["demasiado-corto", "en-blanco", "vacio"],
)
def test_ningun_rechazo_interpola_el_valor(secreto):
    with pytest.raises(ConfiguracionJWTInvalida) as capturado:
        exigir_configuracion_jwt(configuracion_con(secreto))

    mensaje = str(capturado.value)
    assert "MARCADOR" not in mensaje
    assert repr(secreto) not in mensaje


# ---------------------------------------------------------------------------
# 7. Una variable vacia o en blanco es una variable ausente
# ---------------------------------------------------------------------------

EN_BLANCO = ["", " ", "   ", "\t", "\n", " \t\r\n "]
IDS_EN_BLANCO = ["vacio", "un-espacio", "espacios", "tabulador", "salto", "mezcla"]


@pytest.mark.parametrize("valor", EN_BLANCO, ids=IDS_EN_BLANCO)
def test_jwt_en_blanco_se_trata_como_ausente(valor):
    configuracion = configuracion_con(valor)

    assert configuracion.jwt_secret_key is None
    with pytest.raises(ConfiguracionJWTInvalida) as capturado:
        exigir_configuracion_jwt(configuracion)
    assert str(capturado.value) == MENSAJE_SECRETO_AUSENTE


@pytest.mark.parametrize("valor", EN_BLANCO, ids=IDS_EN_BLANCO)
def test_jwt_en_blanco_como_secretstr_tambien_es_ausente(valor):
    assert Settings(jwt_secret_key=SecretStr(valor)).jwt_secret_key is None


def test_un_secreto_no_vacio_no_se_recorta():
    """Recortarlo firmaria con una clave distinta de la configurada."""
    con_espacios = "  " + "k" * 40 + "  "

    configuracion = exigir_configuracion_jwt(configuracion_con(con_espacios))

    assert configuracion.secreto == con_espacios


def test_jwt_vacio_en_el_archivo_env_es_ausente(tmp_path, monkeypatch):
    """El caso real: copiar ``.env.example`` tal cual deja la linea vacia."""
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    archivo = tmp_path / ".env"
    archivo.write_text("JWT_SECRET_KEY=\n", encoding="utf-8")

    configuracion = Settings(_env_file=archivo)

    assert configuracion.jwt_secret_key is None
    with pytest.raises(ConfiguracionJWTInvalida) as capturado:
        exigir_configuracion_jwt(configuracion)
    assert str(capturado.value) == MENSAJE_SECRETO_AUSENTE


def test_la_api_no_arranca_con_jwt_en_blanco_y_lo_reporta_como_ausencia():
    entorno = entorno_sin_jwt()
    entorno["JWT_SECRET_KEY"] = "   "

    resultado = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        capture_output=True,
        text=True,
        env=entorno,
        cwd=str(DIRECTORIO_BACKEND),
    )

    assert resultado.returncode != 0
    assert "Falta la variable de entorno JWT_SECRET_KEY" in resultado.stderr


def test_alembic_y_el_etl_siguen_sin_depender_de_jwt_en_blanco():
    entorno = entorno_sin_jwt()
    entorno["JWT_SECRET_KEY"] = "   "

    resultado = subprocess.run(
        [sys.executable, "-c", "import app.etl, app.loader;" + RENDERIZAR_ALEMBIC_SIN_CONFTEST],
        capture_output=True,
        text=True,
        env=entorno,
        cwd=str(DIRECTORIO_BACKEND),
    )

    assert resultado.returncode == 0, resultado.stderr
