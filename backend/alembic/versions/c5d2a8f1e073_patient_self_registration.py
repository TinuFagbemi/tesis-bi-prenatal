"""Patient self-registration with versioned acceptances (SCRUM-110).

Revision ID: c5d2a8f1e073
Revises: 9c1d7f2ab4e8
Create Date: 2026-11-05

**What it adds.** A table that records which version of which legal text an
account accepted and when, and one SECURITY DEFINER function,
``seguridad.autorregistrar_paciente``, that creates in a single call the patient
profile, her mobile phone, an active PACIENTE account, the link between them and
the two acceptances. It returns two identifiers and nothing else.

**Why a function and not grants to the web process.** ``fetalalert_api`` has no
privilege at all on ``operacional.paciente`` or ``telefono_paciente``, and
SCRUM-98 made that absence the protection of those tables. Granting it INSERT
there -- and an INSERT policy on ``usuario_paciente`` without ``es_admin()`` --
would let any request of the web process write profiles and links in any shape.
The function instead fixes the shape: the role is always PACIENTE, the account is
always active, the profile is always new and always gets exactly one link, one
principal CELULAR phone and both acceptances. The web process receives
``EXECUTE`` on this function and **no new privilege on any table**.

**Who owns it.** ``fetalalert_provision_owner``, the NOLOGIN role that already
owns the two provisioning helpers of SCRUM-97: creating an account is the same
responsibility. No login credential is a member of it, and the runtime check of
``app.db.privilegios`` keeps refusing an API connected as anything that could
assume it. The role receives INSERT on the five tables involved, USAGE on their
four sequences, and one INSERT policy on ``usuario_paciente`` -- the only table
of the five with FORCE ROW LEVEL SECURITY. It receives no UPDATE, no DELETE and
no SELECT it did not already have.

**What this revision does not change.** ``POST /api/v1/cuentas/...`` keeps
requiring ADMIN, ``pol_provision`` keeps requiring ``es_admin()``, MEDICO gains
nothing, and the deferred role/link trigger of SCRUM-97 still validates every
account at commit. Only the two acceptances of this revision are new rows of a
new kind.

All data handled by this prototype is fictitious and simulated.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5d2a8f1e073"
down_revision: Union[str, None] = "9c1d7f2ab4e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The same names earlier revisions use, repeated on purpose: a migration is read
# on its own and must not import constants that could change after it.
OPERACIONAL = "operacional"
SEGURIDAD = "seguridad"
ROL_API = "fetalalert_api"
ROL_PROVISION_OWNER = "fetalalert_provision_owner"

TABLA = "aceptacion_documento"
FUNCION = f"{SEGURIDAD}.autorregistrar_paciente"
FIRMA = (
    f"{FUNCION}(varchar, varchar, varchar, varchar, varchar, date, varchar, "
    "varchar, varchar, varchar, varchar, varchar, varchar)"
)

# Tables the function writes, and the sequences behind their keys. ``serial``,
# not IDENTITY, so USAGE on the sequence is required and explicit.
TABLAS_ESCRITAS = (
    "paciente",
    "telefono_paciente",
    "usuario",
    "usuario_paciente",
    TABLA,
)
SECUENCIAS = (
    "paciente_id_paciente_seq",
    "telefono_paciente_id_telefono_paciente_seq",
    "usuario_id_usuario_seq",
    f"{TABLA}_id_aceptacion_seq",
)

# Name the function reports when its own checks refuse the input. The API
# validates the same rules first; this is the second layer, for a caller that
# would reach the function some other way.
RESTRICCION_DATOS = "autorregistro_datos_validos"


def _sql(sentencia: str) -> None:
    op.execute(sentencia)


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

PREFLIGHT = f"""
DO $preflight$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles
         WHERE rolname = '{ROL_PROVISION_OWNER}'
           AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
    ) THEN
        RAISE EXCEPTION
            'Migracion abortada: falta el rol {ROL_PROVISION_OWNER} o no es un '
            'rol NOLOGIN sin capacidades. Ejecuta scripts/bootstrap_roles.py.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROL_API}') THEN
        RAISE EXCEPTION 'Migracion abortada: falta el rol {ROL_API}.';
    END IF;
