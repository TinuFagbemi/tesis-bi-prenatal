"""publish authorised clinical surface for the medical dashboard

Revision ID: 9c1d7f2ab4e8
Revises: 3b4a352bc39a
Create Date: 2026-09-26 23:40:00.000000

La superficie clinica autorizada que el tablero medico necesita, y las dos
columnas del modelo estrella que le faltaban (SCRUM-99).

**Por que existe esta revision.** SCRUM-98 publico cuatro vistas seudonimizadas
y demostro sobre ellas el aislamiento por medico. Esa superficie era suficiente
para validar seguridad y RLS, y sigue siendo correcta; lo que no es, es
utilizable para el caso clinico de SCRUM-73. Un medico no puede dar seguimiento
a un seudonimo: necesita saber a quien atiende, distinguir dos pacientes que se
llaman igual y poder contactarla. Esta revision **extiende** aquella superficie;
no la corrige ni la reemplaza.

**Que cambia respecto a SCRUM-98, y que no.** Las cuatro vistas anteriores se
quedan exactamente como estan -- ni una columna, ni un predicado, ni un
propietario --. ``v_lectura`` en particular se reutiliza tal cual como hecho del
modelo medico: sigue teniendo una fila por lectura y conserva la ``secuencia_
sesion`` corregida. Lo que se anade son siete vistas nuevas y dos columnas.

**La PII, y por que sigue siendo minimo privilegio.** ``v_paciente_medico``
publica nombre, cedula, telefono y correo. Eso es un cambio de postura
deliberado y aprobado, no un descuido: el nombre no identifica -- dos gestantes
pueden llamarse igual -- y sin cedula el medico no puede saber a cual de las dos
esta viendo. La PII no se publica globalmente: la vista solo tiene fila para una
paciente con al menos un seguimiento clinico vigente, y dentro del modelo de
Power BI el rol de RLS la reduce a las pacientes de quien consulta.

**Donde vive el aislamiento, dicho sin rodeos.** En modo Import PostgreSQL no
aplica RLS individual por medico en cada visualizacion. PostgreSQL protege la
frontera de publicacion -- que objetos puede nombrar la cuenta tecnica -- y el
aislamiento entre medicos lo aplica el RLS del dataset, por UPN. La cuenta
``fetalalert_powerbi`` puede leer la superficie publicada entera, y por eso es
una credencial de servicio que no se entrega a ningun medico. DirectQuery con
SSO daria filtrado en la base y queda fuera del alcance de este ticket.

**Dos entitlements, y no uno.** El de embarazo ya existia. El de paciente es
nuevo porque la alternativa -- meter ``upn_medico`` dentro de la dimension de
paciente -- repetiria ``seudonimo_paciente`` una vez por medico autorizado y esa
columna dejaria de ser clave: Power BI no admite el lado *uno* de una relacion
con valores repetidos, y la dimension acabaria en una relacion muchos a muchos
con embarazo. Separandolos, la dimension conserva una fila por paciente y el
filtro de RLS sigue actuando directamente sobre la tabla que lleva la PII.

**Ninguna vista nueva toca ``operacional``.** Las siete leen ``analitico``,
``privado`` y ``publicacion.v_entitlement_medico``. Por eso las siete pertenecen
al migrador y ninguna necesita ``fetalalert_rls_owner``: el unico objeto que
cruza hacia el operacional sigue siendo la vista de entitlement de SCRUM-98, que
ya resolvia ese problema. Y por eso tambien ``fetalalert_rls_owner`` sigue sin
acceso a ``privado.seudonimo_paciente``, que es el mapa que ata un seudonimo a
una persona.

**Las dos columnas nuevas.** ``dim_paciente.email_pac`` viene de
``operacional.paciente.email_pac`` y de ningun otro sitio: no es
``usuario.email``, que es una credencial. ``dim_embarazo.id_clinica`` viene de
``operacional.embarazo.id_clinica``; ``dim_paciente.id_clinica`` no servia para
esto porque se deriva de *todos* los embarazos de la paciente y queda NULL
cuando son de clinicas distintas -- justo el caso en que haria falta.

**Tener clinica no autoriza nada.** ``medico_clinica`` no aparece en ninguna
vista de esta revision. La autoridad del acceso individual sigue siendo, y solo,
``seguimiento_clinico`` vigente.

Todos los datos de este proyecto son simulados y ficticios.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c1d7f2ab4e8"
down_revision: Union[str, None] = "3b4a352bc39a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Los mismos nombres que la revision anterior, repetidos aqui a proposito: una
# migracion se lee sola y no debe importar constantes de otra, que podria
# cambiar despues sin que esta se entere.
OPERACIONAL = "operacional"
ANALITICO = "analitico"
PRIVADO = "privado"
PUBLICACION = "publicacion"

ROL_POWERBI = "fetalalert_powerbi"
ROL_RLS_OWNER = "fetalalert_rls_owner"

# La vista de SCRUM-98 de la que cuelgan las nuevas. Pertenece a
# ``fetalalert_rls_owner`` y por eso hay que pedirle permiso explicitamente.
VISTA_DE_ENTITLEMENT = "v_entitlement_medico"

# Las siete vistas nuevas, en orden de dependencia: la de entitlement de
# paciente antes que la dimension que la consulta.
VISTAS_NUEVAS = (
    "v_entitlement_paciente_medico",
    "v_paciente_medico",
    "v_embarazo_medico",
    "v_embarazo_factor_riesgo",
    "v_tiempo_gestacional",
    "v_semaforo",
    "v_factor_riesgo",
)


def _sql(sentencia: str) -> None:
    op.execute(sentencia)


# ---------------------------------------------------------------------------
# Las dos columnas del modelo estrella
# ---------------------------------------------------------------------------
#
# Las dos se anaden nullable, se rellenan desde el operacional y solo entonces
# pasan a NOT NULL. Es el unico orden que funciona sobre una base que ya tiene
# filas, y deja la columna igual de estricta que su origen -- que es lo que el
# modelo de ``app.etl.modelos`` declara y lo que ``alembic check`` compara.
#
# El backfill lee ``operacional.embarazo``, que lleva FORCE ROW LEVEL SECURITY.
# Funciona porque el migrador hereda ``fetalalert_mantenimiento`` y esa tabla
# lleva ``pol_mantenimiento FOR ALL ... USING (true)`` dirigida a ese rol. Sin
# esa politica el UPDATE veria cero filas y el ``SET NOT NULL`` siguiente
# fallaria -- ruidosamente, que es lo correcto, pero fallaria.
COLUMNAS = f"""
ALTER TABLE {ANALITICO}.dim_paciente ADD COLUMN email_pac varchar(120);

