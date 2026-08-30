"""Reading, validation and normalisation of the simulated FetalAlert dataset.

Nothing in this module opens a database connection. It turns the JSON produced
by ``scripts/generate_mock_data.py`` into rows that already carry the Python
type every SQLAlchemy column expects, and refuses anything that does not match
the approved sample.

``Base.metadata`` is the only authority on columns and types: there is no second
hand-written description of the tables that could drift away from the models.

Validation is deliberately *not* a re-implementation of the schema. PostgreSQL
remains the final authority on CHECK, UNIQUE and foreign keys; what happens here
is the smaller job of turning an unusable input file into a comprehensible error
before a single row is written.
"""

from __future__ import annotations

import enum
import json
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Table,
)
from sqlalchemy import Enum as TipoEnum

import app.models  # noqa: F401  -- registers every model on Base.metadata
from app.db.base import SCHEMA_OPERACIONAL, Base

# ---------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------

# Every loader failure is declared here, in the module that depends on nothing
# else inside the package, so ``postgres.py`` can raise them without an import
# cycle and the CLI can catch the whole family with a single ``except``.


class ErrorDeCarga(Exception):
    """Base of every failure the loader reports with a clean message."""


class DatasetInvalido(ErrorDeCarga):
    """The JSON file does not match the approved simulated dataset."""


class AmbienteNoPermitido(ErrorDeCarga):
    """``APP_ENV`` names an environment where the loader must not run."""


class MotorNoSoportado(ErrorDeCarga):
    """The target is not a PostgreSQL server."""


class EsquemaDesactualizado(ErrorDeCarga):
    """The database is not deployed at the current Alembic head."""


class ConflictoDeDatos(ErrorDeCarga):
    """A primary key already exists in the database carrying different values."""


# ---------------------------------------------------------------------------
# Constantes del dataset aprobado
# ---------------------------------------------------------------------------

RAIZ_DEL_REPOSITORIO = Path(__file__).resolve().parents[3]
RUTA_POR_DEFECTO = RAIZ_DEL_REPOSITORIO / "data" / "generated" / "dataset_fetalalert.json"

COMANDO_DEL_GENERADOR = "python scripts/generate_mock_data.py"

# The five metadata fields approved in SCRUM-54. Pinning them is what tells an
# unapproved or truncated sample apart from the one this loader is meant for.
METADATA_APROBADA: dict[str, Any] = {
    "nombre": "Dataset simulado FetalAlert",
    "semilla": 20260810,
    "total_sesiones": 732,
    "total_registros_biometricos": 1180,
    "uso": "Validación técnica y funcional",
}

# JSON section -> physical table. 21 of the 22 operational tables: auditoria_log
# is absent on purpose, its rows come from real actions and are never seeded.
MAPA_SECCIONES: dict[str, str] = {
    "especialidades": "especialidad",
    "factores_riesgo": "factor_riesgo",
    "tiempo_gestacional": "tiempo_gestacional",
    "semaforos": "semaforo",
    "roles": "rol",
    "clinicas": "clinica",
    "pacientes": "paciente",
    "medicos": "medico",
    "dispositivos": "dispositivo",
    "usuarios": "usuario",
    "telefonos_paciente": "telefono_paciente",
    "telefonos_medico": "telefono_medico",
    "medico_clinica": "medico_clinica",
    "embarazos": "embarazo",
    "seguimiento_clinico": "seguimiento_clinico",
    "embarazo_factor_riesgo": "embarazo_factor_riesgo",
    "asignacion_dispositivo": "asignacion_dispositivo",
    "sesiones_monitoreo": "sesion_monitoreo",
    "lecturas_biometricas": "lectura_biometrica",
    "usuario_medico": "usuario_medico",
    "usuario_paciente": "usuario_paciente",
}

SECCION_POR_TABLA: dict[str, str] = {
    tabla: seccion for seccion, tabla in MAPA_SECCIONES.items()
}

# Present in the JSON but never loaded: the two administrators it lists are
# already part of ``usuarios``, so inserting it separately would duplicate them.
SECCIONES_INFORMATIVAS = frozenset({"usuarios_administradores"})

# Foreign-key order. ``test_load_mock_data`` derives the dependencies from
# Base.metadata and fails if this tuple ever contradicts them.
ORDEN_DE_CARGA: tuple[str, ...] = (
    # Catálogos
    "especialidad",
    "factor_riesgo",
    "tiempo_gestacional",
    "semaforo",
    "rol",
    # Entidades raíz
    "clinica",
    "paciente",
    # Dependientes iniciales
    "medico",
    "dispositivo",
    "usuario",
    # Contactos y afiliaciones
    "telefono_paciente",
    "telefono_medico",
    "medico_clinica",
    # Embarazos
    "embarazo",
    # Relaciones del embarazo
    "seguimiento_clinico",
    "embarazo_factor_riesgo",
    "asignacion_dispositivo",
    # Monitoreo
    "sesion_monitoreo",
    "lectura_biometrica",
    # Relaciones de usuarios
    "usuario_medico",
    "usuario_paciente",
)

