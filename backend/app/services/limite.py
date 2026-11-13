"""Sliding-window request limit for the self-registration endpoint (SCRUM-110).

**Why it exists.** ``POST /api/v1/registro/pacientes`` is the only write of this
API that answers a caller nobody authenticated. Each accepted request costs an
Argon2id hash -- deliberately expensive -- and inserts a profile, a phone, an
account, a link, two acceptances and an audit entry. Unbounded, a loop could
fill the tables or keep the CPU busy hashing. The limit is the proportional
answer for a prototype; it is not a defence against a distributed attacker, and
the documentation says so.

**Two budgets, both counted.** One per observed origin and one for the whole
process, over the same window. The per-origin one stops a single client; the
global one bounds the total even when the origin is the same for everybody --
which is exactly the case behind the local portal adapter, where every request
reaches the API from the adapter's address.

**What counts.** Every request the route receives, valid or not, and it counts
*before* the body is hashed: a 422 is cheap, but an unlimited stream of them is
still an unlimited stream, and refusing late would spend the hash first.

**Where it lives.** In the memory of one process, guarded by a lock because the
API serves requests from a thread pool. A restart empties it. Several workers
would each keep their own; this prototype runs one.

**The origin.** ``request.client.host``, the address Starlette observed --
never ``X-Forwarded-For``, which the caller chooses (same rule as the audit
trail). The keys are kept only in memory and never logged.

All data handled by this prototype is fictitious and simulated.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    """Whether the request may proceed and, if not, how long to wait."""

    permitida: bool
    reintentar_en_segundos: int = 0


class LimiteDeSolicitudes:
    """Per-key and global sliding windows, safe across threads.

    ``reloj`` returns monotonic seconds; it is injectable so tests can move time
    without sleeping.
    """

    def __init__(
        self,
        *,
        maximo_por_origen: int,
        maximo_global: int,
        ventana_segundos: int,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        if maximo_por_origen < 1 or maximo_global < 1 or ventana_segundos < 1:
            raise ValueError("Los limites y la ventana deben ser positivos.")
        self.maximo_por_origen = maximo_por_origen
        self.maximo_global = maximo_global
        self.ventana_segundos = ventana_segundos
        self._reloj = reloj
        self._candado = threading.Lock()
        self._por_origen: dict[str, deque[float]] = {}
        self._global: deque[float] = deque()

    def _purgar(self, marcas: deque[float], desde: float) -> None:
        while marcas and marcas[0] <= desde:
            marcas.popleft()

    def _espera(self, marcas: deque[float], ahora: float) -> int:
        return max(1, math.ceil(marcas[0] + self.ventana_segundos - ahora))

    def consumir(self, origen: str) -> Decision:
        """Record one request from ``origen`` if both budgets allow it.

        A refused request is **not** recorded: a client that keeps retrying
        while blocked does not push its own release further away, and the
        global budget is not spent on requests that were never served.
        """
        with self._candado:
            ahora = self._reloj()
            desde = ahora - self.ventana_segundos
            self._purgar(self._global, desde)
            propias = self._por_origen.get(origen)
            if propias is not None:
                self._purgar(propias, desde)
                if not propias:
                    del self._por_origen[origen]
                    propias = None

            if propias is not None and len(propias) >= self.maximo_por_origen:
                return Decision(False, self._espera(propias, ahora))
            if len(self._global) >= self.maximo_global:
                return Decision(False, self._espera(self._global, ahora))

            self._global.append(ahora)
            self._por_origen.setdefault(origen, deque()).append(ahora)
            # Only accepted requests create keys, so at most ``maximo_global``
            # origins hold a mark inside the window. Origins that never came back
            # are swept here, which keeps the dictionary bounded by that number.
            if len(self._por_origen) > self.maximo_global:
                for clave in [c for c, m in self._por_origen.items() if not m or m[-1] <= desde]:
                    del self._por_origen[clave]
            return Decision(True)

    def reiniciar(self) -> None:
        """Forget every request. For tests; the API never calls it."""
        with self._candado:
            self._por_origen.clear()
            self._global.clear()
