"""Ciclo real del cargador contra un servidor PostgreSQL 16.

Se omite salvo que esté definida ``SCRUM61_TEST_DATABASE_URL``, para que la
suite normal siga siendo offline. El workflow de CI convierte esa omisión en un
job en rojo: aquí no basta con que las pruebas existan, tienen que ejecutarse.

Barreras de seguridad, distintas de las de ``test_migration_postgresql.py``.
Aquel módulo borra y recrea el esquema completo, así que se protege exigiendo un
nombre de base concreto. Este no ejecuta una sola sentencia DDL sobre el
esquema, no migra, no crea, no borra y no recrea nada: **todo ocurre dentro de
una transacción que siempre se revierte**. Esa es su protección, y por eso no
hardcodea ningún nombre de base.

Lo que sí exige, y verifica antes de tocar nada:

* que la URL apunte a PostgreSQL, comprobado sobre la URL sin conectarse;
* que la base ya esté exactamente en el ``head`` de Alembic.

Si la base no está migrada, la prueba falla y pide ``alembic upgrade head``: no
la migra por su cuenta. Nunca usa ``DATABASE_URL`` como alternativa, así que no
puede caer por accidente sobre la base de desarrollo.

Ejecución desde ``backend/``::

    $env:SCRUM61_TEST_DATABASE_URL = "postgresql+psycopg://<usuario>:<clave>@127.0.0.1:<puerto>/<base>"
    .\\.venv\\Scripts\\python.exe -m pytest tests/test_load_mock_data_postgresql.py -v

Estas pruebas escriben y revierten miles de filas sobre las mismas llaves
primarias, y una de ellas consume ``nextval`` sobre una secuencia real: deben
ejecutarse en serie contra una misma base, nunca en paralelo.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select, text, tuple_
from sqlalchemy.exc import IntegrityError

from app.loader import (
    ORDEN_DE_CARGA,
    ConflictoDeDatos,
    DatasetNormalizado,
    ErrorDeCarga,
    MotorNoSoportado,
    ResultadoCarga,
    cargar_dataset,
    citar_secuencia,
    leer_estado_de_secuencia,
    preflight,
    proximo_valor,
    secuencia_de_tabla,
    tabla_operacional,
    validar_dataset,
    verificar_url,
)
from tests.test_generate_mock_data import cargar_generador

VARIABLE_DE_ENTORNO = "SCRUM61_TEST_DATABASE_URL"

pytestmark = pytest.mark.skipif(
    not os.environ.get(VARIABLE_DE_ENTORNO),
    reason=(
        f"Define {VARIABLE_DE_ENTORNO} apuntando a una base PostgreSQL ya migrada "
        "a head para ejecutar la validación del cargador."
    ),
)

# Cantidades aprobadas en SCRUM-54, verificadas contra el servidor.
SESIONES_ESPERADAS = 732
LECTURAS_ESPERADAS = 1180
LECTURAS_HR_ESPERADAS = 560
LECTURAS_MOVIMIENTO_ESPERADAS = 620
SEMAFORO_ESPERADO = {"OK": 826, "WARNING": 295, "ERROR": 59}
LECTURAS_POR_SESION_ESPERADAS = {5: 112, 1: 620}
FILAS_ESPERADAS = 2270

# Tabla usada para las pruebas que tocan una secuencia real. Es la primera con
# secuencia propia del orden de carga que no arrastra dependencias.
TABLA_CON_SECUENCIA = "clinica"


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def criterio_de_pk(nombre: str, filas: list[dict[str, Any]]):
    """Restringe una consulta a exactamente las llaves primarias del dataset.

    Se reconstruye aquí, en vez de importar el ayudante privado del cargador,
    para que la verificación no dependa de los internos de lo que verifica.
    """
    tabla = tabla_operacional(nombre)
    columnas = list(tabla.primary_key.columns)
    nombres = [columna.name for columna in columnas]
    if len(columnas) == 1:
        return columnas[0].in_([fila[nombres[0]] for fila in filas])
    return tuple_(*columnas).in_(
        [tuple(fila[nombre_pk] for nombre_pk in nombres) for fila in filas]
    )


def contar_del_dataset(conexion, nombre: str, filas: list[dict[str, Any]]) -> int:
    return conexion.execute(
        select(func.count())
        .select_from(tabla_operacional(nombre))
        .where(criterio_de_pk(nombre, filas))
    ).scalar_one()


def contar_filas_del_dataset(conexion, dataset: DatasetNormalizado) -> dict[str, int]:
    return {
        nombre: contar_del_dataset(conexion, nombre, dataset[nombre])
        for nombre in ORDEN_DE_CARGA
    }


def contar_auditoria(conexion) -> int:
    return conexion.execute(
        select(func.count()).select_from(tabla_operacional("auditoria_log"))
    ).scalar_one()


# ---------------------------------------------------------------------------
# Fixtures de conexión: exigen el entorno, no lo preparan
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def url_de_pruebas() -> str:
    url = os.environ[VARIABLE_DE_ENTORNO]
    try:
        # Sobre la URL, sin conectarse: una URL equivocada se rechaza antes de
        # que exista un engine que pueda crear algo.
        verificar_url(url)
    except MotorNoSoportado as error:
        pytest.fail(f"{VARIABLE_DE_ENTORNO}: {error}")
    return url


@pytest.fixture(scope="session")
def engine_de_pruebas(url_de_pruebas):
    """Engine sobre una base que ya debe estar migrada.

    No ejecuta ``alembic upgrade head``: preparar la base es responsabilidad de
    quien lanza las pruebas, y en CI lo hace el paso de SCRUM-52 que corre antes.
    """
    engine = create_engine(url_de_pruebas)
    with engine.connect() as conexion:
        try:
            preflight(conexion)
        except ErrorDeCarga as error:
            engine.dispose()
            # Sin URL en el mensaje: lleva credenciales.
            pytest.fail(
                f"La base indicada por {VARIABLE_DE_ENTORNO} no está lista: {error}"
            )
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def dataset_crudo(tmp_path_factory) -> dict:
    """Dataset aprobado, generado una sola vez con la semilla oficial."""
    generador = cargar_generador()
    carpeta = tmp_path_factory.mktemp("dataset_simulado")

    with pytest.MonkeyPatch.context() as parche:
        parche.setattr(generador, "CARPETA_SALIDA", carpeta)
        generador.main()

    return json.loads(
        (carpeta / "dataset_fetalalert.json").read_text(encoding="utf-8")
    )


@pytest.fixture(scope="session")
def dataset(dataset_crudo) -> DatasetNormalizado:
    return validar_dataset(dataset_crudo)


@pytest.fixture(scope="session")
def revision(engine_de_pruebas) -> str:
    """Revisión confirmada por el preflight, fuera de toda transacción de carga.

    El orden aprobado es validación -> preflight -> transacción -> carga, así
    que la comprobación de Alembic ocurre aquí, en su propia conexión, y
    ``cargar_dataset`` solo recibe el resultado.
    """
    with engine_de_pruebas.connect() as conexion:
        return preflight(conexion)


@pytest.fixture
def transaccion_revertida(engine_de_pruebas):
    """Conexión cuya transacción siempre se revierte.

    ``cargar_dataset`` no hace commit ni rollback: la transacción es de quien
    llama, y aquí ese dueño revierte pase lo que pase.
    """
    conexion = engine_de_pruebas.connect()
    transaccion = conexion.begin()
    try:
        yield conexion
    finally:
        transaccion.rollback()
        conexion.close()


# ---------------------------------------------------------------------------
# Escenarios 1, 2 y 5: una sola carga observada, luego revertida
# ---------------------------------------------------------------------------


@dataclass
class Observaciones:
    """Todo lo que se mide sobre una carga doble, ya revertida.

    Se recoge de una vez y la transacción se cierra antes de que corra ninguna
    prueba: dejarla abierta bloquearía a las pruebas que insertan esas mismas
    llaves primarias.
    """

    primera: ResultadoCarga
    segunda: ResultadoCarga
    filas_por_tabla: dict[str, int] = field(default_factory=dict)
    filas_tras_rollback: dict[str, int] = field(default_factory=dict)
    lecturas_hr: int = 0
    lecturas_movimiento: int = 0
    semaforo: dict[str, int] = field(default_factory=dict)
    lecturas_por_sesion: dict[int, int] = field(default_factory=dict)
    auditoria_antes: int = 0
    auditoria_despues: int = 0
    secuencias: dict[str, tuple[int, int]] = field(default_factory=dict)


@pytest.fixture(scope="module")
def observaciones(engine_de_pruebas, dataset, revision) -> Observaciones:
    lectura = tabla_operacional("lectura_biometrica")
    ids_lectura = [fila["id_lectura"] for fila in dataset["lectura_biometrica"]]
    codigo_por_semaforo = {
        fila["id_semaforo"]: fila["codigo_nivel"] for fila in dataset["semaforo"]
    }

    conexion = engine_de_pruebas.connect()
    transaccion = conexion.begin()
    try:
        auditoria_antes = contar_auditoria(conexion)

        primera = cargar_dataset(conexion, dataset, revision=revision)
        segunda = cargar_dataset(conexion, dataset, revision=revision)

        datos = Observaciones(primera=primera, segunda=segunda)
        datos.auditoria_antes = auditoria_antes
        datos.auditoria_despues = contar_auditoria(conexion)
        datos.filas_por_tabla = contar_filas_del_dataset(conexion, dataset)

        datos.lecturas_hr = conexion.execute(
            select(func.count())
            .select_from(lectura)
            .where(lectura.c.id_lectura.in_(ids_lectura))
            .where(lectura.c.hr_valor.is_not(None))
        ).scalar_one()
        datos.lecturas_movimiento = conexion.execute(
            select(func.count())
            .select_from(lectura)
            .where(lectura.c.id_lectura.in_(ids_lectura))
            .where(lectura.c.mov_valor.is_not(None))
        ).scalar_one()

        datos.semaforo = {
            codigo_por_semaforo[id_semaforo]: cantidad
            for id_semaforo, cantidad in conexion.execute(
                select(lectura.c.id_semaforo, func.count())
                .where(lectura.c.id_lectura.in_(ids_lectura))
                .group_by(lectura.c.id_semaforo)
            ).all()
        }

        por_sesion = (
            select(lectura.c.id_sesion, func.count().label("cuantas"))
            .where(lectura.c.id_lectura.in_(ids_lectura))
            .group_by(lectura.c.id_sesion)
            .subquery()
        )
        datos.lecturas_por_sesion = {
            cuantas: sesiones
            for cuantas, sesiones in conexion.execute(
                select(por_sesion.c.cuantas, func.count()).group_by(
                    por_sesion.c.cuantas
                )
            ).all()
        }

        # Estado de las secuencias sin consumirlas: (máximo, próximo valor).
        for ajuste in primera.secuencias:
            tabla = tabla_operacional(ajuste.tabla)
            maximo = conexion.execute(
                select(func.max(tabla.c[ajuste.columna]))
            ).scalar_one()
            estado = leer_estado_de_secuencia(conexion, ajuste.secuencia)
            datos.secuencias[ajuste.tabla] = (int(maximo), proximo_valor(*estado))
    finally:
        transaccion.rollback()
        conexion.close()

    with engine_de_pruebas.connect() as verificacion:
        datos.filas_tras_rollback = contar_filas_del_dataset(verificacion, dataset)

    return datos


# --- Escenario 1: primera carga --------------------------------------------


def test_la_primera_carga_puebla_las_21_tablas(observaciones, dataset):
    assert len(observaciones.filas_por_tabla) == 21
    assert observaciones.filas_por_tabla == {
        nombre: len(dataset[nombre]) for nombre in ORDEN_DE_CARGA
    }
    assert sum(observaciones.filas_por_tabla.values()) == FILAS_ESPERADAS


def test_la_primera_carga_inserta_todas_las_filas_del_dataset(observaciones):
    assert observaciones.primera.insertados == FILAS_ESPERADAS
    assert observaciones.primera.existentes == 0


def test_la_primera_carga_deja_las_732_sesiones_del_dataset(observaciones):
    assert observaciones.primera.sesiones_verificadas == SESIONES_ESPERADAS
    assert observaciones.filas_por_tabla["sesion_monitoreo"] == SESIONES_ESPERADAS


def test_la_primera_carga_deja_las_1180_lecturas_del_dataset(observaciones):
    assert observaciones.primera.lecturas_verificadas == LECTURAS_ESPERADAS
    assert observaciones.filas_por_tabla["lectura_biometrica"] == LECTURAS_ESPERADAS


def test_la_distribucion_hr_spo2_y_movimiento_se_conserva(observaciones):
    assert observaciones.lecturas_hr == LECTURAS_HR_ESPERADAS
    assert observaciones.lecturas_movimiento == LECTURAS_MOVIMIENTO_ESPERADAS
    assert (
        observaciones.lecturas_hr + observaciones.lecturas_movimiento
        == LECTURAS_ESPERADAS
    )


def test_la_distribucion_del_semaforo_se_conserva(observaciones):
    assert observaciones.semaforo == SEMAFORO_ESPERADO


def test_la_relacion_sesion_lecturas_es_1_a_n(observaciones):
    """112 sesiones con cinco lecturas y 620 con una: el 1:N es real en el servidor."""
    assert observaciones.lecturas_por_sesion == LECTURAS_POR_SESION_ESPERADAS
    assert sum(observaciones.lecturas_por_sesion.values()) == SESIONES_ESPERADAS


def test_auditoria_log_no_recibe_ninguna_fila(observaciones):
    assert observaciones.auditoria_despues == observaciones.auditoria_antes


# --- Escenario 2: segunda carga --------------------------------------------


def test_la_segunda_carga_no_inserta_ninguna_fila(observaciones):
    assert observaciones.segunda.insertados == 0


def test_la_segunda_carga_reporta_todo_como_existente_sin_cambios(observaciones):
    assert observaciones.segunda.existentes == FILAS_ESPERADAS
    assert all(
        tabla.insertados == 0 and tabla.existentes == tabla.total
        for tabla in observaciones.segunda.tablas
    )


def test_la_segunda_carga_no_cambia_ningun_conteo(observaciones, dataset):
    assert observaciones.segunda.sesiones_verificadas == SESIONES_ESPERADAS
    assert observaciones.segunda.lecturas_verificadas == LECTURAS_ESPERADAS
    assert observaciones.filas_por_tabla == {
        nombre: len(dataset[nombre]) for nombre in ORDEN_DE_CARGA
    }


def test_la_segunda_carga_tampoco_mueve_las_secuencias(observaciones):
    assert observaciones.segunda.secuencias_ajustadas == 0


# --- Escenario 5: secuencias sin consumirlas -------------------------------


def test_cada_pk_autoincremental_queda_con_su_secuencia_alineada(observaciones):
    """El próximo id automático supera el máximo cargado, sin gastar la secuencia."""
    assert observaciones.secuencias

    for tabla, (maximo, proximo) in observaciones.secuencias.items():
        assert proximo > maximo, f"la secuencia de '{tabla}' quedó por detrás"


def test_las_tablas_de_pk_compuesta_o_no_autoincremental_no_se_ajustan(observaciones):
    ajustadas = {ajuste.tabla for ajuste in observaciones.primera.secuencias}

    assert ajustadas.isdisjoint(
        {"medico_clinica", "embarazo_factor_riesgo", "usuario_medico", "usuario_paciente"}
    )
    assert "auditoria_log" not in ajustadas
    assert len(ajustadas) == 17


# --- Aislamiento -----------------------------------------------------------


def test_la_base_no_conserva_filas_del_dataset_tras_el_rollback(observaciones):
    assert observaciones.filas_tras_rollback == dict.fromkeys(ORDEN_DE_CARGA, 0)


# ---------------------------------------------------------------------------
# Escenario 3: conflicto de contenido bajo la misma llave primaria
# ---------------------------------------------------------------------------


@pytest.fixture
def dataset_con_conflicto(dataset_crudo) -> DatasetNormalizado:
    """Copia del dataset con una PK existente y contenido distinto."""
    alterado = copy.deepcopy(dataset_crudo)
    alterado["clinicas"][0]["nombre_clinica"] = "Clínica Rural Simulada RENOMBRADA"
    return validar_dataset(alterado)


def test_una_pk_existente_con_contenido_distinto_aborta_la_carga(
    transaccion_revertida, dataset, dataset_con_conflicto, revision
):
    cargar_dataset(transaccion_revertida, dataset, revision=revision)

    with pytest.raises(ConflictoDeDatos):
        cargar_dataset(transaccion_revertida, dataset_con_conflicto, revision=revision)


def test_el_conflicto_identifica_la_tabla_la_pk_y_los_campos(
    transaccion_revertida, dataset, dataset_con_conflicto, revision
):
    cargar_dataset(transaccion_revertida, dataset, revision=revision)

    with pytest.raises(ConflictoDeDatos) as error:
        cargar_dataset(transaccion_revertida, dataset_con_conflicto, revision=revision)

    mensaje = str(error.value)
    assert "clinica" in mensaje
    assert "id_clinica" in mensaje
    assert "nombre_clinica" in mensaje
    # El valor divergente no se imprime: solo el nombre del campo.
    assert "RENOMBRADA" not in mensaje


def test_el_conflicto_revierte_la_transaccion_completa(
    engine_de_pruebas, dataset, dataset_con_conflicto, revision
):
    conexion = engine_de_pruebas.connect()
    transaccion = conexion.begin()
    try:
        cargar_dataset(conexion, dataset, revision=revision)
        assert contar_del_dataset(conexion, "clinica", dataset["clinica"]) == 3
        with pytest.raises(ConflictoDeDatos):
            cargar_dataset(conexion, dataset_con_conflicto, revision=revision)
    finally:
        transaccion.rollback()
        conexion.close()

    with engine_de_pruebas.connect() as verificacion:
        assert contar_filas_del_dataset(verificacion, dataset) == dict.fromkeys(
            ORDEN_DE_CARGA, 0
        )


# ---------------------------------------------------------------------------
# Escenario 4a: violación UNIQUE distinta de la llave primaria
# ---------------------------------------------------------------------------


@pytest.fixture
def dataset_con_ruc_duplicado(dataset_crudo) -> DatasetNormalizado:
    """Dos clínicas con distinta PK y el mismo RUC.

    La validación previa comprueba llaves primarias, no columnas UNIQUE: este
    caso llega a PostgreSQL a propósito, porque la base sigue siendo la
    autoridad final y un ``ON CONFLICT DO NOTHING`` lo habría ocultado.
    """
    alterado = copy.deepcopy(dataset_crudo)
    clon = copy.deepcopy(alterado["clinicas"][0])
    clon["id_clinica"] = 999
    alterado["clinicas"].append(clon)
    return validar_dataset(alterado)


def test_un_ruc_duplicado_con_otra_pk_es_rechazado_por_postgresql(
    transaccion_revertida, dataset_con_ruc_duplicado, revision
):
    with pytest.raises(IntegrityError) as error:
        cargar_dataset(
            transaccion_revertida, dataset_con_ruc_duplicado, revision=revision
        )

    assert "uq_clinica_ruc" in str(error.value)


def test_la_violacion_unique_revierte_la_transaccion_completa(
    engine_de_pruebas, dataset, dataset_con_ruc_duplicado, revision
):
    """El fallo ocurre en la sexta tabla: las cinco anteriores ya estaban escritas."""
    conexion = engine_de_pruebas.connect()
    transaccion = conexion.begin()
    try:
        with pytest.raises(IntegrityError):
            cargar_dataset(conexion, dataset_con_ruc_duplicado, revision=revision)
    finally:
        transaccion.rollback()
        conexion.close()

    with engine_de_pruebas.connect() as verificacion:
        residuales = contar_filas_del_dataset(verificacion, dataset)

    assert residuales == dict.fromkeys(ORDEN_DE_CARGA, 0)
    assert residuales["especialidad"] == 0
    assert residuales["rol"] == 0


# ---------------------------------------------------------------------------
# Escenario 4b: violación CHECK
# ---------------------------------------------------------------------------


@pytest.fixture
def dataset_con_spo2_fuera_de_rango(dataset_crudo) -> DatasetNormalizado:
    """Una lectura con SpO2 = 150, que viola ck_lectura_biometrica_spo2_rango."""
    alterado = copy.deepcopy(dataset_crudo)
    for fila in alterado["lecturas_biometricas"]:
        if fila["spo2_valor"] is not None:
            fila["spo2_valor"] = 150
            break
    return validar_dataset(alterado)


def test_un_spo2_fuera_de_rango_es_rechazado_por_postgresql(
    transaccion_revertida, dataset_con_spo2_fuera_de_rango, revision
):
    with pytest.raises(IntegrityError) as error:
        cargar_dataset(
            transaccion_revertida, dataset_con_spo2_fuera_de_rango, revision=revision
        )

    assert "spo2_rango" in str(error.value)


def test_la_violacion_check_revierte_la_transaccion_completa(
    engine_de_pruebas, dataset, dataset_con_spo2_fuera_de_rango, revision
):
    """El fallo ocurre en la penúltima tabla: 18 tablas ya estaban escritas."""
    conexion = engine_de_pruebas.connect()
    transaccion = conexion.begin()
    try:
        with pytest.raises(IntegrityError):
            cargar_dataset(conexion, dataset_con_spo2_fuera_de_rango, revision=revision)
    finally:
        transaccion.rollback()
        conexion.close()

    with engine_de_pruebas.connect() as verificacion:
        residuales = contar_filas_del_dataset(verificacion, dataset)

    assert residuales == dict.fromkeys(ORDEN_DE_CARGA, 0)
    assert residuales["clinica"] == 0
    assert residuales["sesion_monitoreo"] == 0


# ---------------------------------------------------------------------------
# Escenario 5b: nextval real sobre una secuencia del esquema
# ---------------------------------------------------------------------------


@pytest.fixture
def secuencia_vigilada(engine_de_pruebas):
    """Instantánea de una secuencia real que se restaura siempre.

    Las pruebas que usan esta fixture consumen ``nextval``, y ``nextval`` no es
    transaccional por naturaleza: aunque la evidencia muestra que un
    ``ALTER SEQUENCE ... RESTART`` previo hace que el rollback también deshaga
    el ``nextval``, la limpieza no se apoya en ese comportamiento. El
    ``try/finally`` restaura el estado exacto con ``setval`` pase lo que pase, y
    falla de forma explícita si la restauración no funciona.

    No debe ejecutarse en paralelo contra la misma base.
    """
    tabla = tabla_operacional(TABLA_CON_SECUENCIA)
    columna = tabla.autoincrement_column

    with engine_de_pruebas.connect() as conexion:
        ubicacion = secuencia_de_tabla(conexion, tabla, columna)
        assert ubicacion is not None, (
            f"'{TABLA_CON_SECUENCIA}' debería tener una secuencia asociada"
        )
        secuencia = citar_secuencia(conexion, *ubicacion)
        previo = leer_estado_de_secuencia(conexion, secuencia)

    try:
        yield secuencia, previo
    finally:
        with engine_de_pruebas.begin() as conexion:
            conexion.execute(
                text(
                    "SELECT setval(CAST(:secuencia AS regclass), :valor, :llamada)"
                ),
                {"secuencia": secuencia, "valor": previo[0], "llamada": previo[1]},
            )
        with engine_de_pruebas.connect() as conexion:
            restaurado = leer_estado_de_secuencia(conexion, secuencia)
        if restaurado != previo:
            pytest.fail(
                f"No se pudo restaurar {secuencia}: quedó en {restaurado} y estaba "
                f"en {previo}."
            )


def test_el_siguiente_id_automatico_es_mayor_que_el_maximo(
    engine_de_pruebas, dataset, secuencia_vigilada, revision
):
    secuencia, _ = secuencia_vigilada
    tabla = tabla_operacional(TABLA_CON_SECUENCIA)

    conexion = engine_de_pruebas.connect()
    transaccion = conexion.begin()
    try:
        cargar_dataset(conexion, dataset, revision=revision)
        maximo = conexion.execute(
            select(func.max(tabla.c[tabla.autoincrement_column.name]))
        ).scalar_one()
        siguiente = conexion.execute(
            text("SELECT nextval(CAST(:secuencia AS regclass))"),
            {"secuencia": secuencia},
        ).scalar_one()

        assert siguiente > maximo
    finally:
        transaccion.rollback()
        conexion.close()


def test_el_rollback_deja_la_secuencia_como_estaba(
    engine_de_pruebas, dataset, secuencia_vigilada, revision
):
    """El ajuste de secuencias también se revierte: no queda ningún cambio."""
    secuencia, previo = secuencia_vigilada

    conexion = engine_de_pruebas.connect()
    transaccion = conexion.begin()
    try:
        cargar_dataset(conexion, dataset, revision=revision)
        conexion.execute(
            text("SELECT nextval(CAST(:secuencia AS regclass))"),
            {"secuencia": secuencia},
        )
    finally:
        transaccion.rollback()
        conexion.close()

    with engine_de_pruebas.connect() as verificacion:
        assert leer_estado_de_secuencia(verificacion, secuencia) == previo


def test_una_secuencia_ya_adelantada_no_se_reduce(
    engine_de_pruebas, dataset, secuencia_vigilada, revision
):
    secuencia, _ = secuencia_vigilada
    adelantada = 999_999

    conexion = engine_de_pruebas.connect()
    transaccion = conexion.begin()
    try:
        conexion.execute(text(f"ALTER SEQUENCE {secuencia} RESTART WITH {adelantada}"))

        resultado = cargar_dataset(conexion, dataset, revision=revision)
        ajuste = next(
            a for a in resultado.secuencias if a.tabla == TABLA_CON_SECUENCIA
        )

        assert ajuste.ajustada is False
        assert ajuste.proximo_antes == adelantada
        assert ajuste.proximo_despues == adelantada
        assert proximo_valor(*leer_estado_de_secuencia(conexion, secuencia)) == (
            adelantada
        )
    finally:
        transaccion.rollback()
        conexion.close()
