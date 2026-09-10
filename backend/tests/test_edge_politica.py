"""La politica de reintentos: limites, formula y configuracion invalida (SCRUM-65).

Sin red, sin PostgreSQL y sin dormir un solo segundo real. Casi todo lo que se
afirma aqui es aritmetica y validacion, que es exactamente lo que tiene que poder
comprobarse sin infraestructura.

La unica excepcion es la seccion 6, que abre un SQLite temporal para comprobar
algo que la aritmetica sola no puede: que una politica invalida falla **antes**
de tocar la base, y no a mitad de una pasada.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import math
from datetime import timedelta
from fractions import Fraction

import httpx
import pytest

from app.edge import almacenamiento as alm
from app.edge import captura as cap
from app.edge import outbox
from app.edge import sincronizacion as sincro
from app.edge.cliente import ClienteEdge
from app.edge.estados import EstadoEntrega
from app.edge.politica import (
    BASE_DELAY_POR_OMISION,
    FACTOR_DE_FASES,
    LIMITE_OPERACIONAL_SEGUNDOS,
    MARGEN_DEL_LEASE_SEGUNDOS,
    MAX_ATTEMPTS_POR_OMISION,
    RESOLUCION_MINIMA_SEGUNDOS,
    ConfiguracionInvalida,
    PoliticaDeReintentos,
)
from tests.test_edge_captura import PAQUETE_DE_UNA_LECTURA

VALORES_VALIDOS = dict(
    max_attempts=5, base_delay_seconds=1.0, max_delay_seconds=60.0,
    batch_limit=50, http_timeout=10.0,
)


def politica(**cambios) -> PoliticaDeReintentos:
    return PoliticaDeReintentos(**{**VALORES_VALIDOS, **cambios})


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
    evaluar la potencia sin cuidado lanzaria ``OverflowError`` en mitad de una
    sincronizacion.
    """
    p = politica(max_attempts=5000)
    assert p.demora(4999) == p.max_delay_seconds
    assert math.isfinite(p.demora(10**6))


@pytest.mark.parametrize("k", [10**6, 10**12, 2**62])
def test_un_ordinal_enorme_devuelve_el_techo_sin_desbordar(k):
    """No hay tope fijo del exponente: hay saturacion, y satura en el techo."""
    p = politica()
    assert p.demora(k) == p.max_delay_seconds


# --- La regresion del tope fijo, dentro del dominio programable ------------
#
# Habia un ``EXPONENTE_MAXIMO = 60`` que evitaba el desbordamiento y de paso
# rompia la formula: cuantas duplicaciones caben hasta el techo depende de la
# base, y con una base muy pequena sesenta no bastan. Con base 2**-100 y techo
# 1.0, el intento 101 devolvia 9.09e-13 en lugar de 1.0 -- un factor de 2**40.
#
# Ese par ya no es configurable: 2**-100 es 7.9e-31 s, veinticinco ordenes de
# magnitud por debajo de la resolucion de ``timedelta``, asi que la politica lo
# rechaza (seccion 4). El caso mas ancho que **si** queda dentro del contrato es
# el intervalo entero: base minima programable contra techo maximo operacional.
# Ahi caben 36.33 duplicaciones, de modo que hoy un tope de 60 no se notaria --y
# eso es justo lo que estas pruebas no fijan--. Lo que fijan es que la saturacion
# cae donde la base dice, no donde diga una constante.

BASE_MINIMA = RESOLUCION_MINIMA_SEGUNDOS
TECHO_MAXIMO = LIMITE_OPERACIONAL_SEGUNDOS


def politica_del_intervalo_completo() -> PoliticaDeReintentos:
    """Base minima contra techo maximo: el rango mas ancho que se admite."""
    return politica(
        max_attempts=500,
        base_delay_seconds=BASE_MINIMA,
        max_delay_seconds=TECHO_MAXIMO,
    )


