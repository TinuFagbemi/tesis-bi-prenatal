"""What the API's own database credential must not be (SCRUM-98).

Row-level security is only worth what the connecting role is worth. A
``SUPERUSER`` ignores every policy, a role with ``BYPASSRLS`` ignores every
policy, and the owner of a table ignores its policies unless the table was
declared ``FORCE ROW LEVEL SECURITY``. So a deployment that points the API at
the credential that owns the schema gets green tests and no isolation at all.

**Names are not checked; effective privilege is.** A role called
``fetalalert_api`` that happens to be a member of ``fetalalert_rls_owner`` is
not restricted, however innocent its name. So the question asked of the
catalogue is what this role can actually reach.

**And the three privilege types of ``pg_has_role`` are not interchangeable.**
Reproduced on PostgreSQL 16, granting the same role three different ways:

===========================  ========  =======  =====
grant                        MEMBER    USAGE    SET
===========================  ========  =======  =====
``INHERIT FALSE, SET FALSE``  true      false    false
``INHERIT TRUE,  SET FALSE``  true      true     false
``INHERIT FALSE, SET TRUE``   true      false    true
(no membership)               false     false    false
===========================  ========  =======  =====

So ``MEMBER`` only says a grant exists; it is ``SET`` that answers "can this
role become that one" and ``USAGE`` that answers "does it already carry its
privileges". Using ``MEMBER`` for either question over-reports. All three are
asked here: the first two because they are the real capabilities, and
``MEMBER`` as an extra prohibition -- a runtime credential has no business
holding a grant on a privileged role even with both options off, because
turning them on is one ``GRANT`` away.

**Deployed environments fail closed, without exception.** A connection that
cannot be opened, a query that times out, a catalogue that answers nothing, a
row that comes back incomplete: none of these is evidence of a restricted
credential, so none of them may let the process serve. The database can be down
at start-up and come back a second later, and a process that started anyway
would then be serving on a credential nobody ever checked.

**The one permissiveness, and it is temporary.** In ``development``, ``test``
and ``ci`` a violation is reported at ``ERROR`` and the process continues,
because the restricted role only becomes usable once the Alembic revisions have
granted it anything at all. That window belongs to the migration of this project
to separated credentials and must be closed before SCRUM-98 is finished; see
:data:`PERMISIVIDAD_TRANSITORIA_SCRUM98`.

All data in this project is simulated and fictitious.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool

from app.config import AMBIENTES_PERMITIDOS
from app.db.base import SCHEMA_OPERACIONAL
from app.db.base_analitica import SCHEMA_ANALITICO
from app.db.roles import ESQUEMA_DE_HELPERS, ROLES_PRIVILEGIADOS

registrador = logging.getLogger(__name__)

# **La deuda de SCRUM-98, ya saldada.**
#
# Mientras la revisión de la sub-fase 3 no existía, apuntar ``DATABASE_URL`` al
# rol restringido dejaba a la API sin poder leer una sola fila, así que los
# ambientes simulados avisaban y seguían. Esa tolerancia tenía una condición
# escrita: no podía llegar al commit final del ticket.
#
# Los GRANT existen, las políticas están desplegadas y hay una suite que recorre
# las rutas reales conectada como ``fetalalert_api``. La constante queda en
# ``False`` y el fallo cerrado rige en todas partes: un proceso que arranque con
# privilegios que no le tocan no sigue adelante con un aviso, se detiene.
#
# Se conserva la constante, en vez de borrarla junto con la rama que gobierna,
# porque la prueba que la nombra es lo que impide que vuelva a ponerse en cierto
# sin que nadie lo note.
PERMISIVIDAD_TRANSITORIA_SCRUM98 = False

# **Dos cotas, porque son dos esperas distintas.**
#
# ``statement_timeout`` acota una sentencia que ya se está ejecutando; no limita
# en absoluto el tiempo de *establecer* la conexión. Un servidor que acepta el
# TCP y nunca completa el saludo dejaría el arranque colgado indefinidamente con
# solo la primera, así que hace falta la segunda.
#
# ``connect_timeout`` es un parámetro de libpq y se pasa como ``connect_args``,
# es decir al construir el engine: no puede fijarse por conexión. Por eso la
# comprobación construye su propio engine efímero en lugar de reutilizar el de
# la aplicación, cuyo pool no debe heredar esta cota.
SEGUNDOS_MAXIMOS_DE_CONEXION = 5
TIEMPO_MAXIMO_DE_COMPROBACION = "5s"

# Todos los schemas que este proyecto llegará a tener. Los que aún no existen se
# ignoran sin error: ``has_schema_privilege`` lanza sobre un schema inexistente,
# así que la consulta filtra por ``pg_namespace`` antes de preguntar.
ESQUEMAS_DEL_PROYECTO = (
    SCHEMA_OPERACIONAL,
    SCHEMA_ANALITICO,
    ESQUEMA_DE_HELPERS,
    "privado",
    "publicacion",
)

# ``public`` se comprueba aparte y siempre: existe en toda base, y un runtime
# que pueda crear ahí tiene dónde dejar objetos propios.
ESQUEMAS_VIGILADOS = (*ESQUEMAS_DEL_PROYECTO, "public")

# Clases de objeto cuyo propietario importa. Además de tablas: vistas, vistas
# materializadas, secuencias, particiones y tablas foráneas. Poseer cualquiera
# de ellas en un schema del proyecto es poseer una vía de acceso.
CLASES_VIGILADAS = ("r", "v", "m", "S", "p", "f")

_CONSULTA = text(
    """
    SELECT
        rol.rolsuper         AS es_superusuario,
        rol.rolbypassrls     AS omite_rls,
        rol.rolcreaterole    AS puede_crear_roles,
        rol.rolcreatedb      AS puede_crear_bases,
        rol.rolreplication   AS puede_replicar,
        -- Propiedad efectiva sobre relaciones, funciones y los propios schemas.
        EXISTS (
            SELECT 1 FROM pg_class objeto
            JOIN pg_namespace esquema ON esquema.oid = objeto.relnamespace
            WHERE esquema.nspname = ANY(:esquemas)
              AND objeto.relkind = ANY(:clases)
              AND pg_has_role(rol.oid, objeto.relowner, 'USAGE')
            UNION ALL
            SELECT 1 FROM pg_proc funcion
            JOIN pg_namespace esquema ON esquema.oid = funcion.pronamespace
            WHERE esquema.nspname = ANY(:esquemas)
              AND pg_has_role(rol.oid, funcion.proowner, 'USAGE')
            UNION ALL
            SELECT 1 FROM pg_namespace esquema
            WHERE esquema.nspname = ANY(:esquemas)
              AND pg_has_role(rol.oid, esquema.nspowner, 'USAGE')
        )                    AS es_propietario,
        EXISTS (
            SELECT 1 FROM pg_class objeto
            JOIN pg_namespace esquema ON esquema.oid = objeto.relnamespace
            WHERE esquema.nspname = ANY(:esquemas)
              AND objeto.relkind = ANY(:clases)
              AND pg_has_role(rol.oid, objeto.relowner, 'SET')
            UNION ALL
            SELECT 1 FROM pg_proc funcion
            JOIN pg_namespace esquema ON esquema.oid = funcion.pronamespace
            WHERE esquema.nspname = ANY(:esquemas)
              AND pg_has_role(rol.oid, funcion.proowner, 'SET')
            UNION ALL
            SELECT 1 FROM pg_namespace esquema
            WHERE esquema.nspname = ANY(:esquemas)
              AND pg_has_role(rol.oid, esquema.nspowner, 'SET')
        )                    AS puede_asumir_al_propietario,
        EXISTS (
            SELECT 1 FROM pg_roles privilegiado
            WHERE privilegiado.rolname = ANY(:privilegiados)
              AND pg_has_role(rol.oid, privilegiado.oid, 'USAGE')
        )                    AS hereda_un_rol_privilegiado,
        EXISTS (
            SELECT 1 FROM pg_roles privilegiado
            WHERE privilegiado.rolname = ANY(:privilegiados)
              AND pg_has_role(rol.oid, privilegiado.oid, 'SET')
        )                    AS puede_asumir_un_rol_privilegiado,
        EXISTS (
            SELECT 1 FROM pg_roles privilegiado
            WHERE privilegiado.rolname = ANY(:privilegiados)
              AND pg_has_role(rol.oid, privilegiado.oid, 'MEMBER')
        )                    AS tiene_membresia_privilegiada,
        -- El filtro por pg_namespace es lo que hace seguro preguntar por un
        -- schema que todavía no existe: sin él, has_schema_privilege lanzaría.
        EXISTS (
            SELECT 1 FROM pg_namespace esquema
            WHERE esquema.nspname = ANY(:vigilados)
              AND has_schema_privilege(rol.oid, esquema.oid, 'CREATE')
        )                    AS puede_crear_objetos
    FROM pg_roles rol
    WHERE rol.rolname = current_user
    """
)


class RuntimeNoRestringido(RuntimeError):
    """The API is connected as a role that row-level security cannot constrain."""


class PrivilegiosNoVerificables(RuntimeError):
    """The check could not be completed, which is not the same as passing it."""


@dataclass(frozen=True)
class InformeDePrivilegios:
    """What the current connection's role can actually reach."""

    es_superusuario: bool
    omite_rls: bool
    puede_crear_roles: bool
    puede_crear_bases: bool
    puede_replicar: bool
    es_propietario: bool
    puede_asumir_al_propietario: bool
    hereda_un_rol_privilegiado: bool
    puede_asumir_un_rol_privilegiado: bool
    tiene_membresia_privilegiada: bool
    puede_crear_objetos: bool

    @property
    def violaciones(self) -> tuple[str, ...]:
        """Every reason this role must not be the API's, in reading order."""
        motivos = []
        if self.es_superusuario:
            motivos.append("es SUPERUSER, y un superusuario omite toda política RLS")
        if self.omite_rls:
            motivos.append("tiene BYPASSRLS, así que ninguna política se le aplica")
        if self.puede_crear_roles:
            motivos.append(
                "tiene CREATEROLE, con lo que puede fabricarse un rol y "
                "concederse lo que le falte"
            )
        if self.puede_crear_bases:
            motivos.append("tiene CREATEDB, que ninguna credencial de runtime necesita")
        if self.puede_replicar:
            motivos.append(
                "tiene REPLICATION, que permite leer el flujo entero del clúster "
                "al margen de cualquier política"
            )
        if self.es_propietario:
            motivos.append(
                f"posee -- por sí mismo o por herencia -- objetos de "
                f"{SCHEMA_OPERACIONAL} o {SCHEMA_ANALITICO}, y el propietario omite "
                "las políticas de sus tablas salvo FORCE ROW LEVEL SECURITY"
            )
        if self.puede_asumir_al_propietario:
            motivos.append(
                "puede hacer SET ROLE al propietario de las tablas, así que la "
                "restricción sería voluntaria"
            )
        if self.hereda_un_rol_privilegiado:
            motivos.append(
                "hereda un rol técnico privilegiado, y con él lo que ese rol alcanza"
            )
        if self.puede_asumir_un_rol_privilegiado:
            motivos.append(
                "puede hacer SET ROLE a un rol técnico privilegiado, de modo que "
                "alcanza directamente lo que los helpers exponen de forma acotada"
            )
        if self.puede_crear_objetos:
            motivos.append(
                "puede crear objetos en algún schema del proyecto o en public, "
                "y un objeto propio es un objeto sin políticas"
            )
        if self.tiene_membresia_privilegiada:
            motivos.append(
                "tiene una membresía sobre un rol técnico privilegiado; aunque "
                "hoy esté concedida sin INHERIT ni SET, activarlas es un solo "
                "GRANT y ninguna credencial de runtime debería tenerla"
            )
        return tuple(motivos)

    @property
    def restringido(self) -> bool:
        return not self.violaciones


