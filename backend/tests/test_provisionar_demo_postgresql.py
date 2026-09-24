"""El aprovisionamiento del dispositivo simulado, contra PostgreSQL real (SCRUM-72).

Se omite salvo que esté definida ``SCRUM72_PROVISION_TEST_DATABASE_URL``, y esa
variable debe apuntar a una base **desechable** cuyo nombre empiece por
``scrum72_``. Nunca cae por omisión sobre ``ALEMBIC_DATABASE_URL`` ni sobre
``DATABASE_URL``: lo que esta suite ejercita **escribe filas clínicas**, y
dirigirla a la base de trabajo de alguien sería exactamente el accidente que la
guarda existe para impedir.

La base ya debe estar migrada a head y con el dataset simulado cargado, igual
que ``test_load_mock_data_postgresql.py``: preparar la base es responsabilidad
de quien lanza las pruebas.

**Lo que aquí se demuestra, y por qué importa.** ``scripts/provisionar_demo.py``
es el único componente de SCRUM-72 que habla con PostgreSQL, y existe por una
razón concreta: los embarazos ``ACTIVO`` del dataset canónico empezaron en 2025,
así que hoy van por la semana 50 o más y el contrato de ingesta solo admite
1-42. Ninguna sesión capturada hoy podría ingresarse contra ellos. El
aprovisionamiento agrega un episodio anclado al reloj real **sin tocar una sola
fila canónica**, y eso último es lo que estas pruebas vigilan fila por fila.

Ejemplo de invocación, contra una base desechable::

    $env:SCRUM72_PROVISION_TEST_DATABASE_URL = "postgresql+psycopg://<usuario>:<clave>@127.0.0.1:<puerto>/scrum72_provision_tmp"
    python -m pytest tests/test_provisionar_demo_postgresql.py

Todos los datos y credenciales implicados son ficticios y efímeros.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

VARIABLE_DE_ENTORNO = "SCRUM72_PROVISION_TEST_DATABASE_URL"
PATRON_DE_BASE = re.compile(r"^scrum72_[a-z0-9_]{1,40}$")

RAIZ = Path(__file__).resolve().parents[2]
RUNNER = RAIZ / "scripts" / "provisionar_demo.py"

pytestmark = pytest.mark.skipif(
    not os.environ.get(VARIABLE_DE_ENTORNO),
    reason=(
        f"Define {VARIABLE_DE_ENTORNO} apuntando a una base PostgreSQL "
        "desechable, ya migrada y con el dataset simulado cargado. Esta suite "
        "escribe filas clínicas."
    ),
)

# Tablas del dataset canónico que el aprovisionamiento **no** debe tocar. Se
# comprueban por conteo y, las que importan, fila por fila.
TABLAS_INTOCABLES = (
    "clinica",
    "paciente",
    "medico",
    "usuario",
    "sesion_monitoreo",
    "lectura_biometrica",
    "semaforo",
    "tiempo_gestacional",
    "medico_clinica",
)


def cargar_script():
    """El script de aprovisionamiento como módulo, para leer sus constantes.

    Se importan en vez de repetirse: una prueba que escribiera ``28`` o
    ``DEMO-SCRUM72-01`` a mano seguiría pasando el día que el script cambiara
    de valor, que es justo cuando debería fallar.
    """
    especificacion = importlib.util.spec_from_file_location(
        "provisionar_demo_bajo_prueba", RUNNER
    )
    modulo = importlib.util.module_from_spec(especificacion)
    sys.modules[especificacion.name] = modulo
    especificacion.loader.exec_module(modulo)
    return modulo


@pytest.fixture(scope="module")
def script():
    return cargar_script()


@pytest.fixture(scope="module")
def url_de_pruebas() -> str:
    url = os.environ[VARIABLE_DE_ENTORNO]
    nombre = make_url(url).database or ""
    if not PATRON_DE_BASE.fullmatch(nombre):
        pytest.fail(
            f"{VARIABLE_DE_ENTORNO} debe apuntar a una base desechable cuyo "
            "nombre empiece por 'scrum72_'. Esta suite escribe filas clínicas."
        )
    return url


@pytest.fixture(scope="module")
def engine(url_de_pruebas):
    motor = create_engine(url_de_pruebas, poolclass=NullPool, future=True)
    with motor.connect() as conexion:
        cargadas = conexion.execute(
            text("SELECT count(*) FROM operacional.lectura_biometrica")
        ).scalar_one()
    if not cargadas:
        motor.dispose()
        pytest.fail(
            "La base indicada no tiene el dataset simulado cargado. Ejecuta "
            "alembic y scripts/load_mock_data.py antes de esta suite."
        )
    yield motor
    motor.dispose()


def _ejecutar(url: str, ruta_provision: Path, orden: str = "provisionar"):
    """Invoca el script como lo haría una persona: por línea de comandos."""
    entorno = dict(os.environ)
    entorno["ALEMBIC_DATABASE_URL"] = url
    entorno["GESTANTE_PROVISION_PATH"] = str(ruta_provision)
    entorno["PYTHONPATH"] = str(RAIZ / "backend")
    entorno["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(RUNNER), orden],
        capture_output=True,
        text=True,
        env=entorno,
        cwd=str(RAIZ),
    )


def _conteos(conexion, tablas=TABLAS_INTOCABLES) -> dict[str, int]:
    return {
        tabla: conexion.execute(
            text(f"SELECT count(*) FROM operacional.{tabla}")
        ).scalar_one()
        for tabla in tablas
    }


def _huella_de_embarazos(conexion) -> list[tuple]:
    """Las filas de ``embarazo`` tal como están, ordenadas.

    Un conteo solo detecta altas y bajas. Esto detecta además una modificación:
    si el aprovisionamiento cambiara el estado o la fecha de un episodio
    canónico para «hacerle sitio» al suyo, la huella cambiaría.
    """
    filas = conexion.execute(
        text(
            "SELECT id_embarazo, id_paciente, id_clinica, fecha_inicio,"
            "       fecha_probable_parto, estado_embarazo, fecha_cierre"
            "  FROM operacional.embarazo ORDER BY id_embarazo"
        )
    ).all()
    return [tuple(fila) for fila in filas]


@pytest.fixture(scope="module")
def estado_inicial(engine):
    """Fotografía del dataset canónico antes de aprovisionar nada."""
    with engine.connect() as conexion:
        demo = conexion.execute(
            text(
                "SELECT count(*) FROM operacional.dispositivo"
                " WHERE codigo_dispositivo = :codigo"
            ),
            {"codigo": cargar_script().CODIGO_DISPOSITIVO_DEMO},
        ).scalar_one()
        if demo:
            pytest.fail(
                "La base ya tiene un episodio de demostración. Esta suite "
                "necesita una base desechable recién cargada."
            )
        return {
            "conteos": _conteos(conexion),
            "embarazos": _huella_de_embarazos(conexion),
        }


@pytest.fixture(scope="module")
def primera_ejecucion(engine, url_de_pruebas, estado_inicial, tmp_path_factory):
    """Aprovisiona una vez y deja a mano el archivo y la salida."""
    ruta = tmp_path_factory.mktemp("provision") / "provision.json"
    resultado = _ejecutar(url_de_pruebas, ruta)
    assert resultado.returncode == 0, resultado.stderr
    return {"ruta": ruta, "salida": resultado.stdout}


# ---------------------------------------------------------------------------
# Creación
# ---------------------------------------------------------------------------


def test_la_primera_ejecucion_crea_el_episodio_de_demostracion(
    engine, script, primera_ejecucion
):
    with engine.connect() as conexion:
        fila = conexion.execute(
            text(
                "SELECT d.id_dispositivo, d.estado, ad.id_embarazo,"
                "       e.estado_embarazo, e.fecha_inicio"
                "  FROM operacional.dispositivo d"
                "  JOIN operacional.asignacion_dispositivo ad"
                "    ON ad.id_dispositivo = d.id_dispositivo"
                "  JOIN operacional.embarazo e ON e.id_embarazo = ad.id_embarazo"
                " WHERE d.codigo_dispositivo = :codigo"
            ),
            {"codigo": script.CODIGO_DISPOSITIVO_DEMO},
        ).one()

    assert fila.estado_embarazo == "ACTIVO"
    assert fila.estado == "ASIGNADO"


def test_el_episodio_queda_en_la_semana_que_el_script_declara(
    engine, script, primera_ejecucion
):
    """Anclado al reloj real: hoy tiene que caer en ``SEMANA_INICIAL``.

    Es la razón de existir del aprovisionamiento. Si el episodio quedara donde
    quedan los canónicos --semana 50 o más-- el contrato de ingesta lo
    rechazaría y la demostración no probaría nada.
    """
    with engine.connect() as conexion:
        inicio, semana = conexion.execute(
            text(
                "SELECT e.fecha_inicio,"
                "       ((CURRENT_DATE - e.fecha_inicio) / 7) + 1 AS semana"
                "  FROM operacional.embarazo e"
                "  JOIN operacional.asignacion_dispositivo ad"
                "    ON ad.id_embarazo = e.id_embarazo"
                "  JOIN operacional.dispositivo d"
                "    ON d.id_dispositivo = ad.id_dispositivo"
                " WHERE d.codigo_dispositivo = :codigo"
            ),
            {"codigo": script.CODIGO_DISPOSITIVO_DEMO},
        ).one()

    assert semana == script.SEMANA_INICIAL
    assert inicio == date.today() - timedelta(
        days=(script.SEMANA_INICIAL - 1) * script.DIAS_POR_SEMANA
    )


def test_el_episodio_tiene_un_seguimiento_principal_vigente(
    engine, script, primera_ejecucion
):
    """Sin esto el ETL no podría derivar el médico responsable y abortaría."""
    with engine.connect() as conexion:
        seguimientos = conexion.execute(
            text(
                "SELECT count(*) FROM operacional.seguimiento_clinico sc"
                "  JOIN operacional.asignacion_dispositivo ad"
                "    ON ad.id_embarazo = sc.id_embarazo"
                "  JOIN operacional.dispositivo d"
                "    ON d.id_dispositivo = ad.id_dispositivo"
                " WHERE d.codigo_dispositivo = :codigo"
                "   AND sc.rol_seguimiento = 'PRINCIPAL'"
                "   AND sc.fecha_asignacion <= CURRENT_DATE"
                "   AND (sc.fecha_fin IS NULL OR sc.fecha_fin >= CURRENT_DATE)"
            ),
            {"codigo": script.CODIGO_DISPOSITIVO_DEMO},
        ).scalar_one()

    assert seguimientos == 1


def test_la_cuenta_elegida_queda_con_un_solo_embarazo_en_curso(
    engine, script, primera_ejecucion
):
    """Dos episodios ``ACTIVO`` harían que la interfaz declarara ambigüedad y
    se negara --con razón-- a llamar «actual» a ninguno."""
    with engine.connect() as conexion:
        activos = conexion.execute(
            text(
                "SELECT count(*) FROM operacional.embarazo"
                " WHERE estado_embarazo = 'ACTIVO'"
                "   AND id_paciente = ("
                "        SELECT e.id_paciente FROM operacional.embarazo e"
                "          JOIN operacional.asignacion_dispositivo ad"
                "            ON ad.id_embarazo = e.id_embarazo"
                "          JOIN operacional.dispositivo d"
                "            ON d.id_dispositivo = ad.id_dispositivo"
                "         WHERE d.codigo_dispositivo = :codigo)"
            ),
            {"codigo": script.CODIGO_DISPOSITIVO_DEMO},
        ).scalar_one()

    assert activos == 1


# ---------------------------------------------------------------------------
# El archivo de aprovisionamiento
# ---------------------------------------------------------------------------


def test_el_archivo_trae_identificadores_que_existen_de_verdad(
    engine, primera_ejecucion
):
    """**La prueba que da sentido al ticket.** Los identificadores del archivo
    no se inventan: cada uno tiene que existir en la base, y los de catálogo
    tienen que corresponder a la semana y al nivel que dicen representar."""
    contenido = json.loads(primera_ejecucion["ruta"].read_text(encoding="utf-8"))
    catalogos = contenido["catalogos"]

    with engine.connect() as conexion:
        for codigo, id_semaforo in catalogos["semaforo"].items():
            real = conexion.execute(
                text(
                    "SELECT codigo_nivel FROM operacional.semaforo"
                    " WHERE id_semaforo = :id"
                ),
                {"id": id_semaforo},
            ).scalar_one_or_none()
            assert real == codigo, f"id_semaforo={id_semaforo} no es {codigo}"

        for fila in catalogos["tiempo_gestacional"]:
            real = conexion.execute(
                text(
                    "SELECT semana_gestacion, trimestre"
                    "  FROM operacional.tiempo_gestacional"
                    " WHERE id_tiempo_gest = :id"
                ),
                {"id": fila["id_tiempo_gest"]},
            ).one_or_none()
            assert real is not None, f"id_tiempo_gest={fila['id_tiempo_gest']} no existe"
            assert real.semana_gestacion == fila["semana"]
            assert real.trimestre == fila["trimestre"]

        existe_dispositivo = conexion.execute(
            text(
                "SELECT count(*) FROM operacional.dispositivo"
                " WHERE id_dispositivo = :id"
            ),
            {"id": contenido["dispositivo"]["id_dispositivo"]},
        ).scalar_one()

    assert existe_dispositivo == 1


def test_los_identificadores_de_catalogo_no_coinciden_con_la_semana(
    primera_ejecucion,
):
    """Guarda contra el atajo que parecía funcionar: en la base real los
    ``id_tiempo_gest`` empiezan en 100, así que «id = semana» es falso."""
    catalogos = json.loads(
        primera_ejecucion["ruta"].read_text(encoding="utf-8")
    )["catalogos"]

    distintos = [
        fila
        for fila in catalogos["tiempo_gestacional"]
        if fila["id_tiempo_gest"] != fila["semana"]
    ]
    assert distintos, (
        "si el identificador coincidiera con la semana, un adaptador que los "
        "confundiera pasaría esta suite y fallaría contra la base real"
    )


def test_el_archivo_no_guarda_datos_personales(primera_ejecucion):
    """La vinculación con la persona es ``id_usuario``, y nada más."""
    contenido = primera_ejecucion["ruta"].read_text(encoding="utf-8")

    assert "@" not in contenido
    assert "email" not in contenido
    assert "nombre" not in contenido


def test_el_correo_se_informa_por_pantalla_y_no_al_archivo(primera_ejecucion):
    """Quien opera necesita saber con qué cuenta entrar; el archivo no."""
    assert "@" in primera_ejecucion["salida"]


# ---------------------------------------------------------------------------
# Idempotencia
# ---------------------------------------------------------------------------


def test_la_segunda_ejecucion_no_duplica_nada(
    engine, script, url_de_pruebas, primera_ejecucion, tmp_path
):
    with engine.connect() as conexion:
        antes = _conteos(
            conexion,
            ("embarazo", "dispositivo", "asignacion_dispositivo", "seguimiento_clinico"),
        )
        huella_antes = _huella_de_embarazos(conexion)

    ruta = tmp_path / "otra-provision.json"
    resultado = _ejecutar(url_de_pruebas, ruta)
    assert resultado.returncode == 0, resultado.stderr

    with engine.connect() as conexion:
        despues = _conteos(
            conexion,
            ("embarazo", "dispositivo", "asignacion_dispositivo", "seguimiento_clinico"),
        )
        huella_despues = _huella_de_embarazos(conexion)

    assert despues == antes
    assert huella_despues == huella_antes


def test_la_segunda_ejecucion_reconoce_el_mismo_episodio(
    url_de_pruebas, primera_ejecucion, tmp_path
):
    """Mismo embarazo, mismo dispositivo, misma cuenta: el archivo es estable."""
    primero = json.loads(primera_ejecucion["ruta"].read_text(encoding="utf-8"))

    ruta = tmp_path / "segunda.json"
    assert _ejecutar(url_de_pruebas, ruta).returncode == 0
    segundo = json.loads(ruta.read_text(encoding="utf-8"))

    assert segundo["cuenta"] == primero["cuenta"]
    assert segundo["embarazo"] == primero["embarazo"]
    assert segundo["dispositivo"] == primero["dispositivo"]
    assert segundo["catalogos"] == primero["catalogos"]


def test_verificar_informa_sin_escribir(
    engine, url_de_pruebas, primera_ejecucion, tmp_path
):
    with engine.connect() as conexion:
        antes = _huella_de_embarazos(conexion)

    resultado = _ejecutar(url_de_pruebas, tmp_path / "no-se-escribe.json", "verificar")

    assert resultado.returncode == 0, resultado.stderr
    assert not (tmp_path / "no-se-escribe.json").exists()
    with engine.connect() as conexion:
        assert _huella_de_embarazos(conexion) == antes


# ---------------------------------------------------------------------------
# Preservación del dataset canónico
# ---------------------------------------------------------------------------


def test_ninguna_fila_canonica_cambia(engine, estado_inicial, primera_ejecucion):
    """Ni se modifica ni se borra nada del dataset: solo se agrega.

    El episodio nuevo aparece al final de la huella; todo lo anterior tiene que
    estar idéntico, campo por campo.
    """
    with engine.connect() as conexion:
        conteos = _conteos(conexion)
        huella = _huella_de_embarazos(conexion)

    assert conteos == estado_inicial["conteos"]

    cuantos = len(estado_inicial["embarazos"])
    assert huella[:cuantos] == estado_inicial["embarazos"]
    assert len(huella) == cuantos + 1


def test_el_aprovisionamiento_no_agrega_ni_una_lectura(engine, estado_inicial):
    """Aclara de dónde salen los conteos del informe: aprovisionar **no**
    produce lecturas. Los hechos analíticos solo aumentan cuando el portal
    registra una sesión y esta se sincroniza."""
    with engine.connect() as conexion:
        lecturas = conexion.execute(
            text("SELECT count(*) FROM operacional.lectura_biometrica")
        ).scalar_one()
        sesiones = conexion.execute(
            text("SELECT count(*) FROM operacional.sesion_monitoreo")
        ).scalar_one()

    assert lecturas == estado_inicial["conteos"]["lectura_biometrica"]
    assert sesiones == estado_inicial["conteos"]["sesion_monitoreo"]