def test_el_rango_mas_ancho_sigue_duplicando_hasta_el_ultimo_paso():
    """Justo antes de saturar: 1e-06 duplicada 36 veces, aun por debajo del techo."""
    p = politica_del_intervalo_completo()
    assert p.demora(37) == math.ldexp(BASE_MINIMA, 36) == 68719.476736
    assert p.demora(37) < TECHO_MAXIMO


def test_el_rango_mas_ancho_satura_exactamente_en_el_techo():
    """Y una duplicacion mas se pasaria, asi que la respuesta es el techo."""
    p = politica_del_intervalo_completo()
    assert math.ldexp(BASE_MINIMA, 37) > TECHO_MAXIMO
    assert p.demora(38) == TECHO_MAXIMO


@pytest.mark.parametrize("k", [39, 500, 10**6, 2**62])
def test_el_rango_mas_ancho_no_baja_del_techo_despues_de_saturar(k):
    assert politica_del_intervalo_completo().demora(k) == TECHO_MAXIMO


def test_la_saturacion_depende_de_la_base_y_no_de_una_constante():
    """Dos politicas validas saturan en ordinales muy distintos.

    Es lo que un ``EXPONENTE_MAXIMO`` fijo no puede representar: con base 1.0 y
    techo 60.0 la septima espera ya esta en el techo, y con la base minima hacen
    falta treinta y ocho.
    """
    assert politica().demora(6) == 32.0
    assert politica().demora(7) == 60.0

    ancha = politica_del_intervalo_completo()
    assert ancha.demora(37) != TECHO_MAXIMO
    assert ancha.demora(38) == TECHO_MAXIMO


@pytest.mark.parametrize(
    "base, techo",
    [
        (1.0, 60.0),
        (0.001, 0.5),
        (0.25, 0.25),
        (RESOLUCION_MINIMA_SEGUNDOS, LIMITE_OPERACIONAL_SEGUNDOS),
        (RESOLUCION_MINIMA_SEGUNDOS, RESOLUCION_MINIMA_SEGUNDOS),
        (LIMITE_OPERACIONAL_SEGUNDOS, LIMITE_OPERACIONAL_SEGUNDOS),
    ],
)
def test_la_demora_coincide_con_la_formula_en_aritmetica_exacta(base, techo):
    """Comparada contra ``min(base * 2^(k-1), techo)`` calculado con fracciones.

    La referencia se evalua con ``Fraction`` y no con ``base * 2.0 ** (k - 1)``
    porque esa segunda forma desborda al calcular la potencia por separado, y
    entonces la prueba acusaria a la implementacion de un error que es suyo.

    Los seis pares recorren el dominio admisible de punta a punta: el caso por
    omision, un techo que no es potencia de dos, base igual a techo, el intervalo
    entero y sus dos extremos degenerados. Doscientos ordinales bastan y sobran
    --el par mas ancho satura en el 38-- pero se sigue recorriendo la zona
    posterior a la saturacion, que es donde vivia el error del tope fijo.
    """
    p = politica(
        max_attempts=5, base_delay_seconds=base, max_delay_seconds=techo,
        batch_limit=1, http_timeout=1.0,
    )
    for k in range(1, 200):
        valor = Fraction(base) * Fraction(2) ** (k - 1)
        esperado = techo if valor >= Fraction(techo) else float(valor)
        assert p.demora(k) == esperado, k


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
# 4. Duraciones programables: el dominio de las tres esperas
# ---------------------------------------------------------------------------
#
# Un float finito y positivo no es lo mismo que una duracion que este comando
# pueda programar. Las tres esperas configurables --y el lease que se deriva de
# una de ellas-- acaban dentro de un ``timedelta``, y ahi ``5e-324`` se convierte
# en cero y ``1e308`` revienta. Lo que se comprueba aqui es que el rechazo ocurre
# al construir la politica, que es el unico sitio donde todavia no hay nada hecho.

CAMPOS_DE_DURACION = ["base_delay_seconds", "max_delay_seconds", "http_timeout"]


