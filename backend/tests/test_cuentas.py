"""Aprovisionamiento y ciclo de vida de cuentas, sin servidor PostgreSQL (SCRUM-97).

Lo que se prueba aqui es **nuestro** contrato: la forma canonica del correo, los
cuerpos que se aceptan y los que no, el orden RBAC -> existencia, que codigo
recibe cada desenlace, quien confirma y quien revierte, que se audita y que no
sale nunca en una respuesta. El servicio se sustituye por dobles donde lo que se
mira es el router; lo que depende de filas reales -- restricciones, triggers,
candados y carreras -- vive en ``test_cuentas_postgresql.py``.

Todas las cuentas y perfiles son ficticios y completamente simulados.
"""

from __future__ import annotations

import io
import logging
from datetime import timedelta
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from app.api.dependencias import (
    DESAFIO_SIN_CREDENCIAL,
    MENSAJE_ERROR_INTERNO,
    MENSAJE_ROL_NO_AUTORIZADO,
    get_db_auditoria,
    obtener_configuracion_jwt,
)
from app.api.v1 import cuentas as router_cuentas
from app.api.v1.autenticacion import RUTAS_CON_CREDENCIALES
from app.config import ConfiguracionJWT
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.enums import NombreRol
from app.models.seguridad import AuditoriaLog
from app.schemas.autenticacion import CredencialesEntrada
from app.schemas.cuentas import (
    CuentaMedicoProvisionada,
    CuentaPacienteProvisionada,
    EstadoCuentaEntrada,
    EstadoCuentaSalida,
    ProvisionEntrada,
)
from app.services import cuentas as servicio_cuentas
from app.services import principal as modulo_principal
from app.services.auditoria import AccionAuditada, ENTIDAD_USUARIO
from app.services.correo import (
    LONGITUD_MAXIMA_EMAIL,
    EmailNoCanonizable,
    canonizar_email,
)
from app.services.cuentas import (
    PERFIL_MEDICO,
    PERFIL_PACIENTE,
    RESTRICCION_ROL_VINCULO,
    ConflictoDeCuenta,
    CuentaAdministrativa,
    CuentaInexistente,
    CuentaProvisionada,
    EmailEnUso,
    EstadoDeCuenta,
    PerfilInexistente,
    PerfilYaVinculado,
    clasificar_error_de_integridad,
)
from app.services.passwords import PREFIJO_ARGON2ID
from tests.conftest import construir_config_alembic, identidad_simulada, principal_de_prueba
from tests.test_autenticacion_api import SesionDeCuentas, bearer

RUTA_PACIENTE = "/api/v1/cuentas/pacientes/{}"
RUTA_MEDICO = "/api/v1/cuentas/medicos/{}"
RUTA_ESTADO = "/api/v1/cuentas/{}/estado"

EMAIL_PACIENTE = "paciente31@example.com"
EMAIL_MEDICO = "medico06@example.com"
PASSWORD = "Clave-Simulada-SCRUM97-7731"
ID_PACIENTE = 130
ID_MEDICO = 105
ID_USUARIO_NUEVO = 137
ID_ADMIN = 100

SECRETO = "secreto-ficticio-de-las-pruebas-de-cuentas-con-longitud"
CONFIGURACION = ConfiguracionJWT(secreto=SECRETO, expiracion=timedelta(minutes=30))

REVISION_SCRUM_97 = "54053d46abd6"
REVISION_ANALITICA = "60facdbacf51"


# ---------------------------------------------------------------------------
# 1. Correo canonico: una sola regla
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("paciente31@example.com", "paciente31@example.com"),
        ("Paciente31@Example.COM", "paciente31@example.com"),
        ("  paciente31@example.com  ", "paciente31@example.com"),
        ("\t MEDICO06@EXAMPLE.COM \n", "medico06@example.com"),
    ],
    ids=["canonico", "mayusculas", "espacios-exteriores", "tabs-y-mayusculas"],
)
def test_canonizar_aplica_strip_y_lower(entrada, esperado):
    assert canonizar_email(entrada) == esperado


def test_canonizar_es_idempotente():
    una_vez = canonizar_email("  Paciente31@Example.com ")

    assert canonizar_email(una_vez) == una_vez


@pytest.mark.parametrize("entrada", ["", " ", "\t\n", " "], ids=["vacio", "espacio", "tab", "nbsp"])
def test_vacio_tras_normalizar_se_rechaza(entrada):
    with pytest.raises(EmailNoCanonizable):
        canonizar_email(entrada)


@pytest.mark.parametrize(
    "entrada",
    ["paciente 31@example.com", "paciente31@exa\tmple.com", "paciente31@ example.com"],
    ids=["espacio", "tab", "nbsp"],
)
def test_whitespace_interno_se_rechaza(entrada):
    with pytest.raises(EmailNoCanonizable):
        canonizar_email(entrada)


def test_strip_ocurre_antes_del_limite_de_longitud():
    """120 caracteres utiles con relleno exterior caben; 121 no."""
    justo = "a" * (LONGITUD_MAXIMA_EMAIL - len("@example.com")) + "@example.com"
    assert len(justo) == LONGITUD_MAXIMA_EMAIL

    assert canonizar_email(f"   {justo}   ") == justo
    with pytest.raises(EmailNoCanonizable):
        canonizar_email("a" + justo)


def test_el_limite_sale_de_la_columna():
    assert LONGITUD_MAXIMA_EMAIL == 120


def test_el_mensaje_de_error_no_repite_el_valor():
    sonda = "Sonda Interna@example.com"
    with pytest.raises(EmailNoCanonizable) as error:
        canonizar_email(sonda)

    assert sonda not in str(error.value)
    assert sonda.lower() not in str(error.value)


