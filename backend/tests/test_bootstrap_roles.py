"""El bootstrap de roles: qué crea, qué se niega a contener y qué verifica.

Estas pruebas no tocan PostgreSQL. Lo que afirman es el contrato del artefacto
versionado -- ``scripts/bootstrap_roles.sql`` -- y la lógica pura de
``app.db.roles``. El comportamiento real contra un clúster vive en
``test_bootstrap_postgresql.py``, que se omite sin una base desechable.

La razón de separarlo: que el archivo no contenga una contraseña es una
propiedad del archivo, comprobable siempre y en cualquier máquina. No debe
depender de que alguien tenga PostgreSQL levantado.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.db.roles import (
    CAPACIDADES_PROHIBIDAS,
    MEMBRESIAS_DEL_MIGRADOR,
    ROL_MANTENIMIENTO,
    ROL_PROVISION_OWNER,
    ROL_RLS_OWNER,
    ROLES_DUENIOS_DE_HELPERS,
    ROLES_NOLOGIN,
    ROLES_RUNTIME,
    EstadoDeRol,
    InformeDeRoles,
    OpcionesDeMembresia,
)

RAIZ = Path(__file__).resolve().parents[2]
ARCHIVO_SQL = RAIZ / "scripts" / "bootstrap_roles.sql"
ARCHIVO_RUNNER = RAIZ / "scripts" / "bootstrap_roles.py"


@pytest.fixture(scope="module")
def sql() -> str:
    return ARCHIVO_SQL.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sql_ejecutable(sql: str) -> str:
    """El archivo sin sus comentarios.

    La cabecera nombra a propósito los roles que este bootstrap **no** crea, y
    esa explicación es justamente lo que se quiere conservar. Una prueba que
    buscara esos nombres en el archivo entero castigaría la documentación en
    lugar del comportamiento, así que lo que se inspecciona es lo que PostgreSQL
    llega a ejecutar.
    """
    return "\n".join(
        linea for linea in sql.splitlines() if not linea.lstrip().startswith("--")
    )


# ---------------------------------------------------------------------------
# 1. Dónde vive, y que exista
# ---------------------------------------------------------------------------


def test_el_bootstrap_vive_en_el_directorio_scripts_del_repositorio():
    """``scripts/`` es la convención del repositorio; no se abre una segunda.

    El repositorio ya coloca sus comandos en ``scripts/`` -- el generador del
    dataset, el cargador, el nodo edge y el ETL. Introducir ``backend/scripts/``
    solo para este archivo partiría esa convención en dos sin ninguna razón
    técnica: el bootstrap no es código de la aplicación, es un comando de
    operación como los otros cuatro.
    """
    assert ARCHIVO_SQL.is_file()
    assert ARCHIVO_RUNNER.is_file()
    assert not (RAIZ / "backend" / "scripts").exists()


# ---------------------------------------------------------------------------
# 2. Lo que el archivo no puede contener
# ---------------------------------------------------------------------------


def test_no_contiene_ninguna_contrasena(sql):
    """Un secreto versionado deja de ser un secreto."""
    assert "PASSWORD" not in sql.upper()
    assert "ENCRYPTED" not in sql.upper()


def test_no_crea_ningun_rol_con_login(sql):
    """Los roles que se conectan son responsabilidad del despliegue o del CI."""
    creaciones = re.findall(r"CREATE ROLE[^']*", sql)
    assert creaciones, "se esperaba al menos una creación de rol"
    for creacion in creaciones:
        assert "NOLOGIN" in creacion
        # Retirado NOLOGIN, no debe quedar ningún LOGIN suelto.
        assert "LOGIN" not in creacion.replace("NOLOGIN", "")


@pytest.mark.parametrize("rol", sorted(ROLES_RUNTIME))
def test_no_menciona_los_roles_de_conexion(sql_ejecutable, rol):
    """fetalalert_api, _etl, _powerbi y el de pruebas no se crean aquí.

    La cabecera sí los nombra, para decir precisamente que no se crean aquí; lo
    que no puede es tocarlos.
    """
    assert rol not in sql_ejecutable


def test_no_concede_privilegios_sobre_objetos(sql_ejecutable):
    """Schemas, tablas, secuencias y funciones son territorio de Alembic.

    Las únicas concesiones admitidas son membresías de rol -- cluster-globales,
    y por tanto imposibles de poner en una migración. Un ``GRANT ... ON algo``
    sería un privilegio sobre un objeto y no pertenece aquí.
    """
    concesiones = re.findall(r"GRANT\s+[^']*", sql_ejecutable)
    assert concesiones, "se esperaba al menos una concesión de membresía"
    for concesion in concesiones:
        assert " ON " not in f" {concesion.upper()} ", concesion
    for prohibido in ("ON SCHEMA", "ON TABLE", "ON SEQUENCE", "ON FUNCTION", "ON ALL"):
        assert prohibido not in sql_ejecutable.upper()


def test_no_cambia_propietarios_ni_toca_bases(sql_ejecutable):
    """Sin ALTER OWNER, y sin crear o borrar bases ni roles.

    Se mira el SQL ejecutable: la cabecera menciona ``ALTER FUNCTION ... OWNER
    TO`` para explicar por qué hacen falta las opciones de membresía, y esa
    explicación debe poder escribirse.
    """
    mayusculas = sql_ejecutable.upper()
    assert "OWNER TO" not in mayusculas
    assert "CREATE DATABASE" not in mayusculas
    assert "DROP DATABASE" not in mayusculas
    assert "DROP ROLE" not in mayusculas


def test_no_lleva_metacomandos_de_psql(sql):
    """El mismo texto lo ejecutan psql y el runner de Python.

    Un meta-comando haría que el runner fallara o, peor, que ambos caminos
    ejecutaran cosas distintas.
    """
    for linea in sql.splitlines():
        assert not linea.lstrip().startswith(chr(92)), linea


# ---------------------------------------------------------------------------
# 3. Lo que el archivo sí hace
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rol", sorted(ROLES_NOLOGIN))
def test_crea_cada_rol_aprobado(sql, rol):
    assert rol in sql


def test_crea_exactamente_los_tres_roles_aprobados(sql):
    """Ni uno más: un rol extra es superficie que nadie pidió."""
    nombrados = set(re.findall(r"'(fetalalert_[a-z_]+)'", sql))
    assert nombrados == set(ROLES_NOLOGIN)


@pytest.mark.parametrize("capacidad", sorted(set(CAPACIDADES_PROHIBIDAS.values())))
def test_niega_explicitamente_cada_capacidad(sql, capacidad):
    """Los NO* son el valor por omisión, pero se escriben igualmente.

    Así el contrato está en el archivo y no depende de que quien lo lea recuerde
    cuáles son los defaults de PostgreSQL.
    """
    assert f"NO{capacidad}" in sql


def test_es_idempotente_por_construccion(sql):
    """Crea lo que falta y verifica lo que existe; nunca altera a ciegas."""
    assert "IF NOT FOUND" in sql
    assert "RAISE EXCEPTION" in sql
    assert "ALTER ROLE" not in sql.upper()


def test_la_membresia_se_concede_a_current_user(sql):
    """El nombre del migrador cambia entre entornos; su identidad no."""
    assert "current_user" in sql
    assert "GRANT %I TO %I WITH ADMIN FALSE, INHERIT %s, SET %s" in sql


@pytest.mark.parametrize("rol,opciones", sorted(MEMBRESIAS_DEL_MIGRADOR.items()))
def test_cada_membresia_declara_sus_opciones_exactas(sql_ejecutable, rol, opciones):
    """PostgreSQL 16 separa INHERIT de SET, y aquí se escriben las dos.

    Un rol con CREATEROLE queda como miembro de lo que crea, pero con
    ``inherit_option = false`` y ``set_option = false``. Sin estas concesiones
    explícitas, ``ALTER FUNCTION ... OWNER TO`` fallaría con «must be able to
    SET ROLE» y la migración de la sub-fase 3 no podría dejar los helpers en su
    propietario previsto.
    """
    hereda, puede_set = opciones
    fila = f"('{rol}',"
    linea = next(l for l in sql_ejecutable.splitlines() if fila in l)

    assert str(hereda).lower() in linea
    assert linea.count("true") + linea.count("false") == 2


def test_los_duenos_de_helpers_no_heredan_pero_si_pueden_asumirse():
    """INHERIT FALSE, SET TRUE: lo mínimo para transferir la propiedad.

    Heredarlos haría que el migrador arrastrara sus privilegios en cada
    sentencia ordinaria; no poder asumirlos impediría crear los helpers.
    """
    for rol in ROLES_DUENIOS_DE_HELPERS:
        hereda, puede_set = MEMBRESIAS_DEL_MIGRADOR[rol]
        assert hereda is False
        assert puede_set is True


def test_mantenimiento_si_se_hereda():
    """Una policy dirigida ``TO`` un rol se evalúa contra lo que se hereda.

    Sin ``INHERIT TRUE`` la policy de mantenimiento no se aplicaría y el
    cargador del dataset no podría escribir.
    """
    hereda, _ = MEMBRESIAS_DEL_MIGRADOR[ROL_MANTENIMIENTO]

    assert hereda is True


def test_el_bootstrap_no_usa_pg_has_role(sql):
    """``pg_has_role`` responde verdadero para todo superusuario.

    Usarlo para decidir si conceder ocultaría que el bootstrap nunca corrió en
    un clúster administrado por un superusuario.
    """
    assert "pg_has_role" not in sql


# ---------------------------------------------------------------------------
# 4. La lógica de evaluación, sin base de datos
# ---------------------------------------------------------------------------


def _opciones(hereda=True, puede_set=True, administra=False) -> OpcionesDeMembresia:
    return OpcionesDeMembresia(hereda=hereda, puede_set=puede_set, administra=administra)


MIGRADOR = "migrador_ficticio"


def _membresias_correctas(migrador: str = MIGRADOR) -> dict:
    """Las tres membresías que el bootstrap deja, con sus opciones exactas."""
    return {
        rol: {migrador: OpcionesDeMembresia(hereda=h, puede_set=s, administra=True)}
        for rol, (h, s) in MEMBRESIAS_DEL_MIGRADOR.items()
    }


def _informe(
    roles: dict | None = None,
    membresias: dict | None = None,
    pertenencias: dict | None = None,
    alcance: dict | None = None,
    migrador: str | None = MIGRADOR,
) -> InformeDeRoles:
    if roles is None:
        roles = {rol: () for rol in ROLES_NOLOGIN}
    if membresias is None:
        membresias = _membresias_correctas(migrador) if migrador else {}
    return InformeDeRoles(
        roles={
            nombre: EstadoDeRol(nombre=nombre, capacidades_indebidas=capacidades)
            for nombre, capacidades in roles.items()
        },
        membresias=membresias,
        pertenencias=pertenencias or {rol: frozenset() for rol in ROLES_NOLOGIN},
        alcance_de_runtime=alcance or {},
        migrador_esperado=migrador,
    )


def test_un_cluster_con_los_tres_roles_y_sus_membresias_es_conforme():
    informe = _informe()

    assert informe.conforme, informe.motivo
    assert informe.motivo == ""
    assert informe.ausentes == frozenset()


def test_un_rol_ausente_no_es_conforme_y_el_motivo_lo_nombra():
    informe = _informe(roles={ROL_RLS_OWNER: (), ROL_PROVISION_OWNER: ()})

    assert not informe.conforme
    assert ROL_MANTENIMIENTO in informe.motivo
    assert "bootstrap_roles.py" in informe.motivo


@pytest.mark.parametrize("capacidad", sorted(set(CAPACIDADES_PROHIBIDAS.values())))
def test_cualquier_capacidad_indebida_rompe_la_conformidad(capacidad):
    roles = {rol: () for rol in ROLES_NOLOGIN}
    roles[ROL_RLS_OWNER] = (capacidad,)
    informe = _informe(roles=roles)

    assert not informe.conforme
    assert capacidad in informe.motivo
    assert ROL_RLS_OWNER in informe.motivo


def test_el_informe_distingue_ausente_de_no_conforme():
    roles = {ROL_RLS_OWNER: ("LOGIN",), ROL_PROVISION_OWNER: ()}
    informe = _informe(roles=roles)

    assert informe.ausentes == frozenset({ROL_MANTENIMIENTO})
    assert informe.no_conformes == frozenset({ROL_RLS_OWNER})


def test_los_dos_duenos_de_helpers_son_roles_distintos():
    """Separarlos es lo que impide que el UPDATE de provisión lo herede nadie más."""
    assert ROL_RLS_OWNER != ROL_PROVISION_OWNER
    assert {ROL_RLS_OWNER, ROL_PROVISION_OWNER} <= ROLES_NOLOGIN


# ---------------------------------------------------------------------------
# 5. Ningun rol de runtime puede asumir ni heredar un rol privilegiado
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("runtime", sorted(ROLES_RUNTIME))
@pytest.mark.parametrize("privilegiado", sorted(ROLES_NOLOGIN))
def test_una_membresia_de_runtime_rompe_la_conformidad(runtime, privilegiado):
    """El agujero que esto cierra: un nombre inocente con privilegio real.

    Un rol llamado ``fetalalert_api`` que sea miembro de
    ``fetalalert_rls_owner`` alcanza directamente lo que los helpers exponen de
    forma acotada. Comprobar nombres no bastaría; lo que se comprueba es la
    membresía.
    """
    membresias = _membresias_correctas()
    membresias[privilegiado][runtime] = _opciones()
    informe = _informe(membresias=membresias)

    assert not informe.conforme
    assert f"{runtime} es miembro inesperado de {privilegiado}" in informe.motivo


def test_la_membresia_del_migrador_no_rompe_la_conformidad():
    """El migrador sí debe tenerlas; es quien crea los helpers."""
    informe = _informe()

    assert informe.conforme, informe.motivo
    assert informe.miembros_de_mantenimiento == frozenset({MIGRADOR})


# ---------------------------------------------------------------------------
# 6. El informe exige las membresias, y rechaza todo lo demas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("faltante", sorted(MEMBRESIAS_DEL_MIGRADOR))
def test_una_membresia_ausente_impide_declarar_conformidad(faltante):
    """``verificar`` no puede decir conforme=true si nadie podría migrar.

    Es el estado exacto en el que la migración fallaría después: los roles
    existen, pero el migrador no puede transferirles la propiedad.
    """
    membresias = _membresias_correctas()
    del membresias[faltante]
    informe = _informe(membresias=membresias)

    assert not informe.conforme
    assert f"no es miembro de {faltante}" in informe.motivo
    assert "bootstrap_roles.py" in informe.motivo


@pytest.mark.parametrize("rol", sorted(MEMBRESIAS_DEL_MIGRADOR))
@pytest.mark.parametrize("campo", ["hereda", "puede_set"])
def test_una_opcion_de_membresia_distinta_de_la_esperada_se_rechaza(rol, campo):
    """Ni de menos ni de más: una opción más ancha concede lo que no toca."""
    hereda, puede_set = MEMBRESIAS_DEL_MIGRADOR[rol]
    valores = {"hereda": hereda, "puede_set": puede_set}
    valores[campo] = not valores[campo]

    membresias = _membresias_correctas()
    membresias[rol][MIGRADOR] = OpcionesDeMembresia(**valores, administra=True)
    informe = _informe(membresias=membresias)

    assert not informe.conforme
    assert "se esperaba" in informe.motivo


@pytest.mark.parametrize("rol", sorted(ROLES_NOLOGIN))
def test_un_miembro_arbitrario_y_desconocido_tambien_se_rechaza(rol):
    """No es una lista negra de nombres conocidos.

    Un rol sin relación con el proyecto que tenga una concesión sobre un dueño
    de helpers es exactamente igual de peligroso, y más fácil de pasar por alto.
    """
    membresias = _membresias_correctas()
    membresias[rol]["un_rol_cualquiera"] = _opciones()
    informe = _informe(membresias=membresias)

    assert not informe.conforme
    assert "un_rol_cualquiera es miembro inesperado" in informe.motivo


@pytest.mark.parametrize("runtime", sorted(ROLES_RUNTIME))
@pytest.mark.parametrize("objetivo", sorted(ROLES_NOLOGIN))
def test_un_alcance_indirecto_desde_runtime_se_rechaza(runtime, objetivo):
    """La cadena A -> B -> rol técnico no aparece entre los miembros directos."""
    informe = _informe(alcance={runtime: frozenset({objetivo})})

    assert not informe.conforme
    assert f"{runtime} alcanza {objetivo}" in informe.motivo


@pytest.mark.parametrize("rol", sorted(ROLES_NOLOGIN))
def test_un_rol_tecnico_miembro_de_otro_rol_se_rechaza(rol):
    """Un helper se ejecuta con la identidad de su dueño.

    Si ese dueño hereda otro rol, el helper alcanza también lo que ese rol
    alcance, que es justo lo contrario de un privilegio acotado.
    """
    pertenencias = {r: frozenset() for r in ROLES_NOLOGIN}
    pertenencias[rol] = frozenset({"otro_rol"})
    informe = _informe(pertenencias=pertenencias)

    assert not informe.conforme
    assert f"{rol} es miembro de otro_rol" in informe.motivo


def test_sin_migrador_esperado_no_se_exigen_membresias():
    """``None`` describe el clúster sin imponer el requisito."""
    informe = _informe(membresias={}, migrador=None)

    assert informe.conforme, informe.motivo


# ---------------------------------------------------------------------------
# 7. El contrato de ADMIN es explicito
# ---------------------------------------------------------------------------


def test_el_bootstrap_concede_admin_false_de_forma_explicita(sql_ejecutable):
    """PostgreSQL 16 conserva la opción existente si se omite.

    Dejarlo implícito sería confiar en un valor invisible. El bootstrap no
    concede ADMIN a nadie: el migrador ya lo tiene, y solo por haber creado los
    roles.
    """
    assert "WITH ADMIN FALSE" in sql_ejecutable
    assert "ADMIN TRUE" not in sql_ejecutable.upper()


def test_el_bootstrap_documenta_que_otro_migrador_no_puede_reejecutarlo(sql):
    """Quien no creó los roles no tiene ADMIN, y PostgreSQL lo rechaza.

    Es la consecuencia que rompía el orden del CI, así que queda escrita donde
    alguien la vaya a leer antes de reordenar los pasos.
    """
    assert "denied to grant role" in sql.lower()
    assert "ADMIN option" in sql


def test_los_duenos_de_helpers_son_el_subconjunto_que_posee_objetos():
    assert ROLES_DUENIOS_DE_HELPERS == {ROL_RLS_OWNER, ROL_PROVISION_OWNER}
    assert ROL_MANTENIMIENTO not in ROLES_DUENIOS_DE_HELPERS
    assert not (ROLES_RUNTIME & ROLES_NOLOGIN)


# ---------------------------------------------------------------------------
# 8. ADMIN efectivo: del migrador y de nadie mas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rol", sorted(ROLES_NOLOGIN))
@pytest.mark.parametrize("intruso", [*sorted(ROLES_RUNTIME), "un_rol_cualquiera"])
def test_cualquier_otro_administrador_rompe_la_conformidad(rol, intruso):
    """ADMIN es el derecho a conceder el rol a un tercero.

    El migrador lo conserva por haber creado los roles -- la concesion implicita
    de CREATEROLE lo trae con ``admin_option = true`` --, y eso es lo que le
    permite reejecutar el bootstrap. Cualquier otro que lo tuviera podria
    entregar un dueno de helpers sin que el bootstrap se enterase.
    """
    membresias = _membresias_correctas()
    membresias[rol][intruso] = _opciones(administra=True)
    informe = _informe(membresias=membresias)

    assert not informe.conforme
    assert f"{intruso} administra {rol}" in informe.motivo


def test_el_migrador_si_puede_administrar():
    """Sin ADMIN efectivo no podria reejecutar el bootstrap de forma idempotente."""
    informe = _informe()

    assert informe.administradores_indebidos() == ()
    assert informe.conforme, informe.motivo


@pytest.mark.parametrize("rol", sorted(ROLES_NOLOGIN))
def test_un_miembro_sin_admin_no_se_reporta_como_administrador(rol):
    """La comprobacion mira ``admin_option``, no la mera membresia."""
    membresias = _membresias_correctas()
    membresias[rol]["otro"] = _opciones(administra=False)
    informe = _informe(membresias=membresias)

    assert informe.administradores_indebidos() == ()
    # Sigue siendo no conforme, pero por miembro inesperado, no por ADMIN.
    assert "otro es miembro inesperado" in informe.motivo
