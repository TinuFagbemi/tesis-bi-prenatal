"""Dispositivo de la gestante (simulado): produce una captura local.

Uso desde la raiz del repositorio::

    python scripts/dispositivo_gestante.py capturar                          # movimientos fetales
    python scripts/dispositivo_gestante.py capturar --tipo SIGNOS_MATERNOS

**Que representa.** En produccion, una captura la origina el dispositivo
fisico. Este MVP no tiene hardware, asi que este comando representa ese evento:
una sola captura cada vez que se ejecuta. No es un control de la paciente --la
interfaz web no ofrece capturar ni enviar-- y no genera lecturas periodicas por
su cuenta.

**Que hace.** Lee el aprovisionamiento del dispositivo (``provision.json``, que
escribe ``scripts/provisionar_demo.py``) y de el, y solo de el, toma la cuenta,
el embarazo, el dispositivo y los catalogos. Llama a
:func:`app.gestante.movimientos.registrar_sesion_simulada`, que arma el paquete
con los valores fijos de ``app.gestante.simulacion`` --en movimientos,
``MOV_SIMULADO`` y FC/SpO2 nulos-- y lo guarda con :func:`app.edge.capturar`
en ``data/gestante/movimientos/cuenta-<id_usuario>.sqlite3``, en estado
``PENDIENTE``. Es la misma cola que entrega el envio automatico del portal
(``app.gestante.envio_automatico``) cuando hay conexion y una sesion vigente de
esa paciente.

**Lo que no hace, a proposito.** No usa la red ni ninguna credencial: capturar
tiene que funcionar sin Internet. No acepta ``id_usuario``, ``id_embarazo`` ni
``id_dispositivo`` como argumentos: solo el aprovisionamiento decide a quien
sirve este dispositivo. No envia nada ni crea otra cola, ni reintentos, ni
idempotencia propios: todo eso es ``app.edge``. No abre PostgreSQL. No imprime
el contenido del paquete.

La ruta del aprovisionamiento y la carpeta de las colas salen de
``GESTANTE_PROVISION_PATH`` y ``GESTANTE_MOVIMIENTOS_DIR``, las mismas que usa
el portal.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

RAIZ_DEL_REPOSITORIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_DEL_REPOSITORIO / "backend"))

from app.edge.captura import PaqueteInvalido  # noqa: E402  -- tras ajustar sys.path
from app.gestante import movimientos, provision  # noqa: E402
from app.gestante.config import (  # noqa: E402
    ConfiguracionGestanteInvalida,
    GestanteSettings,
    cargar_settings_gestante,
)
from app.gestante.simulacion import SimulacionNoAplicable  # noqa: E402
from app.models.enums import TipoSesion  # noqa: E402

CODIGO_DE_EXITO = 0
CODIGO_DE_ERROR = 1


def ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


def construir_parser() -> argparse.ArgumentParser:
    """Una orden y un solo argumento: el tipo de sesion.

    No hay opciones para la cuenta, el embarazo ni el dispositivo, ni para una
    credencial: si existieran, cualquiera podria escribir en la cola de otra
    paciente o hacer pasar una captura por otro dispositivo.
    """
    parser = argparse.ArgumentParser(
        prog="dispositivo_gestante.py",
        description=(
            "Dispositivo simulado de la gestante: produce una captura local, sin "
            "red, en la cola de la cuenta aprovisionada. Datos simulados."
        ),
    )
    ordenes = parser.add_subparsers(dest="orden", required=True)
    capturar = ordenes.add_parser(
        "capturar",
        help="Guarda una captura simulada como PENDIENTE. No usa la red.",
    )
    capturar.add_argument(
        "--tipo",
        choices=[tipo.value for tipo in TipoSesion],
        default=TipoSesion.MOVIMIENTOS_FETALES.value,
        help="Tipo de sesion (por omision MOVIMIENTOS_FETALES).",
    )
    return parser


def capturar(
    settings: GestanteSettings, tipo: TipoSesion, ahora: datetime
) -> int:
    try:
        aprovisionamiento = provision.cargar(settings.provision_path)
    except provision.ProvisionInvalida as fallo:
        print(f"Error: {fallo.detalle}", file=sys.stderr)
        return CODIGO_DE_ERROR

    try:
        registro = movimientos.registrar_sesion_simulada(
            settings,
            id_usuario=aprovisionamiento.id_usuario,
            provision=aprovisionamiento,
            tipo_sesion=tipo,
            ahora=ahora,
        )
    except (SimulacionNoAplicable, PaqueteInvalido) as fallo:
        print(f"Error: {fallo.detalle}", file=sys.stderr)
        return CODIGO_DE_ERROR

    ruta = movimientos.ruta_para_la_cuenta(settings, aprovisionamiento.id_usuario)
    # Identificadores locales y la clave, que no es un dato clinico. Nunca el
    # paquete.
    print("Captura guardada en el dispositivo como PENDIENTE. No se ha usado la red.")
    print(f"  cola             : {ruta}")
    print(f"  id_outbox        : {registro.id_outbox}")
    print(f"  Idempotency-Key  : {registro.clave}")
    print(
        "  La entrega la hace el portal automaticamente cuando hay conexion y "
        "una sesion vigente de la paciente."
    )
    return CODIGO_DE_EXITO


def main(
    argv: list[str] | None = None,
    *,
    settings: GestanteSettings | None = None,
    reloj: Callable[[], datetime] = ahora_utc,
) -> int:
    """Punto de entrada. ``settings`` y ``reloj`` existen para las pruebas."""
    try:
        argumentos = construir_parser().parse_args(argv)
        configuracion = settings or cargar_settings_gestante()
        return capturar(configuracion, TipoSesion(argumentos.tipo), reloj())
    except ConfiguracionGestanteInvalida as error:
        print(f"Error: {error}", file=sys.stderr)
        return CODIGO_DE_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
