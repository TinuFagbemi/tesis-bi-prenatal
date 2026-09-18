-- Aprovisionamiento de los roles NOLOGIN de FetalAlert (SCRUM-98).
--
-- Se ejecuta UNA vez por clúster, ANTES de Alembic, con una credencial que
-- tenga CREATEROLE. Es idempotente: crea lo que falte, verifica lo que ya
-- exista y aborta si encuentra un rol homónimo con atributos no permitidos.
-- Nunca corrige un rol existente por su cuenta: prefiere detenerse a alterar en
-- silencio un rol que alguien pudo crear con otra intención.
--
-- Lo que este archivo NO hace, y por qué:
--
-- * No contiene contraseñas y no crea roles con LOGIN. Los roles de conexión
--   -- fetalalert_api, fetalalert_etl, fetalalert_powerbi y el rol de pruebas --
--   los crea el despliegue o el CI desde secretos externos. Un secreto en un
--   archivo versionado deja de ser un secreto.
-- * No concede privilegios sobre schemas, tablas, secuencias ni funciones. Eso
--   pertenece a las migraciones de Alembic, que son las que conocen los objetos.
-- * No cambia el propietario de ningún objeto existente.
-- * No crea ni elimina bases de datos.
--
-- Los tres roles son NOLOGIN a propósito: no son cuentas, son capacidades.
-- Nadie se autentica como ellos; se usan como propietarios de funciones
-- SECURITY DEFINER y como destinatarios de políticas técnicas.

-- Ejecutar SIEMPRE en una sola transacción, para que un rol no conforme no deje
-- el clúster a medio aprovisionar:
--
--     psql -v ON_ERROR_STOP=1 -1 -f scripts/bootstrap_roles.sql
--     python scripts/bootstrap_roles.py aplicar
--
-- El archivo es SQL puro y sin meta-comandos de psql, para que ambos
-- caminos ejecuten exactamente el mismo texto.

DO $bootstrap$
DECLARE
    nombre_rol text;
    -- Los tres roles aprobados para SCRUM-98, y solo esos.
    roles_requeridos constant text[] := ARRAY[
        -- Propietario de los cinco helpers de contexto. Solo lee.
        'fetalalert_rls_owner',
        -- Propietario de los dos helpers de provisión. Necesita UPDATE de una
        -- única columna para poder tomar FOR NO KEY UPDATE; por eso está
        -- separado del anterior, para que ese privilegio no lo herede nadie más.
        'fetalalert_provision_owner',
        -- Destinatario de la policy de mantenimiento, para que un migrador o
        -- cargador que no sea superusuario pueda escribir el dataset simulado.
        'fetalalert_mantenimiento'
    ];
    fila pg_roles%ROWTYPE;
BEGIN
    FOREACH nombre_rol IN ARRAY roles_requeridos LOOP
        SELECT * INTO fila FROM pg_roles WHERE rolname = nombre_rol;

        IF NOT FOUND THEN
            -- Los NO* son los valores por omisión de PostgreSQL. Se escriben
            -- igualmente para que el contrato quede en el archivo y no dependa
            -- de que quien lo lea recuerde cuáles son los defaults.
            EXECUTE format(
                'CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
                'NOREPLICATION NOBYPASSRLS INHERIT',
                nombre_rol
            );
            RAISE NOTICE 'bootstrap: rol % creado', nombre_rol;
        ELSE
            -- Ya existe. No se altera: se verifica y, si no es conforme, se
            -- aborta la transacción entera.
            IF fila.rolcanlogin OR fila.rolsuper OR fila.rolbypassrls
               OR fila.rolcreaterole OR fila.rolcreatedb OR fila.rolreplication THEN
                RAISE EXCEPTION
                    'bootstrap abortado: el rol % ya existe con atributos no '
                    'permitidos (login=%, super=%, bypassrls=%, createrole=%, '
                    'createdb=%, replication=%). Se esperaba un rol NOLOGIN sin '
                    'ninguna capacidad. Revíselo manualmente antes de reintentar; '
                    'este script no altera roles preexistentes.',
                    nombre_rol, fila.rolcanlogin, fila.rolsuper, fila.rolbypassrls,
                    fila.rolcreaterole, fila.rolcreatedb, fila.rolreplication;
            END IF;
            RAISE NOTICE 'bootstrap: rol % ya existe y es conforme', nombre_rol;
        END IF;
    END LOOP;
