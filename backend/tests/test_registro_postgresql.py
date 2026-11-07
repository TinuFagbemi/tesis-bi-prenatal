"""Autorregistro de pacientes contra PostgreSQL real, como ``fetalalert_api`` (SCRUM-110).

Se omite salvo que este definida ``SCRUM110_TEST_DATABASE_URL``, y esa variable
tiene que conectarse como ``fetalalert_api`` a una base **desechable**, migrada al
head y cargada con el dataset simulado. Nunca cae por omision sobre otra
variable.

**Por que con la credencial de la API.** Lo que esta suite demuestra es
justamente lo que un superusuario no deja ver: que el proceso web registra a una
paciente sin tener ningun privilegio sobre ``operacional.paciente``, que la
funcion SECURITY DEFINER y la politica nueva bastan, que el trigger diferido de
SCRUM-97 acepta la cuenta al confirmar, y que el rol de la API sigue sin poder
escribir perfiles ni vinculos por su cuenta. SCRUM-98 documento dos defectos
reales que solo aparecieron al conectarse como el rol restringido.

**Dos engines.** El de la aplicacion es el de la API y es el sujeto de cada
asercion. El observador (usuario administrativo del servidor de pruebas) solo
mira y limpia, por identificadores exactos: las cedulas y los correos de esta
suite son una lista cerrada, y la auditoria se retira por marca de agua. Esa
limpieza es aceptable **solo** en una base desechable y sin nadie escribiendo a
la vez; por eso el nombre de la base se valida antes de crear ningun engine. Es
una herramienta de prueba: el entorno de la demostracion **no** se limpia asi,
se restaura con el mecanismo del PR #24.

Todas las identidades y datos son ficticios y completamente simulados.
"""

from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.api.dependencias import get_db_auditoria, obtener_configuracion_jwt
from app.api.v1 import registro as router_registro
from app.config import ConfiguracionJWT
from app.db.session import get_db
from app.legal import vigente
from app.main import app
from app.models.enums import DocumentoLegal
from app.services.limite import LimiteDeSolicitudes
from app.services.passwords import PREFIJO_ARGON2ID, hashear, verificar

VARIABLE_DE_ENTORNO = "SCRUM110_TEST_DATABASE_URL"
ROL_ESPERADO = "fetalalert_api"
ROL_DUENO = "fetalalert_provision_owner"

BASE_EFIMERA_DE_CI = "scrum52_validacion_tmp"
PREFIJO_DE_BASE_APROBADO = re.compile(r"^scrum110_[a-z0-9_]{1,40}$")

RUTA = "/api/v1/registro/pacientes"
RUTA_TOKEN = "/api/v1/autenticacion/token"
RUTA_YO = "/api/v1/autenticacion/yo"
RUTA_EMBARAZOS = "/api/v1/clinico/embarazos"
RUTA_PROVISION = "/api/v1/cuentas/pacientes/{}"

PASSWORD = "  Clave Simulada SCRUM110 "

# Identidades ficticias de la suite. Cerradas: la limpieza las resuelve a
# identificadores y borra por identificador.
CEDULAS = tuple(f"SIM-S110-{numero:03d}" for numero in range(1, 9))
EMAILS = tuple(f"paciente{9100 + numero}@example.com" for numero in range(1, 9))
FIRMA = (
    "seguridad.autorregistrar_paciente(varchar, varchar, varchar, varchar, "
    "varchar, date, varchar, varchar, varchar, varchar, varchar, varchar, varchar)"
)

CONFIGURACION = ConfiguracionJWT(
    secreto="secreto-simulado-de-pruebas-scrum110-0001",
    expiracion=timedelta(minutes=30),
)

pytestmark = pytest.mark.skipif(
    not os.environ.get(VARIABLE_DE_ENTORNO),
    reason=(
        f"Define {VARIABLE_DE_ENTORNO} apuntando a una base desechable, migrada y "
        f"cargada, con el rol {ROL_ESPERADO}."
    ),
)


class BaseNoAutorizada(RuntimeError):
    """La URL apunta a una base que esta suite no puede tocar."""


