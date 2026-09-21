"""enable row level security and clinical context helpers

Revision ID: 3b4a352bc39a
Revises: 54053d46abd6
Create Date: 2026-09-16 22:53:12.768506

Row-level security for the clinical tables, the helpers the policies call, and
the grants that make the restricted roles usable (SCRUM-98).

**What decides who sees what.** One custom setting, ``fetalalert.id_usuario``,
installed by the application with ``set_config(..., true)`` for the length of a
transaction. Everything else -- the role, the patient profile, the physician
profile, the follow-up -- is resolved here, from relations PostgreSQL already
holds. Nothing the client sends reaches a policy.

**Six tables carry policies, and only six.** ``usuario_paciente``,
``usuario_medico``, ``embarazo``, ``asignacion_dispositivo``,
``sesion_monitoreo`` and ``lectura_biometrica``. Everything else is protected by
the absence of a privilege, which is simpler and stronger than a policy: a role
that cannot reach a table at all needs no rule about which of its rows it may
see. ``operacional.paciente``, ``medico``, ``telefono_*``, ``medico_clinica``,
``seguimiento_clinico``, ``clinica`` and the catalogues are granted to nobody at
runtime; ``usuario`` and ``rol`` are readable because the login path runs before
any identity exists and could not work otherwise; ``auditoria_log`` is
insert-only; and ``idempotencia_solicitud`` stays global on purpose -- a policy
per patient there would make the winning claim of a race invisible and break
replay.

**FORCE, and why it matters here.** Without it the table owner ignores its own
policies, and in a deployment where the migrator owns the schema that would mean
the loader and any data migration see everything -- which is intended -- but so
would anything else that connected with that credential. ``FORCE`` makes the
exception explicit instead of implicit: the owner is subject like everybody
else, and the maintenance path gets a policy addressed to
``fetalalert_mantenimiento``, a role the runtime credentials neither hold nor
can assume.

**Nothing here creates a role.** Roles are cluster-global, so the three NOLOGIN
ones come from ``scripts/bootstrap_roles.sql`` and the login ones from
deployment or CI. This revision validates that they exist and are not dangerous,
and aborts with a readable sentence when they are not.

All data in this project is simulated and fictitious.
"""
from typing import Sequence, Union

from dataclasses import dataclass

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3b4a352bc39a'
down_revision: Union[str, None] = '54053d46abd6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Vocabulario
# ---------------------------------------------------------------------------

OPERACIONAL = "operacional"
ANALITICO = "analitico"
SEGURIDAD = "seguridad"
# El mapa de seudonimos. Nadie fuera del ETL y del mantenimiento lo alcanza, y
# Power BI ni siquiera recibe USAGE sobre el schema.
PRIVADO = "privado"
# Lo unico que Power BI puede consultar. Son vistas, no tablas: leen de
# ``analitico`` y de ``privado`` con los privilegios de su propietario, de modo
# que publicar una columna es un acto explicito y no el resultado de un GRANT
# demasiado ancho.
PUBLICACION = "publicacion"

ROL_RLS_OWNER = "fetalalert_rls_owner"
ROL_PROVISION_OWNER = "fetalalert_provision_owner"
ROL_MANTENIMIENTO = "fetalalert_mantenimiento"
ROL_API = "fetalalert_api"
ROL_ETL = "fetalalert_etl"
# La credencial tecnica con la que Power BI consulta la base. **No es la
# identidad de ningun medico**: cada medico entra a Power BI Service con la suya
# y el RLS del dataset filtra por ella. Aqui solo hay una cuenta de solo lectura
# sobre las superficies publicadas.
ROL_POWERBI = "fetalalert_powerbi"

# El rol que ejecuta las consultas de la aplicacion. Uno, y solo uno.
#
# Antes habia dos: este y un ``fetalalert_rls_test`` que existia para que las
# pruebas pudieran conectarse como un rol restringido. Se elimino, y la razon es
# que era la pieza equivocada: un rol de pruebas con politicas propias significa
# que lo que se demuestra es el aislamiento *del rol de pruebas*, no el de la
# API, y obliga a la migracion a exigir en produccion la existencia de un rol
# que solo sirve en CI. Las pruebas de aislamiento se conectan ahora como
# ``fetalalert_api`` sobre un cluster efimero, que es el sujeto real.
ROLES_DE_APLICACION = (ROL_API,)

# Los roles que ninguna credencial de runtime puede alcanzar: son propietarios
# de funciones SECURITY DEFINER o tienen escritura sin filtro sobre lo clinico.
ROLES_PRIVILEGIADOS = (ROL_RLS_OWNER, ROL_PROVISION_OWNER, ROL_MANTENIMIENTO)

# Los roles que se autentican y ejecutan consultas de usuario final.
ROLES_DE_RUNTIME = (ROL_API, ROL_ETL, ROL_POWERBI)

# Membresias exactas que el migrador debe tener, con sus opciones efectivas
# (INHERIT, SET). Replica ``app.db.roles.MEMBRESIAS_DEL_MIGRADOR``: si las dos
# se separan, el preflight lo dice antes de que la migracion conceda nada.
MEMBRESIAS_DEL_MIGRADOR = (
    (ROL_MANTENIMIENTO, True, True),
    (ROL_RLS_OWNER, False, True),
    (ROL_PROVISION_OWNER, False, True),
)

# Atributos que ningun rol de esta revision puede tener. ``rolsuper`` y
# ``rolbypassrls`` volverian decorativas las politicas; los otros tres permiten
# fabricarse una via de escape -- crear un rol, una base o una replica-- sin
# pasar por el migrador.
CAPACIDADES_PROHIBIDAS = (
    ("rolsuper", "SUPERUSER"),
    ("rolbypassrls", "BYPASSRLS"),
    ("rolcreaterole", "CREATEROLE"),
    ("rolcreatedb", "CREATEDB"),
    ("rolreplication", "REPLICATION"),
)

# Tablas con politicas. Seis, y la lista es deliberadamente corta.
TABLAS_CON_RLS = (
    "usuario_paciente",
    "usuario_medico",
    "embarazo",
    "asignacion_dispositivo",
    "sesion_monitoreo",
    "lectura_biometrica",
)

# Universo que el ETL extrae. Solo tres de ellas llevan RLS; el resto le llega
# por privilegio directo.
TABLAS_DEL_ETL = (
    "clinica", "embarazo", "embarazo_factor_riesgo", "especialidad",
    "factor_riesgo", "lectura_biometrica", "medico", "medico_clinica",
    "paciente", "seguimiento_clinico", "semaforo", "sesion_monitoreo",
    "telefono_medico", "telefono_paciente", "tiempo_gestacional",
)
TABLAS_DEL_ETL_CON_RLS = ("embarazo", "sesion_monitoreo", "lectura_biometrica")

# Destino del ETL: las nueve estructuras del modelo estrella. El ETL escribe
# aqui y en ningun otro sitio.
# Las dos tablas del mapa privado. Clave operacional -> seudonimo estable.
TABLAS_DEL_MAPA = ("seudonimo_paciente", "seudonimo_embarazo")

# Las cuatro superficies publicadas, y las unicas que Power BI puede nombrar.
VISTAS_PUBLICADAS = (
    "v_embarazo",
    "v_lectura",
    "v_entitlement_medico",
    "v_resumen_administrativo",
)

TABLAS_ANALITICAS = (
    "dim_clinica", "dim_embarazo", "dim_factor_riesgo", "dim_medico",
    "dim_paciente", "dim_semaforo", "dim_tiempo_gestacional",
    "bridge_embarazo_factor_riesgo", "fact_lectura_biometrica",
)

# Secuencias que la API consume, una por cada INSERT que ejecuta. Ni una mas:
# son ``serial`` y no ``IDENTITY``, asi que el USAGE es obligatorio y explicito.
SECUENCIAS_DE_LA_API = (
    "usuario_id_usuario_seq",
    "auditoria_log_id_log_seq",
    "idempotencia_solicitud_id_idempotencia_seq",
    "sesion_monitoreo_id_sesion_seq",
    "lectura_biometrica_id_lectura_seq",
)

_LISTA = ", ".join


def _sql(sentencia: str) -> None:
    op.execute(sentencia)


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

# Se ejecuta antes de cualquier DDL. Un GRANT a un rol inexistente aborta con un
# mensaje del servidor que no dice que hacer; esto aborta con uno que si.
# Las cinco capacidades, como predicado y como texto, derivadas una sola vez de
# la tabla de arriba: la lista que se comprueba y la que se nombra en el mensaje
# no pueden separarse.
_PREDICADO_PROHIBIDO = " OR ".join(columna for columna, _ in CAPACIDADES_PROHIBIDAS)
_NOMBRES_PROHIBIDOS = ", ".join(etiqueta for _, etiqueta in CAPACIDADES_PROHIBIDAS)

