"""Qué no puede ser la credencial con la que la API se conecta (SCRUM-98).

La seguridad por filas vale exactamente lo que valga el rol que se conecta. Un
``SUPERUSER`` ignora toda política, un rol con ``BYPASSRLS`` ignora toda
política, y el propietario de una tabla ignora las suyas salvo ``FORCE ROW
LEVEL SECURITY``. Un despliegue que apunte la API a la credencial que posee el
esquema obtiene pruebas en verde y cero aislamiento.

Dos cosas se comprueban aquí, sin abrir ninguna conexión:

* **qué cuenta como violación**, incluidos los privilegios efectivos que llegan
  por membresía -- un rol llamado ``fetalalert_api`` que pueda hacer ``SET ROLE``
  a un propietario no está restringido, por inocente que sea su nombre;
* **que un ambiente desplegado falle cerrado siempre**, incluso cuando la
  comprobación no llega a completarse.

La consulta real contra el catálogo se ejercita en
``test_bootstrap_postgresql.py``.
"""

from __future__ import annotations

import logging
import re

import pytest

from app.config import AMBIENTES_PERMITIDOS
from app.db import privilegios
from app.db.privilegios import (
    InformeDePrivilegios,
    PrivilegiosNoVerificables,
    RuntimeNoRestringido,
    exigir_runtime_restringido,
    verificar_al_arrancar,
)

AMBIENTES_DESPLEGADOS = ("production", "staging", "produccion", "prod", "cualquier-otro")

# Las siete propiedades, una a una. Cada nombre es el campo del informe.
VIOLACIONES = tuple(InformeDePrivilegios.__annotations__)


def informe(**violaciones: bool) -> InformeDePrivilegios:
    """Un informe con las violaciones indicadas y el resto en falso."""
    campos = dict.fromkeys(VIOLACIONES, False)
    campos.update(violaciones)
    return InformeDePrivilegios(**campos)


class _MotorDeLaAplicacion:
    """Lo único que ``verificar_al_arrancar`` necesita del engine de la API.

    Solo le pide la URL: construye el suyo a partir de ella, con su propio
    ``connect_timeout``, para no tocar el pool de la aplicación.
    """

    url = "postgresql+psycopg://ficticio:ficticia@127.0.0.1:1/base_inexistente"


@pytest.fixture
def conexion_que_falla(monkeypatch):
    """Hace que el engine de la comprobación falle del modo indicado.

    Se intercepta ``create_engine`` y no el engine recibido, porque es el
    verificador el que conecta. Así el fallo es determinista y la prueba no
    depende de la red ni espera un timeout real.
    """
    construidos = []

    def instalar(error: Exception):
        class _Verificador:
            def connect(self):
                raise error

            def dispose(self):
                construidos.append("dispose")

        def falso(url, **kwargs):
            construidos.append(kwargs)
            return _Verificador()

        monkeypatch.setattr(privilegios, "create_engine", falso)
        return construidos

    return instalar


@pytest.fixture
def evaluacion(monkeypatch):
    """Sustituye la consulta al catálogo por un resultado fijo.

    Lo que se ejercita aquí es la decisión, no el SQL. Así estas pruebas corren
    en cualquier máquina y no dependen de que exista un clúster.
    """

    def instalar(resultado) -> None:
        def falso(conexion):
            if isinstance(resultado, Exception):
                raise resultado
            return resultado

        monkeypatch.setattr(privilegios, "evaluar_privilegios", falso)

    return instalar


# ---------------------------------------------------------------------------
# 1. Qué cuenta como violación
# ---------------------------------------------------------------------------


def test_son_once_propiedades_y_no_menos():
    """Si se añade una y no se prueba, esta aserción lo dice."""
    assert len(VIOLACIONES) == 11


def test_la_membresia_sin_opciones_tambien_es_una_violacion():
    """``MEMBER`` es prohibición adicional, no la señal de «puede asumir».

    Una membresía con INHERIT y SET apagados no concede nada hoy, pero
    encenderlos es un solo GRANT. Ninguna credencial de runtime debería
    tenerla.
    """
    resultado = informe(tiene_membresia_privilegiada=True)

    assert not resultado.restringido
    assert "un solo" in resultado.violaciones[0]


