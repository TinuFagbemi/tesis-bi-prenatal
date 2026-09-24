"""El paquete fijo de una sesion de movimiento simulada (SCRUM-72).

**Separado del dataset canonico a proposito.** ``scripts/generate_mock_data.py``
genera el dataset completo que carga PostgreSQL; este modulo no lo toca, no lo
importa y no comparte ninguna semilla con el. Lo que construye aqui es un
*fixture* mucho mas pequeno: un paquete unico, fijo y reproducible, pensado
para demostrar el flujo de captura offline de un dispositivo de paciente, no
para poblar una base de datos.

**Puro.** Ninguna funcion de este modulo abre una conexion, llama a la red ni
lee configuracion. Recibe el embarazo ya autorizado por quien la llama --nunca
decide eso aqui-- y la hora del reloj inyectado, y devuelve un diccionario con
la forma exacta que ``SesionMonitoreoEntrada`` exige. La autoridad sobre esa
forma sigue siendo ese contrato: este modulo no valida nada, solo construye.

**Los identificadores de catalogo son un marcador, no un dato verificado.**
``ID_DISPOSITIVO_SIMULADO``, ``ID_TIEMPO_GEST_SIMULADO`` e
``ID_SEMAFORO_SIMULADO`` son constantes fijas para que la captura **local**
--que no toca PostgreSQL-- sea siempre reproducible. Pero la ingesta central
exige que el dispositivo este realmente asignado a ese embarazo en esas
fechas, y que el tiempo gestacional corresponda **exactamente** a la semana
real del embarazo en el instante de captura (``app.services.ingesta``): son
hechos de una base de datos concreta que este adaptador, sin conexion a
PostgreSQL y sin una ruta que se los publique a la paciente, no tiene forma
legitima de conocer. Por eso una sincronizacion real puede fallar con un 404 o
un 422 aunque la captura local haya sido perfecta -- y esa es la respuesta
correcta: un fallo real reportado con honestidad, no un exito fabricado. Ese
desacoplo se documenta, no se oculta.

**El valor biometrico no lo elige la paciente ni un numero aleatorio de
JavaScript.** Es una constante fija de este modulo, igual para cualquier
sesion simulada. No es una medicion, no es un conteo percibido y no pretende
serlo: la interfaz debe presentarlo como lo que es.

Todos los datos son ficticios y simulados.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.models.enums import EstadoSesion, TipoSesion

# Marcadores de catalogo. Ver la nota del modulo: la captura local nunca
# depende de que existan en una base concreta; la sincronizacion real si.
ID_DISPOSITIVO_SIMULADO = 1
ID_TIEMPO_GEST_SIMULADO = 1
ID_SEMAFORO_SIMULADO = 1

# Valores biometricos fijos de la simulacion, uno por forma de lectura. Nunca
# generados al azar ni escritos por la paciente.
HR_SIMULADO = Decimal("80.00")
SPO2_SIMULADO = Decimal("97.00")
MOV_SIMULADO = 12


def construir_paquete_simulado(
    *, id_embarazo: int, tipo_sesion: TipoSesion, ahora: datetime
) -> dict:
    """Un paquete valido y reproducible para ``app.edge.captura.capturar``.

    ``id_embarazo`` llega ya autorizado por quien llama -- esta funcion no lo
    comprueba, y no puede: es pura y no conoce a la paciente ni al servidor
    central. ``tipo_sesion`` llega ya validado como un :class:`TipoSesion` real
    por Pydantic, en la ruta que llama a esta funcion -- los dos unicos valores
    que ese tipo admite son los dos que esta funcion sabe construir, asi que no
    hay un tercer caso que rechazar aqui.

    La sesion se declara ``COMPLETADA`` en el mismo instante que la unica
    lectura que lleva, porque una simulacion no tiene sentido pendiente: existe
    para demostrar que el paquete se valida, se guarda y puede sincronizarse,
    no para modelar una sesion en curso.
    """
    instante = ahora.isoformat()
    es_movimiento = tipo_sesion is TipoSesion.MOVIMIENTOS_FETALES

    lectura = {
        "id_tiempo_gest": ID_TIEMPO_GEST_SIMULADO,
        "id_semaforo": ID_SEMAFORO_SIMULADO,
        "fecha_hora_captura": instante,
        "fecha_hora_sincronizacion": None,
        "hr_valor": None if es_movimiento else str(HR_SIMULADO),
        "spo2_valor": None if es_movimiento else str(SPO2_SIMULADO),
        "mov_valor": MOV_SIMULADO if es_movimiento else None,
    }

    return {
        "id_embarazo": id_embarazo,
        "id_dispositivo": ID_DISPOSITIVO_SIMULADO,
        "tipo_sesion": tipo_sesion.value,
        "fecha_inicio": instante,
        "fecha_fin": instante,
        "estado_sesion": EstadoSesion.COMPLETADA.value,
        "lecturas": [lectura],
    }
