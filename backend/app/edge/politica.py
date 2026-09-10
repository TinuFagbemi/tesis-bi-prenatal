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

# Total de intentos por evento, primer intento incluido.
MAX_ATTEMPTS_POR_OMISION = 5

# Espera base, en segundos. Debe ser estrictamente positiva: con 0 todas las
# demoras valdrian 0 y no existiria espera incremental, que es justo lo que el
# ticket promete. Las pruebas no necesitan el 0 porque el sleeper se inyecta.
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

# Tope del exponente antes de calcular la potencia. Sin el, un ``max_attempts``
# absurdamente grande produciria ``2 ** 2000`` como entero y el paso a float
# lanzaria OverflowError. Con el, el resultado satura en el techo, que es lo que
# la formula haria de todos modos.
EXPONENTE_MAXIMO = 60


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
        # Finitud primero, siempre. Ver ``_numero_finito``.
        for nombre in ("base_delay_seconds", "max_delay_seconds", "http_timeout"):
            if not _numero_finito(getattr(self, nombre)):
                raise ConfiguracionInvalida(
                    f"'{nombre}' debe ser un numero finito: no se admiten NaN, "
                    "infinito ni valores no numericos."
                )

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

        if self.base_delay_seconds <= 0:
            raise ConfiguracionInvalida(
                "'base_delay_seconds' debe ser mayor que 0. Con 0 todas las "
                "demoras valdrian 0 y no existiria espera incremental."
            )

        if self.max_delay_seconds < self.base_delay_seconds:
            raise ConfiguracionInvalida(
                "'max_delay_seconds' no puede ser menor que "
                "'base_delay_seconds': el techo quedaria por debajo de la "
                "primera espera."
            )

        if self.http_timeout <= 0:
            raise ConfiguracionInvalida("'http_timeout' debe ser mayor que 0.")

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
        exponente = min(k - 1, EXPONENTE_MAXIMO)
        return min(
            self.base_delay_seconds * (2.0**exponente), self.max_delay_seconds
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
        """
        return FACTOR_DE_FASES * self.http_timeout + MARGEN_DEL_LEASE_SEGUNDOS