END
$preflight$;
"""


# ---------------------------------------------------------------------------
# The function
# ---------------------------------------------------------------------------

# ``pg_temp`` explicit and last, as in every helper of this project: otherwise a
# caller could shadow an unqualified object with a temporary one. Everything is
# schema-qualified anyway.
FUNCION_SQL = f"""
CREATE FUNCTION {FUNCION}(
    p_cedula varchar,
    p_primer_nombre varchar,
    p_segundo_nombre varchar,
    p_apellido_paterno varchar,
    p_apellido_materno varchar,
    p_fecha_nac date,
    p_email varchar,
    p_celular varchar,
    p_password_hash varchar,
    p_version_privacidad varchar,
    p_sha256_privacidad varchar,
    p_version_terminos varchar,
    p_sha256_terminos varchar
)
RETURNS TABLE (id_paciente_creado integer, id_usuario_creado integer)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, operacional, pg_temp
AS $funcion$
DECLARE
    v_paciente integer;
    v_usuario integer;
    v_rol integer;
BEGIN
    -- The same rules the API applies, checked again here. Required texts may
    -- not be blank, optional ones arrive NULL or with content, the birth date
    -- is not after today in Panama and the password is an Argon2id digest,
    -- never a clear value.
    IF p_cedula IS NULL OR btrim(p_cedula) = ''
       OR p_primer_nombre IS NULL OR btrim(p_primer_nombre) = ''
       OR p_apellido_paterno IS NULL OR btrim(p_apellido_paterno) = ''
       OR (p_segundo_nombre IS NOT NULL AND btrim(p_segundo_nombre) = '')
       OR (p_apellido_materno IS NOT NULL AND btrim(p_apellido_materno) = '')
       OR p_celular IS NULL OR btrim(p_celular) = ''
       OR p_email IS NULL
       OR p_fecha_nac IS NULL
       OR p_fecha_nac > (now() AT TIME ZONE 'America/Panama')::date
       OR p_password_hash IS NULL OR left(p_password_hash, 10) <> '$argon2id$'
       OR p_version_privacidad IS NULL OR p_sha256_privacidad IS NULL
       OR p_version_terminos IS NULL OR p_sha256_terminos IS NULL
    THEN
        RAISE EXCEPTION USING
            ERRCODE = 'check_violation',
            CONSTRAINT = '{RESTRICCION_DATOS}',
            MESSAGE = 'Los datos del autorregistro no cumplen el contrato.';
    END IF;

    -- By name, never by a numeric id: the catalogue's ids are the dataset's.
    SELECT r.id_rol INTO STRICT v_rol
      FROM operacional.rol r
     WHERE r.nombre_rol = 'PACIENTE';

    INSERT INTO operacional.paciente (
        cedula, primer_nombre, segundo_nombre, apellido_paterno,
        apellido_materno, email_pac, fecha_nac
    ) VALUES (
        p_cedula, p_primer_nombre, p_segundo_nombre, p_apellido_paterno,
        p_apellido_materno, p_email, p_fecha_nac
    )
    RETURNING paciente.id_paciente INTO v_paciente;

    INSERT INTO operacional.telefono_paciente (
        id_paciente, tipo_contacto, valor_contacto, principal
    ) VALUES (v_paciente, 'CELULAR', p_celular, true);

    INSERT INTO operacional.usuario (id_rol, email, password_hash, activo)
    VALUES (v_rol, p_email, p_password_hash, true)
    RETURNING usuario.id_usuario INTO v_usuario;

    INSERT INTO operacional.usuario_paciente (id_usuario, id_paciente)
    VALUES (v_usuario, v_paciente);

    INSERT INTO operacional.{TABLA} (id_usuario, documento, version, sha256)
    VALUES
        (v_usuario, 'POLITICA_PRIVACIDAD', p_version_privacidad, p_sha256_privacidad),
        (v_usuario, 'TERMINOS_CONDICIONES', p_version_terminos, p_sha256_terminos);

    id_paciente_creado := v_paciente;
    id_usuario_creado := v_usuario;
    RETURN NEXT;
END
$funcion$;