def test_el_login_usa_la_misma_forma_canonica():
    credenciales = CredencialesEntrada(email="  Paciente01@Example.COM ", password="x")

    assert credenciales.email == "paciente01@example.com"


def test_la_provision_usa_la_misma_forma_canonica():
    entrada = ProvisionEntrada(email=" Paciente31@EXAMPLE.com", password=PASSWORD)

    assert entrada.email == EMAIL_PACIENTE


def test_login_y_provision_comparten_el_mismo_tipo():
    """No dos reglas que puedan divergir: es literalmente la misma anotacion."""
    assert (
        CredencialesEntrada.model_fields["email"].metadata
        == ProvisionEntrada.model_fields["email"].metadata
    )


def test_autenticar_busca_la_cuenta_en_forma_canonica():
    """El servicio interno tambien canoniza, aunque lo llame otro codigo."""
    sesion = SesionDeCuentas()
    capturadas = []
    original = sesion.execute

    def espia(sentencia, *args, **kwargs):
        capturadas.append(sentencia.compile(dialect=postgresql.dialect()).params)
        return original(sentencia, *args, **kwargs)

    sesion.execute = espia

    modulo_principal.autenticar(sesion, "  Paciente01@Example.COM ", "x")

    assert list(capturadas[0].values()) == ["paciente01@example.com"]


def test_autenticar_con_un_correo_sin_forma_canonica_no_consulta_y_verifica(monkeypatch):
    """Sin retorno temprano: paga la verificacion Argon2id del usuario inexistente."""
    llamadas = []
    original = modulo_principal.verificar
    monkeypatch.setattr(
        modulo_principal, "verificar", lambda d, c: (llamadas.append(d), original(d, c))[1]
    )
    sesion = SesionDeCuentas()

    assert modulo_principal.autenticar(sesion, "con espacio@example.com", "x") is None
    assert llamadas == [None]
    assert sesion.consultas == 0


# ---------------------------------------------------------------------------
# 2. Contratos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {"rol": "ADMIN"},
        {"rol": "PACIENTE"},
        {"activo": False},
        {"id_usuario": 1},
        {"password_hash": "$argon2id$v=19$falso"},
        {"primer_nombre": "Gestante"},
        {"cedula": "SIM-PAC-999"},
    ],
    ids=["rol-admin", "rol-paciente", "activo", "id_usuario", "password_hash", "nombre", "cedula"],
)
def test_la_provision_rechaza_cualquier_campo_extra(extra):
    with pytest.raises(ValidationError):
        ProvisionEntrada(email=EMAIL_PACIENTE, password=PASSWORD, **extra)


def test_la_provision_no_declara_un_campo_de_rol():
    assert set(ProvisionEntrada.model_fields) == {"email", "password"}


def test_la_password_es_secretstr_y_no_aparece_en_el_repr():
    entrada = ProvisionEntrada(email=EMAIL_PACIENTE, password=PASSWORD)

    assert PASSWORD not in repr(entrada)
    assert PASSWORD not in str(entrada.model_dump())
    assert entrada.password.get_secret_value() == PASSWORD


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"email": EMAIL_PACIENTE},
        {"password": PASSWORD},
        {"email": EMAIL_PACIENTE, "password": ""},
        {"email": EMAIL_PACIENTE, "password": "x" * 513},
        {"email": "", "password": PASSWORD},
        {"email": "a b@example.com", "password": PASSWORD},
        {"email": 31, "password": PASSWORD},
    ],
    ids=["sin-password", "sin-email", "password-vacia", "password-larga", "email-vacio", "email-con-espacio", "email-no-texto"],
)
def test_cuerpos_de_provision_invalidos(cuerpo):
    with pytest.raises(ValidationError):
        ProvisionEntrada(**cuerpo)


@pytest.mark.parametrize("valor", ["false", "true", 0, 1, None, "no"])
def test_el_estado_solo_acepta_booleanos_json(valor):
    with pytest.raises(ValidationError):
        EstadoCuentaEntrada.model_validate({"activo": valor})


def test_el_estado_rechaza_campos_extra_y_ausencia():
    with pytest.raises(ValidationError):
        EstadoCuentaEntrada.model_validate({"activo": False, "rol": "ADMIN"})
    with pytest.raises(ValidationError):
        EstadoCuentaEntrada.model_validate({})


@pytest.mark.parametrize(
    ("modelo", "campos"),
    [
        (CuentaPacienteProvisionada, {"id_usuario", "id_paciente", "rol", "activo"}),
        (CuentaMedicoProvisionada, {"id_usuario", "id_medico", "rol", "activo"}),
        (EstadoCuentaSalida, {"id_usuario", "rol", "activo"}),
    ],
)
def test_las_respuestas_no_tienen_campos_sensibles(modelo, campos):
    assert set(modelo.model_fields) == campos


# ---------------------------------------------------------------------------
# 3. El rol objetivo sale del tipo de perfil
# ---------------------------------------------------------------------------


def test_el_rol_de_cada_tipo_de_perfil_es_fijo():
    assert PERFIL_PACIENTE.rol is NombreRol.PACIENTE
    assert PERFIL_MEDICO.rol is NombreRol.MEDICO


def test_ningun_tipo_de_perfil_produce_admin():
    tipos = [v for v in vars(servicio_cuentas).values() if isinstance(v, servicio_cuentas.TipoDePerfil)]

    assert len(tipos) == 2
    assert NombreRol.ADMIN not in {t.rol for t in tipos}