def exigir_base_desechable(url: str) -> str:
    """El nombre de la base si esta autorizada; si no, lo explica. Sin servidor."""
    base = make_url(url).database or ""
    if base == BASE_EFIMERA_DE_CI or PREFIJO_DE_BASE_APROBADO.fullmatch(base):
        return base
    raise BaseNoAutorizada(
        f"{VARIABLE_DE_ENTORNO} apunta a la base {base!r}. Esta suite confirma "
        "registros y luego los retira, asi que solo admite la base efimera de CI "
        f"({BASE_EFIMERA_DE_CI!r}) o un nombre que empiece por 'scrum110_'. Nunca "
        "la base de la demostracion ni una base compartida."
    )


def cuerpo(numero: int = 1, **cambios) -> dict:
    datos = {
        "primer_nombre": " Lucía ",
        "segundo_nombre": "  ",
        "apellido_paterno": "Herrera",
        "apellido_materno": "Quintero",
        "cedula": CEDULAS[numero - 1],
        "fecha_nac": "1996-04-18",
        "email": EMAILS[numero - 1],
        "celular": "6000-1101",
        "password": PASSWORD,
        "confirmacion_password": PASSWORD,
        "acepta_politica_privacidad": True,
        "version_politica_privacidad": vigente(DocumentoLegal.POLITICA_PRIVACIDAD).version,
        "acepta_terminos_condiciones": True,
        "version_terminos_condiciones": vigente(DocumentoLegal.TERMINOS_CONDICIONES).version,
    }
    datos.update(cambios)
    return datos


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def url() -> str:
    valor = os.environ[VARIABLE_DE_ENTORNO]
    try:
        exigir_base_desechable(valor)
    except BaseNoAutorizada as error:
        pytest.fail(str(error))
    if make_url(valor).username != ROL_ESPERADO:
        pytest.fail(f"{VARIABLE_DE_ENTORNO} debe conectarse como {ROL_ESPERADO}.")
    return valor


@pytest.fixture(scope="session")
def engine_api(url):
    motor = create_engine(url, poolclass=NullPool)
    try:
        yield motor
    finally:
        motor.dispose()


@pytest.fixture(scope="session")
def engine_observador(url):
    administrativa = make_url(url).set(
        username=os.environ.get("SCRUM110_ADMIN_USER", "fetalalert_ci"),
        password=os.environ.get("SCRUM110_ADMIN_PASSWORD", ""),
    )
    motor = create_engine(administrativa, poolclass=NullPool)
    try:
        yield motor
    finally:
        motor.dispose()


def observar(engine, consulta: str, **parametros):
    with engine.connect() as conexion:
        return conexion.execute(text(consulta), parametros).mappings().all()


def escalar(engine, consulta: str, **parametros):
    with engine.connect() as conexion:
        return conexion.execute(text(consulta), parametros).scalar()


@pytest.fixture
def limpieza(engine_observador):
    """Retira exactamente lo de esta suite: sus cedulas, sus correos y su traza."""
    with engine_observador.connect() as conexion:
        ultimo_log = conexion.execute(
            text("SELECT coalesce(max(id_log), 0) FROM operacional.auditoria_log")
        ).scalar_one()
        previas = conexion.execute(
            text(
                "SELECT count(*) FROM operacional.paciente WHERE cedula = ANY(:c)"
                " OR email_pac = ANY(:e)"
            ),
            {"c": list(CEDULAS), "e": list(EMAILS)},
        ).scalar_one()
    assert previas == 0, "La base ya contiene identidades de esta suite: no es desechable."

    yield

    with engine_observador.begin() as conexion:
        cuentas = list(
            conexion.execute(
                text("SELECT id_usuario FROM operacional.usuario WHERE email = ANY(:e)"),
                {"e": list(EMAILS)},
            ).scalars()
        )
        conexion.execute(
            text("DELETE FROM operacional.auditoria_log WHERE id_log > :desde"),
            {"desde": ultimo_log},
        )
        if cuentas:
            conexion.execute(
                text("DELETE FROM operacional.aceptacion_documento WHERE id_usuario = ANY(:ids)"),
                {"ids": cuentas},
            )
            conexion.execute(
                text("DELETE FROM operacional.usuario_paciente WHERE id_usuario = ANY(:ids)"),
                {"ids": cuentas},
            )
            conexion.execute(
                text("DELETE FROM operacional.usuario WHERE id_usuario = ANY(:ids)"),
                {"ids": cuentas},
            )
        # El telefono se va en cascada con el perfil.
        conexion.execute(
            text("DELETE FROM operacional.paciente WHERE cedula = ANY(:c)"),
            {"c": list(CEDULAS)},
        )


