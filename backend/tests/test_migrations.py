"""Offline tests for the initial Alembic revision.

Nothing here opens a database connection: the revision is rendered to PostgreSQL
SQL with ``as_sql=True``, exactly the way ``alembic upgrade head --sql`` does it,
and the resulting DDL is compared against ``Base.metadata``. The real round trip
against a PostgreSQL server lives in ``test_migration_postgresql.py``.

The comparison is deliberately structural rather than textual: autogenerate
emits constraints in alphabetical order while the models declare them in
whichever order reads best, so each ``CREATE TABLE`` body is split into a set of
column and constraint clauses before the two sides are matched.
"""

import io
import re

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, Enum, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

import app.models  # noqa: F401  -- registers every model on Base.metadata
from app.db.base import SCHEMA_OPERACIONAL, Base
from tests.conftest import construir_config_alembic
from tests.test_models import ONDELETE_ESPERADOS, TABLAS_ESPERADAS

# Deploying the operational schema is a single step: SCRUM-52 produces one
# revision and every later sprint stacks on top of it.
CANTIDAD_DE_REVISIONES_ESPERADA = 1

# Shape of the deployed schema, pinned so a silent drift in either the models or
# the revision fails here. UNIQUE went from 18 to 17 when the 1:1 between
# sesion_monitoreo and lectura_biometrica became 1:N; everything else held.
CANTIDADES_ESPERADAS = {
    "tablas": 22,
    "primary_key": 22,
    "foreign_key": 25,
    "on_delete_restrict": 15,
    "on_delete_cascade": 10,
    "unique": 17,
    "check": 29,
    "indices": 13,
}

CREATE_TABLE = re.compile(
    rf"CREATE TABLE {SCHEMA_OPERACIONAL}\.(?P<tabla>\w+) \((?P<cuerpo>.*?)\n\)",
    re.DOTALL,
)
CREATE_INDEX = re.compile(
    rf"CREATE INDEX (?P<indice>\w+) ON {SCHEMA_OPERACIONAL}\.(?P<tabla>\w+) "
    r"\((?P<columnas>[^)]*)\)"
)
DROP_TABLE = re.compile(rf"DROP TABLE {SCHEMA_OPERACIONAL}\.(?P<tabla>\w+)")
DROP_INDEX = re.compile(rf"DROP INDEX {SCHEMA_OPERACIONAL}\.(?P<indice>\w+)")


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------


def _dividir_en_clausulas(cuerpo: str) -> set[str]:
    """Split a ``CREATE TABLE`` body at top-level commas.

    A naive ``split(",")`` would tear ``CHECK (x IN ('A', 'B'))`` apart, so the
    parenthesis depth is tracked. Whitespace inside each clause is collapsed
    because Alembic and SQLAlchemy indent the same DDL differently.
    """
    clausulas: set[str] = []
    actual: list[str] = []
    profundidad = 0
    for caracter in cuerpo:
        if caracter == "(":
            profundidad += 1
        elif caracter == ")":
            profundidad -= 1
        if caracter == "," and profundidad == 0:
            clausulas.append(" ".join("".join(actual).split()))
            actual = []
        else:
            actual.append(caracter)
    ultima = " ".join("".join(actual).split())
    if ultima:
        clausulas.append(ultima)
    return set(clausulas)


def _clausulas_por_tabla(sql: str) -> dict[str, set[str]]:
    return {
        coincidencia["tabla"]: _dividir_en_clausulas(coincidencia["cuerpo"])
        for coincidencia in CREATE_TABLE.finditer(sql)
    }


def _renderizar(direccion: str) -> str:
    """Render ``upgrade`` or ``downgrade`` as PostgreSQL SQL, without a server.

    ``target_metadata`` is passed so Alembic reuses the naming convention of
    ``Base.metadata``; without it the CHECK constraints that back the enums
    would come out under different names than the ones the models declare.
    """
    script = ScriptDirectory.from_config(construir_config_alembic())
    modulo = script.get_revision(script.get_heads()[0]).module

    salida = io.StringIO()
    contexto = MigrationContext.configure(
        dialect=postgresql.dialect(),
        opts={
            "as_sql": True,
            "output_buffer": salida,
            "target_metadata": Base.metadata,
        },
    )
    with Operations.context(contexto):
        getattr(modulo, direccion)()
    return salida.getvalue()


def _columnas_enum() -> list[tuple[str, str, list[str]]]:
    """(table, column, values) for every enum-typed column in the metadata."""
    return [
        (tabla.name, columna.name, list(columna.type.enums))
        for tabla in Base.metadata.tables.values()
        for columna in tabla.columns
        if isinstance(columna.type, Enum)
    ]