def evaluar_privilegios(conexion: Connection) -> InformeDePrivilegios:
    """The six properties of the role this connection authenticated as.

    A row that comes back missing or incomplete raises rather than defaulting
    any field to ``False``: an unanswered question is not a passed check.
    """
    conexion.execute(
        text(f"SET LOCAL statement_timeout = '{TIEMPO_MAXIMO_DE_COMPROBACION}'")
    )
    filas = (
        conexion.execute(
            _CONSULTA,
            {
                "privilegiados": sorted(ROLES_PRIVILEGIADOS),
                "esquemas": list(ESQUEMAS_DEL_PROYECTO),
                "vigilados": list(ESQUEMAS_VIGILADOS),
                "clases": list(CLASES_VIGILADAS),
            },
        )
        .mappings()
        .all()
    )

    if len(filas) != 1:
        raise PrivilegiosNoVerificables(
            "El catálogo no describió exactamente una fila para la credencial "
            "actual, así que no se pudo determinar si está restringida."
        )

    fila = filas[0]
    campos = {campo: fila[campo] for campo in InformeDePrivilegios.__annotations__}
    if any(valor is None for valor in campos.values()):
        raise PrivilegiosNoVerificables(
            "El catálogo devolvió un resultado incompleto para la credencial "
            "actual, así que no se pudo determinar si está restringida."
        )

    return InformeDePrivilegios(**campos)


