"""Auditoria: catalogo, frontera transaccional y saneamiento (SCRUM-70).

Lo que se comprueba aqui no es tanto que se escriba una fila como **cuando** se
escribe y **que no** lleva dentro. El caso que mas importa es el que no deja
rastro: una operacion de negocio que revierte no puede dejar detras una
auditoria de exito, y la unica forma de garantizarlo es que la entrada viva en la
misma transaccion que el paquete. Si alguien anadiera un commit intermedio, la
prueba del rollback lo dice.

Lo que esta tabla **no** es: inmutable criptograficamente, ni una garantia de no
repudio. Es append-only por diseno de la aplicacion --solo se inserta-- y su FK
es ``ON DELETE RESTRICT``, de modo que una cuenta con historial no se puede
borrar. Nada mas, y nada menos.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.models.seguridad import AuditoriaLog
from app.services import auditoria as modulo
from app.services.auditoria import (
    ENTIDAD_SESION_MONITOREO,
    ENTIDAD_USUARIO,
    IP_DESCONOCIDA,
    LONGITUD_MAXIMA_IP,
    MENSAJE_FALLO_DE_AUDITORIA,
    AccionAuditada,
    FalloDeAuditoria,
    direccion_de_origen,
    registrar,
    registrar_con_commit,
)


class SesionDeAuditoria:
    """Doble que anota lo que se le pide, en el orden en que se le pide."""

    def __init__(self, *, fallar_en_commit: bool = False) -> None:
        self.fallar_en_commit = fallar_en_commit
        self.agregados: list = []
        self.pasos: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, entidad) -> None:
        self.agregados.append(entidad)
        self.pasos.append("add")

    def flush(self) -> None:
        self.pasos.append("flush")

    def commit(self) -> None:
        if self.fallar_en_commit:
            self.pasos.append("commit-fallido")
            raise SQLAlchemyError("fallo simulado de la base")
        self.commits += 1
        self.pasos.append("commit")

    def rollback(self) -> None:
        self.rollbacks += 1
        self.pasos.append("rollback")


@pytest.fixture
def sesion() -> SesionDeAuditoria:
    return SesionDeAuditoria()


# ---------------------------------------------------------------------------
# 1. El catalogo es cerrado y cabe en la columna
# ---------------------------------------------------------------------------


def test_el_catalogo_tiene_exactamente_ocho_acciones():
    """Las cuatro de SCRUM-70 y las cuatro del ciclo de cuentas de SCRUM-97."""
    assert {a.value for a in AccionAuditada} == {
        "LOGIN_EXITOSO",
        "LOGIN_FALLIDO",
        "ACCESO_DENEGADO_ROL",
        "SESION_MONITOREO_REGISTRADA",
        "CUENTA_PACIENTE_PROVISIONADA",
        "CUENTA_MEDICO_PROVISIONADA",
        "CUENTA_DESACTIVADA",
        "CUENTA_REACTIVADA",
    }


def test_no_existe_una_accion_para_cada_token_rechazado():
    """Se excluyo a proposito: seria una escritura sin autenticar en la tabla."""
    assert not any("TOKEN" in a.value for a in AccionAuditada)


def test_no_existe_una_accion_de_replay():
    """Un reenvio no crea ninguna fila de negocio que auditar."""
    assert not any("REPLAY" in a.value or "REENVIO" in a.value for a in AccionAuditada)


def test_cada_codigo_cabe_en_la_columna():
    ancho = AuditoriaLog.__table__.c.accion.type.length

    for accion in AccionAuditada:
        assert len(accion.value) <= ancho


def test_las_entidades_son_nombres_de_tabla_no_rutas():
    for entidad in (ENTIDAD_USUARIO, ENTIDAD_SESION_MONITOREO):
        assert "/" not in entidad
        assert entidad.islower()


# ---------------------------------------------------------------------------
# 2. ip_origen
# ---------------------------------------------------------------------------


def test_una_direccion_observada_se_registra():
    assert direccion_de_origen("127.0.0.1") == "127.0.0.1"


def test_sin_cliente_se_usa_el_literal_tecnico():
    """La columna es NOT NULL, asi que siempre hay un valor."""
    assert direccion_de_origen(None) == IP_DESCONOCIDA
    assert direccion_de_origen("") == IP_DESCONOCIDA


def test_una_direccion_que_no_cabe_se_sustituye_entera():
    """Media direccion no es una direccion: truncar la haria parecer real."""
    assert direccion_de_origen("x" * (LONGITUD_MAXIMA_IP + 1)) == IP_DESCONOCIDA


def test_el_literal_tecnico_cabe_en_la_columna():
    assert len(IP_DESCONOCIDA) <= LONGITUD_MAXIMA_IP


def test_una_ipv6_normal_cabe():
    ipv6 = "2001:0db8:85a3:0000:0000:8a2e:0370:7334"

    assert direccion_de_origen(ipv6) == ipv6


def test_no_se_consulta_x_forwarded_for():
    """No hay proxy de confianza, asi que esa cabecera la elige el cliente."""
    from pathlib import Path

    fuente = Path(modulo.__file__).read_text(encoding="utf-8")

    assert "X-Forwarded-For" in fuente  # se menciona para explicar por que no
    assert "headers.get" not in fuente
    assert "forwarded" not in fuente.lower().replace("x-forwarded-for", "")


# ---------------------------------------------------------------------------
# 3. registrar: sin commit, dentro de la transaccion ajena
# ---------------------------------------------------------------------------


def test_registrar_no_confirma_ni_revierte(sesion):
    registrar(
        sesion,
        AccionAuditada.SESION_MONITOREO_REGISTRADA,
        id_usuario=130,
        ip_origen="127.0.0.1",
    )

    assert sesion.commits == 0
    assert sesion.rollbacks == 0
    assert sesion.pasos == ["add", "flush"]


def test_registrar_hace_flush_para_que_el_fallo_salga_aqui(sesion):
    """Sin el, una violacion de restriccion aparecería en el commit del llamador."""
    registrar(sesion, AccionAuditada.LOGIN_EXITOSO, id_usuario=1, ip_origen="1.2.3.4")

    assert "flush" in sesion.pasos


def test_la_fila_lleva_los_campos_previstos(sesion):
    entrada = registrar(
        sesion,
        AccionAuditada.SESION_MONITOREO_REGISTRADA,
        id_usuario=130,
        ip_origen="10.0.0.5",
        nombre_entidad=ENTIDAD_SESION_MONITOREO,
        id_entidad="900",
    )

    assert entrada.id_usuario == 130
    assert entrada.accion == "SESION_MONITOREO_REGISTRADA"
    assert entrada.nombre_entidad_afectada == ENTIDAD_SESION_MONITOREO
    assert entrada.id_entidad_afectada == "900"
    assert entrada.ip_origen == "10.0.0.5"


def test_el_actor_desconocido_queda_en_nulo(sesion):
    """El modelo lo permite, y ``LOGIN_FALLIDO`` es el caso que lo motivo."""
    entrada = registrar(
        sesion, AccionAuditada.LOGIN_FALLIDO, id_usuario=None, ip_origen="1.2.3.4"
    )

    assert entrada.id_usuario is None
    assert entrada.nombre_entidad_afectada is None
    assert entrada.id_entidad_afectada is None


def test_el_instante_es_utc_consciente(sesion):
    entrada = registrar(
        sesion, AccionAuditada.LOGIN_EXITOSO, id_usuario=1, ip_origen="1.2.3.4"
    )

    assert entrada.fecha_hora.tzinfo is not None
    assert entrada.fecha_hora.utcoffset() == timedelta(0)


def test_el_instante_se_puede_inyectar(sesion):
    momento = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)

    entrada = registrar(
        sesion,
        AccionAuditada.LOGIN_EXITOSO,
        id_usuario=1,
        ip_origen="1.2.3.4",
        momento=momento,
    )

    assert entrada.fecha_hora == momento


# ---------------------------------------------------------------------------
# 4. registrar_con_commit: transaccion propia y fallo cerrado
# ---------------------------------------------------------------------------


def test_registrar_con_commit_confirma(sesion):
    registrar_con_commit(
        sesion, AccionAuditada.LOGIN_EXITOSO, id_usuario=130, ip_origen="1.2.3.4"
    )

    assert sesion.commits == 1
    assert sesion.pasos == ["add", "flush", "commit"]


def test_un_fallo_revierte_y_se_reporta():
    """Nunca ``except Exception: pass``: el llamador tiene que enterarse."""
    sesion = SesionDeAuditoria(fallar_en_commit=True)

    with pytest.raises(FalloDeAuditoria) as capturado:
        registrar_con_commit(
            sesion, AccionAuditada.LOGIN_EXITOSO, id_usuario=130, ip_origen="1.2.3.4"
        )

    assert sesion.rollbacks == 1
    assert capturado.value.detalle == MENSAJE_FALLO_DE_AUDITORIA


def test_el_error_de_la_base_queda_encadenado_pero_no_publicado():
    sesion = SesionDeAuditoria(fallar_en_commit=True)

    with pytest.raises(FalloDeAuditoria) as capturado:
        registrar_con_commit(
            sesion, AccionAuditada.LOGIN_EXITOSO, id_usuario=1, ip_origen="1.2.3.4"
        )

    assert isinstance(capturado.value.__cause__, SQLAlchemyError)
    assert "fallo simulado" not in str(capturado.value)


def test_el_log_del_fallo_no_lleva_los_valores_de_la_fila(caplog):
    caplog.set_level(logging.ERROR)
    sesion = SesionDeAuditoria(fallar_en_commit=True)

    with pytest.raises(FalloDeAuditoria):
        registrar_con_commit(
            sesion,
            AccionAuditada.LOGIN_EXITOSO,
            id_usuario=987654,
            ip_origen="203.0.113.9",
        )

    assert "987654" not in caplog.text
    assert "203.0.113.9" not in caplog.text
    assert "fallo simulado" not in caplog.text
    assert "LOGIN_EXITOSO" in caplog.text


# ---------------------------------------------------------------------------
# 5. La frontera transaccional en la ingesta
# ---------------------------------------------------------------------------


@pytest.fixture
def ingesta(monkeypatch):
    """El endpoint real con su doble de Session, para observar el orden."""
    from fastapi.testclient import TestClient

    from app.api.dependencias import get_db_auditoria, usuario_actual
    from app.db.session import get_db
    from app.main import app
    from app.services import idempotencia as modulo_idempotencia
    from app.services.ingesta import ReglaDeNegocioViolada, ResultadoIngesta
    from tests.conftest import principal_de_prueba
    from tests.test_ingestion_api import SesionFalsa

    sesion = SesionFalsa()
    auditoria_aparte = SesionDeAuditoria()

    app.dependency_overrides[get_db] = lambda: sesion
    app.dependency_overrides[get_db_auditoria] = lambda: auditoria_aparte
    app.dependency_overrides[usuario_actual] = principal_de_prueba

    def instalar(comportamiento):
        def falso(s, e):
            if isinstance(comportamiento, BaseException):
                raise comportamiento
            return comportamiento

        monkeypatch.setattr(modulo_idempotencia, "registrar_sesion", falso)

    cliente = TestClient(app, headers={"Idempotency-Key": "clave-auditoria-01"})
    try:
        yield cliente, sesion, auditoria_aparte, instalar, ResultadoIngesta, ReglaDeNegocioViolada
    finally:
        app.dependency_overrides.clear()


def test_la_creacion_se_audita_antes_del_commit(ingesta):
    """Dentro de la transaccion del paquete, y antes de confirmarla."""
    cliente, sesion, _, instalar, Resultado, _ = ingesta
    instalar(Resultado(id_sesion=900, ids_lectura=(1301,)))
    from tests.test_ingestion_schemas import paquete

    respuesta = cliente.post("/api/v1/sesiones-monitoreo", json=paquete())

    assert respuesta.status_code == 201
    assert sesion.pasos.index("add") < sesion.pasos.index("commit")
    assert len(sesion.agregados) == 1
    assert sesion.agregados[0].accion == "SESION_MONITOREO_REGISTRADA"


def test_la_auditoria_de_exito_apunta_a_la_sesion_creada(ingesta):
    cliente, sesion, _, instalar, Resultado, _ = ingesta
    instalar(Resultado(id_sesion=907, ids_lectura=(1400,)))
    from tests.test_ingestion_schemas import paquete

    cliente.post("/api/v1/sesiones-monitoreo", json=paquete())

    entrada = sesion.agregados[0]
    assert entrada.nombre_entidad_afectada == ENTIDAD_SESION_MONITOREO
    assert entrada.id_entidad_afectada == "907"


def test_un_rollback_de_negocio_no_deja_auditoria_de_exito(ingesta):
    """La prueba central del ticket.

    La entrada vive en la misma transaccion, asi que el rollback se la lleva. No
    hace falta una compensacion ni un borrado: no hay una segunda transaccion
    donde el exito pudiera sobrevivir.
    """
    cliente, sesion, aparte, instalar, _, ReglaViolada = ingesta
    instalar(ReglaViolada("regla simulada"))
    from tests.test_ingestion_schemas import paquete

    respuesta = cliente.post("/api/v1/sesiones-monitoreo", json=paquete())

    assert respuesta.status_code == 422
    assert sesion.commits == 0
    assert sesion.rollbacks == 1
    assert sesion.agregados == []
    assert aparte.agregados == []


def test_un_replay_no_genera_auditoria(ingesta):
    """No se creo ninguna fila de negocio, y ese camino revierte por contrato."""
    cliente, sesion, aparte, instalar, Resultado, _ = ingesta
    from types import SimpleNamespace

    from app.services.idempotencia import huella_del_paquete
    from app.schemas.monitoreo import SesionMonitoreoEntrada
    from tests.test_ingestion_schemas import paquete

    cuerpo = paquete()
    huella = huella_del_paquete(SesionMonitoreoEntrada.model_validate(cuerpo))
    sesion.reclamaciones = [
        SimpleNamespace(huella=huella, id_sesion=900, ids_lectura=[1301])
    ]

    respuesta = cliente.post("/api/v1/sesiones-monitoreo", json=cuerpo)

    assert respuesta.status_code == 201
    assert respuesta.headers["Idempotency-Replayed"] == "true"
    assert sesion.agregados == []
    assert aparte.agregados == []
    assert sesion.commits == 0


def test_la_auditoria_de_la_ingesta_no_usa_una_transaccion_aparte(ingesta):
    """Si la usara, el rollback del negocio no podria arrastrarla."""
    cliente, sesion, aparte, instalar, Resultado, _ = ingesta
    instalar(Resultado(id_sesion=900, ids_lectura=(1301,)))
    from tests.test_ingestion_schemas import paquete

    cliente.post("/api/v1/sesiones-monitoreo", json=paquete())

    assert aparte.agregados == []
    assert aparte.commits == 0


def test_no_hay_commits_intermedios(ingesta):
    """Un solo commit en todo el flujo: el del paquete."""
    cliente, sesion, _, instalar, Resultado, _ = ingesta
    instalar(Resultado(id_sesion=900, ids_lectura=(1301,)))
    from tests.test_ingestion_schemas import paquete

    cliente.post("/api/v1/sesiones-monitoreo", json=paquete())

    assert sesion.pasos.count("commit") == 1
    assert sesion.pasos[-1] == "commit"


# ---------------------------------------------------------------------------
# 6. Nada prohibido llega a una fila
# ---------------------------------------------------------------------------

PROHIBIDOS = (
    "password",
    "argon2",
    "Bearer",
    "Authorization",
    "eyJ",
    "hr_valor",
    "spo2_valor",
    "cedula",
    "telefono",
    "@example.com",
    "Idempotency-Key",
    "postgresql://",
    "SELECT",
)


def test_ninguna_fila_lleva_datos_prohibidos(ingesta):
    cliente, sesion, _, instalar, Resultado, _ = ingesta
    instalar(Resultado(id_sesion=900, ids_lectura=(1301,)))
    from tests.test_ingestion_schemas import paquete

    cliente.post(
        "/api/v1/sesiones-monitoreo",
        json=paquete(),
        headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.abc.def"},
    )

    entrada = sesion.agregados[0]
    texto = " ".join(
        str(valor)
        for valor in (
            entrada.id_usuario,
            entrada.accion,
            entrada.nombre_entidad_afectada,
            entrada.id_entidad_afectada,
            entrada.ip_origen,
        )
    )

    for prohibido in PROHIBIDOS:
        assert prohibido not in texto


def test_la_clave_de_idempotencia_no_llega_a_la_fila(ingesta):
    cliente, sesion, _, instalar, Resultado, _ = ingesta
    instalar(Resultado(id_sesion=900, ids_lectura=(1301,)))
    from tests.test_ingestion_schemas import paquete

    cliente.post(
        "/api/v1/sesiones-monitoreo",
        json=paquete(),
        headers={"Idempotency-Key": "clave-que-no-debe-guardarse"},
    )

    entrada = sesion.agregados[0]
    assert entrada.id_entidad_afectada != "clave-que-no-debe-guardarse"
    for campo in (entrada.nombre_entidad_afectada, entrada.id_entidad_afectada):
        assert campo is None or "clave-que-no" not in str(campo)


def test_la_tabla_no_tiene_columna_para_un_payload():
    """El esquema mismo impide guardar un cuerpo clinico."""
    columnas = set(AuditoriaLog.__table__.c.keys())

    assert columnas == {
        "id_log",
        "id_usuario",
        "accion",
        "nombre_entidad_afectada",
        "id_entidad_afectada",
        "ip_origen",
        "fecha_hora",
    }