def test_las_cotas_no_son_numeros_elegidos_a_ojo():
    """El minimo lo dice ``timedelta``; el maximo es una decision, y se declara."""
    assert RESOLUCION_MINIMA_SEGUNDOS == timedelta.resolution.total_seconds()
    assert RESOLUCION_MINIMA_SEGUNDOS == 1e-06
    assert LIMITE_OPERACIONAL_SEGUNDOS == timedelta(days=1).total_seconds()
    assert LIMITE_OPERACIONAL_SEGUNDOS == 86_400.0


@pytest.mark.parametrize("campo", CAMPOS_DE_DURACION)
def test_se_rechaza_el_float_positivo_mas_pequeno(campo):
    """``5e-324`` es finito, es positivo, y como espera no existe.

    Es exactamente lo que la version anterior aceptaba: pasaba el ``> 0`` y
    despues ``timedelta`` lo redondeaba a cero, de modo que la espera incremental
    que promete el ticket desaparecia sin que nadie se enterara.
    """
    assert 5e-324 > 0
    assert math.isfinite(5e-324)
    assert timedelta(seconds=5e-324).total_seconds() == 0.0

    with pytest.raises(ConfiguracionInvalida):
        politica(**{campo: 5e-324})


@pytest.mark.parametrize(
    "valor",
    [
        5e-324,
        1e-09,
        2e-07,
        5e-07,
        9.99e-07,
        math.nextafter(RESOLUCION_MINIMA_SEGUNDOS, 0.0),
    ],
)
@pytest.mark.parametrize("campo", CAMPOS_DE_DURACION)
def test_se_rechaza_cualquier_duracion_por_debajo_del_microsegundo(campo, valor):
    """Toda la franja, no solo su extremo inferior.

    ``timedelta`` redondea al microsegundo mas cercano en lugar de truncar, asi
    que por debajo del minimo hay dos averias distintas y ninguna es aceptable:
    de medio microsegundo hacia abajo la espera se vuelve cero, y entre medio
    microsegundo y uno la espera **crece** hasta un microsegundo. En los dos
    casos lo que se programa deja de ser lo que se configuro, que es el motivo
    por el que el minimo es la resolucion y no el cero.
    """
    assert 0 < valor < RESOLUCION_MINIMA_SEGUNDOS
    assert timedelta(seconds=valor).total_seconds() != valor

    with pytest.raises(ConfiguracionInvalida):
        politica(**{campo: valor})


def test_se_acepta_exactamente_la_resolucion_minima():
    """El borde inferior es inclusivo, y lo que se programa es lo que se pidio."""
    p = politica(
        base_delay_seconds=RESOLUCION_MINIMA_SEGUNDOS,
        max_delay_seconds=RESOLUCION_MINIMA_SEGUNDOS,
        http_timeout=RESOLUCION_MINIMA_SEGUNDOS,
    )
    assert p.demora(1) == RESOLUCION_MINIMA_SEGUNDOS
    assert timedelta(seconds=p.demora(1)).total_seconds() == RESOLUCION_MINIMA_SEGUNDOS


def test_se_acepta_exactamente_el_limite_operacional():
    """Y el borde superior tambien, con el techo justo en las veinticuatro horas."""
    p = politica(
        base_delay_seconds=LIMITE_OPERACIONAL_SEGUNDOS,
        max_delay_seconds=LIMITE_OPERACIONAL_SEGUNDOS,
    )
    assert p.demora(1) == LIMITE_OPERACIONAL_SEGUNDOS
    assert p.demora(10**6) == LIMITE_OPERACIONAL_SEGUNDOS
    assert timedelta(seconds=p.demora(1)).total_seconds() == LIMITE_OPERACIONAL_SEGUNDOS


@pytest.mark.parametrize(
    "valor",
    [
        math.nextafter(LIMITE_OPERACIONAL_SEGUNDOS, math.inf),
        86_401.0,
        1e09,
    ],
)
@pytest.mark.parametrize("campo", ["base_delay_seconds", "max_delay_seconds"])
def test_se_rechaza_cualquier_duracion_por_encima_del_limite(campo, valor):
    """Un ulp por encima ya sobra: la cota es cerrada por arriba."""
    with pytest.raises(ConfiguracionInvalida):
        politica(**{campo: valor})


