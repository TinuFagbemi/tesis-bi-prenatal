"""Contrato de Adminer en Docker Compose (SCRUM-103).

Adminer es una herramienta auxiliar de inspección visual para la sustentación,
no un componente de FetalAlert. Lo que estas pruebas defienden es justamente eso:

- pertenece al perfil ``demo``: un ``docker compose up`` ordinario, sin perfiles,
  no lo arranca, y el sistema funciona igual sin él;
- ningún servicio de FetalAlert depende de Adminer, y Adminer no depende de la
  API, de modo que detener ``api`` no lo afecta;
- se publica solo en la interfaz local, con una imagen de versión fija y sin
  plugins, temas ni credenciales;
- ``PGOPTIONS`` abre sus sesiones en solo lectura por omisión, y esa variable
  no llega a ningún otro servicio.

Se comprueba de dos maneras complementarias. La primera lee el archivo por
bloques de servicio y descarta los comentarios, para que una cadena escrita en
un comentario no haga pasar la prueba. La segunda pide a Docker Compose el
modelo resuelto, con y sin el perfil, que es lo que de verdad se ejecuta.

El modelo se resuelve con valores ficticios y con un ``--env-file`` vacío, para
que ningún ``.env`` local intervenga, y no se imprime nunca.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
COMPOSE = RAIZ / "docker-compose.yml"

IMAGEN_ADMINER = "adminer:6.1.1-standalone"
PERFIL_DEMO = "demo"
PGOPTIONS_SOLO_LECTURA = "-c default_transaction_read_only=on"

# Valores ficticios, exclusivos de esta comprobación. No corresponden a ningún
# entorno.
VARIABLES_FICTICIAS = {
    "POSTGRES_USER": "compose_adminer",
    "POSTGRES_PASSWORD": "compose_adminer_no_es_secreto",
    "POSTGRES_DB": "compose_adminer",
    "DATABASE_URL": (
        "postgresql+psycopg://compose_adminer:"
        "compose_adminer_no_es_secreto@db:5432/compose_adminer"
    ),
}

# Prefijos que la prueba controla: se retiran del entorno heredado para que el
# resultado no dependa de la máquina. COMPOSE_ incluye COMPOSE_PROFILES, que
# activaría el perfil sin pedirlo.
PREFIJOS_CONTROLADOS = (
    "ADMINER_",
    "APP_",
    "COMPOSE_",
    "DATABASE_URL",
    "EDGE_",
    "JWT_",
    "PG",
    "POSTGRES_",
)


# ---------------------------------------------------------------------------
# 1. El archivo, leído por bloques de servicio
# ---------------------------------------------------------------------------


def _sin_comentario(linea: str) -> str:
    """La línea sin su comentario. El archivo no usa ``#`` dentro de valores."""
    if linea.lstrip().startswith("#"):
        return ""
    return linea.split(" #", 1)[0].rstrip()


def _bloques_de_servicios() -> dict[str, list[str]]:
    """Cada servicio de ``services:`` con sus líneas útiles, sin comentarios.

    Un servicio empieza en una clave con exactamente dos espacios de sangría y
    termina en la siguiente, o en la siguiente clave de primer nivel.
    """
    bloques: dict[str, list[str]] = {}
    dentro_de_services = False
    actual: str | None = None

    for cruda in COMPOSE.read_text(encoding="utf-8").splitlines():
        linea = _sin_comentario(cruda)
        if not linea.strip():
            continue
        if not linea.startswith(" "):
            dentro_de_services = linea == "services:"
            actual = None
            continue
        if not dentro_de_services:
            continue
        if linea.startswith("  ") and not linea.startswith("   "):
            actual = linea.strip().rstrip(":")
            bloques[actual] = []
            continue
        if actual is not None:
            bloques[actual].append(linea.strip())

    return bloques


def test_el_archivo_declara_db_api_y_adminer():
    assert set(_bloques_de_servicios()) == {"db", "api", "adminer"}


def test_adminer_usa_la_imagen_fija_y_el_perfil_demo():
    bloque = _bloques_de_servicios()["adminer"]

    assert f"image: {IMAGEN_ADMINER}" in bloque
    assert f'profiles: ["{PERFIL_DEMO}"]' in bloque


def test_adminer_se_publica_solo_en_la_interfaz_local():
    bloque = _bloques_de_servicios()["adminer"]

    assert '- "127.0.0.1:8090:8080"' in bloque


def test_adminer_no_recibe_credenciales_plugins_ni_tema():
    """Ninguna interpolación de variables y nada que parezca una credencial."""
    texto = "\n".join(_bloques_de_servicios()["adminer"])

    for prohibido in ("${", "PASSWORD", "_URL", "POSTGRES_", "ADMINER_", "volumes:"):
        assert prohibido not in texto, prohibido