def _mensaje(informe: InformeDePrivilegios) -> str:
    return (
        "La credencial de ejecución de la API no está restringida: "
        + "; ".join(informe.violaciones)
        + ". DATABASE_URL debe apuntar al rol de runtime restringido, nunca al "
        "rol que posee los objetos ni al que ejecuta las migraciones."
    )


def _es_desplegado(app_env: str) -> bool:
    return app_env not in AMBIENTES_PERMITIDOS


def exigir_runtime_restringido(conexion: Connection, app_env: str) -> InformeDePrivilegios:
    """Refuse to serve with a credential that row-level security cannot bind.

    Raises :class:`RuntimeNoRestringido` in a deployed environment. In a
    simulated one it reports at ``ERROR`` and returns, while
    :data:`PERMISIVIDAD_TRANSITORIA_SCRUM98` holds.
    """
    informe = evaluar_privilegios(conexion)
    if informe.restringido:
        return informe

    mensaje = _mensaje(informe)
    if _es_desplegado(app_env) or not PERMISIVIDAD_TRANSITORIA_SCRUM98:
        raise RuntimeNoRestringido(mensaje)

    registrador.error(
        "%s Se continúa porque APP_ENV='%s' es un ambiente simulado y la "
        "permisividad transitoria de SCRUM-98 sigue activa; en cualquier "
        "ambiente desplegado la API se negaría a arrancar.",
        mensaje,
        app_env,
    )
    return informe


