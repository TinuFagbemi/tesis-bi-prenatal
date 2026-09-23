"""Offline tests for the Alembic revision chain.

Nothing here opens a database connection: the chain is rendered to PostgreSQL SQL
with ``as_sql=True``, exactly the way ``alembic upgrade head --sql`` does it, and
the resulting DDL is compared against ``Base.metadata``. The real round trip
against a PostgreSQL server lives in ``test_migration_postgresql.py``.

**The whole chain, not just the head.** What these tests describe is the schema
the migrations deploy, and from SCRUM-63 onwards that schema is the sum of more
than one revision. Rendering only the head would compare a single ``CREATE
TABLE`` against the twenty-three tables of the metadata and fail for the wrong
reason.

**The operational schema only.** From SCRUM-69 the chain also deploys the
analytic schema, whose contract lives in ``test_etl_esquema.py``. The rendered
chain is therefore split by schema before anything is counted here: every
statement about ``analitico`` is set aside -- and a test below checks that
nothing else was -- so the pinned operational counts keep describing exactly
the twenty-three operational tables, instead of becoming a global total.

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

# SCRUM-52 deployed the operational schema in one revision and every later
# sprint stacks on top of it. SCRUM-63 adds the second: idempotencia_solicitud.
# SCRUM-69 adds the third, the analytic schema, which touches nothing here.
# SCRUM-97 adds the fourth: ck_usuario_email_canonico and the role/link
# triggers, on tables that already existed.
CANTIDAD_DE_REVISIONES_ESPERADA = 5

# Shape of the deployed schema, pinned so a silent drift in either the models or
# the revisions fails here. UNIQUE went from 18 to 17 when the 1:1 between
# sesion_monitoreo and lectura_biometrica became 1:N; SCRUM-63 then added one
# table with one primary key, one foreign key with ON DELETE RESTRICT, one
# UNIQUE and one CHECK, and no index of its own. SCRUM-97 adds one CHECK to an
# existing table -- ck_usuario_email_canonico -- and nothing else counted here:
# its triggers and function are not constraints of this inventory.
#
# SCRUM-98 subfase 5 anade el mapa de seudonimos: dos tablas en el schema
# ``privado``, cada una con su PRIMARY KEY, su UNIQUE y una llave foranea hacia
# ``operacional`` con ON DELETE RESTRICT. No viven en ``operacional``, asi que no
# entran en ``ONDELETE_ESPERADOS`` -- que inventaria el modelo operativo -- pero
# si aparecen en el SQL renderizado. Su aporte se declara aparte para que siga
# siendo visible cual de los dos esquemas lo produce.
APORTE_DEL_MAPA = {
    "primary_key": 2,
    "foreign_key": 2,
    "on_delete_restrict": 2,
    # ``unique`` no aparece: las dos restricciones del mapa se renderizan como
    # ``CONSTRAINT uq_... UNIQUE`` en la columna, sin el ``UNIQUE (`` que esta
    # cifra cuenta.
}

CANTIDADES_ESPERADAS = {
    "tablas": 23,
    "primary_key": 23,
    "foreign_key": 26,
    "on_delete_restrict": 16,
    "on_delete_cascade": 10,
    "unique": 18,
    "check": 31,
    # 14 desde SCRUM-98: ix_lectura_biometrica_id_sesion, que la politica de
    # esa tabla necesita y que la cascada del borrado tampoco tenia.
    "indices": 14,
}

CREATE_TABLE = re.compile(
    rf"CREATE TABLE {SCHEMA_OPERACIONAL}\.(?P<tabla>\w+) \((?P<cuerpo>.*?)\n\)",
    re.DOTALL,
)
CREATE_INDEX = re.compile(
    rf"CREATE INDEX (?P<indice>\w+) ON {SCHEMA_OPERACIONAL}\.(?P<tabla>\w+) "
    r"\((?P<columnas>[^)]*)\)"
)
# A constraint a later revision adds to a table created earlier (SCRUM-97). It is
# part of the deployed table as much as a clause of its CREATE TABLE.
ADD_CONSTRAINT = re.compile(
    rf"ALTER TABLE {SCHEMA_OPERACIONAL}\.(?P<tabla>\w+) ADD (?P<clausula>CONSTRAINT .*?);\n",
    re.DOTALL,
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
    """Clauses of each table as the chain leaves it: CREATE TABLE plus later ADDs."""
    clausulas = {
        coincidencia["tabla"]: _dividir_en_clausulas(coincidencia["cuerpo"])
        for coincidencia in CREATE_TABLE.finditer(sql)
    }
    for coincidencia in ADD_CONSTRAINT.finditer(sql):
        if coincidencia["tabla"] in clausulas:
            clausulas[coincidencia["tabla"]].add(" ".join(coincidencia["clausula"].split()))
    return clausulas


def _revisiones_en_orden(direccion: str) -> list:
    """Every revision of the chain, in the order that direction applies them.

    ``walk_revisions`` starts at the head, which is already the order a downgrade
    runs in; an upgrade runs the other way round.
    """
    script = ScriptDirectory.from_config(construir_config_alembic())
    revisiones = list(script.walk_revisions())
    return revisiones if direccion == "downgrade" else list(reversed(revisiones))


def _renderizar(direccion: str) -> str:
    """Render the whole chain as PostgreSQL SQL, without a server.

    Every revision is applied in order rather than only the head, because what
    the rest of this module compares against ``Base.metadata`` is the schema the
    chain deploys, not the delta of its last step.

    ``target_metadata`` is passed so Alembic reuses the naming convention of
    ``Base.metadata``; without it the CHECK constraints that back the enums
    would come out under different names than the ones the models declare.
    """
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
        for revision in _revisiones_en_orden(direccion):
            getattr(revision.module, direccion)()
    return salida.getvalue()


def _columnas_enum() -> list[tuple[str, str, list[str]]]:
    """(table, column, values) for every enum-typed column in the metadata."""
    return [
        (tabla.name, columna.name, list(columna.type.enums))
        for tabla in Base.metadata.tables.values()
        for columna in tabla.columns
        if isinstance(columna.type, Enum)
    ]


# The analytic schema of SCRUM-69, named as a literal for the same reason the
# revisions do: this module describes DDL, not application constants.
ESQUEMA_ANALITICO = "analitico"
SEPARADOR_DE_SENTENCIAS = ";\n"


def _sentencias(sql: str) -> list[str]:
    return [sentencia for sentencia in sql.split(SEPARADOR_DE_SENTENCIAS) if sentencia.strip()]


# El schema de las superficies publicadas. Sus vistas leen ``analitico``,
# ``privado`` y ``operacional`` a la vez -- eso es lo que hacen --, asi que no
# pertenecen a ninguna de las dos particiones que este modulo separa y se
# excluyen de la del esquema analitico. La garantia que esa particion protege es
# que ninguna **tabla** analitica referencie una operacional; una vista que lee
# de los dos esquemas no es eso, y la comprueba
# ``test_el_unico_cruce_de_esquema_es_el_del_mapa_de_seudonimos``.
ESQUEMA_PUBLICACION = "publicacion"


def _es_del_esquema_analitico(sentencia: str) -> bool:
    if ESQUEMA_PUBLICACION in sentencia:
        return False
    return ESQUEMA_ANALITICO in sentencia


def _solo_operacional(sql: str) -> str:
    """The rendered chain without the statements about the analytic schema."""
    return SEPARADOR_DE_SENTENCIAS.join(
        sentencia for sentencia in _sentencias(sql) if not _es_del_esquema_analitico(sentencia)
    )


@pytest.fixture(scope="module")
def sql_upgrade() -> str:
    return _solo_operacional(_renderizar("upgrade"))


@pytest.fixture(scope="module")
def sql_downgrade() -> str:
    return _solo_operacional(_renderizar("downgrade"))


@pytest.mark.parametrize("direccion", ["upgrade", "downgrade"])
def test_solo_se_apartan_sentencias_del_esquema_analitico(direccion):
    """The split must not hide an operational statement from the counts below.

    Everything set aside names the analytic schema and none of it names the
    operational one -- which also means no analytic table references an
    operational one.
    """
    apartadas = [
        sentencia
        for sentencia in _sentencias(_renderizar(direccion))
        if _es_del_esquema_analitico(sentencia)
    ]

    assert apartadas
    assert all(SCHEMA_OPERACIONAL not in sentencia for sentencia in apartadas)


# --------------------------------------------------------------------------
# 1. Identidad de la revisión
# --------------------------------------------------------------------------


def test_la_cadena_de_revisiones_es_lineal():
    """One base and one head: two heads would mean two schemas to deploy."""
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


def test_la_cadena_va_del_head_a_la_base_sin_saltos():
    """Walking down from the head must reach the base and pass through nothing else."""
    script = ScriptDirectory.from_config(construir_config_alembic())
    recorrido = [revision.revision for revision in script.walk_revisions()]

    assert recorrido[0] == script.get_heads()[0]
    assert recorrido[-1] == script.get_bases()[0]
    assert len(recorrido) == CANTIDAD_DE_REVISIONES_ESPERADA


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
    # Dos desde SCRUM-98: ``operacional`` y ``seguridad``, el de los helpers.
    # Cuatro en este render: operacional, seguridad (SCRUM-98 subfase 3) y los
    # dos de la publicacion (subfase 5). El de ``analitico`` no cuenta aqui
    # porque ``sql_upgrade`` aparta las sentencias de ese esquema.
    assert sql_upgrade.count("CREATE SCHEMA") == 4


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
    """Las del modelo operativo, mas las dos del mapa privado."""
    referencias = sql_upgrade.count(f"REFERENCES {SCHEMA_OPERACIONAL}.")

    assert referencias == len(ONDELETE_ESPERADOS) + APORTE_DEL_MAPA["foreign_key"]


@pytest.mark.parametrize("politica", ["RESTRICT", "CASCADE"])
def test_cantidad_de_politicas_on_delete(politica, sql_upgrade):
    """The RESTRICT/CASCADE split is pinned by ONDELETE_ESPERADOS in test_models.

    Las dos del mapa privado son RESTRICT, y no por comodidad: es lo que impide
    borrar una paciente por debajo de los datos ya publicados.
    """
    esperadas = sum(1 for p in ONDELETE_ESPERADOS.values() if p == politica)
    if politica == "RESTRICT":
        esperadas += APORTE_DEL_MAPA["on_delete_restrict"]

    assert sql_upgrade.count(f"ON DELETE {politica}") == esperadas


def test_ninguna_llave_foranea_queda_sin_politica(sql_upgrade):
    con_politica = sql_upgrade.count("ON DELETE ")

    assert con_politica == len(ONDELETE_ESPERADOS) + APORTE_DEL_MAPA["foreign_key"]


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
    """Pin the shape of the schema so an accidental drop or addition fails loudly.

    ``WITH CHECK (`` de las policies de SCRUM-98 contiene ``CHECK (``, y contarlo
    mezclaria dos cosas distintas: las restricciones de columna del modelo y la
    clausula de escritura de una politica. Se descuenta, asi que esta cifra
    sigue describiendo lo que siempre describio.
    """
    encontrados = sql_upgrade.count(patron)
    encontrados -= APORTE_DEL_MAPA.get(clave, 0)
    if clave == "check":
        encontrados -= sql_upgrade.count("WITH CHECK (")

    assert encontrados == CANTIDADES_ESPERADAS[clave]


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


# Los tres schemas que esta cadena crea entera y puede por tanto eliminar
# entera. El CASCADE alcanza exactamente lo que ella misma puso dentro.
SCHEMAS_QUE_SE_ELIMINAN_ENTEROS = ("seguridad", "publicacion", "privado")


def test_el_downgrade_no_usa_cascade_sobre_tablas(sql_downgrade):
    """CASCADE would silently drop objects this revision never created.

    Las unicas excepciones son los ``DROP SCHEMA ... CASCADE`` de los tres
    schemas que esta cadena crea por completo: ``seguridad`` con sus nueve
    funciones, y ``publicacion`` y ``privado`` con sus vistas y su mapa. Ninguno
    contiene nada que no haya creado ella. Sobre una **tabla** seguiria siendo
    inaceptable, y por eso la prueba distingue el objeto en vez de prohibir la
    palabra.
    """
    permitidos = {
        f"DROP SCHEMA {schema} CASCADE" for schema in SCHEMAS_QUE_SE_ELIMINAN_ENTEROS
    }
    for linea in sql_downgrade.splitlines():
        if "CASCADE" not in linea:
            continue
        assert any(permitido in linea for permitido in permitidos), linea
    assert "IF EXISTS" not in sql_downgrade


# ---------------------------------------------------------------------------
# El dia clinico, sin servidor
# ---------------------------------------------------------------------------


def _revision_de_rls():
    """El modulo de la revision de SCRUM-98, cargado por ruta.

    Una revision de Alembic no es importable por nombre de paquete, y tampoco
    debe importar codigo de la aplicacion: su SQL tiene que quedar congelado.
    Por eso la zona horaria se declara dentro de la revision y es *esta* prueba
    la que impide que se separe de la del ETL.
    """
    import importlib.util
    from pathlib import Path

    ruta = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "3b4a352bc39a_enable_row_level_security_and_clinical_.py"
    )
    spec = importlib.util.spec_from_file_location("revision_rls", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_la_zona_clinica_de_la_revision_es_la_del_etl():
    """Un solo dia clinico para todo el sistema: el de Panama.

    Si alguien cambiara la zona en un sitio y no en el otro, la vigencia de una
    asignacion y el dia de una lectura dejarian de coincidir entre el ETL y las
    politicas. Esta prueba es lo que mantiene las dos definiciones atadas sin
    que la migracion tenga que importar la aplicacion.
    """
    from app.etl.reglas import ZONA_HORARIA_CLINICA

    assert _revision_de_rls().ZONA_CLINICA == str(ZONA_HORARIA_CLINICA)


def test_la_revision_no_depende_del_timezone_de_la_sesion(sql_upgrade):
    """Ni ``CURRENT_DATE`` ni un ``::date`` desnudo sobre un timestamptz.

    Las dos expresiones las resuelve el parametro ``TimeZone`` de la sesion, de
    modo que el mismo instante daba dias distintos segun quien preguntara. Se
    comprueba sobre el SQL renderizado, que es lo que la base va a recibir.
    """
    assert "CURRENT_DATE" not in sql_upgrade
    assert "f.fecha_hora::date" not in sql_upgrade
    assert "date_trunc('month', f.fecha_hora)" not in sql_upgrade


def test_cada_conversion_de_instante_nombra_la_zona_clinica():
    """Y lo hace de forma explicita, no por omision."""
    revision = _revision_de_rls()
    zona = revision.ZONA_CLINICA

    for expresion in (
        revision.HOY_CLINICO,
        revision._dia_clinico("f.fecha_hora"),
        revision._mes_clinico("f.fecha_hora"),
    ):
        assert f"AT TIME ZONE '{zona}'" in expresion, expresion


def test_las_fechas_que_ya_son_date_no_se_convierten():
    """``dim_embarazo.fecha_inicio`` es ``DATE``: no tiene zona que aplicar.

    Convertirla seria un error distinto -- trataria una fecha civil como si
    fuera un instante --, asi que se deja como estaba.
    """
    vistas = _revision_de_rls().VISTAS

    assert "date_trunc('month', de.fecha_inicio)::date" in vistas
    assert "de.fecha_inicio AT TIME ZONE" not in vistas


def test_la_ventana_de_secuencia_ordena_por_tiempo_y_desempata():
    """``secuencia_sesion`` se ordena por el instante, con ``id_sesion`` detras.

    Las dos mitades importan y por motivos distintos.

    El **instante primero**: ``id_sesion`` es una clave surrogate que el servidor
    asigna al recibir la sesion, no al ocurrir, y en un sistema offline-first eso
    es orden de sincronizacion. Ordenar por el invertiria la serie justo en el
    escenario que esta tesis modela.

    El **desempate despues**: sin un segundo criterio, dos sesiones que empiezan
    en el mismo instante pueden intercambiar su numero entre ejecuciones. Eso no
    se puede comprobar ejecutando la consulta -- PostgreSQL reutiliza el plan
    dentro de una sesion y el empate no se manifiesta, cosa que se verifico
    mutando la ventana y viendo que ninguna prueba de runtime moria. Donde si es
    determinista es en el texto del SQL, y aqui es donde se fija.
    """
    revision = _revision_de_rls()

    ventana = re.search(
        r"row_number\(\)\s*OVER\s*\((.*?)\)", revision.VISTAS, re.S
    )
    assert ventana is not None, "no hay ventana row_number() en las vistas"

    interior = " ".join(ventana.group(1).split())
    assert "PARTITION BY id_embarazo" in interior, interior
    assert "ORDER BY inicio_sesion, id_sesion" in interior, interior