_RUNTIME_SQL = ", ".join(f"'{rol}'" for rol in ROLES_DE_RUNTIME)
_PRIVILEGIADOS_SQL = ", ".join(f"'{rol}'" for rol in ROLES_PRIVILEGIADOS)
_REQUERIDOS_SQL = ", ".join(
    f"'{rol}'" for rol in ROLES_PRIVILEGIADOS + ROLES_DE_RUNTIME
)
_MEMBRESIAS_SQL = ", ".join(
    f"('{rol}', {str(hereda).lower()}, {str(asume).lower()})"
    for rol, hereda, asume in MEMBRESIAS_DEL_MIGRADOR
)

PREFLIGHT = f"""
DO $preflight$
DECLARE
    faltantes text;
    culpables text;
BEGIN
    -- 1. Existencia. Un GRANT a un rol inexistente aborta con un mensaje del
    -- servidor que no dice que hacer; esto aborta con uno que si.
    SELECT string_agg(nombre, ', ' ORDER BY nombre) INTO faltantes
    FROM unnest(ARRAY[{_REQUERIDOS_SQL}]) AS nombre
    WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = nombre);

    IF faltantes IS NOT NULL THEN
        RAISE EXCEPTION
            'Migracion abortada: faltan los roles %. Los NOLOGIN los crea '
            '"python scripts/bootstrap_roles.py aplicar" y los de conexion el '
            'despliegue o el CI desde secretos externos. Esta revision concede '
            'privilegios: no puede crear los roles a los que se los concede.',
            faltantes;
    END IF;

    -- 2. Capacidades. Ni los de runtime ni los tecnicos pueden tener ninguna de
    -- las cinco: dos anulan las politicas y tres abren una via de escape.
    SELECT string_agg(rolname, ', ' ORDER BY rolname) INTO culpables
    FROM pg_roles
    WHERE rolname = ANY(ARRAY[{_REQUERIDOS_SQL}])
      AND ({_PREDICADO_PROHIBIDO});

    IF culpables IS NOT NULL THEN
        RAISE EXCEPTION
            'Migracion abortada: los roles % tienen alguna capacidad prohibida '
            '({_NOMBRES_PROHIBIDOS}). SUPERUSER y BYPASSRLS harian decorativas '
            'las politicas de esta revision; CREATEROLE, CREATEDB y REPLICATION '
            'permiten fabricarse una via de escape sin pasar por el migrador.',
            culpables;
    END IF;

    -- 3. Los tecnicos, ademas, NOLOGIN: son duenos de funciones SECURITY
    -- DEFINER y de la escritura de mantenimiento.
    SELECT string_agg(rolname, ', ' ORDER BY rolname) INTO culpables
    FROM pg_roles
    WHERE rolname = ANY(ARRAY[{_PRIVILEGIADOS_SQL}]) AND rolcanlogin;

    IF culpables IS NOT NULL THEN
        RAISE EXCEPTION
            'Migracion abortada: los roles tecnicos % pueden autenticarse. Son '
            'propietarios de funciones SECURITY DEFINER: nadie debe poder '
            'conectarse como ellos.',
            culpables;
    END IF;

    -- 4. Propiedad. Un rol de runtime propietario de un objeto puede alterarlo
    -- o borrarlo, y sobre una tabla propia las politicas solo se le aplican con
    -- FORCE. Se miran las cinco clases de objeto de esta base, y la base misma.
    SELECT string_agg(DISTINCT descripcion, ', ' ORDER BY descripcion)
      INTO culpables
    FROM (
        SELECT r.rolname || ' posee la base ' || d.datname AS descripcion
        FROM pg_database d JOIN pg_roles r ON r.oid = d.datdba
        WHERE r.rolname = ANY(ARRAY[{_RUNTIME_SQL}])
          AND d.datname = current_database()
        UNION ALL
        SELECT r.rolname || ' posee el schema ' || n.nspname
        FROM pg_namespace n JOIN pg_roles r ON r.oid = n.nspowner
        WHERE r.rolname = ANY(ARRAY[{_RUNTIME_SQL}])
        UNION ALL
        SELECT r.rolname || ' posee la relacion ' || c.relname
        FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner
        WHERE r.rolname = ANY(ARRAY[{_RUNTIME_SQL}])
        UNION ALL
        SELECT r.rolname || ' posee la funcion ' || p.proname
        FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner
        WHERE r.rolname = ANY(ARRAY[{_RUNTIME_SQL}])
        UNION ALL
        SELECT r.rolname || ' posee el tipo ' || t.typname
        FROM pg_type t JOIN pg_roles r ON r.oid = t.typowner
        WHERE r.rolname = ANY(ARRAY[{_RUNTIME_SQL}])
    ) AS propiedad;

    IF culpables IS NOT NULL THEN
        RAISE EXCEPTION
            'Migracion abortada: %. La propiedad de los objetos pertenece al '
            'migrador, no a las credenciales que atienden peticiones.',
            culpables;
    END IF;

    -- 5. CREATE sobre cualquier schema. Con eso un rol de runtime se fabrica su
    -- propia tabla, o una funcion que sombrease a un helper si algo la buscara
    -- sin calificar.
    SELECT string_agg(rol || ' sobre ' || nspname, ', ' ORDER BY rol, nspname)
      INTO culpables
    FROM pg_namespace, unnest(ARRAY[{_RUNTIME_SQL}]) AS rol
    WHERE nspname NOT LIKE 'pg\\_%'
      AND nspname <> 'information_schema'
      AND has_schema_privilege(rol, nspname, 'CREATE');

    IF culpables IS NOT NULL THEN
        RAISE EXCEPTION
            'Migracion abortada: hay CREATE sobre un schema para %. Ningun rol '
            'de runtime recibe CREATE en esta revision, y tenerlo de antes le '
            'permite crear objetos propios que ninguna politica cubre.',
            culpables;
    END IF;

    -- 6. Membresia, herencia y SET ROLE hacia los roles privilegiados. Se usa
    -- pg_has_role, que recorre cadenas indirectas: comparar miembros directos no
    -- veria "api miembro de X, X miembro de mantenimiento". Las tres vias se
    -- comprueban por separado porque no son intercambiables: MEMBER dice que la
    -- concesion existe, USAGE que hereda los privilegios y SET que puede asumir
    -- el rol.
    SELECT string_agg(
               candidato || ' -> ' || objetivo || ' (' || via || ')',
               ', ' ORDER BY candidato, objetivo, via)
      INTO culpables
    FROM unnest(ARRAY[{_RUNTIME_SQL}]) AS candidato,
         unnest(ARRAY[{_PRIVILEGIADOS_SQL}]) AS objetivo,
         unnest(ARRAY['MEMBER', 'USAGE', 'SET']) AS via
    WHERE pg_has_role(candidato, objetivo, via);

    IF culpables IS NOT NULL THEN
        RAISE EXCEPTION
            'Migracion abortada: hay alcance de un rol de runtime sobre un rol '
            'privilegiado: %. Ninguna de las tres vias puede llegar a una '
            'credencial que atienda peticiones.',
            culpables;
    END IF;

    -- 7. Y el runtime tampoco puede alcanzar al migrador, que conserva ADMIN
    -- efectivo sobre los roles que creo por la membresia implicita de
    -- CREATEROLE. Esa capacidad es exclusivamente suya.
    SELECT string_agg(candidato || ' (' || via || ')', ', ' ORDER BY candidato, via)
      INTO culpables
    FROM unnest(ARRAY[{_RUNTIME_SQL}]) AS candidato,
         unnest(ARRAY['MEMBER', 'USAGE', 'SET']) AS via
    WHERE pg_has_role(candidato, current_user, via);

    IF culpables IS NOT NULL THEN
        RAISE EXCEPTION
            'Migracion abortada: % alcanza al migrador %. El migrador conserva '
            'ADMIN efectivo sobre los roles que creo, y esa capacidad no puede '
            'llegar ni directa ni indirectamente a la API ni al ETL.',
            culpables, current_user;
    END IF;

    -- 8. Las membresias del migrador, exactas. Se agregan con bool_or porque
    -- PostgreSQL 16 anade una fila aparte para la concesion implicita que un
    -- CREATEROLE recibe al crear un rol: lo que importa es la capacidad
    -- efectiva, no en que fila viaja.
    SELECT string_agg(
               esperado.rol
               || ' (hereda=' || coalesce(efectivo.hereda::text, 'sin concesion')
               || ' asume=' || coalesce(efectivo.asume::text, 'sin concesion') || ')',
               ', ' ORDER BY esperado.rol)
      INTO culpables
    FROM (VALUES {_MEMBRESIAS_SQL}) AS esperado(rol, hereda, asume)
    LEFT JOIN (
        SELECT concedido.rolname AS rol,
               bool_or(v.inherit_option) AS hereda,
               bool_or(v.set_option) AS asume
        FROM pg_auth_members v
        JOIN pg_roles concedido ON concedido.oid = v.roleid
        JOIN pg_roles receptor ON receptor.oid = v.member
        WHERE receptor.rolname = current_user
        GROUP BY concedido.rolname
    ) AS efectivo ON efectivo.rol = esperado.rol
    WHERE efectivo.rol IS NULL
       OR efectivo.hereda IS DISTINCT FROM esperado.hereda
       OR efectivo.asume IS DISTINCT FROM esperado.asume;

    IF culpables IS NOT NULL THEN
        RAISE EXCEPTION
            'Migracion abortada: el migrador % no tiene las membresias exactas '
            'que esta revision necesita: %. Ejecuta '
            '"python scripts/bootstrap_roles.py aplicar" con esta misma '
            'identidad antes de migrar.',
            current_user, culpables;
    END IF;

    -- 9. Y nadie mas que el migrador puede ser miembro de un rol tecnico.
    --
    -- El punto 8 comprueba que el migrador tenga lo que necesita; este cierra
    -- el otro lado: que no haya *nadie mas*. No es una lista negra de nombres
    -- conocidos, porque un rol desconocido con una concesion sobre el dueno de
    -- un SECURITY DEFINER es igual de peligroso y mucho mas facil de no ver.
    --
    -- ADMIN OPTION cuenta como intrusion por si solo, aunque INHERIT y SET
    -- esten apagados: ADMIN es el derecho a conceder el rol mas alla, de modo
    -- que quien lo tiene puede encenderse las otras dos con un solo GRANT. El
    -- migrador lo tiene porque creo los roles -- es la capacidad que le permite
    -- reejecutar el bootstrap -- y es el unico que puede tenerlo.
    SELECT string_agg(descripcion, ', ' ORDER BY descripcion) INTO culpables
    FROM (
        SELECT receptor.rolname || ' es miembro inesperado de '
               || concedido.rolname
               || CASE WHEN bool_or(v.admin_option) THEN ' (con ADMIN OPTION)'
                       ELSE '' END AS descripcion
        FROM pg_auth_members v
        JOIN pg_roles concedido ON concedido.oid = v.roleid
        JOIN pg_roles receptor ON receptor.oid = v.member
        WHERE concedido.rolname = ANY(ARRAY[{_PRIVILEGIADOS_SQL}])
          AND receptor.rolname <> current_user
        GROUP BY receptor.rolname, concedido.rolname
    ) AS intrusos;

    IF culpables IS NOT NULL THEN
        RAISE EXCEPTION
            'Migracion abortada: %. Solo el migrador puede ser miembro de los '
            'roles tecnicos. Una concesion de mas, incluso con INHERIT y SET '
            'apagados, es un GRANT de distancia de poder asumir al propietario '
            'de las funciones SECURITY DEFINER.',
            culpables;
    END IF;

    -- 10. Y un rol tecnico no puede ser miembro de nada.
    --
    -- Serlo le daria, por herencia, lo que ese otro rol alcance, y los helpers
    -- se ejecutan con su identidad: un dueno que heredara privilegios ajenos
    -- ampliaria en silencio lo que cada SECURITY DEFINER puede leer.
    SELECT string_agg(
               receptor.rolname || ' es miembro de ' || concedido.rolname,
               ', ' ORDER BY receptor.rolname || concedido.rolname)
      INTO culpables
    FROM pg_auth_members v
    JOIN pg_roles concedido ON concedido.oid = v.roleid
    JOIN pg_roles receptor ON receptor.oid = v.member
    WHERE receptor.rolname = ANY(ARRAY[{_PRIVILEGIADOS_SQL}]);

    IF culpables IS NOT NULL THEN
        RAISE EXCEPTION
            'Migracion abortada: %. Los roles tecnicos no son miembros de nada: '
            'son propietarios de funciones SECURITY DEFINER, y heredar '
            'privilegios ajenos ampliaria lo que esas funciones alcanzan.',
            culpables;
    END IF;
END
$preflight$;
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# ``pg_temp`` va explicito y al final. Si se omite PostgreSQL lo antepone, y un
# llamante podria crear una tabla o funcion temporal que sombrease un objeto sin
# calificar. Aun asi todo va calificado con su schema.
SEARCH_PATH = "SET search_path = pg_catalog, operacional, pg_temp"

# Cota de nueve digitos: el identificador cabe en ``integer`` y un valor mas
# largo se descarta antes de convertirlo, en vez de reventar el cast.
HELPERS = f"""
CREATE FUNCTION {SEGURIDAD}.usuario_actual_id() RETURNS integer
LANGUAGE plpgsql STABLE
{SEARCH_PATH}
AS $funcion$
DECLARE
    crudo text;
