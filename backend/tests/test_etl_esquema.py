"""Offline contract of the analytic star schema and of its Alembic revision.

Nothing here opens a database connection. The metadata of the nine structures
is compared against the approved model, and the revision is rendered to
PostgreSQL SQL the way ``alembic upgrade --sql`` would, then compared against
that metadata. The round trip against a real server lives in
``test_etl_postgresql.py``.

This is a contract of its own: the 23 operational tables keep theirs in
``test_models.py`` and ``test_migrations.py``, untouched by anything here.
"""

import io
import re

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

import app.etl.modelos  # noqa: F401  -- registers the analytic tables
import app.models  # noqa: F401  -- registers the operational tables
from app.db.base import SCHEMA_OPERACIONAL, Base
from app.db.base_analitica import SCHEMA_ANALITICO, BaseAnalitica
from app.etl.modelos import TABLAS_ANALITICAS
from tests.conftest import construir_config_alembic
from tests.test_migrations import _dividir_en_clausulas
from tests.test_models import PG_MAX_IDENTIFIER_LENGTH, TABLAS_ESPERADAS

REVISION_ANALITICA = "60facdbacf51"
REVISION_SCRUM_63 = "87d8ed46686b"

# Logical names of the Draw.io v6 -> physical snake_case names.
TABLAS_DEL_MODELO = {
    "Fact_LecturaBiometrica": "fact_lectura_biometrica",
    "Dim_TiempoGestacional": "dim_tiempo_gestacional",
    "Dim_Semaforo": "dim_semaforo",
    "Dim_Medico": "dim_medico",
    "Dim_Paciente": "dim_paciente",
    "Dim_Clinica": "dim_clinica",
    "Dim_FactorRiesgo": "dim_factor_riesgo",
    "Bridge_EmbarazoFactorRiesgo": "bridge_embarazo_factor_riesgo",
    "Dim_Embarazo": "dim_embarazo",
}

# column -> (PostgreSQL type, nullable). Written by hand from the approved model
# and the physical corrections of the design, not read back from the models.
COLUMNAS_ESPERADAS = {
    "fact_lectura_biometrica": {
        "id_lectura": ("BIGINT", False),
        "id_sesion": ("INTEGER", False),
        "id_paciente": ("INTEGER", False),
        "id_medico": ("INTEGER", False),
        "id_clinica": ("INTEGER", False),
        "id_tiempo_gestacional": ("INTEGER", False),
        "id_embarazo": ("INTEGER", False),
        "id_semaforo": ("INTEGER", False),
        "hr_valor": ("NUMERIC(5, 2)", True),
        "spo2_valor": ("NUMERIC(5, 2)", True),
        "mov_valor": ("INTEGER", True),
        "estado_hr": ("VARCHAR(10)", True),
        "estado_spo2": ("VARCHAR(10)", True),
        "estado_mov": ("VARCHAR(10)", True),
        "fecha_hora": ("TIMESTAMP WITH TIME ZONE", False),
    },
    "dim_tiempo_gestacional": {
        "id_tiempo_gest": ("INTEGER", False),
        "semana_gestacion": ("INTEGER", False),
        "mes_gestacion": ("INTEGER", False),
        "trimestre": ("INTEGER", False),
        "descripcion": ("TEXT", True),
    },
    "dim_semaforo": {
        "id_semaforo": ("INTEGER", False),
        "codigo_nivel": ("VARCHAR(7)", False),
        "etiqueta_visual": ("VARCHAR(60)", False),
        "color_hex": ("VARCHAR(7)", False),
        "prioridad": ("INTEGER", False),
        "mensaje_app": ("VARCHAR(255)", False),
        "version_referencia": ("VARCHAR(30)", False),
    },
    "dim_medico": {
        "id_medico": ("INTEGER", False),
        "id_clinica": ("INTEGER", True),
        "nombre_completo": ("VARCHAR(243)", False),
        "especialidad": ("VARCHAR(100)", False),
        "email_med": ("VARCHAR(120)", False),
        "telefono_med": ("VARCHAR(120)", True),
    },
    "dim_paciente": {
        "id_paciente": ("INTEGER", False),
        "id_clinica": ("INTEGER", True),
        "cedula": ("VARCHAR(20)", False),
        "nombre_completo": ("VARCHAR(243)", False),
        "telefono_pac": ("VARCHAR(120)", True),
        "fecha_nac": ("DATE", False),
    },
    "dim_clinica": {
        "id_clinica": ("INTEGER", False),
        "nombre_clinica": ("VARCHAR(150)", False),
        "provincia": ("VARCHAR(60)", False),
        "distrito": ("VARCHAR(60)", False),
    },
    "dim_factor_riesgo": {
        "id_factor_riesgo": ("INTEGER", False),
        "clave_factor": ("VARCHAR(50)", False),
        "nombre_factor": ("VARCHAR(150)", False),
        "descripcion": ("TEXT", True),
        "activo": ("BOOLEAN", False),
    },
    "bridge_embarazo_factor_riesgo": {
        "id_embarazo": ("INTEGER", False),
        "id_factor_riesgo": ("INTEGER", False),
        "fecha_diagnostico": ("DATE", False),
        "activo": ("BOOLEAN", False),
        "observaciones": ("TEXT", True),
    },
    "dim_embarazo": {
        "id_embarazo": ("INTEGER", False),
        "id_paciente": ("INTEGER", False),
        "numero_gestas": ("INTEGER", False),
        "numero_partos": ("INTEGER", False),
        "estado_embarazo": ("VARCHAR(20)", False),
        "fecha_inicio": ("DATE", False),
        "fecha_probable_parto": ("DATE", False),
        "fecha_cierre": ("DATE", True),
        "duracion_est_semanas": ("INTEGER", False),
        "clasificacion_embarazo": ("VARCHAR(20)", True),
    },
}

