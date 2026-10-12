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
import shutil
import subprocess
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from tests import test_demo_control as demo_control_offline
from tests.test_demo_control import dc  # noqa: F401  -- fixture del script

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
            # Una huella por cada fila de cada tabla (SCRUM-104). Se toma aqui,
            # y no en una fixture aparte, porque este es el unico punto del
            # modulo que corre con seguridad antes de aprovisionar.
            "filas": _huellas_por_fila(conexion),
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


# ---------------------------------------------------------------------------
# Demo Control (SCRUM-104): lectura estricta, verificacion y reset sin escribir
# ---------------------------------------------------------------------------
#
# Aqui se ejercitan contra PostgreSQL real las consultas que la suite offline
# (``test_demo_control.py``) sustituye por dobles. Docker y HTTP siguen
# simulados: lo que estas pruebas demuestran es que **ninguna opcion de Demo
# Control escribe en la base**, comparando el contenido de cada tabla fila por
# fila, y no solo cuantas filas hay.

ESQUEMAS_VIGILADOS = ("operacional", "analitico", "privado")

# Lo unico que el aprovisionamiento agrega al dataset canonico: una fila en
# cada una de estas tablas. Todo lo demas tiene que quedar identico.
FILAS_DEL_EPISODIO = {
    "operacional.dispositivo": 1,
    "operacional.embarazo": 1,
    "operacional.asignacion_dispositivo": 1,
    "operacional.seguimiento_clinico": 1,
}


def _tablas_vigiladas(conexion) -> list[str]:
    return [
        f"{fila.table_schema}.{fila.table_name}"
        for fila in conexion.execute(
            text(
                "SELECT table_schema, table_name FROM information_schema.tables"
                " WHERE table_schema = ANY(:esquemas) AND table_type = 'BASE TABLE'"
                " ORDER BY 1, 2"
            ),
            {"esquemas": list(ESQUEMAS_VIGILADOS)},
        )
    ]


def _huellas_por_fila(conexion) -> dict[str, Counter]:
    """Una huella por **cada fila** de cada tabla, y el valor de cada secuencia.

    ``md5(t::text)`` cambia si cambia cualquier columna de la fila, asi que una
    fila canonica modificada deja de estar en el conjunto aunque el conteo de la
    tabla siga igual. Es un multiconjunto para no depender de las llaves.
    """
    huellas = {
        tabla: Counter(
            conexion.execute(text(f"SELECT md5(t::text) FROM {tabla} t")).scalars()
        )
        for tabla in _tablas_vigiladas(conexion)
    }
    huellas["secuencias"] = Counter(
        f"{fila.schemaname}.{fila.sequencename}={fila.last_value}"
        for fila in conexion.execute(
            text("SELECT schemaname, sequencename, last_value FROM pg_sequences")
        )
    )
    return huellas


@pytest.fixture(scope="module")
def huellas_canonicas(estado_inicial):
    """El dataset canonico fila por fila, **antes** de aprovisionar.

    Lo fotografia ``estado_inicial``, que falla si la base ya trae un episodio
    de demostracion y de la que depende ``primera_ejecucion``: el orden no
    depende de que prueba se ejecute primero.
    """
    return estado_inicial["filas"]


@pytest.fixture(scope="module")
def aprovisionada(huellas_canonicas, primera_ejecucion):
    """Garantiza el orden: primero la fotografia canonica, despues el episodio."""
    return primera_ejecucion


@pytest.fixture
def control(dc, url_de_pruebas, aprovisionada, tmp_path, monkeypatch):
    """Demo Control con PostgreSQL real y Docker/HTTP simulados."""
    clave_etl = os.environ.get("ETL_PASSWORD")
    if not clave_etl:
        pytest.fail(
            "Define ETL_PASSWORD (la clave del rol fetalalert_etl del servidor de "
            "pruebas): Demo Control valida el destino con la credencial del ETL."
        )
    url_etl = (
        make_url(url_de_pruebas)
        .set(username="fetalalert_etl", password=clave_etl)
        .render_as_string(hide_password=False)
    )
    raiz = tmp_path / "repo"
    (raiz / "scripts").mkdir(parents=True)
    control = demo_control_offline.control_con_postgresql_real(
        dc,
        monkeypatch,
        raiz=raiz,
        datos=tmp_path / "datos",
        url_mantenimiento=url_de_pruebas,
        url_etl=url_etl,
    )
    shutil.copy2(aprovisionada["ruta"], control.ajustes.provision_path)
    return control


def _texto(control) -> str:
    return "\n".join(control.salida)


def _sin_claves(control) -> None:
    for clave in control.claves:
        assert clave not in _texto(control)


