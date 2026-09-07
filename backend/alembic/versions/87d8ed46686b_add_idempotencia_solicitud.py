"""add idempotencia solicitud

Adds the single table that makes an HTTP resend recognisable:
``operacional.idempotencia_solicitud``. Nothing else is touched. The 21 tables
the simulated dataset loads keep exactly the columns they had, because the
loader normalises every column of every table it knows and a new column on any
of them would break the dataset instead of the API.

``UNIQUE (recurso, clave)`` is the whole guarantee. It is what lets a claim be
taken atomically with ``INSERT ... ON CONFLICT DO NOTHING``, so two concurrent
copies of the same package cannot both be accepted.

``id_sesion`` and ``ids_lectura`` are nullable on purpose: the claim is taken
before the session exists and completed by an ``UPDATE`` inside the same
transaction. PostgreSQL 16 accepts neither a deferrable ``NOT NULL`` nor a
deferrable ``CHECK``, so «a committed row is complete» stays an invariant of the
calling code. The CHECK that *is* enforceable pins the other half of it: the two
result columns are either both absent or both present, never half a result.

``ON DELETE RESTRICT`` rather than CASCADE: a claim must not disappear together
with the session it identifies, because that would silently free a used key and
let the next resend create a second session.

The schema is not created here -- ``150788f88be7`` owns it -- and the downgrade
therefore drops only this table and leaves ``operacional`` standing.

Revision ID: 87d8ed46686b
Revises: 150788f88be7
Create Date: 2026-09-05 17:06:06.102790

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '87d8ed46686b'
down_revision: Union[str, None] = '150788f88be7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_OPERACIONAL = 'operacional'


def upgrade() -> None:
    op.create_table('idempotencia_solicitud',
    sa.Column('id_idempotencia', sa.Integer(), nullable=False),
    sa.Column('recurso', sa.String(length=80), nullable=False),
    sa.Column('clave', sa.String(length=128), nullable=False),
    sa.Column('huella', sa.String(length=64), nullable=False),
    sa.Column('id_sesion', sa.Integer(), nullable=True),
    sa.Column('ids_lectura', sa.ARRAY(sa.Integer()), nullable=True),
    sa.Column('fecha_hora', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('(id_sesion IS NULL AND ids_lectura IS NULL) OR (id_sesion IS NOT NULL AND ids_lectura IS NOT NULL AND cardinality(ids_lectura) > 0)', name=op.f('ck_idempotencia_solicitud_resultado_completo')),
    sa.ForeignKeyConstraint(['id_sesion'], ['operacional.sesion_monitoreo.id_sesion'], name=op.f('fk_idempotencia_solicitud_id_sesion_sesion_monitoreo'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id_idempotencia', name=op.f('pk_idempotencia_solicitud')),
    sa.UniqueConstraint('recurso', 'clave', name=op.f('uq_idempotencia_solicitud_recurso_clave')),
    schema=SCHEMA_OPERACIONAL
    )


def downgrade() -> None:
    op.drop_table('idempotencia_solicitud', schema=SCHEMA_OPERACIONAL)