TABLA_DE_SESIONES = "sesion_monitoreo"
TABLA_DE_LECTURAS = "lectura_biometrica"
TABLA_DE_TIEMPO_GESTACIONAL = "tiempo_gestacional"

# Fetal movement is only clinically meaningful from week 20 onwards. The rule
# spans two tables, so no row-level CHECK can express it: it is validated here.
SEMANA_MINIMA_DE_MOVIMIENTO = 20

# A validated dataset: physical table name -> rows with Python-typed values.
DatasetNormalizado = dict[str, list[dict[str, Any]]]


def tabla_operacional(nombre: str) -> Table:
    """The SQLAlchemy ``Table`` behind a physical table name."""
    return Base.metadata.tables[f"{SCHEMA_OPERACIONAL}.{nombre}"]


def clave_primaria(tabla: Table) -> tuple[str, ...]:
    """Names of the columns forming the primary key, in declaration order."""
    return tuple(columna.name for columna in tabla.primary_key.columns)


# ---------------------------------------------------------------------------
# Lectura del archivo
# ---------------------------------------------------------------------------


def leer_dataset(ruta: Path) -> dict[str, Any]:
    """Parse the dataset file, or explain how to produce it."""
    ruta = Path(ruta)
    try:
        contenido = ruta.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise DatasetInvalido(
            f"No existe el archivo del dataset simulado: {ruta}. "
            f"Genéralo primero con: {COMANDO_DEL_GENERADOR}"
        ) from error
    except OSError as error:
        raise DatasetInvalido(
            f"No se pudo leer el archivo del dataset simulado: {ruta} ({error.strerror})."
        ) from error

    try:
        dataset = json.loads(contenido)
    except json.JSONDecodeError as error:
        raise DatasetInvalido(
            f"El archivo {ruta} no es JSON válido (línea {error.lineno}, "
            f"columna {error.colno}): {error.msg}."
        ) from error

    if not isinstance(dataset, dict):
        raise DatasetInvalido(
            f"El archivo {ruta} debe contener un objeto JSON en la raíz, "
            f"no {type(dataset).__name__}."
        )

    return dataset


# ---------------------------------------------------------------------------
# Conversión de tipos
# ---------------------------------------------------------------------------


def _a_datetime(valor: Any) -> datetime:
    if isinstance(valor, datetime):
        return valor
    if isinstance(valor, str):
        try:
            return datetime.fromisoformat(valor)
        except ValueError as error:
            raise DatasetInvalido(
                f"no es una marca de tiempo ISO válida: {error}"
            ) from error
    raise DatasetInvalido(
        f"se esperaba una marca de tiempo y llegó {type(valor).__name__}"
    )


def _a_date(valor: Any) -> date:
    if isinstance(valor, datetime):
        raise DatasetInvalido(
            "se esperaba una fecha sin hora y llegó una marca de tiempo completa"
        )
    if isinstance(valor, date):
        return valor
    if isinstance(valor, str):
        try:
            return date.fromisoformat(valor)
        except ValueError as error:
            raise DatasetInvalido(f"no es una fecha ISO válida: {error}") from error
    raise DatasetInvalido(f"se esperaba una fecha y llegó {type(valor).__name__}")


def _a_decimal(valor: Any) -> Decimal:
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, bool):
        raise DatasetInvalido("se esperaba un número y llegó un booleano")
    if isinstance(valor, (int, float, str)):
        try:
            return Decimal(str(valor))
        except InvalidOperation as error:
            raise DatasetInvalido(
                f"no es un número decimal válido: {valor!r}"
            ) from error
    raise DatasetInvalido(f"se esperaba un número y llegó {type(valor).__name__}")


