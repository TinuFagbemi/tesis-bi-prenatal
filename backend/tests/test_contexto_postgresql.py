"""El contexto contra PostgreSQL: vive y muere con la transaccion (SCRUM-98).

Se omite salvo que este definida ``SCRUM98_CONTEXTO_TEST_DATABASE_URL``. Nunca
cae por omision sobre ``DATABASE_URL`` ni sobre ``ALEMBIC_DATABASE_URL``.

Lo que esta suite demuestra no se puede demostrar sin un servidor: que
``set_config(..., true)`` es de transaccion y no de sesion, y sobre todo que un
contexto **no viaja entre solicitudes por una conexion reutilizada del pool**.
Ese es el fallo concreto que este ticket existe para impedir -- la paciente A
libera la conexion, la paciente B la recibe y hereda la identidad de A -- y la
unica forma honesta de comprobarlo es forzar la reutilizacion de verdad, con un
``QueuePool`` de tamano 1 sobre un servidor real.

**Se conecta como un rol restringido**, no como el superusuario del contenedor:
uno con ``LOGIN`` y nada mas. Comprobar el aislamiento con una credencial que lo
omite todo no comprobaria nada.

Todas las credenciales son efimeras, ficticias y solo viven en memoria de la
ejecucion.
"""

from __future__ import annotations

import os
import re
import secrets

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool, QueuePool

from app.db.contexto import NOMBRE_DEL_CONTEXTO, instalar_contexto, leer_contexto

VARIABLE_DE_ENTORNO = "SCRUM98_CONTEXTO_TEST_DATABASE_URL"
PATRON_DE_BASE = re.compile(r"^scrum98_[a-z0-9_]{1,40}$")
PATRON_DE_CLAVE = re.compile(r"^[A-Za-z0-9_-]+$")

# Dos identidades ficticias. No corresponden a ninguna cuenta del dataset: lo
# que se observa es el valor del parametro, no una fila.
PACIENTE_A = 9_000_101
PACIENTE_B = 9_000_202

pytestmark = pytest.mark.skipif(
    not os.environ.get(VARIABLE_DE_ENTORNO),
    reason=(
        f"Define {VARIABLE_DE_ENTORNO} para ejecutar las pruebas de contexto "
        "contra un PostgreSQL desechable."
    ),
)


def _clave() -> str:
    clave = secrets.token_urlsafe(24)
    assert PATRON_DE_CLAVE.fullmatch(clave)
    return clave


@pytest.fixture(scope="session")
def url_administrativa() -> str:
    valor = os.environ[VARIABLE_DE_ENTORNO]
    nombre = make_url(valor).database or ""
    if not PATRON_DE_BASE.match(nombre):
        pytest.fail(
            f"{VARIABLE_DE_ENTORNO} apunta a la base '{nombre}'. Esta suite crea "
            "y elimina un rol del cluster, asi que solo se ejecuta contra una "
            "base desechable cuyo nombre empiece por 'scrum98_'."
        )
    return valor


