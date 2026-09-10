"""La politica de reintentos: limites, formula y configuracion invalida (SCRUM-65).

Sin red, sin PostgreSQL y sin dormir un solo segundo real. Todo lo que se afirma
aqui es aritmetica y validacion, que es exactamente lo que tiene que poder
comprobarse sin infraestructura.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import math

import pytest

from app.edge.politica import (
    BASE_DELAY_POR_OMISION,
    EXPONENTE_MAXIMO,
    FACTOR_DE_FASES,
    MARGEN_DEL_LEASE_SEGUNDOS,
    MAX_ATTEMPTS_POR_OMISION,
    ConfiguracionInvalida,
    PoliticaDeReintentos,
)


def politica(**cambios) -> PoliticaDeReintentos:
    valores = dict(
        max_attempts=5, base_delay_seconds=1.0, max_delay_seconds=60.0,
        batch_limit=50, http_timeout=10.0,
    )
    valores.update(cambios)
    return PoliticaDeReintentos(**valores)


# ---------------------------------------------------------------------------
# 1. El limite incluye el primer intento
# ---------------------------------------------------------------------------


def test_el_primer_intento_esta_dentro_del_limite():
    """``max_attempts = 1`` significa un intento y ningun reintento."""
    p = politica(max_attempts=1)
    assert p.quedan_intentos(0, None) is True
    assert p.quedan_intentos(1, None) is False


@pytest.mark.parametrize("consumidos", [0, 1, 2])
def test_max_attempts_incluye_el_intento_inicial(consumidos):
    p = politica(max_attempts=3)
    assert p.quedan_intentos(consumidos, None) is True


def test_no_quedan_intentos_al_alcanzar_el_limite():
    p = politica(max_attempts=3)
    assert p.quedan_intentos(3, None) is False
    assert p.quedan_intentos(4, None) is False


def test_el_limite_adoptado_manda_sobre_el_configurado():
    """Bajar el entorno no reescribe la politica de un evento en curso."""
    p = politica(max_attempts=2)
    assert p.limite_de(7) == 7
    assert p.quedan_intentos(5, 7) is True
    assert p.limite_de(None) == 2


# ---------------------------------------------------------------------------
# 2. La formula, y el off-by-one que la acecha
# ---------------------------------------------------------------------------


def test_la_primera_espera_es_exactamente_la_base():
    """Falla el intento 1 -> se espera ``base``, no ``base/2`` ni ``base*2``."""
    assert politica(base_delay_seconds=1.0).demora(1) == 1.0
    assert politica(base_delay_seconds=0.25).demora(1) == 0.25


@pytest.mark.parametrize(
    "k, esperado", [(1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (5, 16.0), (6, 32.0)]
)
def test_la_espera_crece_de_forma_exponencial(k, esperado):
    assert politica().demora(k) == esperado


def test_el_intento_cuatro_espera_ocho_segundos_y_no_cuatro():
    """El off-by-one exacto que el plan corrigio.

    ``k`` es el ordinal del intento que **acaba de fallar**, no la cantidad de
    intentos previos. Un evento con tres intentos heredados hace el numero 4, y
    ocho segundos es la respuesta correcta.
    """
    assert politica(base_delay_seconds=1.0).demora(4) == 8.0
    assert politica(base_delay_seconds=1.0).demora(3) == 4.0


def test_el_techo_se_aplica():
    p = politica(base_delay_seconds=1.0, max_delay_seconds=5.0)
    assert p.demora(3) == 4.0
    assert p.demora(4) == 5.0
    assert p.demora(40) == 5.0


def test_un_exponente_enorme_no_desborda():
    """Con un limite absurdo, la demora satura en el techo en vez de reventar.

    ``2 ** 2000`` como entero es representable en Python y como float no lo es;
    sin el tope del exponente, convertirlo lanzaria ``OverflowError`` en mitad de
    una sincronizacion.
    """
    p = politica(max_attempts=5000)
    assert p.demora(4999) == p.max_delay_seconds
    assert math.isfinite(p.demora(EXPONENTE_MAXIMO + 100))


@pytest.mark.parametrize("k", [0, -1, -100])
def test_no_existe_espera_antes_del_primer_intento(k):
    with pytest.raises(ConfiguracionInvalida):
        politica().demora(k)


def test_el_ordinal_tiene_que_ser_entero():
    with pytest.raises(ConfiguracionInvalida):
        politica().demora(1.5)


# ---------------------------------------------------------------------------
# 3. Configuracion invalida
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("campo", ["base_delay_seconds", "max_delay_seconds", "http_timeout"])
@pytest.mark.parametrize("valor", [float("nan"), float("inf"), float("-inf")])
def test_se_rechaza_cualquier_espera_no_finita(campo, valor):
    """``NaN`` e infinitos, en los tres campos numericos.

    Es el motivo por el que la finitud se comprueba **antes** que el signo:
    ``float('nan') < 0`` es ``False`` y ``float('nan') >= 0`` tambien, asi que una
    comprobacion de signo escrita primero dejaria pasar un ``NaN`` y el
    sincronizador acabaria durmiendo un valor sin sentido.
    """
    with pytest.raises(ConfiguracionInvalida):
        politica(**{campo: valor})


@pytest.mark.parametrize("valor", [0, -1, -100])
def test_se_rechaza_un_maximo_de_intentos_menor_que_uno(valor):
    with pytest.raises(ConfiguracionInvalida):
        politica(max_attempts=valor)


@pytest.mark.parametrize("valor", [0, -1])
def test_se_rechaza_un_lote_no_positivo(valor):
    with pytest.raises(ConfiguracionInvalida):
        politica(batch_limit=valor)


@pytest.mark.parametrize("valor", [0.0, -0.5])
def test_se_rechaza_una_espera_base_no_positiva(valor):
    """Con base 0 todas las demoras valen 0 y no hay espera incremental.

    Se rechaza en lugar de admitirse "solo para pruebas" porque el sleeper es
    inyectado: ninguna prueba necesita ese valor para no dormir, asi que
    permitirlo solo abriria la puerta a una configuracion que contradice lo que
    el ticket promete.
    """
    with pytest.raises(ConfiguracionInvalida):
        politica(base_delay_seconds=valor)


def test_se_rechaza_un_techo_por_debajo_de_la_base():
    with pytest.raises(ConfiguracionInvalida):
        politica(base_delay_seconds=10.0, max_delay_seconds=1.0)


@pytest.mark.parametrize("valor", [0.0, -1.0])
def test_se_rechaza_un_timeout_no_positivo(valor):
    with pytest.raises(ConfiguracionInvalida):
        politica(http_timeout=valor)


@pytest.mark.parametrize("campo", ["max_attempts", "batch_limit"])
def test_un_booleano_no_es_un_entero_valido(campo):
    """``isinstance(True, int)`` es cierto, y no debe bastar."""
    with pytest.raises(ConfiguracionInvalida):
        politica(**{campo: True})


@pytest.mark.parametrize("campo", ["max_attempts", "batch_limit", "base_delay_seconds"])
def test_un_valor_no_numerico_se_rechaza(campo):
    with pytest.raises(ConfiguracionInvalida):
        politica(**{campo: "tres"})


def test_una_politica_valida_es_inmutable():
    p = politica()
    with pytest.raises(Exception):
        p.max_attempts = 99


# ---------------------------------------------------------------------------
# 4. El lease
# ---------------------------------------------------------------------------


def test_la_duracion_del_lease_sale_del_timeout_configurado():
    assert politica(http_timeout=10.0).duracion_del_lease == (
        FACTOR_DE_FASES * 10.0 + MARGEN_DEL_LEASE_SEGUNDOS
    )
    assert politica(http_timeout=10.0).duracion_del_lease == 70.0


def test_el_lease_es_holgado_frente_al_timeout():
    """Es una heuristica conservadora, no un maximo real de la peticion.

    httpx no impone una fecha limite total y sus timeouts de lectura y escritura
    acotan la inactividad entre fragmentos, asi que una respuesta puede llegar
    despues. Lo unico que se afirma es que la ventana es holgada.
    """
    p = politica(http_timeout=3.0)
    assert p.duracion_del_lease > p.http_timeout


# ---------------------------------------------------------------------------
# 5. Las omisiones documentadas
# ---------------------------------------------------------------------------


def test_los_valores_por_omision_construyen_una_politica_valida():
    p = PoliticaDeReintentos()
    assert p.max_attempts == MAX_ATTEMPTS_POR_OMISION
    assert p.base_delay_seconds == BASE_DELAY_POR_OMISION


def test_la_suma_de_esperas_por_omision_es_la_documentada():
    """1 + 2 + 4 + 8 = 15 s con cinco intentos y base 1.

    La cifra aparece en el README y en el decision log; si la formula cambiara,
    esta prueba lo diria antes que la documentacion.
    """
    p = PoliticaDeReintentos()
    esperas = [p.demora(k) for k in range(1, p.max_attempts)]
    assert esperas == [1.0, 2.0, 4.0, 8.0]
    assert sum(esperas) == 15.0
