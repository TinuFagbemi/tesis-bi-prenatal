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

**La colisión de llave primaria.** Ni ``sesion_monitoreo`` ni
``lectura_biometrica`` tienen un UNIQUE fuera de su PK, y el cliente no envía
identificadores, así que la única forma de provocar un choque real es adelantar
la secuencia de ``id_sesion`` para que el siguiente ``nextval`` devuelva un valor
ya ocupado. Eso reproduce lo que de verdad significa hoy un ``23505`` en estas
tablas: una secuencia desincronizada, o sea un defecto del servidor, y por eso
la respuesta esperada es **500 y no 409**. No es un reenvío: un cliente no puede
provocarlo ni queriendo.

La maniobra lleva tres salvaguardas: la secuencia se descubre desde el catálogo
con ``pg_get_serial_sequence`` en lugar de suponer su nombre; su estado se
fotografía antes y se restaura después con ``setval`` en un ``try/finally`` que
falla explícitamente si la restauración no funciona; y esa restauración corre
con ``lock_timeout``, para que un problema de aislamiento se manifieste como un
error y nunca como un bloqueo indefinido.

**Sobre reenvíos.** SCRUM-62 no los detecta, y hay una prueba que lo deja por
escrito: el mismo JSON enviado dos veces crea dos sesiones distintas, ambas con
201.

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
from app.models.monitoreo import (
    AsignacionDispositivo,
    Dispositivo,
    LecturaBiometrica,
    SesionMonitoreo,
)
from app.models.seguridad import AuditoriaLog
from app.services.errores import MENSAJE_INESPERADO

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
CODIGO_DISPOSITIVO_SIN_ASIGNAR = "SCRUM62-DISP-0002"

# Identificador que no puede existir; cabe en un INTEGER de PostgreSQL.
ID_INEXISTENTE = 2_000_000_000

# El embarazo ficticio del que cuelga todo. Las semanas gestacionales de las
# ventanas de abajo se derivan de esta fecha, no se eligen a mano.
FECHA_INICIO_EMBARAZO = date(2025, 8, 1)
FECHA_PROBABLE_PARTO = date(2026, 5, 8)


@dataclass(frozen=True)
class Ventana:
    """Una sesión situada en el tiempo, con la semana gestacional que le toca.

    La semana está calculada a mano a partir de ``FECHA_INICIO_EMBARAZO`` y no
    copiada de la implementación: es el valor contra el que se la verifica.
    """

    inicio: datetime
    captura: datetime
    sincronizacion: datetime
    fin: datetime
    semana: int


def _ventana(inicio: datetime, semana: int) -> Ventana:
    return Ventana(
        inicio=inicio,
        captura=inicio + timedelta(minutes=5),
        sincronizacion=inicio + timedelta(minutes=40),
        fin=inicio + timedelta(minutes=30),
        semana=semana,
    )


# 2026-03-01 - 2025-08-01 = 212 días -> (212 // 7) + 1 = semana 31.
# Por encima de la semana 20, así que admite movimiento fetal.
VENTANA = _ventana(datetime(2026, 3, 1, 14, 0, tzinfo=timezone.utc), semana=31)

# 2025-10-17 - 2025-08-01 = 77 días -> (77 // 7) + 1 = semana 12.
# Por debajo de la semana 20: el movimiento fetal aquí debe rechazarse.
VENTANA_TEMPRANA = _ventana(
    datetime(2025, 10, 17, 14, 0, tzinfo=timezone.utc), semana=12
)

INICIO = VENTANA.inicio
FIN = VENTANA.fin
CAPTURA = VENTANA.captura
SINCRONIZACION = VENTANA.sincronizacion

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
    id_dispositivo_sin_asignar: int
    id_asignacion: int
    id_tiempo_por_semana: dict[int, int]
    id_semaforo: int

    def id_tiempo(self, semana: int) -> int:
        return self.id_tiempo_por_semana[semana]