@pytest.fixture(scope="session")
def url_restringida(url_administrativa):
    """Un rol con ``LOGIN`` y ninguna capacidad, creado para esta ejecucion.

    Es lo que hace que la prueba signifique algo: un superusuario tendria el
    mismo comportamiento de ``set_config``, pero probarlo con el no diria nada
    sobre el rol con el que la API correra.
    """
    nombre = f"scrum98_tmp_{secrets.token_hex(4)}_contexto"
    clave = _clave()

    admin = create_engine(
        url_administrativa, poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as conexion:
        conexion.exec_driver_sql(
            f"CREATE ROLE \"{nombre}\" LOGIN PASSWORD '{clave}' "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
        )

    yield make_url(url_administrativa).set(
        username=nombre, password=clave
    ).render_as_string(hide_password=False)

    with admin.connect() as conexion:
        conexion.exec_driver_sql(f'REASSIGN OWNED BY "{nombre}" TO CURRENT_USER')
        conexion.exec_driver_sql(f'DROP OWNED BY "{nombre}"')
        conexion.exec_driver_sql(f'DROP ROLE IF EXISTS "{nombre}"')
    admin.dispose()


@pytest.fixture
def engine_de_una_conexion(url_restringida):
    """Un pool de **una** conexion, que fuerza la reutilizacion.

    ``pool_size=1`` con ``max_overflow=0`` es lo que convierte «podria
    reutilizarse» en «se reutiliza siempre». Sin esto la prueba dependeria de la
    suerte de que SQLAlchemy devolviera la misma conexion.
    """
    motor = create_engine(
        url_restringida, poolclass=QueuePool, pool_size=1, max_overflow=0
    )
    try:
        yield motor
    finally:
        motor.dispose()


def _identidad_del_backend(sesion: Session) -> int:
    """PID del proceso servidor, para afirmar que la conexion es la misma."""
    return sesion.execute(text("SELECT pg_backend_pid()")).scalar()


def _solicitud(fabrica, identidad: int | None, terminar: str = "rollback"):
    """Una solicitud completa: abrir sesion, instalar contexto, cerrar.

    Reproduce el ciclo real de ``get_db``: la sesion se abre, se usa y se
    cierra, y la conexion vuelve al pool. ``terminar`` recorre los tres finales
    posibles -- commit, rollback y cierre sin mas -- porque el contexto debe
    morir en los tres.
    """
    sesion = fabrica()
    try:
        pid = _identidad_del_backend(sesion)
        if identidad is not None:
            instalar_contexto(sesion, identidad)
        visto = leer_contexto(sesion)
        if terminar == "commit":
            sesion.commit()
        elif terminar == "rollback":
            sesion.rollback()
        return pid, visto
    finally:
        sesion.close()


# ---------------------------------------------------------------------------
# 1. El contexto es de transaccion
# ---------------------------------------------------------------------------


def test_el_contexto_se_instala_y_se_lee_en_la_misma_transaccion(
    engine_de_una_conexion,
):
    fabrica = sessionmaker(bind=engine_de_una_conexion)
    _, visto = _solicitud(fabrica, PACIENTE_A)

    assert visto == PACIENTE_A


@pytest.mark.parametrize("final", ["commit", "rollback"])
def test_el_contexto_muere_al_terminar_la_transaccion(engine_de_una_conexion, final):
    """PostgreSQL descarta el valor local en el commit y en el rollback."""
    fabrica = sessionmaker(bind=engine_de_una_conexion)
    sesion = fabrica()
    try:
        instalar_contexto(sesion, PACIENTE_A)
        assert leer_contexto(sesion) == PACIENTE_A

        getattr(sesion, final)()

        assert leer_contexto(sesion) is None
    finally:
        sesion.close()


def test_sin_instalar_nada_no_hay_contexto(engine_de_una_conexion):
    """Una conexion recien tomada del pool no tiene identidad."""
    fabrica = sessionmaker(bind=engine_de_una_conexion)
    _, visto = _solicitud(fabrica, None)

    assert visto is None


def test_un_set_de_sesion_si_filtraria_entre_solicitudes(engine_de_una_conexion):
    """La contraprueba que justifica la eleccion de ``set_config(..., true)``.

    Se instala la identidad con un ``SET`` corriente -- de sesion, no de
    transaccion -- la solicitud **confirma**, y despues se recorre el mismo
    ciclo: cerrar la sesion, devolver la conexion al pool y tomarla otra vez. El
    valor sobrevive.

    El commit no es un detalle de la prueba: es el camino real de las rutas que
    escriben. ``sesiones.py`` confirma al registrar un paquete y ``cuentas.py``
    al provisionar, asi que con un ``SET`` corriente la identidad de esa
    solicitud quedaria en la conexion esperando a la siguiente.

    Un matiz que conviene dejar escrito, porque se comprobo reproduciendolo: un
    ``SET`` dentro de una transaccion que hace **rollback** si se deshace, asi
    que la fuga no aparece por ese camino. Eso lo hace peor, no mejor: solo se
    manifestaria en las solicitudes que confirman, que son precisamente las que
    escriben datos clinicos.
    """
    fabrica = sessionmaker(bind=engine_de_una_conexion)

    sesion = fabrica()
    try:
        pid_1 = _identidad_del_backend(sesion)
        # Deliberadamente el mecanismo equivocado, y solo dentro de esta prueba.
        sesion.execute(text(f"SET {NOMBRE_DEL_CONTEXTO} = '{PACIENTE_A}'"))
        sesion.commit()
    finally:
        sesion.close()

    segunda = fabrica()
    try:
        pid_2 = _identidad_del_backend(segunda)
        heredado = leer_contexto(segunda)
    finally:
        segunda.rollback()
        segunda.close()
        # No se deja el cluster contaminado para las pruebas siguientes.
        engine_de_una_conexion.dispose()

    assert pid_1 == pid_2, "el pool debe devolver la misma conexion"
    assert heredado == PACIENTE_A, (
        "un SET de sesion sobrevive al checkin; por eso el codigo usa "
        "set_config(..., true) y nunca SET"
    )


def test_el_codigo_de_produccion_no_usa_set_en_ninguna_parte():
    """La regla que la contraprueba anterior justifica, comprobada en la fuente."""
    import inspect

    from app.db import contexto as modulo

    fuente = inspect.getsource(modulo)
    sentencias = [
        linea.strip()
        for linea in fuente.splitlines()
        if "SET " in linea and not linea.strip().startswith("#")
    ]

    assert sentencias == [], sentencias
    assert "set_config" in fuente


# ---------------------------------------------------------------------------
# 2. La prueba central: cero fuga entre conexiones del pool
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("final", ["commit", "rollback", "cierre"])
def test_la_conexion_se_reutiliza_de_verdad(engine_de_una_conexion, final):
    """Sin esto, las pruebas siguientes no demostrarian nada.

    Si el pool entregara conexiones distintas, la ausencia de fuga seria un
    accidente y no una garantia.
    """
    fabrica = sessionmaker(bind=engine_de_una_conexion)
    pid_a, _ = _solicitud(fabrica, PACIENTE_A, terminar=final)
    pid_b, _ = _solicitud(fabrica, PACIENTE_B, terminar=final)

    assert pid_a == pid_b, "el pool debe devolver la misma conexion"


@pytest.mark.parametrize("final", ["commit", "rollback", "cierre"])
def test_a_luego_b_no_hereda_el_contexto_de_a(engine_de_una_conexion, final):
    fabrica = sessionmaker(bind=engine_de_una_conexion)

    pid_a, visto_a = _solicitud(fabrica, PACIENTE_A, terminar=final)
    pid_b, visto_b = _solicitud(fabrica, PACIENTE_B, terminar=final)

    assert pid_a == pid_b
    assert visto_a == PACIENTE_A
    assert visto_b == PACIENTE_B


@pytest.mark.parametrize("final", ["commit", "rollback", "cierre"])
def test_b_luego_a_tampoco(engine_de_una_conexion, final):
    """El orden inverso, para que la prueba no sea accidentalmente direccional."""
    fabrica = sessionmaker(bind=engine_de_una_conexion)

    pid_b, visto_b = _solicitud(fabrica, PACIENTE_B, terminar=final)
    pid_a, visto_a = _solicitud(fabrica, PACIENTE_A, terminar=final)

    assert pid_a == pid_b
    assert visto_b == PACIENTE_B
    assert visto_a == PACIENTE_A


@pytest.mark.parametrize("final", ["commit", "rollback", "cierre"])
@pytest.mark.parametrize("primero", [PACIENTE_A, PACIENTE_B])
def test_una_solicitud_sin_contexto_no_hereda_la_anterior(
    engine_de_una_conexion, final, primero
):
    """El caso que cierra el escenario: A, luego B, luego nadie."""
    fabrica = sessionmaker(bind=engine_de_una_conexion)

    pid_1, _ = _solicitud(fabrica, primero, terminar=final)
    pid_2, visto = _solicitud(fabrica, None, terminar=final)

    assert pid_1 == pid_2
    assert visto is None


def test_una_excepcion_en_la_solicitud_tampoco_deja_contexto(engine_de_una_conexion):
    """El camino que un rollback olvidado dejaria abierto.

    ``Session.close()`` revierte la transaccion pendiente y el pool emite su
    propio ROLLBACK al devolver la conexion, asi que el contexto muere aunque
    nadie lo limpie a mano. Se comprueba en vez de suponerlo.
    """
    fabrica = sessionmaker(bind=engine_de_una_conexion)

    sesion = fabrica()
    pid_1 = _identidad_del_backend(sesion)
    instalar_contexto(sesion, PACIENTE_A)
    try:
        raise RuntimeError("fallo a mitad de la solicitud")
    except RuntimeError:
        pass
    finally:
        sesion.close()

    pid_2, visto = _solicitud(fabrica, None)

    assert pid_1 == pid_2
    assert visto is None


# ---------------------------------------------------------------------------
# 3. El rol con el que se comprueba es realmente restringido
# ---------------------------------------------------------------------------


def test_el_rol_de_la_prueba_no_es_superusuario_ni_omite_rls(engine_de_una_conexion):
    """Si esta fallara, todo lo anterior dejaria de significar algo."""
    fabrica = sessionmaker(bind=engine_de_una_conexion)
    sesion = fabrica()
    try:
        fila = sesion.execute(
            text(
                "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb "
                "FROM pg_roles WHERE rolname = current_user"
            )
        ).mappings().one()
    finally:
        sesion.rollback()
        sesion.close()

    assert fila["rolsuper"] is False
    assert fila["rolbypassrls"] is False
    assert fila["rolcreaterole"] is False
    assert fila["rolcreatedb"] is False


def test_un_rol_restringido_puede_instalar_su_propio_contexto(engine_de_una_conexion):
    """``set_config`` sobre un parametro personalizado no exige privilegios.

    Importa comprobarlo: si hiciera falta un privilegio para instalarlo, el
    diseno entero dependeria de concederselo al rol de runtime.
    """
    fabrica = sessionmaker(bind=engine_de_una_conexion)
    _, visto = _solicitud(fabrica, PACIENTE_A)

    assert visto == PACIENTE_A


def test_dos_conexiones_simultaneas_no_comparten_contexto(url_restringida):
    """Cada backend tiene el suyo: el aislamiento tambien es entre conexiones."""
    motor = create_engine(url_restringida, poolclass=NullPool)
    try:
        fabrica = sessionmaker(bind=motor)
        primera, segunda = fabrica(), fabrica()
        try:
            instalar_contexto(primera, PACIENTE_A)
            instalar_contexto(segunda, PACIENTE_B)

            assert leer_contexto(primera) == PACIENTE_A
            assert leer_contexto(segunda) == PACIENTE_B
        finally:
            primera.rollback()
            segunda.rollback()
            primera.close()
            segunda.close()
    finally:
        motor.dispose()