UPDATE {ANALITICO}.dim_paciente d
   SET email_pac = p.email_pac
  FROM {OPERACIONAL}.paciente p
 WHERE p.id_paciente = d.id_paciente;

ALTER TABLE {ANALITICO}.dim_paciente ALTER COLUMN email_pac SET NOT NULL;

ALTER TABLE {ANALITICO}.dim_embarazo ADD COLUMN id_clinica integer;

UPDATE {ANALITICO}.dim_embarazo d
   SET id_clinica = e.id_clinica
  FROM {OPERACIONAL}.embarazo e
 WHERE e.id_embarazo = d.id_embarazo;

ALTER TABLE {ANALITICO}.dim_embarazo ALTER COLUMN id_clinica SET NOT NULL;

ALTER TABLE {ANALITICO}.dim_embarazo
    ADD CONSTRAINT fk_dim_embarazo_id_clinica_dim_clinica
    FOREIGN KEY (id_clinica) REFERENCES {ANALITICO}.dim_clinica(id_clinica)
    ON DELETE RESTRICT;

CREATE INDEX ix_dim_embarazo_id_clinica
    ON {ANALITICO}.dim_embarazo (id_clinica);
"""

COLUMNAS_ATRAS = f"""
DROP INDEX {ANALITICO}.ix_dim_embarazo_id_clinica;
ALTER TABLE {ANALITICO}.dim_embarazo
    DROP CONSTRAINT fk_dim_embarazo_id_clinica_dim_clinica;
