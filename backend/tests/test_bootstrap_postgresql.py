"""El bootstrap de roles contra un clúster PostgreSQL real (SCRUM-98).

Se omite salvo que esté definida ``SCRUM98_TEST_DATABASE_URL``. Nunca cae por
omisión sobre ``DATABASE_URL`` ni sobre ``ALEMBIC_DATABASE_URL``: lo que esta
suite ejercita crea y elimina **roles**, que en PostgreSQL son objetos del
clúster entero y no de una base. Un descuido aquí no ensuciaría una base: la
ensuciaría para todas.

Por eso hay dos guardas y no una:

* la variable debe existir, y apuntar a una base cuyo nombre empiece por
  ``scrum98_``, para que dirigirla a una base de trabajo sea un acto deliberado;
* todo lo que la ejecución crea queda registrado y se elimina al final, y los
  roles que ya existían **no** se tocan.

**El bootstrap se ejecuta como un migrador real, no como el superusuario.** La
credencial de la variable solo sirve para fabricar ese migrador y su base: a
partir de ahí, todo -- crear los roles, concederse las membresías, crear el
schema de helpers, transferir la propiedad de las funciones -- corre bajo un rol
con ``LOGIN``, ``CREATEROLE``, y sin ``SUPERUSER`` ni ``BYPASSRLS``. Hacerlo
como superusuario ocultaría exactamente lo que hay que demostrar: un
superusuario transfiere cualquier propiedad sin necesitar membresías, así que
una prueba que lo usara pasaría aunque el diseño de roles estuviera mal.

Ejemplo de invocación, contra un contenedor desechable::

    $env:SCRUM98_TEST_DATABASE_URL = "postgresql+psycopg://<usuario>:<clave>@127.0.0.1:<puerto>/scrum98_bootstrap_tmp"
    python -m pytest tests/test_bootstrap_postgresql.py

Todos los datos y credenciales implicados son ficticios y efímeros.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app.db.privilegios import (
    ESQUEMAS_DEL_PROYECTO,
    SEGUNDOS_MAXIMOS_DE_CONEXION,
    TIEMPO_MAXIMO_DE_COMPROBACION,
    PrivilegiosNoVerificables,
    evaluar_privilegios,
    verificar_al_arrancar,
)
from tests.conftest import construir_config_alembic
from app.db.roles import (
    CAPACIDADES_PROHIBIDAS,
    ROL_PROVISION_OWNER,
    ESQUEMA_DE_HELPERS,
    MEMBRESIAS_DEL_MIGRADOR,
    ROL_API,
    ROL_MANTENIMIENTO,
    ROL_RLS_OWNER,
    ROLES_DUENIOS_DE_HELPERS,
    ROLES_NOLOGIN,
    ROLES_RUNTIME,
    evaluar_roles,
)

VARIABLE_DE_ENTORNO = "SCRUM98_TEST_DATABASE_URL"
PATRON_DE_BASE = re.compile(r"^scrum98_[a-z0-9_]{1,40}$")
PATRON_DE_CLAVE = re.compile(r"^[A-Za-z0-9_-]+$")

RAIZ = Path(__file__).resolve().parents[2]
RUNNER = RAIZ / "scripts" / "bootstrap_roles.py"

# Los dos que existen hoy. La lista completa que vigila la comprobacion de
# arranque -- con seguridad, privado y publicacion -- se importa de
# ``app.db.privilegios`` y se usa donde hace falta.
ESQUEMAS_EXISTENTES = ("operacional", "analitico")

pytestmark = pytest.mark.skipif(
    not os.environ.get(VARIABLE_DE_ENTORNO),
    reason=(
        f"Define {VARIABLE_DE_ENTORNO} para ejecutar el bootstrap contra un "
        "clúster PostgreSQL desechable. Crea y elimina roles cluster-globales."
    ),
)


def _sufijo() -> str:
    return secrets.token_hex(4)


def _clave() -> str:
    """Contraseña efímera que solo vive en memoria de esta ejecución.

    ``token_urlsafe`` produce ``[A-Za-z0-9_-]``, sin comillas ni barras, y eso
    se comprueba: ``CREATE ROLE`` es DDL y PostgreSQL no admite parámetros
    vinculados, así que el valor va literal en la sentencia.
    """
    clave = secrets.token_urlsafe(24)
    assert PATRON_DE_CLAVE.fullmatch(clave)
    return clave


def _entorno(url: str) -> dict:
    entorno = dict(os.environ)
    entorno["ALEMBIC_DATABASE_URL"] = url
    entorno["PYTHONPATH"] = str(RAIZ / "backend")
    entorno["PYTHONIOENCODING"] = "utf-8"
    return entorno


def _correr_runner(url: str, orden: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RUNNER), orden],
        capture_output=True,
        text=True,
        env=_entorno(url),
        cwd=str(RAIZ),
    )


# ---------------------------------------------------------------------------
# Infraestructura desechable
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def url_administrativa() -> str:
    valor = os.environ[VARIABLE_DE_ENTORNO]
    nombre = make_url(valor).database or ""
    if not PATRON_DE_BASE.match(nombre):
        pytest.fail(
            f"{VARIABLE_DE_ENTORNO} apunta a la base '{nombre}'. Esta suite crea y "
            "elimina roles del clúster completo, así que solo se ejecuta contra "
            "una base desechable cuyo nombre empiece por 'scrum98_'."
        )
    return valor


@pytest.fixture(scope="session")
def admin(url_administrativa):
    """Engine de mantenimiento. Solo fabrica y destruye infraestructura."""
    motor = create_engine(
        url_administrativa, poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    try:
        yield motor
    finally:
        motor.dispose()


@pytest.fixture(scope="session")
def roles_preexistentes(admin) -> frozenset[str]:
    """Los roles aprobados que ya estaban antes de tocar nada.

    Decide qué se limpia: la ejecución elimina los que ella creó y deja intactos
    los que encontró. Borrar un rol que otra cosa usa sería convertir una prueba
    en un incidente.
    """
    with admin.connect() as conexion:
        presentes = conexion.execute(
            text("SELECT rolname FROM pg_roles WHERE rolname = ANY(:nombres)"),
            {"nombres": sorted(ROLES_NOLOGIN)},
        ).scalars()
        return frozenset(presentes)


@pytest.fixture(scope="session")
def migrador(admin, url_administrativa, roles_preexistentes):
    """Un migrador realista: ``LOGIN``, ``CREATEROLE``, sin superusuario.

    Tiene su propia base, de la que es propietario, para poder crear schemas sin
    que nadie le conceda nada. Es la credencial que en un despliegue viajaría en
    ``ALEMBIC_DATABASE_URL`` y que nunca llega al proceso web.

    ``CREATEROLE`` no es una concesión generosa: es el privilegio mínimo para
    ejecutar el bootstrap, y PostgreSQL 16 lo acota a los roles que ese rol creó.
    """
    sufijo = _sufijo()
    nombre = f"scrum98_tmp_{sufijo}_migrador"
    base = f"scrum98_mig_{sufijo}"
    clave = _clave()

    with admin.connect() as conexion:
        conexion.exec_driver_sql(
            f"CREATE ROLE \"{nombre}\" LOGIN PASSWORD '{clave}' "
            "NOSUPERUSER NOBYPASSRLS CREATEROLE NOCREATEDB NOREPLICATION"
        )
        conexion.exec_driver_sql(f'CREATE DATABASE "{base}" OWNER "{nombre}"')

    # ``str(URL)`` enmascara la contraseña -- es lo que protege un log accidental
    # --, así que para construir una URL utilizable hay que pedirla explícitamente.
    # Si los roles tecnicos ya existen -- porque otro migrador los creo antes --,
    # este no tiene ADMIN sobre ellos y el bootstrap fallaria con «permission
    # denied to grant role». Delegarlo es una decision administrativa
    # deliberada, y quien la toma aqui es la credencial administrativa, no el
    # propio migrador. Asi la suite no depende del orden en que se ejecute.
    with admin.connect() as conexion:
        for rol in sorted(ROLES_NOLOGIN & roles_preexistentes):
            conexion.exec_driver_sql(
                f'GRANT "{rol}" TO "{nombre}" WITH ADMIN TRUE, INHERIT FALSE, SET TRUE'
            )

    url = make_url(url_administrativa).set(
        username=nombre, password=clave, database=base
    ).render_as_string(hide_password=False)
    yield nombre, url

    with admin.connect() as conexion:
        conexion.exec_driver_sql(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{base}'"
        )
        conexion.exec_driver_sql(f'DROP DATABASE IF EXISTS "{base}"')
        for rol in sorted(ROLES_NOLOGIN - roles_preexistentes):
            conexion.exec_driver_sql(f'DROP ROLE IF EXISTS "{rol}"')
        conexion.exec_driver_sql(f'DROP ROLE IF EXISTS "{nombre}"')


@pytest.fixture(scope="session")
def admin_en_la_base_del_migrador(admin, migrador, url_administrativa):
    """Superusuario, pero conectado a la base donde están los schemas.

    ``admin`` vive en la base de la variable de entorno; los objetos del
    proyecto los crea el migrador en la suya. Para transferir la propiedad de
    una tabla hace falta estar en la base que la contiene.
    """
    _, url = migrador
    destino = make_url(url_administrativa).set(database=make_url(url).database)
    motor = create_engine(
        destino, poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    try:
        yield motor
    finally:
        motor.dispose()


@pytest.fixture(scope="session")
def engine(migrador):
    """Conexión **como el migrador**, que es quien todo esto debe poder hacer."""
    _, url = migrador
    motor = create_engine(url, poolclass=NullPool)
    try:
        yield motor
    finally:
        motor.dispose()


@pytest.fixture(scope="session")
def bootstrap_aplicado(migrador):
    """Ejecuta el runner real una vez, con la credencial del migrador."""
    _, url = migrador
    resultado = _correr_runner(url, "aplicar")
    assert resultado.returncode == 0, resultado.stderr
    return resultado


@pytest.fixture(scope="session")
def esquemas(engine, bootstrap_aplicado):
    """Los dos schemas del proyecto, vacíos, creados por el migrador.

    Una aserción de privilegios necesita un objeto sobre el que preguntar:
    ``has_schema_privilege`` lanza si el schema no existe, y omitir el caso
    dejaría la comprobación sin ejecutar -- justo lo que el guardián de CI
    prohíbe.

    No es el trabajo de Alembic hecho a mano: estos no tienen ni una tabla, y su
    única función es ser la diana de «después del bootstrap, ningún rol tiene
    USAGE ni CREATE sobre ellos».
    """
    for nombre in ESQUEMAS_EXISTENTES:
        with engine.begin() as conexion:
            conexion.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{nombre}"')
    return ESQUEMAS_EXISTENTES


@pytest.fixture(scope="session")
def roles_de_runtime(engine, bootstrap_aplicado):
    """Cuatro roles con ``LOGIN`` y nada más, creados por el migrador.

    Representan a la API, al ETL, a Power BI y al rol restringido de pruebas.
    Ninguno recibe membresía en nada: que no la tengan es lo que se comprueba.
    """
    creados = {}
    for papel in sorted(ROLES_RUNTIME):
        nombre = f"scrum98_tmp_{_sufijo()}_{papel.split('_', 1)[1]}"
        clave = _clave()
        with engine.begin() as conexion:
            conexion.exec_driver_sql(
                f"CREATE ROLE \"{nombre}\" LOGIN PASSWORD '{clave}' "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            )
        creados[papel] = (nombre, clave)

    yield creados

    for nombre, _ in creados.values():
        with engine.begin() as conexion:
            conexion.exec_driver_sql(f'DROP ROLE IF EXISTS "{nombre}"')


# ---------------------------------------------------------------------------
# 1. El migrador no es superusuario, y aun asi basta
# ---------------------------------------------------------------------------


def test_el_migrador_no_es_superusuario_ni_omite_rls(engine):
    """Si esta prueba fallara, todas las demás dejarían de demostrar nada."""
    with engine.connect() as conexion:
        fila = (
            conexion.execute(
                text(
                    "SELECT rolsuper, rolbypassrls, rolcreaterole "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            )
            .mappings()
            .one()
        )

    assert fila["rolsuper"] is False
    assert fila["rolbypassrls"] is False
    assert fila["rolcreaterole"] is True


def test_el_runner_termina_bien_y_declara_conformidad(bootstrap_aplicado):
    assert "conforme=true" in bootstrap_aplicado.stdout


@pytest.mark.parametrize("rol", sorted(ROLES_NOLOGIN))
def test_cada_rol_aprobado_existe(bootstrap_aplicado, engine, rol):
    with engine.connect() as conexion:
        existe = conexion.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :rol"), {"rol": rol}
        ).scalar()

    assert existe == 1


@pytest.mark.parametrize("rol", sorted(ROLES_NOLOGIN))
@pytest.mark.parametrize("columna", sorted(CAPACIDADES_PROHIBIDAS))
def test_ningun_rol_tiene_capacidades_prohibidas(
    bootstrap_aplicado, engine, rol, columna
):
    """NOLOGIN y sin una sola capacidad. Incluido LOGIN: no son cuentas."""
    with engine.connect() as conexion:
        valor = conexion.execute(
            text(f"SELECT {columna} FROM pg_roles WHERE rolname = :rol"), {"rol": rol}
        ).scalar()

    assert valor is False


# ---------------------------------------------------------------------------
# 2. Membresias: opciones exactas, leidas de pg_auth_members
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rol,esperado", sorted(MEMBRESIAS_DEL_MIGRADOR.items()))
def test_el_migrador_recibe_cada_membresia_con_sus_opciones(
    bootstrap_aplicado, engine, migrador, rol, esperado
):
    """``INHERIT`` y ``SET`` son cosas distintas desde PostgreSQL 16.

    Y hay que concederlas: un rol con ``CREATEROLE`` queda como miembro de lo
    que crea, pero con ``inherit_option = false`` y **``set_option = false``**.
    Sin la concesión explícita, ``ALTER FUNCTION ... OWNER TO`` fallaría con
    ``must be able to SET ROLE``.
    """
    nombre_migrador, _ = migrador
    hereda_esperado, set_esperado = esperado

    with engine.connect() as conexion:
        informe = evaluar_roles(conexion)

    opciones = informe.opciones_de(rol, nombre_migrador)

    assert opciones is not None, f"el migrador no es miembro de {rol}"
    assert opciones.hereda is hereda_esperado
    assert opciones.puede_set is set_esperado


@pytest.mark.parametrize("rol", sorted(ROLES_DUENIOS_DE_HELPERS))
def test_el_migrador_puede_asumir_los_duenos_pero_no_los_hereda(
    bootstrap_aplicado, engine, rol
):
    """Lo mínimo para transferir la propiedad, y ni un privilegio más.

    Heredarlos arrastraría sus privilegios a cada sentencia ordinaria del
    migrador; no poder asumirlos impediría crear los helpers.
    """
    with engine.connect() as conexion:
        fila = (
            conexion.execute(
                text(
                    "SELECT pg_has_role(current_user, :rol, 'SET') AS puede_set, "
                    "pg_has_role(current_user, :rol, 'USAGE') AS hereda"
                ),
                {"rol": rol},
            )
            .mappings()
            .one()
        )

    assert fila["puede_set"] is True
    assert fila["hereda"] is False


def test_el_migrador_hereda_mantenimiento(bootstrap_aplicado, engine):
    """Una policy dirigida ``TO`` un rol se evalúa contra lo que se hereda."""
    with engine.connect() as conexion:
        hereda = conexion.execute(
            text("SELECT pg_has_role(current_user, :rol, 'USAGE')"),
            {"rol": ROL_MANTENIMIENTO},
        ).scalar()

    assert hereda is True


def test_el_informe_del_modulo_coincide_con_el_catalogo(bootstrap_aplicado, engine):
    with engine.connect() as conexion:
        informe = evaluar_roles(conexion)

    assert informe.ausentes == frozenset()
    assert set(informe.roles) == set(ROLES_NOLOGIN)
    assert informe.membresias_faltantes() == ()
    assert informe.alcances_indebidos() == ()
    assert informe.roles_contaminados() == ()


def test_la_membresia_de_mantenimiento_queda_registrada(
    bootstrap_aplicado, engine, migrador
):
    """En ``pg_auth_members``, no solo según ``pg_has_role``.

    ``pg_has_role`` respondería verdadero para un superusuario aunque la
    concesión nunca se hubiera hecho, y entonces la prueba pasaría en un clúster
    donde el bootstrap no se ejecutó.
    """
    nombre_migrador, _ = migrador
    with engine.connect() as conexion:
        informe = evaluar_roles(conexion)

    assert nombre_migrador in informe.miembros_de_mantenimiento


# ---------------------------------------------------------------------------
# 3. La demostracion central: crear los helpers con su propietario final
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("propietario", sorted(ROLES_DUENIOS_DE_HELPERS))
def test_el_migrador_transfiere_cada_helper_a_su_propietario_previsto(
    bootstrap_aplicado, engine, propietario
):
    """El flujo completo de la sub-fase 3, ensayado sin superusuario.

    PostgreSQL exige dos cosas para ``ALTER FUNCTION ... OWNER TO``, y las dos
    se comprobaron reproduciéndolas: poder hacer ``SET ROLE`` al nuevo
    propietario, y que ese propietario tenga ``CREATE`` sobre el schema de la
    función. Faltando la primera responde ``must be able to SET ROLE``;
    faltando la segunda, ``permission denied for schema``.

    Por eso los dos dueños de helpers **sí** reciben ``CREATE`` sobre el schema
    que los contiene. Es la corrección de una afirmación anterior de este
    ticket, que decía que no debían tenerlo en ninguna parte: eso es
    incompatible con poseer una función.
    """
    nombre = f"ensayo_{propietario.split('_')[1]}"
    funcion = f'"{ESQUEMA_DE_HELPERS}"."{nombre}"'

    with engine.begin() as conexion:
        conexion.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{ESQUEMA_DE_HELPERS}"')
        conexion.exec_driver_sql(
            f'GRANT USAGE, CREATE ON SCHEMA "{ESQUEMA_DE_HELPERS}" TO "{propietario}"'
        )
        conexion.exec_driver_sql(
            f"CREATE OR REPLACE FUNCTION {funcion}() RETURNS boolean "
            "LANGUAGE sql STABLE AS 'SELECT true'"
        )
        conexion.exec_driver_sql(f'ALTER FUNCTION {funcion}() OWNER TO "{propietario}"')

    with engine.connect() as conexion:
        real = conexion.execute(
            text(
                "SELECT pg_get_userbyid(p.proowner) FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = :esquema AND p.proname = :nombre"
            ),
            {"esquema": ESQUEMA_DE_HELPERS, "nombre": nombre},
        ).scalar()

    assert real == propietario


@pytest.mark.parametrize("propietario", sorted(ROLES_DUENIOS_DE_HELPERS))
def test_sin_create_en_el_schema_la_transferencia_falla(
    bootstrap_aplicado, engine, propietario
):
    """La contraprueba: quitado el ``CREATE``, PostgreSQL se niega.

    Demuestra que la concesión no es decorativa y que la prueba anterior no
    pasaría por algún otro motivo.
    """
    esquema = f"{ESQUEMA_DE_HELPERS}_sin_create"
    nombre = f"ensayo_{propietario.split('_')[1]}"
    funcion = f'"{esquema}"."{nombre}"'

    with engine.begin() as conexion:
        conexion.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{esquema}"')
        conexion.exec_driver_sql(
            f"CREATE OR REPLACE FUNCTION {funcion}() RETURNS boolean "
            "LANGUAGE sql STABLE AS 'SELECT true'"
        )

    with pytest.raises(Exception) as error:
        with engine.begin() as conexion:
            conexion.exec_driver_sql(
                f'ALTER FUNCTION {funcion}() OWNER TO "{propietario}"'
            )

    assert "permission denied for schema" in str(error.value)


@pytest.mark.parametrize("propietario", sorted(ROLES_DUENIOS_DE_HELPERS))
def test_los_duenos_necesitan_create_solo_en_el_schema_de_helpers(
    bootstrap_aplicado, esquemas, engine, propietario
):
    """El ``CREATE`` es una excepción de un solo schema, no una regla general."""
    with engine.connect() as conexion:
        for esquema in ESQUEMAS_EXISTENTES:
            tiene = conexion.execute(
                text("SELECT has_schema_privilege(:rol, :esquema, 'CREATE')"),
                {"rol": propietario, "esquema": esquema},
            ).scalar()
            assert tiene is False, esquema


def test_mantenimiento_no_necesita_create_en_ninguna_parte(
    bootstrap_aplicado, esquemas, engine
):
    """No posee objetos: solo es la diana de una policy."""
    with engine.connect() as conexion:
        for esquema in (*ESQUEMAS_EXISTENTES, "public"):
            tiene = conexion.execute(
                text("SELECT has_schema_privilege(:rol, :esquema, 'CREATE')"),
                {"rol": ROL_MANTENIMIENTO, "esquema": esquema},
            ).scalar()
            assert tiene is False, esquema


# ---------------------------------------------------------------------------
# 4. Ningun rol de runtime alcanza un rol privilegiado
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("papel", sorted(ROLES_RUNTIME))
@pytest.mark.parametrize("privilegiado", sorted(ROLES_NOLOGIN))
def test_un_rol_de_runtime_no_puede_asumir_ni_heredar_un_rol_privilegiado(
    engine, roles_de_runtime, papel, privilegiado
):
    nombre, _ = roles_de_runtime[papel]

    with engine.connect() as conexion:
        fila = (
            conexion.execute(
                text(
                    "SELECT pg_has_role(:rol, :objetivo, 'MEMBER') AS membresia, "
                    "pg_has_role(:rol, :objetivo, 'SET') AS puede_set, "
                    "pg_has_role(:rol, :objetivo, 'USAGE') AS hereda"
                ),
                {"rol": nombre, "objetivo": privilegiado},
            )
            .mappings()
            .one()
        )

    assert fila["membresia"] is False
    assert fila["puede_set"] is False
    assert fila["hereda"] is False


def test_un_rol_de_runtime_que_intenta_asumir_al_dueno_es_rechazado(
    migrador, roles_de_runtime
):
    """No solo el catálogo lo dice: PostgreSQL lo rechaza de verdad."""
    _, url_migrador = migrador
    nombre, clave = roles_de_runtime["fetalalert_api"]
    destino = make_url(url_migrador).set(username=nombre, password=clave)

    motor = create_engine(destino, poolclass=NullPool)
    try:
        with pytest.raises(Exception) as error:
            with motor.connect() as conexion:
                conexion.exec_driver_sql(f'SET ROLE "{ROL_RLS_OWNER}"')
    finally:
        motor.dispose()

    assert "permission denied to set role" in str(error.value)


def test_el_informe_no_reporta_ninguna_membresia_indebida(engine, roles_de_runtime):
    with engine.connect() as conexion:
        informe = evaluar_roles(conexion)

    assert informe.miembros_inesperados() == ()
    assert informe.conforme, informe.motivo


@pytest.mark.parametrize("papel", sorted(ROLES_RUNTIME))
@pytest.mark.parametrize("esquema", ESQUEMAS_EXISTENTES)
def test_ningun_rol_de_runtime_puede_crear_objetos(
    engine, esquemas, roles_de_runtime, papel, esquema
):
    """La prohibición de ``CREATE`` es absoluta para los roles que se conectan."""
    nombre, _ = roles_de_runtime[papel]

    with engine.connect() as conexion:
        puede = conexion.execute(
            text("SELECT has_schema_privilege(:rol, :esquema, 'CREATE')"),
            {"rol": nombre, "esquema": esquema},
        ).scalar()

    assert puede is False


@pytest.mark.parametrize("papel", sorted(ROLES_RUNTIME))
def test_ningun_rol_de_runtime_puede_crear_en_public(engine, roles_de_runtime, papel):
    """``public`` existe siempre; desde PostgreSQL 15 ``PUBLIC`` no recibe CREATE."""
    nombre, _ = roles_de_runtime[papel]

    with engine.connect() as conexion:
        puede = conexion.execute(
            text("SELECT has_schema_privilege(:rol, 'public', 'CREATE')"),
            {"rol": nombre},
        ).scalar()

    assert puede is False


def test_el_rol_de_la_api_se_evalua_como_restringido(migrador, roles_de_runtime):
    """La comprobación de arranque aprueba un rol de runtime bien formado."""
    _, url_migrador = migrador
    nombre, clave = roles_de_runtime["fetalalert_api"]
    destino = make_url(url_migrador).set(username=nombre, password=clave)

    motor = create_engine(destino, poolclass=NullPool)
    try:
        with motor.connect() as conexion:
            with conexion.begin():
                informe = evaluar_privilegios(conexion)
    finally:
        motor.dispose()

    assert informe.restringido, informe.violaciones


def test_el_migrador_se_evalua_como_no_restringido(bootstrap_aplicado, engine):
    """La contraprueba que convierte la anterior en evidencia.

    El migrador puede asumir los dueños de helpers y hereda mantenimiento, así
    que la comprobación debe rechazarlo como credencial de runtime. Si aprobara
    a los dos, no estaría distinguiendo nada.
    """
    with engine.connect() as conexion:
        with conexion.begin():
            informe = evaluar_privilegios(conexion)

    assert not informe.restringido
    assert (
        informe.puede_asumir_un_rol_privilegiado or informe.hereda_un_rol_privilegiado
    )


# ---------------------------------------------------------------------------
# 5. Idempotencia y guardias del runner
# ---------------------------------------------------------------------------


def test_aplicarlo_dos_veces_no_falla_ni_cambia_nada(
    bootstrap_aplicado, engine, migrador
):
    """Segunda pasada sobre un clúster ya aprovisionado: mismo estado."""
    _, url = migrador
    with engine.connect() as conexion:
        antes = evaluar_roles(conexion)

    resultado = _correr_runner(url, "aplicar")

    with engine.connect() as conexion:
        despues = evaluar_roles(conexion)

    assert resultado.returncode == 0, resultado.stderr
    assert antes == despues


def test_verificar_no_escribe_y_reporta_conformidad(bootstrap_aplicado, migrador):
    _, url = migrador
    resultado = _correr_runner(url, "verificar")

    assert resultado.returncode == 0, resultado.stderr
    assert "conforme=true" in resultado.stdout


def test_el_runner_se_niega_sin_su_variable(migrador):
    """Sin ALEMBIC_DATABASE_URL no hay respaldo: se detiene."""
    _, url = migrador
    entorno = _entorno(url)
    del entorno["ALEMBIC_DATABASE_URL"]
    entorno["DATABASE_URL"] = url  # a mano, y aun así no se toma

    resultado = subprocess.run(
        [sys.executable, str(RUNNER), "verificar"],
        capture_output=True,
        text=True,
        env=entorno,
        cwd=str(RAIZ),
    )

    assert resultado.returncode != 0
    assert "ALEMBIC_DATABASE_URL" in resultado.stderr


# ---------------------------------------------------------------------------
# 6. El bootstrap no concede acceso a datos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rol", sorted(ROLES_NOLOGIN))
@pytest.mark.parametrize("esquema", ESQUEMAS_EXISTENTES)
@pytest.mark.parametrize("privilegio", ["USAGE", "CREATE"])
def test_el_bootstrap_no_concede_privilegios_de_schema(
    bootstrap_aplicado, esquemas, engine, rol, esquema, privilegio
):
    """Los GRANT sobre objetos son de Alembic. Tras el bootstrap no hay ninguno.

    Los ``USAGE`` positivos que el diseño exige -- ``operacional`` y
    ``seguridad`` para el rol de la API y los dueños de helpers, ``analitico`` y
    ``privado`` para el ETL, ``publicacion`` para Power BI -- llegan con la
    migración. Lo que se fija aquí es la línea de partida: el bootstrap crea
    capacidades, no accesos.
    """
    with engine.connect() as conexion:
        tiene = conexion.execute(
            text("SELECT has_schema_privilege(:rol, :esquema, :privilegio)"),
            {"rol": rol, "esquema": esquema, "privilegio": privilegio},
        ).scalar()

    assert tiene is False


@pytest.mark.parametrize("esquema", ESQUEMAS_EXISTENTES)
def test_public_no_conserva_privilegios_sobre_los_esquemas_del_proyecto(
    bootstrap_aplicado, esquemas, engine, esquema
):
    """``PUBLIC`` se comprueba en el ACL, no con ``has_schema_privilege``.

    Esa función toma un rol nominal; ``PUBLIC`` es el receptor con OID 0 dentro
    del ACL, así que se lee de ahí.
    """
    with engine.connect() as conexion:
        concedidos = conexion.execute(
            text(
                "SELECT a.privilege_type FROM pg_namespace n, "
                "aclexplode(n.nspacl) a WHERE n.nspname = :n AND a.grantee = 0"
            ),
            {"n": esquema},
        ).scalars()

        assert list(concedidos) == []


# ---------------------------------------------------------------------------
# 7. MEMBER, USAGE y SET no son lo mismo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hereda,puede_set,member_esperado,usage_esperado,set_esperado",
    [
        (False, False, True, False, False),
        (True, False, True, True, False),
        (False, True, True, False, True),
    ],
    ids=["inherit_f_set_f", "inherit_t_set_f", "inherit_f_set_t"],
)
def test_la_matriz_de_pg_has_role_por_combinacion_de_opciones(
    engine, hereda, puede_set, member_esperado, usage_esperado, set_esperado
):
    """``MEMBER`` es verdadero con cualquier concesión; ``SET`` no.

    Es la razón de la corrección: usar ``MEMBER`` para responder «puede hacer
    SET ROLE» sobre-reporta, porque una concesión con ambas opciones apagadas ya
    lo hace verdadero sin conceder nada.
    """
    sufijo = _sufijo()
    objetivo = f"scrum98_tmp_{sufijo}_objetivo"
    miembro = f"scrum98_tmp_{sufijo}_miembro"

    with engine.begin() as conexion:
        conexion.exec_driver_sql(f'CREATE ROLE "{objetivo}" NOLOGIN')
        conexion.exec_driver_sql(f'CREATE ROLE "{miembro}" NOLOGIN')
        conexion.exec_driver_sql(
            f'GRANT "{objetivo}" TO "{miembro}" '
            f"WITH INHERIT {'TRUE' if hereda else 'FALSE'}, "
            f"SET {'TRUE' if puede_set else 'FALSE'}"
        )
    try:
        with engine.connect() as conexion:
            fila = (
                conexion.execute(
                    text(
                        "SELECT pg_has_role(:m, :o, 'MEMBER') AS member, "
                        "pg_has_role(:m, :o, 'USAGE') AS usage_, "
                        "pg_has_role(:m, :o, 'SET') AS set_"
                    ),
                    {"m": miembro, "o": objetivo},
                )
                .mappings()
                .one()
            )
    finally:
        with engine.begin() as conexion:
            conexion.exec_driver_sql(f'DROP ROLE IF EXISTS "{miembro}"')
            conexion.exec_driver_sql(f'DROP ROLE IF EXISTS "{objetivo}"')

    assert fila["member"] is member_esperado
    assert fila["usage_"] is usage_esperado
    assert fila["set_"] is set_esperado


def test_sin_membresia_las_tres_respuestas_son_falsas(engine, roles_de_runtime):
    nombre, _ = roles_de_runtime["fetalalert_api"]

    with engine.connect() as conexion:
        fila = (
            conexion.execute(
                text(
                    "SELECT pg_has_role(:m, :o, 'MEMBER') AS member, "
                    "pg_has_role(:m, :o, 'USAGE') AS usage_, "
                    "pg_has_role(:m, :o, 'SET') AS set_"
                ),
                {"m": nombre, "o": ROL_RLS_OWNER},
            )
            .mappings()
            .one()
        )

    assert (fila["member"], fila["usage_"], fila["set_"]) == (False, False, False)


# ---------------------------------------------------------------------------
# 8. Los dos timeouts, contra el servidor real
# ---------------------------------------------------------------------------


def test_la_comprobacion_fija_statement_timeout_en_su_transaccion(engine):
    """``evaluar_privilegios`` lo fija con ``SET LOCAL``, y se verifica ahí."""
    with engine.connect() as conexion:
        with conexion.begin():
            evaluar_privilegios(conexion)
            vigente = conexion.execute(
                text("SELECT current_setting('statement_timeout')")
            ).scalar()

    assert vigente == TIEMPO_MAXIMO_DE_COMPROBACION


def test_una_sentencia_mas_lenta_que_el_limite_es_abortada(engine):
    """El límite no es decorativo: PostgreSQL corta de verdad.

    Se usa un valor propio y muy corto para no alargar la suite; lo que se
    demuestra es el mecanismo, no el número.
    """
    with pytest.raises(Exception) as error:
        with engine.connect() as conexion:
            with conexion.begin():
                conexion.exec_driver_sql("SET LOCAL statement_timeout = '150ms'")
                conexion.exec_driver_sql("SELECT pg_sleep(3)")

    assert "statement timeout" in str(error.value).lower()


def test_el_connect_timeout_viaja_en_los_argumentos_de_conexion(engine):
    """``connect_timeout`` es de libpq y se fija al construir el engine.

    Aquí se comprueba que un engine construido como lo hace
    ``verificar_al_arrancar`` conecta de verdad con esa opción puesta: si el
    parámetro no fuera válido, ``connect`` fallaría.
    """
    motor = create_engine(
        engine.url,
        poolclass=NullPool,
        connect_args={"connect_timeout": SEGUNDOS_MAXIMOS_DE_CONEXION},
    )
    try:
        with motor.connect() as conexion:
            vivo = conexion.execute(text("SELECT 1")).scalar()
    finally:
        motor.dispose()

    assert vivo == 1


def test_un_destino_inalcanzable_falla_cerrado_en_desplegado():
    """Puerto cerrado: la comprobación no se completa, así que no se arranca."""

    class _Motor:
        url = make_url(
            "postgresql+psycopg://ficticio:ficticia@127.0.0.1:1/base_inexistente"
        )

    with pytest.raises(PrivilegiosNoVerificables):
        verificar_al_arrancar(_Motor(), "production")


# ---------------------------------------------------------------------------
# 9. Contrapruebas: cada via de privilegio se detecta de verdad
# ---------------------------------------------------------------------------


def _evaluar_como(url_base: str, nombre: str, clave: str):
    """Abre una conexión con ese rol y devuelve su informe de privilegios."""
    destino = make_url(url_base).set(username=nombre, password=clave)
    motor = create_engine(destino, poolclass=NullPool)
    try:
        with motor.connect() as conexion:
            with conexion.begin():
                return evaluar_privilegios(conexion)
    finally:
        motor.dispose()


ATRIBUTOS = ("SUPERUSER", "CREATEDB", "CREATEROLE", "REPLICATION", "BYPASSRLS")


def _limpiar_rol(nombre: str, *motores) -> None:
    """Elimina un rol y todo lo que posee, en cada base indicada.

    ``DROP OWNED BY`` solo alcanza la base donde se ejecuta, así que un rol con
    objetos en otra base no se puede eliminar hasta recorrerlas todas.
    """
    for motor in motores:
        with motor.connect() as conexion:
            existe = conexion.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": nombre}
            ).scalar()
            if not existe:
                return
            conexion.exec_driver_sql(f'REASSIGN OWNED BY "{nombre}" TO CURRENT_USER')
            conexion.exec_driver_sql(f'DROP OWNED BY "{nombre}"')
    with motores[-1].connect() as conexion:
        conexion.exec_driver_sql(f'DROP ROLE IF EXISTS "{nombre}"')


@pytest.fixture
def rol_defectuoso(admin, admin_en_la_base_del_migrador, migrador):
    """Fabrica un rol con exactamente el defecto que se quiera demostrar.

    **Todo pasa por la conexión administrativa**, y ese detalle es en sí una
    garantía del diseño: PostgreSQL 16 no deja que un rol conceda un atributo
    que él no tiene, así que el migrador -- sin CREATEDB, sin REPLICATION y sin
    BYPASSRLS -- no podría fabricar un rol defectuoso ni queriendo. Lo que estas
    pruebas ejercitan es la **detección**, no quién crea.

    No se confía en que los roles nazcan limpios: cada contraprueba parte de un
    rol restringido y le añade una sola vía de privilegio.
    """
    _, url = migrador
    creados = []

    def crear(concedidos=()):
        nombre = f"scrum98_tmp_{_sufijo()}"
        clave = _clave()
        opciones = " ".join(
            atributo if atributo in concedidos else f"NO{atributo}"
            for atributo in ATRIBUTOS
        )
        with admin.connect() as conexion:
            conexion.exec_driver_sql(
                f"CREATE ROLE \"{nombre}\" LOGIN PASSWORD '{clave}' {opciones}"
            )
        creados.append(nombre)
        return nombre, clave, url

    yield crear

    # El orden importa, y la base también. ``DROP OWNED BY`` solo alcanza los
    # objetos de la base donde se ejecuta, así que hay que pasar por las dos: la
    # administrativa y la del migrador, que es donde viven los schemas. Y
    # primero lo que el rol posee, después el rol; al revés PostgreSQL responde
    # «cannot be dropped because some objects depend on it».
    for nombre in reversed(creados):
        for motor in (admin_en_la_base_del_migrador, admin):
            with motor.connect() as conexion:
                conexion.exec_driver_sql(
                    f'REASSIGN OWNED BY "{nombre}" TO CURRENT_USER'
                )
                conexion.exec_driver_sql(f'DROP OWNED BY "{nombre}"')
        with admin.connect() as conexion:
            conexion.exec_driver_sql(f'DROP ROLE IF EXISTS "{nombre}"')


def test_un_rol_recien_fabricado_y_limpio_es_la_linea_base(rol_defectuoso):
    """Sin esta, las contrapruebas no demostrarían nada."""
    nombre, clave, url = rol_defectuoso()

    informe = _evaluar_como(url, nombre, clave)

    assert informe.restringido, informe.violaciones


@pytest.mark.parametrize(
    "atributo,campo",
    [
        ("CREATEROLE", "puede_crear_roles"),
        ("CREATEDB", "puede_crear_bases"),
        ("REPLICATION", "puede_replicar"),
        ("BYPASSRLS", "omite_rls"),
        ("SUPERUSER", "es_superusuario"),
    ],
)
def test_cada_atributo_de_rol_prohibido_se_detecta(rol_defectuoso, atributo, campo):
    nombre, clave, url = rol_defectuoso(concedidos=(atributo,))

    informe = _evaluar_como(url, nombre, clave)

    assert not informe.restringido
    assert getattr(informe, campo) is True


@pytest.mark.parametrize(
    "objeto,creacion",
    [
        ("tabla", 'CREATE TABLE operacional.{n} (x int)'),
        ("vista", 'CREATE VIEW operacional.{n} AS SELECT 1 AS x'),
        ("secuencia", 'CREATE SEQUENCE operacional.{n}'),
        (
            "materializada",
            'CREATE MATERIALIZED VIEW operacional.{n} AS SELECT 1 AS x',
        ),
    ],
)
def test_poseer_cualquier_clase_de_objeto_del_proyecto_se_detecta(
    admin_en_la_base_del_migrador, esquemas, rol_defectuoso, objeto, creacion
):
    """Tablas, vistas, materializadas y secuencias cuentan por igual."""
    nombre, clave, url = rol_defectuoso()
    relacion = f"obj_{objeto}_{_sufijo()}"

    with admin_en_la_base_del_migrador.connect() as conexion:
        conexion.exec_driver_sql(creacion.format(n=relacion))
        palabra = {
            "tabla": "TABLE",
            "vista": "VIEW",
            "secuencia": "SEQUENCE",
            "materializada": "MATERIALIZED VIEW",
        }[objeto]
        conexion.exec_driver_sql(
            f'ALTER {palabra} operacional.{relacion} OWNER TO "{nombre}"'
        )

    informe = _evaluar_como(url, nombre, clave)

    assert not informe.restringido
    assert informe.es_propietario is True


def test_poseer_una_funcion_del_proyecto_se_detecta(
    admin_en_la_base_del_migrador, esquemas, rol_defectuoso
):
    """Las funciones cuentan igual que las relaciones: son objetos del schema."""
    nombre, clave, url = rol_defectuoso()
    funcion = f"f_{_sufijo()}"

    with admin_en_la_base_del_migrador.connect() as conexion:
        conexion.exec_driver_sql(
            f"CREATE FUNCTION analitico.{funcion}() RETURNS int "
            "LANGUAGE sql IMMUTABLE AS 'SELECT 1'"
        )
        conexion.exec_driver_sql(
            f'ALTER FUNCTION analitico.{funcion}() OWNER TO "{nombre}"'
        )

    informe = _evaluar_como(url, nombre, clave)

    assert not informe.restringido
    assert informe.es_propietario is True


def test_poseer_un_schema_del_proyecto_se_detecta(
    admin_en_la_base_del_migrador, esquemas, rol_defectuoso
):
    nombre, clave, url = rol_defectuoso()
    propio = f"scrum98_esquema_{_sufijo()}"

    with admin_en_la_base_del_migrador.connect() as conexion:
        conexion.exec_driver_sql(f'CREATE SCHEMA "analitico_temporal_{_sufijo()}"')
        conexion.exec_driver_sql(f'ALTER SCHEMA "analitico" OWNER TO "{nombre}"')
    try:
        informe = _evaluar_como(url, nombre, clave)
    finally:
        with admin_en_la_base_del_migrador.connect() as conexion:
            conexion.exec_driver_sql('ALTER SCHEMA "analitico" OWNER TO CURRENT_USER')

    assert not informe.restringido
    assert informe.es_propietario is True
    assert propio  # el nombre solo existe para que el sufijo no se repita


@pytest.mark.parametrize("esquema", [*ESQUEMAS_EXISTENTES, "public"])
def test_poder_crear_objetos_en_un_schema_vigilado_se_detecta(
    admin_en_la_base_del_migrador, esquemas, rol_defectuoso, esquema
):
    """Incluido ``public``, que existe siempre y es el más fácil de olvidar."""
    nombre, clave, url = rol_defectuoso()

    with admin_en_la_base_del_migrador.connect() as conexion:
        conexion.exec_driver_sql(f'GRANT CREATE ON SCHEMA "{esquema}" TO "{nombre}"')

    informe = _evaluar_como(url, nombre, clave)

    assert not informe.restringido
    assert informe.puede_crear_objetos is True


@pytest.mark.parametrize(
    "opciones,campo",
    [
        ("INHERIT TRUE, SET FALSE", "hereda_un_rol_privilegiado"),
        ("INHERIT FALSE, SET TRUE", "puede_asumir_un_rol_privilegiado"),
        ("INHERIT FALSE, SET FALSE", "tiene_membresia_privilegiada"),
    ],
)
def test_cada_via_hacia_un_rol_tecnico_se_detecta(
    bootstrap_aplicado, admin, rol_defectuoso, opciones, campo
):
    """Herencia, ``SET ROLE`` y la simple membresía: las tres cuentan.

    La tercera es la que ``MEMBER`` aporta: no concede nada hoy, pero encender
    las opciones es un solo ``GRANT``.
    """
    nombre, clave, url = rol_defectuoso()

    with admin.connect() as conexion:
        conexion.exec_driver_sql(
            f'GRANT "{ROL_PROVISION_OWNER}" TO "{nombre}" WITH {opciones}'
        )

    informe = _evaluar_como(url, nombre, clave)

    assert not informe.restringido
    assert getattr(informe, campo) is True
    assert informe.tiene_membresia_privilegiada is True


def test_un_alcance_indirecto_rompe_la_conformidad_del_informe(
    bootstrap_aplicado, admin, engine
):
    """Cadena: el rol de la API es miembro de un puente, y el puente del técnico.

    Una comparación de miembros directos no vería este camino; ``pg_has_role``
    resuelve la cadena completa.

    Aquí el rol lleva el nombre canónico ``fetalalert_api`` y no uno con sufijo:
    lo que la comprobación vigila son esos nombres, que son los que un
    despliegue real usará. Se crea y se elimina dentro de la prueba.
    """
    nombre_api = ROL_API
    puente = f"scrum98_tmp_{_sufijo()}_puente"

    with admin.connect() as conexion:
        # El rol de la API puede existir ya -- un cluster de CI lo crea antes --,
        # y en ese caso se usa el que hay en vez de fabricar otro.
        existia = conexion.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": nombre_api}
        ).scalar()
        if not existia:
            conexion.exec_driver_sql(f'CREATE ROLE "{nombre_api}" NOLOGIN')
        conexion.exec_driver_sql(f'CREATE ROLE "{puente}" NOLOGIN')
        conexion.exec_driver_sql(
            f'GRANT "{ROL_PROVISION_OWNER}" TO "{puente}" WITH INHERIT TRUE, SET TRUE'
        )
        conexion.exec_driver_sql(
            f'GRANT "{puente}" TO "{nombre_api}" WITH INHERIT TRUE, SET TRUE'
        )
    try:
        with engine.connect() as conexion:
            informe = evaluar_roles(conexion)
    finally:
        with admin.connect() as conexion:
            conexion.exec_driver_sql(f'REVOKE "{puente}" FROM "{nombre_api}"')
            conexion.exec_driver_sql(f'DROP OWNED BY "{puente}"')
            conexion.exec_driver_sql(f'DROP ROLE IF EXISTS "{puente}"')
            if not existia:
                conexion.exec_driver_sql(f'DROP ROLE IF EXISTS "{nombre_api}"')

    assert not informe.conforme
    assert f"{nombre_api} alcanza {ROL_PROVISION_OWNER}" in informe.motivo


def test_sin_contaminacion_el_informe_vuelve_a_ser_conforme(
    bootstrap_aplicado, engine, roles_de_runtime
):
    """La contraparte de la anterior: sin cadenas, el clúster es aceptable."""
    with engine.connect() as conexion:
        informe = evaluar_roles(conexion)

    assert informe.conforme, informe.motivo
    assert informe.alcances_indebidos() == ()
    assert informe.miembros_inesperados() == ()
    assert informe.roles_contaminados() == ()


# ---------------------------------------------------------------------------
# 10. El runner ante un rol preexistente no conforme
# ---------------------------------------------------------------------------


def test_un_rol_preexistente_no_conforme_detiene_el_bootstrap(
    admin, admin_en_la_base_del_migrador, migrador
):
    """Código controlado, sin traceback y sin filtrar la URL ni el driver.

    El ``RAISE EXCEPTION`` del archivo SQL llega como una excepción de psycopg,
    no de SQLAlchemy, porque el bootstrap se ejecuta contra el cursor del
    driver. Capturar solo la de SQLAlchemy dejaría escapar un traceback con la
    sentencia, sus parámetros y la credencial de conexión.
    """
    _, url = migrador
    intruso = ROL_MANTENIMIENTO

    _limpiar_rol(intruso, admin, admin_en_la_base_del_migrador)
    with admin.connect() as conexion:
        conexion.exec_driver_sql(
            f"CREATE ROLE \"{intruso}\" LOGIN PASSWORD 'ficticia_efimera'"
        )
    try:
        resultado = _correr_runner(url, "aplicar")
    finally:
        _limpiar_rol(intruso, admin, admin_en_la_base_del_migrador)

    assert resultado.returncode == 1
    assert "Traceback" not in resultado.stderr
    assert "psycopg" not in resultado.stderr
    assert "efimero" not in resultado.stderr
    assert "postgresql+psycopg://" not in resultado.stderr
    assert intruso in resultado.stderr
    assert "atributos no permitidos" in resultado.stderr


def test_tras_el_fallo_no_queda_ningun_rol_a_medias(
    admin, admin_en_la_base_del_migrador, migrador
):
    """La transacción completa se revierte: ningún rol nuevo sobrevive."""
    _, url = migrador
    intruso = ROL_RLS_OWNER

    for rol in sorted(ROLES_NOLOGIN):
        _limpiar_rol(rol, admin, admin_en_la_base_del_migrador)
    with admin.connect() as conexion:
        conexion.exec_driver_sql(
            f"CREATE ROLE \"{intruso}\" LOGIN PASSWORD 'ficticia_efimera'"
        )

    resultado = _correr_runner(url, "aplicar")

    with admin.connect() as conexion:
        presentes = set(
            conexion.execute(
                text("SELECT rolname FROM pg_roles WHERE rolname = ANY(:n)"),
                {"n": sorted(ROLES_NOLOGIN)},
            ).scalars()
        )
        conexion.exec_driver_sql(f'DROP ROLE IF EXISTS "{intruso}"')

    assert resultado.returncode == 1
    assert presentes == {intruso}, "el bootstrap no debió crear ningún rol"

    # Se restituye el estado que las demás pruebas de sesión esperan.
    _correr_runner(url, "aplicar")


# ---------------------------------------------------------------------------
# 11. ADMIN efectivo, contra el catalogo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rol", sorted(ROLES_NOLOGIN))
def test_solo_el_migrador_administra_cada_rol_tecnico(
    bootstrap_aplicado, engine, migrador, rol
):
    """La capacidad de conceder estos roles es exclusiva de quien los creo.

    El bootstrap concede ``WITH ADMIN FALSE``, asi que no la ensancha; el
    migrador la conserva solo por la concesion implicita de ``CREATEROLE``.
    """
    nombre_migrador, _ = migrador
    with engine.connect() as conexion:
        informe = evaluar_roles(conexion)

    administradores = {
        miembro
        for miembro, opciones in informe.membresias[rol].items()
        if opciones.administra
    }

    # El migrador de esta ejecucion siempre administra: o porque creo el rol, o
    # porque el administrador se lo delego explicitamente. Puede haber otro si
    # el cluster venia con los roles de una ejecucion anterior, y eso tambien es
    # un migrador, no un rol de runtime: lo que importa es que ninguno de estos
    # sea una credencial de aplicacion.
    assert nombre_migrador in administradores
    assert not (administradores & ROLES_RUNTIME)


@pytest.mark.parametrize("papel", sorted(ROLES_RUNTIME))
def test_ningun_rol_de_runtime_administra_un_rol_tecnico(
    bootstrap_aplicado, engine, roles_de_runtime, papel
):
    """Ni directamente ni por cadena: no alcanzan el rol, luego no lo administran."""
    nombre, _ = roles_de_runtime[papel]
    with engine.connect() as conexion:
        informe = evaluar_roles(conexion)

    for rol in ROLES_NOLOGIN:
        opciones = informe.opciones_de(rol, nombre)
        assert opciones is None, f"{nombre} no debe tener membresia en {rol}"
    assert nombre not in informe.alcance_de_runtime


def test_un_administrador_intruso_se_detecta(
    bootstrap_aplicado, admin, engine, rol_defectuoso
):
    """Con ADMIN OPTION, un tercero podria entregar el rol a quien quisiera."""
    nombre, _, _ = rol_defectuoso()
    with admin.connect() as conexion:
        conexion.exec_driver_sql(
            f'GRANT "{ROL_PROVISION_OWNER}" TO "{nombre}" '
            "WITH ADMIN TRUE, INHERIT FALSE, SET FALSE"
        )
    try:
        with engine.connect() as conexion:
            informe = evaluar_roles(conexion)
    finally:
        with admin.connect() as conexion:
            conexion.exec_driver_sql(
                f'REVOKE "{ROL_PROVISION_OWNER}" FROM "{nombre}"'
            )

    assert not informe.conforme
    assert f"{nombre} administra {ROL_PROVISION_OWNER}" in informe.motivo


# ---------------------------------------------------------------------------
# 9. El preflight de la migracion, contra membresias contaminadas
# ---------------------------------------------------------------------------
#
# ``app/db/roles.py`` ya rechaza un miembro inesperado de un rol tecnico, un
# administrador indebido y un rol tecnico que sea miembro de algo. Pero ese
# modulo lo comprueba *el runner del bootstrap*, y un cluster puede recibir la
# migracion sin que nadie haya vuelto a ejecutar el runner: basta con que
# alguien conceda un rol despues. Por eso la garantia tiene que estar tambien en
# la propia revision, y por eso estas contrapruebas ejercitan ``alembic
# upgrade`` y no ``bootstrap_roles.py verificar``.
#
# **Que se demuestra en cada caso.** No solo que la migracion falle: que falle
# *antes de crear nada suyo*. Se comprueba una por una la ausencia de las cinco
# cosas que esta revision anade -- el indice de lecturas, el schema
# ``seguridad``, sus funciones, las politicas y el GRANT a la API -- y que
# ``alembic_version`` siga en la revision anterior. Un preflight que abortara a
# mitad dejaria el aislamiento a medias, que es peor que no tenerlo.

REVISION_PREVIA_A_RLS = "54053d46abd6"
REVISION_DE_RLS = "3b4a352bc39a"


def _cabeza_alembic() -> str:
    [cabeza] = ScriptDirectory.from_config(construir_config_alembic()).get_heads()
    return cabeza


def _migrar(url: str, destino: str) -> None:
    with pytest.MonkeyPatch.context() as parche:
        parche.setenv("ALEMBIC_DATABASE_URL", url)
        parche.setenv("APP_ENV", "ci")
        command.upgrade(construir_config_alembic(), destino)


@pytest.fixture
def base_para_migrar(admin, migrador, url_administrativa, bootstrap_aplicado):
    """Una base nueva del migrador, ya en la revision anterior a la de RLS.

    Nueva por prueba, y no la del migrador: una migracion que aborta a medias
    dejaria restos, y lo que cada caso afirma es precisamente que no los hay.
    """
    nombre_migrador, _ = migrador
    base = f"scrum98_pre_{_sufijo()}"
    with admin.connect() as conexion:
        conexion.exec_driver_sql(
            f'CREATE DATABASE "{base}" OWNER "{nombre_migrador}"'
        )

    url = (
        make_url(url_administrativa)
        .set(
            username=nombre_migrador,
            password=make_url(migrador[1]).password,
            database=base,
        )
        .render_as_string(hide_password=False)
    )
    _migrar(url, REVISION_PREVIA_A_RLS)

    observador = create_engine(
        make_url(url_administrativa).set(database=base),
        poolclass=NullPool,
    )
    try:
        yield url, observador
    finally:
        observador.dispose()
        with admin.connect() as conexion:
            conexion.exec_driver_sql(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{base}'"
            )
            conexion.exec_driver_sql(f'DROP DATABASE IF EXISTS "{base}"')


@pytest.fixture
def intruso(admin):
    """Dos roles ajenos al proyecto, para contaminar y retirar.

    NOLOGIN y sin capacidades: lo que los hace peligrosos no es lo que son, sino
    la concesion que se les da. Se crean con la credencial administrativa porque
    lo que se reproduce es «alguien con permisos hizo un GRANT», no una accion
    del migrador.
    """
    nombres = (f"scrum98_intruso_{_sufijo()}", f"scrum98_puente_{_sufijo()}")
    with admin.connect() as conexion:
        for nombre in nombres:
            conexion.exec_driver_sql(f'CREATE ROLE "{nombre}" NOLOGIN')
    yield nombres
    with admin.connect() as conexion:
        for nombre in nombres:
            conexion.exec_driver_sql(f'DROP ROLE IF EXISTS "{nombre}"')


def _objetos_de_la_revision(observador) -> dict:
    """Las cinco cosas que la revision de RLS crea, contadas una a una."""
    with observador.connect() as conexion:
        return {
            "revision": conexion.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one(),
            "schema_seguridad": conexion.execute(
                text("SELECT count(*) FROM pg_namespace WHERE nspname = 'seguridad'")
            ).scalar_one(),
            "helpers": conexion.execute(
                text(
                    "SELECT count(*) FROM pg_proc p JOIN pg_namespace n "
                    "ON n.oid = p.pronamespace WHERE n.nspname = 'seguridad'"
                )
            ).scalar_one(),
            "politicas": conexion.execute(
                text("SELECT count(*) FROM pg_policies WHERE schemaname = 'operacional'")
            ).scalar_one(),
            "indice": conexion.execute(
                text(
                    "SELECT count(*) FROM pg_indexes WHERE schemaname = 'operacional' "
                    "AND indexname = 'ix_lectura_biometrica_id_sesion'"
                )
            ).scalar_one(),
            "grant_api": conexion.execute(
                text(
                    "SELECT has_table_privilege(:rol, 'operacional.embarazo', 'SELECT')"
                ),
                {"rol": ROL_API},
            ).scalar_one(),
        }


def _abortar_migrando(url: str) -> str:
    """Intenta migrar a head y devuelve el mensaje del servidor. Falla si no aborta."""
    try:
        _migrar(url, "head")
    except DBAPIError as error:
        return str(error)
    pytest.fail(
        "La migracion completo el upgrade con una membresia contaminada. El "
        "preflight tenia que haberla abortado antes de crear nada."
    )


def _exigir_que_no_creo_nada(observador) -> None:
    estado = _objetos_de_la_revision(observador)

    assert estado["revision"] == REVISION_PREVIA_A_RLS
    assert estado["schema_seguridad"] == 0
    assert estado["helpers"] == 0
    assert estado["politicas"] == 0
    assert estado["indice"] == 0
    assert estado["grant_api"] is False


# --- (a) miembro inesperado, sin ninguna opcion encendida ------------------


def test_un_miembro_inesperado_sin_opciones_aborta_la_migracion(
    admin, base_para_migrar, intruso
):
    """INHERIT FALSE, SET FALSE, ADMIN FALSE no concede nada hoy.

    Y aun asi se rechaza, porque encender las opciones es un solo GRANT y
    quien ya es miembro no necesita que nadie le conceda la membresia otra vez.
    """
    url, observador = base_para_migrar
    nombre, _ = intruso
    with admin.connect() as conexion:
        conexion.exec_driver_sql(
            f'GRANT "{ROL_RLS_OWNER}" TO "{nombre}" '
            "WITH ADMIN FALSE, INHERIT FALSE, SET FALSE"
        )

    mensaje = _abortar_migrando(url)

    assert "Migracion abortada" in mensaje
    assert nombre in mensaje and ROL_RLS_OWNER in mensaje
    assert "miembro inesperado" in mensaje
    _exigir_que_no_creo_nada(observador)


# --- (b) administrador inesperado ------------------------------------------


def test_un_administrador_inesperado_aborta_la_migracion(
    admin, base_para_migrar, intruso
):
    """ADMIN TRUE con INHERIT y SET apagados: el derecho a conceder el rol."""
    url, observador = base_para_migrar
    nombre, _ = intruso
    with admin.connect() as conexion:
        conexion.exec_driver_sql(
            f'GRANT "{ROL_PROVISION_OWNER}" TO "{nombre}" '
            "WITH ADMIN TRUE, INHERIT FALSE, SET FALSE"
        )

    mensaje = _abortar_migrando(url)

    assert "Migracion abortada" in mensaje
    assert nombre in mensaje and ROL_PROVISION_OWNER in mensaje
    assert "ADMIN OPTION" in mensaje
    _exigir_que_no_creo_nada(observador)


# --- (c) un rol tecnico miembro de otro rol --------------------------------


def test_un_rol_tecnico_miembro_de_otro_rol_aborta_la_migracion(
    admin, base_para_migrar, intruso
):
    """Al reves que los anteriores: el contaminado es el propio rol tecnico."""
    url, observador = base_para_migrar
    nombre, _ = intruso
    with admin.connect() as conexion:
        conexion.exec_driver_sql(f'GRANT "{nombre}" TO "{ROL_MANTENIMIENTO}"')

    mensaje = _abortar_migrando(url)

    assert "Migracion abortada" in mensaje
    assert f"{ROL_MANTENIMIENTO} es miembro de {nombre}" in mensaje
    _exigir_que_no_creo_nada(observador)


# --- (d) cadena indirecta ---------------------------------------------------


def test_una_cadena_indirecta_hasta_un_rol_tecnico_aborta_la_migracion(
    admin, base_para_migrar, intruso
):
    """El camino largo: la API llega al dueno por un rol puente.

    Ningun GRANT nombra a la API y al dueno en la misma sentencia, que es lo que
    hace invisible este caso a una comparacion de miembros directos. Lo cazan
    dos comprobaciones a la vez -- el alcance efectivo con ``pg_has_role`` y el
    conjunto exacto de miembros directos --, y basta con que una lo haga.
    """
    url, observador = base_para_migrar
    _, puente = intruso
    with admin.connect() as conexion:
        conexion.exec_driver_sql(
            f'GRANT "{ROL_RLS_OWNER}" TO "{puente}" WITH INHERIT TRUE, SET TRUE'
        )
        conexion.exec_driver_sql(
            f'GRANT "{puente}" TO "{ROL_API}" WITH INHERIT TRUE, SET TRUE'
        )

    mensaje = _abortar_migrando(url)

    assert "Migracion abortada" in mensaje
    assert ROL_RLS_OWNER in mensaje
    assert ("alcance de un rol de runtime" in mensaje) or (
        "miembro inesperado" in mensaje
    )
    _exigir_que_no_creo_nada(observador)

    with admin.connect() as conexion:
        conexion.exec_driver_sql(f'REVOKE "{puente}" FROM "{ROL_API}"')


# --- (e) configuracion limpia ----------------------------------------------


def test_con_las_membresias_limpias_la_migracion_completa(base_para_migrar):
    """La contraparte imprescindible de las cuatro de arriba.

    Sin esta, «aborta» podria ser cierto por cualquier motivo, incluso porque el
    preflight rechace un cluster correcto. Aqui no se contamina nada y la
    revision se aplica entera: cabeza nueva, schema, nueve helpers, veintiuna
    politicas, el indice y el GRANT a la API.
    """
    url, observador = base_para_migrar

    _migrar(url, "head")
    estado = _objetos_de_la_revision(observador)

    assert estado["revision"] == _cabeza_alembic() == REVISION_DE_RLS
    assert estado["schema_seguridad"] == 1
    assert estado["helpers"] == 9
    assert estado["politicas"] == 21
    assert estado["indice"] == 1
    assert estado["grant_api"] is True