LLAVES_PRIMARIAS_ESPERADAS = {
    "fact_lectura_biometrica": ("id_lectura",),
    "dim_tiempo_gestacional": ("id_tiempo_gest",),
    "dim_semaforo": ("id_semaforo",),
    "dim_medico": ("id_medico",),
    "dim_paciente": ("id_paciente",),
    "dim_clinica": ("id_clinica",),
    "dim_factor_riesgo": ("id_factor_riesgo",),
    "bridge_embarazo_factor_riesgo": ("id_embarazo", "id_factor_riesgo"),
    "dim_embarazo": ("id_embarazo",),
}

# (table, column) -> (referred table, referred column), all inside analitico.
LLAVES_FORANEAS_ESPERADAS = {
    ("fact_lectura_biometrica", "id_paciente"): ("dim_paciente", "id_paciente"),
    ("fact_lectura_biometrica", "id_medico"): ("dim_medico", "id_medico"),
    ("fact_lectura_biometrica", "id_clinica"): ("dim_clinica", "id_clinica"),
    ("fact_lectura_biometrica", "id_tiempo_gestacional"): (
        "dim_tiempo_gestacional",
        "id_tiempo_gest",
    ),
    ("fact_lectura_biometrica", "id_embarazo"): ("dim_embarazo", "id_embarazo"),
    ("fact_lectura_biometrica", "id_semaforo"): ("dim_semaforo", "id_semaforo"),
    ("bridge_embarazo_factor_riesgo", "id_embarazo"): ("dim_embarazo", "id_embarazo"),
    ("bridge_embarazo_factor_riesgo", "id_factor_riesgo"): (
        "dim_factor_riesgo",
        "id_factor_riesgo",
    ),
    ("dim_embarazo", "id_paciente"): ("dim_paciente", "id_paciente"),
    ("dim_paciente", "id_clinica"): ("dim_clinica", "id_clinica"),
    ("dim_medico", "id_clinica"): ("dim_clinica", "id_clinica"),
}

