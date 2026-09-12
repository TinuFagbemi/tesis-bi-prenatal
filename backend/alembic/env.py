from itertools import chain
from logging.config import fileConfig

from alembic import context
from sqlalchemy import CheckConstraint, MetaData, engine_from_config, pool

import app.etl.modelos  # noqa: F401  -- registers the analytic tables on BaseAnalitica.metadata
import app.models  # noqa: F401  -- registers every model on Base.metadata
from app.config import settings
from app.db.base import NAMING_CONVENTION, Base
from app.db.base_analitica import BaseAnalitica

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.database_url)

# One registry per schema (SCRUM-69): the operational one keeps its own contract
# of 23 tables and the analytic one holds the star schema. Alembic compares the
# database against a single MetaData holding a copy of every table of both,
# under the naming convention they share.
#
# A single MetaData, and not the list Alembic also accepts, because Alembic
# applies the naming convention to its operations -- ``op.create_table`` and the
# rest -- only when ``target_metadata`` is a MetaData that carries one. With a
# list, the CHECK constraints that back the enums of the first revision would
# be created under their bare names on a fresh database, and the deployed schema
# would stop matching the models. Leaving the analytic tables out instead would
# make autogenerate see them as unknown, and ``alembic check`` would propose
# dropping a schema the project owns.
target_metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _esquema_referido(tabla, esquema_destino, restriccion, esquema_referido):
    """Schema of the table a copied foreign key points at.

    The models write their foreign keys without a schema ("paciente.id_paciente")
    and each registry resolves them in its own default schema. The combined
    MetaData has no default, so the copy names it explicitly: no foreign key
    crosses from one schema to the other, so it is always the table's own.
    """
    return esquema_referido or tabla.schema


for registro in (Base.metadata, BaseAnalitica.metadata):
    for tabla in registro.tables.values():
        tabla.to_metadata(target_metadata, referred_schema_fn=_esquema_referido)

# Every operational table lives in the non-default ``operacional`` schema. Without
# include_schemas=True Alembic only reflects the connection's default schema, so
# autogenerate would keep proposing the 22 tables as missing and ``alembic check``
# would report divergence against a database that is already up to date.
INCLUDE_SCHEMAS = True

# ``Enum(native_enum=False, create_constraint=True)`` makes SQLAlchemy attach a
# CHECK constraint that the type owns -- "type bound" in its own vocabulary.
# Alembic 1.19 started comparing CHECK constraints, but it builds the metadata
# side with ``all_table_check_constraints()``, which deliberately drops the
# type-bound ones, while it reflects every named CHECK back from the server.
# That asymmetry makes autogenerate propose dropping all of them on every run,
# against a database that is already up to date.
#
# The names are derived from the metadata itself, so adding, renaming or
# removing an enum column keeps this in sync with no list to maintain.
TYPE_BOUND_CHECK_NAMES = frozenset(
    constraint.name
    for table in target_metadata.tables.values()
    for constraint in chain(
        table.constraints, *(column.constraints for column in table.columns)
    )
    # ``_type_bound`` is private, but it is the same flag Alembic reads to build
    # the metadata side of the comparison, so both stay in agreement.
    if isinstance(constraint, CheckConstraint)
    and getattr(constraint, "_type_bound", False)
)


def include_object(object_, name, type_, reflected, compare_to) -> bool:
    """Ignore only the reflected CHECK that the Enum type itself redeclares.

    Everything else is left to Alembic: explicit CHECK constraints, columns,
    indexes, unique constraints and foreign keys keep being compared, so a real
    divergence still fails ``alembic check``.
    """
    if type_ == "check_constraint" and reflected:
        return name not in TYPE_BOUND_CHECK_NAMES
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=INCLUDE_SCHEMAS,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=INCLUDE_SCHEMAS,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