def test_cada_tipo_crea_su_propio_vinculo():
    paciente = PERFIL_PACIENTE.crear_vinculo(ID_USUARIO_NUEVO, ID_PACIENTE)
    medico = PERFIL_MEDICO.crear_vinculo(ID_USUARIO_NUEVO, ID_MEDICO)

    assert type(paciente).__name__ == "UsuarioPaciente"
    assert (paciente.id_usuario, paciente.id_paciente) == (ID_USUARIO_NUEVO, ID_PACIENTE)
    assert type(medico).__name__ == "UsuarioMedico"
    assert (medico.id_usuario, medico.id_medico) == (ID_USUARIO_NUEVO, ID_MEDICO)


# ---------------------------------------------------------------------------
# 4. Catalogo de auditoria
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "accion",
    [
        "CUENTA_PACIENTE_PROVISIONADA",
        "CUENTA_MEDICO_PROVISIONADA",
        "CUENTA_DESACTIVADA",
        "CUENTA_REACTIVADA",
    ],
)
def test_los_eventos_del_ciclo_existen_y_caben(accion):
    ancho = AuditoriaLog.__table__.c.accion.type.length

    assert AccionAuditada(accion).value == accion
    assert len(accion) <= ancho


def test_no_hay_eventos_de_no_op_ni_de_rechazo_de_provision():
    valores = {a.value for a in AccionAuditada}

    assert not any("RECHAZ" in v or "DUPLIC" in v or "SIN_CAMBIO" in v for v in valores)


# ---------------------------------------------------------------------------
# 5. Router: dobles del servicio
# ---------------------------------------------------------------------------


@pytest.fixture
def sesion() -> SesionDeCuentas:
    return SesionDeCuentas()


@pytest.fixture
def auditoria_propia() -> SesionDeCuentas:
    return SesionDeCuentas()


@pytest.fixture
def base_http(sesion, auditoria_propia):
    app.dependency_overrides[get_db] = lambda: sesion
    app.dependency_overrides[get_db_auditoria] = lambda: auditoria_propia
    app.dependency_overrides[obtener_configuracion_jwt] = lambda: CONFIGURACION
    try:
        yield TestClient(app, raise_server_exceptions=True)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def llamadas(monkeypatch):
    """Sustituye el servicio y anota cada llamada; por defecto, exito."""
    registro: dict[str, list] = {"provisionar": [], "cambiar_estado": []}
    comportamiento: dict[str, object] = {}

    def provisionar(sesion_bd, tipo, id_perfil, *, email, password):
        registro["provisionar"].append((tipo, id_perfil, email, password))
        if "provisionar" in comportamiento:
            raise comportamiento["provisionar"]
        return CuentaProvisionada(
            id_usuario=ID_USUARIO_NUEVO, id_perfil=id_perfil, rol=tipo.rol, activo=True
        )

    def cambiar_estado(sesion_bd, id_usuario, activo):
        registro["cambiar_estado"].append((id_usuario, activo))
        if "cambiar_estado" in comportamiento:
            valor = comportamiento["cambiar_estado"]
            if isinstance(valor, BaseException):
                raise valor
            return valor
        return EstadoDeCuenta(
            id_usuario=id_usuario, rol=NombreRol.PACIENTE, activo=activo, hubo_transicion=True
        )

    monkeypatch.setattr(servicio_cuentas, "provisionar", provisionar)
    monkeypatch.setattr(servicio_cuentas, "cambiar_estado", cambiar_estado)
    return SimpleNamespace(registro=registro, comportamiento=comportamiento)


@pytest.fixture
def como_admin(base_http):
    with identidad_simulada(app, principal_de_prueba(NombreRol.ADMIN, ID_ADMIN)):
        yield base_http


def auditadas(sesion: SesionDeCuentas) -> list[AuditoriaLog]:
    return [e for e in sesion.agregados if isinstance(e, AuditoriaLog)]


def cuerpo(email: str = EMAIL_PACIENTE) -> dict:
    return {"email": email, "password": PASSWORD}


def test_admin_provisiona_paciente(como_admin, sesion, llamadas):
    respuesta = como_admin.post(RUTA_PACIENTE.format(ID_PACIENTE), json=cuerpo())

    assert respuesta.status_code == 201
    assert respuesta.json() == {
        "id_usuario": ID_USUARIO_NUEVO,
        "id_paciente": ID_PACIENTE,
        "rol": "PACIENTE",
        "activo": True,
    }
    [(tipo, id_perfil, email, password)] = llamadas.registro["provisionar"]
    assert tipo is PERFIL_PACIENTE
    assert (id_perfil, email, password) == (ID_PACIENTE, EMAIL_PACIENTE, PASSWORD)
    assert (sesion.commits, sesion.rollbacks) == (1, 0)


def test_admin_provisiona_medico(como_admin, sesion, llamadas):
    respuesta = como_admin.post(RUTA_MEDICO.format(ID_MEDICO), json=cuerpo(EMAIL_MEDICO))

    assert respuesta.status_code == 201
    assert respuesta.json() == {
        "id_usuario": ID_USUARIO_NUEVO,
        "id_medico": ID_MEDICO,
        "rol": "MEDICO",
        "activo": True,
    }
    assert llamadas.registro["provisionar"][0][0] is PERFIL_MEDICO
    assert sesion.commits == 1