@contextmanager
def api_sobre(engine):
    def sesion_negocio():
        with Session(bind=engine, autoflush=False) as sesion_bd:
            yield sesion_bd

    def sesion_auditoria():
        with Session(bind=engine, autoflush=False) as sesion_bd:
            yield sesion_bd

    holgado = LimiteDeSolicitudes(maximo_por_origen=1000, maximo_global=1000, ventana_segundos=60)
    app.dependency_overrides[get_db] = sesion_negocio
    app.dependency_overrides[get_db_auditoria] = sesion_auditoria
    app.dependency_overrides[obtener_configuracion_jwt] = lambda: CONFIGURACION
    app.dependency_overrides[router_registro.obtener_limitador] = lambda: holgado
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def cliente(engine_api, limpieza):
    with api_sobre(engine_api) as cliente:
        yield cliente


def conteos(engine_observador) -> dict[str, int]:
    return {
        tabla: escalar(engine_observador, f"SELECT count(*) FROM operacional.{tabla}")
        for tabla in (
            "paciente",
            "telefono_paciente",
            "usuario",
            "usuario_paciente",
            "aceptacion_documento",
        )
    }


# ---------------------------------------------------------------------------
# 1. La credencial
# ---------------------------------------------------------------------------


def test_el_engine_de_la_aplicacion_es_el_rol_de_la_api(engine_api):
    with engine_api.connect() as conexion:
        assert conexion.execute(text("SELECT current_user")).scalar() == ROL_ESPERADO
        assert not conexion.execute(
            text("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user")
        ).scalar()


# ---------------------------------------------------------------------------
# 2. Registro real, inicio de sesion inmediato y portal vacio
# ---------------------------------------------------------------------------