ALTER TABLE {ANALITICO}.dim_embarazo DROP COLUMN id_clinica;
ALTER TABLE {ANALITICO}.dim_paciente DROP COLUMN email_pac;
"""


# ---------------------------------------------------------------------------
# Las siete vistas
# ---------------------------------------------------------------------------
#
# ``EXISTS`` y no ``JOIN`` en los dos filtros de autorizacion, y la diferencia
# no es de estilo. Un embarazo puede tener a la vez un seguimiento PRINCIPAL,
# uno de APOYO y uno de REEMPLAZO, y los tres conceden lo mismo: un JOIN contra
# el entitlement devolveria el mismo episodio tres veces y la dimension dejaria
# de tener una fila por embarazo. ``EXISTS`` responde si hay alguno y no
# multiplica.

VISTAS = f"""
-- Derivada de la vista de SCRUM-98 y no del operacional. Asi el propietario de
-- las politicas no necesita ``privado.seudonimo_paciente``: el unico sitio
-- donde se cruza hacia ``operacional`` sigue siendo ``v_entitlement_medico``,
-- que ya pertenece a ``fetalalert_rls_owner`` por esa misma razon.
CREATE VIEW {PUBLICACION}.v_entitlement_paciente_medico AS
SELECT DISTINCT em.upn_medico,
       sp.seudonimo AS seudonimo_paciente
FROM {PUBLICACION}.{VISTA_DE_ENTITLEMENT} em
JOIN {PRIVADO}.seudonimo_embarazo sem ON sem.seudonimo = em.seudonimo_embarazo
JOIN {ANALITICO}.dim_embarazo de      ON de.id_embarazo = sem.id_embarazo
JOIN {PRIVADO}.seudonimo_paciente sp  ON sp.id_paciente = de.id_paciente;

COMMENT ON VIEW {PUBLICACION}.v_entitlement_paciente_medico IS
'La relacion medico-paciente que aplica el RLS del dataset sobre la dimension '
'de paciente. Se deriva de v_entitlement_medico, de modo que hereda su '
'vigencia sin repetir su logica: una paciente aparece para un UPN si y solo si '
'ese medico tiene al menos un embarazo suyo autorizado hoy. El DISTINCT es '
'necesario porque una paciente con dos embarazos autorizados daria dos filas. '
'``upn_medico`` existe unicamente para aplicar la seguridad y no es una '
'columna analitica.';

-- Grano: una paciente. ``seudonimo_paciente`` es unico, y de eso depende que
-- Power BI pueda ponerla en el lado *uno* de su relacion con el embarazo.
CREATE VIEW {PUBLICACION}.v_paciente_medico AS
SELECT sp.seudonimo AS seudonimo_paciente,
       -- El campo del segmentador. Se compone aqui y no en Power Query ni en
       -- DAX porque asi se audita leyendo la vista, y porque el nombre sin la
       -- cedula no distingue a dos pacientes homonimas. La cedula se publica
       -- ademas como columna propia: el medico tiene que poder buscar por ella.
       dp.nombre_completo || ' — ' || dp.cedula AS paciente_display,
       dp.nombre_completo,
       dp.cedula,
       dp.telefono_pac,
       dp.email_pac,
       -- La fecha exacta, no el tramo de cinco anios de la superficie anonima:
       -- la edad materna es un factor de riesgo obstetrico y el tramo no sirve
       -- clinicamente. La edad se calcula en DAX, que es donde no envejece
       -- entre refrescos.
       dp.fecha_nac
FROM {ANALITICO}.dim_paciente dp
JOIN {PRIVADO}.seudonimo_paciente sp ON sp.id_paciente = dp.id_paciente
WHERE EXISTS (
    SELECT 1 FROM {PUBLICACION}.v_entitlement_paciente_medico ep
     WHERE ep.seudonimo_paciente = sp.seudonimo
);

COMMENT ON VIEW {PUBLICACION}.v_paciente_medico IS
'Publicacion clinica autorizada, no anonima ni seudonimizada: lleva nombre, '
'cedula, telefono y correo. Es PII limitada al proposito clinico -- identificar '
'inequivocamente a la paciente atendida y poder contactarla -- y el universo se '
'restringe aqui a las pacientes con al menos un seguimiento clinico vigente. '
'La reduccion a las pacientes de quien consulta la aplica el rol de RLS del '
'dataset sobre v_entitlement_paciente_medico. El nombre no es clave: las '
'relaciones usan seudonimo_paciente.';