def test_el_aprovisionamiento_solo_agrega_las_filas_del_episodio(
    engine, huellas_canonicas, aprovisionada
):
    """Integridad canonica por huellas: ninguna fila previa cambio ni desaparecio."""
    with engine.connect() as conexion:
        ahora = _huellas_por_fila(conexion)

    for tabla in [t for t in huellas_canonicas if t != "secuencias"]:
        antes = huellas_canonicas[tabla]
        # Toda fila canonica sigue ahi, con el mismo contenido...
        assert not antes - ahora[tabla], f"{tabla}: una fila canonica cambio o falta"
        # ...y lo unico nuevo son las filas del episodio de demostracion.
        nuevas = sum((ahora[tabla] - antes).values())
        assert nuevas == FILAS_DEL_EPISODIO.get(tabla, 0), tabla
    assert set(ahora) == set(huellas_canonicas)


def test_la_conexion_de_demo_control_no_puede_escribir(dc, url_de_pruebas, engine):
    with engine.connect() as conexion:
        antes = _huellas_por_fila(conexion)

    for sentencia in (
        "UPDATE operacional.embarazo SET numero_gestas = numero_gestas",
        "DELETE FROM operacional.dispositivo",
        "INSERT INTO operacional.clinica (nombre_clinica, provincia, distrito)"
        " VALUES ('x', 'x', 'x')",
        "SELECT nextval('operacional.embarazo_id_embarazo_seq')",
        "CREATE TABLE public.no_debe_existir (x integer)",
    ):
        with pytest.raises(DBAPIError) as error:
            with dc._solo_lectura(url_de_pruebas) as conexion:
                conexion.execute(text(sentencia))
        assert getattr(error.value.orig, "sqlstate", None) == "25006", sentencia

    with dc._solo_lectura(url_de_pruebas) as conexion:
        assert conexion.execute(
            text(
                "SELECT current_setting('transaction_read_only'),"
                " current_setting('default_transaction_read_only'),"
                " current_setting('transaction_isolation')"
            )
        ).one() == ("on", "on", "repeatable read")
    with engine.connect() as conexion:
        assert _huellas_por_fila(conexion) == antes


def test_localizar_el_episodio_coincide_con_el_archivo_y_no_trae_el_correo(
    dc, url_de_pruebas, script, aprovisionada
):
    with dc._solo_lectura(url_de_pruebas) as conexion:
        episodio = dc.provisionar_demo.localizar_episodio_demo(conexion)
        catalogos = dc.provisionar_demo.leer_catalogos(conexion)

    archivo = json.loads(aprovisionada["ruta"].read_text(encoding="utf-8"))
    assert episodio["id_usuario"] == archivo["cuenta"]["id_usuario"]
    assert episodio["id_embarazo"] == archivo["embarazo"]["id_embarazo"]
    assert episodio["id_dispositivo"] == archivo["dispositivo"]["id_dispositivo"]
    assert episodio["fecha_inicio"].isoformat() == archivo["embarazo"]["fecha_inicio"]
    assert catalogos == archivo["catalogos"]
    assert episodio["estado_embarazo"] == "ACTIVO" and episodio["cuenta_activa"] is True
    assert not any("email" in clave or "nombre" in clave for clave in episodio)
    assert dc.comparar_provision(aprovisionada["ruta"], episodio, catalogos) == (
        "COHERENTE",
        [],
    )


def test_localizar_no_elige_cuando_el_episodio_es_ambiguo(dc, engine, script, aprovisionada):
    """Dos asignaciones del dispositivo de la demo: se niega, no toma la ultima."""
    with engine.connect() as conexion:
        transaccion = conexion.begin()
        try:
            conexion.execute(
                text(
                    "INSERT INTO operacional.asignacion_dispositivo"
                    " (id_dispositivo, id_embarazo, fecha_inicio, activo)"
                    " SELECT ad.id_dispositivo, ad.id_embarazo,"
                    "        ad.fecha_inicio + 1, false"
                    "   FROM operacional.asignacion_dispositivo ad"
                    "   JOIN operacional.dispositivo d USING (id_dispositivo)"
                    "  WHERE d.codigo_dispositivo = :codigo"
                ),
                {"codigo": script.CODIGO_DISPOSITIVO_DEMO},
            )
            # ``provisionar`` reconoce el episodio igualmente...
            assert script._buscar_demo_existente(conexion) is not None
            # ...pero la lectura estricta de Demo Control se niega a elegir.
            with pytest.raises(dc.provisionar_demo.EpisodioDemoNoIdentificable) as error:
                dc.provisionar_demo.localizar_episodio_demo(conexion)
            assert "hay 2" in error.value.detalle
        finally:
            transaccion.rollback()


