"""Ciclo real del endpoint de ingesta contra un servidor PostgreSQL 16 (SCRUM-62).

Se omite salvo que esté definida ``SCRUM62_TEST_DATABASE_URL``. Nunca cae por
omisión sobre ``DATABASE_URL``: una prueba que escribe no puede terminar sobre
la base de desarrollo de alguien por haberse olvidado de exportar una variable.
El workflow de CI convierte la omisión en un job en rojo.

**Aislamiento.** No se ejecuta una sola sentencia DDL: no se migra, no se crea,
no se borra y no se trunca. Cada prueba abre una transacción exterior y la
revierte pase lo que pase, y la ``Session`` que atiende la petición se une a esa
transacción con ``join_transaction_mode="create_savepoint"``.

Ese modo no es un detalle: es lo que hace posible la prueba. Con el modo por
omisión (``conditional_savepoint``) una Session unida a una conexión que ya
tiene transacción cae en ``rollback_only``, y entonces el ``rollback()`` del
endpoint arrastraría también la transacción exterior, borrando las filas de
referencia que la fixture acaba de crear; no habría forma de distinguir "revirtió
el paquete" de "revirtió todo". Con ``create_savepoint`` la Session abre un
SAVEPOINT propio: el ``commit()`` del endpoint lo libera —así que el commit que
se prueba es real y observable— y su ``rollback()`` vuelve al savepoint sin
tocar lo de fuera. Hay una prueba dedicada a dejar constancia de ello.

**Requisito previo.** La base ya debe estar desplegada en el ``head`` de
Alembic; estas pruebas lo comprueban y fallan pidiendo ``alembic upgrade head``,
pero no migran por su cuenta.

**La colisión de llave primaria (escenario E).** Ni ``sesion_monitoreo`` ni
``lectura_biometrica`` tienen un UNIQUE fuera de su PK, y el cliente no envía
identificadores, así que la única forma de provocar un choque real es adelantar
la secuencia de ``id_sesion`` para que el siguiente ``nextval`` devuelva un valor
ya ocupado. Eso hace el escenario E, con tres salvaguardas: la secuencia se
descubre desde el catálogo con ``pg_get_serial_sequence`` en lugar de suponer su
nombre; su estado se fotografía antes y se restaura después con ``setval`` en un
``try/finally`` que falla explícitamente si la restauración no funciona; y esa
restauración corre con ``lock_timeout``, para que un problema de aislamiento se
manifieste como un error y nunca como un bloqueo indefinido.

Ejecución desde ``backend/``::

    $env:SCRUM62_TEST_DATABASE_URL = "postgresql+psycopg://<usuario>:<clave>@127.0.0.1:<puerto>/<base>"
    .\\.venv\\Scripts\\python.exe -m pytest tests/test_ingestion_api_postgresql.py -v

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.main import app
from app.models.catalogos import Semaforo, TiempoGestacional
from app.models.clinico import Clinica, Embarazo, Paciente
from app.models.enums import CodigoSemaforo, EstadoSesion, OrigenDato, TipoSesion
from app.models.monitoreo import Dispositivo, LecturaBiometrica, SesionMonitoreo
from app.models.seguridad import AuditoriaLog
from app.services.errores import MENSAJE_CONFLICTO

VARIABLE_DE_ENTORNO = "SCRUM62_TEST_DATABASE_URL"
MOTOR_REQUERIDO = "postgresql"
RUTA = "/api/v1/sesiones-monitoreo"

pytestmark = pytest.mark.skipif(
    not os.environ.get(VARIABLE_DE_ENTORNO),
    reason=(
        f"Define {VARIABLE_DE_ENTORNO} apuntando a una base PostgreSQL ya migrada "
        "a head para ejecutar la validación del endpoint de ingesta."
    ),
)

# Datos ficticios de esta suite. Los valores UNIQUE llevan el prefijo del ticket
# para no poder coincidir con los del dataset simulado, y de todos modos ninguno
# sobrevive: todo ocurre dentro de una transacción que se revierte.
RUC_FICTICIO = "SCRUM62-RUC-0001"
CEDULA_FICTICIA = "SCRUM62-8-000-0001"
EMAIL_FICTICIO = "scrum62.paciente@example.invalid"
CODIGO_DISPOSITIVO_FICTICIO = "SCRUM62-DISP-0001"

# Semanas gestacionales usadas por las pruebas: una por encima del umbral de
# movimiento y otra por debajo.
SEMANA_CON_MOVIMIENTO = 41
SEMANA_SIN_MOVIMIENTO = 12

# Identificador que no puede existir; cabe en un INTEGER de PostgreSQL.
ID_INEXISTENTE = 2_000_000_000

INICIO = datetime(2026, 3, 1, 14, 0, tzinfo=timezone.utc)
FIN = INICIO + timedelta(minutes=30)
CAPTURA = INICIO + timedelta(minutes=5)
SINCRONIZACION = INICIO + timedelta(minutes=40)

# Viola ck_lectura_biometrica_spo2_rango. El contrato Pydantic lo deja pasar a
# propósito: el rango 0-100 es autoridad de PostgreSQL.
SPO2_FUERA_DE_RANGO = 150

# Fragmentos que jamás deben aparecer en una respuesta.
FRAGMENTOS_PROHIBIDOS = (
    "postgresql+psycopg://",
    "psycopg",
    "INSERT INTO",
    "SELECT",
    "Traceback",
    "sqlalchemy",
    "spo2_rango",
)


# ---------------------------------------------------------------------------
# Conexión: se exige el entorno, no se prepara
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def url_de_pruebas() -> str:
    """URL de la base de pruebas, comprobada sin abrir ninguna conexión.

    La comprobación se hace sobre la URL para que un destino equivocado se
    rechace antes de que exista un engine capaz de crear algo. La URL nunca se
    imprime: lleva credenciales.
    """
    url = os.environ[VARIABLE_DE_ENTORNO]
    try:
        motor = make_url(url).get_backend_name()
    except ArgumentError:
        pytest.fail(f"{VARIABLE_DE_ENTORNO} no es una URL de conexión válida.")
    if motor != MOTOR_REQUERIDO:
        pytest.fail(
            f"{VARIABLE_DE_ENTORNO} apunta a un motor '{motor}'. Estas pruebas "
            "solo se ejecutan contra PostgreSQL."
        )
    return url


@pytest.fixture(scope="session")
def engine_de_pruebas(url_de_pruebas):
    """Engine sobre una base que ya debe estar migrada.

    No ejecuta ``alembic upgrade head``: preparar la base es responsabilidad de
    quien lanza las pruebas, y en CI lo hace el paso de SCRUM-52 que corre antes.
    """
    engine = create_engine(url_de_pruebas)
    if not inspect(engine).has_table(
        SesionMonitoreo.__tablename__, schema=SesionMonitoreo.__table__.schema
    ):
        engine.dispose()
        # Sin URL en el mensaje: lleva credenciales.
        pytest.fail(
            f"La base indicada por {VARIABLE_DE_ENTORNO} no tiene el esquema "
            "operacional desplegado. Ejecuta 'alembic upgrade head' desde "
            "backend/ antes de correr estas pruebas."
        )
    yield engine
    engine.dispose()


@pytest.fixture
def conexion_revertida(engine_de_pruebas):
    """Conexión cuya transacción exterior siempre se revierte."""
    conexion = engine_de_pruebas.connect()
    transaccion = conexion.begin()
    try:
        yield conexion
    finally:
        transaccion.rollback()
        conexion.close()


@pytest.fixture
def sesion_de_pruebas(conexion_revertida):
    """La Session que atenderá la petición, unida por SAVEPOINT.

    ``join_transaction_mode="create_savepoint"`` es explícito y necesario; el
    docstring del módulo explica por qué el valor por omisión no sirve aquí.
    """
    sesion = Session(
        bind=conexion_revertida, join_transaction_mode="create_savepoint"
    )
    try:
        yield sesion
    finally:
        sesion.close()


# ---------------------------------------------------------------------------
# Referencias preexistentes: el endpoint no crea ninguna
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Referencias:
    """Filas ficticias que el paquete de ingesta podrá referenciar."""

    id_clinica: int
    id_paciente: int
    id_embarazo: int
    id_dispositivo: int
    id_tiempo_con_movimiento: int
    id_tiempo_sin_movimiento: int
    id_semaforo: int


def _asegurar_tiempo_gestacional(conexion, semana: int, mes: int, trimestre: int) -> int:
    """Id de la semana pedida, reutilizando la fila si el catálogo ya la trae.

    ``semana_gestacion`` es UNIQUE, así que insertar a ciegas fallaría en una
    base que ya tenga cargado el dataset simulado. Reutilizar o crear deja la
    suite corriendo igual sobre una base vacía de CI que sobre una de desarrollo.
    """
    existente = conexion.execute(
        select(TiempoGestacional.id_tiempo_gest).where(
            TiempoGestacional.semana_gestacion == semana
        )
    ).scalar_one_or_none()
    if existente is not None:
        return existente

    return conexion.execute(
        insert(TiempoGestacional)
        .values(
            semana_gestacion=semana,
            mes_gestacion=mes,
            trimestre=trimestre,
            grupo_clinico=f"TRIMESTRE_{trimestre}",
            descripcion=f"Semana gestacional {semana} (SCRUM-62, simulada)",
        )
        .returning(TiempoGestacional.id_tiempo_gest)
    ).scalar_one()


def _asegurar_semaforo(conexion) -> int:
    """Id de cualquier nivel del semáforo, creando uno si el catálogo está vacío.

    El endpoint no clasifica: recibe ``id_semaforo`` ya decidido y solo verifica
    que exista. Cuál de los tres niveles sea da igual para estas pruebas.
    """
    existente = conexion.execute(
        select(Semaforo.id_semaforo).order_by(Semaforo.id_semaforo).limit(1)
    ).scalar_one_or_none()
    if existente is not None:
        return existente

    return conexion.execute(
        insert(Semaforo)
        .values(
            codigo_nivel=CodigoSemaforo.OK,
            etiqueta_visual="Normal",
            color_hex="#008000",
            prioridad=1,
            mensaje_app="Lectura dentro del rango esperado.",
            version_referencia="SCRUM62-SIM",
        )
        .returning(Semaforo.id_semaforo)
    ).scalar_one()


@pytest.fixture
def referencias(conexion_revertida) -> Referencias:
    """Crea el mínimo de filas ficticias que un paquete necesita referenciar.

    Los identificadores no se fijan a mano: se dejan a las secuencias de
    PostgreSQL y se leen con ``RETURNING``, igual que hará el endpoint. Todo
    desaparece con el rollback de la transacción exterior.
    """
    id_clinica = conexion_revertida.execute(
        insert(Clinica)
        .values(
            nombre_clinica="Clínica Rural Simulada SCRUM-62",
            ruc=RUC_FICTICIO,
            provincia="Chiriquí",
            distrito="Renacimiento",
            corregimiento="Plaza Caisán",
            direccion_fisica="Dirección simulada sin correspondencia real",
        )
        .returning(Clinica.id_clinica)
    ).scalar_one()

    id_paciente = conexion_revertida.execute(
        insert(Paciente)
        .values(
            cedula=CEDULA_FICTICIA,
            primer_nombre="Gestante",
            apellido_paterno="Simulada",
            email_pac=EMAIL_FICTICIO,
            fecha_nac=date(1998, 5, 14),
        )
        .returning(Paciente.id_paciente)
    ).scalar_one()

    id_embarazo = conexion_revertida.execute(
        insert(Embarazo)
        .values(
            id_paciente=id_paciente,
            id_clinica=id_clinica,
            numero_gestas=2,
            numero_partos=1,
            fecha_inicio=date(2025, 8, 1),
            fecha_probable_parto=date(2026, 5, 8),
        )
        .returning(Embarazo.id_embarazo)
    ).scalar_one()

    id_dispositivo = conexion_revertida.execute(
        insert(Dispositivo)
        .values(
            id_clinica=id_clinica,
            codigo_dispositivo=CODIGO_DISPOSITIVO_FICTICIO,
            modelo="FetalAlert Simulado",
            version_firmware="0.0.0-sim",
        )
        .returning(Dispositivo.id_dispositivo)
    ).scalar_one()

    return Referencias(
        id_clinica=id_clinica,
        id_paciente=id_paciente,
        id_embarazo=id_embarazo,
        id_dispositivo=id_dispositivo,
        id_tiempo_con_movimiento=_asegurar_tiempo_gestacional(
            conexion_revertida, SEMANA_CON_MOVIMIENTO, mes=10, trimestre=3
        ),
        id_tiempo_sin_movimiento=_asegurar_tiempo_gestacional(
            conexion_revertida, SEMANA_SIN_MOVIMIENTO, mes=3, trimestre=1
        ),
        id_semaforo=_asegurar_semaforo(conexion_revertida),
    )


@pytest.fixture
def cliente(sesion_de_pruebas) -> TestClient:
    app.dependency_overrides[get_db] = lambda: sesion_de_pruebas
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# La secuencia de id_sesion: se descubre, se vigila y se restaura
# ---------------------------------------------------------------------------

# Estos tres ayudantes se escriben aquí en lugar de importarse del cargador de
# SCRUM-61, que tiene equivalentes. Esta suite es deliberadamente independiente
# de aquel módulo -- solo reutiliza de él una constante --, y atarla a los
# internos de una pieza que no está probando volvería frágiles las dos.

# Segundos que la restauración espera por el bloqueo de la secuencia antes de
# rendirse. ``ALTER SEQUENCE`` toma un bloqueo exclusivo, así que si alguna vez
# la restauración corriera antes de que la transacción exterior se revierta,
# esto la hace fallar con un error legible en lugar de esperar para siempre.
ESPERA_MAXIMA_DE_BLOQUEO = "10s"


def localizar_secuencia(conexion, tabla, columna: str) -> str:
    """Nombre citado de la secuencia que respalda ``columna``, según el catálogo.

    Se pregunta a PostgreSQL con ``pg_get_serial_sequence`` en vez de componer
    ``<tabla>_<columna>_seq`` a mano: el nombre real depende de cómo se creó la
    columna, y suponerlo sería adivinar. La tabla y la columna viajan como
    parámetros; el identificador que vuelve se cita con el preparador del
    dialecto, no a mano.
    """
    preparador = conexion.dialect.identifier_preparer
    encontrada = conexion.execute(
        text(
            "SELECT n.nspname, c.relname "
            "FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.oid = pg_get_serial_sequence(:tabla, :columna)::regclass"
        ),
        {"tabla": preparador.format_table(tabla), "columna": columna},
    ).one_or_none()

    if encontrada is None:
        pytest.fail(
            f"'{tabla.name}.{columna}' no tiene una secuencia asociada; el "
            "escenario de colisión de llave primaria no puede prepararse."
        )

    esquema, nombre = encontrada
    return f"{preparador.quote_schema(esquema)}.{preparador.quote(nombre)}"


def leer_estado_de_secuencia(conexion, secuencia: str) -> tuple[int, bool]:
    """``(last_value, is_called)``, leído sin consumir la secuencia."""
    fila = conexion.execute(
        text(f"SELECT last_value, is_called FROM {secuencia}")
    ).one()
    return int(fila[0]), bool(fila[1])


@pytest.fixture
def secuencia_de_sesiones(engine_de_pruebas):
    """Secuencia de ``id_sesion``, fotografiada antes y restaurada siempre.

    Debe pedirse **antes** que cualquier fixture que abra la transacción
    exterior. pytest desmonta en orden inverso al de montaje, y así la
    restauración corre después del rollback de esa transacción, cuando el
    bloqueo exclusivo que ``ALTER SEQUENCE`` deja tomado ya se liberó.

    La restauración no se apoya en que el rollback deshaga el ``ALTER
    SEQUENCE`` -- hay una prueba dedicada a comprobar que lo hace --: pase lo
    que pase, ``setval`` devuelve el estado exacto y la fixture falla si no lo
    consigue.
    """
    tabla = SesionMonitoreo.__table__

    with engine_de_pruebas.connect() as conexion:
        secuencia = localizar_secuencia(conexion, tabla, "id_sesion")
        previo = leer_estado_de_secuencia(conexion, secuencia)

    try:
        yield secuencia, previo
    finally:
        with engine_de_pruebas.begin() as conexion:
            conexion.execute(
                text(f"SET LOCAL lock_timeout = '{ESPERA_MAXIMA_DE_BLOQUEO}'")
            )
            conexion.execute(
                text("SELECT setval(CAST(:secuencia AS regclass), :valor, :llamada)"),
                {"secuencia": secuencia, "valor": previo[0], "llamada": previo[1]},
            )
        with engine_de_pruebas.connect() as conexion:
            restaurado = leer_estado_de_secuencia(conexion, secuencia)
        if restaurado != previo:
            pytest.fail(
                f"No se pudo restaurar {secuencia}: quedó en {restaurado} y "
                f"estaba en {previo}."
            )


# ---------------------------------------------------------------------------
# Constructores de paquetes
# ---------------------------------------------------------------------------


def lectura_de_signos(referencias: Referencias, **cambios: Any) -> dict[str, Any]:
    fila: dict[str, Any] = {
        "id_tiempo_gest": referencias.id_tiempo_con_movimiento,
        "id_semaforo": referencias.id_semaforo,
        "fecha_hora_captura": CAPTURA.isoformat(),
        "fecha_hora_sincronizacion": SINCRONIZACION.isoformat(),
        "hr_valor": 88.5,
        "spo2_valor": 97,
        "mov_valor": None,
    }
    fila.update(cambios)
    return fila


def lectura_de_movimiento(referencias: Referencias, **cambios: Any) -> dict[str, Any]:
    fila: dict[str, Any] = {
        "id_tiempo_gest": referencias.id_tiempo_con_movimiento,
        "id_semaforo": referencias.id_semaforo,
        "fecha_hora_captura": CAPTURA.isoformat(),
        "fecha_hora_sincronizacion": SINCRONIZACION.isoformat(),
        "hr_valor": None,
        "spo2_valor": None,
        "mov_valor": 12,
    }
    fila.update(cambios)
    return fila


def paquete_de_signos(
    referencias: Referencias,
    *,
    lecturas: list[dict[str, Any]] | None = None,
    **cambios: Any,
) -> dict[str, Any]:
    cuerpo: dict[str, Any] = {
        "id_embarazo": referencias.id_embarazo,
        "id_dispositivo": referencias.id_dispositivo,
        "tipo_sesion": "SIGNOS_MATERNOS",
        "fecha_inicio": INICIO.isoformat(),
        "fecha_fin": FIN.isoformat(),
        "lecturas": [lectura_de_signos(referencias)] if lecturas is None else lecturas,
    }
    cuerpo.update(cambios)
    return cuerpo


def paquete_de_movimiento(
    referencias: Referencias,
    *,
    lecturas: list[dict[str, Any]] | None = None,
    **cambios: Any,
) -> dict[str, Any]:
    cuerpo: dict[str, Any] = {
        "id_embarazo": referencias.id_embarazo,
        "id_dispositivo": referencias.id_dispositivo,
        "tipo_sesion": "MOVIMIENTOS_FETALES",
        "fecha_inicio": INICIO.isoformat(),
        "fecha_fin": FIN.isoformat(),
        "lecturas": (
            [lectura_de_movimiento(referencias)] if lecturas is None else lecturas
        ),
    }
    cuerpo.update(cambios)
    return cuerpo


# ---------------------------------------------------------------------------
# Consultas de verificación, acotadas al embarazo ficticio de cada prueba
# ---------------------------------------------------------------------------


def sesiones_del_embarazo(conexion, id_embarazo: int) -> list[Any]:
    """Filas de la sesión como diccionarios, leídas por la conexión exterior.

    ``.mappings()`` y no objetos ORM: la consulta va por la conexión, no por la
    ``Session`` del endpoint, así que lo que se comprueba es lo que quedó en la
    base y no lo que un mapa de identidad recuerde. Los tipos de columna siguen
    aplicándose, de modo que un enum vuelve como enum.
    """
    return (
        conexion.execute(
            select(SesionMonitoreo).where(SesionMonitoreo.id_embarazo == id_embarazo)
        )
        .mappings()
        .all()
    )


def contar_sesiones(conexion, id_embarazo: int) -> int:
    return conexion.execute(
        select(func.count())
        .select_from(SesionMonitoreo)
        .where(SesionMonitoreo.id_embarazo == id_embarazo)
    ).scalar_one()


def lecturas_del_embarazo(conexion, id_embarazo: int) -> list[Any]:
    return (
        conexion.execute(
            select(LecturaBiometrica)
            .join(
                SesionMonitoreo,
                SesionMonitoreo.id_sesion == LecturaBiometrica.id_sesion,
            )
            .where(SesionMonitoreo.id_embarazo == id_embarazo)
            .order_by(LecturaBiometrica.id_lectura)
        )
        .mappings()
        .all()
    )


def contar_lecturas(conexion, id_embarazo: int) -> int:
    return len(lecturas_del_embarazo(conexion, id_embarazo))


def contar_auditoria(conexion) -> int:
    return conexion.execute(
        select(func.count()).select_from(AuditoriaLog)
    ).scalar_one()


# ---------------------------------------------------------------------------
# Escenario A: creación completa
# ---------------------------------------------------------------------------


def test_a_un_paquete_valido_se_crea_completo(cliente, referencias, conexion_revertida):
    respuesta = cliente.post(RUTA, json=paquete_de_signos(referencias))

    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo["lecturas_creadas"] == 1
    assert len(cuerpo["ids_lectura"]) == 1

    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 1
    assert contar_lecturas(conexion_revertida, referencias.id_embarazo) == 1


def test_a_los_identificadores_los_genera_postgresql(
    cliente, referencias, conexion_revertida
):
    """El cliente no envía PK y aun así recibe las que la base asignó."""
    respuesta = cliente.post(RUTA, json=paquete_de_signos(referencias))
    cuerpo = respuesta.json()

    sesion = sesiones_del_embarazo(conexion_revertida, referencias.id_embarazo)[0]
    lecturas = lecturas_del_embarazo(conexion_revertida, referencias.id_embarazo)

    assert cuerpo["id_sesion"] == sesion["id_sesion"]
    assert cuerpo["ids_lectura"] == [fila["id_lectura"] for fila in lecturas]
    assert all(isinstance(identificador, int) for identificador in cuerpo["ids_lectura"])


def test_a_la_sesion_persiste_los_valores_enviados(
    cliente, referencias, conexion_revertida
):
    cliente.post(RUTA, json=paquete_de_signos(referencias, estado_sesion="COMPLETADA"))

    sesion = sesiones_del_embarazo(conexion_revertida, referencias.id_embarazo)[0]

    assert sesion["id_dispositivo"] == referencias.id_dispositivo
    assert sesion["estado_sesion"] is EstadoSesion.COMPLETADA
    assert sesion["fecha_inicio"] == INICIO
    assert sesion["fecha_fin"] == FIN
    assert sesion["fecha_inicio"].tzinfo is not None


def test_a_los_valores_por_omision_los_aplica_el_modelo(
    cliente, referencias, conexion_revertida
):
    """Omitir estado y origen deja que el modelo ponga los suyos."""
    cuerpo = paquete_de_signos(referencias)
    del cuerpo["fecha_fin"]

    cliente.post(RUTA, json=cuerpo)

    sesion = sesiones_del_embarazo(conexion_revertida, referencias.id_embarazo)[0]

    assert sesion["estado_sesion"] is EstadoSesion.PENDIENTE
    assert sesion["origen_dato"] is OrigenDato.DISPOSITIVO
    assert sesion["fecha_fin"] is None


def test_a_una_metrica_que_no_aplica_queda_en_null(
    cliente, referencias, conexion_revertida
):
    """NULL, nunca cero: la ETL tiene que poder distinguirlos."""
    cliente.post(RUTA, json=paquete_de_signos(referencias))

    lectura = lecturas_del_embarazo(conexion_revertida, referencias.id_embarazo)[0]

    assert lectura["hr_valor"] == Decimal("88.50")
    assert lectura["spo2_valor"] == Decimal("97.00")
    assert lectura["mov_valor"] is None


def test_a_una_lectura_de_movimiento_deja_hr_y_spo2_en_null(
    cliente, referencias, conexion_revertida
):
    cliente.post(RUTA, json=paquete_de_movimiento(referencias))

    lectura = lecturas_del_embarazo(conexion_revertida, referencias.id_embarazo)[0]

    assert lectura["mov_valor"] == 12
    assert lectura["hr_valor"] is None
    assert lectura["spo2_valor"] is None


def test_a_el_endpoint_no_crea_catalogos_como_efecto_secundario(
    cliente, referencias, conexion_revertida
):
    tiempos_antes = conexion_revertida.execute(
        select(func.count()).select_from(TiempoGestacional)
    ).scalar_one()
    semaforos_antes = conexion_revertida.execute(
        select(func.count()).select_from(Semaforo)
    ).scalar_one()

    cliente.post(RUTA, json=paquete_de_signos(referencias))

    assert (
        conexion_revertida.execute(
            select(func.count()).select_from(TiempoGestacional)
        ).scalar_one()
        == tiempos_antes
    )
    assert (
        conexion_revertida.execute(
            select(func.count()).select_from(Semaforo)
        ).scalar_one()
        == semaforos_antes
    )


def test_a_auditoria_log_no_recibe_ninguna_fila(cliente, referencias, conexion_revertida):
    antes = contar_auditoria(conexion_revertida)

    cliente.post(RUTA, json=paquete_de_signos(referencias))

    assert contar_auditoria(conexion_revertida) == antes


# ---------------------------------------------------------------------------
# Escenario B: varias lecturas en una misma sesión
# ---------------------------------------------------------------------------


def test_b_cinco_lecturas_se_persisten_todas(cliente, referencias, conexion_revertida):
    respuesta = cliente.post(
        RUTA,
        json=paquete_de_signos(
            referencias, lecturas=[lectura_de_signos(referencias) for _ in range(5)]
        ),
    )

    assert respuesta.status_code == 201
    assert respuesta.json()["lecturas_creadas"] == 5
    assert contar_lecturas(conexion_revertida, referencias.id_embarazo) == 5


def test_b_todas_las_lecturas_apuntan_a_la_misma_sesion(
    cliente, referencias, conexion_revertida
):
    """Evidencia directa del 1:N: no hay 1:1 accidental."""
    respuesta = cliente.post(
        RUTA,
        json=paquete_de_signos(
            referencias, lecturas=[lectura_de_signos(referencias) for _ in range(5)]
        ),
    )
    id_sesion = respuesta.json()["id_sesion"]

    lecturas = lecturas_del_embarazo(conexion_revertida, referencias.id_embarazo)

    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 1
    assert {lectura["id_sesion"] for lectura in lecturas} == {id_sesion}
    assert len({lectura["id_lectura"] for lectura in lecturas}) == 5


def test_b_los_identificadores_devueltos_coinciden_con_los_persistidos(
    cliente, referencias, conexion_revertida
):
    respuesta = cliente.post(
        RUTA,
        json=paquete_de_signos(
            referencias, lecturas=[lectura_de_signos(referencias) for _ in range(3)]
        ),
    )

    persistidos = [
        fila["id_lectura"]
        for fila in lecturas_del_embarazo(conexion_revertida, referencias.id_embarazo)
    ]

    assert respuesta.json()["ids_lectura"] == persistidos


# ---------------------------------------------------------------------------
# Escenario C: referencia inexistente
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "campo", ["id_embarazo", "id_dispositivo"]
)
def test_c_una_referencia_de_sesion_inexistente_devuelve_404(
    cliente, referencias, conexion_revertida, campo
):
    respuesta = cliente.post(
        RUTA, json=paquete_de_signos(referencias, **{campo: ID_INEXISTENTE})
    )

    assert respuesta.status_code == 404
    assert str(ID_INEXISTENTE) in respuesta.json()["detail"]
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 0
    assert contar_lecturas(conexion_revertida, referencias.id_embarazo) == 0


@pytest.mark.parametrize("campo", ["id_tiempo_gest", "id_semaforo"])
def test_c_una_referencia_de_lectura_inexistente_devuelve_404(
    cliente, referencias, conexion_revertida, campo
):
    respuesta = cliente.post(
        RUTA,
        json=paquete_de_signos(
            referencias,
            lecturas=[lectura_de_signos(referencias, **{campo: ID_INEXISTENTE})],
        ),
    )

    assert respuesta.status_code == 404
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 0
    assert contar_lecturas(conexion_revertida, referencias.id_embarazo) == 0


def test_c_las_referencias_validas_de_la_fixture_sobreviven_al_rechazo(
    cliente, referencias, conexion_revertida
):
    """El rollback del endpoint revierte su paquete, no el resto de la transacción.

    Esta es la prueba de que ``create_savepoint`` está haciendo su trabajo: con
    el modo por omisión, el ``rollback()`` del endpoint se habría llevado por
    delante también estas filas de referencia.
    """
    cliente.post(RUTA, json=paquete_de_signos(referencias, id_embarazo=ID_INEXISTENTE))

    assert (
        conexion_revertida.execute(
            select(Embarazo.id_embarazo).where(
                Embarazo.id_embarazo == referencias.id_embarazo
            )
        ).scalar_one_or_none()
        == referencias.id_embarazo
    )
    assert (
        conexion_revertida.execute(
            select(Dispositivo.codigo_dispositivo).where(
                Dispositivo.id_dispositivo == referencias.id_dispositivo
            )
        ).scalar_one()
        == CODIGO_DISPOSITIVO_FICTICIO
    )


# ---------------------------------------------------------------------------
# Escenario D: rollback provocado por una lectura inválida
# ---------------------------------------------------------------------------


def test_d_una_lectura_invalida_devuelve_422(cliente, referencias):
    """SpO2 = 150 lo rechaza el CHECK de PostgreSQL, y 23514 se traduce a 422."""
    respuesta = cliente.post(
        RUTA,
        json=paquete_de_signos(
            referencias,
            lecturas=[
                lectura_de_signos(referencias),
                lectura_de_signos(referencias, spo2_valor=SPO2_FUERA_DE_RANGO),
                lectura_de_signos(referencias),
            ],
        ),
    )

    assert respuesta.status_code == 422


def test_d_no_queda_carga_parcial_ni_sesion_huerfana(
    cliente, referencias, conexion_revertida
):
    """Fallaba la segunda de tres lecturas, y la sesión ya estaba escrita."""
    cliente.post(
        RUTA,
        json=paquete_de_signos(
            referencias,
            lecturas=[
                lectura_de_signos(referencias),
                lectura_de_signos(referencias, spo2_valor=SPO2_FUERA_DE_RANGO),
                lectura_de_signos(referencias),
            ],
        ),
    )

    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 0
    assert contar_lecturas(conexion_revertida, referencias.id_embarazo) == 0


def test_d_una_hr_no_positiva_tambien_revierte(cliente, referencias, conexion_revertida):
    """ck_lectura_biometrica_hr_positiva, otra restricción que vive en la base."""
    respuesta = cliente.post(
        RUTA,
        json=paquete_de_signos(
            referencias, lecturas=[lectura_de_signos(referencias, hr_valor=0)]
        ),
    )

    assert respuesta.status_code == 422
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 0


def test_d_un_movimiento_negativo_tambien_revierte(
    cliente, referencias, conexion_revertida
):
    respuesta = cliente.post(
        RUTA,
        json=paquete_de_movimiento(
            referencias, lecturas=[lectura_de_movimiento(referencias, mov_valor=-1)]
        ),
    )

    assert respuesta.status_code == 422
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 0


# ---------------------------------------------------------------------------
# Escenario E: colisión real de llave primaria contra el servidor
# ---------------------------------------------------------------------------


def paquete_distinguible(referencias: Referencias) -> dict[str, Any]:
    """Segundo paquete, deliberadamente distinto del primero en todo lo visible.

    Si el endpoint llegara a sobrescribir en vez de rechazar, la fila guardada
    cambiaría de tipo, de estado, de fecha y de forma biométrica: comparar
    contra estos valores es lo que convierte «no sobrescribió» en algo
    comprobable y no en una afirmación de fe.
    """
    return paquete_de_movimiento(
        referencias,
        fecha_inicio=(INICIO + timedelta(days=1)).isoformat(),
        fecha_fin=(FIN + timedelta(days=1)).isoformat(),
        estado_sesion="INTERRUMPIDA",
        lecturas=[
            lectura_de_movimiento(
                referencias,
                fecha_hora_captura=(CAPTURA + timedelta(days=1)).isoformat(),
                fecha_hora_sincronizacion=(
                    SINCRONIZACION + timedelta(days=1)
                ).isoformat(),
                mov_valor=99,
            )
        ],
    )


@pytest.fixture
def colision_de_pk(secuencia_de_sesiones, cliente, referencias, conexion_revertida):
    """Crea una sesión legítima y deja la secuencia apuntando a su ``id_sesion``.

    ``secuencia_de_sesiones`` va primero en la firma a propósito: así se monta
    antes que la transacción exterior y se desmonta después de su rollback.

    Tras el ``ALTER SEQUENCE``, el siguiente ``nextval`` devuelve un valor que ya
    está ocupado, de modo que el INSERT del endpoint choca de verdad contra
    ``pk_sesion_monitoreo``. No se simula nada: el error lo levanta PostgreSQL.
    """
    secuencia, _ = secuencia_de_sesiones

    primera = cliente.post(RUTA, json=paquete_de_signos(referencias))
    assert primera.status_code == 201, "la sesión previa debía crearse sin problemas"
    id_ocupado = int(primera.json()["id_sesion"])

    original = sesiones_del_embarazo(conexion_revertida, referencias.id_embarazo)[0]

    # El único valor interpolado es un int() derivado de la respuesta: no puede
    # llevar más que dígitos. ALTER SEQUENCE no admite parámetros.
    conexion_revertida.execute(
        text(f"ALTER SEQUENCE {secuencia} RESTART WITH {id_ocupado}")
    )

    segunda = cliente.post(RUTA, json=paquete_distinguible(referencias))
    return id_ocupado, dict(original), segunda


def test_e_una_colision_real_de_pk_devuelve_409(colision_de_pk):
    _, _, respuesta = colision_de_pk

    assert respuesta.status_code == 409
    assert respuesta.json()["detail"] == MENSAJE_CONFLICTO


def test_e_el_conflicto_no_se_trata_como_reenvio_exitoso(colision_de_pk):
    """Un reenvío no es un éxito: sin 2xx y sin identificadores devueltos."""
    _, _, respuesta = colision_de_pk
    cuerpo = respuesta.json()

    assert not respuesta.is_success
    assert "id_sesion" not in cuerpo
    assert "ids_lectura" not in cuerpo
    assert "lecturas_creadas" not in cuerpo


def test_e_el_registro_previo_no_fue_sobrescrito(
    colision_de_pk, referencias, conexion_revertida
):
    id_ocupado, original, _ = colision_de_pk

    actual = sesiones_del_embarazo(conexion_revertida, referencias.id_embarazo)[0]

    assert dict(actual) == original
    assert actual["id_sesion"] == id_ocupado
    assert actual["tipo_sesion"] is TipoSesion.SIGNOS_MATERNOS
    assert actual["estado_sesion"] is EstadoSesion.PENDIENTE
    assert actual["fecha_inicio"] == INICIO


def test_e_no_aparecio_una_sesion_adicional(
    colision_de_pk, referencias, conexion_revertida
):
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 1


def test_e_no_quedaron_lecturas_parciales(
    colision_de_pk, referencias, conexion_revertida
):
    """Solo sobrevive la lectura de la sesión legítima; la del paquete rechazado no."""
    lecturas = lecturas_del_embarazo(conexion_revertida, referencias.id_embarazo)

    assert len(lecturas) == 1
    assert lecturas[0]["hr_valor"] == Decimal("88.50")
    assert lecturas[0]["mov_valor"] is None


def test_e_la_respuesta_del_conflicto_no_filtra_nada(colision_de_pk):
    _, _, respuesta = colision_de_pk

    for fragmento in FRAGMENTOS_PROHIBIDOS:
        assert fragmento not in respuesta.text
    assert "pk_sesion_monitoreo" not in respuesta.text
    assert "23505" not in respuesta.text


def test_e_el_rollback_restaura_la_secuencia_alterada(
    secuencia_de_sesiones, engine_de_pruebas
):
    """``ALTER SEQUENCE`` queda dentro de la transacción, y el rollback lo deshace.

    Es la comprobación que justifica la maniobra del escenario E. La red de
    seguridad de la fixture (``setval``) se aplicaría igualmente, pero esta
    prueba mide el rollback *antes* de que esa red actúe, así que un fallo del
    aislamiento se vería aquí en lugar de quedar tapado.
    """
    secuencia, previo = secuencia_de_sesiones
    adelantada = 987_654

    conexion = engine_de_pruebas.connect()
    transaccion = conexion.begin()
    try:
        conexion.execute(text(f"ALTER SEQUENCE {secuencia} RESTART WITH {adelantada}"))
        durante = leer_estado_de_secuencia(conexion, secuencia)
        assert durante != previo, "el ALTER SEQUENCE debía cambiar algo de verdad"
        assert durante[0] == adelantada
    finally:
        transaccion.rollback()
        conexion.close()

    with engine_de_pruebas.connect() as verificacion:
        assert leer_estado_de_secuencia(verificacion, secuencia) == previo


# ---------------------------------------------------------------------------
# Regla de la semana 20, aplicada por la capa de servicio
# ---------------------------------------------------------------------------


def test_el_movimiento_antes_de_la_semana_20_se_rechaza(
    cliente, referencias, conexion_revertida
):
    respuesta = cliente.post(
        RUTA,
        json=paquete_de_movimiento(
            referencias,
            lecturas=[
                lectura_de_movimiento(
                    referencias, id_tiempo_gest=referencias.id_tiempo_sin_movimiento
                )
            ],
        ),
    )

    assert respuesta.status_code == 422
    assert "semana" in respuesta.json()["detail"]
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 0


def test_los_signos_maternos_antes_de_la_semana_20_si_se_aceptan(
    cliente, referencias, conexion_revertida
):
    """La regla es de los movimientos fetales, no de todas las lecturas."""
    respuesta = cliente.post(
        RUTA,
        json=paquete_de_signos(
            referencias,
            lecturas=[
                lectura_de_signos(
                    referencias, id_tiempo_gest=referencias.id_tiempo_sin_movimiento
                )
            ],
        ),
    )

    assert respuesta.status_code == 201
    assert contar_lecturas(conexion_revertida, referencias.id_embarazo) == 1


# ---------------------------------------------------------------------------
# Escenario F: saneamiento
# ---------------------------------------------------------------------------


def test_f_la_respuesta_de_error_no_filtra_detalles_internos(cliente, referencias):
    """El error lo genera PostgreSQL de verdad, no un doble."""
    respuesta = cliente.post(
        RUTA,
        json=paquete_de_signos(
            referencias,
            lecturas=[lectura_de_signos(referencias, spo2_valor=SPO2_FUERA_DE_RANGO)],
        ),
    )

    assert respuesta.status_code == 422
    for fragmento in FRAGMENTOS_PROHIBIDOS:
        assert fragmento not in respuesta.text


def test_f_la_respuesta_de_error_no_filtra_la_url_de_conexion(cliente, referencias):
    url = os.environ[VARIABLE_DE_ENTORNO]
    clave = make_url(url).password

    respuesta = cliente.post(
        RUTA,
        json=paquete_de_signos(
            referencias,
            lecturas=[lectura_de_signos(referencias, spo2_valor=SPO2_FUERA_DE_RANGO)],
        ),
    )

    assert url not in respuesta.text
    if clave:
        assert clave not in respuesta.text


def test_f_el_404_solo_menciona_lo_que_el_cliente_ya_sabia(cliente, referencias):
    respuesta = cliente.post(
        RUTA, json=paquete_de_signos(referencias, id_embarazo=ID_INEXISTENTE)
    )

    for fragmento in FRAGMENTOS_PROHIBIDOS:
        assert fragmento not in respuesta.text


# ---------------------------------------------------------------------------
# Aislamiento: la base queda como estaba
# ---------------------------------------------------------------------------


def test_la_base_no_conserva_nada_despues_de_una_creacion_exitosa(engine_de_pruebas):
    """Ciclo completo y revertido, verificado desde una conexión nueva.

    Es la única prueba que abre su propia conexión: necesita mirar la base
    *fuera* de la transacción que acaba de revertirse.
    """
    conexion = engine_de_pruebas.connect()
    transaccion = conexion.begin()
    try:
        sesion = Session(bind=conexion, join_transaction_mode="create_savepoint")
        referencias_locales = _referencias_para(conexion)
        app.dependency_overrides[get_db] = lambda: sesion
        try:
            with TestClient(app) as cliente_local:
                respuesta = cliente_local.post(
                    RUTA, json=paquete_de_signos(referencias_locales)
                )
            assert respuesta.status_code == 201
            assert contar_sesiones(conexion, referencias_locales.id_embarazo) == 1
        finally:
            app.dependency_overrides.clear()
            sesion.close()
    finally:
        transaccion.rollback()
        conexion.close()

    with engine_de_pruebas.connect() as verificacion:
        assert (
            verificacion.execute(
                select(func.count())
                .select_from(Clinica)
                .where(Clinica.ruc == RUC_FICTICIO)
            ).scalar_one()
            == 0
        )
        assert (
            verificacion.execute(
                select(func.count())
                .select_from(Dispositivo)
                .where(Dispositivo.codigo_dispositivo == CODIGO_DISPOSITIVO_FICTICIO)
            ).scalar_one()
            == 0
        )
        assert (
            verificacion.execute(
                select(func.count())
                .select_from(Paciente)
                .where(Paciente.cedula == CEDULA_FICTICIA)
            ).scalar_one()
            == 0
        )


def _referencias_para(conexion) -> Referencias:
    """Mismo contenido que la fixture ``referencias``, para la prueba de arriba.

    La fixture no sirve aquí: esta prueba es dueña de su propia conexión, y
    depender de ``conexion_revertida`` abriría una segunda transacción.
    """
    id_clinica = conexion.execute(
        insert(Clinica)
        .values(
            nombre_clinica="Clínica Rural Simulada SCRUM-62",
            ruc=RUC_FICTICIO,
            provincia="Chiriquí",
            distrito="Renacimiento",
            corregimiento="Plaza Caisán",
            direccion_fisica="Dirección simulada sin correspondencia real",
        )
        .returning(Clinica.id_clinica)
    ).scalar_one()
    id_paciente = conexion.execute(
        insert(Paciente)
        .values(
            cedula=CEDULA_FICTICIA,
            primer_nombre="Gestante",
            apellido_paterno="Simulada",
            email_pac=EMAIL_FICTICIO,
            fecha_nac=date(1998, 5, 14),
        )
        .returning(Paciente.id_paciente)
    ).scalar_one()
    id_embarazo = conexion.execute(
        insert(Embarazo)
        .values(
            id_paciente=id_paciente,
            id_clinica=id_clinica,
            numero_gestas=2,
            numero_partos=1,
            fecha_inicio=date(2025, 8, 1),
            fecha_probable_parto=date(2026, 5, 8),
        )
        .returning(Embarazo.id_embarazo)
    ).scalar_one()
    id_dispositivo = conexion.execute(
        insert(Dispositivo)
        .values(
            id_clinica=id_clinica,
            codigo_dispositivo=CODIGO_DISPOSITIVO_FICTICIO,
            modelo="FetalAlert Simulado",
            version_firmware="0.0.0-sim",
        )
        .returning(Dispositivo.id_dispositivo)
    ).scalar_one()
    return Referencias(
        id_clinica=id_clinica,
        id_paciente=id_paciente,
        id_embarazo=id_embarazo,
        id_dispositivo=id_dispositivo,
        id_tiempo_con_movimiento=_asegurar_tiempo_gestacional(
            conexion, SEMANA_CON_MOVIMIENTO, mes=10, trimestre=3
        ),
        id_tiempo_sin_movimiento=_asegurar_tiempo_gestacional(
            conexion, SEMANA_SIN_MOVIMIENTO, mes=3, trimestre=1
        ),
        id_semaforo=_asegurar_semaforo(conexion),
    )