-- Grano: un episodio. ``seudonimo_embarazo`` unico.
--
-- **Es un superset del contrato de ``v_embarazo``, y eso es deliberado.** El
-- tablero de SCRUM-73 ya esta construido sobre aquella vista: paginas, medidas,
-- formato y marcadores. Si esta publicara solo el detalle clinico nuevo, migrar
-- el informe obligaria a rehacer los visuales que hoy funcionan. Publicando
-- **las doce columnas de ``v_embarazo`` con el mismo nombre y el mismo tipo** y
-- anadiendo las nuevas al lado, el origen de esa tabla del modelo puede
-- cambiarse sin tocar nada mas.
--
-- Por eso conviven dos representaciones de la misma fecha, y no es duplicacion
-- gratuita: ``mes_inicio`` es la generalizada que el informe ya usa y
-- ``fecha_inicio`` la exacta que el seguimiento prenatal necesita. Aqui el mes
-- no protege nada -- la vista lleva el episodio completo y se une a una que
-- lleva la cedula --, asi que se conserva por compatibilidad, no por privacidad.
CREATE VIEW {PUBLICACION}.v_embarazo_medico AS
SELECT sem.seudonimo AS seudonimo_embarazo,
       sp.seudonimo  AS seudonimo_paciente,
       de.estado_embarazo,
       -- Siempre NULL: el ETL no inventa una clasificacion que ningun origen de
       -- negocio define todavia. Se publica unicamente porque forma parte del
       -- contrato de ``v_embarazo`` y quitarla romperia el informe existente.
       de.clasificacion_embarazo,
       de.duracion_est_semanas,
       de.numero_gestas,
       de.numero_partos,
       -- Las cuatro de compatibilidad, calculadas igual que en ``v_embarazo``.
       date_trunc('month', de.fecha_inicio)::date         AS mes_inicio,
       date_trunc('month', de.fecha_probable_parto)::date AS mes_probable_parto,
       (de.fecha_cierre IS NOT NULL)                      AS cerrado,
       (5 * floor(
            extract(year FROM age(de.fecha_inicio, dp.fecha_nac)) / 5))::integer
                                                          AS edad_tramo_inicio,
       -- Y el detalle clinico que SCRUM-99 anade. Fechas exactas: el
       -- seguimiento se planifica sobre la fecha probable de parto, y truncarla
       -- al mes -- correcto en la superficie anonima -- aqui la inutiliza.
       de.fecha_inicio,
       de.fecha_probable_parto,
       de.fecha_cierre,
       -- La clinica del episodio, desnormalizada. Son tres atributos sobre una
       -- dimension de muy pocas filas, y evitan una tabla y una relacion mas en
       -- el modelo: cada relacion que no existe es una via de propagacion de
       -- filtros que no hay que analizar. ``provincia`` ya estaba en el contrato
       -- anterior y conserva su nombre.
       dc.nombre_clinica,
       dc.provincia,
       dc.distrito
FROM {ANALITICO}.dim_embarazo de
JOIN {PRIVADO}.seudonimo_embarazo sem ON sem.id_embarazo = de.id_embarazo
JOIN {ANALITICO}.dim_paciente dp      ON dp.id_paciente  = de.id_paciente
JOIN {PRIVADO}.seudonimo_paciente sp  ON sp.id_paciente  = de.id_paciente
JOIN {ANALITICO}.dim_clinica dc       ON dc.id_clinica   = de.id_clinica
WHERE EXISTS (
    SELECT 1 FROM {PUBLICACION}.{VISTA_DE_ENTITLEMENT} em
     WHERE em.seudonimo_embarazo = sem.seudonimo
);