@pytest.mark.parametrize(
    ("ruta", "accion"),
    [
        (RUTA_PACIENTE.format(ID_PACIENTE), AccionAuditada.CUENTA_PACIENTE_PROVISIONADA),
        (RUTA_MEDICO.format(ID_MEDICO), AccionAuditada.CUENTA_MEDICO_PROVISIONADA),
    ],
)
def test_la_provision_se_audita_en_la_transaccion_de_negocio(
    como_admin, sesion, auditoria_propia, llamadas, ruta, accion
):
    como_admin.post(ruta, json=cuerpo())

    [entrada] = auditadas(sesion)
    assert entrada.accion == accion.value
    assert entrada.id_usuario == ID_ADMIN
    assert entrada.nombre_entidad_afectada == ENTIDAD_USUARIO
    assert entrada.id_entidad_afectada == str(ID_USUARIO_NUEVO)
    assert auditoria_propia.agregados == []


def test_el_rol_de_la_ruta_manda_aunque_el_cuerpo_pida_admin(como_admin, sesion, llamadas):
    respuesta = como_admin.post(
        RUTA_PACIENTE.format(ID_PACIENTE), json={**cuerpo(), "rol": "ADMIN"}
    )

    assert respuesta.status_code == 422
    assert llamadas.registro["provisionar"] == []
    assert sesion.commits == 0


def test_la_respuesta_no_lleva_correo_password_ni_hash(como_admin, llamadas):
    texto = como_admin.post(RUTA_PACIENTE.format(ID_PACIENTE), json=cuerpo()).text

    for prohibido in (EMAIL_PACIENTE, PASSWORD, PREFIJO_ARGON2ID, "password", "email", "token"):
        assert prohibido not in texto


@pytest.mark.parametrize(
    ("error", "estado"),
    [
        (PerfilInexistente(servicio_cuentas.MENSAJE_PACIENTE_INEXISTENTE), 404),
        (PerfilYaVinculado(), 409),
        (EmailEnUso(), 409),
        (ConflictoDeCuenta(servicio_cuentas.MENSAJE_CONFLICTO_CONCURRENTE), 409),
    ],
    ids=["perfil-inexistente", "perfil-vinculado", "email-en-uso", "conflicto"],
)
def test_los_rechazos_revierten_y_no_auditan(como_admin, sesion, llamadas, error, estado):
    llamadas.comportamiento["provisionar"] = error

    respuesta = como_admin.post(RUTA_PACIENTE.format(ID_PACIENTE), json=cuerpo())

    assert respuesta.status_code == estado
    assert respuesta.json() == {"detail": error.detalle}
    assert (sesion.commits, sesion.rollbacks) == (0, 1)
    assert auditadas(sesion) == []


def test_un_fallo_de_auditoria_revierte_la_provision_y_es_500(como_admin, sesion, llamadas):
    def estallar():
        raise SQLAlchemyError("fallo simulado de la auditoria")

    sesion.flush = estallar

    respuesta = como_admin.post(RUTA_PACIENTE.format(ID_PACIENTE), json=cuerpo())

    assert respuesta.status_code == 500
    assert respuesta.json() == {"detail": MENSAJE_ERROR_INTERNO}
    assert (sesion.commits, sesion.rollbacks) == (0, 1)


def test_un_fallo_inesperado_es_500_generico(como_admin, sesion, llamadas):
    llamadas.comportamiento["provisionar"] = RuntimeError("detalle interno 7731")

    respuesta = como_admin.post(RUTA_PACIENTE.format(ID_PACIENTE), json=cuerpo())

    assert respuesta.status_code == 500
    assert "7731" not in respuesta.text
    assert sesion.rollbacks == 1


# --- errores de base traducidos --------------------------------------------


class _Diag:
    def __init__(self, restriccion):
        self.constraint_name = restriccion
        self.table_name = "usuario"
        self.column_name = None


class _Original(Exception):
    def __init__(self, sqlstate, restriccion):
        super().__init__(f"MENSAJE-DEL-DRIVER {EMAIL_PACIENTE} SELECT secreto")
        self.sqlstate = sqlstate
        self.diag = _Diag(restriccion)


def error_de_integridad(sqlstate: str, restriccion: str | None) -> IntegrityError:
    return IntegrityError(
        "INSERT INTO operacional.usuario (email) VALUES (%(email)s)",
        {"email": EMAIL_PACIENTE},
        _Original(sqlstate, restriccion),
    )


@pytest.mark.parametrize(
    ("sqlstate", "restriccion", "mensaje"),
    [
        ("23505", "uq_usuario_email", servicio_cuentas.MENSAJE_EMAIL_EN_USO),
        ("23505", "uq_usuario_paciente_id_paciente", servicio_cuentas.MENSAJE_PERFIL_YA_VINCULADO),
        ("23505", "uq_usuario_medico_id_medico", servicio_cuentas.MENSAJE_PERFIL_YA_VINCULADO),
        ("23505", "pk_usuario_paciente", servicio_cuentas.MENSAJE_VINCULO_INCOHERENTE),
        ("23505", "pk_usuario_medico", servicio_cuentas.MENSAJE_VINCULO_INCOHERENTE),
        ("23514", RESTRICCION_ROL_VINCULO, servicio_cuentas.MENSAJE_VINCULO_INCOHERENTE),
    ],
)
def test_clasificacion_de_restricciones_conocidas(sqlstate, restriccion, mensaje):
    conflicto = clasificar_error_de_integridad(error_de_integridad(sqlstate, restriccion))

    assert isinstance(conflicto, ConflictoDeCuenta)
    assert conflicto.detalle == mensaje


@pytest.mark.parametrize(
    ("sqlstate", "restriccion"),
    [
        ("23505", "uq_otra_tabla"),
        ("23514", "ck_usuario_email_canonico"),
        ("23503", "fk_usuario_id_rol_rol"),
        ("23502", None),
    ],
)
def test_lo_desconocido_no_se_convierte_en_409(sqlstate, restriccion):
    assert clasificar_error_de_integridad(error_de_integridad(sqlstate, restriccion)) is None