CHECKS_ESPERADOS = {
    "ck_fact_lectura_biometrica_forma_valida",
    "ck_fact_lectura_biometrica_estado_hr_valido",
    "ck_fact_lectura_biometrica_estado_spo2_valido",
    "ck_fact_lectura_biometrica_estado_mov_valido",
    "ck_dim_semaforo_codigo_nivel_valido",
    "ck_dim_semaforo_prioridad_rango",
    "ck_dim_tiempo_gestacional_semana_rango",
    "ck_dim_tiempo_gestacional_mes_rango",
    "ck_dim_tiempo_gestacional_trimestre_rango",
    "ck_dim_embarazo_estado_embarazo_valido",
    "ck_dim_embarazo_duracion_no_negativa",
}

UNIQUES_ESPERADOS = {
    ("dim_semaforo", ("codigo_nivel",)),
    ("dim_semaforo", ("prioridad",)),
    ("dim_tiempo_gestacional", ("semana_gestacion",)),
    ("dim_factor_riesgo", ("clave_factor",)),
}

INDICES_ESPERADOS = {
    ("fact_lectura_biometrica", "id_sesion"),
    ("fact_lectura_biometrica", "id_paciente"),
    ("fact_lectura_biometrica", "id_medico"),
    ("fact_lectura_biometrica", "id_clinica"),
    ("fact_lectura_biometrica", "id_tiempo_gestacional"),
    ("fact_lectura_biometrica", "id_embarazo"),
    ("fact_lectura_biometrica", "id_semaforo"),
    ("bridge_embarazo_factor_riesgo", "id_factor_riesgo"),
    ("dim_embarazo", "id_paciente"),
    ("dim_paciente", "id_clinica"),
    ("dim_medico", "id_clinica"),
}


def _tabla(nombre: str):
    return BaseAnalitica.metadata.tables[f"{SCHEMA_ANALITICO}.{nombre}"]


def _nombres_de_objetos():
    for tabla in TABLAS_ANALITICAS:
        for restriccion in tabla.constraints:
            yield str(restriccion.name)
        for indice in tabla.indexes:
            yield str(indice.name)


# ---------------------------------------------------------------------------
# 1. Las nueve estructuras, en su propio registro y esquema
# ---------------------------------------------------------------------------


def test_las_nueve_estructuras_del_modelo_v6_existen():
    fisicas = {tabla.name for tabla in BaseAnalitica.metadata.tables.values()}

    assert fisicas == set(TABLAS_DEL_MODELO.values())
    assert {tabla.name for tabla in TABLAS_ANALITICAS} == fisicas
    assert all(tabla.schema == SCHEMA_ANALITICO for tabla in TABLAS_ANALITICAS)


def test_el_contrato_operacional_de_23_tablas_no_cambia():
    operacionales = {tabla.name for tabla in Base.metadata.tables.values()}

    assert operacionales == TABLAS_ESPERADAS
    assert len(Base.metadata.tables) == 23
    assert operacionales.isdisjoint(tabla.name for tabla in TABLAS_ANALITICAS)
    assert all(t.schema == SCHEMA_OPERACIONAL for t in Base.metadata.tables.values())


def test_el_orden_de_carga_respeta_las_llaves_foraneas():
    posicion = {tabla.name: i for i, tabla in enumerate(TABLAS_ANALITICAS)}
    for tabla in TABLAS_ANALITICAS:
        for llave in tabla.foreign_keys:
            assert posicion[llave.column.table.name] < posicion[tabla.name]


# ---------------------------------------------------------------------------
# 2. Columnas, tipos, nulabilidad y claves
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("nombre_tabla", sorted(COLUMNAS_ESPERADAS))
def test_columnas_tipos_y_nulabilidad(nombre_tabla):
    tabla = _tabla(nombre_tabla)
    reales = {
        columna.name: (str(columna.type.compile(dialect=postgresql.dialect())), columna.nullable)
        for columna in tabla.columns
    }

    assert reales == COLUMNAS_ESPERADAS[nombre_tabla]