def normalizar_valor(columna: Column, valor: Any) -> Any:
    """Canonical Python value for ``columna``, from JSON or from the server.

    Both sides of the idempotency comparison go through this same function, so
    ``97`` from the file and ``Decimal("97.00")`` from PostgreSQL end up equal,
    and a timestamp keeps its instant whichever offset it was written with.

    ``None`` stays ``None``: a metric that does not apply is never turned into
    zero or an empty string.
    """
    if valor is None:
        return None

    tipo = columna.type

    # Enum first: sqlalchemy.Enum is a String subclass.
    if isinstance(tipo, TipoEnum):
        if isinstance(valor, enum.Enum):
            return valor.value
        if isinstance(valor, str):
            return valor
        raise DatasetInvalido(
            f"se esperaba un valor de enum como texto y llegó {type(valor).__name__}"
        )

    if isinstance(tipo, DateTime):
        momento = _a_datetime(valor)
        if momento.tzinfo is None:
            raise DatasetInvalido(
                "se esperaba una marca de tiempo con zona horaria y llegó una sin offset"
            )
        return momento.astimezone(timezone.utc)

    if isinstance(tipo, Date):
        return _a_date(valor)

    if isinstance(tipo, Numeric):
        return _a_decimal(valor)

    # Boolean before Integer: PostgreSQL returns real booleans, and the dataset
    # must not smuggle in "true" as a string.
    if isinstance(tipo, Boolean):
        if not isinstance(valor, bool):
            raise DatasetInvalido(
                f"se esperaba un booleano y llegó {type(valor).__name__}"
            )
        return valor

    if isinstance(tipo, Integer):
        if isinstance(valor, bool) or not isinstance(valor, int):
            raise DatasetInvalido(f"se esperaba un entero y llegó {type(valor).__name__}")
        return valor

    if isinstance(tipo, String):
        if not isinstance(valor, str):
            raise DatasetInvalido(f"se esperaba texto y llegó {type(valor).__name__}")
        return valor

    raise DatasetInvalido(f"tipo de columna no contemplado: {tipo!r}")


def normalizar_fila(tabla: Table, fila: dict[str, Any]) -> dict[str, Any]:
    """Normalise every column of one row, naming the column that fails."""
    normalizada: dict[str, Any] = {}
    for columna in tabla.columns:
        try:
            normalizada[columna.name] = normalizar_valor(columna, fila[columna.name])
        except DatasetInvalido as error:
            raise DatasetInvalido(f"columna '{columna.name}': {error}") from error
    return normalizada


# ---------------------------------------------------------------------------
# Validación
# ---------------------------------------------------------------------------


def _validar_metadata(dataset: dict[str, Any]) -> None:
    metadata = dataset.get("metadata")
    if metadata is None:
        raise DatasetInvalido(
            "El dataset no tiene sección 'metadata'; no se puede confirmar que sea "
            "la muestra aprobada."
        )
    if not isinstance(metadata, dict):
        raise DatasetInvalido(
            f"'metadata' debe ser un objeto y llegó {type(metadata).__name__}."
        )

    for campo, esperado in METADATA_APROBADA.items():
        if campo not in metadata:
            raise DatasetInvalido(f"'metadata' no declara el campo '{campo}'.")
        if metadata[campo] != esperado:
            raise DatasetInvalido(
                f"'metadata.{campo}' es {metadata[campo]!r} y el dataset aprobado "
                f"declara {esperado!r}. Regenera la muestra con: "
                f"{COMANDO_DEL_GENERADOR}"
            )


def _validar_secciones(dataset: dict[str, Any]) -> None:
    for seccion in MAPA_SECCIONES:
        if seccion not in dataset:
            raise DatasetInvalido(f"Falta la colección obligatoria '{seccion}'.")
        filas = dataset[seccion]
        if not isinstance(filas, list):
            raise DatasetInvalido(
                f"La colección '{seccion}' debe ser una lista y llegó "
                f"{type(filas).__name__}."
            )
        for indice, fila in enumerate(filas):
            if not isinstance(fila, dict):
                raise DatasetInvalido(
                    f"'{seccion}[{indice}]' debe ser un objeto y llegó "
                    f"{type(fila).__name__}."
                )


def _validar_coherencia_de_totales(dataset: dict[str, Any]) -> None:
    """The declared totals must match what the collections actually carry."""
    declarados = {
        "total_sesiones": (SECCION_POR_TABLA[TABLA_DE_SESIONES], "sesiones"),
        "total_registros_biometricos": (
            SECCION_POR_TABLA[TABLA_DE_LECTURAS],
            "lecturas",
        ),
    }
    for campo, (seccion, etiqueta) in declarados.items():
        real = len(dataset[seccion])
        if dataset["metadata"][campo] != real:
            raise DatasetInvalido(
                f"'metadata.{campo}' declara {dataset['metadata'][campo]} {etiqueta} "
                f"pero '{seccion}' trae {real}."
            )


def _validar_columnas(seccion: str, tabla: Table, filas: list[dict[str, Any]]) -> None:
    esperadas = {columna.name for columna in tabla.columns}
    for indice, fila in enumerate(filas):
        presentes = set(fila)
        faltantes = esperadas - presentes
        if faltantes:
            raise DatasetInvalido(
                f"'{seccion}[{indice}]' no trae la(s) columna(s) obligatoria(s) "
                f"{sorted(faltantes)} de la tabla '{tabla.name}'."
            )
        sobrantes = presentes - esperadas
        if sobrantes:
            raise DatasetInvalido(
                f"'{seccion}[{indice}]' trae campo(s) {sorted(sobrantes)} que no "
                f"existen en la tabla '{tabla.name}'."
            )