@pytest.mark.parametrize("sqlstate", ["40001", "40P01"])
def test_serializacion_y_deadlock_son_conflicto_concurrente(sqlstate):
    error = OperationalError("SELECT 1", {}, _Original(sqlstate, None))

    conflicto = clasificar_error_de_integridad(error)

    assert conflicto.detalle == servicio_cuentas.MENSAJE_CONFLICTO_CONCURRENTE


def test_un_commit_rechazado_por_el_trigger_diferido_es_409(como_admin, sesion, llamadas, caplog):
    def commit_rechazado():
        raise error_de_integridad("23514", RESTRICCION_ROL_VINCULO)

    sesion.commit = commit_rechazado

    with caplog.at_level(logging.WARNING):
        respuesta = como_admin.post(RUTA_PACIENTE.format(ID_PACIENTE), json=cuerpo())

    assert respuesta.status_code == 409
    assert sesion.rollbacks == 1
    assert "rol_vinculo_coherente" in caplog.text


def test_control_el_error_fabricado_si_contiene_los_marcadores():
    """Sin este control, «no aparece en el log» no probaria nada."""
    texto = str(error_de_integridad("23505", "uq_usuario_email"))

    assert "MENSAJE-DEL-DRIVER" in texto
    assert EMAIL_PACIENTE in texto
    assert "INSERT" in texto


def test_un_duplicado_de_base_no_filtra_al_log_ni_a_la_respuesta(como_admin, sesion, llamadas, caplog):
    llamadas.comportamiento["provisionar"] = error_de_integridad("23505", "uq_usuario_email")

    with caplog.at_level(logging.DEBUG):
        respuesta = como_admin.post(RUTA_PACIENTE.format(ID_PACIENTE), json=cuerpo())

    assert respuesta.status_code == 409
    for prohibido in (EMAIL_PACIENTE, "MENSAJE-DEL-DRIVER", "INSERT", "secreto", PASSWORD):
        assert prohibido not in respuesta.text
        assert prohibido not in caplog.text
    assert all(registro.exc_info is None for registro in caplog.records)


def test_un_rollback_que_tambien_falla_sigue_saneado(como_admin, sesion, llamadas, caplog):
    llamadas.comportamiento["provisionar"] = error_de_integridad("23505", "uq_usuario_email")

    def rollback_roto():
        raise OperationalError("ROLLBACK", {}, _Original("08006", None))

    sesion.rollback = rollback_roto

    respuesta = como_admin.post(RUTA_PACIENTE.format(ID_PACIENTE), json=cuerpo())

    assert respuesta.status_code == 409
    assert "MENSAJE-DEL-DRIVER" not in caplog.text


# --- RBAC antes que la existencia ------------------------------------------


@pytest.mark.parametrize("rol", [NombreRol.PACIENTE, NombreRol.MEDICO])
@pytest.mark.parametrize(
    "peticion",
    [
        ("post", RUTA_PACIENTE, cuerpo()),
        ("post", RUTA_MEDICO, cuerpo(EMAIL_MEDICO)),
        ("patch", RUTA_ESTADO, {"activo": False}),
    ],
    ids=["paciente", "medico", "estado"],
)
def test_un_no_admin_recibe_403_identico_exista_o_no_el_objetivo(
    base_http, sesion, auditoria_propia, llamadas, rol, peticion
):
    metodo, ruta, carga = peticion
    with identidad_simulada(app, principal_de_prueba(rol)):
        existente = getattr(base_http, metodo)(ruta.format(ID_PACIENTE), json=carga)
        inexistente = getattr(base_http, metodo)(ruta.format(2_000_000_000), json=carga)

    assert existente.status_code == inexistente.status_code == 403
    assert existente.text == inexistente.text
    assert existente.json()["detail"] == MENSAJE_ROL_NO_AUTORIZADO
    assert llamadas.registro == {"provisionar": [], "cambiar_estado": []}
    assert sesion.consultas == 0 and sesion.commits == 0
    denegaciones = auditadas(auditoria_propia)
    assert [d.accion for d in denegaciones] == [AccionAuditada.ACCESO_DENEGADO_ROL.value] * 2
    assert {d.nombre_entidad_afectada for d in denegaciones} == {ENTIDAD_USUARIO}


def test_un_no_admin_con_cuerpo_invalido_recibe_403_no_422(base_http, llamadas):
    with identidad_simulada(app, principal_de_prueba(NombreRol.PACIENTE)):
        respuesta = base_http.post(RUTA_PACIENTE.format("no-es-un-id"), json={"rol": "ADMIN"})

    assert respuesta.status_code == 403


@pytest.mark.parametrize(
    ("metodo", "ruta", "carga"),
    [
        ("post", RUTA_PACIENTE.format(ID_PACIENTE), cuerpo()),
        ("post", RUTA_MEDICO.format(ID_MEDICO), cuerpo(EMAIL_MEDICO)),
        ("patch", RUTA_ESTADO.format(ID_USUARIO_NUEVO), {"activo": False}),
    ],
)
def test_sin_credencial_es_401(base_http, llamadas, metodo, ruta, carga):
    respuesta = getattr(base_http, metodo)(ruta, json=carga)

    assert respuesta.status_code == 401
    assert respuesta.headers["www-authenticate"] == DESAFIO_SIN_CREDENCIAL
    assert llamadas.registro == {"provisionar": [], "cambiar_estado": []}


def test_un_token_invalido_es_401(base_http, llamadas):
    respuesta = base_http.post(
        RUTA_PACIENTE.format(ID_PACIENTE), json=cuerpo(), headers=bearer("no.es.valido")
    )

    assert respuesta.status_code == 401
    assert llamadas.registro["provisionar"] == []


