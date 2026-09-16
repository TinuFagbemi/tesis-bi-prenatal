r"""enforce canonical email and account role link

Two guarantees of the account life cycle (SCRUM-97) that the application alone
cannot close under concurrency, installed in PostgreSQL. No table, column or
index is created; the 23 operational tables keep their shape.

**1. Canonical access email.** ``email.strip().lower()`` is the only form stored.
Before anything changes, every existing account is checked: a value that would
be empty, would keep internal whitespace, would exceed the column, or would
collide with another account once normalised stops the migration with a count
and no address. Nothing is chosen, merged or deleted -- that decision belongs to
a person. Only then are the existing rows normalised and
``ck_usuario_email_canonico`` added. The existing ``uq_usuario_email`` is kept:
over canonical values it already is the case-insensitive uniqueness, so no
``citext``, no extension and no functional index are added.

The strip in SQL removes ``\s`` from both ends, which matches Python's
``str.strip()`` for every address the simulated dataset or the API can produce.
``\s`` is written instead of the equivalent ``[[:space:]]`` because SQLAlchemy
reads ``:space`` inside a text clause as a bind parameter.

**2. Role and clinical link agree at commit.** For every account:

========  ================  ==============
Rol       usuario_paciente  usuario_medico
========  ================  ==============
PACIENTE  exactly 1         0
MEDICO    0                 exactly 1
ADMIN     0                 0
========  ================  ==============

The rule spans three tables, so it is one PL/pgSQL function,
``operacional.validar_rol_vinculo_usuario()``, fired by three constraint
triggers ``DEFERRABLE INITIALLY DEFERRED`` -- on ``usuario`` when its role or
key is set, and on each bridge on insert, update and delete. Deferred, so an
account and then its link can be inserted inside one transaction; evaluated at
commit against the rows as they are then. The role is resolved through
``rol.nombre_rol``, never through a numeric id.

The function takes ``FOR NO KEY UPDATE`` on the account row before counting its
links. Two transactions touching the links of the same account therefore check
one after the other, and the second one counts what the first committed.
``NO KEY`` and not ``FOR UPDATE`` on purpose: inserting a link takes
``FOR KEY SHARE`` on the account, and ``FOR UPDATE`` would conflict with the
other transaction's key share and turn every such race into a deadlock.

Existing accounts are validated first, under a lock that keeps writers out
until the triggers exist, and an incoherent account aborts the migration. It is
reported, never corrected: re-linking a person to a clinical profile is not a
decision a migration may take.

No statement here uses ``%`` or a bind-like colon, so the same text runs online
through psycopg and renders offline with ``--sql``.

**Downgrade** drops the triggers, the function and the CHECK, without CASCADE
and without IF EXISTS. It does not restore the original capitalisation of any
address: canonical values are valid under the previous revision too, and the
previous form is not known anymore.

Revision ID: 54053d46abd6
Revises: 60facdbacf51
Create Date: 2026-09-15 14:19:40.124370

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '54053d46abd6'
down_revision: Union[str, None] = '60facdbacf51'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_OPERACIONAL = 'operacional'

# The names and texts below are literals, like the rest of the chain: a
# migration keeps describing the DDL it applied, whatever the application
# constants become later.
NOMBRE_CHECK_EMAIL = 'ck_usuario_email_canonico'
CONDICION_EMAIL_CANONICO = r"email <> '' AND email = lower(email) AND email !~ '\s'"
LONGITUD_MAXIMA_EMAIL = 120

FUNCION_ROL_VINCULO = f'{SCHEMA_OPERACIONAL}.validar_rol_vinculo_usuario'
# Reported as the constraint name of the error the function raises, so the API
# can recognise this rule without reading the driver's message.
RESTRICCION_ROL_VINCULO = 'rol_vinculo_coherente'

TRIGGERS_ROL_VINCULO = (
    ('trg_usuario_rol_vinculo', 'usuario', 'INSERT OR UPDATE OF id_rol, id_usuario'),
    ('trg_usuario_paciente_rol_vinculo', 'usuario_paciente', 'INSERT OR UPDATE OR DELETE'),
    ('trg_usuario_medico_rol_vinculo', 'usuario_medico', 'INSERT OR UPDATE OR DELETE'),
)

EMAIL_NORMALIZADO = r"lower(regexp_replace(email, '^\s+|\s+$', '', 'g'))"


def vinculo_coherente(rol: str, pacientes: str, medicos: str) -> str:
    """The table of the docstring, written once for the check and the function."""
    return (
        f"(({rol} = 'PACIENTE' AND {pacientes} = 1 AND {medicos} = 0)"
        f" OR ({rol} = 'MEDICO' AND {pacientes} = 0 AND {medicos} = 1)"
        f" OR ({rol} = 'ADMIN' AND {pacientes} = 0 AND {medicos} = 0))"
    )


BLOQUEAR_CUENTAS = (
    f"LOCK TABLE {SCHEMA_OPERACIONAL}.usuario, {SCHEMA_OPERACIONAL}.usuario_paciente, "
    f"{SCHEMA_OPERACIONAL}.usuario_medico IN SHARE ROW EXCLUSIVE MODE"
)

VERIFICAR_EMAILS = f"""
DO $verificacion$
DECLARE
    v_vacios integer;
    v_con_espacios integer;
    v_demasiado_largos integer;
    v_colisiones integer;
