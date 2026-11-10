"""El paquete de una sesión simulada, con referencias resueltas de verdad.

**Separado del dataset canónico a propósito.** ``scripts/generate_mock_data.py``
genera el dataset completo que carga PostgreSQL; este módulo no lo toca, no lo
importa y no comparte ninguna semilla con él. Lo que construye es un paquete
único, con valores biométricos fijos, para demostrar el flujo de captura
offline de un dispositivo de paciente.

**Ningún identificador se inventa.** ``id_dispositivo``, ``id_tiempo_gest`` e
``id_semaforo`` salen del aprovisionamiento de este dispositivo
(:mod:`app.gestante.provision`), que los leyó de la base real con la
credencial de mantenimiento. Este módulo solo los busca y los coloca; si el
que hace falta no está, se niega a construir el paquete en lugar de poner un
número que el servidor rechazaría más tarde.

**El semáforo no se elige: se deriva, con la única autoridad que existe.** El
ETL vuelve a clasificar cada lectura con SIM-1.0 y aborta la corrida completa
si el nivel operacional no coincide con el derivado
(``DiscrepanciaDeSemaforo``). Es decir: un semáforo escogido a mano no solo
sería una afirmación clínica inventada, sino una bomba de tiempo que rompería
el ETL de todo el conjunto. Por eso se llama a
:func:`app.etl.reglas.clasificar_lectura` sobre los mismos valores que viajan
en el paquete. No es una segunda copia de las reglas: es una llamada a la
primera.

**La semana gestacional tampoco se declara: se calcula** con
:func:`app.services.ingesta.semana_gestacional`, la misma función con la que
el servidor va a comprobarla. Declararla de otro modo sería tener dos
aritméticas para una sola cosa.

**El valor biométrico no lo elige la paciente ni un número aleatorio de
JavaScript.** Es una constante fija de este módulo, igual para cualquier
sesión simulada. No es una medición, no es un conteo percibido y no pretende
serlo.

Todos los datos son ficticios y simulados.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

# Reutilización de lectura de las dos autoridades que ya existen. Ninguno de
# los dos módulos abre una conexión: son reglas puras. Escribir aquí una
# segunda aritmética de la semana o un segundo umbral clínico es exactamente
# lo que este proyecto no hace.
from app.etl.reglas import clasificar_lectura
from app.gestante.provision import Provision
from app.loader.dataset import SEMANA_MINIMA_DE_MOVIMIENTO
from app.models.enums import EstadoSesion, TipoSesion
from app.services.ingesta import semana_gestacional

# Valores biométricos fijos de la simulación, uno por forma de lectura. Nunca
# generados al azar ni escritos por la paciente. Están dentro de lo que
# SIM-1.0 considera normal, pero eso no se afirma aquí: lo dice
# ``clasificar_lectura`` cuando se le pregunta.
HR_SIMULADO = Decimal("80.00")
SPO2_SIMULADO = Decimal("97.00")
MOV_SIMULADO = 12

# Varias lecturas conjuntas por sesión de signos maternos (opcional; por
# omisión, una). La primera es exactamente la de siempre; las siguientes
# varían de forma fija respecto de ella, un minuto antes cada una, de modo que
# la última coincide con el instante de la captura. Movimientos fetales sigue
# siendo un conteo consolidado: una sola lectura.
DESVIOS_HR_SIMULADOS = (Decimal("0"), Decimal("2"), Decimal("-1"), Decimal("1"), Decimal("3"))
DESVIOS_SPO2_SIMULADOS = (Decimal("0"), Decimal("-1"), Decimal("0"), Decimal("1"), Decimal("0"))
INTERVALO_ENTRE_LECTURAS = timedelta(minutes=1)
MAXIMO_DE_LECTURAS_SIMULADAS = 10


class SimulacionNoAplicable(RuntimeError):
    """No se puede formar un paquete válido para este embarazo en este momento.

    No es un fallo del adaptador: es el dominio diciendo que la combinación
    pedida no existe --una semana fuera del catálogo, movimiento fetal antes de
    la semana en que se admite--. ``detalle`` es texto escrito aquí, sin
    valores biométricos.
    """

    def __init__(self, detalle: str) -> None:
        self.detalle = detalle
        super().__init__(detalle)


def construir_paquete_simulado(
    *, provision: Provision, tipo_sesion: TipoSesion, ahora: datetime, lecturas: int = 1
) -> dict:
    """Un paquete con la forma que ``SesionMonitoreoEntrada`` exige, o una negativa.

    ``provision`` ya fue comprobado por quien llama: esta función no decide de
    quién es el embarazo. Lo que sí hace es resolver las tres referencias y
    derivar el semáforo, y negarse en las situaciones en que el dominio no
    admite la sesión pedida.

    La sesión se declara ``COMPLETADA`` en el instante de su última lectura:
    una simulación no tiene sentido pendiente, existe para demostrar que el
    paquete se valida, se guarda y puede sincronizarse. Con ``lecturas=1`` (por
    omisión) es una sola lectura en ``ahora``. Con más, solo en signos
    maternos, son lecturas conjuntas seguidas que terminan en ``ahora``; cada
    una lleva la semana y el semáforo que le corresponden.
    """
    es_movimiento = tipo_sesion is TipoSesion.MOVIMIENTOS_FETALES
    if not 1 <= lecturas <= MAXIMO_DE_LECTURAS_SIMULADAS:
        raise SimulacionNoAplicable(
            f"Una sesión simulada admite de 1 a {MAXIMO_DE_LECTURAS_SIMULADAS} lecturas."
        )
    if es_movimiento and lecturas != 1:
        raise SimulacionNoAplicable(
            "Movimientos fetales es un conteo consolidado: una sola lectura por sesión."
        )

    filas_de_lectura = []
    for indice in range(lecturas):
        instante = ahora - (lecturas - 1 - indice) * INTERVALO_ENTRE_LECTURAS
        semana = semana_gestacional(provision.fecha_inicio_embarazo, instante)
        fila = provision.semana(semana)
        if fila is None:
            raise SimulacionNoAplicable(
                f"Hoy este embarazo va por la semana gestacional {semana}, que el "
                "catálogo no contempla. No se registra una sesión que el servidor "
                "rechazaría."
            )

        if es_movimiento and semana < SEMANA_MINIMA_DE_MOVIMIENTO:
            raise SimulacionNoAplicable(
                f"No se registran movimientos fetales antes de la semana "
                f"{SEMANA_MINIMA_DE_MOVIMIENTO}; este embarazo va por la {semana}."
            )

        desvio = indice % len(DESVIOS_HR_SIMULADOS)
        hr = None if es_movimiento else HR_SIMULADO + DESVIOS_HR_SIMULADOS[desvio]
        spo2 = None if es_movimiento else SPO2_SIMULADO + DESVIOS_SPO2_SIMULADOS[desvio]
        mov = MOV_SIMULADO if es_movimiento else None

        estados = clasificar_lectura(
            hr_valor=hr,
            spo2_valor=spo2,
            mov_valor=mov,
            semana=semana,
            trimestre=fila.trimestre,
        )
        id_semaforo = provision.id_semaforo_de(estados.codigo_global)
        if id_semaforo is None:
            raise SimulacionNoAplicable(
                "El aprovisionamiento de este dispositivo no incluye el nivel de "
                "semáforo que corresponde a esta lectura."
            )

        filas_de_lectura.append({
            "id_tiempo_gest": fila.id_tiempo_gest,
            "id_semaforo": id_semaforo,
            "fecha_hora_captura": instante.isoformat(),
            "fecha_hora_sincronizacion": None,
            "hr_valor": None if hr is None else str(hr),
            "spo2_valor": None if spo2 is None else str(spo2),
            "mov_valor": mov,
        })

    return {
        "id_embarazo": provision.id_embarazo,
        "id_dispositivo": provision.id_dispositivo,
        "tipo_sesion": tipo_sesion.value,
        "fecha_inicio": filas_de_lectura[0]["fecha_hora_captura"],
        "fecha_fin": filas_de_lectura[-1]["fecha_hora_captura"],
        "estado_sesion": EstadoSesion.COMPLETADA.value,
        "lecturas": filas_de_lectura,
    }