def test_localizar_sin_episodio_se_niega_y_no_crea_nada(dc, engine, script, aprovisionada):
    with engine.connect() as conexion:
        antes = _huellas_por_fila(conexion)
    with engine.connect() as conexion:
        transaccion = conexion.begin()
        try:
            conexion.execute(
                text(
                    "UPDATE operacional.dispositivo SET codigo_dispositivo = 'OTRO-CODIGO'"
                    " WHERE codigo_dispositivo = :codigo"
                ),
                {"codigo": script.CODIGO_DISPOSITIVO_DEMO},
            )
            with pytest.raises(dc.provisionar_demo.EpisodioDemoNoIdentificable) as error:
                dc.provisionar_demo.localizar_episodio_demo(conexion)
            assert "hay 0" in error.value.detalle
        finally:
            transaccion.rollback()
    with engine.connect() as conexion:
        assert _huellas_por_fila(conexion) == antes


def test_validar_el_entorno_contra_postgresql_real(dc, control):
    entorno = dc.validar_entorno(control.dep)

    assert entorno.rol_etl == "fetalalert_etl"
    assert PATRON_DE_BASE.fullmatch(entorno.base)
    assert entorno.episodio["estado_embarazo"] == "ACTIVO"
    assert control.mundo.de_control == []


def test_otro_postgresql_en_el_mismo_puerto_no_se_valida(dc, control, monkeypatch):
    """La identidad sale del servidor: una IP distinta de la del ``db`` se rechaza."""
    monkeypatch.setattr(demo_control_offline, "IP_DB", "203.0.113.77")

    with pytest.raises(dc.EntornoNoValidado) as error:
        dc.validar_entorno(control.dep)

    assert "no llega al contenedor db de este proyecto" in str(error.value)
    for clave in control.claves:
        assert clave not in str(error.value)


def test_verificar_con_postgresql_real_no_modifica_ni_una_fila(dc, engine, control):
    with engine.connect() as conexion:
        antes = _huellas_por_fila(conexion)
    archivos = demo_control_offline.huella_de_carpeta(control.ajustes.provision_path.parent)

    informe = dc.opcion_verificar(control.dep)
    dc.opcion_verificar(control.dep)

    with engine.connect() as conexion:
        assert _huellas_por_fila(conexion) == antes
        assert not conexion.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE datname = current_database() AND pid <> pg_backend_pid()"
                "   AND state LIKE 'idle in transaction%'"
            )
        ).scalar_one()
    assert demo_control_offline.huella_de_carpeta(
        control.ajustes.provision_path.parent
    ) == archivos
    assert control.mundo.de_control == [] and control.mundo.del_etl == []

    por_etiqueta = {linea.etiqueta: linea for linea in informe.lineas}
    # Lo que si se cumple en esta base: el episodio recien aprovisionado.
    for etiqueta in (
        "Destino", "Credenciales", "Migraciones", "Episodio demo", "Cuenta PACIENTE",
        "Embarazo vigente", "Semana gestacional", "Médico responsable",
        "Dispositivo", "Aprovisionamiento local", "Cola local",
    ):
        assert por_etiqueta[etiqueta].nivel == "OK", por_etiqueta[etiqueta]
    assert "Semana 28" in por_etiqueta["Semana gestacional"].detalle
    # El resto depende de si en esta base ya corrio el ETL, y la verificacion
    # tiene que decir exactamente lo que hay, sin arreglarlo: se compara con lo
    # que la propia base responde.
    with engine.connect() as conexion:
        sin_etl, con_seudonimo = conexion.execute(
            text(
                "SELECT (SELECT count(*) FROM operacional.lectura_biometrica l"
                "         WHERE NOT EXISTS (SELECT 1 FROM analitico.fact_lectura_biometrica f"
                "                            WHERE f.id_lectura = l.id_lectura)),"
                "       EXISTS (SELECT 1 FROM privado.seudonimo_embarazo s"
                "                JOIN operacional.asignacion_dispositivo ad USING (id_embarazo)"
                "                JOIN operacional.dispositivo d USING (id_dispositivo)"
                "               WHERE d.codigo_dispositivo = :codigo)"
            ),
            {"codigo": dc.provisionar_demo.CODIGO_DISPOSITIVO_DEMO},
        ).one()
    assert por_etiqueta["Seudónimo del embarazo"].nivel == ("OK" if con_seudonimo else "FAIL")
    assert por_etiqueta["ETL al día"].nivel == ("WARN" if sin_etl else "OK")
    if sin_etl:
        assert f"{sin_etl} lectura(s) sin incorporar (0 del embarazo demo)" in (
            por_etiqueta["ETL al día"].detalle
        )
    assert "0 sesión(es) y 0 lectura(s)" in por_etiqueta["Base de demostración"].detalle
    assert informe.estado_global == (
        "NO DISPONIBLE"
        if not con_seudonimo
        else "REQUIERE PREPARACIÓN" if sin_etl else "LISTO PARA DEMO"
    )
    _sin_claves(control)