BEGIN
    SELECT count(*) FILTER (WHERE normalizado = ''),
           count(*) FILTER (WHERE normalizado ~ '\\s'),
           count(*) FILTER (WHERE length(normalizado) > {LONGITUD_MAXIMA_EMAIL})
      INTO v_vacios, v_con_espacios, v_demasiado_largos
      FROM (SELECT {EMAIL_NORMALIZADO} AS normalizado
              FROM {SCHEMA_OPERACIONAL}.usuario) AS cuentas;

    SELECT count(*)
      INTO v_colisiones
      FROM (SELECT 1
              FROM {SCHEMA_OPERACIONAL}.usuario
             GROUP BY {EMAIL_NORMALIZADO}
            HAVING count(*) > 1) AS repetidos;

    IF v_vacios + v_con_espacios + v_demasiado_largos + v_colisiones > 0 THEN
        RAISE EXCEPTION USING MESSAGE =
            'SCRUM-97: no se normalizan los correos de acceso. Vacios: ' || v_vacios
            || '; con espacios internos: ' || v_con_espacios
            || '; demasiado largos: ' || v_demasiado_largos
            || '; grupos que colisionan: ' || v_colisiones
            || '. Corrija las cuentas y vuelva a migrar; no se modifico nada.';
    END IF;
END
$verificacion$
"""

NORMALIZAR_EMAILS = (
    f"UPDATE {SCHEMA_OPERACIONAL}.usuario SET email = {EMAIL_NORMALIZADO} "
    f"WHERE email <> {EMAIL_NORMALIZADO}"
)

VERIFICAR_VINCULOS = f"""
DO $verificacion$
DECLARE
    v_incoherentes integer;
BEGIN
    SELECT count(*)
      INTO v_incoherentes
      FROM {SCHEMA_OPERACIONAL}.usuario u
      JOIN {SCHEMA_OPERACIONAL}.rol r ON r.id_rol = u.id_rol
     WHERE NOT {vinculo_coherente(
        'r.nombre_rol',
        f'(SELECT count(*) FROM {SCHEMA_OPERACIONAL}.usuario_paciente p WHERE p.id_usuario = u.id_usuario)',
        f'(SELECT count(*) FROM {SCHEMA_OPERACIONAL}.usuario_medico m WHERE m.id_usuario = u.id_usuario)',
     )};

    IF v_incoherentes > 0 THEN
        RAISE EXCEPTION USING MESSAGE =
            'SCRUM-97: ' || v_incoherentes || ' cuenta(s) no tienen el vinculo clinico '
            || 'que exige su rol. No se corrige automaticamente; no se modifico nada.';
    END IF;
END
$verificacion$
"""

CREAR_FUNCION = f"""
CREATE FUNCTION {FUNCION_ROL_VINCULO}() RETURNS trigger
LANGUAGE plpgsql
AS $funcion$
DECLARE
    v_cuentas integer[];
    v_id integer;
    v_rol varchar;
    v_pacientes integer;
    v_medicos integer;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_cuentas := ARRAY[OLD.id_usuario];
    ELSIF TG_OP = 'UPDATE' THEN
        v_cuentas := ARRAY[OLD.id_usuario, NEW.id_usuario];
    ELSE
        v_cuentas := ARRAY[NEW.id_usuario];
    END IF;

    FOREACH v_id IN ARRAY v_cuentas LOOP
        SELECT r.nombre_rol
          INTO v_rol
          FROM {SCHEMA_OPERACIONAL}.usuario u
          JOIN {SCHEMA_OPERACIONAL}.rol r ON r.id_rol = u.id_rol
         WHERE u.id_usuario = v_id
           FOR NO KEY UPDATE OF u;

        -- The account is gone: its links went with it, nothing left to check.
        CONTINUE WHEN NOT FOUND;

        SELECT count(*) INTO v_pacientes
          FROM {SCHEMA_OPERACIONAL}.usuario_paciente
         WHERE id_usuario = v_id;
        SELECT count(*) INTO v_medicos
          FROM {SCHEMA_OPERACIONAL}.usuario_medico
         WHERE id_usuario = v_id;

        IF NOT {vinculo_coherente('v_rol', 'v_pacientes', 'v_medicos')} THEN
            RAISE EXCEPTION USING
                ERRCODE = 'check_violation',
                CONSTRAINT = '{RESTRICCION_ROL_VINCULO}',
                MESSAGE = 'La cuenta no tiene exactamente el vinculo clinico que exige su rol.';
        END IF;
    END LOOP;

    RETURN NULL;
END
$funcion$
"""


def crear_trigger(nombre: str, tabla: str, eventos: str) -> str:
    return (
        f"CREATE CONSTRAINT TRIGGER {nombre} "
        f"AFTER {eventos} ON {SCHEMA_OPERACIONAL}.{tabla} "
        "DEFERRABLE INITIALLY DEFERRED "
        f"FOR EACH ROW EXECUTE FUNCTION {FUNCION_ROL_VINCULO}()"
    )


def upgrade() -> None:
    op.execute(BLOQUEAR_CUENTAS)

    op.execute(VERIFICAR_EMAILS)
    op.execute(NORMALIZAR_EMAILS)
    op.create_check_constraint(
        op.f(NOMBRE_CHECK_EMAIL),
        'usuario',
        sa.text(CONDICION_EMAIL_CANONICO),
        schema=SCHEMA_OPERACIONAL,
    )

    op.execute(VERIFICAR_VINCULOS)
    op.execute(CREAR_FUNCION)
    for nombre, tabla, eventos in TRIGGERS_ROL_VINCULO:
        op.execute(crear_trigger(nombre, tabla, eventos))


def downgrade() -> None:
    for nombre, tabla, _ in reversed(TRIGGERS_ROL_VINCULO):
        op.execute(f"DROP TRIGGER {nombre} ON {SCHEMA_OPERACIONAL}.{tabla}")
    op.execute(f"DROP FUNCTION {FUNCION_ROL_VINCULO}()")
    op.drop_constraint(
        op.f(NOMBRE_CHECK_EMAIL), 'usuario', schema=SCHEMA_OPERACIONAL, type_='check'
    )
