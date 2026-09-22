"""Tres procesos, tres credenciales, y ningún respaldo entre ellas (SCRUM-98).

La API corre con un rol restringido, las migraciones con el rol que posee los
objetos y el ETL con un rol técnico propio. Lo que estas pruebas defienden no es
que las variables existan, sino lo contrario: que **ninguna cae sobre otra**.

Un respaldo silencioso a ``DATABASE_URL`` sería el fallo exacto que la
separación existe para evitar. En un sentido, las migraciones correrían con el
rol restringido y fallarían de forma ruidosa -- molesto pero visible. En el
otro, la API acabaría sirviendo con el rol que posee las tablas, y entonces
ninguna política de seguridad por filas la afectaría: tests en verde, cero
aislamiento y nada que lo delate.

Todas las URLs de este archivo son ficticias y no corresponden a ningún entorno.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import config
from app.config import (
    VARIABLE_URL_ALEMBIC,
    VARIABLE_URL_API,
    VARIABLE_URL_ETL,
    UrlDeEntornoInvalida,
    exigir_url_de_entorno,
)

RAIZ = Path(__file__).resolve().parents[2]

URL_FICTICIA = "postgresql+psycopg://usuario:clave_ficticia_de_prueba@localhost:5432/base"
URL_API_FICTICIA = "postgresql+psycopg://api_ficticia:clave_ficticia@localhost:5432/base"

VARIABLES_AJENAS_A_LA_API = (VARIABLE_URL_ALEMBIC, VARIABLE_URL_ETL)


@pytest.fixture
def entorno_limpio(monkeypatch):
    """Sin las variables y sin el ``.env`` del repositorio.

    Apuntar ``env_file`` a un archivo inexistente es lo que hace la prueba
    independiente de la máquina: si no, un ``.env`` que definiera la variable
    haría pasar una prueba cuyo objeto es precisamente su ausencia.
    """
    for variable in (VARIABLE_URL_API, *VARIABLES_AJENAS_A_LA_API):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(config, "ENV_FILE", "no_existe_a_proposito.env")


# ---------------------------------------------------------------------------
# 1. Sin respaldo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variable", VARIABLES_AJENAS_A_LA_API)
def test_una_variable_ausente_levanta_en_vez_de_usar_un_valor_por_omision(
    entorno_limpio, variable
):
    with pytest.raises(UrlDeEntornoInvalida) as excepcion:
        exigir_url_de_entorno(variable)

    assert variable in str(excepcion.value)


@pytest.mark.parametrize("variable", VARIABLES_AJENAS_A_LA_API)
def test_no_cae_sobre_database_url_aunque_este_definida(
    entorno_limpio, monkeypatch, variable
):
    """El caso que importa: hay una credencial privilegiada a mano y no se toma."""
    monkeypatch.setenv(VARIABLE_URL_API, URL_API_FICTICIA)

    with pytest.raises(UrlDeEntornoInvalida):
        exigir_url_de_entorno(variable)


@pytest.mark.parametrize("variable", VARIABLES_AJENAS_A_LA_API)
def test_el_mensaje_explica_por_que_no_hay_respaldo(entorno_limpio, variable):
    mensaje = str(pytest.raises(UrlDeEntornoInvalida, exigir_url_de_entorno, variable).value)

    assert "no** reutiliza DATABASE_URL" in mensaje or "reutiliza DATABASE_URL" in mensaje
    assert ".env" in mensaje


@pytest.mark.parametrize("variable", VARIABLES_AJENAS_A_LA_API)
@pytest.mark.parametrize("vacio", ["", "   ", "\t"])
def test_una_variable_en_blanco_cuenta_como_ausente(
    entorno_limpio, monkeypatch, variable, vacio
):
    monkeypatch.setenv(variable, vacio)

    with pytest.raises(UrlDeEntornoInvalida):
        exigir_url_de_entorno(variable)


# ---------------------------------------------------------------------------
# 2. Forma del valor, sin conectarse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variable", VARIABLES_AJENAS_A_LA_API)
def test_acepta_una_url_postgresql(entorno_limpio, monkeypatch, variable):
    monkeypatch.setenv(variable, URL_FICTICIA)

    assert exigir_url_de_entorno(variable) == URL_FICTICIA


@pytest.mark.parametrize("variable", VARIABLES_AJENAS_A_LA_API)
@pytest.mark.parametrize("url", ["sqlite:///archivo.db", "mysql://u:c@h/d"])
def test_rechaza_un_motor_que_no_es_postgresql(
    entorno_limpio, monkeypatch, variable, url
):
    """Se rechaza sobre la URL, antes de que exista un engine que cree algo."""
    monkeypatch.setenv(variable, url)

    with pytest.raises(UrlDeEntornoInvalida):
        exigir_url_de_entorno(variable)


@pytest.mark.parametrize("variable", VARIABLES_AJENAS_A_LA_API)
def test_el_valor_rechazado_nunca_aparece_en_el_mensaje(
    entorno_limpio, monkeypatch, variable
):
    """Una URL de conexión lleva credenciales; no se repite en ningún error."""
    clave = "clave_ficticia_que_no_debe_aparecer"
    monkeypatch.setenv(variable, f"sqlite:///usuario:{clave}@host/base")

    mensaje = str(pytest.raises(UrlDeEntornoInvalida, exigir_url_de_entorno, variable).value)

    assert clave not in mensaje
    assert "usuario" not in mensaje


def test_una_variable_desconocida_no_se_atiende(entorno_limpio):
    """Solo se reconocen las variables que este proyecto declara."""
    with pytest.raises(UrlDeEntornoInvalida):
        exigir_url_de_entorno("OTRA_DATABASE_URL")


# ---------------------------------------------------------------------------
# 3. La API no carga lo que no le corresponde
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variable", VARIABLES_AJENAS_A_LA_API)
def test_settings_no_declara_las_urls_de_los_otros_procesos(variable):
    """Si estuvieran en ``Settings``, el proceso web las cargaría al importar.

    ``app.config`` lo importa la API. Un campo aquí bastaría para que la
    credencial del migrador viviera en memoria del proceso web solo por existir
    en el entorno, que es exactamente lo que la separación quiere evitar. Se
    leen en una clase aparte, instanciada dentro de la función, y nada del
    camino de la API la llama.
    """
    assert variable.lower() not in config.Settings.model_fields


def test_settings_si_declara_la_url_de_la_api():
    assert VARIABLE_URL_API.lower() in config.Settings.model_fields


def test_el_valor_por_omision_de_database_url_no_es_el_propietario():
    """Un entorno sin configurar no debe arrancar con la credencial del dueño.

    El defecto anterior nombraba a ``fetalalert_dev``, que es el migrador y
    propietario de las tablas: olvidar la variable habría dado una API que omite
    toda política RLS, y el olvido no se habría notado.
    """
    defecto = config.Settings.model_fields["database_url"].default

    assert "fetalalert_dev" not in defecto
    assert "fetalalert_api" in defecto
    assert "sin_configurar" in defecto


@pytest.mark.parametrize("pedida", VARIABLES_AJENAS_A_LA_API)
def test_pedir_una_url_no_carga_la_otra(entorno_limpio, monkeypatch, pedida):
    """Un proceso solo debe tener a la vista la credencial que le toca.

    Se define la variable contraria con un valor reconocible y se comprueba que
    no aparece en el resultado: un modelo con los dos campos declarados las
    habría leído ambas.
    """
    otra = next(v for v in VARIABLES_AJENAS_A_LA_API if v != pedida)
    monkeypatch.setenv(pedida, URL_FICTICIA)
    monkeypatch.setenv(otra, "postgresql+psycopg://otra:otra@localhost:5432/otra")

    obtenida = exigir_url_de_entorno(pedida)

    assert obtenida == URL_FICTICIA
    assert "otra" not in obtenida


def test_la_api_importa_y_arranca_sin_las_urls_ajenas():
    """La prueba que de verdad importa: la API no las necesita.

    Se ejecuta en un subproceso con las dos variables explícitamente retiradas
    del entorno. Si ``app.main`` las exigiera -- directamente o a través de
    cualquier import -- el proceso terminaría distinto de cero. Que construya la
    aplicación es la evidencia de que la credencial del migrador no hace falta
    para servir, y por tanto de que no hay motivo para entregársela.
    """
    entorno = dict(os.environ)
    for variable in VARIABLES_AJENAS_A_LA_API:
        entorno.pop(variable, None)
    entorno["PYTHONPATH"] = str(RAIZ / "backend")
    entorno["PYTHONIOENCODING"] = "utf-8"
    entorno["JWT_SECRET_KEY"] = "clave_ficticia_de_prueba_con_longitud_suficiente"
    entorno[VARIABLE_URL_API] = URL_API_FICTICIA

    resultado = subprocess.run(
        [sys.executable, "-c", "from app.main import app; print(app.title)"],
        capture_output=True,
        text=True,
        env=entorno,
        cwd=str(RAIZ / "backend"),
    )

    assert resultado.returncode == 0, resultado.stderr


@pytest.mark.parametrize("variable", VARIABLES_AJENAS_A_LA_API)
def test_el_codigo_de_la_api_no_nombra_las_urls_ajenas(variable):
    """Ni ``app.main`` ni el arranque las mencionan: no son suyas."""
    for modulo in ("app/main.py", "app/db/session.py", "app/api/dependencias.py"):
        fuente = (RAIZ / "backend" / modulo).read_text(encoding="utf-8")
        assert variable not in fuente, modulo


# ---------------------------------------------------------------------------
# 4. El contrato del despliegue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variable", VARIABLES_AJENAS_A_LA_API)
def test_docker_compose_no_pasa_las_urls_ajenas_al_servicio_api(variable):
    """El proceso web no debe recibir la credencial del migrador ni la del ETL.

    Se busca en el bloque del servicio ``api``, no en el archivo entero: un
    comentario que explique por qué no se pasan es deseable y no debe romper
    esta prueba.
    """
    compose = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")
    bloque_api = compose.split("  api:", 1)[1]
    asignaciones = [
        linea for linea in bloque_api.splitlines() if linea.strip().startswith(variable)
    ]

    assert asignaciones == []


def test_docker_compose_si_pasa_database_url_al_servicio_api():
    compose = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")
    bloque_api = compose.split("  api:", 1)[1]

    assert any(
        linea.strip().startswith(VARIABLE_URL_API) for linea in bloque_api.splitlines()
    )


@pytest.mark.parametrize("variable", [VARIABLE_URL_API, *VARIABLES_AJENAS_A_LA_API])
def test_el_env_example_documenta_las_tres_variables(variable):
    ejemplo = (RAIZ / ".env.example").read_text(encoding="utf-8")

    assert f"{variable}=" in ejemplo


def test_el_env_example_no_trae_una_contrasena_real():
    """Los valores del ejemplo son ficticios y lo dicen."""
    ejemplo = (RAIZ / ".env.example").read_text(encoding="utf-8")

    assert "dev_only_change_me" in ejemplo
    assert "ficticias" in ejemplo