def test_un_rol_sin_ninguna_de_las_propiedades_esta_restringido():
    assert informe().restringido
    assert informe().violaciones == ()


@pytest.mark.parametrize("violacion", VIOLACIONES)
def test_cada_propiedad_por_separado_rompe_la_restriccion(violacion):
    resultado = informe(**{violacion: True})

    assert not resultado.restringido
    assert len(resultado.violaciones) == 1


def test_varias_propiedades_se_reportan_todas():
    """Un mensaje que nombrara solo la primera dejaría trabajo oculto."""
    resultado = informe(es_superusuario=True, omite_rls=True, es_propietario=True)

    assert len(resultado.violaciones) == 3


def test_el_motivo_del_superusuario_explica_por_que_importa():
    assert "omite toda política RLS" in informe(es_superusuario=True).violaciones[0]


def test_el_motivo_del_propietario_menciona_force_row_level_security():
    """Es la excepción exacta que decide si el propietario queda sujeto."""
    assert "FORCE ROW LEVEL SECURITY" in informe(es_propietario=True).violaciones[0]


# ---------------------------------------------------------------------------
# 2. Privilegio efectivo, no comparacion de nombres
# ---------------------------------------------------------------------------


def test_poder_asumir_al_propietario_es_una_violacion():
    """Una restricción que el propio rol puede levantar no es una restricción."""
    resultado = informe(puede_asumir_al_propietario=True)

    assert not resultado.restringido
    assert "SET ROLE" in resultado.violaciones[0]


@pytest.mark.parametrize(
    "campo",
    [
        "hereda_un_rol_privilegiado",
        "puede_asumir_un_rol_privilegiado",
        "tiene_membresia_privilegiada",
    ],
)
def test_alcanzar_un_rol_tecnico_privilegiado_es_una_violacion(campo):
    """Herencia y ``SET ROLE`` son dos caminos, y los dos cuentan."""
    resultado = informe(**{campo: True})

    assert not resultado.restringido


def test_un_rol_puede_ser_propietario_solo_por_herencia():
    """``pg_has_role(..., 'USAGE')`` cubre la propiedad heredada.

    El campo no dice «es el propietario literal» sino «alcanza lo que el
    propietario alcanza», que es la propiedad que importa para RLS.
    """
    assert "por herencia" in informe(es_propietario=True).violaciones[0]


# ---------------------------------------------------------------------------
# 3. El ambiente desplegado falla cerrado, siempre
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ambiente", AMBIENTES_DESPLEGADOS)
@pytest.mark.parametrize("violacion", VIOLACIONES)
def test_en_un_ambiente_desplegado_cualquier_violacion_impide_arrancar(
    evaluacion, ambiente, violacion
):
    evaluacion(informe(**{violacion: True}))

    with pytest.raises(RuntimeNoRestringido) as excepcion:
        exigir_runtime_restringido(None, ambiente)

    assert "DATABASE_URL" in str(excepcion.value)


def test_la_consulta_usa_pg_has_role_con_set_y_no_con_member():
    """La corrección: ``MEMBER`` no responde «puede asumir el rol».

    Reproducido en PostgreSQL 16 sobre la misma concesión:
    ``INHERIT FALSE, SET FALSE`` da MEMBER=true, USAGE=false, SET=false. Usar
    ``MEMBER`` para decidir «puede hacer SET ROLE» sobre-reporta.
    """
    consulta = " ".join(str(privilegios._CONSULTA).split())
    tipo = dict(
        re.findall(r"pg_has_role\([^)]*'(MEMBER|USAGE|SET)'\) \) AS (\w+)", consulta)[::1]
    )
    # (privilegio -> campo) invertido a (campo -> privilegio)
    por_campo = {
        campo: privilegio
        for privilegio, campo in re.findall(
            r"pg_has_role\([^)]*'(MEMBER|USAGE|SET)'\s*\)\s*\)\s*AS\s+(\w+)", consulta
        )
    }

    assert por_campo["puede_asumir_al_propietario"] == "SET"
    assert por_campo["puede_asumir_un_rol_privilegiado"] == "SET"
    assert por_campo["tiene_membresia_privilegiada"] == "MEMBER"
    assert por_campo["es_propietario"] == "USAGE"
    assert por_campo["hereda_un_rol_privilegiado"] == "USAGE"


