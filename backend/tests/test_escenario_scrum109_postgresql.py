"""Carga del escenario de SCRUM-109 contra PostgreSQL real.

Se omite salvo que este definida ``SCRUM109_TEST_DATABASE_URL``, que debe
apuntar a una base **desechable** cuyo nombre empiece por ``scrum109_``, ya
migrada a head y con el dataset canonico cargado --y **sin** el aprovisionamiento
antiguo--, con la credencial de mantenimiento. Nunca cae sobre
``ALEMBIC_DATABASE_URL``: lo que aqui se ejercita escribe filas clinicas.

Cada prueba trabaja dentro de una transaccion que se revierte al final, de modo
que la base queda como estaba. La unica excepcion es la del ``--ensayo`` del
CLI, que confirma que el propio CLI revierte.

Ejemplo, contra una base desechable::

    $env:SCRUM109_TEST_DATABASE_URL = "postgresql+psycopg://<usuario>:<clave>@127.0.0.1:<puerto>/scrum109_pruebas"
    python -m pytest tests/test_escenario_scrum109_postgresql.py

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app.etl import reglas
from app.loader.dataset import ErrorDeCarga
from app.services.ingesta import semana_gestacional
from tests.test_escenario_scrum109 import _cargar

VARIABLE_DE_ENTORNO = "SCRUM109_TEST_DATABASE_URL"
PATRON_DE_BASE = re.compile(r"^scrum109_[a-z0-9_]{1,40}$")

pytestmark = pytest.mark.skipif(
    not os.environ.get(VARIABLE_DE_ENTORNO),
    reason=(
        f"Define {VARIABLE_DE_ENTORNO} apuntando a una base PostgreSQL desechable "
        "(scrum109_*), migrada y con el dataset canonico. Esta suite escribe filas clinicas."
    ),
)

AHORA = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)
TABLAS_OPERACIONALES = (
    "paciente", "telefono_paciente", "usuario", "usuario_paciente", "dispositivo", "embarazo",
    "seguimiento_clinico", "asignacion_dispositivo", "sesion_monitoreo", "lectura_biometrica",
    "clinica", "medico", "medico_clinica", "semaforo", "tiempo_gestacional", "rol",
    "embarazo_factor_riesgo", "idempotencia_solicitud", "auditoria_log",
)


@pytest.fixture(scope="module")
def cargador():
    return _cargar("cargar_escenario_scrum109")


@pytest.fixture(scope="module")
def d(cargador):
    return cargador.definicion


@pytest.fixture(scope="module")
def url() -> str:
    valor = os.environ[VARIABLE_DE_ENTORNO]
    if not PATRON_DE_BASE.fullmatch(make_url(valor).database or ""):
        pytest.fail(f"{VARIABLE_DE_ENTORNO} debe nombrar una base desechable 'scrum109_*'.")
    return valor


@pytest.fixture(scope="module")
def motor(url):
    m = create_engine(url, poolclass=NullPool, future=True)
    with m.connect() as c:
        assert c.execute(text("SELECT count(*) FROM operacional.lectura_biometrica")).scalar() == 1180, (
            "La base de pruebas debe tener exactamente el dataset canonico."
        )
        assert not c.execute(
            text("SELECT count(*) FROM operacional.dispositivo WHERE codigo_dispositivo LIKE 'DEMO-%'")
        ).scalar(), "La base de pruebas no debe haber pasado por el aprovisionamiento antiguo."
    yield m
    m.dispose()


@pytest.fixture
def conexion(motor):
    with motor.connect() as c:
        transaccion = c.begin()
        try:
            yield c
        finally:
            transaccion.rollback()


@pytest.fixture(scope="module")
def revision(motor, cargador):
    with motor.connect() as c:
        return cargador.preflight(c)


@pytest.fixture(scope="module")
def clave(cargador):
    return cargador.escenario_demo.password_simulada()


def _cargar_escenario(cargador, conexion, revision, clave, corte_texto=None):
    corte = cargador.interpretar_corte(corte_texto, AHORA)
    return cargador.cargar_escenario(conexion, corte=corte, revision=revision, clave=clave)


def _huella(conexion) -> dict[str, Counter]:
    return {
        t: Counter(conexion.execute(text(f"SELECT md5(x::text) FROM operacional.{t} x")).scalars())
        for t in TABLAS_OPERACIONALES
    }


def _secuencias(conexion) -> dict[str, str]:
    return dict(conexion.execute(text(
        "SELECT schemaname || '.' || sequencename, coalesce(last_value::text, '-') FROM pg_sequences"
    )).all())


def _filas_del_escenario(conexion, d) -> int:
    total = 0
    for tabla, (columna, (a, b)) in d.BLOQUES.items():
        total += conexion.execute(
            text(f"SELECT count(*) FROM operacional.{tabla} WHERE {columna} BETWEEN :a AND :b"), {"a": a, "b": b}
        ).scalar()
    return total


# ---------------------------------------------------------------------------
# Primera carga, reejecucion y corte
# ---------------------------------------------------------------------------


def test_primera_carga_y_reejecucion_identica(cargador, conexion, revision, clave):
    primera = _cargar_escenario(cargador, conexion, revision, clave, "2026-11-18")
    assert primera.insertadas == primera.esperadas
    assert all(primera.presentes[t] == n for t, n in primera.esperadas.items())
    segunda = _cargar_escenario(cargador, conexion, revision, clave, "2026-11-18")
    assert sum(segunda.insertadas.values()) == 0
    assert segunda.existentes == primera.esperadas


def test_avanzar_el_corte_inserta_solo_lo_pendiente(cargador, conexion, revision, clave):
    antes = _cargar_escenario(cargador, conexion, revision, clave, "2026-10-10")
    despues = _cargar_escenario(cargador, conexion, revision, clave, "2026-11-18")
    pendientes = despues.esperadas["lectura_biometrica"] - antes.esperadas["lectura_biometrica"]
    assert pendientes > 0
    assert despues.insertadas["lectura_biometrica"] == pendientes
    assert despues.insertadas["sesion_monitoreo"] == pendientes
    assert despues.insertadas["paciente"] == 0


def test_un_corte_anterior_se_rechaza_sin_borrar(cargador, conexion, revision, clave, d):
    _cargar_escenario(cargador, conexion, revision, clave, "2026-11-18")
    filas = _filas_del_escenario(conexion, d)
    punto = conexion.begin_nested()
    with pytest.raises(cargador.ErrorDeCorte, match="restaura un respaldo"):
        _cargar_escenario(cargador, conexion, revision, clave, "2026-10-10")
    punto.rollback()
    assert _filas_del_escenario(conexion, d) == filas


def test_no_hay_observaciones_futuras_respecto_al_corte(cargador, conexion, revision, clave):
    resultado = _cargar_escenario(cargador, conexion, revision, clave, "2026-10-10")
    maximo = conexion.execute(text(
        "SELECT max(fecha_hora_captura) FROM operacional.lectura_biometrica WHERE id_lectura BETWEEN 10900001 AND 10999999"
    )).scalar()
    assert maximo <= resultado.corte


# ---------------------------------------------------------------------------
# Preservacion del canonico, integridad y secuencias
# ---------------------------------------------------------------------------


def test_el_dataset_canonico_queda_intacto_fila_por_fila(cargador, conexion, revision, clave):
    antes = _huella(conexion)
    resultado = _cargar_escenario(cargador, conexion, revision, clave, "2026-11-18")
    despues = _huella(conexion)
    for tabla in TABLAS_OPERACIONALES:
        assert not antes[tabla] - despues[tabla], f"{tabla}: una fila previa cambio o desaparecio"
        nuevas = sum((despues[tabla] - antes[tabla]).values())
        assert nuevas == resultado.esperadas.get(tabla, 0), tabla


def test_las_secuencias_quedan_detras_de_los_bloques(cargador, conexion, revision, clave, d):
    _cargar_escenario(cargador, conexion, revision, clave, "2026-10-10")
    for tabla, (columna, (_, ultimo)) in d.BLOQUES.items():
        modelo = cargador.tabla_operacional(tabla)
        secuencia = cargador.citar_secuencia(conexion, *cargador.secuencia_de_tabla(conexion, modelo, modelo.c[columna]))
        assert cargador.proximo_valor(*cargador.leer_estado_de_secuencia(conexion, secuencia)) > ultimo, tabla


def test_integridad_de_relaciones(cargador, conexion, revision, clave):
    _cargar_escenario(cargador, conexion, revision, clave, "2026-11-18")
    sin_cubrir = conexion.execute(text(
        "SELECT count(*) FROM operacional.sesion_monitoreo s"
        " WHERE s.id_sesion BETWEEN 10900001 AND 10999999 AND NOT EXISTS ("
        "   SELECT 1 FROM operacional.asignacion_dispositivo a"
        "    WHERE a.id_embarazo = s.id_embarazo AND a.id_dispositivo = s.id_dispositivo"
        "      AND a.fecha_inicio <= (s.fecha_inicio AT TIME ZONE 'America/Panama')::date"
        "      AND (a.fecha_fin IS NULL OR a.fecha_fin >= (s.fecha_inicio AT TIME ZONE 'America/Panama')::date))"
    )).scalar()
    assert sin_cubrir == 0
    episodio = cargador.provisionar_demo.localizar_episodio_demo(conexion)
    assert (episodio["id_embarazo"], episodio["id_usuario"]) == (10902, 10901)
    assert episodio["asignacion_activa"] and episodio["estado_embarazo"] == "ACTIVO"
    # El trigger diferido cuenta/vinculo se evalua al confirmar: se fuerza aqui.
    conexion.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


# ---------------------------------------------------------------------------
# Clinica y medico de Darien: se resuelven en la base, nunca se adivinan
# ---------------------------------------------------------------------------


def test_el_escenario_queda_en_la_clinica_de_darien_con_su_medico_afiliado(cargador, conexion, revision, clave, d):
    resultado = _cargar_escenario(cargador, conexion, revision, clave, "2026-11-18")
    clinica, provincia = conexion.execute(text(
        "SELECT id_clinica, provincia FROM operacional.clinica WHERE id_clinica = :c"
    ), {"c": resultado.referencias.id_clinica}).one()
    assert provincia == "Darién"
    # En el dataset canonico, el medico de Darien es el de la cuenta medico05.
    correo = conexion.execute(text(
        "SELECT u.email FROM operacional.usuario_medico um JOIN operacional.usuario u USING (id_usuario)"
        " WHERE um.id_medico = :m"
    ), {"m": resultado.referencias.id_medico}).scalar_one()
    assert correo == "medico05@example.com"
    for tabla in ("embarazo", "dispositivo"):
        fuera = conexion.execute(text(
            f"SELECT count(*) FROM operacional.{tabla} WHERE id_{tabla} BETWEEN 10901 AND 10999"
            " AND id_clinica <> :c"
        ), {"c": clinica}).scalar()
        assert fuera == 0, tabla
    medicos = set(conexion.execute(text(
        "SELECT id_medico FROM operacional.seguimiento_clinico WHERE id_seguimiento BETWEEN 10901 AND 10999"
    )).scalars())
    assert medicos == {resultado.referencias.id_medico}
    cedulas = conexion.execute(text(
        "SELECT cedula FROM operacional.paciente WHERE id_paciente BETWEEN 10901 AND 10999"
    )).scalars().all()
    assert len(cedulas) == len(d.PACIENTES) and all(c.startswith("5-") for c in cedulas)
    # La sexta paciente sigue sin cuenta ni embarazo: no hay relacion con la
    # clinica que inventarle.
    assert not conexion.execute(text(
        "SELECT count(*) FROM operacional.embarazo WHERE id_paciente = 10906"
    )).scalar()
    assert not conexion.execute(text(
        "SELECT count(*) FROM operacional.usuario_paciente WHERE id_paciente = 10906"
    )).scalar()


def test_dos_medicos_posibles_en_darien_se_rechaza_sin_elegir(cargador, conexion, revision, clave, d):
    # Otro medico con cuenta pasa a estar afiliado solo a Darien.
    conexion.execute(text("UPDATE operacional.medico_clinica SET id_clinica = 102 WHERE id_medico = 100"))
    punto = conexion.begin_nested()
    with pytest.raises(cargador.ErrorDeEscenario, match="exactamente un medico"):
        _cargar_escenario(cargador, conexion, revision, clave, "2026-10-10")
    punto.rollback()
    assert _filas_del_escenario(conexion, d) == 0


def test_sin_medico_que_cubra_todo_el_escenario_se_rechaza(cargador, conexion, revision, clave, d):
    # La afiliacion de Darien empieza despues del primer embarazo (2025-01-13).
    conexion.execute(text("UPDATE operacional.medico_clinica SET fecha_inicio = '2025-06-01' WHERE id_clinica = 102"))
    punto = conexion.begin_nested()
    with pytest.raises(cargador.ErrorDeEscenario, match="hay 0"):
        _cargar_escenario(cargador, conexion, revision, clave, "2026-10-10")
    punto.rollback()
    assert _filas_del_escenario(conexion, d) == 0


def test_semaforos_y_semanas_coinciden_con_las_reglas_del_etl(cargador, conexion, revision, clave):
    _cargar_escenario(cargador, conexion, revision, clave, "2026-11-18")
    filas = conexion.execute(text(
        "SELECT l.id_lectura, l.hr_valor, l.spo2_valor, l.mov_valor, l.fecha_hora_captura, t.semana_gestacion,"
        "       t.trimestre, s.codigo_nivel, e.fecha_inicio"
        "  FROM operacional.lectura_biometrica l"
        "  JOIN operacional.sesion_monitoreo se USING (id_sesion)"
        "  JOIN operacional.embarazo e USING (id_embarazo)"
        "  JOIN operacional.tiempo_gestacional t USING (id_tiempo_gest)"
        "  JOIN operacional.semaforo s USING (id_semaforo)"
        " WHERE l.id_lectura BETWEEN 10900001 AND 10999999"
    )).all()
    assert filas
    for f in filas:
        assert f.semana_gestacion == semana_gestacional(f.fecha_inicio, f.fecha_hora_captura.astimezone(cargador.definicion.ZONA))
        estados = reglas.clasificar_lectura(
            hr_valor=f.hr_valor, spo2_valor=f.spo2_valor, mov_valor=f.mov_valor,
            semana=f.semana_gestacion, trimestre=f.trimestre,
        )
        assert estados.codigo_global == f.codigo_nivel, f.id_lectura
    assert {f.codigo_nivel for f in filas} == {"OK", "WARNING"}


def test_las_cuentas_verifican_la_clave_simulada_y_se_reutilizan(cargador, conexion, revision, clave):
    _cargar_escenario(cargador, conexion, revision, clave, "2026-10-10")
    hashes = dict(conexion.execute(text(
        "SELECT id_usuario, password_hash FROM operacional.usuario WHERE id_usuario BETWEEN 10901 AND 10999"
    )).all())
    assert len(hashes) == 5
    assert all(cargador.passwords.verificar(h, clave) for h in hashes.values())
    _cargar_escenario(cargador, conexion, revision, clave, "2026-10-10")
    assert dict(conexion.execute(text(
        "SELECT id_usuario, password_hash FROM operacional.usuario WHERE id_usuario BETWEEN 10901 AND 10999"
    )).all()) == hashes


# ---------------------------------------------------------------------------
# Conflictos y rollback
# ---------------------------------------------------------------------------


def test_un_fallo_a_mitad_revierte_todo(cargador, conexion, revision, clave, d, monkeypatch):
    antes, secuencias = _huella(conexion), _secuencias(conexion)

    def fallar(_conexion):
        raise RuntimeError("fallo inyectado despues de insertar")

    monkeypatch.setattr(cargador, "reservar_secuencias", fallar)
    punto = conexion.begin_nested()
    with pytest.raises(RuntimeError, match="fallo inyectado"):
        _cargar_escenario(cargador, conexion, revision, clave, "2026-11-18")
    punto.rollback()
    assert _huella(conexion) == antes
    assert _secuencias(conexion) == secuencias
    assert _filas_del_escenario(conexion, d) == 0


def test_misma_llave_con_otro_contenido_aborta_sin_sobrescribir(cargador, conexion, revision, clave):
    conexion.execute(text(
        "INSERT INTO operacional.paciente (id_paciente, cedula, primer_nombre, apellido_paterno, email_pac, fecha_nac)"
        " VALUES (10901, '9-999-9999', 'Otra', 'Persona', 'otra.persona@example.com', '1990-01-01')"
    ))
    punto = conexion.begin_nested()
    with pytest.raises(ErrorDeCarga, match="paciente"):
        _cargar_escenario(cargador, conexion, revision, clave, "2026-10-10")
    punto.rollback()
    assert conexion.execute(text("SELECT primer_nombre FROM operacional.paciente WHERE id_paciente = 10901")).scalar() == "Otra"


def test_el_aprovisionamiento_antiguo_se_detecta_y_no_se_reasigna(cargador, conexion, revision, clave):
    antiguo = cargador.provisionar_demo._provisionar(conexion, date(2026, 10, 22))
    punto = conexion.begin_nested()
    with pytest.raises(cargador.ConflictoDeEscenario, match="aprovisionamiento antiguo"):
        _cargar_escenario(cargador, conexion, revision, clave, "2026-10-10")
    punto.rollback()
    asignado = conexion.execute(text(
        "SELECT ad.id_embarazo FROM operacional.asignacion_dispositivo ad"
        "  JOIN operacional.dispositivo d USING (id_dispositivo) WHERE d.codigo_dispositivo = 'DEMO-SCRUM72-01'"
    )).scalars().all()
    assert asignado == [antiguo["id_embarazo"]]


def test_una_fila_ajena_en_el_bloque_reservado_se_rechaza(cargador, conexion, revision, clave):
    conexion.execute(text(
        "INSERT INTO operacional.sesion_monitoreo (id_sesion, id_embarazo, id_dispositivo, tipo_sesion, fecha_inicio)"
        " VALUES (10999000, 100, 100, 'SIGNOS_MATERNOS', '2025-03-01T10:00:00-05:00')"
    ))
    punto = conexion.begin_nested()
    with pytest.raises(cargador.ConflictoDeEscenario, match="bloque reservado"):
        _cargar_escenario(cargador, conexion, revision, clave, "2026-10-10")
    punto.rollback()


def test_un_embarazo_ajeno_de_la_protagonista_se_rechaza(cargador, conexion, revision, clave):
    _cargar_escenario(cargador, conexion, revision, clave, "2026-10-10")
    conexion.execute(text(
        "INSERT INTO operacional.embarazo (id_paciente, id_clinica, numero_gestas, numero_partos, fecha_inicio,"
        " fecha_probable_parto, estado_embarazo) VALUES (10901, 100, 3, 2, '2026-10-01', '2027-07-08', 'SUSPENDIDO')"
    ))
    punto = conexion.begin_nested()
    with pytest.raises(cargador.ConflictoDeEscenario, match="embarazo"):
        _cargar_escenario(cargador, conexion, revision, clave, "2026-10-10")
    punto.rollback()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_el_ensayo_del_cli_no_deja_rastro(cargador, motor, url, d, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", url)
    monkeypatch.setenv("GESTANTE_PROVISION_PATH", str(tmp_path / "provision.json"))
    with motor.connect() as c:
        antes = _huella(c), _secuencias(c)
    assert cargador.main(["cargar", "--ensayo", "--corte", "2026-10-10"]) == cargador.CODIGO_DE_EXITO
    assert "ENSAYO" in capsys.readouterr().out
    with motor.connect() as c:
        assert (_huella(c), _secuencias(c)) == antes
        assert _filas_del_escenario(c, d) == 0
    assert not list(tmp_path.iterdir())