def test_ningun_otro_servicio_menciona_a_adminer_ni_pgoptions():
    """Ni dependencias hacia Adminer ni la variable de solo lectura fuera de él.

    Se buscan solo líneas útiles: un comentario que explique la herramienta es
    deseable y no debe romper esta prueba.
    """
    for servicio, bloque in _bloques_de_servicios().items():
        if servicio == "adminer":
            continue
        texto = "\n".join(bloque)
        assert "adminer" not in texto.lower(), servicio
        assert "PGOPTIONS" not in texto, servicio


# ---------------------------------------------------------------------------
# 2. El modelo resuelto por Docker Compose
# ---------------------------------------------------------------------------


def _compose_disponible() -> bool:
    if shutil.which("docker") is None:
        return False
    resultado = subprocess.run(
        ["docker", "compose", "version"], capture_output=True, text=True
    )
    return resultado.returncode == 0


requiere_compose = pytest.mark.skipif(
    not _compose_disponible(), reason="Docker Compose no está disponible"
)


def _resolver(*argumentos: str) -> dict:
    """Modelo resuelto con valores ficticios. No se imprime nunca."""
    entorno = {
        clave: valor
        for clave, valor in os.environ.items()
        if not clave.startswith(PREFIJOS_CONTROLADOS)
    }
    entorno.update(VARIABLES_FICTICIAS)

    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as vacio:
        env_file_vacio = vacio.name
    try:
        resultado = subprocess.run(
            [
                "docker", "compose",
                "-f", str(COMPOSE),
                "--env-file", env_file_vacio,
                *argumentos,
                "config", "--format", "json",
            ],
            cwd=RAIZ,
            env=entorno,
            capture_output=True,
            text=True,
        )
    finally:
        os.unlink(env_file_vacio)

    # Solo el código de salida: la salida contiene valores de entorno.
    assert resultado.returncode == 0, (
        f"docker compose config falló con código {resultado.returncode}"
    )
    return json.loads(resultado.stdout)


@pytest.fixture(scope="module")
def modelo_normal() -> dict:
    return _resolver()


@pytest.fixture(scope="module")
def modelo_demo() -> dict:
    return _resolver("--profile", PERFIL_DEMO)


@requiere_compose
def test_sin_el_perfil_adminer_no_forma_parte_del_proyecto(modelo_normal):
    assert set(modelo_normal["services"]) == {"db", "api"}


@requiere_compose
def test_con_el_perfil_adminer_se_suma_a_db_y_api(modelo_demo):
    assert set(modelo_demo["services"]) == {"db", "api", "adminer"}


@requiere_compose
def test_solo_adminer_pertenece_al_perfil(modelo_demo):
    servicios = modelo_demo["services"]

    assert servicios["adminer"].get("profiles") == [PERFIL_DEMO]
    assert not servicios["db"].get("profiles")
    assert not servicios["api"].get("profiles")


@requiere_compose
def test_adminer_resuelto_usa_la_imagen_fija(modelo_demo):
    adminer = modelo_demo["services"]["adminer"]

    assert adminer["image"] == IMAGEN_ADMINER
    assert not adminer["image"].endswith(":latest")
    assert "build" not in adminer
    assert not adminer.get("volumes")


@requiere_compose
def test_adminer_resuelto_se_publica_solo_en_loopback(modelo_demo):
    puertos = modelo_demo["services"]["adminer"]["ports"]

    assert len(puertos) == 1
    assert puertos[0]["host_ip"] == "127.0.0.1"
    assert str(puertos[0]["published"]) == "8090"
    assert int(puertos[0]["target"]) == 8080


@requiere_compose
def test_adminer_depende_solo_de_db(modelo_demo):
    """Detener la API no puede afectar a Adminer."""
    assert set(modelo_demo["services"]["adminer"]["depends_on"]) == {"db"}


@requiere_compose
def test_ningun_servicio_de_fetalalert_depende_de_adminer(modelo_demo):
    for nombre in ("db", "api"):
        assert "adminer" not in (modelo_demo["services"][nombre].get("depends_on") or {})


@requiere_compose
def test_adminer_solo_recibe_la_opcion_de_solo_lectura(modelo_demo):
    entorno = modelo_demo["services"]["adminer"].get("environment") or {}

    assert entorno == {"PGOPTIONS": PGOPTIONS_SOLO_LECTURA}


@requiere_compose
def test_pgoptions_no_llega_a_ningun_otro_servicio(modelo_demo):
    for nombre in ("db", "api"):
        entorno = modelo_demo["services"][nombre].get("environment") or {}
        assert "PGOPTIONS" not in entorno, nombre
