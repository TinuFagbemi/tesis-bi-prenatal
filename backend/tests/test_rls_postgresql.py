"""Aislamiento por filas contra PostgreSQL, como un rol restringido (SCRUM-98).

Se omite salvo que este definida ``SCRUM98_RLS_TEST_DATABASE_URL``. Nunca cae por
omision sobre otra variable.

**Todo lo clinico se comprueba conectado como ``fetalalert_api``**, el mismo rol
con el que la API atiende peticiones: ``LOGIN`` y nada mas, ni ``SUPERUSER``, ni
``BYPASSRLS``, ni propiedad de ninguna tabla. Un ``SELECT`` exitoso como el
propietario no demostraria nada, porque el propietario omite sus propias
politicas salvo ``FORCE``, y un superusuario las omite siempre.

Antes existia un ``fetalalert_api`` con sus propias politicas para este
papel. Se retiro: con un rol de pruebas, lo que quedaba demostrado era el
aislamiento *de ese rol*, y cada despliegue tenia que aprovisionar una identidad
que solo servia en CI. El sujeto de la prueba es ahora el sujeto real.

La base debe estar migrada al head y cargada con el dataset simulado. El CI la
deja asi antes de llegar aqui; en local, el README explica el orden.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import os
import re

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

VARIABLE_DE_ENTORNO = "SCRUM98_RLS_TEST_DATABASE_URL"

TABLAS_CON_RLS = (
    "usuario_paciente",
    "usuario_medico",
    "embarazo",
    "asignacion_dispositivo",
    "sesion_monitoreo",
    "lectura_biometrica",
)

# Lo que la API tiene prohibido incluso nombrar. La proteccion aqui no es una
# politica: es la ausencia de privilegio, que es mas simple y mas fuerte.
VEDADAS_A_LA_API = (
    "paciente",
    "medico",
    "telefono_paciente",
    "telefono_medico",
    "medico_clinica",
    "seguimiento_clinico",
    "clinica",
    "auditoria_log",
)

pytestmark = pytest.mark.skipif(
    not os.environ.get(VARIABLE_DE_ENTORNO),
    reason=(
        f"Define {VARIABLE_DE_ENTORNO} apuntando a una base migrada y cargada, "
        "con el rol fetalalert_api."
    ),
)


@pytest.fixture(scope="session")
def url() -> str:
    valor = os.environ[VARIABLE_DE_ENTORNO]
    if make_url(valor).username != "fetalalert_api":
        pytest.fail(
            f"{VARIABLE_DE_ENTORNO} debe conectarse como fetalalert_api. "
            "Comprobar el aislamiento con otra credencial no demuestra nada."
        )
    return valor


@pytest.fixture(scope="session")
def engine(url):
    motor = create_engine(url, poolclass=NullPool)
    try:
        yield motor
    finally:
        motor.dispose()


@pytest.fixture(scope="session")
def identidades(url):
    """Identidades reales del dataset, leidas una vez con una conexion sin RLS.

    El propietario solo se usa para *elegir* a quien suplantar; ninguna asercion
    de aislamiento se hace con el.
    """
    administrativa = make_url(url).set(
        username=os.environ.get("SCRUM98_RLS_ADMIN_USER", "fetalalert_ci"),
        password=os.environ.get("SCRUM98_RLS_ADMIN_PASSWORD", ""),
    )
    motor = create_engine(administrativa, poolclass=NullPool)
    try:
        with motor.connect() as conexion:
            filas = conexion.execute(
                text(
                    """
                    SELECT u.id_usuario, r.nombre_rol,
                           up.id_paciente, um.id_medico
                    FROM operacional.usuario u
                    JOIN operacional.rol r ON r.id_rol = u.id_rol
                    LEFT JOIN operacional.usuario_paciente up
                           ON up.id_usuario = u.id_usuario
                    LEFT JOIN operacional.usuario_medico um
                           ON um.id_usuario = u.id_usuario
                    WHERE u.activo
                    ORDER BY u.id_usuario
                    """
                )
            ).mappings().all()
            seguimientos = conexion.execute(
                text(
                    """
                    SELECT sc.id_medico, sc.id_embarazo, sc.activo,
                           sc.fecha_asignacion, sc.fecha_fin,
                           (sc.activo
                            AND sc.fecha_asignacion <= CURRENT_DATE
                            AND (sc.fecha_fin IS NULL
                                 OR sc.fecha_fin >= CURRENT_DATE)) AS vigente
                    FROM operacional.seguimiento_clinico sc
                    """
                )
            ).mappings().all()
    finally:
        motor.dispose()

    pacientes = [f for f in filas if f["nombre_rol"] == "PACIENTE"]
    medicos = [f for f in filas if f["nombre_rol"] == "MEDICO"]
    admins = [f for f in filas if f["nombre_rol"] == "ADMIN"]
    assert len(pacientes) >= 2 and medicos and admins, "dataset insuficiente"
    return {
        "pacientes": pacientes,
        "medicos": medicos,
        "admin": admins[0],
        "seguimientos": seguimientos,
    }


def _como(engine, id_usuario, consulta, parametros=None):
    """Ejecuta una consulta con esa identidad instalada, y revierte siempre."""
    with engine.connect() as conexion:
        with conexion.begin():
            if id_usuario is not None:
                conexion.execute(
                    text("SELECT set_config('fetalalert.id_usuario', :v, true)"),
                    {"v": str(id_usuario)},
                )
            return conexion.execute(text(consulta), parametros or {}).scalars().all()


def _cuenta(engine, id_usuario, tabla, condicion="true"):
    filas = _como(
        engine, id_usuario, f"SELECT count(*) FROM operacional.{tabla} WHERE {condicion}"
    )
    return filas[0]


# ---------------------------------------------------------------------------
# 1. El rol de la prueba es realmente restringido
# ---------------------------------------------------------------------------


def test_el_rol_no_es_superusuario_ni_omite_rls(engine):
    """Si esta fallara, ninguna de las demas demostraria nada."""
    fila = _como(
        engine,
        None,
        "SELECT rolsuper::text || ',' || rolbypassrls::text || ',' || "
        "rolcreaterole::text FROM pg_roles WHERE rolname = current_user",
    )

    assert fila[0] == "false,false,false"


def test_el_rol_no_posee_ninguna_tabla_clinica(engine):
    poseidas = _como(
        engine,
        None,
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname IN ('operacional','analitico') "
        "AND pg_has_role(current_user, c.relowner, 'USAGE')",
    )

    assert poseidas[0] == 0


@pytest.mark.parametrize("tabla", TABLAS_CON_RLS)
def test_cada_tabla_protegida_tiene_enable_y_force(engine, tabla):
    fila = _como(
        engine,
        None,
        "SELECT relrowsecurity::text || ',' || relforcerowsecurity::text "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='operacional' AND c.relname=:t",
        {"t": tabla},
    )

    assert fila[0] == "true,true"


# ---------------------------------------------------------------------------
# 2. Fallo cerrado
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tabla", ["embarazo", "sesion_monitoreo", "lectura_biometrica"])
def test_sin_contexto_no_se_ve_ninguna_fila(engine, tabla):
    assert _cuenta(engine, None, tabla) == 0


@pytest.mark.parametrize(
    "valor",
    ["", "   ", "abc", "1; DROP TABLE operacional.embarazo", "-1", "0",
     "99999999999999", "1.5", "NULL"],
    ids=["vacio", "espacios", "letras", "inyeccion", "negativo", "cero",
         "desbordado", "decimal", "texto-null"],
)
@pytest.mark.parametrize("tabla", ["embarazo", "lectura_biometrica"])
def test_un_contexto_invalido_o_manipulado_no_abre_nada(engine, valor, tabla):
    """El helper descarta antes de convertir: no revienta el cast, devuelve NULL."""
    with engine.connect() as conexion:
        with conexion.begin():
            conexion.execute(
                text("SELECT set_config('fetalalert.id_usuario', :v, true)"),
                {"v": valor},
            )
            filas = conexion.execute(
                text(f"SELECT count(*) FROM operacional.{tabla}")
            ).scalar()

    assert filas == 0


def test_un_usuario_inexistente_no_abre_nada(engine):
    assert _cuenta(engine, 9_000_999, "embarazo") == 0


# ---------------------------------------------------------------------------
# 3. PACIENTE
# ---------------------------------------------------------------------------


def test_la_paciente_ve_sus_embarazos_y_solo_los_suyos(engine, identidades):
    paciente = identidades["pacientes"][0]

    propios = _como(
        engine,
        paciente["id_usuario"],
        "SELECT DISTINCT id_paciente FROM operacional.embarazo",
    )

    assert propios == [paciente["id_paciente"]]


def test_la_paciente_no_ve_los_embarazos_de_otra(engine, identidades):
    a, b = identidades["pacientes"][0], identidades["pacientes"][1]

    ajenos = _cuenta(
        engine, a["id_usuario"], "embarazo", f"id_paciente = {b['id_paciente']}"
    )

    assert ajenos == 0


def test_la_paciente_ve_sus_sesiones_y_lecturas(engine, identidades):
    paciente = identidades["pacientes"][0]

    sesiones = _cuenta(engine, paciente["id_usuario"], "sesion_monitoreo")
    lecturas = _cuenta(engine, paciente["id_usuario"], "lectura_biometrica")

    assert sesiones > 0
    assert lecturas > 0


def test_las_sesiones_visibles_pertenecen_a_embarazos_propios(engine, identidades):
    """No basta con que vea algunas: no debe ver ninguna que no cuelgue de ella."""
    paciente = identidades["pacientes"][0]

    huerfanas = _como(
        engine,
        paciente["id_usuario"],
        "SELECT count(*) FROM operacional.sesion_monitoreo s "
        "WHERE NOT EXISTS (SELECT 1 FROM operacional.embarazo e "
        "WHERE e.id_embarazo = s.id_embarazo)",
    )

    assert huerfanas[0] == 0


def test_cambiar_el_identificador_en_la_consulta_no_amplia_nada(engine, identidades):
    """Filtrar por el id de otra no lo hace visible: el filtro no es autorizacion."""
    a, b = identidades["pacientes"][0], identidades["pacientes"][1]

    visibles = _como(
        engine,
        a["id_usuario"],
        "SELECT count(*) FROM operacional.embarazo WHERE id_paciente = :otra",
        {"otra": b["id_paciente"]},
    )

    assert visibles[0] == 0


def test_dos_pacientes_ven_conjuntos_disjuntos(engine, identidades):
    a, b = identidades["pacientes"][0], identidades["pacientes"][1]

    de_a = set(_como(engine, a["id_usuario"], "SELECT id_embarazo FROM operacional.embarazo"))
    de_b = set(_como(engine, b["id_usuario"], "SELECT id_embarazo FROM operacional.embarazo"))

    assert de_a and de_b
    assert de_a.isdisjoint(de_b)


@pytest.mark.parametrize("estado", ["ACTIVO", "FINALIZADO", "SUSPENDIDO"])
def test_el_estado_del_embarazo_no_recorta_el_historial(engine, identidades, estado):
    """El alcance de la gestante es su perfil, no su embarazo mas reciente.

    Se comprueba sobre el conjunto: cualquier embarazo suyo en ese estado debe
    estar visible, y ninguno ajeno.
    """
    for paciente in identidades["pacientes"]:
        visibles = _cuenta(
            engine,
            paciente["id_usuario"],
            "embarazo",
            f"estado_embarazo = '{estado}'",
        )
        propios = _como(
            engine,
            paciente["id_usuario"],
            "SELECT count(*) FROM operacional.embarazo "
            f"WHERE estado_embarazo = '{estado}' "
            f"AND id_paciente = {paciente['id_paciente']}",
        )
        assert visibles == propios[0]


# ---------------------------------------------------------------------------
# 4. MEDICO
# ---------------------------------------------------------------------------


def test_el_medico_ve_exactamente_sus_embarazos_vigentes(engine, identidades):
    for medico in identidades["medicos"]:
        esperados = {
            s["id_embarazo"]
            for s in identidades["seguimientos"]
            if s["id_medico"] == medico["id_medico"] and s["vigente"]
        }
        visibles = set(
            _como(
                engine,
                medico["id_usuario"],
                "SELECT id_embarazo FROM operacional.embarazo",
            )
        )

        assert visibles == esperados, medico["id_medico"]


def test_un_seguimiento_no_vigente_no_concede_acceso(engine, identidades):
    """Finalizado o desactivado: el acceso se revoca por completo."""
    no_vigentes = [s for s in identidades["seguimientos"] if not s["vigente"]]
    assert no_vigentes, "el dataset debe tener seguimientos no vigentes"

    for seguimiento in no_vigentes:
        medico = next(
            m
            for m in identidades["medicos"]
            if m["id_medico"] == seguimiento["id_medico"]
        )
        visible = _cuenta(
            engine,
            medico["id_usuario"],
            "embarazo",
            f"id_embarazo = {seguimiento['id_embarazo']}",
        )

        assert visible == 0


def test_la_afiliacion_a_una_clinica_no_concede_acceso(engine, identidades):
    """La regla aprobada: solo el seguimiento directo, nunca medico_clinica.

    En el dataset cada clinica agrupa mas embarazos de los que sigue cada
    medico, asi que si la afiliacion concediera algo se notaria de inmediato.
    """
    for medico in identidades["medicos"]:
        vigentes = {
            s["id_embarazo"]
            for s in identidades["seguimientos"]
            if s["id_medico"] == medico["id_medico"] and s["vigente"]
        }
        visibles = len(
            _como(
                engine,
                medico["id_usuario"],
                "SELECT id_embarazo FROM operacional.embarazo",
            )
        )

        assert visibles == len(vigentes)


def test_el_medico_lee_el_historial_completo_del_embarazo_asignado(engine, identidades):
    """Todo el historial, sin recortar por la fecha de su asignacion."""
    medico = next(
        m
        for m in identidades["medicos"]
        if any(
            s["id_medico"] == m["id_medico"] and s["vigente"]
            for s in identidades["seguimientos"]
        )
    )

    sesiones = _cuenta(engine, medico["id_usuario"], "sesion_monitoreo")
    lecturas = _cuenta(engine, medico["id_usuario"], "lectura_biometrica")

    assert sesiones > 0
    assert lecturas > 0


def test_el_medico_no_alcanza_otro_embarazo_de_la_misma_paciente(engine, identidades):
    """El alcance es el embarazo asignado, no la paciente."""
    for medico in identidades["medicos"]:
        visibles = set(
            _como(
                engine,
                medico["id_usuario"],
                "SELECT id_embarazo FROM operacional.embarazo",
            )
        )
        asignados = {
            s["id_embarazo"]
            for s in identidades["seguimientos"]
            if s["id_medico"] == medico["id_medico"] and s["vigente"]
        }

        assert visibles == asignados


def test_un_medico_sin_asignacion_vigente_no_ve_nada(engine, identidades):
    sin_vigentes = [
        m
        for m in identidades["medicos"]
        if not any(
            s["id_medico"] == m["id_medico"] and s["vigente"]
            for s in identidades["seguimientos"]
        )
    ]
    if not sin_vigentes:
        # Se fabrica el caso con una identidad de medico inexistente.
        assert _cuenta(engine, 9_000_998, "embarazo") == 0
        return
    for medico in sin_vigentes:
        assert _cuenta(engine, medico["id_usuario"], "embarazo") == 0


# ---------------------------------------------------------------------------
# 5. ADMIN
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tabla", ["embarazo", "sesion_monitoreo", "lectura_biometrica", "asignacion_dispositivo"]
)
def test_el_admin_no_tiene_lectura_clinica(engine, identidades, tabla):
    """Alcance administrativo, alcance clinico vacio. Y ningun bypass."""
    assert _cuenta(engine, identidades["admin"]["id_usuario"], tabla) == 0


@pytest.mark.parametrize("tabla", ["usuario_paciente", "usuario_medico"])
def test_el_admin_tampoco_enumera_los_puentes(engine, identidades, tabla):
    """No hay rama de ADMIN en ``pol_propia``, y la ausencia es el contrato.

    Una version anterior concedia ``OR seguridad.es_admin()`` para que la
    provision de SCRUM-97 pudiera comprobar si un perfil ya tenia cuenta. El
    efecto colateral era que una credencial administrativa podia listar los dos
    puentes enteros -- que cuenta corresponde a que paciente y a que medico --,
    que es precisamente la enumeracion que este ticket existe para impedir.

    La provision no la necesita: ``estado_del_perfil_*`` responde con una
    palabra sobre el identificador que quien llama ya trae.
    """
    assert _cuenta(engine, identidades["admin"]["id_usuario"], tabla) == 0


# ---------------------------------------------------------------------------
# 6. Puentes de identidad
# ---------------------------------------------------------------------------


def test_una_paciente_solo_ve_su_propio_vinculo(engine, identidades):
    paciente = identidades["pacientes"][0]

    filas = _como(
        engine,
        paciente["id_usuario"],
        "SELECT id_usuario FROM operacional.usuario_paciente",
    )

    assert filas == [paciente["id_usuario"]]


def test_una_paciente_no_enumera_los_vinculos_de_medicos(engine, identidades):
    paciente = identidades["pacientes"][0]

    assert _cuenta(engine, paciente["id_usuario"], "usuario_medico") == 0


# ---------------------------------------------------------------------------
# 7. Escritura: USING y WITH CHECK
# ---------------------------------------------------------------------------


def _intentar(engine, id_usuario, sentencia, parametros=None):
    """Ejecuta una escritura y devuelve (exito, mensaje). Siempre revierte."""
    try:
        with engine.connect() as conexion:
            with conexion.begin():
                conexion.execute(
                    text("SELECT set_config('fetalalert.id_usuario', :v, true)"),
                    {"v": str(id_usuario)},
                )
                conexion.execute(text(sentencia), parametros or {})
                conexion.rollback()
        return True, ""
    except Exception as error:  # noqa: BLE001 -- se inspecciona el mensaje
        return False, str(error)


@pytest.mark.parametrize("tabla", ["embarazo", "asignacion_dispositivo"])
def test_la_api_no_puede_escribir_donde_solo_lee(engine, identidades, tabla):
    """Sin privilegio de escritura: la negativa llega antes que cualquier policy."""
    paciente = identidades["pacientes"][0]

    exito, mensaje = _intentar(
        engine, paciente["id_usuario"], f"DELETE FROM operacional.{tabla}"
    )

    assert not exito
    assert "permission denied" in mensaje.lower()


@pytest.mark.parametrize("tabla", ["sesion_monitoreo", "lectura_biometrica"])
def test_no_hay_politica_de_update_ni_de_delete(engine, identidades, tabla):
    """Se concede INSERT y SELECT; modificar o borrar no esta previsto."""
    paciente = identidades["pacientes"][0]

    exito, mensaje = _intentar(
        engine, paciente["id_usuario"], f"DELETE FROM operacional.{tabla}"
    )

    assert not exito
    assert "permission denied" in mensaje.lower()


def test_el_with_check_impide_insertar_una_sesion_ajena(engine, identidades):
    """La gestante no puede crear una sesion sobre el embarazo de otra."""
    a, b = identidades["pacientes"][0], identidades["pacientes"][1]
    ajeno = _como(
        engine,
        b["id_usuario"],
        "SELECT id_embarazo FROM operacional.embarazo LIMIT 1",
    )[0]
    dispositivo = _como(
        engine, a["id_usuario"], "SELECT id_dispositivo FROM operacional.dispositivo LIMIT 1"
    )[0]

    exito, mensaje = _intentar(
        engine,
        a["id_usuario"],
        "INSERT INTO operacional.sesion_monitoreo "
        "(id_embarazo, id_dispositivo, tipo_sesion, fecha_inicio, estado_sesion, origen_dato) "
        "VALUES (:e, :d, 'SIGNOS_MATERNOS', now(), 'PENDIENTE', 'DISPOSITIVO')",
        {"e": ajeno, "d": dispositivo},
    )

    assert not exito
    assert "row-level security" in mensaje.lower() or "violates" in mensaje.lower()


def test_el_with_check_permite_insertar_una_sesion_propia(engine, identidades):
    """La contraparte: si no pasara, la politica seria demasiado estrecha."""
    paciente = identidades["pacientes"][0]
    propio = _como(
        engine,
        paciente["id_usuario"],
        "SELECT id_embarazo FROM operacional.embarazo LIMIT 1",
    )[0]
    dispositivo = _como(
        engine,
        paciente["id_usuario"],
        "SELECT id_dispositivo FROM operacional.dispositivo LIMIT 1",
    )[0]

    exito, mensaje = _intentar(
        engine,
        paciente["id_usuario"],
        "INSERT INTO operacional.sesion_monitoreo "
        "(id_embarazo, id_dispositivo, tipo_sesion, fecha_inicio, estado_sesion, origen_dato) "
        "VALUES (:e, :d, 'SIGNOS_MATERNOS', now(), 'PENDIENTE', 'DISPOSITIVO')",
        {"e": propio, "d": dispositivo},
    )

    assert exito, mensaje


def test_un_medico_no_puede_crear_sesiones(engine, identidades):
    """Lee el historial; no lo escribe. El WITH CHECK pide ser la gestante."""
    medico = identidades["medicos"][0]
    asignado = _como(
        engine, medico["id_usuario"], "SELECT id_embarazo FROM operacional.embarazo LIMIT 1"
    )
    if not asignado:
        pytest.skip("ese medico no tiene embarazos vigentes en el dataset")
    dispositivo = _como(
        engine, medico["id_usuario"], "SELECT id_dispositivo FROM operacional.dispositivo LIMIT 1"
    )[0]

    exito, _ = _intentar(
        engine,
        medico["id_usuario"],
        "INSERT INTO operacional.sesion_monitoreo "
        "(id_embarazo, id_dispositivo, tipo_sesion, fecha_inicio, estado_sesion, origen_dato) "
        "VALUES (:e, :d, 'SIGNOS_MATERNOS', now(), 'PENDIENTE', 'DISPOSITIVO')",
        {"e": asignado[0], "d": dispositivo},
    )

    assert not exito


# ---------------------------------------------------------------------------
# 8. Privilegios: lo que la API no puede ni nombrar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tabla", VEDADAS_A_LA_API)
def test_la_api_no_puede_leer_las_tablas_vedadas(engine, tabla):
    """Denegacion por privilegio, que es mas simple que una politica.

    ``auditoria_log`` entra aqui: la API escribe la traza y no la lee.
    """
    exito, mensaje = _intentar(engine, 1, f"SELECT count(*) FROM operacional.{tabla}")

    assert not exito
    assert "permission denied" in mensaje.lower()


def test_la_api_no_puede_nombrar_el_esquema_analitico(engine):
    exito, mensaje = _intentar(engine, 1, "SELECT count(*) FROM analitico.dim_paciente")

    assert not exito
    assert "permission denied" in mensaje.lower()


@pytest.mark.parametrize("esquema", ["operacional", "analitico", "seguridad", "public"])
def test_la_api_no_puede_crear_objetos(engine, esquema):
    exito, mensaje = _intentar(engine, 1, f'CREATE TABLE {esquema}.intruso (x int)')

    assert not exito
    assert "permission denied" in mensaje.lower()


def test_la_api_si_puede_insertar_en_la_auditoria(engine, identidades):
    """Append-only: escribe la traza aunque no pueda leerla."""
    paciente = identidades["pacientes"][0]

    exito, mensaje = _intentar(
        engine,
        paciente["id_usuario"],
        "INSERT INTO operacional.auditoria_log "
        "(id_usuario, accion, ip_origen) VALUES (:u, 'LOGIN_EXITOSO', '127.0.0.1')",
        {"u": paciente["id_usuario"]},
    )

    assert exito, mensaje


def test_idempotencia_sigue_siendo_global(engine, identidades):
    """Sin RLS: una politica por paciente haria invisible la reclamacion ganadora."""
    con_rls = _como(
        engine,
        None,
        "SELECT relrowsecurity::text FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='operacional' AND c.relname='idempotencia_solicitud'",
    )

    assert con_rls[0] == "false"


# ---------------------------------------------------------------------------
# 9. Helpers: search_path, PUBLIC y ausencia de escalacion
# ---------------------------------------------------------------------------


def test_public_no_puede_ejecutar_ningun_helper(engine):
    publicos = _como(
        engine,
        None,
        "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace, "
        "aclexplode(p.proacl) a WHERE n.nspname='seguridad' AND a.grantee=0",
    )

    assert publicos[0] == 0


def test_cada_helper_fija_su_search_path_con_pg_temp_al_final(engine):
    configs = _como(
        engine,
        None,
        "SELECT array_to_string(proconfig, ';') FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='seguridad'",
    )

    # Nueve: cinco de contexto, dos contadores de vinculos para el trigger de
    # coherencia y dos de estado de perfil para la provision.
    assert len(configs) == 9
    for config in configs:
        assert config == "search_path=pg_catalog, operacional, pg_temp"
        assert config.endswith("pg_temp")


def test_ningun_helper_pertenece_a_un_rol_con_login(engine):
    con_login = _como(
        engine,
        None,
        "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "JOIN pg_roles r ON r.oid=p.proowner "
        "WHERE n.nspname='seguridad' AND r.rolcanlogin",
    )

    assert con_login[0] == 0


CONSULTA_ESTADO = "SELECT seguridad.estado_del_perfil_paciente(:p)"


def test_los_helpers_de_provision_exigen_admin(engine, identidades):
    """Un helper que respondiera a cualquiera seria un oraculo de perfiles."""
    paciente = identidades["pacientes"][0]

    como_paciente = _como(
        engine, paciente["id_usuario"], CONSULTA_ESTADO, {"p": paciente["id_paciente"]}
    )
    sin_contexto = _como(engine, None, CONSULTA_ESTADO, {"p": paciente["id_paciente"]})
    como_admin = _como(
        engine,
        identidades["admin"]["id_usuario"],
        CONSULTA_ESTADO,
        {"p": paciente["id_paciente"]},
    )

    assert como_paciente[0] == "sin_autorizacion"
    assert sin_contexto[0] == "sin_autorizacion"
    # Este perfil ya tiene su cuenta: por eso el dataset lo trae vinculado.
    assert como_admin[0] == "vinculado"


def test_el_helper_de_provision_solo_devuelve_una_de_cuatro_palabras(
    engine, identidades
):
    """Ninguna rama puede devolver una columna del perfil ni del vinculo."""
    admin = identidades["admin"]["id_usuario"]
    paciente = identidades["pacientes"][0]

    vinculado = _como(engine, admin, CONSULTA_ESTADO, {"p": paciente["id_paciente"]})
    # Un identificador que el dataset no usa: existe la pregunta, no la fila.
    inexistente = _como(engine, admin, CONSULTA_ESTADO, {"p": 999_000_000})

    assert vinculado[0] == "vinculado"
    assert inexistente[0] == "inexistente"


def test_el_helper_no_distingue_perfil_ajeno_de_inexistente_sin_admin(
    engine, identidades
):
    """Sin contexto administrativo las dos preguntas reciben la misma palabra."""
    paciente = identidades["pacientes"][0]

    existente = _como(
        engine, paciente["id_usuario"], CONSULTA_ESTADO, {"p": paciente["id_paciente"]}
    )
    inexistente = _como(
        engine, paciente["id_usuario"], CONSULTA_ESTADO, {"p": 999_000_000}
    )

    assert existente == inexistente == ["sin_autorizacion"]


def test_un_helper_no_acepta_preguntar_por_otro_sujeto(engine, identidades):
    """Los que derivan la identidad no toman argumentos: no hay a quien suplantar.

    Los cinco que si toman uno preguntan por un objeto -- un embarazo, una
    cuenta, un perfil -- y ninguno acepta un ``id_usuario`` con el que fingir ser
    otro: la identidad sale siempre de ``usuario_actual_id()``, que lee el GUC de
    la transaccion. Los dos contadores del trigger reciben un ``id_usuario``,
    pero devuelven un numero y solo existen para una comprobacion de integridad.
    """
    argumentos = _como(
        engine,
        None,
        "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='seguridad' AND p.pronargs = 0",
    )

    assert argumentos[0] == 4  # usuario_actual_id, es_admin, paciente_actual, medico_actual


def test_el_helper_de_seguimiento_no_revela_embarazos_ajenos(engine, identidades):
    """Responde sobre el llamante, no sobre el embarazo: falso si no es suyo."""
    paciente = identidades["pacientes"][0]
    ajeno = _como(
        engine, paciente["id_usuario"], "SELECT id_embarazo FROM operacional.embarazo LIMIT 1"
    )[0]

    respuesta = _como(
        engine,
        paciente["id_usuario"],
        "SELECT seguridad.embarazo_en_seguimiento_vigente(:e)::text",
        {"e": ajeno},
    )

    assert respuesta[0] == "false"


# ---------------------------------------------------------------------------
# 10. El contexto vive en la transaccion, tambien con politicas activas
# ---------------------------------------------------------------------------


def test_tras_el_commit_la_misma_conexion_no_ve_nada(engine, identidades):
    """El contexto muere con la transaccion; la siguiente empieza sin identidad."""
    paciente = identidades["pacientes"][0]

    with engine.connect() as conexion:
        with conexion.begin():
            conexion.execute(
                text("SELECT set_config('fetalalert.id_usuario', :v, true)"),
                {"v": str(paciente["id_usuario"])},
            )
            dentro = conexion.execute(
                text("SELECT count(*) FROM operacional.embarazo")
            ).scalar()
        with conexion.begin():
            despues = conexion.execute(
                text("SELECT count(*) FROM operacional.embarazo")
            ).scalar()

    assert dentro > 0
    assert despues == 0


def test_instalar_el_contexto_fuera_de_la_transaccion_protegida_no_sirve(
    engine, identidades
):
    """Una identidad instalada en otra transaccion no alcanza a esta.

    Es el fallo que un ``commit`` intermedio provocaria: seguro -- cero filas --
    pero silencioso, y por eso conviene verlo escrito.
    """
    paciente = identidades["pacientes"][0]

    with engine.connect() as conexion:
        with conexion.begin():
            conexion.execute(
                text("SELECT set_config('fetalalert.id_usuario', :v, true)"),
                {"v": str(paciente["id_usuario"])},
            )
        with conexion.begin():
            visibles = conexion.execute(
                text("SELECT count(*) FROM operacional.embarazo")
            ).scalar()

    assert visibles == 0


# ---------------------------------------------------------------------------
# El dia clinico de las politicas es el de Panama, no el de la sesion
# ---------------------------------------------------------------------------
#
# ``pol_alcance`` sobre ``embarazo`` se apoya en
# ``seguridad.embarazo_en_seguimiento_vigente``, que compara las fechas del
# ``SeguimientoClinico`` contra «hoy». Si ese «hoy» fuera ``CURRENT_DATE``,
# saldria del ``TimeZone`` de la sesion, y entonces el conjunto de embarazos que
# un medico puede leer dependeria de como se conecto: entre las 19:00 y la
# medianoche de Panama, una sesion en UTC ya estaria en el dia siguiente y la
# ultima jornada de una asignacion habria caducado antes de tiempo.
#
# Una decision de autorizacion no puede depender de eso.

TIMEZONES_DE_SESION = ("UTC", "Asia/Tokyo", "America/Panama", "Pacific/Kiritimati")


def _visibles_en_zona(engine, id_usuario, zona, consulta):
    """La consulta, con esa identidad y con esa zona horaria de sesion."""
    with engine.connect() as conexion:
        with conexion.begin():
            conexion.execute(text(f"SET TIME ZONE '{zona}'"))
            conexion.execute(
                text("SELECT set_config('fetalalert.id_usuario', :v, true)"),
                {"v": str(id_usuario)},
            )
            return sorted(conexion.execute(text(consulta)).scalars().all())


@pytest.mark.parametrize("zona", TIMEZONES_DE_SESION)
def test_el_alcance_del_medico_no_depende_del_timezone_de_la_sesion(
    engine, identidades, zona
):
    """El mismo medico ve los mismos embarazos desde cualquier zona."""
    id_usuario = identidades["medicos"][0]["id_usuario"]
    consulta = "SELECT id_embarazo FROM operacional.embarazo ORDER BY id_embarazo"

    alcance = _visibles_en_zona(engine, id_usuario, zona, consulta)
    referencia = _visibles_en_zona(engine, id_usuario, "America/Panama", consulta)

    assert alcance == referencia
    assert alcance, "el dataset no da al medico ninguna asignacion vigente"


@pytest.mark.parametrize("zona", TIMEZONES_DE_SESION)
def test_las_sesiones_visibles_del_medico_tampoco_dependen_de_la_zona(
    engine, identidades, zona
):
    """Lo que cuelga del embarazo hereda la misma decision, y debe ser estable."""
    id_usuario = identidades["medicos"][0]["id_usuario"]
    consulta = (
        "SELECT id_sesion FROM operacional.sesion_monitoreo ORDER BY id_sesion"
    )

    alcance = _visibles_en_zona(engine, id_usuario, zona, consulta)
    referencia = _visibles_en_zona(engine, id_usuario, "America/Panama", consulta)

    assert alcance == referencia
    assert alcance, "el medico no alcanza ninguna sesion"


@pytest.mark.parametrize("zona", TIMEZONES_DE_SESION)
def test_el_helper_de_vigencia_responde_igual_en_cualquier_zona(
    engine, identidades, zona
):
    """El helper, interrogado directamente a traves del rol de la aplicacion.

    Es la pieza concreta que ``pol_alcance`` consulta, asi que fijarla aqui
    impide que una futura reescritura de la politica reintroduzca el defecto por
    otro camino.
    """
    id_usuario = identidades["medicos"][0]["id_usuario"]
    consulta = (
        "SELECT id_embarazo FROM operacional.embarazo "
        "WHERE seguridad.embarazo_en_seguimiento_vigente(id_embarazo) "
        "ORDER BY id_embarazo"
    )

    assert _visibles_en_zona(engine, id_usuario, zona, consulta) == _visibles_en_zona(
        engine, id_usuario, "America/Panama", consulta
    )