# --- 422 saneado ------------------------------------------------------------


def test_las_rutas_de_provision_estan_entre_las_rutas_con_credenciales():
    assert "/api/v1/cuentas/pacientes/{id_paciente}" in RUTAS_CON_CREDENCIALES
    assert "/api/v1/cuentas/medicos/{id_medico}" in RUTAS_CON_CREDENCIALES
    assert "/api/v1/autenticacion/token" in RUTAS_CON_CREDENCIALES


@pytest.mark.parametrize("ruta", [RUTA_PACIENTE.format(ID_PACIENTE), RUTA_MEDICO.format(ID_MEDICO)])
@pytest.mark.parametrize(
    "carga",
    [
        {"password": PASSWORD},
        {"email": "con espacio@example.com", "password": PASSWORD},
        {"email": EMAIL_PACIENTE, "password": PASSWORD, "rol": "ADMIN"},
    ],
    ids=["sin-email", "email-invalido", "campo-extra"],
)
def test_el_422_de_la_provision_no_devuelve_la_password(como_admin, llamadas, ruta, carga):
    respuesta = como_admin.post(ruta, json=carga)

    assert respuesta.status_code == 422
    assert PASSWORD not in respuesta.text
    assert '"input"' not in respuesta.text


MARCADOR_PASSWORD_LOGIN = "Marcador-Password-Login-SCRUM97-7731"
MARCADOR_EMAIL_LOGIN = "marcador.correo"
PREFIJOS_DE_RUTA = ["", "/prefijo", "/api-detras-de-un-proxy"]

EMAILS_SIN_FORMA_CANONICA = {
    "solo-espacios": "      ",
    "espacio-interno": f"{MARCADOR_EMAIL_LOGIN} 7731@example.com",
    "tab-interno": f"{MARCADOR_EMAIL_LOGIN}\t7731@example.com",
}

FILTRACIONES_INTERNAS = (
    "SELECT",
    "operacional",
    "sqlalchemy",
    "psycopg",
    "Traceback",
    "email_1",
    "[parameters",
)


def test_control_pydantic_si_hace_eco_del_correo_rechazado():
    """Sin el saneamiento, el 422 devolveria el valor: es lo que se esta vigilando."""
    with pytest.raises(ValidationError) as error:
        CredencialesEntrada.model_validate(
            {"email": EMAILS_SIN_FORMA_CANONICA["espacio-interno"], "password": MARCADOR_PASSWORD_LOGIN}
        )

    [detalle] = error.value.errors()
    assert detalle["input"] == EMAILS_SIN_FORMA_CANONICA["espacio-interno"]


@pytest.mark.parametrize("caso", list(EMAILS_SIN_FORMA_CANONICA))
def test_el_422_canonico_del_login_no_filtra_nada_con_y_sin_root_path(caso):
    """Correo sin forma canonica en el login: 422 por su forma, sin eco de valores.

    La decision de sanear depende de la ruta resuelta, asi que se ejerce con los
    mismos prefijos que la prueba heredada de SCRUM-70, y la respuesta debe ser
    identica con y sin ``root_path``. La base no se consulta: el cuerpo se
    rechaza antes de buscar ninguna cuenta.
    """
    cuerpo = {"email": EMAILS_SIN_FORMA_CANONICA[caso], "password": MARCADOR_PASSWORD_LOGIN}
    detalles = []

    for prefijo in PREFIJOS_DE_RUTA:
        sesion, auditoria = SesionDeCuentas(), SesionDeCuentas()
        app.dependency_overrides[get_db] = lambda: sesion
        app.dependency_overrides[get_db_auditoria] = lambda: auditoria
        app.dependency_overrides[obtener_configuracion_jwt] = lambda: CONFIGURACION
        try:
            respuesta = TestClient(app, root_path=prefijo).post(
                f"{prefijo}/api/v1/autenticacion/token", json=cuerpo
            )
        finally:
            app.dependency_overrides.clear()

        assert respuesta.status_code == 422, prefijo
        errores = respuesta.json()["detail"]
        assert [e["loc"] for e in errores] == [["body", "email"]]
        assert all("input" not in e for e in errores)
        assert MARCADOR_PASSWORD_LOGIN not in respuesta.text
        assert MARCADOR_EMAIL_LOGIN not in respuesta.text
        assert "7731@example.com" not in respuesta.text
        for interno in FILTRACIONES_INTERNAS:
            assert interno not in respuesta.text
        assert sesion.consultas == 0 and auditoria.agregados == []
        detalles.append(errores)

    assert all(detalle == detalles[0] for detalle in detalles)


@pytest.mark.parametrize("identificador", ["0", "-1", "2147483648", "abc"])
def test_un_identificador_fuera_de_rango_es_422(como_admin, llamadas, identificador):
    respuesta = como_admin.post(RUTA_PACIENTE.format(identificador), json=cuerpo())

    assert respuesta.status_code == 422
    assert llamadas.registro["provisionar"] == []


# --- cambio de estado --------------------------------------------------------


@pytest.mark.parametrize(
    ("activo", "accion"),
    [(False, AccionAuditada.CUENTA_DESACTIVADA), (True, AccionAuditada.CUENTA_REACTIVADA)],
)
def test_una_transicion_se_audita_y_confirma(como_admin, sesion, llamadas, activo, accion):
    respuesta = como_admin.patch(RUTA_ESTADO.format(ID_USUARIO_NUEVO), json={"activo": activo})

    assert respuesta.status_code == 200
    assert respuesta.json() == {"id_usuario": ID_USUARIO_NUEVO, "rol": "PACIENTE", "activo": activo}
    [entrada] = auditadas(sesion)
    assert entrada.accion == accion.value
    assert entrada.id_usuario == ID_ADMIN
    assert entrada.id_entidad_afectada == str(ID_USUARIO_NUEVO)
    assert (sesion.commits, sesion.rollbacks) == (1, 0)