# ---------------------------------------------------------------------------
# 3bis. Dos timeouts, porque son dos esperas distintas
# ---------------------------------------------------------------------------


def test_la_comprobacion_fija_un_connect_timeout_propio(conexion_que_falla):
    """``statement_timeout`` no acota establecer la conexión.

    ``connect_timeout`` es de libpq y solo puede fijarse al construir el engine,
    así que la comprobación construye el suyo. Se afirma sobre el engine que
    realmente se usa, interceptando su construcción.
    """
    construidos = conexion_que_falla(OSError("connection refused"))

    with pytest.raises(PrivilegiosNoVerificables):
        verificar_al_arrancar(_MotorDeLaAplicacion(), "production")

    argumentos = construidos[0]["connect_args"]

    assert argumentos["connect_timeout"] == privilegios.SEGUNDOS_MAXIMOS_DE_CONEXION
    assert privilegios.SEGUNDOS_MAXIMOS_DE_CONEXION > 0
    assert "dispose" in construidos, "el engine efímero debe cerrarse"


def test_la_comprobacion_no_altera_el_engine_de_la_aplicacion():
    """El pool de la API conserva la configuración con la que se creó."""
    from app.db.session import engine as engine_de_la_api

    assert "connect_timeout" not in (engine_de_la_api.dialect.__dict__ or {})
    assert engine_de_la_api.pool.__class__.__name__ != "NullPool"


def test_los_dos_timeouts_son_cotas_distintas_y_ambas_existen():
    """Uno acota conectar y el otro consultar; ninguno cubre al otro."""
    import inspect

    assert isinstance(privilegios.SEGUNDOS_MAXIMOS_DE_CONEXION, int)
    assert privilegios.SEGUNDOS_MAXIMOS_DE_CONEXION > 0
    assert privilegios.TIEMPO_MAXIMO_DE_COMPROBACION.endswith("s")

    fuente_consulta = inspect.getsource(privilegios.evaluar_privilegios)
    fuente_arranque = inspect.getsource(privilegios.verificar_al_arrancar)

    assert "statement_timeout" in fuente_consulta
    assert "connect_timeout" not in fuente_consulta
    assert "connect_timeout" in fuente_arranque


@pytest.mark.parametrize("ambiente", AMBIENTES_DESPLEGADOS)
def test_en_un_ambiente_desplegado_una_base_inalcanzable_impide_arrancar(
    conexion_que_falla, ambiente
):
    """La base puede estar caída ahora y volver en un segundo.

    Un proceso que arrancara igualmente serviría, durante toda su vida, con una
    credencial que nadie llegó a comprobar.
    """
    conexion_que_falla(OSError("connection refused"))

    with pytest.raises(PrivilegiosNoVerificables) as excepcion:
        verificar_al_arrancar(_MotorDeLaAplicacion(), ambiente)

    assert "no es una comprobación superada" in str(excepcion.value)


@pytest.mark.parametrize(
    "error",
    [
        OSError("connection refused"),
        TimeoutError("statement timeout"),
        RuntimeError("catalogo ilegible"),
        PrivilegiosNoVerificables("resultado incompleto"),
    ],
    ids=["conexion", "timeout", "consulta", "incompleto"],
)
def test_todo_fallo_de_la_comprobacion_impide_arrancar_en_desplegado(
    conexion_que_falla, error
):
    conexion_que_falla(error)

    with pytest.raises(PrivilegiosNoVerificables):
        verificar_al_arrancar(_MotorDeLaAplicacion(), "production")


@pytest.mark.parametrize("ambiente", AMBIENTES_DESPLEGADOS)
def test_el_fallo_desplegado_no_expone_la_url_ni_el_error_del_driver(
    conexion_que_falla, ambiente
):
    """El mensaje del driver lleva la sentencia, sus parámetros y la URL."""
    secreto = "clave_ficticia_que_no_debe_aparecer"
    conexion_que_falla(
        OSError(f'connection to "postgresql://usuario:{secreto}@host/base" failed')
    )

    with pytest.raises(PrivilegiosNoVerificables) as excepcion:
        verificar_al_arrancar(_MotorDeLaAplicacion(), ambiente)

    mensaje = str(excepcion.value)
    assert secreto not in mensaje
    assert "postgresql://" not in mensaje
    assert "usuario" not in mensaje
    # Solo viaja el nombre de la clase de la excepción.
    assert "OSError" in mensaje