BEGIN
    -- El segundo argumento es *missing_ok*: una conexion sin contexto es una
    -- situacion normal -- el login, la auditoria -- y no un error.
    crudo := current_setting('fetalalert.id_usuario', true);
    IF crudo IS NULL OR crudo !~ '^[0-9]{{1,9}}$' THEN
        RETURN NULL;
    END IF;
    RETURN crudo::integer;
END
$funcion$;

COMMENT ON FUNCTION {SEGURIDAD}.usuario_actual_id() IS
'Identidad instalada en la transaccion, o NULL. Ausente, vacia, no numerica o '
'demasiado larga devuelven NULL: sin identidad no hay filas.';

CREATE FUNCTION {SEGURIDAD}.es_admin() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
{SEARCH_PATH}
AS $funcion$
    SELECT EXISTS (
        SELECT 1
        FROM operacional.usuario u
        JOIN operacional.rol r ON r.id_rol = u.id_rol
        WHERE u.id_usuario = {SEGURIDAD}.usuario_actual_id()
          AND u.activo
          AND r.nombre_rol = 'ADMIN'
    )
$funcion$;

CREATE FUNCTION {SEGURIDAD}.paciente_actual() RETURNS integer
LANGUAGE sql STABLE SECURITY DEFINER
{SEARCH_PATH}
AS $funcion$
    SELECT up.id_paciente
    FROM operacional.usuario_paciente up
    JOIN operacional.usuario u ON u.id_usuario = up.id_usuario
    JOIN operacional.rol r ON r.id_rol = u.id_rol
    WHERE up.id_usuario = {SEGURIDAD}.usuario_actual_id()
      AND u.activo
      AND r.nombre_rol = 'PACIENTE'
$funcion$;

CREATE FUNCTION {SEGURIDAD}.medico_actual() RETURNS integer
LANGUAGE sql STABLE SECURITY DEFINER
{SEARCH_PATH}
AS $funcion$
    SELECT um.id_medico
    FROM operacional.usuario_medico um
    JOIN operacional.usuario u ON u.id_usuario = um.id_usuario
    JOIN operacional.rol r ON r.id_rol = u.id_rol
    WHERE um.id_usuario = {SEGURIDAD}.usuario_actual_id()
      AND u.activo
      AND r.nombre_rol = 'MEDICO'
$funcion$;

CREATE FUNCTION {SEGURIDAD}.embarazo_en_seguimiento_vigente(p_id_embarazo integer)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
{SEARCH_PATH}
AS $funcion$
    SELECT EXISTS (
        SELECT 1
        FROM operacional.seguimiento_clinico sc
        WHERE sc.id_embarazo = p_id_embarazo
          AND sc.id_medico = {SEGURIDAD}.medico_actual()
          AND sc.activo
          AND sc.fecha_asignacion <= CURRENT_DATE
          AND (sc.fecha_fin IS NULL OR sc.fecha_fin >= CURRENT_DATE)
    )
$funcion$;

COMMENT ON FUNCTION {SEGURIDAD}.embarazo_en_seguimiento_vigente(integer) IS
'Asignacion directa, activa y vigente hoy. La afiliacion a una clinica no '
'concede acceso: medico_clinica no aparece aqui a proposito.';

CREATE FUNCTION {SEGURIDAD}.estado_del_perfil_paciente(p_id_paciente integer)
RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
{SEARCH_PATH}
AS $funcion$
BEGIN
    IF NOT {SEGURIDAD}.es_admin() THEN
        RETURN 'sin_autorizacion';
    END IF;
    PERFORM 1 FROM operacional.paciente
     WHERE id_paciente = p_id_paciente
       FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RETURN 'inexistente';
    END IF;
    PERFORM 1 FROM operacional.usuario_paciente
     WHERE id_paciente = p_id_paciente;
    IF FOUND THEN
        RETURN 'vinculado';
    END IF;
    RETURN 'disponible';
END
$funcion$;

COMMENT ON FUNCTION {SEGURIDAD}.estado_del_perfil_paciente(integer) IS
'Lo unico que la provision de SCRUM-97 necesita saber de un perfil, y nada mas: '
'inexistente, disponible, vinculado o sin_autorizacion. Bloquea la fila con FOR '
'NO KEY UPDATE hasta el commit del router, pero no devuelve ni una columna del '
'perfil ni del vinculo, asi que no hay PII que filtrar y ADMIN no puede '
'enumerar nada: pregunta por un identificador que ya trae y recibe una palabra.';