@pytest.mark.parametrize("campo", CAMPOS_DE_DURACION)
def test_se_rechaza_el_valor_que_haria_estallar_timedelta(campo):
    """``1e308`` es finito, y ``timedelta`` no lo admite.

    Antes llegaba intacto hasta ``momento + timedelta(seconds=demora)`` y
    lanzaba ``OverflowError`` a mitad de una pasada, con el evento ya reclamado.
    Ahora lo detiene la cota superior, mucho antes de que exista una peticion.
    """
    assert math.isfinite(1e308)
    with pytest.raises(OverflowError):
        timedelta(seconds=1e308)

    with pytest.raises(ConfiguracionInvalida):
        politica(**{campo: 1e308})


# --- El lease es derivado, y tambien tiene que caber -----------------------

TIMEOUT_MAXIMO = (
    LIMITE_OPERACIONAL_SEGUNDOS - MARGEN_DEL_LEASE_SEGUNDOS
) / FACTOR_DE_FASES


def test_el_timeout_mas_grande_admisible_deja_el_lease_justo_en_el_borde():
    """De ``4 * http_timeout + 30 <= 86400`` sale ``http_timeout <= 21592.5``."""
    assert TIMEOUT_MAXIMO == 21_592.5
    p = politica(http_timeout=TIMEOUT_MAXIMO)
    assert p.duracion_del_lease == LIMITE_OPERACIONAL_SEGUNDOS


def test_se_rechaza_un_timeout_cuyo_lease_derivado_se_pasa_de_la_cota():
    """El campo cabe; lo que no cabe es lo que se deriva de el.

    Un ``http_timeout`` de 21592.6 s es una duracion programable perfectamente
    valida, y aun asi la politica lo rechaza, porque el lease que sale de el ya
    no lo es. El mensaje nombra la formula y el campo que hay que bajar, para que
    quien lo lea no tenga que adivinar por que un valor admisible falla.
    """
    justo_encima = math.nextafter(TIMEOUT_MAXIMO, math.inf)
    assert RESOLUCION_MINIMA_SEGUNDOS <= justo_encima <= LIMITE_OPERACIONAL_SEGUNDOS
    assert (
        FACTOR_DE_FASES * justo_encima + MARGEN_DEL_LEASE_SEGUNDOS
        > LIMITE_OPERACIONAL_SEGUNDOS
    )

    for valor in (justo_encima, 21_592.6, 50_000.0):
        with pytest.raises(ConfiguracionInvalida) as excepcion:
            politica(http_timeout=valor)
        assert "duracion_del_lease" in excepcion.value.detalle
        assert "http_timeout" in excepcion.value.detalle


@pytest.mark.parametrize(
    "timeout",
    [RESOLUCION_MINIMA_SEGUNDOS, 0.5, 1.0, 10.0, 3600.0, TIMEOUT_MAXIMO],
)
def test_el_lease_siempre_es_una_duracion_programable(timeout):
    """Ningun ``http_timeout`` aceptado produce un lease que no se pueda sumar.

    Es la garantia de la que depende ``outbox.reclamar_intento``, que hace
    ``momento + timedelta(seconds=duracion_lease)`` sin defenderse.
    """
    lease = politica(http_timeout=timeout).duracion_del_lease
    assert math.isfinite(lease)
    assert RESOLUCION_MINIMA_SEGUNDOS <= lease <= LIMITE_OPERACIONAL_SEGUNDOS
    assert timedelta(seconds=lease).total_seconds() == lease


# ---------------------------------------------------------------------------
# 5. El lease
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
# 6. Una politica invalida no llega a tocar nada
# ---------------------------------------------------------------------------
#
# Esta es la parte que la aritmetica no puede demostrar. Que la validacion este
# en ``__post_init__`` significa que el rechazo ocurre **antes** de que exista un
# cliente HTTP, antes de incrementar ``outbox.intentos``, antes de insertar una
# fila en ``intento_sincronizacion`` y antes de mover el estado del evento. La
# unica forma honesta de afirmarlo es abrir una base, fotografiarla, intentar
# sincronizar con una configuracion invalida y comparar.