def test_un_resultado_incompleto_no_se_toma_por_aprobado(evaluacion):
    """Una pregunta sin responder no es una comprobación superada."""
    evaluacion(PrivilegiosNoVerificables("el catálogo devolvió un resultado incompleto"))

    with pytest.raises(PrivilegiosNoVerificables):
        exigir_runtime_restringido(None, "production")


# ---------------------------------------------------------------------------
# 4. La permisividad transitoria, ya cerrada
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ambiente", sorted(AMBIENTES_PERMITIDOS))
def test_todo_ambiente_falla_cerrado_ante_un_runtime_no_restringido(
    evaluacion, ambiente
):
    """Sin monkeypatch: el valor desplegado de la constante es el que rige.

    Mientras duro la transicion, un ambiente simulado registraba la violacion y
    continuaba, porque la revision que concede privilegios al rol restringido
    todavia no existia y apuntar la API a ese rol la habria dejado sin leer una
    sola fila. Esa tolerancia tenia una condicion escrita -- no llegar al commit
    final del ticket -- y aqui se comprueba que se cumplio.
    """
    evaluacion(informe(es_superusuario=True))

    with pytest.raises(RuntimeNoRestringido):
        exigir_runtime_restringido(None, ambiente)


def test_la_permisividad_transitoria_quedo_en_falso():
    """La linea que cierra la deuda, afirmada directamente."""
    assert privilegios.PERMISIVIDAD_TRANSITORIA_SCRUM98 is False


@pytest.mark.parametrize("ambiente", sorted(AMBIENTES_PERMITIDOS))
def test_la_rama_tolerante_sigue_siendo_alcanzable_y_es_ruidosa(
    evaluacion, monkeypatch, ambiente, caplog
):
    """Lo que la tolerancia hacia cuando estaba activa, documentado.

    La rama se conserva en el codigo y se ejerce aqui poniendo la constante en
    cierto a proposito. Dos motivos: que quede escrito que nunca fue un respaldo
    silencioso -- registraba a nivel ERROR, nombraba el ambiente y decia que en
    produccion no se habria arrancado --, y que la prueba de arriba signifique
    algo. Sin esta, «falla cerrado» podria ser cierto por haber borrado la rama
    en vez de por haber cerrado la deuda.
    """
    monkeypatch.setattr(privilegios, "PERMISIVIDAD_TRANSITORIA_SCRUM98", True)
    evaluacion(informe(es_superusuario=True))

    with caplog.at_level(logging.ERROR, logger=privilegios.__name__):
        resultado = exigir_runtime_restringido(None, ambiente)

    assert not resultado.restringido
    texto = caplog.text
    assert "no está restringida" in texto
    assert ambiente in texto
    assert "se negaría a arrancar" in texto


def test_la_constante_sigue_declarada():
    """Se conserva junto con su rama: borrarla dejaria la prueba sin sujeto."""
    assert hasattr(privilegios, "PERMISIVIDAD_TRANSITORIA_SCRUM98")


@pytest.mark.parametrize("ambiente", sorted(AMBIENTES_PERMITIDOS))
def test_en_un_ambiente_simulado_una_base_inalcanzable_solo_avisa(
    conexion_que_falla, ambiente, caplog
):
    """Sin servidor a mano, la suite offline no puede exigir uno."""
    conexion_que_falla(OSError("connection refused"))

    with caplog.at_level(logging.WARNING, logger=privilegios.__name__):
        resultado = verificar_al_arrancar(_MotorDeLaAplicacion(), ambiente)

    assert resultado is None
    assert "impediría arrancar" in caplog.text


def test_el_ambiente_por_omision_del_proyecto_es_uno_simulado():
    """``development`` es el valor por omisión de ``app_env``."""
    assert "development" in AMBIENTES_PERMITIDOS