CREATE FUNCTION {SEGURIDAD}.estado_del_perfil_medico(p_id_medico integer)
RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
{SEARCH_PATH}
AS $funcion$
BEGIN
    IF NOT {SEGURIDAD}.es_admin() THEN
        RETURN 'sin_autorizacion';
    END IF;
    PERFORM 1 FROM operacional.medico
     WHERE id_medico = p_id_medico
       FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RETURN 'inexistente';
    END IF;
    PERFORM 1 FROM operacional.usuario_medico
     WHERE id_medico = p_id_medico;
    IF FOUND THEN
        RETURN 'vinculado';
    END IF;
    RETURN 'disponible';
END
$funcion$;

COMMENT ON FUNCTION {SEGURIDAD}.estado_del_perfil_medico(integer) IS
'El equivalente para un perfil medico. Mismas cuatro respuestas y el mismo '
'candado; tampoco devuelve columna alguna.';

CREATE FUNCTION {SEGURIDAD}.vinculos_de_paciente(p_id_usuario integer)
RETURNS integer
LANGUAGE sql STABLE SECURITY DEFINER
{SEARCH_PATH}
AS $funcion$
    SELECT count(*)::integer
    FROM operacional.usuario_paciente
    WHERE id_usuario = p_id_usuario
$funcion$;

COMMENT ON FUNCTION {SEGURIDAD}.vinculos_de_paciente(integer) IS
'Cuantos vinculos de gestante tiene una cuenta: 0 o 1. Existe para el trigger '
'de coherencia de SCRUM-97, que necesita el estado real y no el filtrado. No '
'enumera: hay que traer el id_usuario por el que se pregunta, y la respuesta es '
'un numero, no una fila.';

CREATE FUNCTION {SEGURIDAD}.vinculos_de_medico(p_id_usuario integer)
RETURNS integer
LANGUAGE sql STABLE SECURITY DEFINER
{SEARCH_PATH}
AS $funcion$
    SELECT count(*)::integer
    FROM operacional.usuario_medico
    WHERE id_usuario = p_id_usuario
$funcion$;

COMMENT ON FUNCTION {SEGURIDAD}.vinculos_de_medico(integer) IS
'El equivalente para los vinculos medicos.';
"""

FIRMAS = (
    "usuario_actual_id()",
    "es_admin()",
    "paciente_actual()",
    "medico_actual()",
    "embarazo_en_seguimiento_vigente(integer)",
    "vinculos_de_paciente(integer)",
    "vinculos_de_medico(integer)",
    "estado_del_perfil_paciente(integer)",
    "estado_del_perfil_medico(integer)",
)
# Los de contexto pertenecen al dueno de las politicas; los dos ultimos, al de
# la provision. Los contadores van con los primeros: leen los puentes enteros,
# que es exactamente lo que ``pol_helpers`` le permite a ese rol.
FIRMAS_DE_CONTEXTO = FIRMAS[:7]
FIRMAS_DE_PROVISION = FIRMAS[7:]
# Los dos contadores del trigger de coherencia, nombrados aparte porque tienen
# un invocador mas: el rol de mantenimiento.
FIRMAS_DE_VINCULOS = FIRMAS[5:7]


# ---------------------------------------------------------------------------
# El trigger de coherencia de SCRUM-97, bajo RLS
# ---------------------------------------------------------------------------
#
# ``operacional.validar_rol_vinculo_usuario`` comprueba que cada cuenta tenga
# exactamente el vinculo clinico que su rol exige. Corre como SECURITY INVOKER,
# es decir con la identidad de quien escribe, y cuenta filas de los dos puentes.
#
# Desde que ``pol_propia`` dejo de tener rama de ADMIN, esa cuenta se rompe: una
# administradora que aprovisiona una cuenta PACIENTE inserta el vinculo y, al
# validarlo, no lo ve -- la politica solo le muestra el suyo, y no tiene --, asi
# que el trigger cuenta cero y rechaza la operacion entera. El aislamiento
# estaria impidiendo una comprobacion de integridad, que no es lo que ninguna de
# las dos cosas quiere.
#
# La salida no es ensanchar la politica: es que el trigger pregunte por lo que
# necesita a traves de dos funciones SECURITY DEFINER que devuelven un numero
# -- cuantos vinculos tiene *una* cuenta -- y ninguna fila. El trigger sigue
# siendo del migrador y sigue siendo INVOKER; lo unico que cambia son esas dos
# consultas. Nada mas de su cuerpo se toca, y el downgrade lo restituye.
TRIGGER_DE_COHERENCIA = f"{OPERACIONAL}.validar_rol_vinculo_usuario"

_CUERPO_DEL_TRIGGER = """
CREATE OR REPLACE FUNCTION {funcion}() RETURNS trigger
LANGUAGE plpgsql
AS $trigger$
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
          FROM operacional.usuario u
          JOIN operacional.rol r ON r.id_rol = u.id_rol
         WHERE u.id_usuario = v_id
           FOR NO KEY UPDATE OF u;

        -- The account is gone: its links went with it, nothing left to check.
        CONTINUE WHEN NOT FOUND;

        {conteo_pacientes}
        {conteo_medicos}

        IF NOT ((v_rol = 'PACIENTE' AND v_pacientes = 1 AND v_medicos = 0) OR (v_rol = 'MEDICO' AND v_pacientes = 0 AND v_medicos = 1) OR (v_rol = 'ADMIN' AND v_pacientes = 0 AND v_medicos = 0)) THEN
            RAISE EXCEPTION USING
                ERRCODE = 'check_violation',
                CONSTRAINT = 'rol_vinculo_coherente',
                MESSAGE = 'La cuenta no tiene exactamente el vinculo clinico que exige su rol.';
        END IF;
    END LOOP;

    RETURN NULL;