def _trimestre_de(semana: int) -> int:
    """Trimestre que corresponde a una semana, según el corte del generador."""
    if semana <= 13:
        return 1
    if semana <= 27:
        return 2
    return 3


def _mes_de(semana: int) -> int:
    """Aproximación de mes gestacional usada por el generador."""
    return min(9, ((semana - 1) // 4) + 1)


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


def _crear_dispositivo(conexion, id_clinica: int, codigo: str) -> int:
    return conexion.execute(
        insert(Dispositivo)
        .values(
            id_clinica=id_clinica,
            codigo_dispositivo=codigo,
            modelo="FetalAlert Simulado",
            version_firmware="0.0.0-sim",
        )
        .returning(Dispositivo.id_dispositivo)
    ).scalar_one()


def _crear_asignacion(
    conexion,
    *,
    id_dispositivo: int,
    id_embarazo: int,
    fecha_inicio: date,
    fecha_fin: date | None = None,
    activo: bool = True,
) -> int:
    """Presta un dispositivo a un embarazo durante un período concreto."""
    return conexion.execute(
        insert(AsignacionDispositivo)
        .values(
            id_dispositivo=id_dispositivo,
            id_embarazo=id_embarazo,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            activo=activo,
        )
        .returning(AsignacionDispositivo.id_asignacion)
    ).scalar_one()


def _asegurar_semanas(conexion, semanas) -> dict[int, int]:
    return {
        semana: _asegurar_tiempo_gestacional(
            conexion, semana, mes=_mes_de(semana), trimestre=_trimestre_de(semana)
        )
        for semana in semanas
    }


@pytest.fixture
def referencias(conexion_revertida) -> Referencias:
    """Crea el mínimo de filas ficticias que un paquete necesita referenciar.

    Incluye la ``AsignacionDispositivo`` que presta el dispositivo al embarazo:
    sin ella el paquete ya no describe una combinación posible, porque un
    dispositivo que existe no es lo mismo que un dispositivo entregado a esa
    gestante. La asignación se abre el día en que empieza el embarazo y se deja
    sin cerrar, así que cubre las dos ventanas de prueba.

    También crea un segundo dispositivo **sin** asignación, para poder pedir un
    paquete imposible sin inventar identificadores inexistentes.

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
            fecha_inicio=FECHA_INICIO_EMBARAZO,
            fecha_probable_parto=FECHA_PROBABLE_PARTO,
        )
        .returning(Embarazo.id_embarazo)
    ).scalar_one()

    id_dispositivo = _crear_dispositivo(
        conexion_revertida, id_clinica, CODIGO_DISPOSITIVO_FICTICIO
    )
    id_dispositivo_sin_asignar = _crear_dispositivo(
        conexion_revertida, id_clinica, CODIGO_DISPOSITIVO_SIN_ASIGNAR
    )

    id_asignacion = _crear_asignacion(
        conexion_revertida,
        id_dispositivo=id_dispositivo,
        id_embarazo=id_embarazo,
        fecha_inicio=FECHA_INICIO_EMBARAZO,
        fecha_fin=None,
    )

    return Referencias(
        id_clinica=id_clinica,
        id_paciente=id_paciente,
        id_embarazo=id_embarazo,
        id_dispositivo=id_dispositivo,
        id_dispositivo_sin_asignar=id_dispositivo_sin_asignar,
        id_asignacion=id_asignacion,
        id_tiempo_por_semana=_asegurar_semanas(
            conexion_revertida, (VENTANA.semana, VENTANA_TEMPRANA.semana)
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


def lectura_de_signos(
    referencias: Referencias, *, ventana: Ventana = VENTANA, **cambios: Any
) -> dict[str, Any]:
    """Lectura de signos maternos situada dentro de ``ventana``.

    ``id_tiempo_gest`` sale de la semana que le corresponde a esa ventana, no
    de una constante suelta: el endpoint comprueba ahora que la semana declarada
    sea la que el embarazo tiene en la fecha de captura.
    """
    fila: dict[str, Any] = {
        "id_tiempo_gest": referencias.id_tiempo(ventana.semana),
        "id_semaforo": referencias.id_semaforo,
        "fecha_hora_captura": ventana.captura.isoformat(),
        "fecha_hora_sincronizacion": ventana.sincronizacion.isoformat(),
        "hr_valor": 88.5,
        "spo2_valor": 97,
        "mov_valor": None,
    }
    fila.update(cambios)
    return fila


def lectura_de_movimiento(
    referencias: Referencias, *, ventana: Ventana = VENTANA, **cambios: Any
) -> dict[str, Any]:
    fila: dict[str, Any] = {
        "id_tiempo_gest": referencias.id_tiempo(ventana.semana),
        "id_semaforo": referencias.id_semaforo,
        "fecha_hora_captura": ventana.captura.isoformat(),
        "fecha_hora_sincronizacion": ventana.sincronizacion.isoformat(),
        "hr_valor": None,
        "spo2_valor": None,
        "mov_valor": 12,
    }
    fila.update(cambios)
    return fila


def _paquete(
    referencias: Referencias,
    tipo_sesion: str,
    ventana: Ventana,
    lecturas: list[dict[str, Any]] | None,
    cambios: dict[str, Any],
) -> dict[str, Any]:
    """Sesión cerrada y coherente: trae ``fecha_fin``, así que va COMPLETADA."""
    constructor = (
        lectura_de_movimiento if tipo_sesion == "MOVIMIENTOS_FETALES" else lectura_de_signos
    )
    cuerpo: dict[str, Any] = {
        "id_embarazo": referencias.id_embarazo,
        "id_dispositivo": referencias.id_dispositivo,
        "tipo_sesion": tipo_sesion,
        "fecha_inicio": ventana.inicio.isoformat(),
        "fecha_fin": ventana.fin.isoformat(),
        "estado_sesion": "COMPLETADA",
        "lecturas": (
            [constructor(referencias, ventana=ventana)] if lecturas is None else lecturas
        ),
    }
    cuerpo.update(cambios)
    return cuerpo


def paquete_de_signos(
    referencias: Referencias,
    *,
    ventana: Ventana = VENTANA,
    lecturas: list[dict[str, Any]] | None = None,
    **cambios: Any,
) -> dict[str, Any]:
    return _paquete(referencias, "SIGNOS_MATERNOS", ventana, lecturas, cambios)


def paquete_de_movimiento(
    referencias: Referencias,
    *,
    ventana: Ventana = VENTANA,
    lecturas: list[dict[str, Any]] | None = None,
    **cambios: Any,
) -> dict[str, Any]:
    return _paquete(referencias, "MOVIMIENTOS_FETALES", ventana, lecturas, cambios)


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
    """Omitir estado y origen deja que el modelo ponga los suyos.

    También se quita ``fecha_fin``: una sesión sin estado se guarda como
    PENDIENTE, y una PENDIENTE no puede declarar cuándo terminó.
    """
    cuerpo = paquete_de_signos(referencias)
    del cuerpo["fecha_fin"]
    del cuerpo["estado_sesion"]

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
# El dispositivo tiene que estar asignado a ese embarazo durante la sesión
# ---------------------------------------------------------------------------


def test_un_dispositivo_asignado_al_embarazo_se_acepta(
    cliente, referencias, conexion_revertida
):
    """Camino feliz con una AsignacionDispositivo real detrás."""
    respuesta = cliente.post(RUTA, json=paquete_de_signos(referencias))

    assert respuesta.status_code == 201
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 1


def test_un_dispositivo_existente_pero_no_asignado_se_rechaza(
    cliente, referencias, conexion_revertida
):
    """Dos llaves foráneas válidas no demuestran que el préstamo exista."""
    respuesta = cliente.post(
        RUTA,
        json=paquete_de_signos(
            referencias, id_dispositivo=referencias.id_dispositivo_sin_asignar
        ),
    )

    assert respuesta.status_code == 422
    assert "asignación" in respuesta.json()["detail"]
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 0


def test_una_asignacion_que_empieza_despues_de_la_sesion_se_rechaza(
    cliente, referencias, conexion_revertida
):
    id_dispositivo = _crear_dispositivo(
        conexion_revertida, referencias.id_clinica, "SCRUM62-DISP-TARDE"
    )
    _crear_asignacion(
        conexion_revertida,
        id_dispositivo=id_dispositivo,
        id_embarazo=referencias.id_embarazo,
        fecha_inicio=VENTANA.fin.date() + timedelta(days=1),
    )

    respuesta = cliente.post(
        RUTA, json=paquete_de_signos(referencias, id_dispositivo=id_dispositivo)
    )

    assert respuesta.status_code == 422
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 0


def test_una_asignacion_que_termino_antes_de_la_sesion_se_rechaza(
    cliente, referencias, conexion_revertida
):
    id_dispositivo = _crear_dispositivo(
        conexion_revertida, referencias.id_clinica, "SCRUM62-DISP-CERRADA"
    )
    _crear_asignacion(
        conexion_revertida,
        id_dispositivo=id_dispositivo,
        id_embarazo=referencias.id_embarazo,
        fecha_inicio=FECHA_INICIO_EMBARAZO,
        fecha_fin=VENTANA.inicio.date() - timedelta(days=1),
        activo=False,
    )

    respuesta = cliente.post(
        RUTA, json=paquete_de_signos(referencias, id_dispositivo=id_dispositivo)
    )

    assert respuesta.status_code == 422
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 0


def test_una_asignacion_historica_cerrada_que_cubre_la_sesion_se_acepta(
    cliente, referencias, conexion_revertida
):
    """``activo=False`` no invalida una sesión de cuando el préstamo seguía vivo.

    Ésta es la razón de que la regla sea temporal y no un simple booleano: los
    dispositivos se devuelven, y las sesiones que se tomaron con ellos siguen
    siendo válidas después.
    """
    id_dispositivo = _crear_dispositivo(
        conexion_revertida, referencias.id_clinica, "SCRUM62-DISP-HISTORICA"
    )
    _crear_asignacion(
        conexion_revertida,
        id_dispositivo=id_dispositivo,
        id_embarazo=referencias.id_embarazo,
        fecha_inicio=FECHA_INICIO_EMBARAZO,
        fecha_fin=VENTANA.fin.date() + timedelta(days=30),
        activo=False,
    )

    respuesta = cliente.post(
        RUTA, json=paquete_de_signos(referencias, id_dispositivo=id_dispositivo)
    )

    assert respuesta.status_code == 201
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 1


def test_una_asignacion_de_otro_embarazo_no_sirve(
    cliente, referencias, conexion_revertida
):
    """El dispositivo está prestado, pero a otra gestante."""
    id_otro_paciente = conexion_revertida.execute(
        insert(Paciente)
        .values(
            cedula="SCRUM62-8-000-0002",
            primer_nombre="Otra",
            apellido_paterno="Simulada",
            email_pac="scrum62.otra@example.invalid",
            fecha_nac=date(1996, 2, 3),
        )
        .returning(Paciente.id_paciente)
    ).scalar_one()
    id_otro_embarazo = conexion_revertida.execute(
        insert(Embarazo)
        .values(
            id_paciente=id_otro_paciente,
            id_clinica=referencias.id_clinica,
            numero_gestas=1,
            numero_partos=0,
            fecha_inicio=FECHA_INICIO_EMBARAZO,
            fecha_probable_parto=FECHA_PROBABLE_PARTO,
        )
        .returning(Embarazo.id_embarazo)
    ).scalar_one()

    id_dispositivo = _crear_dispositivo(
        conexion_revertida, referencias.id_clinica, "SCRUM62-DISP-AJENA"
    )
    _crear_asignacion(
        conexion_revertida,
        id_dispositivo=id_dispositivo,
        id_embarazo=id_otro_embarazo,
        fecha_inicio=FECHA_INICIO_EMBARAZO,
    )

    respuesta = cliente.post(
        RUTA, json=paquete_de_signos(referencias, id_dispositivo=id_dispositivo)
    )

    assert respuesta.status_code == 422
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 0


# ---------------------------------------------------------------------------
# La semana declarada tiene que ser la semana real del embarazo
# ---------------------------------------------------------------------------


def test_la_semana_declarada_debe_coincidir_con_la_de_la_captura(
    cliente, referencias, conexion_revertida
):
    """Semana 12 en el catálogo para una captura que cae en la semana 31."""
    respuesta = cliente.post(
        RUTA,
        json=paquete_de_signos(
            referencias,
            lecturas=[
                lectura_de_signos(
                    referencias,
                    id_tiempo_gest=referencias.id_tiempo(VENTANA_TEMPRANA.semana),
                )
            ],
        ),
    )

    assert respuesta.status_code == 422
    detalle = respuesta.json()["detail"]
    assert str(VENTANA.semana) in detalle
    assert str(VENTANA_TEMPRANA.semana) in detalle
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 0


def test_la_semana_correcta_se_acepta(cliente, referencias, conexion_revertida):
    respuesta = cliente.post(
        RUTA, json=paquete_de_signos(referencias, ventana=VENTANA_TEMPRANA)
    )

    assert respuesta.status_code == 201
    lectura = lecturas_del_embarazo(conexion_revertida, referencias.id_embarazo)[0]
    assert lectura["id_tiempo_gest"] == referencias.id_tiempo(VENTANA_TEMPRANA.semana)


def test_una_captura_anterior_al_inicio_del_embarazo_se_rechaza(
    cliente, referencias, conexion_revertida
):
    """Semana gestacional 0 o negativa: fuera del dominio del catálogo.

    Necesita un dispositivo prestado desde antes de que empezara el embarazo;
    con la asignación normal, la sesión de julio fallaría primero por no estar
    cubierta, y la prueba estaría midiendo la otra regla.
    """
    antes = _ventana(
        datetime(2025, 7, 1, 14, 0, tzinfo=timezone.utc), semana=VENTANA.semana
    )
    id_dispositivo = _crear_dispositivo(
        conexion_revertida, referencias.id_clinica, "SCRUM62-DISP-PREVIA"
    )
    _crear_asignacion(
        conexion_revertida,
        id_dispositivo=id_dispositivo,
        id_embarazo=referencias.id_embarazo,
        fecha_inicio=date(2025, 6, 1),
    )

    respuesta = cliente.post(
        RUTA,
        json=paquete_de_signos(referencias, ventana=antes, id_dispositivo=id_dispositivo),
    )

    assert respuesta.status_code == 422
    assert "anterior al inicio del embarazo" in respuesta.json()["detail"]
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 0


def test_una_captura_mas_alla_del_rango_gestacional_se_rechaza(
    cliente, referencias, conexion_revertida
):
    """Semana 60: el catálogo solo admite de la 1 a la 42."""
    tardia = _ventana(
        datetime(2026, 9, 26, 14, 0, tzinfo=timezone.utc), semana=VENTANA.semana
    )

    respuesta = cliente.post(RUTA, json=paquete_de_signos(referencias, ventana=tardia))

    assert respuesta.status_code == 422
    assert "fuera del rango" in respuesta.json()["detail"]
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 0


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
# Colisión interna de secuencia: una PK generada que choca con una existente
# ---------------------------------------------------------------------------

# Un día después de la ventana normal: sigue en la semana 31 (212 y 213 días
# desde el inicio del embarazo caen ambos en la misma semana), así que el
# paquete es válido y llega hasta el INSERT.
VENTANA_DISTINGUIBLE = _ventana(
    VENTANA.inicio + timedelta(days=1), semana=VENTANA.semana
)


def paquete_distinguible(referencias: Referencias) -> dict[str, Any]:
    """Segundo paquete, deliberadamente distinto del primero en todo lo visible.

    Si el endpoint llegara a sobrescribir en vez de fallar, la fila guardada
    cambiaría de tipo, de fechas y de forma biométrica: comparar contra estos
    valores es lo que convierte «no sobrescribió» en algo comprobable y no en
    una afirmación de fe.

    Va COMPLETADA y no INTERRUMPIDA porque trae ``fecha_fin``: tiene que ser un
    paquete plenamente válido para que el fallo que se observa sea el choque de
    llave primaria y no una validación previa.
    """
    return paquete_de_movimiento(
        referencias,
        ventana=VENTANA_DISTINGUIBLE,
        lecturas=[
            lectura_de_movimiento(
                referencias, ventana=VENTANA_DISTINGUIBLE, mov_valor=99
            )
        ],
    )


@pytest.fixture
def colision_de_pk(secuencia_de_sesiones, cliente, referencias, conexion_revertida):
    """Crea una sesión legítima y deja la secuencia apuntando a su ``id_sesion``.

    Esto **no** simula un reenvío: el cliente nunca envía ``id_sesion``, así que
    no tiene forma de provocar una llave duplicada. Lo que reproduce es una
    secuencia que quedó por detrás de las filas ya guardadas, que es un defecto
    del servidor. Por eso la respuesta esperada es 500.

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


def test_e_una_colision_de_secuencia_es_un_error_interno(colision_de_pk):
    """500, no 409: la llave duplicada no la pudo causar el cliente."""
    _, _, respuesta = colision_de_pk

    assert respuesta.status_code == 500
    assert respuesta.json()["detail"] == MENSAJE_INESPERADO


def test_e_la_colision_no_devuelve_identificadores(colision_de_pk):
    """Nada de 2xx y nada que se parezca a una creación."""
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
    assert actual["estado_sesion"] is EstadoSesion.COMPLETADA
    assert actual["fecha_inicio"] == INICIO


def test_e_no_aparecio_una_sesion_adicional(
    colision_de_pk, referencias, conexion_revertida
):
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 1


def test_e_no_quedaron_lecturas_parciales(
    colision_de_pk, referencias, conexion_revertida
):
    """Solo sobrevive la lectura de la sesión legítima; la del paquete fallido no."""
    lecturas = lecturas_del_embarazo(conexion_revertida, referencias.id_embarazo)

    assert len(lecturas) == 1
    assert lecturas[0]["hr_valor"] == Decimal("88.50")
    assert lecturas[0]["mov_valor"] is None


def test_e_la_respuesta_de_la_colision_no_filtra_nada(colision_de_pk):
    _, _, respuesta = colision_de_pk

    for fragmento in FRAGMENTOS_PROHIBIDOS:
        assert fragmento not in respuesta.text
    assert "pk_sesion_monitoreo" not in respuesta.text
    assert "23505" not in respuesta.text


# ---------------------------------------------------------------------------
# Lo que SCRUM-62 hace hoy con un reenvío: nada
# ---------------------------------------------------------------------------


def test_el_mismo_paquete_enviado_dos_veces_crea_dos_sesiones(
    cliente, referencias, conexion_revertida
):
    """La verdad incómoda sobre el estado actual, escrita como prueba.

    No hay detección de reenvíos: el mismo JSON, byte por byte, produce dos
    sesiones distintas y dos respuestas 201. Es exactamente lo que SCRUM-63
    tendrá que cambiar, y dejarlo documentado en una prueba evita que alguien
    suponga lo contrario leyendo el 409 de la tabla de errores.
    """
    cuerpo = paquete_de_signos(referencias)

    primera = cliente.post(RUTA, json=cuerpo)
    segunda = cliente.post(RUTA, json=cuerpo)

    assert primera.status_code == 201
    assert segunda.status_code == 201
    assert primera.json()["id_sesion"] != segunda.json()["id_sesion"]
    assert not set(primera.json()["ids_lectura"]) & set(segunda.json()["ids_lectura"])
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 2
    assert contar_lecturas(conexion_revertida, referencias.id_embarazo) == 2


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
    """Sesión situada de verdad en la semana 12, no solo declarada como tal."""
    respuesta = cliente.post(
        RUTA, json=paquete_de_movimiento(referencias, ventana=VENTANA_TEMPRANA)
    )

    assert respuesta.status_code == 422
    assert str(VENTANA_TEMPRANA.semana) in respuesta.json()["detail"]
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 0


def test_el_movimiento_desde_la_semana_20_se_acepta(
    cliente, referencias, conexion_revertida
):
    respuesta = cliente.post(RUTA, json=paquete_de_movimiento(referencias))

    assert respuesta.status_code == 201
    assert contar_lecturas(conexion_revertida, referencias.id_embarazo) == 1


def test_los_signos_maternos_antes_de_la_semana_20_si_se_aceptan(
    cliente, referencias, conexion_revertida
):
    """La regla es de los movimientos fetales, no de todas las lecturas."""
    respuesta = cliente.post(
        RUTA, json=paquete_de_signos(referencias, ventana=VENTANA_TEMPRANA)
    )

    assert respuesta.status_code == 201
    assert contar_lecturas(conexion_revertida, referencias.id_embarazo) == 1


def test_no_se_puede_esquivar_la_semana_20_declarando_otra_semana(
    cliente, referencias, conexion_revertida
):
    """El agujero que cerró esta revisión, probado explícitamente.

    Antes bastaba con apuntar ``id_tiempo_gest`` a una semana >= 20 para colar
    un movimiento capturado en la semana 12: la regla miraba la semana que traía
    el paquete. Ahora la semana se calcula desde el embarazo y la fecha de
    captura, así que la mentira se detecta antes de llegar al umbral.
    """
    respuesta = cliente.post(
        RUTA,
        json=paquete_de_movimiento(
            referencias,
            ventana=VENTANA_TEMPRANA,
            lecturas=[
                lectura_de_movimiento(
                    referencias,
                    ventana=VENTANA_TEMPRANA,
                    # Semana 31: por encima del umbral, y falsa para esta captura.
                    id_tiempo_gest=referencias.id_tiempo(VENTANA.semana),
                )
            ],
        ),
    )

    assert respuesta.status_code == 422
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 0


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
            fecha_inicio=FECHA_INICIO_EMBARAZO,
            fecha_probable_parto=FECHA_PROBABLE_PARTO,
        )
        .returning(Embarazo.id_embarazo)
    ).scalar_one()
    id_dispositivo = _crear_dispositivo(
        conexion, id_clinica, CODIGO_DISPOSITIVO_FICTICIO
    )
    id_dispositivo_sin_asignar = _crear_dispositivo(
        conexion, id_clinica, CODIGO_DISPOSITIVO_SIN_ASIGNAR
    )
    id_asignacion = _crear_asignacion(
        conexion,
        id_dispositivo=id_dispositivo,
        id_embarazo=id_embarazo,
        fecha_inicio=FECHA_INICIO_EMBARAZO,
        fecha_fin=None,
    )
    return Referencias(
        id_clinica=id_clinica,
        id_paciente=id_paciente,
        id_embarazo=id_embarazo,
        id_dispositivo=id_dispositivo,
        id_dispositivo_sin_asignar=id_dispositivo_sin_asignar,
        id_asignacion=id_asignacion,
        id_tiempo_por_semana=_asegurar_semanas(
            conexion, (VENTANA.semana, VENTANA_TEMPRANA.semana)
        ),
        id_semaforo=_asegurar_semaforo(conexion),
    )