@pytest.fixture(scope="module")
def sql_upgrade() -> str:
    return _renderizar("upgrade")


@pytest.fixture(scope="module")
def sql_downgrade() -> str:
    return _renderizar("downgrade")


# --------------------------------------------------------------------------
# 1. Identidad de la revisión
# --------------------------------------------------------------------------


def test_existe_una_sola_revision_inicial():
    script = ScriptDirectory.from_config(construir_config_alembic())
    revisiones = list(script.walk_revisions())

    assert len(revisiones) == CANTIDAD_DE_REVISIONES_ESPERADA
    assert len(script.get_heads()) == 1
    assert len(script.get_bases()) == 1


def test_la_revision_inicial_no_tiene_predecesora():
    """A second base revision would silently create an unreachable branch."""
    script = ScriptDirectory.from_config(construir_config_alembic())
    inicial = script.get_revision(script.get_bases()[0])

    assert inicial.down_revision is None
    assert inicial.revision in script.get_heads()


# --------------------------------------------------------------------------
# 2. El DDL compila con el dialecto PostgreSQL
# --------------------------------------------------------------------------


def test_el_upgrade_compila_con_el_dialecto_postgresql(sql_upgrade):
    assert sql_upgrade.strip()
    assert f"CREATE TABLE {SCHEMA_OPERACIONAL}." in sql_upgrade


def test_el_downgrade_compila_con_el_dialecto_postgresql(sql_downgrade):
    assert sql_downgrade.strip()
    assert f"DROP TABLE {SCHEMA_OPERACIONAL}." in sql_downgrade


# --------------------------------------------------------------------------
# 3. Creación del esquema
# --------------------------------------------------------------------------


def test_el_esquema_se_crea_antes_que_cualquier_tabla(sql_upgrade):
    creacion = sql_upgrade.index(f"CREATE SCHEMA {SCHEMA_OPERACIONAL}")
    primera_tabla = sql_upgrade.index(f"CREATE TABLE {SCHEMA_OPERACIONAL}.")

    assert creacion < primera_tabla


def test_el_esquema_no_adopta_uno_preexistente(sql_upgrade):
    """Without IF NOT EXISTS the migration refuses to reuse an unknown schema."""
    assert "CREATE SCHEMA IF NOT EXISTS" not in sql_upgrade
    assert sql_upgrade.count("CREATE SCHEMA") == 1


# --------------------------------------------------------------------------
# 4. Conjunto exacto de tablas
# --------------------------------------------------------------------------


def test_crea_exactamente_las_tablas_de_la_metadata(sql_upgrade):
    creadas = set(_clausulas_por_tabla(sql_upgrade))

    assert creadas == TABLAS_ESPERADAS
    assert creadas == {t.name for t in Base.metadata.tables.values()}


def test_el_orden_de_creacion_respeta_las_dependencias(sql_upgrade):
    """A table must not be created before the tables its foreign keys point at."""
    orden = [c["tabla"] for c in CREATE_TABLE.finditer(sql_upgrade)]
    posicion = {nombre: i for i, nombre in enumerate(orden)}

    for tabla in Base.metadata.tables.values():
        for fk in tabla.foreign_keys:
            referida = fk.column.table.name
            if referida != tabla.name:
                assert posicion[referida] < posicion[tabla.name], (
                    f"{tabla.name} se crea antes que {referida}"
                )


# --------------------------------------------------------------------------
# 5. Equivalencia estructural con Base.metadata
# --------------------------------------------------------------------------


@pytest.mark.parametrize("nombre_tabla", sorted(TABLAS_ESPERADAS))
def test_cada_tabla_es_equivalente_a_la_de_la_metadata(nombre_tabla, sql_upgrade):
    """Types, lengths, nullability, defaults, PK/FK/UNIQUE/CHECK -- all at once.

    Both sides are compiled by the same PostgreSQL dialect, so any drift between
    the revision and the models shows up as a clause present on one side only.
    """
    de_la_migracion = _clausulas_por_tabla(sql_upgrade)[nombre_tabla]
    tabla = Base.metadata.tables[f"{SCHEMA_OPERACIONAL}.{nombre_tabla}"]
    ddl = str(CreateTable(tabla).compile(dialect=postgresql.dialect()))
    de_la_metadata = _clausulas_por_tabla(ddl)[nombre_tabla]

    assert de_la_migracion == de_la_metadata