END
$trigger$;
"""

# Las dos unicas lineas que esta revision cambia del cuerpo, y las que restituye.
_CONTEO_CON_HELPERS = (
    f"v_pacientes := {SEGURIDAD}.vinculos_de_paciente(v_id);",
    f"v_medicos := {SEGURIDAD}.vinculos_de_medico(v_id);",
)
_CONTEO_DIRECTO = (
    "SELECT count(*) INTO v_pacientes\n"
    "          FROM operacional.usuario_paciente\n"
    "         WHERE id_usuario = v_id;",
    "SELECT count(*) INTO v_medicos\n"
    "          FROM operacional.usuario_medico\n"
    "         WHERE id_usuario = v_id;",
)


def _trigger(conteos: tuple[str, str]) -> str:
    return _CUERPO_DEL_TRIGGER.format(
        funcion=TRIGGER_DE_COHERENCIA,
        conteo_pacientes=conteos[0],
        conteo_medicos=conteos[1],
    )


def _schema_de_helpers() -> list[str]:
    """Lo que hay que conceder **antes** de transferir la propiedad.

    PostgreSQL exige dos cosas para ``ALTER FUNCTION ... OWNER TO``: poder hacer
    ``SET ROLE`` al nuevo propietario -- que el bootstrap ya concedio -- y que
    ese propietario tenga ``CREATE`` sobre el schema de la funcion. Sin lo
    segundo responde «permission denied for schema seguridad», asi que estas
    sentencias van primero y no con el resto de los grants.

    Es una excepcion de un solo schema: ni ``operacional`` ni ``analitico`` dan
    CREATE a nadie, y ningun rol de runtime lo recibe en ninguno.
    """
    duenos = f"{ROL_RLS_OWNER}, {ROL_PROVISION_OWNER}"
    return [
        f"REVOKE ALL ON SCHEMA {SEGURIDAD} FROM PUBLIC",
        f"GRANT USAGE, CREATE ON SCHEMA {SEGURIDAD} TO {duenos}",
        f"GRANT USAGE ON SCHEMA {SEGURIDAD} TO {_LISTA(ROLES_DE_APLICACION)}",
    ]


def _permisos_de_helpers() -> list[str]:
    """Quien puede ejecutar cada helper. **Antes** de transferir la propiedad.

    El orden no es estetico. ``REVOKE`` solo retira lo que el rol que lo ejecuta
    concedio, y ``GRANT`` exige ser propietario o tener grant option. Ejecutados
    despues del ``ALTER OWNER``, el migrador ya no es ninguna de las dos cosas y
    PostgreSQL responde con un *warning* en vez de un error: las sentencias
    corren, la migracion termina bien y ``PUBLIC`` conserva EXECUTE sobre siete
    funciones SECURITY DEFINER. Se comprobo reproduciendolo.

    Asi que se conceden mientras el migrador sigue siendo el propietario, y el
    ``ALTER OWNER`` posterior conserva la ACL reescribiendo el otorgante.
    """
    sentencias = []
    for firma in FIRMAS:
        sentencias.append(
            f"REVOKE ALL ON FUNCTION {SEGURIDAD}.{firma} FROM PUBLIC"
        )
        sentencias.append(
            f"GRANT EXECUTE ON FUNCTION {SEGURIDAD}.{firma} "
            f"TO {_LISTA(ROLES_DE_APLICACION)}"
        )
    # El dueno de los helpers de provision invoca es_admin(), que pertenece al
    # otro rol tecnico: sin este EXECUTE la provision fallaria en su primera
    # linea. Es la unica concesion cruzada entre los dos duenos.
    sentencias.append(
        f"GRANT EXECUTE ON FUNCTION {SEGURIDAD}.es_admin() TO {ROL_PROVISION_OWNER}"
    )
    # Y el mantenimiento necesita los dos contadores, porque el trigger de
    # coherencia corre con la identidad de quien escribe: el cargador del
    # dataset escribe los puentes, asi que es el quien los invoca. Solo esos
    # dos, y solo porque una comprobacion de integridad depende de ellos.
    for firma in FIRMAS_DE_VINCULOS:
        sentencias.append(
            f"GRANT EXECUTE ON FUNCTION {SEGURIDAD}.{firma} TO {ROL_MANTENIMIENTO}"
        )
    return sentencias


def _propiedad_de_helpers() -> list[str]:
    """Cada helper a su rol NOLOGIN. Va **despues** de conceder los permisos."""
    sentencias = []
    for firma in FIRMAS_DE_CONTEXTO:
        sentencias.append(
            f"ALTER FUNCTION {SEGURIDAD}.{firma} OWNER TO {ROL_RLS_OWNER}"
        )
    for firma in FIRMAS_DE_PROVISION:
        sentencias.append(
            f"ALTER FUNCTION {SEGURIDAD}.{firma} OWNER TO {ROL_PROVISION_OWNER}"
        )
    return sentencias


# ---------------------------------------------------------------------------
# Grants
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Concesion:
    """Un privilegio, su objeto y quien lo recibe. Se lee en los dos sentidos.

    El ``downgrade`` de esta revision tiene que retirar **exactamente** lo que
    el ``upgrade`` concedio, ni una cosa mas. La version anterior usaba
    ``REVOKE ALL ON ALL TABLES IN SCHEMA ...``, que es una escoba: retiraba
    tambien lo que hubiera concedido el despliegue, otra revision o una
    operadora, y dejaba la base en un estado que no era el anterior a esta
    migracion sino uno mas pobre, sin decirlo.

    Declarar cada concesion una sola vez y generar el ``GRANT`` y el ``REVOKE``
    de la misma fila hace la simetria estructural: no se puede anadir un
    privilegio y olvidar retirarlo, porque no hay dos listas que mantener.
    """

    privilegios: str
    objeto: str
    roles: tuple[str, ...]

    def conceder(self) -> str:
        return f"GRANT {self.privilegios} ON {self.objeto} TO {_LISTA(self.roles)}"

    def revocar(self) -> str:
        return f"REVOKE {self.privilegios} ON {self.objeto} FROM {_LISTA(self.roles)}"


def _tabla(nombre: str) -> str:
    return f"TABLE {OPERACIONAL}.{nombre}"


def _concesiones() -> tuple[Concesion, ...]:
    """Privilegios minimos por rol. Lo que no aparece aqui, no se concede.

    No incluye nada del schema ``seguridad``: sus concesiones se van con el
    ``DROP SCHEMA`` del downgrade, asi que retirarlas antes seria retirar algo
    que esta a punto de dejar de existir -- y hacerlo despues fallaria.
    """
    o = OPERACIONAL
    api = ROLES_DE_APLICACION
    concesiones: list[Concesion] = [
        # Schemas. Solo USAGE: ningun rol de runtime recibe CREATE en ninguno.
        Concesion("USAGE", f"SCHEMA {o}", api + (ROL_ETL, ROL_MANTENIMIENTO)),
        Concesion("USAGE", f"SCHEMA {o}", (ROL_RLS_OWNER, ROL_PROVISION_OWNER)),

        # --- API ---------------------------------------------------------
        # ``usuario`` con todas sus columnas: el login necesita password_hash y
        # no hay forma de autenticar sin leerlo. Riesgo residual documentado.
        Concesion("SELECT, INSERT", _tabla("usuario"), api),
        Concesion("UPDATE (activo)", _tabla("usuario"), api),
        Concesion("SELECT", _tabla("rol"), api),
        Concesion("SELECT, INSERT", _tabla("usuario_paciente"), api),
        Concesion("SELECT, INSERT", _tabla("usuario_medico"), api),
        Concesion("SELECT", _tabla("embarazo"), api),
        Concesion("SELECT, INSERT", _tabla("sesion_monitoreo"), api),
        Concesion("SELECT, INSERT", _tabla("lectura_biometrica"), api),
        Concesion("SELECT", _tabla("asignacion_dispositivo"), api),
        Concesion("SELECT", _tabla("dispositivo"), api),
        Concesion("SELECT", _tabla("tiempo_gestacional"), api),
        Concesion("SELECT", _tabla("semaforo"), api),
        # Append-only: sin SELECT, sin UPDATE y sin DELETE. Leer la traza exige
        # la credencial administrativa, no la del proceso web.
        Concesion("INSERT", _tabla("auditoria_log"), api),
        Concesion("SELECT, INSERT", _tabla("idempotencia_solicitud"), api),
        Concesion(
            "UPDATE (id_sesion, ids_lectura)", _tabla("idempotencia_solicitud"), api
        ),

        # --- Duenos de helpers -------------------------------------------
        Concesion("SELECT", _tabla("usuario"), (ROL_RLS_OWNER, ROL_PROVISION_OWNER)),
        Concesion("SELECT", _tabla("rol"), (ROL_RLS_OWNER, ROL_PROVISION_OWNER)),
        Concesion("SELECT", _tabla("usuario_paciente"), (ROL_RLS_OWNER,)),
        Concesion("SELECT", _tabla("usuario_medico"), (ROL_RLS_OWNER,)),
        Concesion("SELECT", _tabla("seguimiento_clinico"), (ROL_RLS_OWNER,)),
        # El dueno de la provision lee los puentes para responder «vinculado»,
        # que es lo unico que devuelve de ellos: un booleano convertido en
        # palabra, nunca una fila.
        Concesion("SELECT", _tabla("usuario_paciente"), (ROL_PROVISION_OWNER,)),
        Concesion("SELECT", _tabla("usuario_medico"), (ROL_PROVISION_OWNER,)),
        # SELECT ... FOR NO KEY UPDATE exige UPDATE sobre alguna columna, y se
        # concede la clave primaria y nada mas. El rol es NOLOGIN y nadie puede
        # asumirlo, asi que la capacidad no llega a ninguna credencial.
        Concesion("SELECT", _tabla("paciente"), (ROL_PROVISION_OWNER,)),
        Concesion("SELECT", _tabla("medico"), (ROL_PROVISION_OWNER,)),
        Concesion("UPDATE (id_paciente)", _tabla("paciente"), (ROL_PROVISION_OWNER,)),
        Concesion("UPDATE (id_medico)", _tabla("medico"), (ROL_PROVISION_OWNER,)),
    ]

    # --- ETL: lectura completa del universo operacional -------------------
    concesiones += [
        Concesion("SELECT", _tabla(t), (ROL_ETL,)) for t in TABLAS_DEL_ETL
    ]
    # El ETL sella cada carga con la revision desplegada, asi que necesita leer
    # la tabla de versiones de Alembic. Solo SELECT, y sobre esa unica tabla.
    concesiones.append(
        Concesion("SELECT", "TABLE public.alembic_version", (ROL_ETL,))
    )
    # Y escribe el modelo estrella: es su destino y el de nadie mas. La API no
    # recibe USAGE sobre este schema, asi que no puede ni nombrarlo.
    concesiones.append(Concesion("USAGE", f"SCHEMA {ANALITICO}", (ROL_ETL,)))
    concesiones += [
        Concesion(
            "SELECT, INSERT, UPDATE, DELETE", f"TABLE {ANALITICO}.{t}", (ROL_ETL,)
        )
        for t in TABLAS_ANALITICAS
    ]

    # --- Mantenimiento: el cargador del dataset y las migraciones de datos --
    concesiones += [
        Concesion(
            "SELECT, INSERT, UPDATE, DELETE", _tabla(t), (ROL_MANTENIMIENTO,)
        )
        for t in TABLAS_CON_RLS
    ]

    concesiones += [
        Concesion("USAGE", f"SEQUENCE {o}.{secuencia}", ROLES_DE_APLICACION)
        for secuencia in SECUENCIAS_DE_LA_API
    ]

    # --- Mapa privado: el ETL lo escribe, el mantenimiento lo conserva -----
    #
    # El ETL inserta un seudonimo la primera vez que ve a una paciente o un
    # embarazo y lo reutiliza siempre despues; por eso necesita SELECT ademas de
    # INSERT. No recibe UPDATE ni DELETE: cambiar un seudonimo ya emitido
    # rompería la serie longitudinal de todo lo publicado, y no hay ninguna
    # operación del ETL que deba poder hacerlo.
    #
    # El mantenimiento si los tiene, porque es la identidad de un restore: un
    # backup que no devuelva el mapa deja la publicación sin longitudinalidad.
    concesiones.append(Concesion("USAGE", f"SCHEMA {PRIVADO}", (ROL_ETL,)))
    # El dueno de ``v_entitlement_medico`` lee el seudonimo del embarazo, y solo
    # ese: no recibe ``seudonimo_paciente``, que es el que ataria un seudonimo a
    # una persona.
    concesiones.append(Concesion("USAGE", f"SCHEMA {PRIVADO}", (ROL_RLS_OWNER,)))
    concesiones.append(
        Concesion(
            "SELECT", f"TABLE {PRIVADO}.seudonimo_embarazo", (ROL_RLS_OWNER,)
        )
    )
    concesiones.append(
        Concesion("USAGE", f"SCHEMA {PRIVADO}", (ROL_MANTENIMIENTO,))
    )
    concesiones += [
        Concesion("SELECT, INSERT", f"TABLE {PRIVADO}.{tabla}", (ROL_ETL,))
        for tabla in TABLAS_DEL_MAPA
    ]
    concesiones += [
        Concesion(
            "SELECT, INSERT, UPDATE, DELETE",
            f"TABLE {PRIVADO}.{tabla}",
            (ROL_MANTENIMIENTO,),
        )
        for tabla in TABLAS_DEL_MAPA
    ]

    # Lo de ``publicacion`` no esta aqui: va en ``_permisos_de_publicacion()``,
    # que corre **antes** de transferir la propiedad de las vistas. Ver alli.

    return tuple(concesiones)


# Cerrar lo que PostgreSQL deja abierto por omision. No se revierte en el
# downgrade a proposito: sobre un schema que no es ``public``, PUBLIC no tiene
# ningun privilegio por omision, de modo que estas sentencias no retiran nada
# que existiera antes y volver a concederlas abriria lo que esta revision cierra.
ENDURECIMIENTO = (
    f"REVOKE ALL ON SCHEMA {OPERACIONAL} FROM PUBLIC",
    f"REVOKE ALL ON SCHEMA {SEGURIDAD} FROM PUBLIC",
    f"REVOKE ALL ON SCHEMA {ANALITICO} FROM PUBLIC",
    f"REVOKE ALL ON SCHEMA {PRIVADO} FROM PUBLIC",
    f"REVOKE ALL ON SCHEMA {PUBLICACION} FROM PUBLIC",
)


def _grants() -> list[str]:
    return list(ENDURECIMIENTO) + [c.conceder() for c in _concesiones()]


def _revocaciones() -> list[str]:
    """Lo mismo, al reves y en orden inverso. Ni una sentencia mas."""
    return [c.revocar() for c in reversed(_concesiones())]


# ---------------------------------------------------------------------------
# Capa de publicacion analitica
# ---------------------------------------------------------------------------
#
# Dos schemas y una separacion que es el punto entero de esta parte:
#
# * ``privado`` guarda el **mapa de seudonimos**: que UUID le corresponde a cada
#   paciente y a cada embarazo. Lo escriben el ETL y el mantenimiento, y nadie
#   mas lo alcanza -- ``fetalalert_powerbi`` no recibe ni USAGE sobre el schema,
#   de modo que no puede nombrarlo aunque quisiera.
# * ``publicacion`` contiene cuatro vistas. Son vistas y no tablas a proposito:
#   una vista lee sus tablas con los privilegios de **su propietario**, asi que
#   publicar una columna es un acto explicito. Conceder SELECT sobre una tabla
#   de ``analitico`` habria publicado tambien la cedula, el nombre y el telefono
#   que esa tabla lleva.
#
# **Esto es seudonimizacion, no anonimizacion.** El seudonimo es estable y el
# mapa existe: quien tuviera acceso al mapa puede volver a la persona, y esa es
# justamente la propiedad que hace util la serie longitudinal. Lo que se protege
# es quien puede recorrer ese camino, no la posibilidad de recorrerlo.

MAPA = f"""
-- Los nombres de las restricciones siguen la convencion del proyecto, que es
-- la misma que ``app.models.privado`` declara: si divergieran, ``alembic
-- check`` lo diria en cada ejecucion. Las tablas no llevan COMMENT por el mismo
-- motivo -- la metadata no lo declara --, y el porque de cada una vive en el
-- docstring de su modelo y en el bloque de arriba.
CREATE TABLE {PRIVADO}.seudonimo_paciente (
    id_paciente integer NOT NULL
        CONSTRAINT pk_seudonimo_paciente PRIMARY KEY
        CONSTRAINT fk_seudonimo_paciente_id_paciente_paciente
            REFERENCES {OPERACIONAL}.paciente(id_paciente) ON DELETE RESTRICT,
    seudonimo uuid NOT NULL DEFAULT gen_random_uuid()
        CONSTRAINT uq_seudonimo_paciente_seudonimo UNIQUE,
    creado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE {PRIVADO}.seudonimo_embarazo (
    id_embarazo integer NOT NULL
        CONSTRAINT pk_seudonimo_embarazo PRIMARY KEY
        CONSTRAINT fk_seudonimo_embarazo_id_embarazo_embarazo
            REFERENCES {OPERACIONAL}.embarazo(id_embarazo) ON DELETE RESTRICT,
    seudonimo uuid NOT NULL DEFAULT gen_random_uuid()
        CONSTRAINT uq_seudonimo_embarazo_seudonimo UNIQUE,
    creado_en timestamptz NOT NULL DEFAULT now()
);
"""

# ``DISTINCT`` en la vigencia y no en el JOIN: un embarazo puede tener a la vez
# un seguimiento PRINCIPAL, uno de APOYO y uno de REEMPLAZO, y los tres conceden
# lo mismo. Sin el DISTINCT el mismo medico veria el embarazo repetido.
VIGENCIA = (
    "sc.activo AND sc.fecha_asignacion <= CURRENT_DATE "
    "AND (sc.fecha_fin IS NULL OR sc.fecha_fin >= CURRENT_DATE)"
)

# Umbral de celda de la superficie administrativa. Por debajo de este numero de
# embarazos distintos, la fila no se publica: un agregado sobre dos embarazos
# es una trayectoria individual con otro nombre.
MINIMO_DE_CELDA = 5

VISTAS = f"""
CREATE VIEW {PUBLICACION}.v_embarazo AS
SELECT sem.seudonimo                       AS seudonimo_embarazo,
       sp.seudonimo                        AS seudonimo_paciente,
       de.estado_embarazo,
       de.clasificacion_embarazo,
       de.duracion_est_semanas,
       de.numero_gestas,
       de.numero_partos,
       -- Fechas generalizadas al mes: la semana gestacional y la evolucion se
       -- siguen igual, y un dia exacto es un cuasi-identificador.
       date_trunc('month', de.fecha_inicio)::date        AS mes_inicio,
       date_trunc('month', de.fecha_probable_parto)::date AS mes_probable_parto,
       (de.fecha_cierre IS NOT NULL)                     AS cerrado,
       -- Edad en tramos de cinco anios, calculada al inicio del embarazo.
       (5 * floor(
            extract(year FROM age(de.fecha_inicio, dp.fecha_nac)) / 5))::integer
                                                          AS edad_tramo_inicio,
       -- Ubicacion a nivel de provincia. El distrito se queda fuera.
       dc.provincia
FROM {ANALITICO}.dim_embarazo de
JOIN {PRIVADO}.seudonimo_embarazo sem ON sem.id_embarazo = de.id_embarazo
JOIN {ANALITICO}.dim_paciente dp ON dp.id_paciente = de.id_paciente
JOIN {PRIVADO}.seudonimo_paciente sp ON sp.id_paciente = de.id_paciente
LEFT JOIN {ANALITICO}.dim_clinica dc ON dc.id_clinica = dp.id_clinica;

COMMENT ON VIEW {PUBLICACION}.v_embarazo IS
'Superficie longitudinal por episodio, seudonimizada. Sin cedula, nombre, '
'telefono, email, fecha de nacimiento, distrito ni identificadores '
'operacionales.';

CREATE VIEW {PUBLICACION}.v_lectura AS
SELECT sem.seudonimo AS seudonimo_embarazo,
       sp.seudonimo  AS seudonimo_paciente,
       dt.semana_gestacion,
       dt.trimestre,
       ds.codigo_nivel AS codigo_semaforo,
       ds.prioridad    AS prioridad_semaforo,
       f.hr_valor,
       f.spo2_valor,
       f.mov_valor,
       f.estado_hr,
       f.estado_spo2,
       f.estado_mov,
       f.fecha_hora::date AS fecha_captura,
       -- Numero de la sesion dentro del episodio, no su clave operacional. Es
       -- lo que permite medir adherencia sin publicar un id de la base.
       dense_rank() OVER (
           PARTITION BY f.id_embarazo ORDER BY f.id_sesion
       ) AS secuencia_sesion,
       dc.provincia
FROM {ANALITICO}.fact_lectura_biometrica f
JOIN {PRIVADO}.seudonimo_embarazo sem ON sem.id_embarazo = f.id_embarazo
JOIN {PRIVADO}.seudonimo_paciente sp ON sp.id_paciente = f.id_paciente
JOIN {ANALITICO}.dim_tiempo_gestacional dt
     ON dt.id_tiempo_gest = f.id_tiempo_gestacional
JOIN {ANALITICO}.dim_semaforo ds ON ds.id_semaforo = f.id_semaforo
LEFT JOIN {ANALITICO}.dim_clinica dc ON dc.id_clinica = f.id_clinica;

COMMENT ON VIEW {PUBLICACION}.v_lectura IS
'Serie longitudinal de lecturas, seudonimizada. Conserva las variables '
'clinicas y los estados que las alertas necesitan; no publica id_lectura, '
'id_sesion, id_paciente, id_medico ni id_embarazo.';

CREATE VIEW {PUBLICACION}.v_entitlement_medico AS
SELECT DISTINCT lower(u.email) AS upn_medico,
       sem.seudonimo           AS seudonimo_embarazo
FROM {OPERACIONAL}.seguimiento_clinico sc
JOIN {OPERACIONAL}.usuario_medico um ON um.id_medico = sc.id_medico
JOIN {OPERACIONAL}.usuario u ON u.id_usuario = um.id_usuario
JOIN {OPERACIONAL}.rol r ON r.id_rol = u.id_rol
JOIN {PRIVADO}.seudonimo_embarazo sem ON sem.id_embarazo = sc.id_embarazo
WHERE {VIGENCIA}
  AND u.activo
  AND r.nombre_rol = 'MEDICO';

COMMENT ON VIEW {PUBLICACION}.v_entitlement_medico IS
'La relacion medico-embarazo que aplica el RLS dinamico del dataset. Deriva de '
'SeguimientoClinico vigente y de nada mas: medico_clinica no aparece, asi que '
'compartir clinica no concede lectura. Los tres tipos -- PRINCIPAL, APOYO y '
'REEMPLAZO -- conceden lo mismo, y por eso el tipo no se filtra. ``upn_medico`` '
'es un identificador directo y existe unicamente para aplicar la seguridad: no '
'se expone como columna analitica de ningun reporte.';

CREATE VIEW {PUBLICACION}.v_resumen_administrativo AS
SELECT dc.provincia,
       date_trunc('month', f.fecha_hora)::date AS mes,
       ds.codigo_nivel AS codigo_semaforo,
       count(*)                          AS lecturas,
       count(DISTINCT f.id_embarazo)     AS embarazos,
       round(avg(f.hr_valor), 1)         AS hr_promedio,
       round(avg(f.spo2_valor), 1)       AS spo2_promedio
FROM {ANALITICO}.fact_lectura_biometrica f
JOIN {ANALITICO}.dim_semaforo ds ON ds.id_semaforo = f.id_semaforo
LEFT JOIN {ANALITICO}.dim_clinica dc ON dc.id_clinica = f.id_clinica
GROUP BY dc.provincia, date_trunc('month', f.fecha_hora), ds.codigo_nivel
HAVING count(DISTINCT f.id_embarazo) >= {MINIMO_DE_CELDA};

COMMENT ON VIEW {PUBLICACION}.v_resumen_administrativo IS
'Indicadores operativos agregados. Sin seudonimos y sin ninguna columna que '
'permita reconstruir una trayectoria: el HAVING descarta las celdas con menos '
'de {MINIMO_DE_CELDA} embarazos distintos, porque un agregado sobre dos '
'episodios es una trayectoria individual con otro nombre.';
"""


def _schemas_de_publicacion() -> list[str]:
    """Los dos schemas, cerrados a PUBLIC antes de que contengan nada."""
    return [
        f"CREATE SCHEMA {PRIVADO}",
        f"CREATE SCHEMA {PUBLICACION}",
        f"REVOKE ALL ON SCHEMA {PRIVADO} FROM PUBLIC",
        f"REVOKE ALL ON SCHEMA {PUBLICACION} FROM PUBLIC",
        # Y lo que se cree en ellos manana tampoco sera de PUBLIC. Se fija para
        # el migrador, que es quien crea objetos aqui.
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {PRIVADO} "
        "REVOKE ALL ON TABLES FROM PUBLIC",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {PUBLICACION} "
        "REVOKE ALL ON TABLES FROM PUBLIC",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {PRIVADO} "
        "REVOKE ALL ON SEQUENCES FROM PUBLIC",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {PUBLICACION} "
        "REVOKE ALL ON SEQUENCES FROM PUBLIC",
    ]


def _permisos_de_publicacion() -> list[str]:
    """Lo que Power BI puede leer. **Antes** de transferir la propiedad.

    El orden es el mismo que el de los helpers, y por la misma razon: ``GRANT``
    exige ser propietario o tener grant option, y ejecutado despues del ``ALTER
    VIEW ... OWNER TO`` el migrador ya no es ninguna de las dos cosas. Ahi
    PostgreSQL responde «permission denied for table v_embarazo» y la migracion
    aborta -- se comprobo reproduciendolo.

    Estas concesiones no aparecen en ``_concesiones()`` y por tanto no se
    retiran una a una en el downgrade: viven en ``publicacion``, y ese schema se
    elimina entero. Retirarlas antes seria retirar algo que esta a punto de
    dejar de existir.

    USAGE sobre ``publicacion`` y nada mas. Ni ``operacional``, ni ``analitico``,
    ni ``privado``: sin USAGE sobre un schema ese rol no puede ni nombrar sus
    objetos, de modo que la proteccion no depende de acordarse de revocar una
    tabla nueva.
    """
    return [
        f"GRANT USAGE ON SCHEMA {PUBLICACION} TO {ROL_POWERBI}",
        *[
            f"GRANT SELECT ON TABLE {PUBLICACION}.{vista} TO {ROL_POWERBI}"
            for vista in VISTAS_PUBLICADAS
        ],
    ]


# La unica vista que cambia de propietario, y el inventario de por que.
#
# Una vista se ejecuta con los privilegios de **su propietario**, y eso es lo
# que permite publicar unas columnas sin conceder la tabla entera. Tres de las
# cuatro leen solo ``analitico`` y ``privado``, que el migrador posee y que no
# llevan politicas: se quedan con el, y el conjunto de objetos que cambian de
# dueno se reduce a uno.
#
# ``v_entitlement_medico`` es la excepcion, y la necesidad es concreta: lee
# ``operacional.usuario_medico``, que lleva FORCE ROW LEVEL SECURITY. El
# migrador, aunque sea el dueno de esa tabla, queda sujeto a sus politicas --
# eso es lo que significa FORCE -- y no tiene ninguna, de modo que la vista
# devolveria cero filas y el RLS del dataset no autorizaria a nadie.
# ``fetalalert_rls_owner`` si tiene una, ``pol_helpers USING (true)``, es NOLOGIN
# y ningun rol de runtime puede asumirlo. Es el mismo patron que ya sostiene los
# helpers SECURITY DEFINER, aplicado al unico sitio que lo necesita.
VISTA_CON_PROPIETARIO_PROPIO = "v_entitlement_medico"


def _propiedad_de_las_vistas() -> list[str]:
    """Transfiere **una** vista, con lo minimo para que pueda leer.

    El CREATE sobre ``publicacion`` se concede y se retira dentro de esta misma
    transaccion: ``ALTER ... OWNER TO`` lo exige y no tiene por que sobrevivir.

    El SELECT sobre ``privado.seudonimo_embarazo`` si persiste, porque la vista
    lo necesita en cada consulta. Es una tabla, no el mapa entero: el dueno de
    las politicas no recibe acceso a ``seudonimo_paciente``, que es el que ataria
    un seudonimo a una persona.
    """
    return [
        f"GRANT CREATE ON SCHEMA {PUBLICACION} TO {ROL_RLS_OWNER}",
        f"ALTER VIEW {PUBLICACION}.{VISTA_CON_PROPIETARIO_PROPIO} "
        f"OWNER TO {ROL_RLS_OWNER}",
        f"REVOKE CREATE ON SCHEMA {PUBLICACION} FROM {ROL_RLS_OWNER}",
    ]


# ---------------------------------------------------------------------------
# Politicas
# ---------------------------------------------------------------------------

API = _LISTA(ROLES_DE_APLICACION)

# Un embarazo es visible si es de la paciente conectada o si el medico conectado
# lo sigue hoy. Las dos ramas devuelven falso sin contexto.
VISIBLE_EMBARAZO = (
    f"id_paciente = {SEGURIDAD}.paciente_actual() "
    f"OR {SEGURIDAD}.embarazo_en_seguimiento_vigente(id_embarazo)"
)

# Las tablas que cuelgan del embarazo heredan su visibilidad preguntando por el.
# Ese EXISTS queda sujeto a la politica de ``embarazo``, asi que la regla vive
# en un solo sitio.
def _cuelga_de_embarazo(tabla: str) -> str:
    return (
        f"EXISTS (SELECT 1 FROM {OPERACIONAL}.embarazo e "
        f"WHERE e.id_embarazo = {tabla}.id_embarazo)"
    )


def _es_de_la_paciente(tabla: str) -> str:
    """Solo la gestante escribe. Un medico lee, no crea sesiones ni lecturas."""
    return (
        f"EXISTS (SELECT 1 FROM {OPERACIONAL}.embarazo e "
        f"WHERE e.id_embarazo = {tabla}.id_embarazo "
        f"AND e.id_paciente = {SEGURIDAD}.paciente_actual())"
    )


def _politicas() -> list[str]:
    o = OPERACIONAL
    sentencias: list[str] = []

    for tabla in TABLAS_CON_RLS:
        sentencias.append(f"ALTER TABLE {o}.{tabla} ENABLE ROW LEVEL SECURITY")
        sentencias.append(f"ALTER TABLE {o}.{tabla} FORCE ROW LEVEL SECURITY")
        # Mantenimiento, en todas: el cargador del dataset simulado y cualquier
        # migracion de datos escriben con esta identidad, que ningun rol de
        # runtime hereda ni puede asumir.
        sentencias.append(
            f"CREATE POLICY pol_mantenimiento ON {o}.{tabla} "
            f"FOR ALL TO {ROL_MANTENIMIENTO} USING (true) WITH CHECK (true)"
        )

    # --- Puentes de identidad -------------------------------------------
    #
    # ``pol_propia`` no tiene rama de ADMIN, y esa ausencia es deliberada. Con
    # ``OR es_admin()`` una credencial administrativa podia listar los puentes
    # enteros -- que cuenta corresponde a que paciente y a que medico --, lo que
    # es exactamente la enumeracion que este ticket existe para impedir. ADMIN
    # aprovisiona cuentas, y para eso le basta preguntar por un identificador
    # que ya trae y recibir una palabra: ver ``estado_del_perfil_*``.
    for tabla in ("usuario_paciente", "usuario_medico"):
        sentencias.append(
            f"CREATE POLICY pol_propia ON {o}.{tabla} FOR SELECT TO {API} "
            f"USING (id_usuario = {SEGURIDAD}.usuario_actual_id())"
        )
        sentencias.append(
            f"CREATE POLICY pol_provision ON {o}.{tabla} FOR INSERT TO {API} "
            f"WITH CHECK ({SEGURIDAD}.es_admin())"
        )
        # Los helpers corren como su dueno y leen estos puentes: los de contexto
        # para resolver el perfil, los de provision para responder «vinculado».
        # Ninguno de los dos duenos puede autenticarse ni ser asumido.
        sentencias.append(
            f"CREATE POLICY pol_helpers ON {o}.{tabla} FOR SELECT "
            f"TO {ROL_RLS_OWNER}, {ROL_PROVISION_OWNER} USING (true)"
        )

    # --- Embarazo --------------------------------------------------------
    sentencias.append(
        f"CREATE POLICY pol_alcance ON {o}.embarazo FOR SELECT TO {API} "
        f"USING ({VISIBLE_EMBARAZO})"
    )

    # --- Asignacion de dispositivo ---------------------------------------
    sentencias.append(
        f"CREATE POLICY pol_alcance ON {o}.asignacion_dispositivo FOR SELECT "
        f"TO {API} USING ({_cuelga_de_embarazo('asignacion_dispositivo')})"
    )

    # --- Sesiones --------------------------------------------------------
    sentencias.append(
        f"CREATE POLICY pol_alcance ON {o}.sesion_monitoreo FOR SELECT TO {API} "
        f"USING ({_cuelga_de_embarazo('sesion_monitoreo')})"
    )
    sentencias.append(
        f"CREATE POLICY pol_ingesta ON {o}.sesion_monitoreo FOR INSERT TO {API} "
        f"WITH CHECK ({_es_de_la_paciente('sesion_monitoreo')})"
    )

    # --- Lecturas --------------------------------------------------------
    visible_sesion = (
        f"EXISTS (SELECT 1 FROM {o}.sesion_monitoreo s "
        f"WHERE s.id_sesion = lectura_biometrica.id_sesion)"
    )
    propia_sesion = (
        f"EXISTS (SELECT 1 FROM {o}.sesion_monitoreo s "
        f"JOIN {o}.embarazo e ON e.id_embarazo = s.id_embarazo "
        f"WHERE s.id_sesion = lectura_biometrica.id_sesion "
        f"AND e.id_paciente = {SEGURIDAD}.paciente_actual())"
    )
    sentencias.append(
        f"CREATE POLICY pol_alcance ON {o}.lectura_biometrica FOR SELECT "
        f"TO {API} USING ({visible_sesion})"
    )
    sentencias.append(
        f"CREATE POLICY pol_ingesta ON {o}.lectura_biometrica FOR INSERT "
        f"TO {API} WITH CHECK ({propia_sesion})"
    )

    # --- ETL: el universo completo, por una via tecnica y explicita -------
    # ``USING (true)`` dirigido solo a su rol. No hereda contexto de usuario
    # final, y ningun rol de aplicacion queda cubierto por esta politica.
    for tabla in TABLAS_DEL_ETL_CON_RLS:
        sentencias.append(
            f"CREATE POLICY pol_etl ON {o}.{tabla} FOR SELECT "
            f"TO {ROL_ETL} USING (true)"
        )

    return sentencias


def _nombres_de_politicas() -> list[tuple[str, str]]:
    """(politica, tabla) de todo lo que esta revision crea, para el downgrade."""
    pares = [("pol_mantenimiento", t) for t in TABLAS_CON_RLS]
    for tabla in ("usuario_paciente", "usuario_medico"):
        pares += [("pol_propia", tabla), ("pol_provision", tabla), ("pol_helpers", tabla)]
    pares += [
        ("pol_alcance", "embarazo"),
        ("pol_alcance", "asignacion_dispositivo"),
        ("pol_alcance", "sesion_monitoreo"),
        ("pol_ingesta", "sesion_monitoreo"),
        ("pol_alcance", "lectura_biometrica"),
        ("pol_ingesta", "lectura_biometrica"),
    ]
    pares += [("pol_etl", t) for t in TABLAS_DEL_ETL_CON_RLS]
    return pares


# ---------------------------------------------------------------------------
# upgrade / downgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    _sql(PREFLIGHT)

    # El indice que la politica de lecturas necesita. Va antes que la politica
    # para que ninguna consulta protegida llegue a correr sin el.
    op.create_index(
        "ix_lectura_biometrica_id_sesion",
        "lectura_biometrica",
        ["id_sesion"],
        unique=False,
        schema=OPERACIONAL,
    )

    op.execute(sa.text(f"CREATE SCHEMA {SEGURIDAD}"))
    for sentencia in _schema_de_helpers():
        _sql(sentencia)
    _sql(HELPERS)
    for sentencia in _permisos_de_helpers():
        _sql(sentencia)
    for sentencia in _propiedad_de_helpers():
        _sql(sentencia)
    # La capa analitica publicada. Va antes de los grants porque estos
    # conceden SELECT sobre sus vistas, y despues de los helpers porque su
    # propietario es el mismo rol NOLOGIN.
    for sentencia in _schemas_de_publicacion():
        _sql(sentencia)
    _sql(MAPA)
    _sql(VISTAS)
    for sentencia in _permisos_de_publicacion():
        _sql(sentencia)
    for sentencia in _propiedad_de_las_vistas():
        _sql(sentencia)

    for sentencia in _grants():
        _sql(sentencia)
    for sentencia in _politicas():
        _sql(sentencia)
    # Va al final: las politicas ya existen, asi que el trigger reescrito no
    # corre ni una vez contra el estado anterior.
    _sql(_trigger(_CONTEO_CON_HELPERS))


def downgrade() -> None:
    # Primero el cuerpo original del trigger: los helpers a los que llama estan
    # en ``seguridad``, y ese schema desaparece unas lineas mas abajo.
    _sql(_trigger(_CONTEO_DIRECTO))

    for politica, tabla in _nombres_de_politicas():
        _sql(f"DROP POLICY {politica} ON {OPERACIONAL}.{tabla}")
    for tabla in TABLAS_CON_RLS:
        _sql(f"ALTER TABLE {OPERACIONAL}.{tabla} NO FORCE ROW LEVEL SECURITY")
        _sql(f"ALTER TABLE {OPERACIONAL}.{tabla} DISABLE ROW LEVEL SECURITY")

    # Exactamente lo que ``_concesiones()`` concedio, retirado una a una. Las
    # del schema ``seguridad`` no estan en esa lista: se van con el DROP.
    for sentencia in _revocaciones():
        _sql(sentencia)

    # Los dos schemas de publicacion se van enteros, con sus vistas y su mapa.
    # CASCADE aqui solo alcanza lo que esta revision creo dentro de ellos:
    # ninguna otra revision los toca, y el downgrade los elimina completos.
    _sql(f"DROP SCHEMA {PUBLICACION} CASCADE")
    _sql(f"DROP SCHEMA {PRIVADO} CASCADE")
    _sql(f"DROP SCHEMA {SEGURIDAD} CASCADE")
    op.drop_index(
        "ix_lectura_biometrica_id_sesion",
        table_name="lectura_biometrica",
        schema=OPERACIONAL,
    )