@pytest.mark.parametrize("activo", [False, True])
def test_un_no_op_responde_200_sin_auditar_ni_confirmar(como_admin, sesion, llamadas, activo):
    llamadas.comportamiento["cambiar_estado"] = EstadoDeCuenta(
        id_usuario=ID_USUARIO_NUEVO, rol=NombreRol.MEDICO, activo=activo, hubo_transicion=False
    )

    respuesta = como_admin.patch(RUTA_ESTADO.format(ID_USUARIO_NUEVO), json={"activo": activo})

    assert respuesta.status_code == 200
    assert respuesta.json()["activo"] is activo
    assert auditadas(sesion) == []
    assert (sesion.commits, sesion.rollbacks) == (0, 1)


@pytest.mark.parametrize(
    ("error", "estado"),
    [(CuentaInexistente(), 404), (CuentaAdministrativa(), 409)],
)
def test_estado_de_cuenta_inexistente_o_admin(como_admin, sesion, llamadas, error, estado):
    llamadas.comportamiento["cambiar_estado"] = error

    respuesta = como_admin.patch(RUTA_ESTADO.format(ID_ADMIN), json={"activo": False})

    assert respuesta.status_code == estado
    assert auditadas(sesion) == []
    assert sesion.commits == 0


@pytest.mark.parametrize(
    "carga",
    [{"activo": "false"}, {"activo": 0}, {}, {"activo": False, "rol": "ADMIN"}],
    ids=["texto", "entero", "vacio", "extra"],
)
def test_cuerpos_de_estado_invalidos_son_422(como_admin, llamadas, carga):
    respuesta = como_admin.patch(RUTA_ESTADO.format(ID_USUARIO_NUEVO), json=carga)

    assert respuesta.status_code == 422
    assert llamadas.registro["cambiar_estado"] == []


# --- inventario --------------------------------------------------------------


def rutas_de_cuentas() -> set[tuple[str, str]]:
    from tests.test_rbac import aplanar

    return {
        (metodo, camino)
        for ruta, camino in aplanar(app.routes)
        if camino.startswith("/api/v1/cuentas")
        for metodo in getattr(ruta, "methods", set())
    }


def test_existen_exactamente_las_tres_rutas_aprobadas():
    assert rutas_de_cuentas() == {
        ("POST", "/api/v1/cuentas/pacientes/{id_paciente}"),
        ("POST", "/api/v1/cuentas/medicos/{id_medico}"),
        ("PATCH", "/api/v1/cuentas/{id_usuario}/estado"),
    }


def test_no_existe_un_get_de_cuentas(base_http):
    assert not any(metodo == "GET" for metodo, _ in rutas_de_cuentas())
    with identidad_simulada(app, principal_de_prueba(NombreRol.ADMIN, ID_ADMIN)):
        assert base_http.get("/api/v1/cuentas/137/estado").status_code == 405


def test_las_tres_rutas_declaran_la_guardia_admin():
    from tests.test_rbac import aplanar, dependencias_de

    for ruta, camino in aplanar(app.routes):
        if camino.startswith("/api/v1/cuentas"):
            assert "verificar_rol" in dependencias_de(ruta)


def test_la_guardia_admite_solo_admin():
    from fastapi import Depends, FastAPI

    from app.api.dependencias import usuario_actual

    aplicacion = FastAPI()

    @aplicacion.get("/sonda")
    def sonda(contexto=Depends(router_cuentas.EXIGIR_ADMIN)) -> dict:
        return {"rol": contexto.rol.value}

    # Desde SCRUM-98 la guardia tambien instala el contexto clinico, asi que
    # depende de la sesion de negocio ademas de la de auditoria. Sin este
    # segundo override la sonda saldria a buscar el engine real.
    aplicacion.dependency_overrides[get_db] = lambda: SesionDeCuentas()
    aplicacion.dependency_overrides[get_db_auditoria] = lambda: SesionDeCuentas()
    permitidos = set()
    for rol in NombreRol:
        aplicacion.dependency_overrides[usuario_actual] = lambda rol=rol: principal_de_prueba(rol)
        if TestClient(aplicacion).get("/sonda").status_code == 200:
            permitidos.add(rol)

    assert permitidos == {NombreRol.ADMIN}


# ---------------------------------------------------------------------------
# 6. La revision de Alembic, renderizada sin servidor
# ---------------------------------------------------------------------------


def _renderizar(direccion: str) -> str:
    script = ScriptDirectory.from_config(construir_config_alembic())
    salida = io.StringIO()
    contexto = MigrationContext.configure(
        dialect=postgresql.dialect(),
        opts={"as_sql": True, "output_buffer": salida, "target_metadata": Base.metadata},
    )
    with Operations.context(contexto):
        getattr(script.get_revision(REVISION_SCRUM_97).module, direccion)()
    return salida.getvalue()


@pytest.fixture(scope="module")
def sql_upgrade() -> str:
    return _renderizar("upgrade")


@pytest.fixture(scope="module")
def sql_downgrade() -> str:
    return _renderizar("downgrade")


