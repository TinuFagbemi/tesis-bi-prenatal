"""La politica de reintentos: cuantos, cuanto se espera, y que es configurable.

Un solo lugar decide **si queda otro intento** y **cuanto se espera antes**. El
emisor, el sincronizador, la reconciliacion, el CLI y las pruebas preguntan
aqui; ninguno calcula una demora por su cuenta. Dos formulas equivalentes en dos
modulos distintos es exactamente como una acaba divergiendo de la otra sin que
nadie lo note.

**``max_attempts`` incluye el primer intento.** Con ``max_attempts = 3`` hay un
intento inmediato y dos reintentos, y no existe un cuarto intento automatico. Se
dice aqui, en el README y en el decision log, porque es la clase de detalle que
cada lector supone al reves.

**La formula, definida una sola vez**::

    delay(k) = min(base_delay_seconds * 2^(k-1), max_delay_seconds)

donde ``k`` es el **numero ordinal del intento que acaba de fallar** de forma
recuperable -- es decir ``intento_sincronizacion.numero``, que es
``outbox.intentos`` despues del incremento. No es "los intentos previamente
consumidos": esa lectura produce un off-by-one, y con intentos heredados de
SCRUM-64 el error se vuelve visible de inmediato. Un evento que trae tres
intentos de v1 hace su intento numero 4; si falla, le corresponde ``delay(4)``.

No hay *jitter*. Un solo nodo no tiene manada que dispersar, y anadirlo
complicaria las pruebas sin aportar nada demostrable.

**Que duraciones se admiten.** No todo float finito y positivo es una espera que
este comando pueda programar, asi que las tres duraciones configurables
--``base_delay_seconds``, ``max_delay_seconds`` y ``http_timeout``-- y el lease
derivado de la ultima viven en un intervalo cerrado:

    1e-06 s (``timedelta.resolution``)  <=  duracion  <=  86400 s (24 horas)

El minimo es la resolucion real de ``timedelta``, que redondea al microsegundo
mas cercano: por debajo de el, lo que se programa deja de ser lo que se
configuro --y por debajo de medio microsegundo se programa cero, es decir
ninguna espera--. El maximo es una decision operacional
--este es un comando finito que una persona lanza y espera-- y de paso mantiene
lejos el ``OverflowError`` de ``timedelta``. Todo se comprueba **al construir la
politica**, de modo que ningun punto de uso tenga que capturar ``OverflowError``
por su cuenta ni descubrir el problema a mitad de una pasada.

**El lease es una heuristica, no una garantia.** ``duracion_del_lease`` acota
cuando un intento sin resultado *puede* darse por abandonado. No demuestra que
la peticion original haya terminado: httpx no impone una fecha limite total, y
sus timeouts de lectura y escritura son limites de inactividad por fragmento, de
modo que una respuesta lenta pero constante puede llegar despues. El diseno lo
asume como caso normal. Lo que evita duplicados no es el lease: es la misma
``Idempotency-Key``, las guardas por ordinal, las guardas de terminalidad y el
tratamiento de resultados tardios.

Todos los datos que esta politica gobierna son simulados y ficticios.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta

# Total de intentos por evento, primer intento incluido.
MAX_ATTEMPTS_POR_OMISION = 5

# Espera base, en segundos. Tiene que ser una duracion programable --al menos
# ``RESOLUCION_MINIMA_SEGUNDOS``, mas abajo--: con 0 todas las demoras valdrian 0
# y no existiria espera incremental, que es justo lo que el ticket promete. Las
# pruebas no necesitan el 0 porque el sleeper se inyecta.
BASE_DELAY_POR_OMISION = 1.0

# Techo de la espera. Con la base por omision la progresion es 1, 2, 4, 8, 16,
# 32, 60, 60... y nunca crece mas.
MAX_DELAY_POR_OMISION = 60.0

# Eventos que una **ronda** selecciona como maximo. No es el maximo de intentos
# ni el total de la ejecucion: lo que acota la ejecucion es la marca de agua de
# ``id_outbox``, no este numero.
BATCH_LIMIT_POR_OMISION = 50

# Heuristica del lease. httpx aplica cuatro timeouts de fase --connect, read,
# write, pool-- y ninguna fecha limite total, asi que multiplicar por cuatro es
# una eleccion conservadora, no una cota demostrada.
FACTOR_DE_FASES = 4
MARGEN_DEL_LEASE_SEGUNDOS = 30.0

# --- Que significa aqui una duracion "programable" -------------------------
#
# Un float finito y positivo no basta. Estas duraciones acaban dentro de un
# ``timedelta`` --``momento + timedelta(seconds=demora)``-- y ahi hay dos bordes
# que un ``> 0`` no ve:
#
#   timedelta(seconds=5e-324).total_seconds()  ==  0.0
#   timedelta(seconds=1e308)                   ->  OverflowError
#
# El primero convierte una espera positiva en ninguna espera; el segundo revienta
# al programarla. Y el lease derivado, ``4 * http_timeout + 30``, desborda a
# ``inf`` mucho antes: ``4 * 1e308`` ya no es un numero.
#
# Asi que la politica distingue entre un float valido y una duracion que este
# CLI puede realmente programar, y lo hace **al construirse**: cualquier
# instancia que exista es utilizable, y ni el emisor, ni el sincronizador, ni la
# outbox necesitan capturar ``OverflowError`` por su cuenta.

# Resolucion efectiva de ``timedelta``: un microsegundo.
#
# ``timedelta`` no trunca, redondea al microsegundo mas cercano, asi que por
# debajo de esta cota la espera que se programa deja de ser la que se configuro:
#
#   timedelta(seconds=5.1e-7).total_seconds()  ==  1e-06   (redondea hacia arriba)
#   timedelta(seconds=5e-7).total_seconds()    ==  0.0     (y de aqui hacia abajo,
#   timedelta(seconds=5e-324).total_seconds()  ==  0.0      ninguna espera)
#
# Admitir esa franja seria admitir una configuracion que el programa contradice
# en silencio, de modo que el minimo es la resolucion misma.
RESOLUCION_MINIMA_SEGUNDOS = timedelta.resolution.total_seconds()

# Cota superior operacional: 24 horas.
#
# No es el maximo de ``float`` ni ``timedelta.max``, que serian cotas falsas: una
# espera de mil anos es representable y no es programable por nadie. Este es un
# comando **finito** que una persona lanza y espera; una sola espera mas larga
# que un dia sobrevive a cualquier sesion manual plausible y a la marca de agua
# de la propia ejecucion, que acota el trabajo a lo que existia al empezar. Con
# la base minima, 24 horas siguen dejando sitio a 36 duplicaciones, muchas mas de
# las que cualquier ``max_attempts`` sensato consume.
#
# La misma cota alcanza al lease, que es derivado, y por tanto acota tambien a
# ``http_timeout``: de ``4 * http_timeout + 30 <= 86400`` sale
# ``http_timeout <= 21592.5`` s. No hace falta escribir ese numero en ninguna
# parte --lo comprueba la validacion explicita del lease-- pero el mensaje de
# error dice de donde viene, para que quien lo lea sepa que campo bajar.
LIMITE_OPERACIONAL_SEGUNDOS = 86_400.0


def _exponente_de_saturacion(base: float, techo: float) -> int:
    """Primer exponente a partir del cual ``base * 2^e`` ya no baja del techo.

    Existe para no evaluar nunca una potencia que pueda desbordarse, y se calcula
    **sin dividir** ``techo / base``: con una base subnormal y un techo enorme esa
    division daria infinito y el logaritmo despues fallaria.

    ``math.frexp`` descompone cada numero en mantisa y exponente binario, con la
    mantisa siempre en ``[0.5, 1)``. La diferencia de exponentes acota cuando el
    producto alcanza el techo, y el ``+ 1`` la vuelve una cota superior segura:
    puede sobrar por uno, y sobrar no cambia el resultado porque por debajo del
    umbral se sigue aplicando ``min`` contra el techo.

    Un tope fijo --habia uno de 60-- parecia equivalente y no lo era: con
    ``base = 2**-100`` y ``techo = 1.0``, el intento 101 devolvia
    ``9.09e-13`` en lugar de ``1.0``, un factor de ``2**40``. La formula no
    admite un maximo constante, porque cuantas duplicaciones caben hasta el techo
    depende de la base.

    Con las cotas de duracion programable ese par ya no es configurable, y el
    peor caso del dominio actual es ``base = 1e-06`` con ``techo = 86400``: caben
    36.33 duplicaciones, esta funcion devuelve 37, y la saturacion cae entre
    ``demora(37)`` y ``demora(38)``. Es decir que hoy un tope de 60 no se
    notaria. Se conserva el calculo derivado igualmente, por dos razones: sigue
    siendo lo que hace ``demora`` exacta y libre de desbordamiento para
    **cualquier** ordinal, y no depende de que esas dos constantes se queden
    donde estan.
    """
    _, exponente_base = math.frexp(base)
    _, exponente_techo = math.frexp(techo)
    return exponente_techo - exponente_base + 1


class ConfiguracionInvalida(Exception):
    """Una politica que no puede construirse, con un motivo escrito aqui.

    ``detalle`` es texto de este proyecto: no lleva valores del entorno, ni
    rutas, ni mensajes de un driver, asi que el CLI puede mostrarlo tal cual.
    """

    def __init__(self, detalle: str) -> None:
        self.detalle = detalle
        super().__init__(detalle)


def _entero_valido(valor: object) -> bool:
    """Un entero de verdad. ``bool`` es subclase de ``int`` y no cuenta."""
    return isinstance(valor, int) and not isinstance(valor, bool)


def _numero_finito(valor: object) -> bool:
    """Un numero real y finito.

    ``math.isfinite`` es lo primero que se comprueba en toda la validacion, y el
    orden **no es cosmetico**: ``float('nan') < 0`` es ``False`` y
    ``float('nan') >= 0`` tambien, asi que una comprobacion de signo escrita
    antes que esta aceptaria ``NaN`` en silencio. Lo mismo ocurre con
    ``max_delay < base_delay``: con un ``NaN`` de por medio la comparacion es
    ``False`` y la configuracion pasaria.
    """
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return False
    return math.isfinite(float(valor))


def _exigir_duracion_programable(valor: float, nombre: str, *, sufijo: str = "") -> None:
    """Rechaza una duracion que este CLI no podria llegar a programar.

    Tres rechazos, y cada uno nombra un fallo real y no un gusto:

    * por debajo de un microsegundo ``timedelta`` la redondea a otra cosa --y a
      cero por debajo de medio microsegundo--, de modo que la "espera
      incremental" que promete el ticket deja de ser la configurada en silencio;
    * por encima de la cota operacional la espera sobrevive a cualquier
      ejecucion plausible de un comando finito;
    * y cualquier valor que haria estallar ``timedelta(seconds=...)`` queda
      descartado por esa misma cota mucho antes de tener la ocasion.

    Se llama **al construir la politica**, nunca en mitad de una pasada: cuando
    ``ConfiguracionInvalida`` sale de aqui todavia no hubo peticion HTTP, ni
    incremento de ``outbox.intentos``, ni intento insertado, ni estado tocado.
    """
    if not _numero_finito(valor):
        raise ConfiguracionInvalida(
            f"'{nombre}' debe ser un numero finito: no se admiten NaN, infinito "
            "ni valores no numericos." + sufijo
        )
    if valor < RESOLUCION_MINIMA_SEGUNDOS:
        raise ConfiguracionInvalida(
            f"'{nombre}' debe ser al menos {RESOLUCION_MINIMA_SEGUNDOS:g} s, la "
            "resolucion de timedelta. Por debajo, la espera programada ya no es "
            "la configurada, y por debajo de medio microsegundo es cero." + sufijo
        )
    if valor > LIMITE_OPERACIONAL_SEGUNDOS:
        raise ConfiguracionInvalida(
            f"'{nombre}' no puede superar {LIMITE_OPERACIONAL_SEGUNDOS:g} s "
            "(24 horas), el limite operacional de este comando finito." + sufijo
        )


@dataclass(frozen=True)
class PoliticaDeReintentos:
    """Cuantos intentos, cuanto se espera y cuantos eventos por ronda.

    Inmutable y validada al construirse: una politica que existe es una politica
    utilizable, y ningun punto de uso tiene que volver a comprobarla.
    """

    max_attempts: int = MAX_ATTEMPTS_POR_OMISION
    base_delay_seconds: float = BASE_DELAY_POR_OMISION
    max_delay_seconds: float = MAX_DELAY_POR_OMISION
    batch_limit: int = BATCH_LIMIT_POR_OMISION
    http_timeout: float = 10.0

    def __post_init__(self) -> None:
        if not _entero_valido(self.max_attempts) or self.max_attempts < 1:
            raise ConfiguracionInvalida(
                "'max_attempts' debe ser un entero mayor o igual que 1. Incluye "
                "el primer intento, asi que 1 significa un unico intento y "
                "ningun reintento."
            )

        if not _entero_valido(self.batch_limit) or self.batch_limit < 1:
            raise ConfiguracionInvalida(
                "'batch_limit' debe ser un entero mayor o igual que 1. Es el "
                "maximo de eventos por ronda, no el maximo de intentos."
            )

        # Finitud primero, siempre --``_exigir_duracion_programable`` empieza
        # por ahi-- y antes de la comparacion de abajo: con un ``NaN`` de por
        # medio ``max_delay < base_delay`` es ``False`` y la politica pasaria.
        for nombre in ("base_delay_seconds", "max_delay_seconds", "http_timeout"):
            _exigir_duracion_programable(getattr(self, nombre), nombre)

        if self.max_delay_seconds < self.base_delay_seconds:
            raise ConfiguracionInvalida(
                "'max_delay_seconds' no puede ser menor que "
                "'base_delay_seconds': el techo quedaria por debajo de la "
                "primera espera."
            )

        # El lease se deriva del timeout, asi que un timeout admisible puede
        # producir un lease que no lo es. Se comprueba aqui, antes de que nadie
        # reclame un intento con el.
        _exigir_duracion_programable(
            FACTOR_DE_FASES * self.http_timeout + MARGEN_DEL_LEASE_SEGUNDOS,
            "duracion_del_lease",
            sufijo=(
                " Se deriva de 'http_timeout' como "
                f"{FACTOR_DE_FASES} * http_timeout + {MARGEN_DEL_LEASE_SEGUNDOS:g}, "
                "asi que reduce 'http_timeout' para que quepa."
            ),
        )

    # ------------------------------------------------------------------
    # Limite
    # ------------------------------------------------------------------

    def limite_de(self, max_intentos_aplicado: int | None) -> int:
        """Limite vigente para un evento: el adoptado, o el configurado.

        Un evento que ya adopto una politica conserva la suya aunque el entorno
        cambie despues. Uno que todavia no la adopto usa la configurada, y la
        adoptara al reclamar su primer intento.
        """
        if max_intentos_aplicado is None:
            return self.max_attempts
        return max_intentos_aplicado

    def quedan_intentos(
        self, intentos: int, max_intentos_aplicado: int | None
    ) -> bool:
        """Si el evento todavia puede recibir otro intento automatico."""
        return intentos < self.limite_de(max_intentos_aplicado)

    # ------------------------------------------------------------------
    # Espera
    # ------------------------------------------------------------------

    def demora(self, k: int) -> float:
        """Segundos a esperar despues de que falle el intento numero ``k``.

        ``k`` es el ordinal del intento que acaba de fallar, empezando en 1. No
        existe espera antes del primer intento, asi que nunca se llama con 0.
        """
        if not _entero_valido(k) or k < 1:
            raise ConfiguracionInvalida(
                "El ordinal de un intento empieza en 1: no hay espera antes del "
                "primero."
            )

        exponente = k - 1
        if exponente >= _exponente_de_saturacion(
            self.base_delay_seconds, self.max_delay_seconds
        ):
            # Ya saturo: devolver el techo es exacto y evita evaluar una potencia
            # que con un ordinal enorme desbordaria.
            return self.max_delay_seconds

        # ``ldexp`` es un ajuste del exponente binario, no una multiplicacion:
        # exacto, y sin construir ``2 ** exponente`` como entero primero.
        return min(
            math.ldexp(self.base_delay_seconds, exponente), self.max_delay_seconds
        )

    # ------------------------------------------------------------------
    # Lease
    # ------------------------------------------------------------------

    @property
    def duracion_del_lease(self) -> float:
        """Segundos tras los cuales un intento sin resultado puede reconciliarse.

        Heuristica conservadora de recuperacion, **no** un maximo real de la
        peticion ni una propiedad garantizada de httpx. Ver el docstring del
        modulo.

        Siempre finita y programable: el constructor ya comprobo esta misma
        expresion contra la resolucion minima y el limite operacional, asi que
        quien la use puede pasarla a ``timedelta`` sin defenderse.
        """
        return FACTOR_DE_FASES * self.http_timeout + MARGEN_DEL_LEASE_SEGUNDOS