BASE_FALSA = "http://nodo-edge.invalid"

TABLAS_QUE_UNA_PASADA_PODRIA_TOCAR = (
    "captura_local",
    "outbox",
    "intento_sincronizacion",
)


@pytest.fixture
def conexion(tmp_path):
    with alm.conectar(tmp_path / "nodo_edge.sqlite3") as conexion:
        alm.inicializar(conexion)
        yield conexion


def fotografia(conexion) -> dict[str, list[tuple]]:
    """Todo lo que una pasada podria cambiar, en una estructura comparable."""
    return {
        tabla: [
            tuple(fila)
            for fila in conexion.execute(f"SELECT * FROM {tabla} ORDER BY 1").fetchall()
        ]
        for tabla in TABLAS_QUE_UNA_PASADA_PODRIA_TOCAR
    }


@pytest.mark.parametrize(
    "cambio",
    [
        {"base_delay_seconds": 5e-324},
        {"base_delay_seconds": float("nan")},
        {"max_delay_seconds": 1e308},
        {"max_delay_seconds": 86_401.0},
        {"http_timeout": 1e308},
        {"http_timeout": 21_592.6},
    ],
    ids=[
        "base-subnormal",
        "base-nan",
        "techo-1e308",
        "techo-por-encima-del-limite",
        "timeout-1e308",
        "timeout-con-lease-desbordado",
    ],
)
def test_una_politica_invalida_no_modifica_sqlite(conexion, cambio):
    """El fallo ocurre al construir la politica, no a mitad de una pasada.

    La llamada a ``sincronizar`` esta dentro del ``raises`` a proposito: es
    inalcanzable, y tiene que serlo. Si algun dia la validacion se moviera de
    ``__post_init__`` a un punto de uso, esta prueba dejaria de pasar por la
    linea de arriba y empezaria a ejercer la de abajo -- y entonces la
    fotografia, o el contador de peticiones, dirian exactamente que se toco.
    """
    cap.capturar(conexion, PAQUETE_DE_UNA_LECTURA, reloj=outbox.ahora_utc)
    antes = fotografia(conexion)
    assert antes["outbox"], "la fotografia necesita algo que comparar"

    peticiones: list[httpx.Request] = []

    def transporte(peticion: httpx.Request) -> httpx.Response:
        peticiones.append(peticion)
        return httpx.Response(201, json={})

    with pytest.raises(ConfiguracionInvalida):
        invalida = PoliticaDeReintentos(**{**VALORES_VALIDOS, **cambio})
        sincro.sincronizar(
            conexion,
            ClienteEdge(
                httpx.Client(
                    transport=httpx.MockTransport(transporte), base_url=BASE_FALSA
                )
            ),
            politica=invalida,
            reloj=outbox.ahora_utc,
            dormir=lambda segundos: None,
        )

    assert peticiones == [], "no debe salir ninguna peticion HTTP"
    assert fotografia(conexion) == antes


def test_lo_que_la_fotografia_estaria_vigilando(conexion):
    """Nombra una por una las cuatro cosas que la prueba anterior protege.

    La comparacion de fotografias es exhaustiva pero muda: si fallara, no diria
    *que* cambio. Esto deja escrito el estado de partida, de modo que las cuatro
    afirmaciones del arreglo --sin peticion, sin intento contado, sin fila de
    historial, sin estado movido-- sean legibles y no haya que deducirlas de un
    diccionario.
    """
    registro = cap.capturar(conexion, PAQUETE_DE_UNA_LECTURA, reloj=outbox.ahora_utc)
    evento = outbox.leer_evento(conexion, registro.id_outbox)

    assert evento["intentos"] == 0
    assert evento["estado"] == EstadoEntrega.PENDIENTE.value
    assert evento["max_intentos_aplicado"] is None
    assert evento["proximo_intento_en"] is None
    assert fotografia(conexion)["intento_sincronizacion"] == []


# ---------------------------------------------------------------------------
# 7. Las omisiones documentadas
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