# --------------------------------------------------------------------------
# 6. Llaves foráneas y políticas de borrado
# --------------------------------------------------------------------------


def test_cantidad_de_llaves_foraneas(sql_upgrade):
    referencias = sql_upgrade.count(f"REFERENCES {SCHEMA_OPERACIONAL}.")

    assert referencias == len(ONDELETE_ESPERADOS)


@pytest.mark.parametrize("politica", ["RESTRICT", "CASCADE"])
def test_cantidad_de_politicas_on_delete(politica, sql_upgrade):
    """The RESTRICT/CASCADE split is pinned by ONDELETE_ESPERADOS in test_models."""
    esperadas = sum(1 for p in ONDELETE_ESPERADOS.values() if p == politica)

    assert sql_upgrade.count(f"ON DELETE {politica}") == esperadas


def test_ninguna_llave_foranea_queda_sin_politica(sql_upgrade):
    con_politica = sql_upgrade.count("ON DELETE ")

    assert con_politica == len(ONDELETE_ESPERADOS)


def test_toda_referencia_lleva_el_esquema(sql_upgrade):
    """An unqualified REFERENCES would resolve through search_path at runtime."""
    assert sql_upgrade.count("REFERENCES ") == sql_upgrade.count(
        f"REFERENCES {SCHEMA_OPERACIONAL}."
    )


# --------------------------------------------------------------------------
# 7. Índices explícitos
# --------------------------------------------------------------------------


def test_indices_explicitos_coinciden_con_la_metadata(sql_upgrade):
    de_la_migracion = {
        (c["tabla"], c["indice"], tuple(x.strip() for x in c["columnas"].split(",")))
        for c in CREATE_INDEX.finditer(sql_upgrade)
    }
    de_la_metadata = {
        (tabla.name, indice.name, tuple(col.name for col in indice.columns))
        for tabla in Base.metadata.tables.values()
        for indice in tabla.indexes
    }

    assert de_la_migracion == de_la_metadata


def test_no_se_inventan_indices_fuera_de_la_metadata(sql_upgrade):
    cantidad = sum(len(t.indexes) for t in Base.metadata.tables.values())

    assert sql_upgrade.count("CREATE INDEX") == cantidad


# --------------------------------------------------------------------------
# 8. Restricciones
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prefijo", "palabra_clave"),
    [("pk_", "PRIMARY KEY"), ("uq_", "UNIQUE"), ("ck_", "CHECK")],
)
def test_los_nombres_de_constraint_de_la_metadata_estan_en_la_migracion(
    prefijo, palabra_clave, sql_upgrade
):
    esperados = {
        constraint.name
        for tabla in Base.metadata.tables.values()
        for constraint in tabla.constraints
        if isinstance(constraint.name, str) and constraint.name.startswith(prefijo)
    }
    encontrados = set(
        re.findall(rf"CONSTRAINT ({prefijo}\w+) {palabra_clave}", sql_upgrade)
    )

    assert esperados
    assert esperados <= encontrados


def test_llaves_primarias_compuestas_se_declaran_completas(sql_upgrade):
    assert "CONSTRAINT pk_medico_clinica PRIMARY KEY (id_medico, id_clinica)" in (
        sql_upgrade
    )
    assert (
        "CONSTRAINT pk_embarazo_factor_riesgo PRIMARY KEY "
        "(id_embarazo, id_factor_riesgo)" in sql_upgrade
    )


def test_la_sesion_admite_varias_lecturas(sql_upgrade):
    """1:N: a UNIQUE on id_sesion would cap the session at a single reading."""
    assert "UNIQUE (id_sesion)" not in sql_upgrade
    assert "uq_lectura_biometrica_id_sesion" not in sql_upgrade


def test_id_sesion_conserva_su_llave_foranea_en_cascada(sql_upgrade):
    """Loosening the cardinality must not touch the FK, its target or ON DELETE."""
    assert "id_sesion INTEGER NOT NULL" in sql_upgrade
    assert (
        "CONSTRAINT fk_lectura_biometrica_id_sesion_sesion_monitoreo "
        "FOREIGN KEY(id_sesion) REFERENCES operacional.sesion_monitoreo (id_sesion) "
        "ON DELETE CASCADE" in sql_upgrade
    )