def test_la_revision_encadena_sobre_la_analitica():
    """SCRUM-97 sigue colgando de la analitica, aunque ya no sea la cabeza.

    Desde SCRUM-98 la cabeza es otra; lo que esta prueba fija es el eslabon de
    SCRUM-97, no cual es el ultimo de la cadena.
    """
    script = ScriptDirectory.from_config(construir_config_alembic())

    assert script.get_revision(REVISION_SCRUM_97).down_revision == REVISION_ANALITICA
    assert len(script.get_heads()) == 1


def test_valida_antes_de_normalizar_y_de_instalar(sql_upgrade):
    verificacion_emails = sql_upgrade.index("v_colisiones")
    normalizacion = sql_upgrade.index("UPDATE operacional.usuario SET email")
    check = sql_upgrade.index("ADD CONSTRAINT ck_usuario_email_canonico")
    verificacion_vinculos = sql_upgrade.index("v_incoherentes")
    funcion = sql_upgrade.index("CREATE FUNCTION")
    primer_trigger = sql_upgrade.index("CREATE CONSTRAINT TRIGGER")

    assert sql_upgrade.index("LOCK TABLE") < verificacion_emails
    assert verificacion_emails < normalizacion < check
    assert verificacion_vinculos < funcion < primer_trigger


def test_el_check_de_la_migracion_es_el_del_modelo(sql_upgrade):
    [check] = [
        c for c in Base.metadata.tables["operacional.usuario"].constraints
        if getattr(c, "name", None) == "ck_usuario_email_canonico"
    ]

    assert f"CHECK ({check.sqltext.text})" in sql_upgrade


def test_tres_constraint_triggers_diferidos(sql_upgrade):
    triggers = [s for s in sql_upgrade.split(";\n") if "CREATE CONSTRAINT TRIGGER" in s]

    assert len(triggers) == 3
    assert all("DEFERRABLE INITIALLY DEFERRED" in t for t in triggers)
    assert all("FOR EACH ROW EXECUTE FUNCTION operacional.validar_rol_vinculo_usuario()" in t for t in triggers)
    assert {t.split(" ON ")[1].split()[0] for t in triggers} == {
        "operacional.usuario",
        "operacional.usuario_paciente",
        "operacional.usuario_medico",
    }


def test_la_funcion_resuelve_el_rol_por_nombre_y_bloquea_la_cuenta(sql_upgrade):
    assert "JOIN operacional.rol r ON r.id_rol = u.id_rol" in sql_upgrade
    assert "FOR NO KEY UPDATE OF u" in sql_upgrade
    assert f"CONSTRAINT = '{RESTRICCION_ROL_VINCULO}'" in sql_upgrade
    assert "ERRCODE = 'check_violation'" in sql_upgrade
    for rol in ("PACIENTE", "MEDICO", "ADMIN"):
        assert f"v_rol = '{rol}'" in sql_upgrade
    assert "id_rol = 100" not in sql_upgrade and "id_rol = 1 " not in sql_upgrade


def test_no_introduce_extensiones_indices_ni_citext(sql_upgrade):
    for prohibido in ("citext", "CREATE EXTENSION", "CREATE INDEX", "CREATE UNIQUE INDEX", "CREATE TABLE"):
        assert prohibido not in sql_upgrade


def test_el_downgrade_retira_todo_sin_cascade(sql_downgrade):
    assert sql_downgrade.count("DROP TRIGGER") == 3
    assert "DROP FUNCTION operacional.validar_rol_vinculo_usuario()" in sql_downgrade
    assert "DROP CONSTRAINT ck_usuario_email_canonico" in sql_downgrade
    assert "CASCADE" not in sql_downgrade
    assert "IF EXISTS" not in sql_downgrade
    assert sql_downgrade.index("DROP TRIGGER") < sql_downgrade.index("DROP FUNCTION")


# ---------------------------------------------------------------------------
# 7. Integracion continua
# ---------------------------------------------------------------------------


def test_el_ci_ejecuta_la_suite_postgresql_de_cuentas_y_la_vigila():
    from tests.test_ci_workflow import RUTA_CI, bloque_run

    guardian = bloque_run("Verificar que ninguna prueba de PostgreSQL quedó omitida")
    ci = RUTA_CI.read_text(encoding="utf-8")
    step = ci.split("- name: Pruebas de cuentas contra PostgreSQL (SCRUM-97)", 1)[1].split("- name:", 1)[0]

    assert "run: python -m pytest -q -rs tests/test_cuentas_postgresql.py --junitxml=pytest-scrum97.xml" in step
    assert "SCRUM97_TEST_DATABASE_URL: postgresql+psycopg://" in step
    assert '"pytest-scrum97.xml": "SCRUM97_TEST_DATABASE_URL"' in guardian
    # El total crece con cada capa nueva: 9 hasta SCRUM-97, y 13 desde que
    # SCRUM-98 anadio pytest-scrum98.xml (roles), pytest-scrum98-contexto.xml
    # (contexto y pool), pytest-scrum98-rls.xml (aislamiento por filas) y
    # pytest-scrum98-http.xml (recorridos HTTP como fetalalert_api), 14 con
    # pytest-scrum98-pub.xml y 15 desde que SCRUM-72 anadio
    # pytest-scrum72-provision.xml (aprovisionamiento de la demo sobre su base
    # desechable). Sigue siendo un numero exacto y no un ">=" a proposito: lo
    # que vigila es que nadie retire un reporte del guardian al anadir el suyo.
    assert '"pytest-scrum72-provision.xml": "SCRUM72_PROVISION_TEST_DATABASE_URL"' in guardian
    assert guardian.count('.xml": "SCRUM') == 15


def test_la_migracion_no_usa_marcadores_que_psycopg_o_sqlalchemy_interpreten(sql_upgrade):
    assert "%" not in sql_upgrade
    assert "[[:" not in sql_upgrade