def _validar_pk_unicas(seccion: str, tabla: Table, filas: list[dict[str, Any]]) -> None:
    columnas = clave_primaria(tabla)
    conteo = Counter(tuple(fila[columna] for columna in columnas) for fila in filas)
    repetidas = sorted(clave for clave, veces in conteo.items() if veces > 1)
    if repetidas:
        raise DatasetInvalido(
            f"La colección '{seccion}' repite la(s) llave(s) primaria(s) "
            f"{repetidas[:5]} de la tabla '{tabla.name}'."
        )


def _validar_lecturas(normalizado: DatasetNormalizado) -> None:
    """Biometric shape, synchronisation order and the week-20 rule."""
    semana_por_tiempo = {
        fila["id_tiempo_gest"]: fila["semana_gestacion"]
        for fila in normalizado[TABLA_DE_TIEMPO_GESTACIONAL]
    }

    for fila in normalizado[TABLA_DE_LECTURAS]:
        identificador = fila["id_lectura"]
        hr = fila["hr_valor"]
        spo2 = fila["spo2_valor"]
        movimiento = fila["mov_valor"]

        signos_maternos = hr is not None and spo2 is not None and movimiento is None
        movimiento_fetal = movimiento is not None and hr is None and spo2 is None
        if not (signos_maternos or movimiento_fetal):
            raise DatasetInvalido(
                f"lectura_biometrica[id_lectura={identificador}] no tiene una forma "
                "válida: debe traer hr_valor y spo2_valor con mov_valor en NULL, o "
                "mov_valor con hr_valor y spo2_valor en NULL."
            )

        captura = fila["fecha_hora_captura"]
        sincronizacion = fila["fecha_hora_sincronizacion"]
        if sincronizacion is not None and sincronizacion < captura:
            raise DatasetInvalido(
                f"lectura_biometrica[id_lectura={identificador}] se sincroniza antes "
                "de haber sido capturada."
            )

        if movimiento_fetal:
            semana = semana_por_tiempo.get(fila["id_tiempo_gest"])
            if semana is not None and semana < SEMANA_MINIMA_DE_MOVIMIENTO:
                raise DatasetInvalido(
                    f"lectura_biometrica[id_lectura={identificador}] registra "
                    f"movimiento fetal en la semana {semana}; no existen movimientos "
                    f"antes de la semana {SEMANA_MINIMA_DE_MOVIMIENTO}."
                )


def _validar_referencias(normalizado: DatasetNormalizado) -> None:
    """Every foreign key in the file must point at a row the file also carries.

    The relationships are read off ``Base.metadata`` instead of being listed by
    hand, so a new foreign key in the models is checked here for free.
    """
    for nombre in ORDEN_DE_CARGA:
        tabla = tabla_operacional(nombre)
        for llave in sorted(tabla.foreign_keys, key=lambda fk: fk.parent.name):
            destino = llave.column.table.name
            if destino not in normalizado:
                continue
            columna = llave.parent.name
            columna_destino = llave.column.name
            disponibles = {fila[columna_destino] for fila in normalizado[destino]}
            for fila in normalizado[nombre]:
                valor = fila[columna]
                if valor is not None and valor not in disponibles:
                    raise DatasetInvalido(
                        f"{nombre}.{columna} = {valor!r} no corresponde a ningún "
                        f"{destino}.{columna_destino} presente en el dataset."
                    )


def validar_dataset(dataset: dict[str, Any]) -> DatasetNormalizado:
    """Validate the whole file and return its rows ready to be inserted.

    Runs before any transaction is opened, so an unusable file never reaches the
    database. The returned mapping is keyed by physical table name and ordered
    by ``ORDEN_DE_CARGA``.
    """
    _validar_metadata(dataset)
    _validar_secciones(dataset)
    _validar_coherencia_de_totales(dataset)

    normalizado: DatasetNormalizado = {}
    for nombre in ORDEN_DE_CARGA:
        seccion = SECCION_POR_TABLA[nombre]
        tabla = tabla_operacional(nombre)
        filas = dataset[seccion]

        _validar_columnas(seccion, tabla, filas)
        _validar_pk_unicas(seccion, tabla, filas)

        normalizadas = []
        for indice, fila in enumerate(filas):
            try:
                normalizadas.append(normalizar_fila(tabla, fila))
            except DatasetInvalido as error:
                raise DatasetInvalido(f"'{seccion}[{indice}]': {error}") from error
        normalizado[nombre] = normalizadas

    _validar_lecturas(normalizado)
    _validar_referencias(normalizado)

    return normalizado