def test_verificar_con_postgresql_real_no_dice_listo_si_llega_a_otro_servidor(
    dc, engine, control, monkeypatch
):
    """La verificacion aplica la misma validacion de instancia que la ejecucion.

    Las consultas son reales; lo unico que cambia es la IP que Docker declara
    para el contenedor ``db``, de modo que la conexion ya no «llega» a el.
    """
    with engine.connect() as conexion:
        antes = _huellas_por_fila(conexion)
    monkeypatch.setattr(demo_control_offline, "IP_DB", "203.0.113.77")

    with pytest.raises(dc.EntornoNoValidado):
        dc.validar_entorno(control.dep)
    informe = dc.opcion_verificar(control.dep)

    assert informe.estado_global == "NO DISPONIBLE"
    assert "LISTO PARA DEMO" not in _texto(control)
    destino = [linea for linea in informe.lineas if linea.etiqueta == "Destino"]
    # Ni la conexion del ETL ni la de mantenimiento llegan a ese contenedor.
    assert destino and {linea.nivel for linea in destino} == {"FAIL"}
    motivos = " ".join(linea.detalle for linea in destino)
    assert "no llega al contenedor db de este proyecto" in motivos
    assert "La credencial de mantenimiento no llega a la misma base" in motivos
    # Sigue sin escribir, y sin tocar Docker.
    with engine.connect() as conexion:
        assert _huellas_por_fila(conexion) == antes
    assert control.mundo.de_control == [] and control.mundo.del_etl == []
    _sin_claves(control)


def test_el_reset_con_postgresql_real_no_escribe_en_la_base(
    dc, engine, control, huellas_canonicas
):
    """R2 completo: reconstruye el archivo local y la base queda intacta."""
    ruta = control.ajustes.provision_path
    desactualizado = json.loads(ruta.read_text(encoding="utf-8"))
    desactualizado["embarazo"]["id_embarazo"] += 1000
    ruta.write_text(json.dumps(desactualizado), encoding="utf-8")
    anterior = ruta.read_bytes()
    with engine.connect() as conexion:
        antes = _huellas_por_fila(conexion)

    control.respuestas.append("S")
    dc.opcion_restablecer(control.dep)

    with engine.connect() as conexion:
        despues = _huellas_por_fila(conexion)
        episodio = dc.provisionar_demo.localizar_episodio_demo(conexion)
        catalogos = dc.provisionar_demo.leer_catalogos(conexion)
    # 1. PostgreSQL: identico fila por fila, secuencias incluidas.
    assert despues == antes
    # 2. El dataset canonico sigue entero respecto de antes de aprovisionar.
    for tabla in [t for t in huellas_canonicas if t != "secuencias"]:
        assert not huellas_canonicas[tabla] - despues[tabla], tabla
    # 3. El archivo local quedo reconstruido, con respaldo del anterior.
    assert "provision.json: DESACTUALIZADO." in _texto(control)
    assert "[OK]   provision.json reconstruido y comprobado." in _texto(control)
    assert dc.comparar_provision(ruta, episodio, catalogos) == ("COHERENTE", [])
    [respaldo] = sorted(ruta.parent.glob("provision.json.*.bak"))
    assert respaldo.read_bytes() == anterior
    # 4. Sin procesos hijos: ni ``provisionar`` ni el ETL.
    assert [c for c in control.mundo.comandos if c[0] != "docker"] == []
    assert control.mundo.de_control == []
    _sin_claves(control)

    # 5. Un segundo reset ya no cambia nada, ni en la base ni en disco.
    archivos = demo_control_offline.huella_de_carpeta(ruta.parent)
    dc.opcion_restablecer(control.dep)
    with engine.connect() as conexion:
        assert _huellas_por_fila(conexion) == antes
    assert demo_control_offline.huella_de_carpeta(ruta.parent) == archivos
    assert control.respuestas == []


def test_cancelar_el_reset_con_postgresql_real_no_cambia_nada(dc, engine, control):
    ruta = control.ajustes.provision_path
    ruta.unlink()
    with engine.connect() as conexion:
        antes = _huellas_por_fila(conexion)

    control.respuestas.append("N")
    dc.opcion_restablecer(control.dep)

    with engine.connect() as conexion:
        assert _huellas_por_fila(conexion) == antes
    assert not ruta.exists()
    assert "Cancelado. No se modificó ningún archivo." in _texto(control)