def verificar_al_arrancar(engine: Engine, app_env: str) -> InformeDePrivilegios | None:
    """The whole check, connection included, as the application start-up runs it.

    **Two timeouts, because there are two waits.** The engine used here is built
    for this call alone, with ``connect_timeout`` in its ``connect_args`` -- a
    libpq parameter that can only be set when the engine is constructed, and
    which bounds establishing the connection. ``statement_timeout``, set inside
    the transaction by :func:`evaluar_privilegios`, bounds the catalogue query
    once it is running. Neither covers the other. The application's own engine
    is left untouched, so its pool keeps the settings the API was configured
    with.

    **Opening the connection is part of the check.** In a deployed environment
    any failure -- the server refusing the connection, a timeout, the catalogue
    query erroring -- stops the process, because none of them is evidence that
    the credential is restricted. A base that is merely down right now would
    otherwise let a process start and serve, for as long as it lives, on a
    credential nobody ever verified.

    In a simulated environment an unreachable database is reported and the
    process continues: there, the same failure means the tests or the developer
    have no server at hand, and refusing to import the application would make
    the offline suite depend on one.

    Nothing that reaches a log here comes from the exception. A driver error
    carries the statement, its parameters and the connection URL; only the class
    name travels, and never ``exc_info``.
    """
    verificador = create_engine(
        engine.url,
        poolclass=NullPool,
        connect_args={"connect_timeout": SEGUNDOS_MAXIMOS_DE_CONEXION},
    )
    try:
        with verificador.connect() as conexion:
            with conexion.begin():
                return exigir_runtime_restringido(conexion, app_env)
    except RuntimeNoRestringido:
        raise
    except Exception as error:  # noqa: BLE001 -- see the docstring
        clase = type(error).__name__
        if _es_desplegado(app_env):
            raise PrivilegiosNoVerificables(
                "No se pudo comprobar los privilegios de la credencial de la API "
                f"al arrancar ({clase}). En un ambiente desplegado eso impide "
                "arrancar: una comprobación que no se completó no es una "
                "comprobación superada."
            ) from None
        registrador.warning(
            "No se pudo comprobar los privilegios de la credencial de la API al "
            "arrancar (%s). Se continúa porque APP_ENV='%s' es un ambiente "
            "simulado; en cualquier ambiente desplegado esto impediría arrancar.",
            clase,
            app_env,
        )
        return None
    finally:
        verificador.dispose()
