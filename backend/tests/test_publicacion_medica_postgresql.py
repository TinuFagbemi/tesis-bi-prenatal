"""Publicación clínica autorizada: PII por alcance, cardinalidad y aislamiento.

Se omite salvo que esté definida ``SCRUM99_PUB_TEST_DATABASE_URL``. Nunca cae
por omisión sobre otra variable.

**Qué demuestra esta suite y qué no.** SCRUM-98 ya demostró que la cuenta
técnica no alcanza nada fuera de lo publicado, y eso no se repite aquí.
SCRUM-99 publica nombre, cédula, teléfono y correo, y lo que hay que demostrar
es distinto: que ampliar la superficie con PII **no** abrió una vía lateral.
Es decir, que el universo de cada médico sigue saliendo de ``seguimiento_
clinico`` vigente y de nada más, y que la PII no viaja con él.

**Dónde se aplica el aislamiento, dicho una vez.** En modo Import el filtrado
por médico lo hace el RLS del *dataset* de Power BI, no PostgreSQL: la cuenta
técnica puede leer la superficie entera. Esta suite comprueba la propiedad que
el modelo semántico necesita para aislar correctamente -- que
``v_entitlement_medico`` y ``v_entitlement_paciente_medico`` contienen
exactamente las asignaciones vigentes y que la PII solo es alcanzable a través
de ellas --, no que la base filtre por sesión. Las consultas se escriben como
las escribiría el RLS del dataset: uniendo por el entitlement de ese UPN.

La base debe estar migrada al head, cargada con el dataset simulado y con el
ETL ejecutado al menos una vez.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

VARIABLE_DE_ENTORNO = "SCRUM99_PUB_TEST_DATABASE_URL"
ROL_ESPERADO = "fetalalert_powerbi"

# Los cuatro campos que SCRUM-99 aprueba para el médico. Se prueban uno a uno y
# no en bloque: «no se filtró PII» es una afirmación más débil que «no se filtró
# la cédula, ni el teléfono, ni el correo, ni el nombre».
CAMPOS_DE_PII = ("nombre_completo", "cedula", "telefono_pac", "email_pac")

# Las siete que SCRUM-99 anade. Se listan aqui para las pruebas de privilegios;
# el contrato de que son exactamente estas vive en la suite de SCRUM-98.
VISTAS_NUEVAS = (
    "v_entitlement_paciente_medico",
    "v_paciente_medico",
    "v_embarazo_medico",
    "v_embarazo_factor_riesgo",
    "v_tiempo_gestacional",
    "v_semaforo",
    "v_factor_riesgo",
)

# Marca del escenario que esta suite fabrica, distinta de la de SCRUM-98 para
# que las dos suites puedan limpiar lo suyo sin tocar lo ajeno. La de la cedula
# va aparte y es corta: la columna es VARCHAR(20) en el origen y la marca larga
# no cabe.
MARCA = "SCRUM99MED"
MARCA_CEDULA = "S99"

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
            "la superficie publicada con otra credencial no demuestra nada."
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
    """Prepara escenarios y lee el operacional. No juzga aislamiento."""
    administrativa = make_url(url).set(
        username=os.environ.get("SCRUM99_PUB_ADMIN_USER", "fetalalert_ci"),
        password=os.environ.get("SCRUM99_PUB_ADMIN_PASSWORD", ""),
    )
    motor = create_engine(
        administrativa, poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
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
# Las dos consultas que el RLS del dataset haría
# ---------------------------------------------------------------------------


def pacientes_de(powerbi, upn: str) -> set[str]:
    """Las pacientes que el modelo semántico mostraría a esa identidad."""
    return {
        str(fila["seudonimo_paciente"])
        for fila in consultar(
            powerbi,
            "SELECT seudonimo_paciente FROM publicacion.v_entitlement_paciente_medico "
            "WHERE upn_medico = lower(:upn)",
            upn=upn,
        )
    }


def embarazos_de(powerbi, upn: str) -> set[str]:
    return {
        str(fila["seudonimo_embarazo"])
        for fila in consultar(
            powerbi,
            "SELECT seudonimo_embarazo FROM publicacion.v_entitlement_medico "
            "WHERE upn_medico = lower(:upn)",
            upn=upn,
        )
    }


def pii_visible_para(powerbi, upn: str, campo: str) -> set[str]:
    """El campo de PII tal como le llegaría a ese médico a través del puente."""
    return {
        fila["valor"]
        for fila in consultar(
            powerbi,
            f"""
            SELECT p.{campo} AS valor
            FROM publicacion.v_paciente_medico p
            JOIN publicacion.v_entitlement_paciente_medico ep
              ON ep.seudonimo_paciente = p.seudonimo_paciente
            WHERE ep.upn_medico = lower(:upn)
            """,
            upn=upn,
        )
        if fila["valor"] is not None
    }


@pytest.fixture(scope="session")
def medicos(observador):
    """Los médicos con asignaciones vigentes, de más a menos, para comparar."""
    filas = consultar(
        observador,
        """
        SELECT lower(u.email) AS upn, um.id_medico,
               count(*) AS vigentes
        FROM operacional.usuario_medico um
        JOIN operacional.usuario u ON u.id_usuario = um.id_usuario
        JOIN operacional.rol r ON r.id_rol = u.id_rol
        JOIN operacional.seguimiento_clinico sc ON sc.id_medico = um.id_medico
        WHERE r.nombre_rol = 'MEDICO' AND u.activo
          AND sc.activo
          AND sc.fecha_asignacion
              <= (CURRENT_TIMESTAMP AT TIME ZONE 'America/Panama')::date
          AND (sc.fecha_fin IS NULL OR sc.fecha_fin
              >= (CURRENT_TIMESTAMP AT TIME ZONE 'America/Panama')::date)
        GROUP BY u.email, um.id_medico
        ORDER BY um.id_medico
        """,
    )
    if len(filas) < 2:
        pytest.fail(
            "hacen falta dos médicos con seguimiento vigente para comparar "
            "alcances; el dataset cargado no los tiene"
        )
    return [dict(fila) for fila in filas]


# ---------------------------------------------------------------------------
# 1. El entitlement de paciente deriva del de embarazo
# ---------------------------------------------------------------------------


def test_el_entitlement_de_paciente_no_repite_la_pareja(powerbi):
    """Grano ``(upn, paciente)``.

    Una paciente con dos embarazos autorizados por el mismo médico daría dos
    filas sin el DISTINCT, y entonces la tabla dejaría de poder usarse como
    puente: el lado *muchos* puede repetir, pero no la pareja entera.
    """
    repetidas = escalar(
        powerbi,
        "SELECT count(*) FROM (SELECT upn_medico, seudonimo_paciente "
        "FROM publicacion.v_entitlement_paciente_medico "
        "GROUP BY 1, 2 HAVING count(*) > 1) AS r",
    )

    assert repetidas == 0


def test_toda_paciente_autorizada_tiene_un_embarazo_autorizado(powerbi):
    """La propiedad «si y solo si», en su dirección más fácil de romper.

    Si el entitlement de paciente concediera por algo distinto de un embarazo
    vigente --por clínica, por ejemplo-- aparecería aquí una pareja sin
    respaldo.
    """
    sin_respaldo = escalar(
        powerbi,
        """
        SELECT count(*)
        FROM publicacion.v_entitlement_paciente_medico ep
        WHERE NOT EXISTS (
            SELECT 1
            FROM publicacion.v_entitlement_medico em
            JOIN publicacion.v_embarazo_medico e
              ON e.seudonimo_embarazo = em.seudonimo_embarazo
            WHERE em.upn_medico = ep.upn_medico
              AND e.seudonimo_paciente = ep.seudonimo_paciente
        )
        """,
    )

    assert sin_respaldo == 0


def test_todo_embarazo_autorizado_trae_a_su_paciente(powerbi):
    """Y la dirección contraria: el médico no pierde a la paciente de un episodio.

    Sin esto el tablero podría mostrar un embarazo autorizado sin poder decir de
    quién es, que es exactamente el problema que SCRUM-99 existe para resolver.
    """
    huerfanos = escalar(
        powerbi,
        """
        SELECT count(*)
        FROM publicacion.v_entitlement_medico em
        JOIN publicacion.v_embarazo_medico e
          ON e.seudonimo_embarazo = em.seudonimo_embarazo
        WHERE NOT EXISTS (
            SELECT 1 FROM publicacion.v_entitlement_paciente_medico ep
            WHERE ep.upn_medico = em.upn_medico
              AND ep.seudonimo_paciente = e.seudonimo_paciente
        )
        """,
    )

    assert huerfanos == 0


def test_el_medico_a_recibe_exactamente_las_pacientes_de_sus_embarazos(
    powerbi, observador, medicos
):
    upn = medicos[0]["upn"]

    esperado = {
        str(fila["seudonimo"])
        for fila in consultar(
            observador,
            """
            SELECT DISTINCT sp.seudonimo::text AS seudonimo
            FROM operacional.seguimiento_clinico sc
            JOIN operacional.embarazo e ON e.id_embarazo = sc.id_embarazo
            JOIN operacional.usuario_medico um ON um.id_medico = sc.id_medico
            JOIN operacional.usuario u ON u.id_usuario = um.id_usuario
            JOIN privado.seudonimo_paciente sp ON sp.id_paciente = e.id_paciente
            WHERE lower(u.email) = lower(:upn) AND u.activo AND sc.activo
              AND sc.fecha_asignacion
                  <= (CURRENT_TIMESTAMP AT TIME ZONE 'America/Panama')::date
              AND (sc.fecha_fin IS NULL OR sc.fecha_fin
                  >= (CURRENT_TIMESTAMP AT TIME ZONE 'America/Panama')::date)
            """,
            upn=upn,
        )
    }

    assert esperado, "el escenario quedaría vacío y no probaría nada"
    assert pacientes_de(powerbi, upn) == esperado


def test_el_medico_b_recibe_un_conjunto_distinto(powerbi, medicos):
    a, b = pacientes_de(powerbi, medicos[0]["upn"]), pacientes_de(
        powerbi, medicos[-1]["upn"]
    )

    assert a and b
    assert a != b


def test_una_identidad_sin_asignaciones_no_recibe_ninguna_paciente(powerbi):
    assert pacientes_de(powerbi, f"nadie.{MARCA.lower()}@example.com") == set()


# ---------------------------------------------------------------------------
# 2. La PII no se escapa de lado
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("campo", CAMPOS_DE_PII)
def test_el_medico_recibe_la_pii_de_sus_pacientes(powerbi, medicos, campo):
    """Guardia anti-vacuidad de todo el bloque siguiente.

    Si el médico no recibiera ninguna PII, «no recibe la de otro» sería cierto
    por ausencia y no probaría nada.
    """
    valores = pii_visible_para(powerbi, medicos[0]["upn"], campo)

    assert valores, f"el médico no recibe ningún {campo} y el escenario es vacío"


@pytest.mark.parametrize("campo", CAMPOS_DE_PII)
def test_el_medico_a_no_alcanza_la_pii_exclusiva_del_medico_b(
    powerbi, medicos, campo
):
    """El requisito central de SCRUM-99.

    Se compara contra lo **exclusivo** de B, no contra todo lo de B: dos médicos
    pueden compartir una paciente legítimamente, y exigir conjuntos disjuntos
    haría fallar la prueba por una razón que no es un fallo.
    """
    upn_a, upn_b = medicos[0]["upn"], medicos[-1]["upn"]

    solo_de_b = pacientes_de(powerbi, upn_b) - pacientes_de(powerbi, upn_a)
    assert solo_de_b, "B no tiene ninguna paciente exclusiva; el escenario es vacío"

    pii_exclusiva_de_b = {
        fila["valor"]
        for fila in consultar(
            powerbi,
            f"SELECT {campo} AS valor FROM publicacion.v_paciente_medico "
            "WHERE seudonimo_paciente = ANY(CAST(:s AS uuid[]))",
            s=sorted(solo_de_b),
        )
        if fila["valor"] is not None
    }
    assert pii_exclusiva_de_b, f"las pacientes exclusivas de B no tienen {campo}"

    assert not (pii_visible_para(powerbi, upn_a, campo) & pii_exclusiva_de_b)


def test_el_medico_a_no_alcanza_los_embarazos_exclusivos_de_b(powerbi, medicos):
    upn_a, upn_b = medicos[0]["upn"], medicos[-1]["upn"]

    solo_de_b = embarazos_de(powerbi, upn_b) - embarazos_de(powerbi, upn_a)
    assert solo_de_b

    assert not (embarazos_de(powerbi, upn_a) & solo_de_b)


def test_el_medico_a_no_alcanza_las_lecturas_de_los_embarazos_de_b(
    powerbi, medicos
):
    upn_a, upn_b = medicos[0]["upn"], medicos[-1]["upn"]
    solo_de_b = embarazos_de(powerbi, upn_b) - embarazos_de(powerbi, upn_a)
    assert solo_de_b

    alcanzables = escalar(
        powerbi,
        """
        SELECT count(*)
        FROM publicacion.v_lectura l
        JOIN publicacion.v_entitlement_medico em
          ON em.seudonimo_embarazo = l.seudonimo_embarazo
        WHERE em.upn_medico = lower(:upn)
          AND l.seudonimo_embarazo = ANY(CAST(:s AS uuid[]))
        """,
        upn=upn_a,
        s=sorted(solo_de_b),
    )

    assert alcanzables == 0


def test_el_medico_a_no_alcanza_los_factores_de_riesgo_de_b(powerbi, medicos):
    upn_a, upn_b = medicos[0]["upn"], medicos[-1]["upn"]
    solo_de_b = embarazos_de(powerbi, upn_b) - embarazos_de(powerbi, upn_a)
    assert solo_de_b

    alcanzables = escalar(
        powerbi,
        """
        SELECT count(*)
        FROM publicacion.v_embarazo_factor_riesgo f
        JOIN publicacion.v_entitlement_medico em
          ON em.seudonimo_embarazo = f.seudonimo_embarazo
        WHERE em.upn_medico = lower(:upn)
          AND f.seudonimo_embarazo = ANY(CAST(:s AS uuid[]))
        """,
        upn=upn_a,
        s=sorted(solo_de_b),
    )

    assert alcanzables == 0


# ---------------------------------------------------------------------------
# 3. La vigencia, y la clínica que no concede
# ---------------------------------------------------------------------------


@pytest.fixture
def asignacion_manipulable(observador, medicos):
    """Un seguimiento vigente que la prueba puede alterar y que se restaura.

    Se toma uno real del dataset, se guarda su estado y se devuelve al final,
    campo por campo. No se crea ni se borra nada.
    """
    upn = medicos[0]["upn"]
    fila = consultar(
        observador,
        """
        SELECT sc.id_seguimiento, sc.activo, sc.fecha_asignacion, sc.fecha_fin,
               sp.seudonimo::text AS seudonimo_paciente
        FROM operacional.seguimiento_clinico sc
        JOIN operacional.embarazo e ON e.id_embarazo = sc.id_embarazo
        JOIN operacional.usuario_medico um ON um.id_medico = sc.id_medico
        JOIN operacional.usuario u ON u.id_usuario = um.id_usuario
        JOIN privado.seudonimo_paciente sp ON sp.id_paciente = e.id_paciente
        WHERE lower(u.email) = lower(:upn) AND sc.activo
          AND sc.fecha_asignacion
              <= (CURRENT_TIMESTAMP AT TIME ZONE 'America/Panama')::date
          AND (sc.fecha_fin IS NULL OR sc.fecha_fin
              >= (CURRENT_TIMESTAMP AT TIME ZONE 'America/Panama')::date)
        ORDER BY sc.id_seguimiento
        LIMIT 1
        """,
        upn=upn,
    )
    assert fila, "no hay ninguna asignación vigente que manipular"
    original = dict(fila[0])

    def aplicar(**cambios):
        asignaciones = ", ".join(f"{clave} = :{clave}" for clave in cambios)
        with observador.connect() as conexion:
            conexion.execute(
                text(
                    f"UPDATE operacional.seguimiento_clinico SET {asignaciones} "
                    "WHERE id_seguimiento = :id"
                ),
                {**cambios, "id": original["id_seguimiento"]},
            )

    yield {"upn": upn, "original": original, "aplicar": aplicar}

    aplicar(
        activo=original["activo"],
        fecha_asignacion=original["fecha_asignacion"],
        fecha_fin=original["fecha_fin"],
    )


def _sigue_autorizada(powerbi, upn, seudonimo) -> bool:
    return seudonimo in pacientes_de(powerbi, upn)


def test_una_asignacion_inactiva_deja_de_conceder_la_paciente(
    powerbi, asignacion_manipulable
):
    caso = asignacion_manipulable
    seudonimo = caso["original"]["seudonimo_paciente"]
    assert _sigue_autorizada(powerbi, caso["upn"], seudonimo)

    caso["aplicar"](activo=False)

    assert not _sigue_autorizada(powerbi, caso["upn"], seudonimo)


def test_una_asignacion_futura_todavia_no_concede_la_paciente(
    powerbi, asignacion_manipulable, observador
):
    caso = asignacion_manipulable
    seudonimo = caso["original"]["seudonimo_paciente"]
    manana = escalar(
        observador,
        "SELECT (CURRENT_TIMESTAMP AT TIME ZONE 'America/Panama')::date + 1",
    )

    caso["aplicar"](fecha_asignacion=manana, fecha_fin=None)

    assert not _sigue_autorizada(powerbi, caso["upn"], seudonimo)


def test_una_asignacion_finalizada_deja_de_conceder_la_paciente(
    powerbi, asignacion_manipulable, observador
):
    caso = asignacion_manipulable
    seudonimo = caso["original"]["seudonimo_paciente"]
    ayer = escalar(
        observador,
        "SELECT (CURRENT_TIMESTAMP AT TIME ZONE 'America/Panama')::date - 1",
    )

    caso["aplicar"](fecha_fin=ayer)

    assert not _sigue_autorizada(powerbi, caso["upn"], seudonimo)


def embarazos_de_su_clinica_sin_seguimiento(observador, upn: str) -> set[str]:
    """Episodios de una clínica a la que está afiliado y que no sigue."""
    return {
        str(fila["seudonimo"])
        for fila in consultar(
            observador,
            """
            SELECT DISTINCT se.seudonimo::text AS seudonimo
            FROM operacional.usuario u
            JOIN operacional.usuario_medico um ON um.id_usuario = u.id_usuario
            JOIN operacional.medico_clinica mc ON mc.id_medico = um.id_medico
            JOIN operacional.embarazo e ON e.id_clinica = mc.id_clinica
            JOIN privado.seudonimo_embarazo se ON se.id_embarazo = e.id_embarazo
            WHERE lower(u.email) = lower(:upn)
              AND NOT EXISTS (
                  SELECT 1 FROM operacional.seguimiento_clinico sc
                  WHERE sc.id_embarazo = e.id_embarazo
                    AND sc.id_medico = um.id_medico
                    AND sc.activo
                    AND sc.fecha_asignacion
                        <= (CURRENT_TIMESTAMP AT TIME ZONE 'America/Panama')::date
                    AND (sc.fecha_fin IS NULL OR sc.fecha_fin
                        >= (CURRENT_TIMESTAMP AT TIME ZONE 'America/Panama')::date)
              )
            """,
            upn=upn,
        )
    }


def test_compartir_clinica_no_concede_ningun_embarazo(powerbi, observador, medicos):
    """La afiliación no es autorización, medido sobre el episodio.

    **Por qué el episodio y no la paciente.** La primera versión de esta prueba
    comparaba pacientes, y eso es incorrecto: una paciente puede tener un
    embarazo que el médico sí sigue y otro, en la misma clínica, que no. Entonces
    la paciente aparece legítimamente en su universo --tiene que poder
    identificarla para el episodio que atiende-- y una comparación por paciente
    la contaría como fuga cuando no lo es. Lo que no puede pasar, y es lo que se
    mide aquí, es que el **episodio** no seguido entre en su alcance.

    La prueba de multiembarazo de más abajo cubre el otro lado: que de ese
    episodio hermano no salga ningún dato clínico.
    """
    upn = medicos[0]["upn"]
    ajenos = embarazos_de_su_clinica_sin_seguimiento(observador, upn)

    if not ajenos:
        pytest.skip(
            "el dataset no tiene ningún embarazo de la clínica del médico fuera "
            "de su seguimiento; la propiedad no es observable aquí"
        )

    assert not (embarazos_de(powerbi, upn) & ajenos)


def test_compartir_clinica_no_publica_datos_clinicos_del_episodio_ajeno(
    powerbi, observador, medicos
):
    """Y de esos episodios no sale nada: ni la fila, ni lecturas, ni factores."""
    upn = medicos[0]["upn"]
    ajenos = embarazos_de_su_clinica_sin_seguimiento(observador, upn)

    if not ajenos:
        pytest.skip("no hay episodios ajenos en su clínica que comprobar")

    for vista in (
        "v_embarazo_medico",
        "v_lectura",
        "v_embarazo_factor_riesgo",
    ):
        alcanzables = escalar(
            powerbi,
            f"""
            SELECT count(*)
            FROM publicacion.{vista} x
            JOIN publicacion.v_entitlement_medico em
              ON em.seudonimo_embarazo = x.seudonimo_embarazo
            WHERE em.upn_medico = lower(:upn)
              AND x.seudonimo_embarazo = ANY(CAST(:s AS uuid[]))
            """,
            upn=upn,
            s=sorted(ajenos),
        )
        assert alcanzables == 0, vista


# ---------------------------------------------------------------------------
# 4. Cardinalidad: ninguna vista nueva duplica nada
# ---------------------------------------------------------------------------


def test_la_dimension_de_paciente_tiene_una_fila_por_paciente(powerbi):
    """De esto depende que Power BI pueda ponerla en el lado *uno*.

    Es la razón entera de que el entitlement de paciente sea una tabla aparte:
    si ``upn_medico`` viviera en esta vista, una paciente seguida por dos
    médicos daría dos filas y la clave dejaría de serlo.
    """
    total, distintos = consultar(
        powerbi,
        "SELECT count(*) AS total, count(DISTINCT seudonimo_paciente) AS distintos "
        "FROM publicacion.v_paciente_medico",
    )[0].values()

    assert total > 0
    assert total == distintos


def test_la_dimension_de_embarazo_tiene_una_fila_por_episodio(powerbi):
    """``EXISTS`` y no ``JOIN`` contra el entitlement.

    Un embarazo con seguimiento PRINCIPAL, de APOYO y de REEMPLAZO a la vez
    saldría tres veces con un JOIN. Es el caso que el fixture de tres médicos
    fuerza más abajo; esto lo comprueba sobre el dataset entero.
    """
    total, distintos = consultar(
        powerbi,
        "SELECT count(*) AS total, count(DISTINCT seudonimo_embarazo) AS distintos "
        "FROM publicacion.v_embarazo_medico",
    )[0].values()

    assert total > 0
    assert total == distintos


def test_el_puente_de_factores_no_repite_la_pareja(powerbi):
    repetidas = escalar(
        powerbi,
        "SELECT count(*) FROM (SELECT seudonimo_embarazo, clave_factor "
        "FROM publicacion.v_embarazo_factor_riesgo "
        "GROUP BY 1, 2 HAVING count(*) > 1) AS r",
    )

    assert repetidas == 0


def test_el_hecho_no_cambio_de_grano(powerbi, observador):
    """``v_lectura`` se reutiliza sin tocarla: una fila por lectura, 1180.

    Si alguna vista nueva hubiera acabado uniéndose al hecho, o si el hecho se
    hubiera replicado por médico, este número dejaría de coincidir con el del
    modelo estrella.
    """
    publicadas = escalar(powerbi, "SELECT count(*) FROM publicacion.v_lectura")
    hechos = escalar(
        observador, "SELECT count(*) FROM analitico.fact_lectura_biometrica"
    )

    assert publicadas == hechos


def test_una_paciente_con_dos_telefonos_no_se_duplica(powerbi, observador):
    """El teléfono es escalar en la dimensión, y el 1:N del origen no la alcanza."""
    con_varios = consultar(
        observador,
        """
        SELECT sp.seudonimo::text AS seudonimo
        FROM operacional.telefono_paciente t
        JOIN privado.seudonimo_paciente sp ON sp.id_paciente = t.id_paciente
        GROUP BY sp.seudonimo
        HAVING count(*) > 1
        """,
    )
    if not con_varios:
        pytest.skip("ninguna paciente del dataset tiene más de un teléfono")

    seudonimos = [fila["seudonimo"] for fila in con_varios]
    filas = escalar(
        powerbi,
        "SELECT count(*) FROM publicacion.v_paciente_medico "
        "WHERE seudonimo_paciente = ANY(CAST(:s AS uuid[]))",
        s=seudonimos,
    )
    publicadas = escalar(
        powerbi,
        "SELECT count(DISTINCT seudonimo_paciente) FROM publicacion.v_paciente_medico "
        "WHERE seudonimo_paciente = ANY(CAST(:s AS uuid[]))",
        s=seudonimos,
    )

    assert filas == publicadas


# ---------------------------------------------------------------------------
# 5. Lo que el ETL trajo: correo y clínica del episodio
# ---------------------------------------------------------------------------


def test_el_correo_publicado_es_el_de_contacto_de_la_paciente(powerbi, observador):
    """El valor publicado es exactamente ``operacional.paciente.email_pac``.

    **Por qué esta prueba no compara contra ``usuario.email``.** El contrato es
    que el correo publicado sea el de contacto clínico y no la credencial de la
    cuenta, y lo natural sería comprobarlo exigiendo que no coincidan. No se
    puede: en el conjunto canónico las 30 pacientes con cuenta tienen los dos
    valores **idénticos**, de modo que una comparación por valor no distingue una
    columna de la otra y pasaría o fallaría por una coincidencia del dataset, no
    por el comportamiento del ETL.

    Lo que sí es observable es esto: el valor publicado coincide con la columna
    de contacto para **todas** las pacientes. Que sea esa columna y no la otra la
    que el ETL lee lo fija la prueba offline
    ``test_la_extraccion_toma_el_correo_de_contacto_y_no_la_credencial``, que
    inspecciona la consulta y no depende de qué valores traiga el dataset.
    """
    discrepancias = escalar(
        observador,
        """
        SELECT count(*)
        FROM publicacion.v_paciente_medico v
        JOIN privado.seudonimo_paciente sp ON sp.seudonimo = v.seudonimo_paciente
        JOIN operacional.paciente p ON p.id_paciente = sp.id_paciente
        WHERE v.email_pac IS DISTINCT FROM p.email_pac
        """,
    )

    assert discrepancias == 0


def test_ninguna_paciente_publicada_se_queda_sin_correo(powerbi):
    """La columna es NOT NULL en la dimensión y tiene que llegar completa."""
    sin_correo = escalar(
        powerbi,
        "SELECT count(*) FROM publicacion.v_paciente_medico WHERE email_pac IS NULL",
    )

    assert sin_correo == 0


def test_la_clinica_publicada_es_la_del_episodio(powerbi, observador):
    """Y no la de la paciente, que es contexto y puede ser NULL.

    ``dim_paciente.id_clinica`` se deriva de *todos* sus embarazos y se anula
    cuando son de clínicas distintas. La del episodio sale de
    ``operacional.embarazo.id_clinica``, que es escalar y obligatorio.
    """
    discrepancias = escalar(
        observador,
        """
        SELECT count(*)
        FROM publicacion.v_embarazo_medico v
        JOIN privado.seudonimo_embarazo se
          ON se.seudonimo = v.seudonimo_embarazo
        JOIN operacional.embarazo e ON e.id_embarazo = se.id_embarazo
        JOIN operacional.clinica c ON c.id_clinica = e.id_clinica
        WHERE v.nombre_clinica IS DISTINCT FROM c.nombre_clinica
           OR v.provincia IS DISTINCT FROM c.provincia
           OR v.distrito IS DISTINCT FROM c.distrito
        """,
    )

    assert discrepancias == 0


def test_ningun_episodio_publicado_se_queda_sin_clinica(powerbi):
    sin_clinica = escalar(
        powerbi,
        "SELECT count(*) FROM publicacion.v_embarazo_medico "
        "WHERE nombre_clinica IS NULL",
    )

    assert sin_clinica == 0


# ---------------------------------------------------------------------------
# 6. Catálogos
# ---------------------------------------------------------------------------


def test_el_catalogo_de_semaforo_conserva_los_tres_niveles(powerbi):
    """Esta revisión publica datos; no redefine reglas clínicas."""
    filas = consultar(
        powerbi,
        "SELECT codigo_nivel, prioridad FROM publicacion.v_semaforo "
        "ORDER BY prioridad",
    )

    assert [fila["codigo_nivel"] for fila in filas] == ["OK", "WARNING", "ERROR"]
    assert [fila["prioridad"] for fila in filas] == [1, 2, 3]


def test_el_catalogo_gestacional_no_recalcula_nada(powerbi, observador):
    publicado = {
        (fila["semana_gestacion"], fila["mes_gestacion"], fila["trimestre"])
        for fila in consultar(powerbi, "SELECT * FROM publicacion.v_tiempo_gestacional")
    }
    del_etl = {
        (fila["semana_gestacion"], fila["mes_gestacion"], fila["trimestre"])
        for fila in consultar(
            observador,
            "SELECT semana_gestacion, mes_gestacion, trimestre "
            "FROM analitico.dim_tiempo_gestacional",
        )
    }

    assert publicado == del_etl


# ---------------------------------------------------------------------------
# 7. La frontera de privilegios que SCRUM-99 tocó
# ---------------------------------------------------------------------------
#
# La revisión necesita que el migrador --dueño de las siete vistas nuevas--
# pueda leer ``publicacion.v_entitlement_medico``, que pertenece a
# ``fetalalert_rls_owner`` desde SCRUM-98. Para concedérselo le presta a ese rol
# ``USAGE`` sobre el schema, asume el rol y lo retira en la misma transacción.
#
# Un préstamo que no se devuelve deja de ser un préstamo, así que lo que se
# comprueba aquí no es que la concesión funcione --eso ya lo demuestra que las
# vistas devuelvan filas-- sino que **no quedó nada de más** cuando terminó.


def es_verdadero(observador, expresion: str) -> bool:
    return escalar(observador, f"SELECT {expresion}")


def test_el_dueno_de_las_politicas_no_conserva_usage_sobre_publicacion(observador):
    """El préstamo se devolvió.

    ``fetalalert_rls_owner`` recibe ``USAGE`` sobre ``publicacion`` durante la
    migración, porque sin él no puede conceder nada sobre un objeto suyo alojado
    ahí. Se retira dentro de la misma transacción, y aquí se fija que así fue:
    si alguien quitara el ``REVOKE``, el rol quedaría con visibilidad permanente
    sobre el schema publicado y esta prueba lo diría.
    """
    assert not es_verdadero(
        observador,
        "has_schema_privilege('fetalalert_rls_owner', 'publicacion', 'USAGE')",
    )


def test_el_dueno_de_las_politicas_sigue_sin_alcanzar_el_mapa_de_pacientes(
    observador,
):
    """La frontera que SCRUM-98 defiende, intacta después de SCRUM-99.

    ``privado.seudonimo_paciente`` es el mapa que ata un seudónimo a una
    persona, y ``fetalalert_rls_owner`` no lo alcanza a propósito: solo tiene
    ``seudonimo_embarazo``, que su vista de entitlement necesita en cada
    consulta.

    Es la razón entera de que ``v_entitlement_paciente_medico`` se derive de la
    vista de SCRUM-98 en vez de reconstruir su lógica desde ``operacional``.
    Aquella otra forma habría obligado a concederle este mapa, y esta prueba es
    la que impide que alguien la tome más adelante «para simplificar».
    """
    assert not es_verdadero(
        observador,
        "has_table_privilege('fetalalert_rls_owner', "
        "'privado.seudonimo_paciente', 'SELECT')",
    )
    assert es_verdadero(
        observador,
        "has_table_privilege('fetalalert_rls_owner', "
        "'privado.seudonimo_embarazo', 'SELECT')",
    )


@pytest.fixture(scope="session")
def migrador(observador) -> str:
    """El rol que ejecutó Alembic, deducido de quién posee las vistas nuevas.

    No se lee de una variable de entorno a propósito: lo que importa es quién es
    el dueño efectivo en la base, que es el que ejerce los privilegios cuando
    Power BI consulta una vista.
    """
    return escalar(
        observador,
        "SELECT pg_get_userbyid(c.relowner) FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'publicacion' AND c.relname = 'v_paciente_medico'",
    )


def test_el_migrador_puede_leer_la_vista_de_entitlement(observador, migrador):
    """Sin esto las siete vistas nuevas devolverían cero filas.

    Una vista se ejecuta con los privilegios de su propietario, y el propietario
    de las nuevas dejó de tener acceso implícito a la de entitlement cuando
    SCRUM-98 la transfirió.
    """
    assert es_verdadero(
        observador,
        f"has_table_privilege('{migrador}', "
        "'publicacion.v_entitlement_medico', 'SELECT')",
    )


def test_el_migrador_no_recibio_ningun_privilegio_de_mas(observador, migrador):
    """Mínimo estricto: una concesión, sobre un objeto, y ninguna otra.

    Se mira la ACL de cada objeto de ``publicacion`` y se descartan los que el
    migrador ya posee --sobre lo propio no hace falta concesión--. Lo que queda
    tiene que ser exactamente la vista de entitlement. Si alguien resolviera
    esto con un ``GRANT SELECT ON ALL TABLES IN SCHEMA publicacion``, aquí
    aparecerían diez objetos más.
    """
    con_concesion_explicita = {
        fila["objeto"]
        for fila in consultar(
            observador,
            """
            SELECT c.relname AS objeto
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'publicacion'
              AND pg_get_userbyid(c.relowner) <> :migrador
              AND has_table_privilege(:migrador, c.oid, 'SELECT')
            """,
            migrador=migrador,
        )
    }

    assert con_concesion_explicita == {"v_entitlement_medico"}


def test_el_migrador_no_gano_acceso_a_ningun_esquema_nuevo(observador, migrador):
    """Y el privilegio es de tabla, no de schema.

    ``USAGE`` sobre ``publicacion`` lo tiene por ser su dueño, no por una
    concesión de esta revisión; sobre los demás esquemas lo tiene por lo mismo.
    Lo que esta prueba descarta es que la solución haya pasado por abrirle un
    schema que no fuera suyo.
    """
    ajenos = [
        fila["nspname"]
        for fila in consultar(
            observador,
            """
            SELECT n.nspname
            FROM pg_namespace n
            WHERE n.nspname IN ('operacional', 'analitico', 'privado',
                                'seguridad', 'publicacion')
              AND pg_get_userbyid(n.nspowner) <> :migrador
              AND has_schema_privilege(:migrador, n.nspname, 'USAGE')
            """,
            migrador=migrador,
        )
    ]

    assert ajenos == []


def test_asumir_el_rol_cambia_current_user_pero_no_session_user(observador):
    """El supuesto que sostiene el ``GRANT ... TO SESSION_USER``.

    La migración concede desde dentro de un ``SET LOCAL ROLE``, donde
    ``CURRENT_USER`` ya es el rol asumido: conceder a ``CURRENT_USER`` sería que
    ``fetalalert_rls_owner`` se concediera a sí mismo lo que ya tiene, y el
    migrador se quedaría igual --que es exactamente el defecto que tuvo la
    primera versión de esta revisión--. ``SESSION_USER`` sigue siendo quien
    abrió la conexión.

    La prueba no necesita las credenciales del migrador: la propiedad es de
    PostgreSQL y se observa con cualquier identidad que pueda asumir el rol.
    """
    # Dentro de una transaccion real, que es como Alembic ejecuta la migracion.
    # El engine del observador esta en AUTOCOMMIT --lo necesita para preparar
    # escenarios--, y ahi ``SET LOCAL`` se descarta en el acto: hay que pedir
    # explicitamente un nivel de aislamiento transaccional para esta conexion.
    with observador.connect().execution_options(
        isolation_level="READ COMMITTED"
    ) as conexion:
        with conexion.begin():
            antes = conexion.execute(
                text("SELECT current_user, session_user")
            ).mappings().one()
            conexion.execute(text("SET LOCAL ROLE fetalalert_rls_owner"))
            dentro = conexion.execute(
                text("SELECT current_user, session_user")
            ).mappings().one()
            conexion.execute(text("RESET ROLE"))
            despues = conexion.execute(text("SELECT current_user")).scalar_one()

    assert dentro["current_user"] == "fetalalert_rls_owner"
    assert dentro["session_user"] == antes["session_user"]
    assert dentro["current_user"] != dentro["session_user"]
    assert despues == antes["current_user"]


@pytest.mark.parametrize(
    "capacidad", ["rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb"]
)
def test_power_bi_no_gano_ninguna_capacidad(observador, capacidad):
    assert not escalar(
        observador,
        f"SELECT {capacidad} FROM pg_roles WHERE rolname = 'fetalalert_powerbi'",
    )


@pytest.mark.parametrize(
    "esquema", ["operacional", "analitico", "privado", "seguridad"]
)
def test_power_bi_sigue_sin_alcanzar_los_esquemas_internos(observador, esquema):
    """Sin ``USAGE`` no puede ni nombrar un objeto del schema.

    Es la protección que no depende de acordarse de revocar: una tabla nueva en
    ``analitico`` queda fuera de su alcance sin que nadie haga nada.
    """
    assert not es_verdadero(
        observador, f"has_schema_privilege('fetalalert_powerbi', '{esquema}', 'USAGE')"
    )


def test_power_bi_no_alcanza_ninguna_tabla_del_modelo_estrella(observador):
    alcanzables = [
        fila["relname"]
        for fila in consultar(
            observador,
            """
            SELECT c.relname
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname IN ('analitico', 'privado')
              AND c.relkind = 'r'
              AND has_table_privilege('fetalalert_powerbi', c.oid, 'SELECT')
            """,
        )
    ]

    assert alcanzables == []


def test_public_no_recibe_nada_sobre_los_objetos_nuevos(observador):
    """Una entrada de PUBLIC en una ACL empieza por '=' -- sin receptor."""
    filas = consultar(
        observador,
        """
        SELECT c.relname AS objeto, array_to_string(c.relacl, ' ') AS acl
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'publicacion' AND c.relname = ANY(:vistas)
        """,
        vistas=list(VISTAS_NUEVAS),
    )

    assert len(filas) == len(VISTAS_NUEVAS), "faltan vistas nuevas en el catálogo"
    for fila in filas:
        assert " =" not in f" {fila['acl'] or ''}", fila


def test_ningun_default_privilege_abre_los_esquemas_publicados(observador):
    """Y lo que se cree mañana tampoco será de PUBLIC."""
    abiertos = [
        fila["acl"]
        for fila in consultar(
            observador,
            """
            SELECT array_to_string(d.defaclacl, ' ') AS acl
            FROM pg_default_acl d
            JOIN pg_namespace n ON n.oid = d.defaclnamespace
            WHERE n.nspname IN ('publicacion', 'privado')
            """,
        )
        if " =" in f" {fila['acl'] or ''}"
    ]

    assert abiertos == []


# ---------------------------------------------------------------------------
# 8. Escenarios controlados: homónimas, multiembarazo y varios seguimientos
# ---------------------------------------------------------------------------
#
# Los tres se fabrican aquí y se retiran al terminar, por los identificadores
# que cada fixture creó. El conjunto canónico no los trae -- 30 pacientes, 30
# embarazos, un seguimiento cada uno -- y depender de que algún día los traiga
# sería depender de una casualidad.


def _ids_de_catalogo(observador) -> dict:
    return dict(
        consultar(
            observador,
            """
            SELECT (SELECT min(id_clinica) FROM operacional.clinica) AS id_clinica,
                   (SELECT min(id_tiempo_gest) FROM operacional.tiempo_gestacional)
                       AS id_tiempo,
                   (SELECT id_semaforo FROM operacional.semaforo
                     WHERE codigo_nivel = 'OK') AS id_semaforo,
                   (SELECT min(id_factor_riesgo) FROM operacional.factor_riesgo)
                       AS id_factor
            """,
        )[0]
    )


def _crear_paciente(conexion, catalogo, *, cedula, nombres, email):
    return conexion.execute(
        text(
            """
            INSERT INTO operacional.paciente
                (cedula, primer_nombre, apellido_paterno, email_pac, fecha_nac)
            VALUES (:cedula, :nombre, :apellido, :email, '1994-03-21')
            RETURNING id_paciente
            """
        ),
        {
            "cedula": cedula,
            "nombre": nombres[0],
            "apellido": nombres[1],
            "email": email,
        },
    ).scalar_one()


def _crear_embarazo(conexion, catalogo, *, id_paciente, gestas):
    return conexion.execute(
        text(
            """
            INSERT INTO operacional.embarazo
                (id_paciente, id_clinica, numero_gestas, numero_partos,
                 fecha_inicio, fecha_probable_parto, estado_embarazo)
            VALUES (:p, :c, :g, 0,
                    CURRENT_DATE - 120, CURRENT_DATE - 120 + 280, 'ACTIVO')
            RETURNING id_embarazo
            """
        ),
        {"p": id_paciente, "c": catalogo["id_clinica"], "g": gestas},
    ).scalar_one()


def _publicar(conexion, catalogo, *, id_paciente, embarazos, nombre_completo, email,
              cedula):
    """Los seudónimos y las filas analíticas, como las dejaría el ETL."""
    conexion.execute(
        text("INSERT INTO privado.seudonimo_paciente (id_paciente) VALUES (:p)"),
        {"p": id_paciente},
    )
    conexion.execute(
        text(
            """
            INSERT INTO analitico.dim_paciente
                (id_paciente, id_clinica, cedula, nombre_completo, telefono_pac,
                 email_pac, fecha_nac)
            VALUES (:p, :c, :cedula, :nombre, '6000-0000', :email, '1994-03-21')
            """
        ),
        {
            "p": id_paciente,
            "c": catalogo["id_clinica"],
            "cedula": cedula,
            "nombre": nombre_completo,
            "email": email,
        },
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
        conexion.execute(
            text(
                """
                INSERT INTO analitico.dim_embarazo
                    (id_embarazo, id_paciente, id_clinica, numero_gestas,
                     numero_partos, estado_embarazo, fecha_inicio,
                     fecha_probable_parto, duracion_est_semanas)
                SELECT e.id_embarazo, e.id_paciente, e.id_clinica, e.numero_gestas,
                       e.numero_partos, e.estado_embarazo, e.fecha_inicio,
                       e.fecha_probable_parto, 40
                FROM operacional.embarazo e WHERE e.id_embarazo = :e
                """
            ),
            {"e": id_embarazo},
        )
    return seudonimos


def _asignar(conexion, *, id_embarazo, id_medico, rol="PRINCIPAL"):
    conexion.execute(
        text(
            """
            INSERT INTO operacional.seguimiento_clinico
                (id_embarazo, id_medico, fecha_asignacion, fecha_fin,
                 rol_seguimiento, activo)
            VALUES (:e, :m, CURRENT_DATE - 30, NULL, :rol, true)
            """
        ),
        {"e": id_embarazo, "m": id_medico, "rol": rol},
    )


def _limpiar(observador, *, pacientes, embarazos):
    with observador.connect() as conexion:
        for sentencia in (
            "DELETE FROM analitico.fact_lectura_biometrica "
            "WHERE id_embarazo = ANY(:ids)",
            "DELETE FROM analitico.bridge_embarazo_factor_riesgo "
            "WHERE id_embarazo = ANY(:ids)",
            "DELETE FROM operacional.embarazo_factor_riesgo "
            "WHERE id_embarazo = ANY(:ids)",
            "DELETE FROM analitico.dim_embarazo WHERE id_embarazo = ANY(:ids)",
            "DELETE FROM privado.seudonimo_embarazo WHERE id_embarazo = ANY(:ids)",
            "DELETE FROM operacional.seguimiento_clinico "
            "WHERE id_embarazo = ANY(:ids)",
            "DELETE FROM operacional.embarazo WHERE id_embarazo = ANY(:ids)",
        ):
            conexion.execute(text(sentencia), {"ids": embarazos})
        for sentencia in (
            "DELETE FROM analitico.dim_paciente WHERE id_paciente = ANY(:ids)",
            "DELETE FROM privado.seudonimo_paciente WHERE id_paciente = ANY(:ids)",
            "DELETE FROM operacional.paciente WHERE id_paciente = ANY(:ids)",
        ):
            conexion.execute(text(sentencia), {"ids": pacientes})


@pytest.fixture
def homonimas(observador, medicos):
    """Dos pacientes distintas que se llaman igual, las dos autorizadas.

    El caso que motiva publicar la cédula: si el tablero identificara por nombre,
    estas dos serían la misma persona y el médico vería una historia clínica que
    mezcla a dos gestantes.
    """
    catalogo = _ids_de_catalogo(observador)
    id_medico = medicos[0]["id_medico"]
    creadas, embarazos, datos = [], [], []

    with observador.connect() as conexion:
        for sufijo in ("X", "Y"):
            cedula = f"SIM-PAC-{MARCA_CEDULA}-{sufijo}"
            email = f"ana.perez.{sufijo.lower()}.{MARCA.lower()}@example.com"
            id_paciente = _crear_paciente(
                conexion,
                catalogo,
                cedula=cedula,
                nombres=("Ana", "Perez"),
                email=email,
            )
            id_embarazo = _crear_embarazo(
                conexion, catalogo, id_paciente=id_paciente, gestas=2
            )
            _asignar(conexion, id_embarazo=id_embarazo, id_medico=id_medico)
            seudonimos = _publicar(
                conexion,
                catalogo,
                id_paciente=id_paciente,
                embarazos=[id_embarazo],
                nombre_completo="Ana Perez",
                email=email,
                cedula=cedula,
            )
            creadas.append(id_paciente)
            embarazos.append(id_embarazo)
            datos.append({"cedula": cedula, "seudonimo_embarazo": seudonimos[id_embarazo]})

    yield {"upn": medicos[0]["upn"], "pacientes": datos}

    _limpiar(observador, pacientes=creadas, embarazos=embarazos)


@pytest.fixture
def multiembarazo(observador, medicos):
    """Una paciente con dos episodios; el médico sigue uno solo.

    El episodio no autorizado se dota de todo lo que podría filtrarse --una
    lectura y un factor de riesgo-- para que «no aparece» signifique algo.
    """
    catalogo = _ids_de_catalogo(observador)
    id_medico = medicos[0]["id_medico"]
    cedula = f"SIM-PAC-{MARCA_CEDULA}-MULTI"
    email = f"multi.{MARCA.lower()}@example.com"

    with observador.connect() as conexion:
        id_paciente = _crear_paciente(
            conexion,
            catalogo,
            cedula=cedula,
            nombres=("Rosa", "Multiple"),
            email=email,
        )
        autorizado = _crear_embarazo(
            conexion, catalogo, id_paciente=id_paciente, gestas=2
        )
        hermano = _crear_embarazo(
            conexion, catalogo, id_paciente=id_paciente, gestas=3
        )
        _asignar(conexion, id_embarazo=autorizado, id_medico=id_medico)
        seudonimos = _publicar(
            conexion,
            catalogo,
            id_paciente=id_paciente,
            embarazos=[autorizado, hermano],
            nombre_completo="Rosa Multiple",
            email=email,
            cedula=cedula,
        )

        # Un factor y una lectura sobre el episodio **no** autorizado.
        conexion.execute(
            text(
                """
                INSERT INTO analitico.bridge_embarazo_factor_riesgo
                    (id_embarazo, id_factor_riesgo, fecha_diagnostico, activo)
                VALUES (:e, :f, CURRENT_DATE - 60, true)
                """
            ),
            {"e": hermano, "f": catalogo["id_factor"]},
        )
        conexion.execute(
            text(
                """
                INSERT INTO analitico.fact_lectura_biometrica
                    (id_lectura, id_sesion, id_paciente, id_medico, id_clinica,
                     id_tiempo_gestacional, id_embarazo, id_semaforo,
                     hr_valor, spo2_valor, estado_hr, estado_spo2, fecha_hora)
                SELECT (SELECT max(id_lectura) + 1
                          FROM analitico.fact_lectura_biometrica),
                       (SELECT max(id_sesion) + 1
                          FROM analitico.fact_lectura_biometrica),
                       :p, :m, :c, :t, :e, :s,
                       120.00, 98.00, 'OK', 'OK', now()
                """
            ),
            {
                "p": id_paciente,
                "m": id_medico,
                "c": catalogo["id_clinica"],
                "t": catalogo["id_tiempo"],
                "e": hermano,
                "s": catalogo["id_semaforo"],
            },
        )

    yield {
        "upn": medicos[0]["upn"],
        "cedula": cedula,
        "email": email,
        "autorizado": seudonimos[autorizado],
        "hermano": seudonimos[hermano],
    }

    _limpiar(observador, pacientes=[id_paciente], embarazos=[autorizado, hermano])


@pytest.fixture
def tres_seguimientos(observador, medicos):
    """Un episodio seguido por tres médicos con los tres tipos a la vez.

    Los tres conceden lo mismo, y el episodio tiene que seguir siendo **una**
    fila: es el caso que un ``JOIN`` contra el entitlement triplicaría.
    """
    if len(medicos) < 3:
        pytest.skip("hacen falta tres médicos para este escenario")

    catalogo = _ids_de_catalogo(observador)
    cedula = f"SIM-PAC-{MARCA_CEDULA}-TRES"
    email = f"tres.{MARCA.lower()}@example.com"

    with observador.connect() as conexion:
        id_paciente = _crear_paciente(
            conexion,
            catalogo,
            cedula=cedula,
            nombres=("Lucia", "Compartida"),
            email=email,
        )
        id_embarazo = _crear_embarazo(
            conexion, catalogo, id_paciente=id_paciente, gestas=1
        )
        for medico, rol in zip(medicos[:3], ("PRINCIPAL", "APOYO", "REEMPLAZO")):
            _asignar(
                conexion,
                id_embarazo=id_embarazo,
                id_medico=medico["id_medico"],
                rol=rol,
            )
        seudonimos = _publicar(
            conexion,
            catalogo,
            id_paciente=id_paciente,
            embarazos=[id_embarazo],
            nombre_completo="Lucia Compartida",
            email=email,
            cedula=cedula,
        )

    yield {
        "upns": [m["upn"] for m in medicos[:3]],
        "seudonimo_embarazo": seudonimos[id_embarazo],
    }

    _limpiar(observador, pacientes=[id_paciente], embarazos=[id_embarazo])


def test_dos_pacientes_homonimas_no_se_colapsan(powerbi, homonimas):
    """El nombre no es llave, y aquí se mide.

    Las dos comparten ``nombre_completo`` exacto. Si el modelo las identificara
    por nombre habría una sola fila; hay dos, con seudónimos distintos y con
    ``paciente_display`` distinto, que es el campo del segmentador.
    """
    cedulas = [p["cedula"] for p in homonimas["pacientes"]]
    filas = consultar(
        powerbi,
        "SELECT seudonimo_paciente, nombre_completo, cedula, paciente_display "
        "FROM publicacion.v_paciente_medico WHERE cedula = ANY(:c)",
        c=cedulas,
    )

    assert len(filas) == 2
    assert len({f["nombre_completo"] for f in filas}) == 1, "el escenario es vacuo"
    assert len({str(f["seudonimo_paciente"]) for f in filas}) == 2
    assert len({f["paciente_display"] for f in filas}) == 2
    assert len({f["cedula"] for f in filas}) == 2
    for fila in filas:
        assert fila["cedula"] in fila["paciente_display"]


def test_agrupar_por_nombre_colapsaria_a_las_homonimas(powerbi, homonimas):
    """La contrapartida: por qué el segmentador no puede usar el nombre.

    Sin esta prueba «usar paciente_display» parecería una preferencia estética.
    Agrupando por nombre las dos gestantes se vuelven una.
    """
    cedulas = [p["cedula"] for p in homonimas["pacientes"]]
    por_nombre = escalar(
        powerbi,
        "SELECT count(DISTINCT nombre_completo) FROM publicacion.v_paciente_medico "
        "WHERE cedula = ANY(:c)",
        c=cedulas,
    )
    por_display = escalar(
        powerbi,
        "SELECT count(DISTINCT paciente_display) FROM publicacion.v_paciente_medico "
        "WHERE cedula = ANY(:c)",
        c=cedulas,
    )

    assert (por_nombre, por_display) == (1, 2)


def test_la_paciente_del_multiembarazo_es_identificable(powerbi, multiembarazo):
    """Guardia anti-vacuidad: su PII sí llega, porque hay un episodio autorizado."""
    filas = consultar(
        powerbi,
        """
        SELECT p.cedula, p.email_pac, p.nombre_completo, p.telefono_pac
        FROM publicacion.v_paciente_medico p
        JOIN publicacion.v_entitlement_paciente_medico ep
          ON ep.seudonimo_paciente = p.seudonimo_paciente
        WHERE ep.upn_medico = lower(:upn) AND p.cedula = :cedula
        """,
        upn=multiembarazo["upn"],
        cedula=multiembarazo["cedula"],
    )

    assert len(filas) == 1
    assert filas[0]["email_pac"] == multiembarazo["email"]


def test_el_episodio_autorizado_del_multiembarazo_esta_publicado(
    powerbi, multiembarazo
):
    alcance = embarazos_de(powerbi, multiembarazo["upn"])

    assert multiembarazo["autorizado"] in alcance


def test_el_episodio_hermano_no_entra_por_la_paciente(powerbi, multiembarazo):
    """La propiedad central del diseño de dos entitlements.

    Identificar a la paciente **no** arrastra sus otros episodios. Si la
    propagación paciente -> embarazo concediera acceso, el hermano aparecería
    aquí.
    """
    assert multiembarazo["hermano"] not in embarazos_de(
        powerbi, multiembarazo["upn"]
    )


@pytest.mark.parametrize(
    "vista", ["v_embarazo_medico", "v_lectura", "v_embarazo_factor_riesgo"]
)
def test_del_episodio_hermano_no_sale_ningun_dato_clinico(
    powerbi, multiembarazo, vista
):
    """Ni el episodio, ni su lectura, ni su factor de riesgo, ni su clínica."""
    alcanzables = escalar(
        powerbi,
        f"""
        SELECT count(*)
        FROM publicacion.{vista} x
        JOIN publicacion.v_entitlement_medico em
          ON em.seudonimo_embarazo = x.seudonimo_embarazo
        WHERE em.upn_medico = lower(:upn)
          AND x.seudonimo_embarazo = CAST(:s AS uuid)
        """,
        upn=multiembarazo["upn"],
        s=multiembarazo["hermano"],
    )

    assert alcanzables == 0


def test_el_hermano_existe_en_la_base_para_que_la_prueba_no_sea_vacua(
    observador, multiembarazo
):
    """Que no aparezca tiene que ser por filtrado, no por ausencia."""
    presente = escalar(
        observador,
        "SELECT count(*) FROM analitico.dim_embarazo de "
        "JOIN privado.seudonimo_embarazo se ON se.id_embarazo = de.id_embarazo "
        "WHERE se.seudonimo = CAST(:s AS uuid)",
        s=multiembarazo["hermano"],
    )
    con_lectura = escalar(
        observador,
        "SELECT count(*) FROM analitico.fact_lectura_biometrica f "
        "JOIN privado.seudonimo_embarazo se ON se.id_embarazo = f.id_embarazo "
        "WHERE se.seudonimo = CAST(:s AS uuid)",
        s=multiembarazo["hermano"],
    )

    assert presente == 1
    assert con_lectura == 1


def test_los_tres_tipos_de_seguimiento_conceden_lo_mismo(powerbi, tres_seguimientos):
    """PRINCIPAL, APOYO y REEMPLAZO: el tipo no se filtra, y no debe filtrarse."""
    for upn in tres_seguimientos["upns"]:
        assert tres_seguimientos["seudonimo_embarazo"] in embarazos_de(powerbi, upn)


def test_tres_seguimientos_no_triplican_el_episodio(powerbi, tres_seguimientos):
    """``EXISTS`` y no ``JOIN``: el episodio sigue siendo una fila.

    Es la prueba que un ``JOIN`` contra el entitlement rompería, y la razón de
    que la vista esté escrita como está.
    """
    filas = escalar(
        powerbi,
        "SELECT count(*) FROM publicacion.v_embarazo_medico "
        "WHERE seudonimo_embarazo = CAST(:s AS uuid)",
        s=tres_seguimientos["seudonimo_embarazo"],
    )

    assert filas == 1


def test_tres_seguimientos_no_triplican_a_la_paciente(powerbi, tres_seguimientos):
    filas = escalar(
        powerbi,
        """
        SELECT count(*)
        FROM publicacion.v_paciente_medico p
        JOIN publicacion.v_embarazo_medico e
          ON e.seudonimo_paciente = p.seudonimo_paciente
        WHERE e.seudonimo_embarazo = CAST(:s AS uuid)
        """,
        s=tres_seguimientos["seudonimo_embarazo"],
    )

    assert filas == 1


def test_el_entitlement_reparte_el_episodio_entre_los_tres(
    powerbi, tres_seguimientos
):
    """El puente sí tiene tres filas -- una por médico -- y eso es correcto.

    Es un puente: su grano es la pareja. Lo que no puede multiplicarse es la
    dimensión, que la prueba anterior fija en una.
    """
    filas = escalar(
        powerbi,
        "SELECT count(*) FROM publicacion.v_entitlement_medico "
        "WHERE seudonimo_embarazo = CAST(:s AS uuid)",
        s=tres_seguimientos["seudonimo_embarazo"],
    )

    assert filas == 3


# ---------------------------------------------------------------------------
# 9. El universo de la superficie, medido sobre la vista y no a través del puente
# ---------------------------------------------------------------------------
#
# Estas cuatro pruebas existen porque el *mutation testing* de la FASE 4 encontró
# que no estaban y hacían falta.
#
# La mutación consistió en quitarle a ``v_paciente_medico`` su filtro de
# entitlement, dejándola publicar las 30 pacientes del conjunto en lugar de las
# 20 con seguimiento vigente. Ninguna prueba falló, y el motivo es instructivo:
# todas consultaban la PII **uniendo** con el puente, igual que hace el RLS del
# modelo, así que el filtro del puente tapaba la ausencia del filtro de la vista.
#
# En Power BI el informe habría seguido enseñando lo correcto. Pero el dataset
# importado habría llevado dentro la PII de diez gestantes que ningún médico
# sigue, y reducir esa exposición es justamente lo que el filtro en SQL aporta
# sobre el RLS del modelo. Por eso ahora se mide la vista **por sí sola**.


def test_la_dimension_de_paciente_solo_publica_pacientes_en_seguimiento(
    powerbi, observador
):
    """Sin unir con el puente: la vista, por sí misma, no lleva a nadie más.

    Es la capa de protección que vive en PostgreSQL y no en Power BI. Una
    paciente sin ningún seguimiento vigente no tiene fila aquí, de modo que su
    nombre, su cédula, su teléfono y su correo no entran siquiera en el archivo
    del informe.
    """
    publicadas = escalar(
        powerbi, "SELECT count(*) FROM publicacion.v_paciente_medico"
    )
    con_seguimiento = escalar(
        powerbi,
        "SELECT count(DISTINCT seudonimo_paciente) "
        "FROM publicacion.v_entitlement_paciente_medico",
    )
    en_la_dimension = escalar(
        observador, "SELECT count(*) FROM analitico.dim_paciente"
    )

    assert publicadas == con_seguimiento
    assert publicadas < en_la_dimension, (
        "todas las pacientes del modelo tienen seguimiento vigente, así que esta "
        "prueba no distingue una superficie filtrada de una que no lo está"
    )


def test_ninguna_fila_de_la_dimension_de_paciente_carece_de_entitlement(powerbi):
    """La misma propiedad, fila a fila y no por conteo.

    Dos conjuntos del mismo tamaño pueden no ser el mismo conjunto.
    """
    huerfanas = escalar(
        powerbi,
        """
        SELECT count(*)
        FROM publicacion.v_paciente_medico p
        WHERE NOT EXISTS (
            SELECT 1 FROM publicacion.v_entitlement_paciente_medico ep
            WHERE ep.seudonimo_paciente = p.seudonimo_paciente
        )
        """,
    )

    assert huerfanas == 0


def test_ninguna_fila_de_la_dimension_de_embarazo_carece_de_entitlement(powerbi):
    huerfanos = escalar(
        powerbi,
        """
        SELECT count(*)
        FROM publicacion.v_embarazo_medico e
        WHERE NOT EXISTS (
            SELECT 1 FROM publicacion.v_entitlement_medico em
            WHERE em.seudonimo_embarazo = e.seudonimo_embarazo
        )
        """,
    )

    assert huerfanos == 0


def test_ninguna_fila_del_puente_de_factores_carece_de_entitlement(powerbi):
    huerfanas = escalar(
        powerbi,
        """
        SELECT count(*)
        FROM publicacion.v_embarazo_factor_riesgo f
        WHERE NOT EXISTS (
            SELECT 1 FROM publicacion.v_entitlement_medico em
            WHERE em.seudonimo_embarazo = f.seudonimo_embarazo
        )
        """,
    )

    assert huerfanas == 0
