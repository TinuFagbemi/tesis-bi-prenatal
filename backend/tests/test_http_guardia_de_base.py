"""La suite HTTP se niega a tocar una base que no sea desechable (SCRUM-98).

Esta comprobacion corre **sin servidor**, y ese es el punto. La salvaguarda que
vigila existe para el caso en que alguien apunte
``SCRUM98_HTTP_TEST_DATABASE_URL`` a una base persistente; probarla solo cuando
hay un PostgreSQL delante la dejaria sin probar justamente donde importa, porque
para entonces ya habria un engine construido.

**Que protege.** ``test_http_como_api_postgresql`` escribe y borra: crea dos
perfiles clinicos, aprovisiona cuentas, ingesta sesiones con sus lecturas, y al
terminar lo retira todo usando marcas de agua -- «borra la auditoria con
``id_log`` mayor que el que habia antes de la prueba», y lo mismo con
``id_sesion``. La tecnica es exacta y no alcanza una fila ajena, pero descansa en
dos supuestos que el codigo no puede verificar: que la base sea desechable y que
nadie mas escriba a la vez. Si otro proceso inserta entre la marca de agua y el
teardown, esas filas caen dentro del rango.

De ahi la lista corta de nombres admitidos, y de ahi que la negativa ocurra
antes de que exista una conexion.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import pytest

from tests.test_http_como_api_postgresql import (
    BASE_EFIMERA_DE_CI,
    VARIABLE_DE_ENTORNO,
    BaseNoAutorizada,
    exigir_base_desechable,
)

PLANTILLA = "postgresql+psycopg://fetalalert_api:clave-simulada@127.0.0.1:5432/{}"

# Nombres que un descuido real produciria: la base de desarrollo del compose, la
# canonica del proyecto, la de mantenimiento del cluster, una de otro ticket.
NO_AUTORIZADAS = (
    "fetalalert",
    "fetalalert_dev",
    "postgres",
    "template1",
    "scrum97_tmp_0a1b2c3d_plantilla",
    "scrum52_validacion",
    "scrum52_validacion_tmp_copia",
    "produccion",
    "",
    # El salto de linea final: ``$`` lo tolera y ``fullmatch`` no. Un nombre asi
    # llega de un fichero de entorno o de un secreto copiado con su salto, y
    # nombraria una base distinta de la que la comprobacion creyo aprobar.
    "scrum98_http\n",
    "scrum52_validacion_tmp\n",
    "scrum98_http\r\n",
    "scrum98_http\n ",
)

AUTORIZADAS = (
    BASE_EFIMERA_DE_CI,
    "scrum98_http_tmp",
    "scrum98_bootstrap_tmp",
    "scrum98_pre_0a1b2c3d",
)


@pytest.mark.parametrize("base", NO_AUTORIZADAS)
def test_una_base_no_autorizada_se_rechaza(base):
    with pytest.raises(BaseNoAutorizada):
        exigir_base_desechable(PLANTILLA.format(base))


@pytest.mark.parametrize("base", AUTORIZADAS)
def test_las_bases_desechables_se_admiten(base):
    """La contraparte: sin ella, «rechaza» podria ser cierto por rechazarlo todo."""
    assert exigir_base_desechable(PLANTILLA.format(base)) == base


def test_el_mensaje_dice_que_hacer_y_nombra_la_base_ofrecida():
    with pytest.raises(BaseNoAutorizada) as error:
        exigir_base_desechable(PLANTILLA.format("fetalalert_dev"))

    mensaje = str(error.value)
    assert "fetalalert_dev" in mensaje
    assert VARIABLE_DE_ENTORNO in mensaje
    assert BASE_EFIMERA_DE_CI in mensaje
    assert "scrum98_" in mensaje


def test_el_mensaje_no_publica_la_credencial():
    """Un rechazo de configuracion no es sitio para imprimir una contrasena."""
    with pytest.raises(BaseNoAutorizada) as error:
        exigir_base_desechable(PLANTILLA.format("produccion"))

    mensaje = str(error.value)
    assert "clave-simulada" not in mensaje
    assert "fetalalert_api" not in mensaje


def test_el_prefijo_aprobado_no_admite_un_sufijo_cualquiera():
    """``scrum98_`` es un prefijo con forma, no una subcadena que baste tener."""
    with pytest.raises(BaseNoAutorizada):
        exigir_base_desechable(PLANTILLA.format("copia_scrum98_http"))
    with pytest.raises(BaseNoAutorizada):
        exigir_base_desechable(PLANTILLA.format("scrum98_CON_MAYUSCULAS"))


def test_la_guardia_se_invoca_desde_la_fixture_de_url():
    """Que exista la funcion no basta: la fixture tiene que llamarla.

    Se lee el codigo fuente de la fixture porque ejecutarla exigiria el servidor,
    y lo que hay que fijar es que la validacion ocurra **ahi**: ``url`` es la
    fixture de la que cuelgan los dos engines, las referencias y la limpieza, asi
    que validar en ella es validar antes de que nada se escriba.
    """
    import inspect

    from tests import test_http_como_api_postgresql as suite

    fuente = inspect.getsource(suite.url.__wrapped__)

    assert "exigir_base_desechable" in fuente
    # Y antes de la comprobacion de la credencial, que es la otra guardia.
    assert fuente.index("exigir_base_desechable") < fuente.index("ROL_ESPERADO")