def test_el_registro_guarda_perfil_telefono_cuenta_vinculo_y_aceptaciones(cliente, engine_observador):
    antes = escalar(engine_observador, "SELECT now()")
    respuesta = cliente.post(RUTA, json=cuerpo())
    despues = escalar(engine_observador, "SELECT now()")

    assert respuesta.status_code == 201, respuesta.text
    datos = respuesta.json()
    assert datos["rol"] == "PACIENTE" and datos["activo"] is True
    id_usuario, id_paciente = datos["id_usuario"], datos["id_paciente"]

    [perfil] = observar(
        engine_observador,
        "SELECT * FROM operacional.paciente WHERE id_paciente = :p",
        p=id_paciente,
    )
    assert perfil["cedula"] == CEDULAS[0]
    assert perfil["primer_nombre"] == "Lucía"
    assert perfil["segundo_nombre"] is None
    assert perfil["apellido_materno"] == "Quintero"
    assert perfil["email_pac"] == EMAILS[0]
    assert str(perfil["fecha_nac"]) == "1996-04-18"

    telefonos = observar(
        engine_observador,
        "SELECT tipo_contacto, valor_contacto, principal FROM operacional.telefono_paciente"
        " WHERE id_paciente = :p",
        p=id_paciente,
    )
    assert [dict(t) for t in telefonos] == [
        {"tipo_contacto": "CELULAR", "valor_contacto": "6000-1101", "principal": True}
    ]

    [cuenta] = observar(
        engine_observador,
        "SELECT u.email, u.password_hash, u.activo, r.nombre_rol FROM operacional.usuario u"
        " JOIN operacional.rol r USING (id_rol) WHERE u.id_usuario = :u",
        u=id_usuario,
    )
    assert cuenta["email"] == EMAILS[0]
    assert cuenta["activo"] is True
    assert cuenta["nombre_rol"] == "PACIENTE"
    assert cuenta["password_hash"].startswith(PREFIJO_ARGON2ID)
    assert verificar(cuenta["password_hash"], PASSWORD)
    assert not verificar(cuenta["password_hash"], PASSWORD.strip())

    assert escalar(
        engine_observador,
        "SELECT id_paciente FROM operacional.usuario_paciente WHERE id_usuario = :u",
        u=id_usuario,
    ) == id_paciente

    aceptaciones = observar(
        engine_observador,
        "SELECT documento, version, sha256, fecha_hora FROM operacional.aceptacion_documento"
        " WHERE id_usuario = :u ORDER BY documento",
        u=id_usuario,
    )
    assert [(a["documento"], a["version"], a["sha256"]) for a in aceptaciones] == [
        (d.value, vigente(d).version, vigente(d).sha256)
        for d in sorted(DocumentoLegal, key=lambda d: d.value)
    ]
    assert all(antes <= a["fecha_hora"] <= despues for a in aceptaciones)

    traza = observar(
        engine_observador,
        "SELECT id_usuario, accion, nombre_entidad_afectada, id_entidad_afectada"
        " FROM operacional.auditoria_log WHERE id_entidad_afectada = :e"
        " AND accion = 'CUENTA_PACIENTE_AUTORREGISTRADA'",
        e=str(id_usuario),
    )
    assert [dict(t) for t in traza] == [
        {
            "id_usuario": id_usuario,
            "accion": "CUENTA_PACIENTE_AUTORREGISTRADA",
            "nombre_entidad_afectada": "usuario",
            "id_entidad_afectada": str(id_usuario),
        }
    ]


def test_la_paciente_inicia_sesion_de_inmediato_y_no_tiene_embarazos(cliente):
    id_usuario = cliente.post(RUTA, json=cuerpo()).json()["id_usuario"]

    login = cliente.post(RUTA_TOKEN, json={"email": EMAILS[0].upper(), "password": PASSWORD})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    cabecera = {"Authorization": f"Bearer {token}"}

    assert cliente.get(RUTA_YO, headers=cabecera).json() == {"id_usuario": id_usuario, "rol": "PACIENTE"}
    embarazos = cliente.get(RUTA_EMBARAZOS, headers=cabecera)
    assert embarazos.status_code == 200
    assert embarazos.json() == []


def test_la_paciente_nueva_no_puede_usar_las_rutas_administrativas(cliente):
    cliente.post(RUTA, json=cuerpo())
    token = cliente.post(RUTA_TOKEN, json={"email": EMAILS[0], "password": PASSWORD}).json()["access_token"]

    respuesta = cliente.post(
        RUTA_PROVISION.format(100),
        json={"email": EMAILS[1], "password": PASSWORD},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert respuesta.status_code == 403


# ---------------------------------------------------------------------------
# 3. Duplicados y concurrencia
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "segundo",
    [
        {"email": EMAILS[1]},  # misma cedula
        {"cedula": CEDULAS[1], "email": EMAILS[0].upper()},  # mismo correo, otra forma
    ],
)
def test_un_duplicado_es_409_y_no_crea_nada(cliente, engine_observador, segundo):
    assert cliente.post(RUTA, json=cuerpo()).status_code == 201
    antes = conteos(engine_observador)

    respuesta = cliente.post(RUTA, json=cuerpo(**segundo))

    assert respuesta.status_code == 409
    assert CEDULAS[0] not in respuesta.text and EMAILS[0] not in respuesta.text
    assert conteos(engine_observador) == antes


