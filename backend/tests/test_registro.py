"""Autorregistro de pacientes, sin servidor PostgreSQL (SCRUM-110).

Lo que se prueba aqui es **nuestro** contrato: que campos acepta el cuerpo y como
los normaliza, que textos legales se sirven y con que huella, que el servicio
hashea la contrasena tal cual y toma version y huella del servidor, como se
clasifican los rechazos de PostgreSQL, que el router confirma una sola vez y
revierte en todo lo demas, que audita a la propia cuenta, que limita las
solicitudes y que nada sensible sale en una respuesta o un log.

Lo que depende de filas reales -- la funcion SECURITY DEFINER, los privilegios,
el trigger diferido, las carreras y el inicio de sesion inmediato -- vive en
``test_registro_postgresql.py``.

Todos los datos son ficticios y completamente simulados.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, OperationalError

from app.api.v1 import registro as router_registro
from app.api.v1.autenticacion import RUTAS_CON_CREDENCIALES
from app.db.session import get_db
from app.legal import (
    DIRECTORIO_DE_TEXTOS,
    DOCUMENTOS_VIGENTES,
    VERSION_POLITICA_PRIVACIDAD,
    VERSION_TERMINOS_CONDICIONES,
    huella,
    normalizar,
    vigente,
)
from app.main import app
from app.models.clinico import Paciente, TelefonoPaciente
from app.models.enums import DocumentoLegal, NombreRol
from app.models.seguridad import AceptacionDocumento, AuditoriaLog, Usuario
from app.schemas import registro as esquema
from app.schemas.autenticacion import CredencialesEntrada
from app.schemas.registro import (
    MENSAJE_EMAIL_FORMATO,
    PATRON_EMAIL_REGISTRO,
    AutorregistroEntrada,
    CuentaAutorregistrada,
)
from app.services import autorregistro as servicio
from app.services.auditoria import ENTIDAD_USUARIO, AccionAuditada
from app.services.limite import LimiteDeSolicitudes
from app.services.passwords import PREFIJO_ARGON2ID, verificar

RUTA = "/api/v1/registro/pacientes"
RUTA_DOCUMENTOS = "/api/v1/registro/documentos"
RAIZ_BACKEND = Path(__file__).resolve().parents[1]

PASSWORD = "  Clave Simulada 110  "
ID_PACIENTE_NUEVO = 4100
ID_USUARIO_NUEVO = 4200

# Huellas de los textos vigentes. Cambiar una palabra de un texto sin cambiar
# su version rompe esta prueba: un texto distinto bajo la misma etiqueta haria
# que las aceptaciones guardadas no dijeran que se acepto.
HUELLAS_VIGENTES = {
    DocumentoLegal.POLITICA_PRIVACIDAD: (
        # 2026-11-v3: textos escritos como los de un servicio en operacion
        # (correo de acceso asignado, acompanamiento del personal de salud,
        # Ley 81 y derechos ARCO), con una seccion final que aclara que esta
        # version es un prototipo academico con datos simulados. Ni v1 ni v2
        # se publicaron: v2 solo se acepto en una base temporal ya eliminada.
        "2026-11-v3",
        "ef37ea4ea2b2e667411456eb141a657fa1bad4e2c140ec52cf9a9afebdbf215f",
    ),
    DocumentoLegal.TERMINOS_CONDICIONES: (
        "2026-11-v3",
        "8362b180aea2bb036dbdb160fc987986d94aa9c23eb4d987ecd5b75afbf808b1",
    ),
}


def cuerpo(**cambios) -> dict:
    base = {
        "primer_nombre": "Lucía",
        "segundo_nombre": "",
        "apellido_paterno": "Herrera",
        "apellido_materno": "Quintero",
        "cedula": "8-910-1101",
        "fecha_nac": "1996-04-18",
        "email": "paciente41@example.com",
        "celular": "6000-1101",
        "password": PASSWORD,
        "confirmacion_password": PASSWORD,
        "acepta_politica_privacidad": True,
        "version_politica_privacidad": VERSION_POLITICA_PRIVACIDAD,
        "acepta_terminos_condiciones": True,
        "version_terminos_condiciones": VERSION_TERMINOS_CONDICIONES,
    }
    base.update(cambios)
    return base


def errores(**cambios) -> list[dict]:
    with pytest.raises(ValidationError) as error:
        AutorregistroEntrada.model_validate(cuerpo(**cambios))
    return error.value.errors()


# ---------------------------------------------------------------------------
# 1. El cuerpo
# ---------------------------------------------------------------------------


def test_el_cuerpo_tiene_exactamente_estos_campos():
    assert set(AutorregistroEntrada.model_fields) == {
        "primer_nombre",
        "segundo_nombre",
        "apellido_paterno",
        "apellido_materno",
        "cedula",
        "fecha_nac",
        "email",
        "celular",
        "password",
        "confirmacion_password",
        "acepta_politica_privacidad",
        "version_politica_privacidad",
        "acepta_terminos_condiciones",
        "version_terminos_condiciones",
    }


@pytest.mark.parametrize(
    "extra",
    [
        {"rol": "ADMIN"},
        {"id_rol": 100},
        {"activo": False},
        {"id_paciente": 100},
        {"id_usuario": 107},
        {"id_clinica": 100},
        {"password_hash": "$argon2id$x"},
    ],
)
def test_el_cliente_no_elige_rol_estado_ni_perfil(extra):
    assert errores(**extra)[0]["type"] == "extra_forbidden"


def test_normaliza_espacios_exteriores_y_opcionales_vacios():
    entrada = AutorregistroEntrada.model_validate(
        cuerpo(
            primer_nombre="  Lucía  ",
            segundo_nombre="   ",
            apellido_materno="",
            cedula=" 8-910-1101 ",
            celular=" 6000-1101 ",
        )
    )
    assert entrada.primer_nombre == "Lucía"
    assert entrada.segundo_nombre is None
    assert entrada.apellido_materno is None
    assert entrada.cedula == "8-910-1101"
    assert entrada.celular == "6000-1101"


def test_un_opcional_con_contenido_se_conserva_sin_espacios_exteriores():
    entrada = AutorregistroEntrada.model_validate(cuerpo(segundo_nombre="  María José "))
    assert entrada.segundo_nombre == "María José"


@pytest.mark.parametrize("campo", ["primer_nombre", "apellido_paterno", "cedula", "celular"])
@pytest.mark.parametrize("valor", ["", "   ", "\t\n "])
def test_un_obligatorio_vacio_o_de_espacios_se_rechaza(campo, valor):
    assert [e["loc"] for e in errores(**{campo: valor})] == [(campo,)]


@pytest.mark.parametrize("campo", ["primer_nombre", "apellido_paterno", "cedula", "celular", "email"])
def test_un_obligatorio_ausente_se_rechaza(campo):
    datos = cuerpo()
    del datos[campo]
    with pytest.raises(ValidationError):
        AutorregistroEntrada.model_validate(datos)


@pytest.mark.parametrize(
    "campo,modelo,columna",
    [
        ("primer_nombre", Paciente, "primer_nombre"),
        ("segundo_nombre", Paciente, "segundo_nombre"),
        ("apellido_paterno", Paciente, "apellido_paterno"),
        ("apellido_materno", Paciente, "apellido_materno"),
        ("cedula", Paciente, "cedula"),
        ("celular", TelefonoPaciente, "valor_contacto"),
    ],
)
def test_las_longitudes_salen_de_las_columnas(campo, modelo, columna):
    limite = modelo.__table__.c[columna].type.length
    AutorregistroEntrada.model_validate(cuerpo(**{campo: "x" * limite}))
    assert errores(**{campo: "x" * (limite + 1)})[0]["loc"] == (campo,)


def test_el_correo_de_perfil_y_de_acceso_tienen_el_mismo_limite():
    assert (
        Paciente.__table__.c.email_pac.type.length
        == Usuario.__table__.c.email.type.length
    )


def test_el_correo_usa_el_mismo_tipo_que_el_login():
    assert (
        AutorregistroEntrada.model_fields["email"].metadata
        == CredencialesEntrada.model_fields["email"].metadata
    )
    entrada = AutorregistroEntrada.model_validate(cuerpo(email="  Paciente41@Example.COM "))
    assert entrada.email == "paciente41@example.com"


@pytest.mark.parametrize(
    "valor",
    ["paciente31@example.com", "paciente01@example.com", "paciente123@example.com", " PACIENTE31@EXAMPLE.COM "],
)
def test_el_correo_asignado_tiene_el_formato_pacientenn(valor):
    entrada = AutorregistroEntrada.model_validate(cuerpo(email=valor))
    assert PATRON_EMAIL_REGISTRO.fullmatch(entrada.email)


@pytest.mark.parametrize(
    "valor",
    [
        "lucia.herrera@example.com",  # un correo personal
        "paciente1@example.com",  # un solo digito
        "pacienteab@example.com",
        "paciente31@example.co",  # dominio distinto
        "paciente31@mail.example.com",
        "paciente31@example.com.pa",
        "xpaciente31@example.com",
        "paciente٣١@example.com",  # digitos que no son 0-9
    ],
)
def test_el_registro_rechaza_otro_formato_de_correo_con_un_ejemplo(valor):
    [error] = errores(email=valor)
    assert error["loc"] == ("email",)
    # Un mensaje fijo, con la regla y un ejemplo; no repite lo recibido.
    assert MENSAJE_EMAIL_FORMATO in error["msg"]
    assert "pacienteNN@example.com" in MENSAJE_EMAIL_FORMATO
    assert "paciente31@example.com" in MENSAJE_EMAIL_FORMATO


def test_el_formato_del_correo_no_afecta_al_inicio_de_sesion():
    """Las cuentas existentes y el login conservan sus reglas: solo el registro lo exige."""
    for correo in ("medico01@example.com", "admin01@example.com", "lucia.herrera@example.com"):
        assert CredencialesEntrada.model_validate({"email": correo, "password": "x"}).email == correo


@pytest.mark.parametrize("valor", [123, None, ["8-1-1"]])
def test_la_cedula_debe_ser_texto(valor):
    assert errores(cedula=valor)[0]["loc"] == ("cedula",)


def test_la_cedula_no_tiene_un_formato_impuesto():
    for cedula in ("8-910-1101", "PE-12-345", "E-8-123456", "N-19-2020", "SIM-PAC-110"):
        assert AutorregistroEntrada.model_validate(cuerpo(cedula=cedula)).cedula == cedula


def test_no_hay_rango_de_edad():
    for fecha in ("1900-01-01", "2026-01-01"):
        AutorregistroEntrada.model_validate(cuerpo(fecha_nac=fecha))


def test_la_fecha_de_hoy_se_acepta_y_la_de_manana_no(monkeypatch):
    monkeypatch.setattr(esquema, "hoy_clinico", lambda: date(2026, 11, 5))
    AutorregistroEntrada.model_validate(cuerpo(fecha_nac="2026-11-05"))
    assert errores(fecha_nac="2026-11-06")[0]["msg"].endswith(esquema.MENSAJE_FECHA_FUTURA)


@pytest.mark.parametrize(
    "valor",
    ["1996-02-30", "1996-13-01", "96-04-18", "1996-4-18", "18/04/1996", "1996-04-18T00:00:00", 829785600, "", None],
)
def test_la_fecha_debe_ser_una_fecha_iso_real(valor):
    assert errores(fecha_nac=valor)[0]["loc"] == ("fecha_nac",)


def test_hoy_clinico_es_el_dia_de_panama():
    from datetime import datetime

    from app.etl.reglas import ZONA_HORARIA_CLINICA

    assert esquema.hoy_clinico() == datetime.now(ZONA_HORARIA_CLINICA).date()


def test_la_password_no_se_recorta_ni_se_transforma():
    entrada = AutorregistroEntrada.model_validate(cuerpo())
    assert entrada.password.get_secret_value() == PASSWORD


@pytest.mark.parametrize("confirmacion", [PASSWORD.strip(), PASSWORD.upper(), PASSWORD + " ", ""])
def test_la_confirmacion_debe_ser_identica(confirmacion):
    resultado = errores(confirmacion_password=confirmacion)
    assert resultado


def test_no_hay_politica_de_contrasena_nueva():
    """De 1 a 512 caracteres, como el login. Ni complejidad ni minimo nuevo."""
    for password in ("a", " ", "1234", "x" * 512):
        AutorregistroEntrada.model_validate(cuerpo(password=password, confirmacion_password=password))
    assert errores(password="", confirmacion_password="")
    assert errores(password="x" * 513, confirmacion_password="x" * 513)


@pytest.mark.parametrize("campo", ["acepta_politica_privacidad", "acepta_terminos_condiciones"])
@pytest.mark.parametrize("valor", [False, None, 1, "true", "si"])
def test_cada_aceptacion_debe_ser_el_booleano_true(campo, valor):
    assert errores(**{campo: valor})[0]["loc"] == (campo,)


@pytest.mark.parametrize("campo", ["acepta_politica_privacidad", "acepta_terminos_condiciones"])
def test_cada_aceptacion_es_obligatoria(campo):
    datos = cuerpo()
    del datos[campo]
    with pytest.raises(ValidationError):
        AutorregistroEntrada.model_validate(datos)


@pytest.mark.parametrize("campo", ["version_politica_privacidad", "version_terminos_condiciones"])
@pytest.mark.parametrize("valor", ["2025-01-v0", "", " 2026-11-v3", None])
def test_la_version_aceptada_debe_ser_la_vigente(campo, valor):
    assert errores(**{campo: valor})[0]["loc"] == (campo,)


def test_los_mensajes_no_repiten_valores():
    for error in errores(fecha_nac="2999-01-01", confirmacion_password="otra"):
        assert "2999" not in error["msg"]
        assert PASSWORD not in error["msg"]


def test_la_respuesta_no_tiene_campos_sensibles():
    assert set(CuentaAutorregistrada.model_fields) == {"id_usuario", "id_paciente", "rol", "activo"}


# ---------------------------------------------------------------------------
# 2. Los textos legales
# ---------------------------------------------------------------------------


def test_hay_exactamente_los_dos_documentos():
    assert set(DOCUMENTOS_VIGENTES) == set(DocumentoLegal)


def test_la_huella_es_la_del_texto_servido():
    for documento in DOCUMENTOS_VIGENTES.values():
        assert documento.sha256 == huella(documento.texto)
        assert re.fullmatch(r"[0-9a-f]{64}", documento.sha256)


def test_cada_texto_conserva_su_version_y_su_huella():
    """Cambiar un texto obliga a cambiar su version, y entonces esta tabla."""
    for documento, (version, sha256) in HUELLAS_VIGENTES.items():
        assert (vigente(documento).version, vigente(documento).sha256) == (version, sha256)


def test_la_huella_no_depende_de_los_finales_de_linea():
    for documento in DOCUMENTOS_VIGENTES.values():
        assert "\r" not in documento.texto
        con_crlf = documento.texto.replace("\n", "\r\n")
        assert huella(normalizar(con_crlf)) == documento.sha256


def test_las_versiones_caben_en_la_columna():
    ancho = AceptacionDocumento.__table__.c.version.type.length
    for documento in DOCUMENTOS_VIGENTES.values():
        assert 0 < len(documento.version) <= ancho


def test_los_textos_no_quedan_fuera_de_la_imagen_de_la_api():
    """``.dockerignore`` excluye ``*.md``: los textos tienen que ser otra cosa."""
    patrones = [
        linea.strip()
        for linea in (RAIZ_BACKEND / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if linea.strip() and not linea.startswith("#")
    ]
    from fnmatch import fnmatch

    for archivo in DIRECTORIO_DE_TEXTOS.glob("*.txt"):
        relativa = archivo.relative_to(RAIZ_BACKEND).as_posix()
        assert not any(fnmatch(archivo.name, p) or fnmatch(relativa, p) for p in patrones), relativa
    assert len(list(DIRECTORIO_DE_TEXTOS.glob("*.txt"))) == 2


def test_la_politica_describe_el_sistema_real():
    # Desde 2026-11-v3 el texto habla como el de un servicio en operacion (para
    # la demostracion), pero solo describe lo que el sistema hace de verdad.
    texto = vigente(DocumentoLegal.POLITICA_PRIVACIDAD).texto.lower()
    assert "ley 81 de 2019" in texto
    assert "consentimiento previo, informado e inequívoco" in texto
    # El correo es el de acceso que asigna el personal, no uno personal.
    assert "correo de acceso que te asigna el personal de fetalalert" in texto
    # La vista medica identifica; la administrativa agrega y suprime.
    assert "puede identificarte" in texto
    assert "nombre, cédula, celular, correo de acceso y fecha de nacimiento" in texto
    assert "cifras agregadas" in texto
    assert "menos de cinco embarazos" in texto
    # Lo que no esta comprobado no se promete. De Power BI solo se comprobo que
    # sus lecturas no pasan por la aplicacion ni por su auditoria; el texto no
    # afirma nada sobre otros registros que puedan existir.
    assert "no quedan en ese registro" in texto
    assert "ninguna consulta" not in texto
    assert "no incluye las consultas" not in texto
    assert "no se puede eliminar desde el portal" in texto


@pytest.mark.parametrize(
    "frase",
    [
        "cifrad",
        "todos los accesos",
        "garantiza la seguridad",
        "cumple con la ley",
        "derecho de supresión",
        "puedes eliminar",
        "seudonimizad",
    ],
)
def test_los_textos_no_prometen_lo_que_el_prototipo_no_hace(frase):
    for documento in DOCUMENTOS_VIGENTES.values():
        assert frase not in documento.texto.lower(), (documento.documento, frase)


def test_los_terminos_dicen_que_no_es_atencion_medica_y_que_la_cuenta_es_inmediata():
    texto = vigente(DocumentoLegal.TERMINOS_CONDICIONES).texto.lower()
    assert "no sustituye tus controles prenatales" in texto
    assert "iniciar sesión de inmediato" in texto
    assert "acompañada por el personal de salud" in texto
    assert "te asigna tu correo de acceso" in texto
    # La sincronizacion es diferida: el texto no deja suponer revision en vivo.
    assert "no las revisa en tiempo real" in texto


@pytest.mark.parametrize("documento", list(DocumentoLegal))
def test_los_textos_no_presentan_un_servicio_clinico_real(documento):
    """El tono es el de un servicio, pero cada texto aclara su alcance real."""
    texto = vigente(documento).texto.lower()
    assert "alcance de esta versión" in texto
    assert "prototipo académico" in texto
    assert "no es un servicio clínico en operación" in texto
    assert "datos son simulados" in texto
    assert "no escribas en ella tus datos reales" in texto


# ---------------------------------------------------------------------------
# 3. El servicio
# ---------------------------------------------------------------------------


class _Fila(tuple):
    pass


class SesionFalsa:
    """Doble de Session: responde la llamada a la funcion y anota la traza."""

    def __init__(self) -> None:
        self.llamadas: list[dict] = []
        self.agregados: list = []
        self.commits = 0
        self.rollbacks = 0
        self.fallo_en_llamada: BaseException | None = None
        self.fallo_en_commit: BaseException | None = None
        self.fallo_en_auditoria: BaseException | None = None

    def execute(self, sentencia, parametros=None, *args, **kwargs):
        texto = str(sentencia)
        if "autorregistrar_paciente" in texto:
            self.llamadas.append(dict(parametros))
            if self.fallo_en_llamada is not None:
                raise self.fallo_en_llamada
            return SimpleNamespace(one=lambda: _Fila((ID_PACIENTE_NUEVO, ID_USUARIO_NUEVO)))
        if "auditoria_log" in texto:
            if self.fallo_en_auditoria is not None:
                raise self.fallo_en_auditoria
            self.agregados.append(AuditoriaLog(**parametros))
            return SimpleNamespace()
        raise AssertionError(f"Sentencia no prevista: {texto[:80]}")

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        if self.fallo_en_commit is not None:
            raise self.fallo_en_commit
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        pass


def test_el_servicio_hashea_la_password_exacta_y_no_la_envia_en_claro():
    sesion = SesionFalsa()
    cuenta = servicio.autorregistrar(sesion, AutorregistroEntrada.model_validate(cuerpo()))

    assert cuenta == servicio.CuentaCreada(id_paciente=ID_PACIENTE_NUEVO, id_usuario=ID_USUARIO_NUEVO)
    [parametros] = sesion.llamadas
    assert parametros["password_hash"].startswith(PREFIJO_ARGON2ID)
    assert verificar(parametros["password_hash"], PASSWORD)
    assert not verificar(parametros["password_hash"], PASSWORD.strip())
    assert PASSWORD not in repr(parametros)


def test_el_servicio_toma_version_y_huella_del_servidor():
    sesion = SesionFalsa()
    servicio.autorregistrar(sesion, AutorregistroEntrada.model_validate(cuerpo()))
    [parametros] = sesion.llamadas
    privacidad = vigente(DocumentoLegal.POLITICA_PRIVACIDAD)
    terminos = vigente(DocumentoLegal.TERMINOS_CONDICIONES)
    assert (parametros["version_privacidad"], parametros["sha256_privacidad"]) == (
        privacidad.version,
        privacidad.sha256,
    )
    assert (parametros["version_terminos"], parametros["sha256_terminos"]) == (
        terminos.version,
        terminos.sha256,
    )


def test_el_mismo_correo_va_al_perfil_y_a_la_cuenta():
    """La funcion recibe un solo correo y lo escribe en los dos sitios."""
    sesion = SesionFalsa()
    servicio.autorregistrar(sesion, AutorregistroEntrada.model_validate(cuerpo(email="Paciente41@Example.COM")))
    [parametros] = sesion.llamadas
    assert parametros["email"] == "paciente41@example.com"
    assert sum(1 for clave in parametros if "email" in clave) == 1


def test_la_llamada_tipa_cada_parametro():
    texto = str(servicio.LLAMADA)
    assert texto.count("CAST(") == 13
    assert "seguridad.autorregistrar_paciente" in texto


class _Diag:
    def __init__(self, sqlstate, restriccion):
        self.sqlstate = sqlstate
        self.constraint_name = restriccion
        self.message_primary = "texto del driver con paciente41@example.com"
        self.table_name = None
        self.column_name = None
        self.schema_name = None


class _Original(Exception):
    def __init__(self, sqlstate, restriccion):
        super().__init__("detalle con 8-910-1101")
        self.sqlstate = sqlstate
        self.diag = _Diag(sqlstate, restriccion)


def error_de_integridad(sqlstate: str, restriccion: str | None) -> IntegrityError:
    return IntegrityError("SELECT ... 'paciente41@example.com'", {"cedula": "8-910-1101"}, _Original(sqlstate, restriccion))


def error_operacional(sqlstate: str) -> OperationalError:
    return OperationalError("SELECT ...", {}, _Original(sqlstate, None))


@pytest.mark.parametrize("restriccion", ["uq_paciente_cedula", "uq_paciente_email_pac", "uq_usuario_email"])
def test_un_duplicado_es_el_mismo_409_generico(restriccion):
    rechazo = servicio.clasificar_rechazo(error_de_integridad("23505", restriccion))
    assert isinstance(rechazo, servicio.DatosYaRegistrados)
    assert rechazo.detalle == servicio.MENSAJE_DATOS_YA_REGISTRADOS


@pytest.mark.parametrize("restriccion", ["autorregistro_datos_validos", "ck_usuario_email_canonico"])
def test_un_check_conocido_es_422(restriccion):
    assert isinstance(
        servicio.clasificar_rechazo(error_de_integridad("23514", restriccion)),
        servicio.DatosRechazados,
    )


@pytest.mark.parametrize("sqlstate", ["40001", "40P01"])
def test_serializacion_y_deadlock_son_conflicto(sqlstate):
    assert isinstance(servicio.clasificar_rechazo(error_operacional(sqlstate)), servicio.ConflictoConcurrente)


@pytest.mark.parametrize(
    "sqlstate,restriccion",
    [
        ("23505", "uq_telefono_paciente_contacto"),
        ("23505", "pk_usuario_paciente"),
        ("23514", "rol_vinculo_coherente"),
        ("23503", "fk_usuario_id_rol_rol"),
        ("23502", None),
    ],
)
def test_lo_inesperado_no_se_disfraza(sqlstate, restriccion):
    assert servicio.clasificar_rechazo(error_de_integridad(sqlstate, restriccion)) is None


# ---------------------------------------------------------------------------
# 4. El router
# ---------------------------------------------------------------------------


@pytest.fixture
def limitador():
    return LimiteDeSolicitudes(maximo_por_origen=100, maximo_global=100, ventana_segundos=60)


@pytest.fixture
def sesion():
    return SesionFalsa()


@pytest.fixture
def cliente(sesion, limitador):
    app.dependency_overrides[get_db] = lambda: sesion
    app.dependency_overrides[router_registro.obtener_limitador] = lambda: limitador
    try:
        yield TestClient(app, raise_server_exceptions=True)
    finally:
        app.dependency_overrides.clear()


def auditadas(sesion: SesionFalsa) -> list[AuditoriaLog]:
    return [e for e in sesion.agregados if isinstance(e, AuditoriaLog)]


def test_registra_sin_ninguna_credencial(cliente, sesion):
    respuesta = cliente.post(RUTA, json=cuerpo())

    assert respuesta.status_code == 201
    assert respuesta.json() == {
        "id_usuario": ID_USUARIO_NUEVO,
        "id_paciente": ID_PACIENTE_NUEVO,
        "rol": "PACIENTE",
        "activo": True,
    }
    assert sesion.commits == 1
    assert sesion.rollbacks == 0


def test_la_auditoria_nombra_a_la_propia_cuenta_y_va_en_la_misma_transaccion(cliente, sesion):
    cliente.post(RUTA, json=cuerpo())

    [entrada] = auditadas(sesion)
    assert entrada.accion == AccionAuditada.CUENTA_PACIENTE_AUTORREGISTRADA.value
    assert entrada.id_usuario == ID_USUARIO_NUEVO
    assert entrada.nombre_entidad_afectada == ENTIDAD_USUARIO
    assert entrada.id_entidad_afectada == str(ID_USUARIO_NUEVO)
    # Una sola entrada y un solo commit: la traza no se confirma por separado.
    assert sesion.commits == 1


def test_un_token_ajeno_no_cambia_nada(cliente, sesion):
    """No hay sesion previa: una cabecera Authorization no concede ni quita."""
    respuesta = cliente.post(RUTA, json=cuerpo(), headers={"Authorization": "Bearer cualquiera"})
    assert respuesta.status_code == 201
    [entrada] = auditadas(sesion)
    assert entrada.id_usuario == ID_USUARIO_NUEVO


def test_la_respuesta_no_lleva_datos_personales_ni_secretos(cliente, sesion):
    respuesta = cliente.post(RUTA, json=cuerpo())
    texto = respuesta.text
    [parametros] = sesion.llamadas
    for sensible in (PASSWORD, "paciente41@example.com", "8-910-1101", "6000-1101", "Herrera", parametros["password_hash"]):
        assert sensible not in texto


@pytest.mark.parametrize(
    "error,estado,mensaje",
    [
        (error_de_integridad("23505", "uq_paciente_cedula"), 409, servicio.MENSAJE_DATOS_YA_REGISTRADOS),
        (error_de_integridad("23505", "uq_usuario_email"), 409, servicio.MENSAJE_DATOS_YA_REGISTRADOS),
        (error_de_integridad("23514", "autorregistro_datos_validos"), 422, servicio.MENSAJE_DATOS_RECHAZADOS),
        (error_operacional("40001"), 409, servicio.MENSAJE_CONFLICTO_CONCURRENTE),
    ],
)
def test_los_rechazos_revierten_no_auditan_y_no_confirman(cliente, sesion, error, estado, mensaje):
    sesion.fallo_en_llamada = error
    respuesta = cliente.post(RUTA, json=cuerpo())

    assert respuesta.status_code == estado
    assert respuesta.json() == {"detail": mensaje}
    assert sesion.commits == 0
    assert sesion.rollbacks == 1
    assert auditadas(sesion) == []


def test_un_rechazo_en_el_commit_tambien_revierte(cliente, sesion):
    """El trigger diferido decide en el commit; su rechazo no confirma nada."""
    sesion.fallo_en_commit = error_de_integridad("23514", "rol_vinculo_coherente")
    respuesta = cliente.post(RUTA, json=cuerpo())

    assert respuesta.status_code == 500
    assert sesion.commits == 0
    assert sesion.rollbacks == 1


def test_un_fallo_de_auditoria_revierte_el_registro(cliente, sesion):
    sesion.fallo_en_auditoria = error_operacional("08006")
    respuesta = cliente.post(RUTA, json=cuerpo())

    assert respuesta.status_code == 500
    assert sesion.commits == 0
    assert sesion.rollbacks == 1


def test_un_fallo_inesperado_es_500_generico(cliente, sesion):
    sesion.fallo_en_llamada = RuntimeError("detalle interno con paciente41@example.com")
    respuesta = cliente.post(RUTA, json=cuerpo())

    assert respuesta.status_code == 500
    assert "lucia" not in respuesta.text
    assert sesion.rollbacks == 1


def test_un_rechazo_de_base_no_filtra_al_log_ni_a_la_respuesta(cliente, sesion, caplog):
    sesion.fallo_en_llamada = error_de_integridad("23505", "uq_paciente_cedula")
    with caplog.at_level(logging.DEBUG):
        respuesta = cliente.post(RUTA, json=cuerpo())

    for texto in (respuesta.text, caplog.text):
        for sensible in ("8-910-1101", "paciente41@example.com", PASSWORD.strip()):
            assert sensible not in texto


def test_un_cuerpo_invalido_no_llama_al_servicio_y_no_devuelve_la_password(cliente, sesion):
    respuesta = cliente.post(RUTA, json=cuerpo(confirmacion_password="Otra Clave Distinta", fecha_nac="2999-01-01"))

    assert respuesta.status_code == 422
    assert sesion.llamadas == []
    assert PASSWORD.strip() not in respuesta.text
    assert "Otra Clave Distinta" not in respuesta.text
    assert all("input" not in error for error in respuesta.json()["detail"])


def test_la_ruta_esta_entre_las_rutas_con_credenciales():
    assert RUTA in RUTAS_CON_CREDENCIALES


def test_las_rutas_administrativas_no_cambian():
    """El autorregistro no toca /cuentas: sigue siendo solo de ADMIN."""
    from app.api.v1 import cuentas

    rutas = {getattr(ruta, "path", None) for ruta in app.routes}
    # Con FastAPI reciente ``app.routes`` puede dar el router incluido entero; las
    # rutas de cada router ya llevan su prefijo.
    rutas |= {
        subruta.path
        for incluido in (router_registro.router, cuentas.router)
        for subruta in incluido.routes
    }
    assert RUTA in rutas
    assert {f"{cuentas.router.prefix}{cuentas.RUTA_PACIENTES}", f"{cuentas.router.prefix}{cuentas.RUTA_MEDICOS}"} <= rutas
    for ruta in cuentas.router.routes:
        dependencias = [d.call for d in ruta.dependant.dependencies]
        assert any(getattr(d, "__name__", "") == "resolver" for d in dependencias)


def test_el_router_de_registro_no_exige_ningun_rol():
    for ruta in router_registro.router.routes:
        nombres = {getattr(d.call, "__name__", "") for d in ruta.dependant.dependencies}
        assert "resolver" not in nombres
        assert "verificar" not in nombres


def test_los_documentos_se_sirven_tal_cual(cliente):
    respuesta = cliente.get(RUTA_DOCUMENTOS)

    assert respuesta.status_code == 200
    publicados = {d["documento"]: d for d in respuesta.json()["documentos"]}
    assert set(publicados) == {d.value for d in DocumentoLegal}
    for documento in DOCUMENTOS_VIGENTES.values():
        publicado = publicados[documento.documento.value]
        assert publicado["version"] == documento.version
        assert publicado["texto"] == documento.texto
        assert publicado["sha256"] == huella(publicado["texto"])


# ---------------------------------------------------------------------------
# 5. El limite de solicitudes
# ---------------------------------------------------------------------------


class Reloj:
    def __init__(self) -> None:
        self.ahora = 1000.0

    def __call__(self) -> float:
        return self.ahora


def test_el_limite_por_origen_y_su_espera():
    reloj = Reloj()
    limite = LimiteDeSolicitudes(maximo_por_origen=2, maximo_global=10, ventana_segundos=60, reloj=reloj)

    assert limite.consumir("a").permitida
    reloj.ahora += 10
    assert limite.consumir("a").permitida
    decision = limite.consumir("a")
    assert not decision.permitida
    assert decision.reintentar_en_segundos == 50
    assert limite.consumir("b").permitida

    reloj.ahora += 50
    assert limite.consumir("a").permitida


def test_el_limite_global_aplica_aunque_cambie_el_origen():
    reloj = Reloj()
    limite = LimiteDeSolicitudes(maximo_por_origen=5, maximo_global=3, ventana_segundos=60, reloj=reloj)
    for origen in ("a", "b", "c"):
        assert limite.consumir(origen).permitida
    assert not limite.consumir("d").permitida


def test_un_rechazo_no_alarga_la_espera():
    reloj = Reloj()
    limite = LimiteDeSolicitudes(maximo_por_origen=1, maximo_global=10, ventana_segundos=60, reloj=reloj)
    assert limite.consumir("a").permitida
    for _ in range(20):
        reloj.ahora += 1
        assert not limite.consumir("a").permitida
    reloj.ahora += 40
    assert limite.consumir("a").permitida


def test_el_diccionario_de_origenes_no_crece_sin_limite():
    reloj = Reloj()
    limite = LimiteDeSolicitudes(maximo_por_origen=1, maximo_global=5, ventana_segundos=10, reloj=reloj)
    for numero in range(200):
        limite.consumir(f"origen-{numero}")
        reloj.ahora += 3
    assert len(limite._por_origen) <= limite.maximo_global + 1


def test_el_limite_es_exacto_entre_hilos():
    limite = LimiteDeSolicitudes(maximo_por_origen=1000, maximo_global=50, ventana_segundos=60)
    permitidas = []
    candado = threading.Lock()

    def golpear():
        for _ in range(20):
            if limite.consumir("mismo").permitida:
                with candado:
                    permitidas.append(1)

    hilos = [threading.Thread(target=golpear) for _ in range(10)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()
    assert len(permitidas) == 50


@pytest.mark.parametrize("parametros", [(0, 1, 1), (1, 0, 1), (1, 1, 0)])
def test_el_limite_no_se_puede_desactivar_con_ceros(parametros):
    with pytest.raises(ValueError):
        LimiteDeSolicitudes(maximo_por_origen=parametros[0], maximo_global=parametros[1], ventana_segundos=parametros[2])


def test_el_endpoint_responde_429_con_retry_after_y_no_hashea(sesion):
    limite = LimiteDeSolicitudes(maximo_por_origen=2, maximo_global=10, ventana_segundos=600)
    app.dependency_overrides[get_db] = lambda: sesion
    app.dependency_overrides[router_registro.obtener_limitador] = lambda: limite
    try:
        cliente = TestClient(app)
        # Un cuerpo invalido tambien cuenta: el limite va antes de la validacion.
        assert cliente.post(RUTA, json={"rol": "ADMIN"}).status_code == 422
        assert cliente.post(RUTA, json=cuerpo()).status_code == 201
        respuesta = cliente.post(RUTA, json=cuerpo(cedula="8-910-1102", email="paciente42@example.com"))
    finally:
        app.dependency_overrides.clear()

    assert respuesta.status_code == 429
    assert int(respuesta.headers["Retry-After"]) > 0
    assert respuesta.json() == {"detail": router_registro.MENSAJE_DEMASIADAS_SOLICITUDES}
    assert len(sesion.llamadas) == 1


def test_el_limite_no_afecta_a_los_documentos():
    limite = LimiteDeSolicitudes(maximo_por_origen=1, maximo_global=1, ventana_segundos=600)
    app.dependency_overrides[router_registro.obtener_limitador] = lambda: limite
    try:
        cliente = TestClient(app)
        for _ in range(5):
            assert cliente.get(RUTA_DOCUMENTOS).status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_los_limites_por_omision_estan_acotados():
    from app.config import Settings

    campos = Settings.model_fields
    for nombre in ("registro_ventana_segundos", "registro_maximo_por_origen", "registro_maximo_global"):
        assert campos[nombre].default >= 1
    with pytest.raises(ValidationError):
        Settings(registro_maximo_por_origen=0)
    with pytest.raises(ValidationError):
        Settings(registro_ventana_segundos=0)


# ---------------------------------------------------------------------------
# 6. La migracion, leida sin servidor: lo que concede y a quien
# ---------------------------------------------------------------------------


def _migracion():
    from importlib import util

    ruta = next((RAIZ_BACKEND / "alembic" / "versions").glob("c5d2a8f1e073_*.py"))
    especificacion = util.spec_from_file_location("migracion_scrum110", ruta)
    modulo = util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


def test_la_api_solo_recibe_execute_sobre_la_funcion():
    m = _migracion()
    concesiones = m._privilegios_del_dueno() + m._permisos_de_la_funcion()
    a_la_api = [s for s in concesiones if s.startswith("GRANT") and s.endswith(m.ROL_API)]
    assert a_la_api == [f"GRANT EXECUTE ON FUNCTION {m.FIRMA} TO fetalalert_api"]
    assert f"REVOKE ALL ON FUNCTION {m.FIRMA} FROM PUBLIC" in concesiones


def test_el_dueno_solo_recibe_insert_y_secuencias():
    m = _migracion()
    for sentencia in m._privilegios_del_dueno():
        assert sentencia.startswith(("GRANT INSERT ON TABLE", "GRANT USAGE ON SEQUENCE"))
        assert sentencia.endswith(f"TO {m.ROL_PROVISION_OWNER}")
    assert m.ROL_PROVISION_OWNER == "fetalalert_provision_owner"


def test_la_politica_nueva_es_solo_del_dueno_y_solo_insert():
    m = _migracion()
    assert m.POLITICA == (
        "CREATE POLICY pol_autorregistro ON operacional.usuario_paciente "
        "FOR INSERT TO fetalalert_provision_owner WITH CHECK (true)"
    )


def test_la_funcion_fija_rol_estado_y_ruta_de_busqueda():
    m = _migracion()
    cuerpo_sql = m.FUNCION_SQL
    assert "SECURITY DEFINER" in cuerpo_sql
    assert "SET search_path = pg_catalog, operacional, pg_temp" in cuerpo_sql
    assert "WHERE r.nombre_rol = 'PACIENTE'" in cuerpo_sql
    assert "'ADMIN'" not in cuerpo_sql and "'MEDICO'" not in cuerpo_sql
    assert "VALUES (v_rol, p_email, p_password_hash, true)" in cuerpo_sql
    # No recibe ningun identificador de perfil ni de cuenta: no puede vincular
    # un perfil existente.
    assert "p_id_" not in cuerpo_sql


def test_el_orden_concede_antes_de_transferir_la_propiedad():
    from inspect import getsource

    fuente = getsource(_migracion().upgrade)
    assert fuente.index("_permisos_de_la_funcion()") < fuente.index("OWNER TO")


# ---------------------------------------------------------------------------
# 7. La salvaguarda de la suite de PostgreSQL, probada sin servidor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "base",
    ["scrum52_validacion_tmp", "scrum110_registro", "scrum110_a1"],
)
def test_la_suite_postgresql_admite_solo_bases_desechables(base):
    from tests.test_registro_postgresql import exigir_base_desechable

    assert exigir_base_desechable(f"postgresql+psycopg://fetalalert_api:x@127.0.0.1:5432/{base}") == base


@pytest.mark.parametrize(
    "base",
    [
        "fetalalert_demo_sustentacion",
        "fetalalert_demo_baseline",
        "fetalalert_dev",
        "scrum72_demo_gestante",
        "scrum110_",
        "scrum110_registro\n",
        "SCRUM110_x",
        "",
    ],
)
def test_la_suite_postgresql_rechaza_la_base_de_la_demo_y_cualquier_otra(base):
    from tests.test_registro_postgresql import BaseNoAutorizada, exigir_base_desechable

    with pytest.raises(BaseNoAutorizada):
        exigir_base_desechable(f"postgresql+psycopg://fetalalert_api:x@127.0.0.1:5432/{base}")