END
$bootstrap$;

-- Membresías mínimas del migrador. Tres, y cada una con sus opciones escritas.
--
-- PostgreSQL 16 separa INHERIT de SET, y la diferencia es exactamente la que
-- este proyecto necesita:
--
--   INHERIT -> los privilegios del rol se aplican sin pedirlos;
--   SET     -> se puede hacer SET ROLE a ese rol, de forma deliberada.
--
-- fetalalert_mantenimiento va con INHERIT TRUE porque una policy dirigida a un
-- rol se evalúa contra los roles que el usuario **hereda**: sin herencia, la
-- policy de mantenimiento no se aplicaría y el cargador no podría escribir.
--
-- Los dos propietarios de helpers van con INHERIT FALSE y SET TRUE. Esa
-- combinación es la mínima que permite crear o transferir una función al
-- propietario previsto -- PostgreSQL exige «must be able to SET ROLE» para
-- ALTER FUNCTION ... OWNER TO -- sin que el migrador arrastre en cada sentencia
-- ordinaria los privilegios de esos roles.
--
-- Por qué hace falta concederlo explícitamente: un rol con CREATEROLE queda
-- como miembro de lo que crea, pero PostgreSQL 16 lo hace con
-- admin_option = true, inherit_option = FALSE y **set_option = FALSE**. Sin
-- esta concesión, la migración fallaría con «must be able to SET ROLE».
--
-- ADMIN va explícito y en FALSE. PostgreSQL 16 conserva la opción existente si
-- se omite, y dejarlo implícito sería confiar en un valor que no se ve. Este
-- bootstrap **no concede ADMIN a nadie**: el migrador ya lo tiene, y solo
-- porque él creó los roles. Esa concesión explícita añade una segunda fila en
-- pg_auth_members y no retira la primera, así que el ADMIN implícito sobrevive
-- y una segunda ejecución del bootstrap por el mismo migrador vuelve a
-- funcionar. Comprobado reproduciéndolo.
--
-- Consecuencia que conviene conocer: un migrador **distinto** del que creó los
-- roles no puede reejecutar este bootstrap. PostgreSQL responde «permission
-- denied to grant role ... Only roles with the ADMIN option may grant this
-- role». Delegarlo es una decisión administrativa deliberada -- un GRANT con
-- ADMIN OPTION desde quien lo tenga -- y no algo que este archivo haga solo.
--
-- Se concede a current_user, que es quien ejecuta este bootstrap: la credencial
-- administrativa que después correrá Alembic y el cargador del dataset. No se
-- nombra ningún rol concreto porque su nombre cambia entre entornos
-- (fetalalert_dev en local, fetalalert_ci en CI).
--
-- Ningún rol de runtime -- api, etl, powerbi, ni el de pruebas -- recibe
-- membresía aquí ni en ningún otro sitio. Que no la tengan es lo que les impide
-- asumir un propietario de helpers o heredar la policy de mantenimiento.
DO $membresias$
DECLARE
    concesion record;
BEGIN
    FOR concesion IN
        SELECT * FROM (VALUES
            ('fetalalert_mantenimiento',   true,  true),
            ('fetalalert_rls_owner',       false, true),
            ('fetalalert_provision_owner', false, true)
        ) AS v(rol, hereda, puede_set)
    LOOP
        EXECUTE format(
            'GRANT %I TO %I WITH ADMIN FALSE, INHERIT %s, SET %s',
            concesion.rol,
            current_user,
            CASE WHEN concesion.hereda THEN 'TRUE' ELSE 'FALSE' END,
            CASE WHEN concesion.puede_set THEN 'TRUE' ELSE 'FALSE' END
        );
        RAISE NOTICE 'bootstrap: % es miembro de % (ADMIN FALSE, INHERIT %, SET %)',
            current_user, concesion.rol, concesion.hereda, concesion.puede_set;
    END LOOP;
END
$membresias$;
