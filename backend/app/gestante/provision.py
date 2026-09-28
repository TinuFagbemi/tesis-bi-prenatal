"""Las referencias con las que este dispositivo puede formar un paquete válido.

**Por qué existe este archivo y no una consulta.** Un paquete de monitoreo no
lleva valores de negocio: lleva llaves subrogadas --``id_dispositivo``,
``id_tiempo_gest``, ``id_semaforo``-- que solo PostgreSQL conoce. Este
adaptador no abre conexiones a PostgreSQL, y la API central no publica ninguna
ruta que le entregue esas llaves a una paciente, a propósito: son datos
técnicos que SCRUM-98 mantiene fuera de su alcance.

Pero el motivo de fondo es más fuerte que la falta de una ruta: **la captura
tiene que funcionar sin red**. Un dispositivo que necesitara preguntarle al
servidor qué identificador de catálogo usar no podría capturar nada
precisamente cuando más falta hace. Así que las referencias viajan con el
dispositivo, escritas cuando se le aprovisiona, igual que un equipo real se
entrega ya configurado con su identidad y con la gestante a la que sirve.

Ese archivo lo produce ``scripts/provisionar_demo.py`` con la credencial de
mantenimiento, leyendo los catálogos y la asignación reales. Este módulo solo
lo lee y lo valida: no inventa un identificador, no adivina un catálogo y no
completa lo que falte.

**Lo que el archivo no lleva.** Ni el correo, ni el nombre, ni el teléfono de
la cuenta: el adaptador ya evita guardar esos datos en su propio SQLite y no
va a guardarlos aquí. La vinculación con la persona es ``id_usuario``, que es
lo mismo que la sesión local guarda.

Todos los datos son ficticios y simulados.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

VERSION_SOPORTADA = 1


class ProvisionInvalida(RuntimeError):
    """El aprovisionamiento no existe, no se puede leer o no dice lo suficiente.

    ``detalle`` es texto escrito aquí. Nunca lleva el contenido del archivo ni
    su ruta completa: una ruta puede tener el nombre de una persona.
    """

    def __init__(self, detalle: str) -> None:
        self.detalle = detalle
        super().__init__(detalle)


@dataclass(frozen=True)
class SemanaGestacional:
    """Una fila del catálogo ``tiempo_gestacional``, tal como está en la base."""

    id_tiempo_gest: int
    trimestre: int


@dataclass(frozen=True)
class Provision:
    """A qué cuenta, a qué embarazo y con qué catálogos sirve este dispositivo."""

    id_usuario: int
    id_embarazo: int
    fecha_inicio_embarazo: date
    id_dispositivo: int
    semaforos: dict[str, int]
    semanas: dict[int, SemanaGestacional]

    def sirve_a(self, id_usuario: int, id_embarazo: int) -> bool:
        """Si este dispositivo fue aprovisionado para esa cuenta y ese embarazo.

        Las dos condiciones son necesarias. Con solo el embarazo, otra cuenta
        que iniciara sesión en el mismo dispositivo podría registrar en él; con
        solo la cuenta, un identificador manipulado en el navegador escogería
        el embarazo.
        """
        return self.id_usuario == id_usuario and self.id_embarazo == id_embarazo

    def id_semaforo_de(self, codigo: str) -> int | None:
        return self.semaforos.get(codigo)

    def semana(self, semana_gestacion: int) -> SemanaGestacional | None:
        return self.semanas.get(semana_gestacion)


def _exigir(condicion: bool, detalle: str) -> None:
    if not condicion:
        raise ProvisionInvalida(detalle)


def cargar(ruta: Path) -> Provision:
    """Lee el aprovisionamiento de este dispositivo, o explica por qué no puede.

    Cualquier problema --el archivo no está, no es JSON, le falta un campo, un
    catálogo viene vacío-- es una :class:`ProvisionInvalida` con una frase, no
    un valor por omisión: un aprovisionamiento a medias produciría paquetes que
    el servidor rechazaría más tarde, lejos de donde se podría entender por qué.
    """
    if not ruta.exists():
        raise ProvisionInvalida(
            "Este dispositivo todavía no está aprovisionado. Ejecuta "
            "'python scripts/provisionar_demo.py provisionar' para prepararlo."
        )

    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProvisionInvalida(
            "El aprovisionamiento de este dispositivo no se pudo leer."
        ) from error

    _exigir(isinstance(datos, dict), "El aprovisionamiento no tiene la forma esperada.")
    _exigir(
        datos.get("version") == VERSION_SOPORTADA,
        f"El aprovisionamiento declara una versión que esta instalación no "
        f"entiende; esta espera la {VERSION_SOPORTADA}.",
    )

    cuenta = datos.get("cuenta") or {}
    embarazo = datos.get("embarazo") or {}
    dispositivo = datos.get("dispositivo") or {}
    catalogos = datos.get("catalogos") or {}

    try:
        id_usuario = int(cuenta["id_usuario"])
        id_embarazo = int(embarazo["id_embarazo"])
        fecha_inicio = date.fromisoformat(str(embarazo["fecha_inicio"]))
        id_dispositivo = int(dispositivo["id_dispositivo"])
        semaforos = {
            str(codigo): int(identificador)
            for codigo, identificador in (catalogos["semaforo"] or {}).items()
        }
        semanas = {
            int(fila["semana"]): SemanaGestacional(
                id_tiempo_gest=int(fila["id_tiempo_gest"]),
                trimestre=int(fila["trimestre"]),
            )
            for fila in (catalogos["tiempo_gestacional"] or [])
        }
    except (KeyError, TypeError, ValueError) as error:
        raise ProvisionInvalida(
            "Al aprovisionamiento de este dispositivo le falta información o la "
            "tiene en un formato que no corresponde."
        ) from error

    _exigir(semaforos, "El aprovisionamiento no trae el catálogo de semáforo.")
    _exigir(semanas, "El aprovisionamiento no trae el catálogo gestacional.")

    return Provision(
        id_usuario=id_usuario,
        id_embarazo=id_embarazo,
        fecha_inicio_embarazo=fecha_inicio,
        id_dispositivo=id_dispositivo,
        semaforos=semaforos,
        semanas=semanas,
    )