@pytest.mark.parametrize("nombre_tabla", sorted(LLAVES_PRIMARIAS_ESPERADAS))
def test_llaves_primarias_reutilizan_la_clave_operacional_sin_secuencia(nombre_tabla):
    tabla = _tabla(nombre_tabla)
    ddl = str(CreateTable(tabla).compile(dialect=postgresql.dialect()))

    assert tuple(c.name for c in tabla.primary_key.columns) == (
        LLAVES_PRIMARIAS_ESPERADAS[nombre_tabla]
    )
    assert "SERIAL" not in ddl
    assert "IDENTITY" not in ddl


def test_llaves_foraneas_quedan_dentro_del_esquema_analitico():
    reales = {
        (tabla.name, llave.parent.name): (llave.column.table.name, llave.column.name)
        for tabla in TABLAS_ANALITICAS
        for llave in tabla.foreign_keys
    }

    assert reales == LLAVES_FORANEAS_ESPERADAS
    assert all(
        llave.column.table.schema == SCHEMA_ANALITICO
        for tabla in TABLAS_ANALITICAS
        for llave in tabla.foreign_keys
    )


def test_id_tiempo_gestacional_apunta_a_id_tiempo_gest():
    """Two logical names, one foreign key: the drawing's mapping, preserved."""
    [llave] = _tabla("fact_lectura_biometrica").c.id_tiempo_gestacional.foreign_keys

    assert (llave.column.table.name, llave.column.name) == (
        "dim_tiempo_gestacional",
        "id_tiempo_gest",
    )


def test_id_sesion_es_una_dimension_degenerada_indexada_sin_llave_foranea():
    columna = _tabla("fact_lectura_biometrica").c.id_sesion

    assert not columna.nullable
    assert not columna.foreign_keys
    assert columna.index


def test_checks_uniques_e_indices():
    checks = {
        str(c.name)
        for tabla in TABLAS_ANALITICAS
        for c in tabla.constraints
        if isinstance(c, CheckConstraint)
    }
    uniques = {
        (tabla.name, tuple(col.name for col in c.columns))
        for tabla in TABLAS_ANALITICAS
        for c in tabla.constraints
        if isinstance(c, UniqueConstraint)
    }
    indices = {
        (tabla.name, col.name)
        for tabla in TABLAS_ANALITICAS
        for indice in tabla.indexes
        for col in indice.columns
    }

    assert checks == CHECKS_ESPERADOS
    assert uniques == UNIQUES_ESPERADOS
    assert indices == INDICES_ESPERADOS


def test_clasificacion_embarazo_es_nullable_y_no_tiene_dominio_inventado():
    tabla = _tabla("dim_embarazo")

    assert tabla.c.clasificacion_embarazo.nullable
    assert not any(
        "clasificacion_embarazo" in str(c.sqltext)
        for c in tabla.constraints
        if isinstance(c, CheckConstraint)
    )


def test_ningun_nombre_de_restriccion_o_indice_supera_63_caracteres():
    nombres = list(_nombres_de_objetos())

    assert nombres
    assert max(len(nombre) for nombre in nombres) <= PG_MAX_IDENTIFIER_LENGTH


# ---------------------------------------------------------------------------
# 3. La revisión de Alembic
# ---------------------------------------------------------------------------

CREATE_TABLE = re.compile(
    rf"CREATE TABLE {SCHEMA_ANALITICO}\.(?P<tabla>\w+) \((?P<cuerpo>.*?)\n\)",
    re.DOTALL,
)
CREATE_INDEX = re.compile(
    rf"CREATE INDEX (?P<indice>\w+) ON {SCHEMA_ANALITICO}\.(?P<tabla>\w+)"
)
DROP_TABLE = re.compile(rf"DROP TABLE {SCHEMA_ANALITICO}\.(?P<tabla>\w+)")