def test_una_carrera_deja_exactamente_un_registro(engine_api, limpieza, engine_observador):
    antes = conteos(engine_observador)
    barrera = threading.Barrier(4)
    estados: list[int] = []
    candado = threading.Lock()

    def registrar():
        # Un cliente por hilo, sin ``with``: el ciclo de vida de la aplicacion no
        # se arranca, igual que en el resto de la suite.
        propio = TestClient(app, raise_server_exceptions=False)
        barrera.wait(timeout=20)
        estado = propio.post(RUTA, json=cuerpo()).status_code
        with candado:
            estados.append(estado)

    with api_sobre(engine_api):
        hilos = [threading.Thread(target=registrar) for _ in range(4)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=60)

    assert sorted(estados) == [201, 409, 409, 409]
    despues = conteos(engine_observador)
    assert {tabla: despues[tabla] - antes[tabla] for tabla in antes} == {
        "paciente": 1,
        "telefono_paciente": 1,
        "usuario": 1,
        "usuario_paciente": 1,
        "aceptacion_documento": 2,
    }


# ---------------------------------------------------------------------------
# 4. Rollback completo
# ---------------------------------------------------------------------------


def test_un_fallo_despues_de_la_funcion_no_deja_nada(cliente, engine_observador, monkeypatch):
    antes = conteos(engine_observador)

    def fallar(*args, **kwargs):
        raise RuntimeError("fallo simulado de la auditoria")

    monkeypatch.setattr(router_registro.auditoria, "registrar", fallar)
    respuesta = cliente.post(RUTA, json=cuerpo())

    assert respuesta.status_code == 500
    assert conteos(engine_observador) == antes
    assert escalar(
        engine_observador,
        "SELECT count(*) FROM operacional.paciente WHERE cedula = :c",
        c=CEDULAS[0],
    ) == 0


# ---------------------------------------------------------------------------
# 5. Sin escalamiento de privilegios
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentencia",
    [
        "INSERT INTO operacional.paciente (cedula, primer_nombre, apellido_paterno, email_pac, fecha_nac)"
        " VALUES ('SIM-S110-009', 'X', 'Y', 'x.scrum110@example.com', DATE '1990-01-01')",
        "INSERT INTO operacional.telefono_paciente (id_paciente, tipo_contacto, valor_contacto, principal)"
        " VALUES (100, 'CELULAR', '6000-0000', false)",
        "SELECT count(*) FROM operacional.aceptacion_documento",
        "INSERT INTO operacional.aceptacion_documento (id_usuario, documento, version, sha256)"
        " VALUES (107, 'POLITICA_PRIVACIDAD', 'x', repeat('a', 64))",
        "UPDATE operacional.paciente SET primer_nombre = 'X' WHERE id_paciente = 100",
    ],
)
def test_la_api_sigue_sin_privilegios_directos(engine_api, sentencia):
    with engine_api.connect() as conexion:
        with pytest.raises(ProgrammingError) as error:
            conexion.execute(text(sentencia))
        conexion.rollback()
    assert error.value.orig.sqlstate == "42501"


def test_la_api_no_puede_vincular_cuentas_por_su_cuenta(engine_api):
    """``pol_provision`` sigue exigiendo ``es_admin()``: sin contexto, se rechaza."""
    with engine_api.connect() as conexion:
        with pytest.raises(DBAPIError) as error:
            conexion.execute(
                text("INSERT INTO operacional.usuario_paciente (id_usuario, id_paciente) VALUES (107, 100)")
            )
        conexion.rollback()
    # El WITH CHECK de la politica se evalua antes que el UNIQUE del puente.
    assert error.value.orig.sqlstate == "42501"


def test_la_funcion_es_del_dueno_nologin_y_solo_la_ejecuta_la_api(engine_observador):
    [fila] = observar(
        engine_observador,
        "SELECT p.prosecdef, pg_get_userbyid(p.proowner) AS dueno,"
        "       array_to_string(p.proconfig, ',') AS config"
        "  FROM pg_proc p WHERE p.oid = CAST(:firma AS regprocedure)",
        firma=FIRMA,
    )
    assert fila["prosecdef"] is True
    assert fila["dueno"] == ROL_DUENO
    assert "search_path=pg_catalog, operacional, pg_temp" in fila["config"]
    for rol, puede in (
        ("fetalalert_api", True),
        ("fetalalert_etl", False),
        ("fetalalert_powerbi", False),
    ):
        assert escalar(
            engine_observador,
            "SELECT has_function_privilege(:rol, CAST(:firma AS regprocedure), 'EXECUTE')",
            rol=rol,
            firma=FIRMA,
        ) is puede
    assert escalar(
        engine_observador,
        "SELECT rolcanlogin OR rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = :r",
        r=ROL_DUENO,
    ) is False


