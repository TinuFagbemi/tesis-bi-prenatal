"""The cluster-global roles FetalAlert expects, and how to check them (SCRUM-98).

**Two families of role, and the difference decides what each may do.**

*Runtime roles* -- ``fetalalert_api``, ``fetalalert_etl``, ``fetalalert_powerbi``
and the restricted role the tests connect as -- are accounts. Something logs in
as them and serves or reads. For them the rules are absolute: no ``SUPERUSER``,
no ``BYPASSRLS``, no ownership of anything in ``operacional`` or ``analitico``,
**no ``CREATE`` on any schema**, and no membership that would let them assume a
privileged role.

*Technical NOLOGIN roles* -- ``fetalalert_rls_owner``,
``fetalalert_provision_owner`` and ``fetalalert_mantenimiento`` -- are not
accounts but capabilities. Nobody authenticates as them. The first two own the
``SECURITY DEFINER`` helpers, so a helper's privilege is attached to the helper
rather than to whoever calls it; the third is the target of the maintenance
policy.

**The correction that experiment forced (SCRUM-98).** An earlier version of this
design said the owner roles must have no ``CREATE`` anywhere. That is
incompatible with owning a function, and PostgreSQL says so plainly. Reproduced
on PostgreSQL 16 with a real non-superuser migrator:

* ``ALTER FUNCTION seguridad.f() OWNER TO fetalalert_rls_owner`` without a
  membership carrying ``SET`` fails with ``must be able to SET ROLE``;
* with ``SET`` but without ``CREATE`` on the schema it fails with
  ``permission denied for schema seguridad``;
* with both, it succeeds and ``pg_proc.proowner`` becomes the intended role.

So the two owner roles need ``CREATE`` on the schema that holds the helpers, and
**only** on that one. The prohibition stays absolute for every runtime role and
for ``fetalalert_mantenimiento``, which owns nothing.

**Membership options matter, and PostgreSQL 16 separates them.** ``INHERIT``
means the privileges apply without asking; ``SET`` means ``SET ROLE`` is
allowed. ``fetalalert_mantenimiento`` is granted with ``INHERIT TRUE`` because a
policy addressed ``TO`` a role is evaluated against the roles the user
*inherits*. The two owner roles are granted with ``INHERIT FALSE, SET TRUE``:
enough to transfer ownership deliberately, not enough to drag their privileges
into every ordinary statement.

**ADMIN is the migrator's and nobody else's.** A role with ``CREATEROLE``
keeps an implicit grant on everything it creates, and PostgreSQL 16 gives that
grant ``admin_option = true`` -- which is what lets the same migrator re-run the
bootstrap. The bootstrap itself grants ``WITH ADMIN FALSE``, so it never widens
that, and the consequence is worth stating plainly: the capability to grant
these roles onward belongs to whoever created them, and must never reach
``fetalalert_api``, ``fetalalert_etl``, ``fetalalert_powerbi`` or the restricted
test role, directly or through a chain. :meth:`InformeDeRoles.administradores_indebidos`
checks the direct case and :meth:`InformeDeRoles.alcances_indebidos` the
indirect one -- a runtime role that cannot reach a technical role at all cannot
administer it either.

**This module never writes.** Creating the roles belongs to
``scripts/bootstrap_roles.sql``; granting privileges on schemas, tables,
sequences and functions belongs to the Alembic revisions. What lives here is the
shared vocabulary plus a read-only evaluation, so the bootstrap runner, the
API's start-up check and the tests all assert against one definition instead of
three copies that can drift.

All accounts and data in this project are simulated and fictitious.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection

# ---------------------------------------------------------------------------
# Technical NOLOGIN roles
# ---------------------------------------------------------------------------

# Owner of the five context helpers. Reads, and nothing else.
ROL_RLS_OWNER = "fetalalert_rls_owner"

# Owner of the two provisioning helpers. Separate from the previous one on
# purpose: taking ``FOR NO KEY UPDATE`` requires UPDATE on at least one column,
# and that privilege must not reach the roles that only need to read.
ROL_PROVISION_OWNER = "fetalalert_provision_owner"

# Target of the maintenance policy, so a migrator or dataset loader that is not
# a superuser can still write the simulated dataset once RLS is enabled. It owns
# nothing, so it never needs CREATE.
ROL_MANTENIMIENTO = "fetalalert_mantenimiento"

ROLES_NOLOGIN = frozenset({ROL_RLS_OWNER, ROL_PROVISION_OWNER, ROL_MANTENIMIENTO})

# The subset that owns objects, and therefore the only roles allowed CREATE on
# the schema holding the helpers.
ROLES_DUENIOS_DE_HELPERS = frozenset({ROL_RLS_OWNER, ROL_PROVISION_OWNER})

# Schema the helpers will live in. Created by the Alembic revision of a later
# sub-phase; named here so the privilege rule has one spelling.
ESQUEMA_DE_HELPERS = "seguridad"

# ---------------------------------------------------------------------------
# Roles that connect
# ---------------------------------------------------------------------------

# Created by deployment or CI from external secrets, never by this repository.
# Named here only so a check can assert what they must *not* be.
ROL_API = "fetalalert_api"
ROL_ETL = "fetalalert_etl"
ROL_POWERBI = "fetalalert_powerbi"

# There is no test role here, and that absence is the point. An earlier draft
# added ``fetalalert_rls_test`` so a suite could connect as something other than
# the owner; it was removed because a dedicated role means the isolation being
# demonstrated is that role's, not the API's, and because it forced every
# deployment to provision a role that only CI would ever use. The isolation
# suites connect as ``fetalalert_api`` against a disposable cluster.
ROLES_RUNTIME = frozenset({ROL_API, ROL_ETL, ROL_POWERBI})
# Kept under its previous name for callers that already import it.
ROLES_CON_LOGIN = ROLES_RUNTIME

# Roles a runtime credential must never be able to assume or inherit. The
# migrator is not listed by name -- it changes between environments -- and is
# detected through object ownership instead.
ROLES_PRIVILEGIADOS = ROLES_NOLOGIN

# ---------------------------------------------------------------------------
# Attributes and membership options
# ---------------------------------------------------------------------------

# Column of ``pg_roles`` -> capability it grants. A NOLOGIN role of this project
# must have none of them: not one is needed to own a function or to receive a
# policy, and every one of them would widen what a compromised helper could do.
CAPACIDADES_PROHIBIDAS = {
    "rolcanlogin": "LOGIN",
    "rolsuper": "SUPERUSER",
    "rolbypassrls": "BYPASSRLS",
    "rolcreaterole": "CREATEROLE",
    "rolcreatedb": "CREATEDB",
    "rolreplication": "REPLICATION",
}

# Role -> (inherit_option, set_option) the migrator must hold on it. See the
# module docstring for why each pair is what it is.
MEMBRESIAS_DEL_MIGRADOR = {
    ROL_MANTENIMIENTO: (True, True),
    ROL_RLS_OWNER: (False, True),
    ROL_PROVISION_OWNER: (False, True),
}

_CONSULTA_ROLES = text(
    """
    SELECT rolname, rolcanlogin, rolsuper, rolbypassrls,
           rolcreaterole, rolcreatedb, rolreplication
    FROM pg_roles
    WHERE rolname = ANY(:nombres)
    """
)

# Membership is read from ``pg_auth_members`` and not with ``pg_has_role``:
# ``pg_has_role`` answers true for every superuser, so it would report a
# membership that was never granted and hide that the bootstrap did not run.
#
# A role may hold more than one grant on the same target -- PostgreSQL 16 adds a
# separate row for the implicit grant a CREATEROLE creator receives -- so the
# options are aggregated with ``bool_or``: what matters is the effective
# capability, not which row carries it.
_CONSULTA_MIEMBROS = text(
    """
    SELECT receptor.rolname         AS miembro,
           bool_or(vinculo.inherit_option) AS hereda,
           bool_or(vinculo.set_option)     AS puede_set,
           bool_or(vinculo.admin_option)   AS administra
    FROM pg_auth_members vinculo
    JOIN pg_roles concedido ON concedido.oid = vinculo.roleid
    JOIN pg_roles receptor  ON receptor.oid  = vinculo.member
    WHERE concedido.rolname = :rol
    GROUP BY receptor.rolname
    """
)


# Un rol técnico no debe ser miembro de nada. Serlo le daría, por herencia, lo
# que ese otro rol alcance -- y los helpers se ejecutan con su identidad.
_CONSULTA_PERTENENCIAS = text(
    """
    SELECT concedido.rolname AS concedido
    FROM pg_auth_members vinculo
    JOIN pg_roles concedido ON concedido.oid = vinculo.roleid
    JOIN pg_roles receptor  ON receptor.oid  = vinculo.member
    WHERE receptor.rolname = :rol
    """
)

# Alcance **efectivo**, que cubre cadenas indirectas: A miembro de B y B miembro
# del rol técnico. Una comparación de miembros directos no vería ese camino.
_CONSULTA_ALCANCE = text(
    """
    SELECT candidato.rolname AS rol,
           objetivo.rolname  AS objetivo,
           pg_has_role(candidato.oid, objetivo.oid, 'MEMBER') AS membresia,
           pg_has_role(candidato.oid, objetivo.oid, 'USAGE')  AS hereda,
           pg_has_role(candidato.oid, objetivo.oid, 'SET')    AS puede_set
    FROM pg_roles candidato, pg_roles objetivo
    WHERE candidato.rolname = ANY(:candidatos)
      AND objetivo.rolname  = ANY(:objetivos)
    """
)


# Centinela: distingue "no se pasó el argumento" de "se pasó None", que
# significa deliberadamente «no exijas ninguna membresía».
_POR_OMISION = object()


@dataclass(frozen=True)
class OpcionesDeMembresia:
    """What one role effectively holds on another."""

    hereda: bool
    puede_set: bool
    administra: bool


@dataclass(frozen=True)
class EstadoDeRol:
    """One role as the catalogue describes it."""

    nombre: str
    capacidades_indebidas: tuple[str, ...]

    @property
    def conforme(self) -> bool:
        return not self.capacidades_indebidas


@dataclass(frozen=True)
class InformeDeRoles:
    """What the cluster looks like, and whether that is acceptable.

    Acceptable means four things at once, and each is a way the role graph could
    be wrong:

    * the three technical roles exist and carry no forbidden capability;
    * the migrator holds exactly the memberships it needs, with exactly the
      ``INHERIT``/``SET`` options the design calls for -- a missing one breaks
      the migration, a wider one grants more than intended;
    * **nobody else** is a direct member. Not "no known runtime name": any
      unexpected member at all, because a hole does not have to be called
      ``fetalalert_api`` to be a hole;
    * no runtime role reaches a technical role, directly or **through a chain**,
      and no technical role is itself a member of anything.
    """

    roles: dict[str, EstadoDeRol]
    # Role name -> {member -> options}. Covers the three technical roles.
    membresias: dict[str, dict[str, OpcionesDeMembresia]]
    # Technical role -> roles it is itself a member of. Should always be empty.
    pertenencias: dict[str, frozenset[str]]
    # Runtime role -> technical roles it reaches by any route.
    alcance_de_runtime: dict[str, frozenset[str]]
    # The migrator this evaluation expected to find, or ``None`` when the caller
    # did not name one.
    migrador_esperado: str | None

    @property
    def miembros_de_mantenimiento(self) -> frozenset[str]:
        return frozenset(self.membresias.get(ROL_MANTENIMIENTO, {}))

    @property
    def ausentes(self) -> frozenset[str]:
        return frozenset(ROLES_NOLOGIN - self.roles.keys())

    @property
    def no_conformes(self) -> frozenset[str]:
        return frozenset(
            nombre for nombre, estado in self.roles.items() if not estado.conforme
        )

    def opciones_de(self, rol: str, miembro: str) -> OpcionesDeMembresia | None:
        return self.membresias.get(rol, {}).get(miembro)

    def membresias_faltantes(self) -> tuple[str, ...]:
        """Memberships the migrator needs and does not have, or has wrong.

        Without this, ``verificar`` would answer ``conforme=true`` on a cluster
        where the roles exist but nobody can create the helpers -- precisely the
        state the migration would then fail on.
        """
        if self.migrador_esperado is None:
            return ()
        problemas = []
        for rol, esperado in sorted(MEMBRESIAS_DEL_MIGRADOR.items()):
            hereda, puede_set = esperado
            opciones = self.opciones_de(rol, self.migrador_esperado)
            if opciones is None:
                problemas.append(
                    f"{self.migrador_esperado} no es miembro de {rol}; ejecute "
                    "'python scripts/bootstrap_roles.py aplicar'"
                )
                continue
            if opciones.hereda != hereda or opciones.puede_set != puede_set:
                problemas.append(
                    f"la membresia de {self.migrador_esperado} en {rol} tiene "
                    f"INHERIT={opciones.hereda}, SET={opciones.puede_set}; se "
                    f"esperaba INHERIT={hereda}, SET={puede_set}"
                )
        return tuple(problemas)

    def miembros_inesperados(self) -> tuple[str, ...]:
        """Any direct member that is not the expected migrator.

        Deliberately not a deny-list of known runtime names: an unknown role
        with a grant on a helper owner is exactly as dangerous, and far more
        likely to go unnoticed.
        """
        if self.migrador_esperado is None:
            # El llamante pidió describir, no juzgar: sin una identidad esperada
            # no hay forma de distinguir al migrador legítimo de un intruso.
            return ()
        permitidos = {self.migrador_esperado}
        problemas = []
        for rol, miembros in sorted(self.membresias.items()):
            for miembro in sorted(set(miembros) - permitidos):
                problemas.append(f"{miembro} es miembro inesperado de {rol}")
        return tuple(problemas)

    def administradores_indebidos(self) -> tuple[str, ...]:
        """Anyone other than the expected migrator holding ADMIN on a technical role.

        ADMIN is the right to grant the role onward. The migrator has it because
        it created the roles, and that is the capability that lets it re-run the
        bootstrap; anybody else holding it could hand a helper owner to a third
        role without the bootstrap ever knowing.
        """
        if self.migrador_esperado is None:
            return ()
        problemas = []
        for rol, miembros in sorted(self.membresias.items()):
            for miembro, opciones in sorted(miembros.items()):
                if opciones.administra and miembro != self.migrador_esperado:
                    problemas.append(f"{miembro} administra {rol}")
        return tuple(problemas)

    def alcances_indebidos(self) -> tuple[str, ...]:
        """Runtime roles that reach a technical role by any route."""
        problemas = []
        for runtime, alcanzados in sorted(self.alcance_de_runtime.items()):
            for objetivo in sorted(alcanzados):
                problemas.append(f"{runtime} alcanza {objetivo}")
        return tuple(problemas)

    def roles_contaminados(self) -> tuple[str, ...]:
        """Technical roles that are themselves members of something."""
        problemas = []
        for rol, concedidos in sorted(self.pertenencias.items()):
            for concedido in sorted(concedidos):
                problemas.append(f"{rol} es miembro de {concedido}")
        return tuple(problemas)

    @property
    def conforme(self) -> bool:
        return not self.motivo

    @property
    def motivo(self) -> str:
        """Why it is not acceptable, or an empty string when it is."""
        problemas = []
        if self.ausentes:
            problemas.append(
                "faltan los roles "
                + ", ".join(sorted(self.ausentes))
                + ". Ejecute 'python scripts/bootstrap_roles.py aplicar' antes de Alembic"
            )
        for nombre in sorted(self.no_conformes):
            capacidades = ", ".join(self.roles[nombre].capacidades_indebidas)
            problemas.append(
                f"el rol {nombre} tiene capacidades prohibidas: {capacidades}"
            )
        problemas.extend(self.membresias_faltantes())
        problemas.extend(self.miembros_inesperados())
        problemas.extend(self.administradores_indebidos())
        problemas.extend(self.alcances_indebidos())
        problemas.extend(self.roles_contaminados())
        return "; ".join(problemas)


def evaluar_roles(
    conexion: Connection, migrador_esperado: str | None = _POR_OMISION
) -> InformeDeRoles:
    """Read the technical roles, their memberships and who reaches them.

    ``migrador_esperado`` defaults to the connected user, which is who the
    bootstrap grants to. Passing ``None`` skips the membership requirement, for
    a caller that only wants to describe the cluster.

    A read, and only a read: it opens nothing, creates nothing and alters
    nothing, so it is safe to call from a start-up check and from a test.
    """
    if migrador_esperado is _POR_OMISION:
        migrador_esperado = conexion.execute(text("SELECT current_user")).scalar()

    filas = conexion.execute(
        _CONSULTA_ROLES, {"nombres": sorted(ROLES_NOLOGIN)}
    ).mappings()

    roles: dict[str, EstadoDeRol] = {}
    for fila in filas:
        indebidas = tuple(
            etiqueta
            for columna, etiqueta in CAPACIDADES_PROHIBIDAS.items()
            if fila[columna]
        )
        roles[fila["rolname"]] = EstadoDeRol(
            nombre=fila["rolname"], capacidades_indebidas=indebidas
        )

    membresias: dict[str, dict[str, OpcionesDeMembresia]] = {}
    pertenencias: dict[str, frozenset[str]] = {}
    for rol in sorted(ROLES_NOLOGIN):
        miembros = conexion.execute(_CONSULTA_MIEMBROS, {"rol": rol}).mappings()
        membresias[rol] = {
            fila["miembro"]: OpcionesDeMembresia(
                hereda=fila["hereda"],
                puede_set=fila["puede_set"],
                administra=fila["administra"],
            )
            for fila in miembros
        }
        pertenencias[rol] = frozenset(
            conexion.execute(_CONSULTA_PERTENENCIAS, {"rol": rol}).scalars()
        )

    # Alcance efectivo de los roles de runtime que existan. ``pg_has_role``
    # resuelve la cadena completa, asi que una membresia indirecta -- api
    # miembro de X, X miembro del rol tecnico -- tambien aparece aqui.
    crudo: dict[str, set[str]] = {}
    for fila in conexion.execute(
        _CONSULTA_ALCANCE,
        {"candidatos": sorted(ROLES_RUNTIME), "objetivos": sorted(ROLES_NOLOGIN)},
    ).mappings():
        if fila["membresia"] or fila["hereda"] or fila["puede_set"]:
            crudo.setdefault(fila["rol"], set()).add(fila["objetivo"])

    return InformeDeRoles(
        roles=roles,
        membresias=membresias,
        pertenencias=pertenencias,
        alcance_de_runtime={
            rol: frozenset(objetivos) for rol, objetivos in crudo.items()
        },
        migrador_esperado=migrador_esperado,
    )