def _renderizar_revision(direccion: str) -> str:
    script = ScriptDirectory.from_config(construir_config_alembic())
    salida = io.StringIO()
    contexto = MigrationContext.configure(
        dialect=postgresql.dialect(),
        opts={
            "as_sql": True,
            "output_buffer": salida,
            "target_metadata": BaseAnalitica.metadata,
        },
    )
    with Operations.context(contexto):
        getattr(script.get_revision(REVISION_ANALITICA).module, direccion)()
    return salida.getvalue()


@pytest.fixture(scope="module")
def sql_upgrade() -> str:
    return _renderizar_revision("upgrade")


@pytest.fixture(scope="module")
def sql_downgrade() -> str:
    return _renderizar_revision("downgrade")


def test_la_revision_analitica_es_el_unico_head_y_desciende_de_scrum_63():
    script = ScriptDirectory.from_config(construir_config_alembic())

    assert script.get_heads() == [REVISION_ANALITICA]
    assert script.get_revision(REVISION_ANALITICA).down_revision == REVISION_SCRUM_63


def test_la_revision_crea_su_esquema_una_sola_vez_y_sin_adoptar_otro(sql_upgrade):
    assert sql_upgrade.count(f"CREATE SCHEMA {SCHEMA_ANALITICO}") == 1
    assert "IF NOT EXISTS" not in sql_upgrade
    assert sql_upgrade.index(f"CREATE SCHEMA {SCHEMA_ANALITICO}") < sql_upgrade.index(
        "CREATE TABLE"
    )


def test_la_revision_no_toca_el_esquema_operacional(sql_upgrade, sql_downgrade):
    assert SCHEMA_OPERACIONAL not in sql_upgrade
    assert SCHEMA_OPERACIONAL not in sql_downgrade


@pytest.mark.parametrize("nombre_tabla", sorted(COLUMNAS_ESPERADAS))
def test_cada_tabla_de_la_revision_equivale_a_la_metadata(nombre_tabla, sql_upgrade):
    de_la_migracion = {
        c["tabla"]: _dividir_en_clausulas(c["cuerpo"])
        for c in CREATE_TABLE.finditer(sql_upgrade)
    }[nombre_tabla]
    ddl = str(CreateTable(_tabla(nombre_tabla)).compile(dialect=postgresql.dialect()))
    de_la_metadata = {
        c["tabla"]: _dividir_en_clausulas(c["cuerpo"]) for c in CREATE_TABLE.finditer(ddl)
    }[nombre_tabla]

    assert de_la_migracion == de_la_metadata


def test_los_indices_de_la_revision_son_los_de_la_metadata(sql_upgrade):
    de_la_migracion = {c["indice"] for c in CREATE_INDEX.finditer(sql_upgrade)}
    de_la_metadata = {indice.name for tabla in TABLAS_ANALITICAS for indice in tabla.indexes}

    assert de_la_migracion == de_la_metadata


def test_el_downgrade_elimina_las_nueve_tablas_en_orden_inverso(sql_upgrade, sql_downgrade):
    creadas = [c["tabla"] for c in CREATE_TABLE.finditer(sql_upgrade)]
    eliminadas = [c["tabla"] for c in DROP_TABLE.finditer(sql_downgrade)]

    assert len(creadas) == 9
    assert eliminadas == list(reversed(creadas))


def test_el_downgrade_elimina_el_esquema_al_final_y_sin_cascade(sql_downgrade):
    ultimo_drop_table = max(c.start() for c in DROP_TABLE.finditer(sql_downgrade))

    assert sql_downgrade.index(f"DROP SCHEMA {SCHEMA_ANALITICO}") > ultimo_drop_table
    assert "CASCADE" not in sql_downgrade
    assert "IF EXISTS" not in sql_downgrade