def test_las_rutas_administrativas_siguen_siendo_solo_de_admin(cliente):
    assert cliente.post(RUTA_PROVISION.format(100), json={"email": EMAILS[0], "password": "x"}).status_code == 401


@pytest.mark.parametrize(
    "cambio",
    [
        {"p_primer_nombre": "   "},
        {"p_cedula": ""},
        {"p_celular": " "},
        {"p_password_hash": "en-claro"},
        {"p_fecha_nac": "2999-01-01"},
    ],
)
def test_la_funcion_rechaza_por_su_cuenta_lo_que_la_api_ya_rechaza(engine_api, cambio):
    """Segunda capa: llamada directa como la API, sin pasar por el esquema."""
    parametros = {
        "p_cedula": CEDULAS[7],
        "p_primer_nombre": "Ana",
        "p_segundo_nombre": None,
        "p_apellido_paterno": "Ruiz",
        "p_apellido_materno": None,
        "p_fecha_nac": "1990-01-01",
        "p_email": EMAILS[7],
        "p_celular": "6000-0008",
        "p_password_hash": hashear("x"),
        "p_vp": "v",
        "p_sp": "a" * 64,
        "p_vt": "v",
        "p_st": "a" * 64,
    }
    parametros.update(cambio)
    with engine_api.connect() as conexion:
        with pytest.raises(DBAPIError) as error:
            conexion.execute(
                text(
                    "SELECT * FROM seguridad.autorregistrar_paciente("
                    " CAST(:p_cedula AS varchar), CAST(:p_primer_nombre AS varchar),"
                    " CAST(:p_segundo_nombre AS varchar), CAST(:p_apellido_paterno AS varchar),"
                    " CAST(:p_apellido_materno AS varchar), CAST(:p_fecha_nac AS date),"
                    " CAST(:p_email AS varchar), CAST(:p_celular AS varchar),"
                    " CAST(:p_password_hash AS varchar), CAST(:p_vp AS varchar),"
                    " CAST(:p_sp AS varchar), CAST(:p_vt AS varchar), CAST(:p_st AS varchar))"
                ),
                parametros,
            )
        conexion.rollback()
    assert error.value.orig.sqlstate == "23514"
    assert error.value.orig.diag.constraint_name == "autorregistro_datos_validos"


# ---------------------------------------------------------------------------
# 6. Rollback cuando falla el propio commit
# ---------------------------------------------------------------------------

# Un trigger de restriccion diferido, creado solo para esta prueba por el
# observador y retirado al terminar, que rechaza el commit cuando la aceptacion
# pertenece a la cuenta de un correo centinela. Reproduce lo que hace el trigger
# de coherencia de SCRUM-97: todo se inserta bien y la negativa llega en el
# COMMIT. La funcion no es SECURITY DEFINER: corre como quien confirma.
EMAIL_CENTINELA_COMMIT = EMAILS[6]
FUNCION_DE_FALLO = "public.scrum110_prueba_fallo_en_commit"
TRIGGER_DE_FALLO = "scrum110_prueba_fallo_en_commit"

CUERPO_DE_FALLO = f"""
CREATE FUNCTION {FUNCION_DE_FALLO}() RETURNS trigger LANGUAGE plpgsql AS $f$
BEGIN
    IF EXISTS (
        SELECT 1 FROM operacional.usuario u
         WHERE u.id_usuario = NEW.id_usuario
           AND u.email = '{EMAIL_CENTINELA_COMMIT}'
    ) THEN
        RAISE EXCEPTION 'rechazo simulado en el commit';
    END IF;
    RETURN NULL;
END
$f$
"""


