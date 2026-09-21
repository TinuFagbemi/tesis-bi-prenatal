"""Protección analítica: seudonimización, entitlements y aislamiento de Power BI.

Se omite salvo que esté definida ``SCRUM98_PUB_TEST_DATABASE_URL``. Nunca cae
por omisión sobre otra variable.

**Todo lo que juzga el aislamiento se ejecuta como ``fetalalert_powerbi``**, la
credencial técnica de solo lectura con la que Power BI consulta la base. No es
la identidad de ningún médico: cada médico entra a Power BI Service con la suya
y el RLS del *dataset* filtra por ella; lo que esta suite demuestra es que la
cuenta técnica, por sí sola, no alcanza nada que no esté publicado, y que la
relación de entitlements contiene exactamente las asignaciones vigentes.

El observador administrativo solo prepara escenarios y lee catálogos. Ninguna
aserción de aislamiento se hace con él.

La base debe estar migrada al head y cargada con el dataset simulado, y el ETL
debe haber corrido al menos una vez para que existan los seudónimos.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import os
import re
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.pool import NullPool

VARIABLE_DE_ENTORNO = "SCRUM98_PUB_TEST_DATABASE_URL"
ROL_ESPERADO = "fetalalert_powerbi"

VISTAS = (
    "v_embarazo",
    "v_lectura",
    "v_entitlement_medico",
    "v_resumen_administrativo",
)

# Lo que una superficie publicada no puede contener, buscado por nombre de
# columna en todas las vistas a la vez.
COLUMNAS_PROHIBIDAS = (
    "cedula",
    "nombre",
    "nombre_completo",
    "apellido",
    "apellido_paterno",
    "apellido_materno",
    "email_pac",
    "telefono",
    "telefono_pac",
    "telefono_med",
    "fecha_nac",
    "direccion",
    "observaciones",
    "descripcion",
    "distrito",
    "id_paciente",
    "id_embarazo",
    "id_lectura",
    "id_sesion",
    "id_medico",
    "id_clinica",
    "id_semaforo",
    "id_tiempo_gest",
    "id_tiempo_gestacional",
)

MINIMO_DE_CELDA = 5

# Marca del escenario que esta suite fabrica. Va en la cedula y en el correo del
# perfil ficticio, que son las dos columnas por las que la limpieza lo reconoce.
# El numero no colisiona con el dataset canonico, cuyas cedulas siguen el patron
# ``SIM-PAC-0NN``.
MARCA_DEL_ESCENARIO = "SCRUM98PUB"
CEDULA_DEL_ESCENARIO = f"SIM-PAC-{MARCA_DEL_ESCENARIO}"
EMAIL_DEL_ESCENARIO = f"perfil.{MARCA_DEL_ESCENARIO.lower()}@example.com"

pytestmark = pytest.mark.skipif(
    not os.environ.get(VARIABLE_DE_ENTORNO),
    reason=(
        f"Define {VARIABLE_DE_ENTORNO} apuntando a una base migrada, cargada y "
        f"con el ETL ejecutado, conectando como {ROL_ESPERADO}."
    ),
)


@pytest.fixture(scope="session")
def url() -> str:
    valor = os.environ[VARIABLE_DE_ENTORNO]
    if make_url(valor).username != ROL_ESPERADO:
        pytest.fail(
            f"{VARIABLE_DE_ENTORNO} debe conectarse como {ROL_ESPERADO}. Comprobar "
            "el aislamiento de la capa publicada con otra credencial no demuestra "
            "nada."
        )
    return valor


@pytest.fixture(scope="session")
def powerbi(url):
    motor = create_engine(url, poolclass=NullPool)
    try:
        yield motor
    finally:
        motor.dispose()


@pytest.fixture(scope="session")
def observador(url):
    """Prepara escenarios y lee catálogos. No juzga aislamiento."""
    administrativa = make_url(url).set(
        username=os.environ.get("SCRUM98_PUB_ADMIN_USER", "fetalalert_ci"),
        password=os.environ.get("SCRUM98_PUB_ADMIN_PASSWORD", ""),
    )
    motor = create_engine(
        administrativa, poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    try:
        yield motor
    finally:
        motor.dispose()


@pytest.fixture(scope="session")
def etl(url):
    """El engine del ETL. Es quien emite seudonimos, asi que es quien debe correr.

    Tres credenciales en esta suite y ninguna intercambiable: ``powerbi`` juzga
    el aislamiento, ``observador`` prepara y mira, y este ejecuta el ETL. Correr
    el ETL con el observador demostraria que un superusuario puede emitir
    seudonimos, que no es la pregunta.
    """
    del_etl = make_url(url).set(
        username=os.environ.get("SCRUM98_PUB_ETL_USER", "fetalalert_etl"),
        password=os.environ.get("SCRUM98_PUB_ETL_PASSWORD", ""),
    )
    motor = create_engine(del_etl, poolclass=NullPool)
    try:
        yield motor
    finally:
        motor.dispose()


def consultar(engine, sql: str, **parametros):
    with engine.connect() as conexion:
        return conexion.execute(text(sql), parametros).mappings().all()


def escalar(engine, sql: str, **parametros):
    filas = consultar(engine, sql, **parametros)
    return None if not filas else list(filas[0].values())[0]


# ---------------------------------------------------------------------------
# 1. La credencial técnica es realmente restringida
# ---------------------------------------------------------------------------


def test_el_rol_de_power_bi_no_es_superusuario_ni_omite_rls(powerbi):
    """Si esta fallara, ninguna de las demás demostraría nada."""
    fila = consultar(
        powerbi,
        "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication "
        "FROM pg_roles WHERE rolname = current_user",
    )[0]

    assert not any(fila.values())


def test_el_rol_de_power_bi_no_posee_ningun_objeto(powerbi):
    poseidos = escalar(
        powerbi,
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema' "
        "AND pg_has_role(current_user, c.relowner, 'USAGE')",
    )

    assert poseidos == 0


@pytest.mark.parametrize(
    "objetivo",
    [
        "fetalalert_rls_owner",
        "fetalalert_provision_owner",
        "fetalalert_mantenimiento",
        "fetalalert_etl",
        "fetalalert_api",
    ],
)
@pytest.mark.parametrize("via", ["MEMBER", "USAGE", "SET"])
def test_el_rol_de_power_bi_no_alcanza_ningun_rol_privilegiado(powerbi, objetivo, via):
    """Ni hereda ni puede asumir al migrador, al owner, al mantenimiento ni al ETL."""
    alcanza = escalar(
        powerbi,
        "SELECT pg_has_role(current_user, :objetivo, :via)",
        objetivo=objetivo,
        via=via,
    )

    assert alcanza is False


@pytest.mark.parametrize("esquema", ["operacional", "analitico", "privado", "seguridad"])
def test_el_rol_de_power_bi_no_alcanza_los_esquemas_internos(powerbi, esquema):
    """Sin USAGE no puede ni nombrar sus objetos.

    Es más fuerte que revocar tabla por tabla: una tabla nueva en cualquiera de
    estos esquemas queda fuera de su alcance sin que nadie tenga que acordarse.
    """
    assert escalar(
        powerbi, "SELECT has_schema_privilege(current_user, :e, 'USAGE')", e=esquema
    ) is False


@pytest.mark.parametrize(
    "tabla",
    [
        "operacional.paciente",
        "operacional.embarazo",
        "operacional.lectura_biometrica",
        "operacional.seguimiento_clinico",
        "analitico.dim_paciente",
        "analitico.fact_lectura_biometrica",
        "privado.seudonimo_paciente",
        "privado.seudonimo_embarazo",
    ],
)
def test_el_rol_de_power_bi_no_puede_leer_las_tablas_internas(powerbi, tabla):
    """Incluido el mapa privado, que es lo que haría reversible lo publicado."""
    with pytest.raises(ProgrammingError):
        consultar(powerbi, f"SELECT count(*) FROM {tabla}")


@pytest.mark.parametrize("esquema", ["publicacion", "privado"])
def test_public_no_recibe_privilegios_en_los_esquemas_nuevos(powerbi, esquema):
    assert escalar(
        powerbi, "SELECT has_schema_privilege('public', :e, 'USAGE')", e=esquema
    ) is False


def test_ningun_objeto_publicado_concede_nada_a_public(observador):
    """Ni las vistas ni el mapa dan un solo privilegio a PUBLIC.

    La migración ejecuta además ``ALTER DEFAULT PRIVILEGES ... REVOKE ALL ON
    TABLES FROM PUBLIC`` en los dos esquemas, y conviene decir qué hace y qué
    no: PostgreSQL no concede nada a PUBLIC sobre una tabla nueva, de modo que
    ese REVOKE no cambia el estado y no deja fila en ``pg_default_acl``. Se
    conserva porque hace explícita la intención y porque protege del caso en que
    alguien añada un ``GRANT ... TO PUBLIC`` por omisión más adelante; pero la
    garantía que de verdad se puede comprobar hoy es esta: ningún objeto de los
    dos esquemas lleva a PUBLIC en su ACL.
    """
    filas = consultar(
        observador,
        """
        SELECT n.nspname || '.' || c.relname AS objeto,
               array_to_string(c.relacl, ' ') AS acl
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname IN ('publicacion', 'privado')
        """,
    )

    assert len(filas) >= 6, "faltan objetos en los esquemas publicados"
    for fila in filas:
        acl = fila["acl"] or ""
        # Una entrada de PUBLIC en una ACL empieza por '=' -- sin receptor.
        assert " =" not in f" {acl}", fila


# ---------------------------------------------------------------------------
# 2. Lo publicado, y lo que no puede llevar
# ---------------------------------------------------------------------------


def test_power_bi_solo_ve_las_cuatro_vistas_publicadas(powerbi):
    visibles = {
        fila["table_name"]
        for fila in consultar(
            powerbi,
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'publicacion'",
        )
    }

    assert visibles == set(VISTAS)


@pytest.mark.parametrize("vista", VISTAS)
def test_ninguna_vista_publica_pii_ni_identificadores_operacionales(powerbi, vista):
    """Ni cédulas, ni nombres, ni teléfonos, ni claves internas del modelo."""
    columnas = {
        fila["column_name"]
        for fila in consultar(
            powerbi,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'publicacion' AND table_name = :v",
            v=vista,
        )
    }

    assert columnas, f"la vista {vista} no expone ninguna columna"
    assert not columnas & set(COLUMNAS_PROHIBIDAS), sorted(
        columnas & set(COLUMNAS_PROHIBIDAS)
    )


def test_las_fechas_publicadas_estan_generalizadas(powerbi):
    """Mes, no día. Un día exacto de inicio es un cuasi-identificador."""
    filas = consultar(powerbi, "SELECT * FROM publicacion.v_embarazo LIMIT 20")

    assert filas
    for fila in filas:
        assert fila["mes_inicio"].day == 1
        if fila["mes_probable_parto"] is not None:
            assert fila["mes_probable_parto"].day == 1


def tipo_instalado(engine, vista: str, columna: str) -> str | None:
    """El tipo que la base migrada declara, no el que el SQL de la vista sugiere."""
    return escalar(
        engine,
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema = 'publicacion' AND table_name = :v "
        "AND column_name = :c",
        v=vista,
        c=columna,
    )


def test_la_fecha_de_captura_se_publica_como_date_y_no_como_timestamp(powerbi):
    """Día calendario, sin hora: el tipo instalado lo garantiza.

    ``fecha_captura`` se conserva al **día** —no al mes— porque el seguimiento
    longitudinal, las tendencias, la adherencia y la secuencia de monitoreos lo
    necesitan. Lo que se retira es la hora. Que la vista la castee a ``date`` se
    lee aquí del catálogo de la base realmente migrada y no del código fuente de
    la vista: si alguien quitara el cast, el SQL seguiría pareciendo razonable y
    la columna volvería a publicar hora, minuto y segundo.

    Se consulta con la credencial de Power BI, que es quien vería el tipo.
    """
    tipo = tipo_instalado(powerbi, "v_lectura", "fecha_captura")

    assert tipo == "date", tipo
    assert "timestamp" not in (tipo or "")


def test_las_fechas_contextuales_del_episodio_se_publican_al_mes(powerbi):
    """La otra mitad del contrato: estas sí son mes, y también ``date``.

    ``date_trunc`` devuelve ``timestamp``; es el cast final a ``date`` lo que
    hace que no se publique una hora 00:00:00 que parecería un dato. Que el día
    sea siempre 1 lo comprueba ``test_las_fechas_publicadas_estan_generalizadas``.
    """
    for columna in ("mes_inicio", "mes_probable_parto"):
        assert tipo_instalado(powerbi, "v_embarazo", columna) == "date", columna


def test_la_edad_publicada_esta_en_tramos(powerbi):
    tramos = {
        fila["edad_tramo_inicio"]
        for fila in consultar(
            powerbi, "SELECT DISTINCT edad_tramo_inicio FROM publicacion.v_embarazo"
        )
    }

    assert tramos
    assert all(tramo % 5 == 0 for tramo in tramos), sorted(tramos)


def test_la_serie_conserva_lo_que_las_alertas_necesitan(powerbi):
    """Minimizar no es vaciar: las variables clínicas siguen ahí."""
    columnas = {
        fila["column_name"]
        for fila in consultar(
            powerbi,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'publicacion' AND table_name = 'v_lectura'",
        )
    }

    assert {
        "semana_gestacion",
        "trimestre",
        "codigo_semaforo",
        "hr_valor",
        "spo2_valor",
        "mov_valor",
        "estado_hr",
        "estado_spo2",
        "estado_mov",
        "fecha_captura",
        "secuencia_sesion",
        "provincia",
    } <= columnas


# ---------------------------------------------------------------------------
# 3. Seudonimización
# ---------------------------------------------------------------------------


def test_los_seudonimos_son_uuid_y_no_se_derivan_de_los_ids(observador):
    """Un hash de una clave secuencial se invierte probando los enteros.

    Se comprueba de dos maneras: que el valor sea un UUID versión 4 -- el que
    produce ``gen_random_uuid`` --, y que ordenar por el identificador no ordene
    los seudónimos. Una derivación determinista de un entero creciente dejaría
    correlación; la aleatoriedad no.
    """
    filas = consultar(
        observador,
        "SELECT id_paciente, seudonimo::text AS s FROM privado.seudonimo_paciente "
        "ORDER BY id_paciente",
    )

    assert len(filas) >= 10, "el mapa está casi vacío; ¿corrió el ETL?"
    for fila in filas:
        assert uuid.UUID(fila["s"]).version == 4

    seudonimos = [fila["s"] for fila in filas]
    assert seudonimos != sorted(seudonimos)


def test_pacientes_distintas_tienen_seudonimos_distintos(observador):
    filas = consultar(
        observador,
        "SELECT count(*) AS n, count(DISTINCT seudonimo) AS d "
        "FROM privado.seudonimo_paciente",
    )[0]

    assert filas["n"] == filas["d"] > 0


def test_embarazos_distintos_tienen_seudonimos_distintos(observador):
    filas = consultar(
        observador,
        "SELECT count(*) AS n, count(DISTINCT seudonimo) AS d "
        "FROM privado.seudonimo_embarazo",
    )[0]

    assert filas["n"] == filas["d"] > 0


def test_el_seudonimo_de_paciente_no_coincide_con_el_de_su_embarazo(observador):
    """Dos espacios de seudónimos, no uno reutilizado."""
    cruces = escalar(
        observador,
        "SELECT count(*) FROM privado.seudonimo_paciente sp "
        "JOIN privado.seudonimo_embarazo se ON se.seudonimo = sp.seudonimo",
    )

    assert cruces == 0


def test_el_mapa_cubre_todo_lo_publicado(observador):
    """Sin cobertura completa, la publicación perdería filas en silencio."""
    faltan_pacientes = escalar(
        observador,
        "SELECT count(*) FROM operacional.paciente p WHERE NOT EXISTS ("
        "SELECT 1 FROM privado.seudonimo_paciente s WHERE s.id_paciente = p.id_paciente)",
    )
    faltan_embarazos = escalar(
        observador,
        "SELECT count(*) FROM operacional.embarazo e WHERE NOT EXISTS ("
        "SELECT 1 FROM privado.seudonimo_embarazo s WHERE s.id_embarazo = e.id_embarazo)",
    )

    assert (faltan_pacientes, faltan_embarazos) == (0, 0)


# ---------------------------------------------------------------------------
# 4. Entitlements: el alcance del médico
# ---------------------------------------------------------------------------


def alcance_publicado(powerbi, upn: str) -> set[str]:
    """Los embarazos que el RLS del dataset le mostraría a esa identidad."""
    # ``str`` en los dos lados: psycopg devuelve ``uuid.UUID`` para una columna
    # uuid y ``str`` para una casteada a texto, y comparar los dos conjuntos sin
    # normalizar daria siempre distinto aunque los valores coincidieran.
    return {
        str(fila["seudonimo_embarazo"])
        for fila in consultar(
            powerbi,
            "SELECT seudonimo_embarazo FROM publicacion.v_entitlement_medico "
            "WHERE upn_medico = lower(:upn)",
            upn=upn,
        )
    }


def alcance_esperado(observador, upn: str) -> set[str]:
    """Lo mismo, calculado desde el operacional por el observador."""
    return {
        str(fila["seudonimo"])
        for fila in consultar(
            observador,
            """
            SELECT se.seudonimo::text AS seudonimo
            FROM operacional.seguimiento_clinico sc
            JOIN operacional.usuario_medico um ON um.id_medico = sc.id_medico
            JOIN operacional.usuario u ON u.id_usuario = um.id_usuario
            JOIN privado.seudonimo_embarazo se ON se.id_embarazo = sc.id_embarazo
            WHERE lower(u.email) = lower(:upn) AND u.activo
              AND sc.activo AND sc.fecha_asignacion <= CURRENT_DATE
              AND (sc.fecha_fin IS NULL OR sc.fecha_fin >= CURRENT_DATE)
            """,
            upn=upn,
        )
    }


@pytest.fixture(scope="session")
def medicos(observador):
    filas = consultar(
        observador,
        """
        SELECT lower(u.email) AS upn, um.id_medico
        FROM operacional.usuario_medico um
        JOIN operacional.usuario u ON u.id_usuario = um.id_usuario
        JOIN operacional.rol r ON r.id_rol = u.id_rol
        WHERE r.nombre_rol = 'MEDICO' AND u.activo
        ORDER BY um.id_medico
        """,
    )
    assert len(filas) >= 2, "hacen falta dos médicos para comparar alcances"
    return [dict(fila) for fila in filas]


def test_el_medico_a_recibe_exactamente_sus_embarazos_vigentes(
    powerbi, observador, medicos
):
    upn = medicos[0]["upn"]

    assert alcance_publicado(powerbi, upn) == alcance_esperado(observador, upn)
    assert alcance_publicado(powerbi, upn), "el dataset no le da ninguna asignación"


def test_el_medico_b_recibe_un_conjunto_distinto(powerbi, observador, medicos):
    a, b = medicos[0]["upn"], medicos[1]["upn"]

    alcance_a = alcance_publicado(powerbi, a)
    alcance_b = alcance_publicado(powerbi, b)

    assert alcance_b == alcance_esperado(observador, b)
    assert alcance_a != alcance_b


def test_una_identidad_sin_asignaciones_no_recibe_nada(powerbi):
    """Y una inexistente tampoco: el filtro no tiene rama por omisión."""
    assert alcance_publicado(powerbi, "nadie.simulado@example.com") == set()


@pytest.fixture
def asignacion_manipulable(observador, medicos):
    """Una asignación vigente del médico A, restaurada al terminar.

    Se toca una fila del dataset y se devuelve exactamente a su estado. Es la
    única forma de demostrar qué ocurre al desactivar, adelantar o cerrar una
    asignación sin inventar un dataset paralelo.
    """
    upn = medicos[0]["upn"]
    fila = consultar(
        observador,
        """
        SELECT sc.id_seguimiento, sc.activo, sc.fecha_asignacion, sc.fecha_fin,
               sc.rol_seguimiento, se.seudonimo::text AS seudonimo
        FROM operacional.seguimiento_clinico sc
        JOIN operacional.usuario_medico um ON um.id_medico = sc.id_medico
        JOIN operacional.usuario u ON u.id_usuario = um.id_usuario
        JOIN privado.seudonimo_embarazo se ON se.id_embarazo = sc.id_embarazo
        WHERE lower(u.email) = :upn AND sc.activo
          AND sc.fecha_asignacion <= CURRENT_DATE
          AND (sc.fecha_fin IS NULL OR sc.fecha_fin >= CURRENT_DATE)
        ORDER BY sc.id_seguimiento LIMIT 1
        """,
        upn=upn,
    )[0]
    original = dict(fila)

    def cambiar(**valores):
        asignaciones = ", ".join(f"{clave} = :{clave}" for clave in valores)
        with observador.connect() as conexion:
            conexion.execute(
                text(
                    f"UPDATE operacional.seguimiento_clinico SET {asignaciones} "
                    "WHERE id_seguimiento = :id"
                ),
                {**valores, "id": original["id_seguimiento"]},
            )

    yield {"upn": upn, "seudonimo": original["seudonimo"], "cambiar": cambiar}

    cambiar(
        activo=original["activo"],
        fecha_asignacion=original["fecha_asignacion"],
        fecha_fin=original["fecha_fin"],
        rol_seguimiento=original["rol_seguimiento"],
    )


def test_una_asignacion_inactiva_deja_de_conceder(powerbi, asignacion_manipulable):
    caso = asignacion_manipulable
    assert caso["seudonimo"] in alcance_publicado(powerbi, caso["upn"])

    caso["cambiar"](activo=False)

    assert caso["seudonimo"] not in alcance_publicado(powerbi, caso["upn"])


def test_una_asignacion_futura_todavia_no_concede(powerbi, asignacion_manipulable):
    caso = asignacion_manipulable

    caso["cambiar"](fecha_asignacion="9999-01-01", fecha_fin=None)

    assert caso["seudonimo"] not in alcance_publicado(powerbi, caso["upn"])


def test_una_asignacion_finalizada_deja_de_conceder(powerbi, asignacion_manipulable):
    caso = asignacion_manipulable

    caso["cambiar"](fecha_fin="2000-01-01", fecha_asignacion="1999-01-01")

    assert caso["seudonimo"] not in alcance_publicado(powerbi, caso["upn"])


@pytest.mark.parametrize("tipo", ["PRINCIPAL", "APOYO", "REEMPLAZO"])
def test_los_tres_tipos_de_seguimiento_conceden_lo_mismo(
    powerbi, asignacion_manipulable, tipo
):
    """El tipo no se filtra, y esto lo fija.

    Un filtro por ``PRINCIPAL`` habría dejado sin datos a quien cubre una baja,
    que es justamente cuando el acceso hace falta.
    """
    caso = asignacion_manipulable

    caso["cambiar"](rol_seguimiento=tipo)

    assert caso["seudonimo"] in alcance_publicado(powerbi, caso["upn"])


def test_pertenecer_a_la_clinica_no_concede_acceso(powerbi, observador, medicos):
    """``medico_clinica`` no aparece en la vista, y aquí se comprueba el efecto."""
    upn = medicos[0]["upn"]
    solo_por_clinica = consultar(
        observador,
        """
        SELECT se.seudonimo::text AS seudonimo
        FROM operacional.medico_clinica mc
        JOIN operacional.embarazo e ON e.id_clinica = mc.id_clinica
        JOIN privado.seudonimo_embarazo se ON se.id_embarazo = e.id_embarazo
        JOIN operacional.usuario_medico um ON um.id_medico = mc.id_medico
        JOIN operacional.usuario u ON u.id_usuario = um.id_usuario
        WHERE lower(u.email) = :upn
          AND NOT EXISTS (
              SELECT 1 FROM operacional.seguimiento_clinico sc
              WHERE sc.id_medico = mc.id_medico AND sc.id_embarazo = e.id_embarazo
                AND sc.activo AND sc.fecha_asignacion <= CURRENT_DATE
                AND (sc.fecha_fin IS NULL OR sc.fecha_fin >= CURRENT_DATE))
        LIMIT 5
        """,
        upn=upn,
    )

    assert solo_por_clinica, "el dataset no permite probar la afiliación por clínica"
    alcance = alcance_publicado(powerbi, upn)
    for fila in solo_por_clinica:
        assert fila["seudonimo"] not in alcance


def test_el_historial_completo_del_embarazo_asignado_esta_publicado(
    powerbi, observador, medicos
):
    """Una asignación es permiso para seguir el episodio, no un tramo de él."""
    upn = medicos[0]["upn"]
    seudonimo = sorted(alcance_publicado(powerbi, upn))[0]


    publicadas = escalar(
        powerbi,
        "SELECT count(*) FROM publicacion.v_lectura WHERE seudonimo_embarazo = :s",
        s=seudonimo,
    )
    internas = escalar(
        observador,
        """
        SELECT count(*) FROM analitico.fact_lectura_biometrica f
        JOIN privado.seudonimo_embarazo se ON se.id_embarazo = f.id_embarazo
        WHERE se.seudonimo::text = :s
        """,
        s=seudonimo,
    )

    assert publicadas == internas > 0


@pytest.fixture
def dos_episodios_de_una_paciente(observador, medicos):
    """Una paciente ficticia con **dos** embarazos, uno solo de ellos asignado.

    **Por que se fabrica.** La version anterior de la prueba buscaba en el
    dataset canonico una paciente con mas de un embarazo, y no hay ninguna: la
    consulta devolvia cero filas y el bucle de aserciones no se ejecutaba nunca.
    Pasaba sin comprobar nada. Depender de que el dataset traiga por accidente la
    forma que una propiedad necesita es lo que convierte una prueba en un adorno.

    **Por que confirma en vez de revertir.** El aislamiento se juzga desde la
    conexion de ``fetalalert_powerbi``, que es otra conexion: una transaccion sin
    confirmar del observador no seria visible ahi, y la prueba mediria el vacio.
    Asi que se confirma y se retira de forma explicita, en orden inverso de
    llaves foraneas. Es la misma decision -- y por la misma razon -- que ya toman
    las pruebas concurrentes de SCRUM-63 y los episodios simulados de la suite
    HTTP.

    **Los dos embarazos entran en la superficie publicada.** Se les emite
    seudonimo y se insertan tambien sus filas en ``analitico``, de modo que los
    dos aparecen en ``publicacion.v_embarazo``. Eso es lo que hace concluyente la
    prueba: el hermano no aparece para el medico **porque el filtro lo excluye**,
    no porque no estuviera publicado.

    Nada de esto toca una fila del dataset canonico ni del cluster persistente.
    """
    medico = medicos[0]
    hoy = "CURRENT_DATE"

    with observador.connect() as conexion:
        id_clinica = conexion.execute(
            text(
                "SELECT id_clinica FROM operacional.clinica ORDER BY id_clinica LIMIT 1"
            )
        ).scalar_one()

        id_paciente = conexion.execute(
            text(
                """
                INSERT INTO operacional.paciente
                    (cedula, primer_nombre, apellido_paterno, email_pac, fecha_nac)
                VALUES (:cedula, 'Perfil', 'Hermanos', :email, '1995-06-14')
                RETURNING id_paciente
                """
            ),
            {"cedula": CEDULA_DEL_ESCENARIO, "email": EMAIL_DEL_ESCENARIO},
        ).scalar_one()

        embarazos = []
        for indice, inicio in enumerate(("2022-03-07", "2024-05-13")):
            id_embarazo = conexion.execute(
                text(
                    """
                    INSERT INTO operacional.embarazo
                        (id_paciente, id_clinica, numero_gestas, numero_partos,
                         fecha_inicio, fecha_probable_parto, estado_embarazo)
                    VALUES (:p, :c, :g, :pa, CAST(:inicio AS date),
                            CAST(:inicio AS date) + 280, :estado)
                    RETURNING id_embarazo
                    """
                ),
                {
                    "p": id_paciente,
                    "c": id_clinica,
                    "g": indice + 2,
                    "pa": indice + 1,
                    "inicio": inicio,
                    "estado": "FINALIZADO" if indice == 0 else "ACTIVO",
                },
            ).scalar_one()
            embarazos.append(id_embarazo)

        # La asignacion vigente, sobre **uno solo** de los dos.
        conexion.execute(
            text(
                f"""
                INSERT INTO operacional.seguimiento_clinico
                    (id_embarazo, id_medico, fecha_asignacion, fecha_fin,
                     rol_seguimiento, activo)
                VALUES (:e, :m, {hoy} - 30, NULL, 'PRINCIPAL', true)
                """
            ),
            {"e": embarazos[0], "m": medico["id_medico"]},
        )

        # Seudonimos: la paciente y **los dos** embarazos.
        conexion.execute(
            text(
                "INSERT INTO privado.seudonimo_paciente (id_paciente) VALUES (:p)"
            ),
            {"p": id_paciente},
        )
        seudonimos = {}
        for id_embarazo in embarazos:
            seudonimos[id_embarazo] = str(
                conexion.execute(
                    text(
                        "INSERT INTO privado.seudonimo_embarazo (id_embarazo) "
                        "VALUES (:e) RETURNING seudonimo"
                    ),
                    {"e": id_embarazo},
                ).scalar_one()
            )

        # Y las filas analiticas, para que los dos entren en v_embarazo.
        conexion.execute(
            text(
                """
                INSERT INTO analitico.dim_paciente
                    (id_paciente, id_clinica, cedula, nombre_completo, fecha_nac)
                VALUES (:p, :c, :cedula, 'Perfil Hermanos', '1995-06-14')
                """
            ),
            {"p": id_paciente, "c": id_clinica, "cedula": CEDULA_DEL_ESCENARIO},
        )
        for indice, id_embarazo in enumerate(embarazos):
            conexion.execute(
                text(
                    """
                    INSERT INTO analitico.dim_embarazo
                        (id_embarazo, id_paciente, numero_gestas, numero_partos,
                         estado_embarazo, fecha_inicio, fecha_probable_parto,
                         duracion_est_semanas)
                    SELECT e.id_embarazo, e.id_paciente, e.numero_gestas,
                           e.numero_partos, e.estado_embarazo, e.fecha_inicio,
                           e.fecha_probable_parto, 40
                    FROM operacional.embarazo e WHERE e.id_embarazo = :e
                    """
                ),
                {"e": id_embarazo},
            )

    yield {
        "upn": medico["upn"],
        "id_paciente": id_paciente,
        "autorizado": seudonimos[embarazos[0]],
        "hermano": seudonimos[embarazos[1]],
        "embarazos": embarazos,
    }

    # Retirada dirigida, en orden inverso de llaves foraneas y por los
    # identificadores que este fixture creo. Ni un LIKE ni un borrado general.
    with observador.connect() as conexion:
        for sentencia in (
            "DELETE FROM analitico.dim_embarazo WHERE id_embarazo = ANY(:ids)",
            "DELETE FROM privado.seudonimo_embarazo WHERE id_embarazo = ANY(:ids)",
            "DELETE FROM operacional.seguimiento_clinico WHERE id_embarazo = ANY(:ids)",
            "DELETE FROM operacional.embarazo WHERE id_embarazo = ANY(:ids)",
        ):
            conexion.execute(text(sentencia), {"ids": embarazos})
        for sentencia in (
            "DELETE FROM analitico.dim_paciente WHERE id_paciente = :p",
            "DELETE FROM privado.seudonimo_paciente WHERE id_paciente = :p",
            "DELETE FROM operacional.paciente WHERE id_paciente = :p",
        ):
            conexion.execute(text(sentencia), {"p": id_paciente})


def test_el_escenario_publica_los_dos_embarazos_de_la_paciente(
    powerbi, dos_episodios_de_una_paciente
):
    """La guardia anti-vacuidad de la prueba siguiente.

    Si los dos episodios no estuvieran en la superficie, «el hermano no aparece»
    seria cierto por ausencia y no por filtrado, y la propiedad quedaria sin
    demostrar. Aqui se fija que los dos **si** estan publicados.
    """
    caso = dos_episodios_de_una_paciente
    publicados = {
        str(fila["seudonimo_embarazo"])
        for fila in consultar(
            powerbi,
            "SELECT seudonimo_embarazo FROM publicacion.v_embarazo "
            "WHERE seudonimo_embarazo = ANY(CAST(:ids AS uuid[]))",
            ids=[caso["autorizado"], caso["hermano"]],
        )
    }

    assert publicados == {caso["autorizado"], caso["hermano"]}


def test_los_dos_episodios_comparten_paciente_y_no_seudonimo(
    observador, dos_episodios_de_una_paciente
):
    """Que sean de la misma paciente es la premisa; conviene comprobarla."""
    caso = dos_episodios_de_una_paciente
    pacientes = {
        fila["id_paciente"]
        for fila in consultar(
            observador,
            "SELECT id_paciente FROM operacional.embarazo WHERE id_embarazo = ANY(:ids)",
            ids=caso["embarazos"],
        )
    }

    assert pacientes == {caso["id_paciente"]}
    assert caso["autorizado"] != caso["hermano"]


def test_no_se_publican_otros_embarazos_de_la_misma_paciente(
    powerbi, dos_episodios_de_una_paciente
):
    """El alcance es por episodio, no por persona.

    Escenario controlado: una paciente, dos embarazos suyos, y una asignacion
    vigente sobre **uno solo**. El medico recibe ese y no el otro, aunque los dos
    esten publicados y sean de la misma mujer.

    La version anterior de esta prueba buscaba el caso en el dataset canonico,
    que no lo tiene: la consulta devolvia cero filas y el bucle de aserciones no
    corria. Pasaba sin demostrar nada.
    """
    caso = dos_episodios_de_una_paciente
    alcance = alcance_publicado(powerbi, caso["upn"])

    assert caso["autorizado"] in alcance
    assert caso["hermano"] not in alcance


def test_el_hermano_no_esta_autorizado_para_nadie(
    powerbi, dos_episodios_de_una_paciente
):
    """Y no solo para ese medico: nadie tiene asignacion vigente sobre el.

    Sin esto, la prueba de arriba seria compatible con un modelo en el que el
    hermano quedara autorizado para otra identidad por algun camino lateral.
    """
    caso = dos_episodios_de_una_paciente
    autorizados = {
        str(fila["seudonimo_embarazo"])
        for fila in consultar(
            powerbi,
            "SELECT seudonimo_embarazo FROM publicacion.v_entitlement_medico",
        )
    }

    assert caso["autorizado"] in autorizados
    assert caso["hermano"] not in autorizados


def test_la_identidad_del_medico_solo_aparece_en_la_superficie_de_seguridad(powerbi):
    """El UPN es un identificador directo: vive donde aplica el RLS y en ningún
    otro sitio. Publicarlo como columna analítica lo pondría en un visual."""
    con_upn = {
        fila["table_name"]
        for fila in consultar(
            powerbi,
            "SELECT DISTINCT table_name FROM information_schema.columns "
            "WHERE table_schema = 'publicacion' "
            "AND (column_name LIKE '%upn%' OR column_name LIKE '%email%')",
        )
    }

    assert con_upn == {"v_entitlement_medico"}


# ---------------------------------------------------------------------------
# 5. La superficie administrativa
# ---------------------------------------------------------------------------


def test_la_superficie_administrativa_no_tiene_granularidad_individual(powerbi):
    columnas = {
        fila["column_name"]
        for fila in consultar(
            powerbi,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'publicacion' "
            "AND table_name = 'v_resumen_administrativo'",
        )
    }

    assert not any("seudonimo" in columna for columna in columnas)
    assert columnas == {
        "provincia",
        "mes",
        "codigo_semaforo",
        "lecturas",
        "embarazos",
        "hr_promedio",
        "spo2_promedio",
    }


def test_ninguna_celda_administrativa_baja_del_minimo(powerbi):
    """Un agregado sobre dos episodios es una trayectoria con otro nombre."""
    minimo = escalar(
        powerbi, "SELECT min(embarazos) FROM publicacion.v_resumen_administrativo"
    )

    assert minimo is None or minimo >= MINIMO_DE_CELDA


def test_la_superficie_administrativa_no_reconstruye_una_trayectoria(powerbi):
    """Sin seudónimo no hay a quién encadenar los meses."""
    filas = consultar(powerbi, "SELECT * FROM publicacion.v_resumen_administrativo")

    assert filas
    for fila in filas:
        texto = " ".join(str(valor) for valor in fila.values())
        assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", texto)


# ---------------------------------------------------------------------------
# 6. Conciliación con el ETL interno
# ---------------------------------------------------------------------------


def test_las_lecturas_publicadas_concilian_con_el_hecho(powerbi, observador):
    publicadas = escalar(powerbi, "SELECT count(*) FROM publicacion.v_lectura")
    internas = escalar(
        observador, "SELECT count(*) FROM analitico.fact_lectura_biometrica"
    )

    assert publicadas == internas > 0


def test_los_embarazos_publicados_concilian_con_la_dimension(powerbi, observador):
    publicados = escalar(powerbi, "SELECT count(*) FROM publicacion.v_embarazo")
    internos = escalar(observador, "SELECT count(*) FROM analitico.dim_embarazo")

    assert publicados == internos > 0


def test_las_sesiones_publicadas_concilian_con_el_operacional(powerbi, observador):
    """``secuencia_sesion`` numera sesiones, no lecturas: los distintos coinciden."""
    publicadas = escalar(
        powerbi,
        "SELECT count(*) FROM (SELECT DISTINCT seudonimo_embarazo, secuencia_sesion "
        "FROM publicacion.v_lectura) AS s",
    )
    internas = escalar(
        observador,
        "SELECT count(DISTINCT id_sesion) FROM analitico.fact_lectura_biometrica",
    )

    assert publicadas == internas > 0


# ---------------------------------------------------------------------------
# 7. Estabilidad exacta del mapa entre ejecuciones del ETL
# ---------------------------------------------------------------------------
#
# Que los seudonimos sean UUID v4 y que no sigan el orden de los identificadores
# dice como se **generan**, no que se **conserven**. Son dos propiedades
# distintas y la segunda es la que sostiene la longitudinalidad de todo lo
# publicado: si una ejecucion del ETL regenerara los UUID, cada embarazo
# apareceria como si fuera otro y ninguna serie cruzaria esa frontera.
#
# Asi que aqui se compara el mapa **fila a fila, columna a columna**, antes y
# despues de volver a ejecutar el ETL completo.

CONSULTA_DEL_MAPA = """
SELECT {clave} AS clave, seudonimo::text AS seudonimo, creado_en
FROM privado.{tabla}
ORDER BY {clave}
"""

TABLAS_DEL_MAPA = (
    ("seudonimo_paciente", "id_paciente"),
    ("seudonimo_embarazo", "id_embarazo"),
)


def capturar_el_mapa(observador) -> dict[str, list[tuple]]:
    """El contenido completo de las dos tablas, comparable por igualdad."""
    return {
        tabla: [
            (fila["clave"], fila["seudonimo"], fila["creado_en"])
            for fila in consultar(
                observador, CONSULTA_DEL_MAPA.format(tabla=tabla, clave=clave)
            )
        ]
        for tabla, clave in TABLAS_DEL_MAPA
    }


def test_el_mapa_no_esta_vacio_antes_de_comparar(observador):
    """La guardia anti-vacuidad: dos mapas vacios tambien son iguales."""
    antes = capturar_el_mapa(observador)

    for tabla, _ in TABLAS_DEL_MAPA:
        assert len(antes[tabla]) >= 10, (tabla, len(antes[tabla]))


def test_volver_a_ejecutar_el_etl_no_altera_una_sola_fila_del_mapa(observador, etl):
    """La propiedad central de la seudonimizacion: el mapa es estable.

    Se captura entero, se ejecuta el ETL de verdad -- el mismo
    ``ejecutar_etl`` que corre en produccion, con la credencial del ETL -- y se
    vuelve a capturar. Los dos contenidos tienen que ser identicos: mismas
    claves, mismos UUID y mismo ``creado_en``. Que coincida el instante de
    creacion es lo que descarta un borrado y reinsercion que hubiera dado la
    casualidad de repetir el UUID.
    """
    from app.etl.ejecucion import ejecutar_etl

    antes = capturar_el_mapa(observador)

    resultado = ejecutar_etl(etl)

    despues = capturar_el_mapa(observador)

    assert despues == antes
    # Y el propio ETL lo declara: cero seudonimos emitidos en esta ejecucion.
    assert resultado.seudonimos_emitidos == {
        "seudonimo_paciente": 0,
        "seudonimo_embarazo": 0,
    }


def test_los_seudonimos_publicados_sobreviven_a_la_ejecucion(powerbi, etl):
    """La estabilidad, vista desde donde importa: la superficie publicada.

    Comparar el mapa demuestra que las filas no cambiaron. Esto demuestra la
    consecuencia que el modelo de Power BI necesita: los seudonimos que el
    dataset ya importo siguen siendo los mismos tras una actualizacion, de modo
    que una serie longitudinal no se parte en dos.
    """
    from app.etl.ejecucion import ejecutar_etl

    def publicados() -> set[str]:
        return {
            str(fila["seudonimo_embarazo"])
            for fila in consultar(
                powerbi, "SELECT seudonimo_embarazo FROM publicacion.v_embarazo"
            )
        }

    antes = publicados()
    assert antes, "la superficie publicada esta vacia; ¿corrio el ETL?"

    ejecutar_etl(etl)

    assert publicados() == antes


def test_una_fila_nueva_recibe_un_seudonimo_y_las_viejas_no_cambian(
    observador, etl, dos_episodios_de_una_paciente
):
    """El otro lado de la estabilidad: emitir lo nuevo sin tocar lo anterior.

    El escenario controlado inserta una paciente y dos embarazos **con** sus
    seudonimos, asi que para probar la emision se retira uno a proposito y se
    deja que el ETL lo vuelva a emitir. Lo que se comprueba es que emite
    exactamente ese y que ninguna de las demas filas se mueve.
    """
    from app.etl.ejecucion import ejecutar_etl

    caso = dos_episodios_de_una_paciente
    huerfano = caso["embarazos"][1]

    with observador.connect() as conexion:
        conexion.execute(
            text("DELETE FROM privado.seudonimo_embarazo WHERE id_embarazo = :e"),
            {"e": huerfano},
        )

    antes = capturar_el_mapa(observador)
    resultado = ejecutar_etl(etl)
    despues = capturar_el_mapa(observador)

    assert resultado.seudonimos_emitidos["seudonimo_embarazo"] == 1
    assert resultado.seudonimos_emitidos["seudonimo_paciente"] == 0

    # Lo anterior, intacto: la diferencia es exactamente una fila nueva.
    nuevas = set(despues["seudonimo_embarazo"]) - set(antes["seudonimo_embarazo"])
    assert len(nuevas) == 1
    assert {fila[0] for fila in nuevas} == {huerfano}
    assert despues["seudonimo_paciente"] == antes["seudonimo_paciente"]

    # Y el UUID reemitido no es el que tenia antes: no se recalcula, se sortea.
    assert {fila[1] for fila in nuevas} != {caso["hermano"]}


# ---------------------------------------------------------------------------
# 8. Como genera PostgreSQL los seudonimos, leido del catalogo
# ---------------------------------------------------------------------------
#
# Las dos pruebas de la seccion anterior -- UUID version 4 y ausencia de
# correlacion con el orden de los identificadores -- describen los valores que
# hay hoy en la tabla. Son evidencia indirecta: un mapa poblado una sola vez por
# un script correcto las pasaria igual, y seguiria pasandolas despues de que
# alguien cambiara el DEFAULT por un hash.
#
# Lo que falta es la causa, y esta en el catalogo: **que expresion ejecuta
# PostgreSQL cuando nadie suministra el valor**. Se comprueba de las tres formas
# que se pueden distinguir entre si:
#
#   1. la expresion declarada, leida de ``pg_attrdef``;
#   2. lo que esa expresion **no** puede ser -- ni una funcion de hash, ni nada
#      que mencione la clave de la fila;
#   3. su efecto observable: dos inserciones sin valor producen dos UUID
#      distintos, que es lo que un DEFAULT determinista no haria.

COLUMNAS_DE_SEUDONIMO = (
    ("seudonimo_paciente", "id_paciente"),
    ("seudonimo_embarazo", "id_embarazo"),
)

# Nombres que delatarian una derivacion del identificador en vez de un sorteo.
# ``uuid_generate_v5`` y ``uuid_generate_v3`` son deterministas por definicion
# -- nombre y namespace --, asi que cuentan como derivacion aunque devuelvan un
# uuid bien formado.
FUNCIONES_DETERMINISTAS = (
    "md5",
    "sha1",
    "sha224",
    "sha256",
    "sha384",
    "sha512",
    "digest",
    "hmac",
    "encode",
    "crypt",
    "uuid_generate_v3",
    "uuid_generate_v5",
    "hashtext",
    "to_hex",
)


def expresion_por_omision(observador, tabla: str, columna: str) -> str | None:
    """La expresion del DEFAULT tal como PostgreSQL la guarda y la reconstruye."""
    return escalar(
        observador,
        """
        SELECT pg_get_expr(d.adbin, d.adrelid)
        FROM pg_attrdef d
        JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
        JOIN pg_class c ON c.oid = d.adrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'privado' AND c.relname = :tabla AND a.attname = :columna
        """,
        tabla=tabla,
        columna=columna,
    )


@pytest.mark.parametrize("tabla,clave", COLUMNAS_DE_SEUDONIMO)
def test_el_default_del_seudonimo_es_gen_random_uuid(observador, tabla, clave):
    """La expresion declarada, no el valor que quedo guardado.

    Si alguien sustituyera el DEFAULT por un hash del identificador, los UUID ya
    emitidos seguirian siendo v4 y las pruebas de la seccion anterior seguirian
    en verde. Esta no.
    """
    expresion = expresion_por_omision(observador, tabla, "seudonimo")

    assert expresion is not None, f"privado.{tabla}.seudonimo no tiene DEFAULT"
    assert "gen_random_uuid()" in expresion, expresion


@pytest.mark.parametrize("tabla,clave", COLUMNAS_DE_SEUDONIMO)
def test_el_default_no_deriva_del_identificador_de_la_fila(observador, tabla, clave):
    """Ni por hash ni nombrando la clave.

    Un seudonimo derivado de una clave secuencial se invierte probando los
    enteros -- treinta en el dataset simulado, unos miles en un despliegue --, y
    el mapa entero habria sido decorativo. Se comprueban las dos formas en que
    eso apareceria en la expresion: una funcion determinista, o una referencia a
    la propia columna clave.
    """
    expresion = (expresion_por_omision(observador, tabla, "seudonimo") or "").lower()

    assert clave not in expresion, expresion
    for funcion in FUNCIONES_DETERMINISTAS:
        assert funcion not in expresion, (funcion, expresion)


@pytest.mark.parametrize("tabla,clave", COLUMNAS_DE_SEUDONIMO)
def test_la_columna_de_seudonimo_es_uuid_no_nula_y_unica(observador, tabla, clave):
    """El tipo y las restricciones que hacen util al DEFAULT.

    Un ``text`` aceptaria cualquier cosa que un cliente decidiera enviar; sin
    ``NOT NULL`` una fila podria quedarse sin seudonimo y desaparecer de la
    publicacion en silencio; sin ``UNIQUE`` dos pacientes podrian compartirlo.
    """
    fila = consultar(
        observador,
        """
        SELECT a.atttypid::regtype::text AS tipo, a.attnotnull AS no_nula,
               EXISTS (
                   SELECT 1 FROM pg_constraint u
                   WHERE u.conrelid = a.attrelid AND u.contype = 'u'
                     AND u.conkey = ARRAY[a.attnum]
               ) AS unica
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'privado' AND c.relname = :tabla
          AND a.attname = 'seudonimo'
        """,
        tabla=tabla,
    )[0]

    assert fila["tipo"] == "uuid"
    assert fila["no_nula"] is True
    assert fila["unica"] is True


def test_el_default_sortea_un_valor_distinto_en_cada_insercion(observador, medicos):
    """El efecto observable del DEFAULT, que es lo que ningun catalogo prueba.

    Dos inserciones seguidas **sin suministrar el seudonimo**, sobre dos filas
    distintas, tienen que producir dos UUID distintos. Un DEFAULT determinista
    sobre la clave los produciria distintos tambien -- las claves difieren --,
    asi que la prueba fuerte es la otra mitad: se inserta **la misma fila dos
    veces**, retirandola en medio, y el valor no se repite. Un hash del
    identificador lo repetiria siempre.

    Todo ocurre en una transaccion que se revierte, asi que no deja rastro.
    """
    # ``READ COMMITTED`` explicito, y no la conexion tal como la da el fixture.
    #
    # El observador se construye con ``isolation_level="AUTOCOMMIT"`` porque su
    # trabajo habitual es preparar escenarios que otra conexion tiene que ver.
    # Ahi ``begin()`` no abre nada y ``rollback()`` no revierte nada: cada
    # sentencia se confirma sola. Esta prueba necesita lo contrario -- escribir y
    # no dejar rastro --, asi que pide un nivel de aislamiento real sobre la
    # misma credencial. Se comprobo reproduciendolo: con AUTOCOMMIT la fila de
    # paciente sobrevivia, y su cedula es UNIQUE, de modo que la segunda
    # ejecucion de la suite fallaba al insertarla.
    with observador.connect().execution_options(
        isolation_level="READ COMMITTED"
    ) as conexion:
        transaccion = conexion.begin()
        try:
            id_clinica = conexion.execute(
                text(
                    "SELECT id_clinica FROM operacional.clinica "
                    "ORDER BY id_clinica LIMIT 1"
                )
            ).scalar_one()
            id_paciente = conexion.execute(
                text(
                    """
                    INSERT INTO operacional.paciente
                        (cedula, primer_nombre, apellido_paterno, email_pac, fecha_nac)
                    VALUES ('SIM-PAC-DEFAULT', 'Sorteo', 'Simulado',
                            'sorteo.default@example.com', '1990-01-01')
                    RETURNING id_paciente
                    """
                )
            ).scalar_one()
            assert id_clinica is not None

            sorteados = []
            for _ in range(2):
                sorteados.append(
                    str(
                        conexion.execute(
                            text(
                                "INSERT INTO privado.seudonimo_paciente (id_paciente) "
                                "VALUES (:p) RETURNING seudonimo"
                            ),
                            {"p": id_paciente},
                        ).scalar_one()
                    )
                )
                conexion.execute(
                    text(
                        "DELETE FROM privado.seudonimo_paciente WHERE id_paciente = :p"
                    ),
                    {"p": id_paciente},
                )
        finally:
            transaccion.rollback()

    primero, segundo = sorteados
    assert primero != segundo, (
        "La misma fila recibio el mismo seudonimo dos veces: el DEFAULT es "
        "determinista y el mapa seria reversible."
    )
    assert uuid.UUID(primero).version == uuid.UUID(segundo).version == 4
