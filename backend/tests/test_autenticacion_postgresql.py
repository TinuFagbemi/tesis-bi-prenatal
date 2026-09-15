"""Autenticacion, RBAC y auditoria contra un PostgreSQL 16 real (SCRUM-70).

Se omite salvo que este definida ``SCRUM70_TEST_DATABASE_URL``. Nunca cae por
omision sobre ``DATABASE_URL``: una suite que hace **commits reales** no puede
terminar sobre la base de desarrollo de alguien por haberse olvidado de exportar
una variable. El workflow de CI convierte la omision en un job en rojo.

**Aislamiento: bases temporales, no transacciones revertidas.** Las demas suites
de la API se apoyan en una transaccion exterior que siempre revierte, y eso basta
cuando todo ocurre dentro de la transaccion de la peticion. Aqui no basta: la
auditoria del login y la de una denegacion usan **una transaccion propia que
confirma**, que es justamente la decision que hay que comprobar, y un SAVEPOINT
exterior no puede contener un commit ajeno. Asi que esta suite crea sus propias
bases con prefijo ``scrum70_tmp_``, las migra, carga el dataset canonico, escribe
de verdad y las elimina al terminar. Solo elimina las que ella creo.

Lo que se valida con datos reales y no con dobles: que las 37 cuentas simuladas
tienen hashes Argon2id que verifican, que el rol sale de PostgreSQL, que
``auditoria_log`` acepta las filas del catalogo con sus restricciones reales
--``id_usuario`` nulo en el login fallido, ``ip_origen`` NOT NULL, la FK
RESTRICT-- y que un rollback de negocio no deja una auditoria de exito.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.api.dependencias import get_db_auditoria, obtener_configuracion_jwt
from app.config import ConfiguracionJWT, settings
from app.db.base import SCHEMA_OPERACIONAL as O
from app.db.session import get_db
from app.loader.dataset import validar_dataset
from app.loader.postgres import cargar_dataset, preflight
from app.main import app
from app.models.enums import NombreRol
from app.services.auditoria import AccionAuditada
from app.services.passwords import PREFIJO_ARGON2ID, verificar
from tests.conftest import construir_config_alembic
from tests.test_etl_postgresql import PAQUETE_INCREMENTO
from tests.test_generate_mock_data import cargar_generador
from tests.test_ingestion_schemas import paquete

VARIABLE_DE_ENTORNO = "SCRUM70_TEST_DATABASE_URL"
PREFIJO_DE_BASE = "scrum70_tmp_"
PATRON_DE_BASE = re.compile(r"^scrum70_tmp_[0-9a-f]{8}_[a-z0-9_]{1,30}$")

pytestmark = pytest.mark.skipif(
    not os.environ.get(VARIABLE_DE_ENTORNO),
    reason=(
        f"Define {VARIABLE_DE_ENTORNO} apuntando a un servidor PostgreSQL donde "
        "el rol pueda crear bases temporales para validar la autenticacion."
    ),
)

SECRETO = "secreto-ficticio-de-la-suite-postgresql-de-scrum70"
CONFIGURACION = ConfiguracionJWT(secreto=SECRETO, expiracion=timedelta(minutes=30))

RUTA_TOKEN = "/api/v1/autenticacion/token"
RUTA_YO = "/api/v1/autenticacion/yo"
RUTA_INGESTA = "/api/v1/sesiones-monitoreo"

# Cuentas del dataset canonico. Los identificadores salen de su numeracion
# determinista: 2 ADMIN, luego 5 MEDICO, luego 30 PACIENTE, desde 100.
EMAIL_ADMIN = "admin01@example.com"
EMAIL_MEDICO = "medico01@example.com"
EMAIL_PACIENTE = "paciente01@example.com"

CONTADOR = itertools.count(1)


# ---------------------------------------------------------------------------
# Servidor y bases temporales
# ---------------------------------------------------------------------------


class ServidorDePruebas:
    """Crea y elimina bases temporales; nunca toca una que no haya creado."""

    def __init__(self, url: str) -> None:
        self.url = make_url(url)
        self.ejecucion = uuid.uuid4().hex[:8]
        self.engine = create_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
        self.creadas: list[str] = []

    def _citar(self, nombre: str) -> str:
        return self.engine.dialect.identifier_preparer.quote(nombre)

    def url_de(self, nombre: str) -> str:
        return self.url.set(database=nombre).render_as_string(hide_password=False)

    def crear(self, sufijo: str, *, plantilla: str | None = None) -> str:
        nombre = f"{PREFIJO_DE_BASE}{self.ejecucion}_{sufijo}"
        assert PATRON_DE_BASE.match(nombre), nombre
        sentencia = f"CREATE DATABASE {self._citar(nombre)}"
        if plantilla is not None:
            assert plantilla in self.creadas
            sentencia += f" TEMPLATE {self._citar(plantilla)}"
        with self.engine.connect() as conexion:
            conexion.execute(text(sentencia))
        self.creadas.append(nombre)
        return nombre

    def eliminar(self, nombre: str) -> None:
        if nombre not in self.creadas or not PATRON_DE_BASE.match(nombre):
            raise AssertionError(f"Se rechaza eliminar una base no creada aqui: {nombre}")
        with self.engine.connect() as conexion:
            conexion.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :nombre AND pid <> pg_backend_pid()"
                ),
                {"nombre": nombre},
            )
            conexion.execute(text(f"DROP DATABASE {self._citar(nombre)}"))
        self.creadas.remove(nombre)


class BaseTemporal:
    def __init__(self, nombre: str, url: str) -> None:
        self.nombre = nombre
        self.url = url
        self.engine = create_engine(url, poolclass=NullPool)

    def escalar(self, sql: str, **parametros: Any) -> Any:
        with self.engine.connect() as conexion:
            return conexion.execute(text(sql), parametros).scalar()

    def filas(self, sql: str, **parametros: Any) -> list:
        with self.engine.connect() as conexion:
            return list(conexion.execute(text(sql), parametros))

    def escribir(self, sql: str, **parametros: Any) -> None:
        with self.engine.begin() as conexion:
            conexion.execute(text(sql), parametros)


def migrar(url: str, destino: str) -> None:
    with pytest.MonkeyPatch.context() as parche:
        parche.setattr(settings, "database_url", url)
        command.upgrade(construir_config_alembic(), destino)


@pytest.fixture(scope="session")
def servidor():
    url = os.environ[VARIABLE_DE_ENTORNO]
    if not make_url(url).get_backend_name().startswith("postgresql"):
        pytest.fail(f"{VARIABLE_DE_ENTORNO} debe apuntar a PostgreSQL.")

    servidor = ServidorDePruebas(url)
    try:
        yield servidor
    finally:
        for nombre in reversed(list(servidor.creadas)):
            servidor.eliminar(nombre)
        servidor.engine.dispose()


@pytest.fixture(scope="session")
def generador():
    return cargar_generador()


@pytest.fixture(scope="session")
def dataset_canonico(tmp_path_factory, generador):
    carpeta = tmp_path_factory.mktemp("dataset_scrum70")
    with pytest.MonkeyPatch.context() as parche:
        parche.setattr(generador, "CARPETA_SALIDA", carpeta)
        generador.main()
    return validar_dataset(
        json.loads((carpeta / "dataset_fetalalert.json").read_text(encoding="utf-8"))
    )


@pytest.fixture(scope="session")
def plantilla(servidor, dataset_canonico) -> str:
    """Base migrada a head con el dataset canonico y ``auditoria_log`` vacia."""
    nombre = servidor.crear("plantilla")
    url = servidor.url_de(nombre)
    migrar(url, "head")
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as conexion:
            revision = preflight(conexion)
        with engine.begin() as conexion:
            cargar_dataset(conexion, dataset_canonico, revision=revision)
    finally:
        engine.dispose()
    return nombre


@contextmanager
def base_nueva(servidor, plantilla):
    nombre = servidor.crear(f"c{next(CONTADOR)}", plantilla=plantilla)
    base = BaseTemporal(nombre, servidor.url_de(nombre))
    try:
        yield base
    finally:
        base.engine.dispose()
        servidor.eliminar(nombre)


@pytest.fixture
def base(servidor, plantilla):
    """Una copia limpia por prueba: los commits reales no se contaminan entre si."""
    with base_nueva(servidor, plantilla) as temporal:
        yield temporal


@contextmanager
def api_sobre(base: BaseTemporal):
    """La aplicacion real, con sus dos sesiones atadas a la base temporal."""

    def sesion():
        with Session(bind=base.engine, autoflush=False) as sesion_bd:
            yield sesion_bd

    app.dependency_overrides[get_db] = sesion
    app.dependency_overrides[get_db_auditoria] = sesion
    app.dependency_overrides[obtener_configuracion_jwt] = lambda: CONFIGURACION
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def cliente(base):
    with api_sobre(base) as cliente:
        yield cliente


@pytest.fixture(scope="session")
def password(generador) -> str:
    return generador.PASSWORD_SIMULADA


def iniciar_sesion(cliente, email: str, password: str) -> str:
    respuesta = cliente.post(RUTA_TOKEN, json={"email": email, "password": password})
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()["access_token"]


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def auditoria_de(base: BaseTemporal) -> list:
    return base.filas(
        f"SELECT id_log, id_usuario, accion, nombre_entidad_afectada,"
        f" id_entidad_afectada, ip_origen, fecha_hora FROM {O}.auditoria_log"
        " ORDER BY id_log"
    )


# ---------------------------------------------------------------------------
# 1. El dataset simulado es autenticable de verdad
# ---------------------------------------------------------------------------


def test_las_37_cuentas_existen_con_su_rol(base):
    conteo = base.filas(
        f"SELECT r.nombre_rol, count(*) AS total FROM {O}.usuario u"
        f" JOIN {O}.rol r ON r.id_rol = u.id_rol GROUP BY r.nombre_rol"
    )

    assert {fila.nombre_rol: fila.total for fila in conteo} == {
        "ADMIN": 2,
        "MEDICO": 5,
        "PACIENTE": 30,
    }


def test_todos_los_hashes_almacenados_son_argon2id(base):
    hashes = [fila[0] for fila in base.filas(f"SELECT password_hash FROM {O}.usuario")]

    assert len(hashes) == 37
    assert all(h.startswith(PREFIJO_ARGON2ID) for h in hashes)
    assert len(set(hashes)) == 37


def test_los_hashes_reales_verifican(base, password):
    hashes = [fila[0] for fila in base.filas(f"SELECT password_hash FROM {O}.usuario")]

    assert all(verificar(h, password) for h in hashes)
    assert not any(verificar(h, "no-es-la-contrasena") for h in hashes)


def test_auditoria_log_empieza_vacia(base):
    assert base.escalar(f"SELECT count(*) FROM {O}.auditoria_log") == 0


# ---------------------------------------------------------------------------
# 2. Login contra filas reales
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "email,rol",
    [
        (EMAIL_ADMIN, "ADMIN"),
        (EMAIL_MEDICO, "MEDICO"),
        (EMAIL_PACIENTE, "PACIENTE"),
    ],
)
def test_cada_perfil_puede_autenticarse(cliente, base, password, email, rol):
    token = iniciar_sesion(cliente, email, password)

    identidad = cliente.get(RUTA_YO, headers=bearer(token))
    assert identidad.status_code == 200
    assert identidad.json()["rol"] == rol


def test_el_login_exitoso_deja_su_auditoria_confirmada(cliente, base, password):
    iniciar_sesion(cliente, EMAIL_PACIENTE, password)

    filas = auditoria_de(base)
    assert len(filas) == 1
    assert filas[0].accion == AccionAuditada.LOGIN_EXITOSO.value
    assert filas[0].id_usuario is not None
    assert filas[0].nombre_entidad_afectada == "usuario"


def test_el_login_fallido_se_registra_con_actor_nulo(cliente, base, password):
    """La columna es nullable exactamente para este caso."""
    respuesta = cliente.post(
        RUTA_TOKEN, json={"email": "nadie@example.com", "password": password}
    )

    assert respuesta.status_code == 401
    filas = auditoria_de(base)
    assert len(filas) == 1
    assert filas[0].accion == AccionAuditada.LOGIN_FALLIDO.value
    assert filas[0].id_usuario is None
    assert filas[0].nombre_entidad_afectada is None


def test_el_correo_intentado_no_queda_en_ninguna_columna(cliente, base, password):
    sonda = "sonda.de.enumeracion@example.com"
    cliente.post(RUTA_TOKEN, json={"email": sonda, "password": password})

    for fila in auditoria_de(base):
        assert sonda not in " ".join(str(v) for v in fila)


def test_una_cuenta_desactivada_no_puede_autenticarse(cliente, base, password):
    base.escribir(
        f"UPDATE {O}.usuario SET activo = false WHERE email = :email",
        email=EMAIL_PACIENTE,
    )

    respuesta = cliente.post(
        RUTA_TOKEN, json={"email": EMAIL_PACIENTE, "password": password}
    )

    assert respuesta.status_code == 401
    assert auditoria_de(base)[0].accion == AccionAuditada.LOGIN_FALLIDO.value


def test_la_ip_origen_siempre_tiene_valor(cliente, base, password):
    """La columna es NOT NULL, y la prueba lo ejerce contra el servidor real."""
    iniciar_sesion(cliente, EMAIL_PACIENTE, password)
    cliente.post(RUTA_TOKEN, json={"email": "nadie@example.com", "password": "x"})

    for fila in auditoria_de(base):
        assert fila.ip_origen
        assert len(fila.ip_origen) <= 45


def test_la_fecha_hora_es_timestamptz(base):
    tipo = base.escalar(
        "SELECT data_type FROM information_schema.columns"
        " WHERE table_schema = :esquema AND table_name = 'auditoria_log'"
        " AND column_name = 'fecha_hora'",
        esquema=O,
    )

    assert tipo == "timestamp with time zone"


# ---------------------------------------------------------------------------
# 3. Estado y rol vigentes, no los del momento de emision
# ---------------------------------------------------------------------------


def test_desactivar_la_cuenta_invalida_el_token_en_la_siguiente_peticion(
    cliente, base, password
):
    token = iniciar_sesion(cliente, EMAIL_PACIENTE, password)
    assert cliente.get(RUTA_YO, headers=bearer(token)).status_code == 200

    base.escribir(
        f"UPDATE {O}.usuario SET activo = false WHERE email = :email",
        email=EMAIL_PACIENTE,
    )

    assert cliente.get(RUTA_YO, headers=bearer(token)).status_code == 401


def test_cambiar_el_rol_cambia_lo_que_el_token_puede_hacer(cliente, base, password):
    """El token no cambia; el permiso si, porque el rol se lee en cada peticion.

    Desde SCRUM-97 una cuenta ADMIN no puede conservar un vinculo de paciente: el
    trigger diferido ``rol_vinculo_coherente`` lo rechaza al confirmar. Por eso el
    cambio de rol retira el vinculo en la **misma** transaccion, dejando un
    estado valido. Lo que la prueba demuestra no cambia.
    """
    token = iniciar_sesion(cliente, EMAIL_PACIENTE, password)
    assert cliente.get(RUTA_YO, headers=bearer(token)).json()["rol"] == "PACIENTE"

    id_admin = base.escalar(f"SELECT id_rol FROM {O}.rol WHERE nombre_rol = 'ADMIN'")
    with base.engine.begin() as conexion:
        conexion.execute(
            text(
                f"DELETE FROM {O}.usuario_paciente WHERE id_usuario ="
                f" (SELECT id_usuario FROM {O}.usuario WHERE email = :email)"
            ),
            {"email": EMAIL_PACIENTE},
        )
        conexion.execute(
            text(f"UPDATE {O}.usuario SET id_rol = :rol WHERE email = :email"),
            {"rol": id_admin, "email": EMAIL_PACIENTE},
        )

    assert cliente.get(RUTA_YO, headers=bearer(token)).json()["rol"] == "ADMIN"


def test_borrar_una_cuenta_sin_historial_invalida_su_token(cliente, base, password):
    """Desde SCRUM-97 el vinculo y la cuenta se retiran en una sola transaccion.

    Borrar solo el vinculo y confirmar dejaria una cuenta MEDICO sin su medico,
    que el trigger diferido ``rol_vinculo_coherente`` rechaza.
    """
    token = iniciar_sesion(cliente, EMAIL_MEDICO, password)
    id_usuario = cliente.get(RUTA_YO, headers=bearer(token)).json()["id_usuario"]

    base.escribir(f"DELETE FROM {O}.auditoria_log WHERE id_usuario = :id", id=id_usuario)
    with base.engine.begin() as conexion:
        conexion.execute(
            text(f"DELETE FROM {O}.usuario_medico WHERE id_usuario = :id"), {"id": id_usuario}
        )
        conexion.execute(text(f"DELETE FROM {O}.usuario WHERE id_usuario = :id"), {"id": id_usuario})

    assert cliente.get(RUTA_YO, headers=bearer(token)).status_code == 401


def test_una_cuenta_con_historial_no_se_puede_borrar(cliente, base, password):
    """``ON DELETE RESTRICT``: la auditoria sobrevive a la cuenta, por diseno."""
    token = iniciar_sesion(cliente, EMAIL_PACIENTE, password)
    id_usuario = cliente.get(RUTA_YO, headers=bearer(token)).json()["id_usuario"]

    with pytest.raises(IntegrityError):
        base.escribir(f"DELETE FROM {O}.usuario WHERE id_usuario = :id", id=id_usuario)


# ---------------------------------------------------------------------------
# 4. RBAC sobre la operacion real
# ---------------------------------------------------------------------------


def test_paciente_puede_registrar_una_sesion(cliente, base, password):
    """El unico perfil que puede, de extremo a extremo y sobre datos reales.

    El paquete es el de SCRUM-69, importado y no copiado: sus fechas y sus
    referencias encajan con las ventanas de asignacion del dataset canonico, asi
    que un 422 aqui hablaria del negocio y no de la autorizacion, que es lo que
    esta prueba mira.
    """
    token = iniciar_sesion(cliente, EMAIL_PACIENTE, password)

    respuesta = cliente.post(
        RUTA_INGESTA,
        json=PAQUETE_INCREMENTO,
        headers={**bearer(token), "Idempotency-Key": "scrum70-pg-0001"},
    )

    assert respuesta.status_code == 201, respuesta.text
    assert base.escalar(f"SELECT count(*) FROM {O}.idempotencia_solicitud") == 1


def test_la_creacion_deja_su_auditoria_confirmada(cliente, base, password):
    """Y apunta a la sesion que PostgreSQL acaba de crear."""
    token = iniciar_sesion(cliente, EMAIL_PACIENTE, password)

    respuesta = cliente.post(
        RUTA_INGESTA,
        json=PAQUETE_INCREMENTO,
        headers={**bearer(token), "Idempotency-Key": "scrum70-pg-0009"},
    )
    id_sesion = respuesta.json()["id_sesion"]

    creacion = auditoria_de(base)[-1]
    assert creacion.accion == AccionAuditada.SESION_MONITOREO_REGISTRADA.value
    assert creacion.nombre_entidad_afectada == "sesion_monitoreo"
    assert creacion.id_entidad_afectada == str(id_sesion)


def test_un_reenvio_no_duplica_ni_vuelve_a_auditar(cliente, base, password):
    """Idempotencia intacta: una sesion, una auditoria, dos respuestas 201."""
    token = iniciar_sesion(cliente, EMAIL_PACIENTE, password)
    cabeceras = {**bearer(token), "Idempotency-Key": "scrum70-pg-0010"}

    primera = cliente.post(RUTA_INGESTA, json=PAQUETE_INCREMENTO, headers=cabeceras)
    segunda = cliente.post(RUTA_INGESTA, json=PAQUETE_INCREMENTO, headers=cabeceras)

    assert primera.status_code == segunda.status_code == 201
    assert primera.headers["Idempotency-Replayed"] == "false"
    assert segunda.headers["Idempotency-Replayed"] == "true"
    assert primera.json() == segunda.json()

    creaciones = [
        fila
        for fila in auditoria_de(base)
        if fila.accion == AccionAuditada.SESION_MONITOREO_REGISTRADA.value
    ]
    assert len(creaciones) == 1
    assert base.escalar(f"SELECT count(*) FROM {O}.idempotencia_solicitud") == 1


def test_una_colision_de_clave_no_escribe_nada(cliente, base, password):
    """409 con contenido distinto: ni sesion, ni auditoria de exito."""
    token = iniciar_sesion(cliente, EMAIL_PACIENTE, password)
    cabeceras = {**bearer(token), "Idempotency-Key": "scrum70-pg-0011"}
    cliente.post(RUTA_INGESTA, json=PAQUETE_INCREMENTO, headers=cabeceras)

    distinto = json.loads(json.dumps(PAQUETE_INCREMENTO))
    distinto["lecturas"] = distinto["lecturas"][:1]
    respuesta = cliente.post(RUTA_INGESTA, json=distinto, headers=cabeceras)

    assert respuesta.status_code == 409
    creaciones = [
        fila
        for fila in auditoria_de(base)
        if fila.accion == AccionAuditada.SESION_MONITOREO_REGISTRADA.value
    ]
    assert len(creaciones) == 1


@pytest.mark.parametrize("email", [EMAIL_ADMIN, EMAIL_MEDICO])
def test_admin_y_medico_reciben_403_en_la_ingesta(cliente, base, password, email):
    token = iniciar_sesion(cliente, email, password)

    respuesta = cliente.post(
        RUTA_INGESTA,
        json=paquete(),
        headers={**bearer(token), "Idempotency-Key": "scrum70-pg-0002"},
    )

    assert respuesta.status_code == 403


def test_la_denegacion_por_rol_se_registra(cliente, base, password):
    token = iniciar_sesion(cliente, EMAIL_ADMIN, password)
    cliente.post(
        RUTA_INGESTA,
        json=paquete(),
        headers={**bearer(token), "Idempotency-Key": "scrum70-pg-0003"},
    )

    acciones = [fila.accion for fila in auditoria_de(base)]
    assert acciones == [
        AccionAuditada.LOGIN_EXITOSO.value,
        AccionAuditada.ACCESO_DENEGADO_ROL.value,
    ]

    denegacion = auditoria_de(base)[-1]
    assert denegacion.nombre_entidad_afectada == "sesion_monitoreo"
    assert denegacion.id_entidad_afectada is None


def test_un_rol_denegado_no_escribe_ninguna_sesion(cliente, base, password):
    antes = base.escalar(f"SELECT count(*) FROM {O}.sesion_monitoreo")
    token = iniciar_sesion(cliente, EMAIL_MEDICO, password)

    cliente.post(
        RUTA_INGESTA,
        json=paquete(),
        headers={**bearer(token), "Idempotency-Key": "scrum70-pg-0004"},
    )

    assert base.escalar(f"SELECT count(*) FROM {O}.sesion_monitoreo") == antes
    assert base.escalar(f"SELECT count(*) FROM {O}.idempotencia_solicitud") == 0


def test_sin_token_la_ingesta_responde_401(cliente):
    respuesta = cliente.post(
        RUTA_INGESTA, json=paquete(), headers={"Idempotency-Key": "scrum70-pg-0005"}
    )

    assert respuesta.status_code == 401
    assert respuesta.headers["www-authenticate"].startswith("Bearer")


# ---------------------------------------------------------------------------
# 5. Atomicidad: un rollback de negocio no deja auditoria de exito
# ---------------------------------------------------------------------------


def test_una_referencia_inexistente_revierte_todo(cliente, base, password):
    """404 de negocio: ni sesion, ni reclamacion, ni auditoria de exito."""
    token = iniciar_sesion(cliente, EMAIL_PACIENTE, password)
    ajeno = paquete()
    ajeno["id_embarazo"] = 2_000_000_000

    respuesta = cliente.post(
        RUTA_INGESTA,
        json=ajeno,
        headers={**bearer(token), "Idempotency-Key": "scrum70-pg-0006"},
    )

    assert respuesta.status_code == 404
    acciones = [fila.accion for fila in auditoria_de(base)]
    assert AccionAuditada.SESION_MONITOREO_REGISTRADA.value not in acciones
    assert base.escalar(f"SELECT count(*) FROM {O}.idempotencia_solicitud") == 0


def test_el_catalogo_escrito_es_el_aprobado(cliente, base, password):
    """Ninguna accion fuera de las cuatro llega a la tabla."""
    iniciar_sesion(cliente, EMAIL_PACIENTE, password)
    cliente.post(RUTA_TOKEN, json={"email": "nadie@example.com", "password": "x"})
    token_admin = iniciar_sesion(cliente, EMAIL_ADMIN, password)
    cliente.post(
        RUTA_INGESTA,
        json=paquete(),
        headers={**bearer(token_admin), "Idempotency-Key": "scrum70-pg-0007"},
    )
    cliente.get(RUTA_YO, headers=bearer("token.invalido.aqui"))

    escritas = {fila.accion for fila in auditoria_de(base)}
    assert escritas <= {accion.value for accion in AccionAuditada}


def test_un_token_rechazado_no_escribe_ninguna_fila(cliente, base):
    """Decision del ticket: seria una escritura sin autenticar en la tabla."""
    for _ in range(5):
        cliente.get(RUTA_YO, headers=bearer("no.es.un.token"))

    assert base.escalar(f"SELECT count(*) FROM {O}.auditoria_log") == 0


# ---------------------------------------------------------------------------
# 6. Saneamiento sobre las filas realmente escritas
# ---------------------------------------------------------------------------

PROHIBIDOS = (
    "$argon2id$",
    "Bearer",
    "eyJ",
    SECRETO,
    "@example.com",
    "postgresql",
    "SELECT",
    "hr_valor",
)


def test_ninguna_fila_escrita_lleva_datos_prohibidos(cliente, base, password):
    token = iniciar_sesion(cliente, EMAIL_ADMIN, password)
    cliente.post(
        RUTA_INGESTA,
        json=paquete(),
        headers={**bearer(token), "Idempotency-Key": "scrum70-pg-0008"},
    )
    cliente.post(RUTA_TOKEN, json={"email": "nadie@example.com", "password": password})

    texto = " ".join(
        " ".join(str(valor) for valor in fila) for fila in auditoria_de(base)
    )

    for prohibido in PROHIBIDOS:
        assert prohibido not in texto
    assert password not in texto


def test_las_columnas_de_la_tabla_son_las_esperadas(base):
    columnas = {
        fila[0]
        for fila in base.filas(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = :esquema AND table_name = 'auditoria_log'",
            esquema=O,
        )
    }

    assert columnas == {
        "id_log",
        "id_usuario",
        "accion",
        "nombre_entidad_afectada",
        "id_entidad_afectada",
        "ip_origen",
        "fecha_hora",
    }


# ---------------------------------------------------------------------------
# 7. Alembic: SCRUM-70 no necesito DDL
# ---------------------------------------------------------------------------


def test_scrum70_no_anadio_ninguna_revision(base):
    """El esquema de SCRUM-51/52 ya preveia esto: SCRUM-70 no creo revision.

    Desde SCRUM-97 el head es la revision de cuentas, y se apoya directamente en
    la analitica de SCRUM-69: entre ambas no hay nada de SCRUM-70.
    """
    script = ScriptDirectory.from_config(construir_config_alembic())
    [head] = script.get_heads()

    assert base.escalar("SELECT version_num FROM alembic_version") == head
    assert head == "54053d46abd6"
    assert script.get_revision(head).down_revision == "60facdbacf51"


def test_el_hash_argon2id_cabe_en_la_columna_desplegada(base):
    ancho = base.escalar(
        "SELECT character_maximum_length FROM information_schema.columns"
        " WHERE table_schema = :esquema AND table_name = 'usuario'"
        " AND column_name = 'password_hash'",
        esquema=O,
    )
    real = base.escalar(f"SELECT max(length(password_hash)) FROM {O}.usuario")

    assert real <= ancho
    assert ancho == 255


def test_la_suite_no_deja_bases_residuales(servidor):
    """Solo quedan las que esta sesion sigue usando; ninguna huerfana."""
    residuales = [
        fila[0]
        for fila in servidor.engine.connect()
        .execute(
            text("SELECT datname FROM pg_database WHERE datname LIKE :patron"),
            {"patron": f"{PREFIJO_DE_BASE}%"},
        )
        .fetchall()
    ]

    ajenas = [
        nombre
        for nombre in residuales
        if not nombre.startswith(f"{PREFIJO_DE_BASE}{servidor.ejecucion}_")
    ]
    assert ajenas == [], f"Bases de ejecuciones anteriores sin limpiar: {ajenas}"