@pytest.fixture
def fallo_en_commit(engine_observador):
    with engine_observador.begin() as conexion:
        conexion.exec_driver_sql(CUERPO_DE_FALLO)
        conexion.exec_driver_sql(
            f"CREATE CONSTRAINT TRIGGER {TRIGGER_DE_FALLO} "
            "AFTER INSERT ON operacional.aceptacion_documento "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            f"EXECUTE FUNCTION {FUNCION_DE_FALLO}()"
        )
    try:
        yield
    finally:
        with engine_observador.begin() as conexion:
            conexion.exec_driver_sql(
                f"DROP TRIGGER IF EXISTS {TRIGGER_DE_FALLO} ON operacional.aceptacion_documento"
            )
            conexion.exec_driver_sql(f"DROP FUNCTION IF EXISTS {FUNCION_DE_FALLO}()")


def test_un_rechazo_en_el_commit_no_deja_nada(cliente, engine_observador, fallo_en_commit):
    antes = conteos(engine_observador)
    ultimo_log = escalar(
        engine_observador, "SELECT coalesce(max(id_log), 0) FROM operacional.auditoria_log"
    )

    respuesta = cliente.post(RUTA, json=cuerpo(7))

    assert respuesta.status_code == 500
    assert "rechazo simulado" not in respuesta.text
    assert conteos(engine_observador) == antes
    assert escalar(
        engine_observador,
        "SELECT count(*) FROM operacional.paciente WHERE cedula = :c",
        c=CEDULAS[6],
    ) == 0
    assert escalar(
        engine_observador,
        "SELECT count(*) FROM operacional.auditoria_log WHERE id_log > :d",
        d=ultimo_log,
    ) == 0
    # Y la cuenta no existe: no se puede iniciar sesion con ella.
    login = cliente.post(RUTA_TOKEN, json={"email": EMAIL_CENTINELA_COMMIT, "password": PASSWORD})
    assert login.status_code == 401


def test_sin_el_rechazo_el_mismo_registro_se_confirma(cliente):
    """Control: lo que hizo fallar la prueba anterior fue el trigger, no los datos."""
    assert cliente.post(RUTA, json=cuerpo(7)).status_code == 201


# ---------------------------------------------------------------------------
# 7. La funcion SECURITY DEFINER, mirada en el catalogo
# ---------------------------------------------------------------------------


def test_public_no_puede_ejecutar_la_funcion(engine_observador):
    publicos = escalar(
        engine_observador,
        "SELECT count(*) FROM pg_proc p, aclexplode(p.proacl) a"
        " WHERE p.oid = CAST(:firma AS regprocedure) AND a.grantee = 0",
        firma=FIRMA,
    )
    assert publicos == 0
    concedidos = {
        fila["rolname"]
        for fila in observar(
            engine_observador,
            "SELECT r.rolname FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) a"
            " JOIN pg_roles r ON r.oid = a.grantee"
            " WHERE p.oid = CAST(:firma AS regprocedure) AND a.privilege_type = 'EXECUTE'",
            firma=FIRMA,
        )
    }
    # El dueno conserva el suyo; la unica concesion a otro rol es la de la API.
    assert concedidos == {"fetalalert_api", ROL_DUENO}


def test_la_funcion_no_tiene_ningun_parametro_de_rol_estado_o_perfil(engine_observador):
    nombres = escalar(
        engine_observador,
        "SELECT array_to_string(proargnames, ',') FROM pg_proc"
        " WHERE oid = CAST(:firma AS regprocedure)",
        firma=FIRMA,
    ).split(",")
    entradas = nombres[:13]
    assert entradas == [
        "p_cedula",
        "p_primer_nombre",
        "p_segundo_nombre",
        "p_apellido_paterno",
        "p_apellido_materno",
        "p_fecha_nac",
        "p_email",
        "p_celular",
        "p_password_hash",
        "p_version_privacidad",
        "p_sha256_privacidad",
        "p_version_terminos",
        "p_sha256_terminos",
    ]
    assert nombres[13:] == ["id_paciente_creado", "id_usuario_creado"]