@pytest.mark.parametrize(
    ("clave", "patron"),
    [
        ("tablas", f"CREATE TABLE {SCHEMA_OPERACIONAL}."),
        ("primary_key", "PRIMARY KEY"),
        ("foreign_key", f"REFERENCES {SCHEMA_OPERACIONAL}."),
        ("on_delete_restrict", "ON DELETE RESTRICT"),
        ("on_delete_cascade", "ON DELETE CASCADE"),
        ("unique", "UNIQUE ("),
        ("check", "CHECK ("),
        ("indices", "CREATE INDEX"),
    ],
)
def test_cantidades_de_la_estructura_desplegada(clave, patron, sql_upgrade):
    """Pin the shape of the schema so an accidental drop or addition fails loudly."""
    assert sql_upgrade.count(patron) == CANTIDADES_ESPERADAS[clave]


def test_las_cantidades_pinneadas_siguen_a_la_metadata():
    """CANTIDADES_ESPERADAS must describe Base.metadata, not a stale snapshot."""
    reales = {
        "tablas": len(Base.metadata.tables),
        "primary_key": len(Base.metadata.tables),
        "foreign_key": sum(len(t.foreign_keys) for t in Base.metadata.tables.values()),
        "on_delete_restrict": sum(
            1 for politica in ONDELETE_ESPERADOS.values() if politica == "RESTRICT"
        ),
        "on_delete_cascade": sum(
            1 for politica in ONDELETE_ESPERADOS.values() if politica == "CASCADE"
        ),
        "unique": sum(
            1
            for t in Base.metadata.tables.values()
            for c in t.constraints
            if isinstance(c, UniqueConstraint)
        ),
        "check": sum(
            1
            for t in Base.metadata.tables.values()
            for c in t.constraints
            if isinstance(c, CheckConstraint)
        ),
        "indices": sum(len(t.indexes) for t in Base.metadata.tables.values()),
    }

    assert reales == CANTIDADES_ESPERADAS


# --------------------------------------------------------------------------
# 9. Enums sin tipos nativos
# --------------------------------------------------------------------------


def test_no_se_emiten_tipos_enum_nativos(sql_upgrade, sql_downgrade):
    """native_enum=False must stay: a native type would need ALTER TYPE to evolve."""
    assert "CREATE TYPE" not in sql_upgrade
    assert "DROP TYPE" not in sql_downgrade


@pytest.mark.parametrize(
    ("nombre_tabla", "nombre_columna", "valores"),
    [(t, c, v) for t, c, v in _columnas_enum()],
    ids=[f"{t}.{c}" for t, c, _ in _columnas_enum()],
)
def test_cada_enum_se_materializa_como_check_con_sus_valores_vigentes(
    nombre_tabla, nombre_columna, valores, sql_upgrade
):
    """Reads the value list from the models, so an enum change fails here first.

    That is the intended signal: whoever edits an enum must regenerate the
    revision instead of letting the deployed CHECK drift from the metadata.
    """
    lista = ", ".join(f"'{valor}'" for valor in valores)

    assert f"{nombre_columna} IN ({lista})" in sql_upgrade


# --------------------------------------------------------------------------
# 10. Downgrade
# --------------------------------------------------------------------------


def test_el_downgrade_elimina_todas_las_tablas_creadas(sql_upgrade, sql_downgrade):
    creadas = [c["tabla"] for c in CREATE_TABLE.finditer(sql_upgrade)]
    eliminadas = [c["tabla"] for c in DROP_TABLE.finditer(sql_downgrade)]

    assert set(eliminadas) == set(creadas)
    assert len(eliminadas) == len(creadas)


def test_el_downgrade_invierte_el_orden_de_creacion(sql_upgrade, sql_downgrade):
    creadas = [c["tabla"] for c in CREATE_TABLE.finditer(sql_upgrade)]
    eliminadas = [c["tabla"] for c in DROP_TABLE.finditer(sql_downgrade)]

    assert eliminadas == list(reversed(creadas))


def test_el_downgrade_elimina_todos_los_indices(sql_upgrade, sql_downgrade):
    creados = {c["indice"] for c in CREATE_INDEX.finditer(sql_upgrade)}
    eliminados = {c["indice"] for c in DROP_INDEX.finditer(sql_downgrade)}

    assert eliminados == creados


def test_el_esquema_se_elimina_despues_de_sus_objetos(sql_downgrade):
    ultimo_drop_table = max(c.start() for c in DROP_TABLE.finditer(sql_downgrade))
    drop_schema = sql_downgrade.index(f"DROP SCHEMA {SCHEMA_OPERACIONAL}")

    assert drop_schema > ultimo_drop_table


def test_el_downgrade_no_usa_cascade(sql_downgrade):
    """CASCADE would silently drop objects this revision never created."""
    assert "CASCADE" not in sql_downgrade
    assert "IF EXISTS" not in sql_downgrade