COMMENT ON VIEW {PUBLICACION}.v_embarazo_medico IS
'Un episodio por fila, con sus fechas exactas y la clinica que lo atiende. '
'Superset del contrato de v_embarazo: conserva sus doce columnas con el mismo '
'nombre y tipo para que el informe de SCRUM-73 pueda cambiar el origen de esa '
'tabla sin rehacer visuales, y anade el detalle clinico al lado. El distrito se '
'publica aqui y no en v_embarazo porque es el de la clinica, no el de la '
'paciente, y en una superficie ligada a la cedula no anade riesgo. Que el '
'episodio traiga clinica no autoriza nada: medico_clinica no participa en '
'ninguna vista y la autoridad sigue siendo seguimiento_clinico vigente.';

-- Grano: embarazo x factor. El puente que el tablero necesita para mostrar
-- varios factores por embarazo sin multiplicar el hecho.
CREATE VIEW {PUBLICACION}.v_embarazo_factor_riesgo AS
SELECT sem.seudonimo AS seudonimo_embarazo,
       dfr.clave_factor,
       b.fecha_diagnostico,
       b.activo
FROM {ANALITICO}.bridge_embarazo_factor_riesgo b
JOIN {PRIVADO}.seudonimo_embarazo sem ON sem.id_embarazo = b.id_embarazo
JOIN {ANALITICO}.dim_factor_riesgo dfr
     ON dfr.id_factor_riesgo = b.id_factor_riesgo
WHERE EXISTS (
    SELECT 1 FROM {PUBLICACION}.{VISTA_DE_ENTITLEMENT} em
     WHERE em.seudonimo_embarazo = sem.seudonimo
);

COMMENT ON VIEW {PUBLICACION}.v_embarazo_factor_riesgo IS
'Puente embarazo-factor, restringido al mismo universo que v_embarazo_medico: '
'los factores de un embarazo no autorizado no son alcanzables. No publica '
'``observaciones``, que es texto libre por embarazo -- hoy inocuo en el dataset '
'simulado, pero el contrato tiene que proteger tambien a los datasets que '
'vengan despues. La clave del factor es ``clave_factor``, no su id.';

-- Catalogo. Grano: una semana gestacional.
CREATE VIEW {PUBLICACION}.v_tiempo_gestacional AS
SELECT dt.semana_gestacion,
       dt.mes_gestacion,
       dt.trimestre
FROM {ANALITICO}.dim_tiempo_gestacional dt;

COMMENT ON VIEW {PUBLICACION}.v_tiempo_gestacional IS
'Catalogo cerrado de semanas gestacionales, sin filtro de autorizacion porque '
'no describe a nadie. No publica ``descripcion``, que en el catalogo vale '
'«Semana gestacional N» y no anade nada a semana_gestacion. Nada se recalcula '
'aqui: la clasificacion la determino el ETL.';

-- Catalogo. Grano: un nivel de semaforo.
CREATE VIEW {PUBLICACION}.v_semaforo AS
SELECT ds.codigo_nivel,
       ds.etiqueta_visual,
       ds.color_hex,
       ds.prioridad,
       ds.mensaje_app,
       ds.version_referencia
FROM {ANALITICO}.dim_semaforo ds;

COMMENT ON VIEW {PUBLICACION}.v_semaforo IS
'Catalogo de los tres niveles OK / WARNING / ERROR con su presentacion. Esta '
'revision publica datos y no redefine reglas: umbrales, clasificacion e '
'interpretacion clinica quedan exactamente como estaban.';

-- Catalogo. Grano: un factor de riesgo.
CREATE VIEW {PUBLICACION}.v_factor_riesgo AS
SELECT dfr.clave_factor,
       dfr.nombre_factor,
       dfr.activo
FROM {ANALITICO}.dim_factor_riesgo dfr;