def test_llamada_directa_como_la_api_siempre_crea_una_paciente_activa(
    engine_api, engine_observador, limpieza
):
    """Ni siquiera saltandose el esquema se obtiene otro rol: no hay forma de pedirlo."""
    with engine_api.begin() as conexion:
        fila = conexion.execute(
            text(
                "SELECT * FROM seguridad.autorregistrar_paciente("
                " CAST(:c AS varchar), 'Ana', NULL, 'Ruiz', NULL, DATE '1990-01-01',"
                " CAST(:e AS varchar), '6000-0006', CAST(:h AS varchar),"
                " 'v', repeat('a', 64), 'v', repeat('b', 64))"
            ),
            {"c": CEDULAS[5], "e": EMAILS[5], "h": hashear("x")},
        ).one()
    [cuenta] = observar(
        engine_observador,
        "SELECT r.nombre_rol, u.activo FROM operacional.usuario u"
        " JOIN operacional.rol r USING (id_rol) WHERE u.id_usuario = :u",
        u=fila.id_usuario_creado,
    )
    assert (cuenta["nombre_rol"], cuenta["activo"]) == ("PACIENTE", True)
    assert escalar(
        engine_observador,
        "SELECT id_paciente FROM operacional.usuario_paciente WHERE id_usuario = :u",
        u=fila.id_usuario_creado,
    ) == fila.id_paciente_creado


@pytest.mark.parametrize(
    "tabla,privilegio,esperado",
    [
        # Lo que el dueno necesita: insertar.
        ("paciente", "INSERT", True),
        ("telefono_paciente", "INSERT", True),
        ("usuario", "INSERT", True),
        ("usuario_paciente", "INSERT", True),
        ("aceptacion_documento", "INSERT", True),
        # Lo que no se le concedio.
        ("paciente", "DELETE", False),
        ("telefono_paciente", "UPDATE", False),
        ("telefono_paciente", "DELETE", False),
        ("usuario", "UPDATE", False),
        ("usuario", "DELETE", False),
        ("usuario_paciente", "UPDATE", False),
        ("usuario_paciente", "DELETE", False),
        ("aceptacion_documento", "SELECT", False),
        ("aceptacion_documento", "UPDATE", False),
        ("aceptacion_documento", "DELETE", False),
        ("usuario_medico", "INSERT", False),
        ("medico", "INSERT", False),
        ("embarazo", "INSERT", False),
        ("auditoria_log", "INSERT", False),
        ("rol", "INSERT", False),
    ],
)
def test_el_dueno_tiene_solo_los_privilegios_minimos(engine_observador, tabla, privilegio, esperado):
    assert escalar(
        engine_observador,
        "SELECT has_table_privilege(:rol, CAST(:tabla AS regclass), :p)",
        rol=ROL_DUENO,
        tabla=f"operacional.{tabla}",
        p=privilegio,
    ) is esperado


@pytest.mark.parametrize("tabla", ["paciente", "telefono_paciente", "aceptacion_documento"])
@pytest.mark.parametrize("privilegio", ["SELECT", "INSERT", "UPDATE", "DELETE"])
def test_la_api_no_tiene_privilegios_sobre_perfil_telefono_ni_aceptaciones(
    engine_observador, tabla, privilegio
):
    assert escalar(
        engine_observador,
        "SELECT has_table_privilege('fetalalert_api', CAST(:t AS regclass), :p)",
        t=f"operacional.{tabla}",
        p=privilegio,
    ) is False


def test_la_politica_nueva_es_solo_del_dueno_y_solo_insert(engine_observador):
    [politica] = observar(
        engine_observador,
        "SELECT cmd, roles::text AS roles FROM pg_policies"
        " WHERE schemaname = 'operacional' AND policyname = 'pol_autorregistro'",
    )
    assert politica["cmd"] == "INSERT"
    assert politica["roles"] == "{fetalalert_provision_owner}"
    # Y pol_provision sigue exigiendo es_admin() para la API.
    [provision] = observar(
        engine_observador,
        "SELECT with_check FROM pg_policies WHERE schemaname = 'operacional'"
        " AND tablename = 'usuario_paciente' AND policyname = 'pol_provision'",
    )
    assert "es_admin()" in provision["with_check"]
