"""Envio automatico de la cola local de cada cuenta (SCRUM-72).

La paciente no inicia ni transmite nada: lo capturado queda ``PENDIENTE`` en el
SQLite de su cuenta y este emisor lo entrega en cuanto hay con que hacerlo.

**Que hace en cada ciclo.** Recorre los tokens centrales que el portal tiene en
memoria, averigua de que cuenta es cada uno por su sesion local vigente y, una
vez por cuenta, llama a :func:`app.gestante.movimientos.enviar_pendientes_de_la_cuenta`.
Esa funcion no toca la red si la cola no tiene nada elegible, verifica la
credencial antes de reclamar ningun evento y ejecuta una ronda de
:func:`app.edge.ejecutar_pasada` respetando la espera programada. Asi, cuando la
red vuelve, el siguiente ciclo entrega lo pendiente sin que nadie haga nada.

**Lo que no hace.** No tiene cola, estados, reintentos ni transporte propios:
todo eso es ``app.edge`` (SCRUM-64/65). No guarda tokens en disco. Y no envia
la cola de una cuenta sin su token: si el portal se reinicia, el token se
pierde --por diseno, nunca se persiste-- y los pendientes esperan en disco
hasta que la paciente vuelva a iniciar sesion.

El hilo es un detalle de despliegue: :func:`ciclo` es una funcion corriente y
las pruebas la llaman directamente, sin esperar ni dormir.

Todos los datos son ficticios y simulados.
"""

from __future__ import annotations

import logging
import threading

from app.gestante import almacen, movimientos, sesion as sesion_local
from app.gestante.rutas import ContextoAdaptador

registrador = logging.getLogger(__name__)


def ciclo(contexto: ContextoAdaptador) -> dict[int, movimientos.EnvioDeLaCuenta]:
    """Un recorrido por las cuentas con token. Devuelve lo hecho por cuenta.

    Una cuenta con dos sesiones abiertas se atiende una sola vez por ciclo. Un
    token que la API rechazo se olvida, igual que hacen las rutas, para que la
    interfaz pida volver a iniciar sesion; los pendientes no se tocan.
    """
    settings = contexto.settings
    hechos: dict[int, movimientos.EnvioDeLaCuenta] = {}
    activos = contexto.tokens.activos()
    if not activos:
        return hechos

    ahora = contexto.reloj()
    with almacen.abrir_almacen(
        settings.sqlite_path, espera_de_bloqueo_ms=settings.busy_timeout_ms
    ) as conexion:
        almacen.inicializar(conexion)
        titulares = [
            (
                hash_sesion,
                token,
                sesion_local.titular_vigente(conexion, hash_sesion, ahora=ahora),
            )
            for hash_sesion, token in activos
        ]

    for hash_sesion, token, id_usuario in titulares:
        if id_usuario is None or id_usuario in hechos:
            continue
        envio = movimientos.enviar_pendientes_de_la_cuenta(
            settings,
            id_usuario=id_usuario,
            token=token,
            constructor_cliente_http=contexto.constructor_cliente_edge,
            reloj=contexto.reloj,
        )
        if envio.credencial_rechazada:
            contexto.tokens.olvidar_por_digest(hash_sesion, token)
        hechos[id_usuario] = envio

    return hechos


class EmisorAutomatico:
    """Ejecuta :func:`ciclo` cada ``intervalo`` segundos en un hilo propio.

    Espera antes del primer ciclo: al arrancar todavia no hay ningun token en
    memoria. Un fallo inesperado de un ciclo se registra y no detiene los
    siguientes; la cola sigue intacta porque ``app.edge`` solo cambia un
    evento dentro de una transaccion.
    """

    def __init__(self, contexto: ContextoAdaptador, intervalo: float) -> None:
        self._contexto = contexto
        self._intervalo = intervalo
        self._parar = threading.Event()
        self._hilo = threading.Thread(
            target=self._bucle, name="envio-automatico", daemon=True
        )

    def iniciar(self) -> None:
        self._hilo.start()

    def detener(self) -> None:
        self._parar.set()
        if self._hilo.is_alive():
            self._hilo.join(timeout=self._contexto.settings.http_timeout * 2)

    def _bucle(self) -> None:
        while not self._parar.wait(self._intervalo):
            try:
                ciclo(self._contexto)
            except Exception:  # noqa: BLE001 -- un ciclo fallido no apaga el emisor
                registrador.exception("Fallo un ciclo del envio automatico.")