def test_el_conjunto_de_ambientes_permitidos_tiene_una_sola_definicion():
    """``app.loader`` lo reexporta desde ``app.config``, no lo declara otra vez."""
    from app.loader import AMBIENTES_PERMITIDOS as del_loader

    assert del_loader is AMBIENTES_PERMITIDOS


# ---------------------------------------------------------------------------
# 5. Lo que el mensaje no dice
# ---------------------------------------------------------------------------


def test_el_mensaje_no_nombra_al_rol_ni_a_la_base(evaluacion):
    """Un rechazo de privilegios no es sitio para publicar la topología."""
    evaluacion(informe(es_superusuario=True, es_propietario=True))

    with pytest.raises(RuntimeNoRestringido) as excepcion:
        exigir_runtime_restringido(None, "production")

    mensaje = str(excepcion.value)
    assert "postgresql://" not in mensaje
    assert "@" not in mensaje


# ---------------------------------------------------------------------------
# 6. Atributos de rol y capacidad de crear objetos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "campo,fragmento",
    [
        ("puede_crear_roles", "CREATEROLE"),
        ("puede_crear_bases", "CREATEDB"),
        ("puede_replicar", "REPLICATION"),
        ("puede_crear_objetos", "crear objetos"),
    ],
)
def test_cada_atributo_prohibido_tiene_su_motivo(campo, fragmento):
    resultado = informe(**{campo: True})

    assert not resultado.restringido
    assert fragmento in resultado.violaciones[0]


def test_la_consulta_vigila_los_cinco_schemas_del_proyecto_y_public():
    """``seguridad``, ``privado`` y ``publicacion`` aún no existen, y da igual.

    La consulta filtra por ``pg_namespace`` antes de preguntar, así que un
    schema ausente se ignora sin lanzar en vez de romper el arranque.
    """
    assert privilegios.ESQUEMAS_DEL_PROYECTO == (
        "operacional",
        "analitico",
        "seguridad",
        "privado",
        "publicacion",
    )
    assert "public" in privilegios.ESQUEMAS_VIGILADOS
    assert "pg_namespace" in str(privilegios._CONSULTA)


def test_la_consulta_vigila_todas_las_clases_de_objeto():
    """Tablas, vistas, materializadas, secuencias, particiones y foráneas."""
    assert set(privilegios.CLASES_VIGILADAS) == {"r", "v", "m", "S", "p", "f"}
    assert "pg_proc" in str(privilegios._CONSULTA)
    assert "nspowner" in str(privilegios._CONSULTA)


# ---------------------------------------------------------------------------
# 7. El ciclo de vida invoca la verificacion una vez y propaga el fallo
# ---------------------------------------------------------------------------


def _ejecutar_ciclo_de_vida(monkeypatch, efecto):
    """Recorre el lifespan de ``app.main`` con la verificación sustituida."""
    import asyncio

    from app import main

    llamadas = []

    def falso(engine, app_env):
        llamadas.append((engine, app_env))
        if isinstance(efecto, Exception):
            raise efecto
        return efecto

    monkeypatch.setattr(main, "verificar_al_arrancar", falso)

    async def recorrer():
        async with main.ciclo_de_vida(main.app):
            pass

    return llamadas, recorrer, asyncio


def test_el_lifespan_invoca_la_verificacion_exactamente_una_vez(monkeypatch):
    llamadas, recorrer, asyncio = _ejecutar_ciclo_de_vida(monkeypatch, informe())

    asyncio.run(recorrer())

    assert len(llamadas) == 1


def test_el_lifespan_propaga_el_fallo_y_no_arranca(monkeypatch):
    """Si la verificación levanta, la aplicación no llega a servir."""
    llamadas, recorrer, asyncio = _ejecutar_ciclo_de_vida(
        monkeypatch, RuntimeNoRestringido("credencial no restringida")
    )

    with pytest.raises(RuntimeNoRestringido):
        asyncio.run(recorrer())

    assert len(llamadas) == 1


def test_el_lifespan_propaga_tambien_un_fallo_no_verificable(monkeypatch):
    llamadas, recorrer, asyncio = _ejecutar_ciclo_de_vida(
        monkeypatch, PrivilegiosNoVerificables("no se pudo comprobar")
    )

    with pytest.raises(PrivilegiosNoVerificables):
        asyncio.run(recorrer())

    assert len(llamadas) == 1