COMMENT ON VIEW {PUBLICACION}.v_factor_riesgo IS
'Catalogo de factores de riesgo. No publica ``descripcion``: en el catalogo es '
'una glosa sin valor clinico para el tablero. ``activo`` se refiere al factor '
'del catalogo, no a su diagnostico en un embarazo -- eso vive en el puente.';
"""


def _permisos() -> list[str]:
    """Lo que Power BI puede leer de lo nuevo, y nada mas.

    ``USAGE`` sobre ``publicacion`` ya lo concedio SCRUM-98 y no se repite. No
    se concede nada sobre ``analitico``, ``operacional``, ``privado`` ni
    ``seguridad``: sin ``USAGE`` sobre un schema el rol no puede ni nombrar sus
    objetos, de modo que la proteccion no depende de acordarse de revocar una
    tabla nueva.
    """
    return [
        f"GRANT SELECT ON TABLE {PUBLICACION}.{vista} TO {ROL_POWERBI}"
        for vista in VISTAS_NUEVAS
    ]


# Las vistas nuevas leen ``publicacion.v_entitlement_medico``, que dejo de ser
# del migrador cuando SCRUM-98 la transfirio a ``fetalalert_rls_owner``. Una
# vista se ejecuta con los privilegios de **su** propietario, asi que el
# migrador -- dueno de las siete nuevas -- necesita SELECT sobre ella, y solo su
# dueno puede concederselo.
#
# De ahi las tres capas de este bloque, y ninguna sobra:
#
# 1. ``GRANT USAGE ON SCHEMA publicacion TO fetalalert_rls_owner``. Sin USAGE un
#    rol no puede ni nombrar los objetos de un schema, y tampoco conceder nada
#    sobre ellos: sin esta linea el GRANT siguiente responde «permission denied
#    for schema publicacion» -- se comprobo reproduciendolo. Se retira al final
#    del bloque, en la misma transaccion. Es el mismo patron que SCRUM-98 ya usa
#    con CREATE para transferir la propiedad de esa misma vista.
# 2. ``SET LOCAL ROLE``. El migrador tiene membresia en ``fetalalert_rls_owner``
#    con la opcion SET pero **sin** INHERIT, de modo que asumir el rol es la
#    unica via y es deliberado que lo sea.
# 3. ``TO SESSION_USER`` y no ``TO CURRENT_USER``. Dentro de un ``SET ROLE``,
#    ``CURRENT_USER`` es el rol asumido: conceder a CURRENT_USER seria que
#    ``fetalalert_rls_owner`` se concediera a si mismo lo que ya tiene, y el
#    migrador se quedaria igual. ``SESSION_USER`` sigue siendo quien se conecto.
#
# Lo que **no** hace este bloque, y es la razon de que se haya elegido este
# camino: ``fetalalert_rls_owner`` no recibe acceso a
# ``privado.seudonimo_paciente``. Ese mapa es el que ataria un seudonimo a una
# persona, y SCRUM-98 se lo niega a proposito. Construir la vista de entitlement
# de paciente desde el operacional habria obligado a concederselo; derivarla de
# ``v_entitlement_medico`` no.
def _prestamo_de_lectura(sentencia: str) -> list[str]:
    """La concesion -- o su retirada -- envuelta en sus tres capas."""
    return [
        f"GRANT USAGE ON SCHEMA {PUBLICACION} TO {ROL_RLS_OWNER}",
        f"SET LOCAL ROLE {ROL_RLS_OWNER}",
        sentencia,
        "RESET ROLE",
        f"REVOKE USAGE ON SCHEMA {PUBLICACION} FROM {ROL_RLS_OWNER}",
    ]


def upgrade() -> None:
    _sql(COLUMNAS)

    for sentencia in _prestamo_de_lectura(
        f"GRANT SELECT ON TABLE {PUBLICACION}.{VISTA_DE_ENTITLEMENT} "
        f"TO SESSION_USER"
    ):
        _sql(sentencia)

    _sql(VISTAS)
    for sentencia in _permisos():
        _sql(sentencia)


def downgrade() -> None:
    # En orden inverso al de creacion: ``v_paciente_medico`` depende de la vista
    # de entitlement de paciente, que se va detras.
    for vista in reversed(VISTAS_NUEVAS):
        _sql(f"DROP VIEW {PUBLICACION}.{vista}")

    # Se retira el SELECT que el upgrade concedio, con la misma identidad que lo
    # concedio. Los GRANT de las siete vistas no se retiran uno a uno porque las
    # vistas acaban de dejar de existir.
    for sentencia in _prestamo_de_lectura(
        f"REVOKE SELECT ON TABLE {PUBLICACION}.{VISTA_DE_ENTITLEMENT} "
        f"FROM SESSION_USER"
    ):
        _sql(sentencia)

    _sql(COLUMNAS_ATRAS)