COMMENT ON FUNCTION {FIRMA} IS
'Autorregistro de una paciente (SCRUM-110): crea perfil, celular principal, '
'cuenta PACIENTE activa, vinculo y las dos aceptaciones, y devuelve solo los dos '
'identificadores. El rol y el estado los fija la funcion; no puede vincular un '
'perfil existente. Pertenece a un rol NOLOGIN y solo la API puede ejecutarla.';
"""


def _permisos_de_la_funcion() -> list[str]:
    """Granted **before** the ownership transfer, as SCRUM-98 established.

    ``GRANT`` needs to be the owner, and run after ``ALTER OWNER`` it only warns:
    ``PUBLIC`` would keep its default EXECUTE on a SECURITY DEFINER function.
    """
    return [
        f"REVOKE ALL ON FUNCTION {FIRMA} FROM PUBLIC",
        f"GRANT EXECUTE ON FUNCTION {FIRMA} TO {ROL_API}",
    ]


def _privilegios_del_dueno() -> list[str]:
    o = OPERACIONAL
    sentencias = [
        f"GRANT INSERT ON TABLE {o}.{tabla} TO {ROL_PROVISION_OWNER}"
        for tabla in TABLAS_ESCRITAS
    ]
    sentencias += [
        f"GRANT USAGE ON SEQUENCE {o}.{secuencia} TO {ROL_PROVISION_OWNER}"
        for secuencia in SECUENCIAS
    ]
    # ``RETURNING`` needs SELECT on the column it returns. The owner already has
    # SELECT on ``paciente`` and ``usuario`` since SCRUM-98, so nothing is added
    # for it; the PostgreSQL suite of SCRUM-110 fails if that grant disappears.
    return sentencias


def _revocaciones_del_dueno() -> list[str]:
    o = OPERACIONAL
    sentencias = [
        f"REVOKE USAGE ON SEQUENCE {o}.{secuencia} FROM {ROL_PROVISION_OWNER}"
        for secuencia in SECUENCIAS
        if not secuencia.startswith(TABLA)
    ]
    sentencias += [
        f"REVOKE INSERT ON TABLE {o}.{tabla} FROM {ROL_PROVISION_OWNER}"
        for tabla in TABLAS_ESCRITAS
        if tabla != TABLA
    ]
    return sentencias


# ``usuario_paciente`` carries FORCE ROW LEVEL SECURITY, so the owner of the
# function needs a policy to insert there. Addressed to that role only; no login
# role holds it, and the function is the only code that runs as it.
POLITICA = (
    f"CREATE POLICY pol_autorregistro ON {OPERACIONAL}.usuario_paciente "
    f"FOR INSERT TO {ROL_PROVISION_OWNER} WITH CHECK (true)"
)
POLITICA_ATRAS = f"DROP POLICY pol_autorregistro ON {OPERACIONAL}.usuario_paciente"


def upgrade() -> None:
    _sql(PREFLIGHT)

    op.create_table(
        TABLA,
        sa.Column("id_aceptacion", sa.Integer(), nullable=False),
        sa.Column("id_usuario", sa.Integer(), nullable=False),
        sa.Column(
            "documento",
            sa.Enum(
                "POLITICA_PRIVACIDAD",
                "TERMINOS_CONDICIONES",
                name="documento_legal",
                native_enum=False,
                create_constraint=True,
                length=30,
            ),
            nullable=False,
        ),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "fecha_hora",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_aceptacion_documento_sha256_hex")
        ),
        sa.CheckConstraint(
            "btrim(version) <> ''",
            name=op.f("ck_aceptacion_documento_version_no_vacia"),
        ),
        sa.ForeignKeyConstraint(
            ["id_usuario"],
            [f"{OPERACIONAL}.usuario.id_usuario"],
            name=op.f("fk_aceptacion_documento_id_usuario_usuario"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id_aceptacion", name=op.f("pk_aceptacion_documento")),
        sa.UniqueConstraint(
            "id_usuario",
            "documento",
            "version",
            name="uq_aceptacion_documento_id_usuario_documento_version",
        ),
        schema=OPERACIONAL,
    )
    op.create_index(
        op.f("ix_aceptacion_documento_id_usuario"),
        TABLA,
        ["id_usuario"],
        unique=False,
        schema=OPERACIONAL,
    )

    for sentencia in _privilegios_del_dueno():
        _sql(sentencia)
    _sql(POLITICA)

    _sql(FUNCION_SQL)
    for sentencia in _permisos_de_la_funcion():
        _sql(sentencia)
    _sql(f"ALTER FUNCTION {FIRMA} OWNER TO {ROL_PROVISION_OWNER}")


def downgrade() -> None:
    _sql(f"DROP FUNCTION {FIRMA}")
    _sql(POLITICA_ATRAS)
    for sentencia in _revocaciones_del_dueno():
        _sql(sentencia)
    # The table takes its own sequence, index, constraints and grants with it.
    op.drop_index(
        op.f("ix_aceptacion_documento_id_usuario"),
        table_name=TABLA,
        schema=OPERACIONAL,
    )
    op.drop_table(TABLA, schema=OPERACIONAL)
