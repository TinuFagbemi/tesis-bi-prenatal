"""Dispositivo de la gestante (simulado): produce una captura local.

Uso desde la raiz del repositorio::

    python scripts/dispositivo_gestante.py capturar                          # movimientos fetales
    python scripts/dispositivo_gestante.py capturar --tipo SIGNOS_MATERNOS
    python scripts/dispositivo_gestante.py capturar --dispositivo DEMO-GESTANTE-02
    python scripts/dispositivo_gestante.py capturar --tipo SIGNOS_MATERNOS --lecturas 5

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

**Varios dispositivos (entorno de pruebas).** ``--dispositivo <codigo>`` usa
el archivo ``<GESTANTE_DISPOSITIVOS_DIR>/<codigo>.json`` que escribe
``provisionar_demo.py provisionar --gestantes N``, en lugar de
``provision.json``. Se elige **un dispositivo**, no una cuenta: la cuenta y el
embarazo siguen saliendo de su aprovisionamiento, cada cuenta tiene su propia
cola y la API solo acepta la sesion si el embarazo pertenece al token con el
que se sincroniza. El codigo se valida contra un patron cerrado, de modo que no
puede señalar otra ruta.

**Varias lecturas por sesion.** ``--lecturas N`` (solo signos maternos; por
omision 1) guarda una sesion con N lecturas conjuntas de FC y SpO2, un minuto
aparte, en un solo paquete y con una sola clave de idempotencia. Movimientos
fetales sigue siendo un conteo consolidado, una lectura.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import argparse
import sys
import re
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

# Codigos de dispositivo de demostracion: DEMO-SCRUM72-01, DEMO-GESTANTE-02...
PATRON_DE_DISPOSITIVO = re.compile(r"DEMO-[A-Z0-9]{1,20}-[0-9]{2}")


def ruta_de_aprovisionamiento(settings: GestanteSettings, dispositivo: str | None) -> Path:
    """``provision.json`` o el archivo de ese dispositivo, nunca otra ruta."""
    if dispositivo is None:
        return settings.provision_path
    if not PATRON_DE_DISPOSITIVO.fullmatch(dispositivo):
        raise ValueError("Codigo de dispositivo no reconocido.")
    return settings.dispositivos_dir / f"{dispositivo}.json"


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
    capturar.add_argument(
        "--dispositivo",
        metavar="CODIGO",
        help=(
            "Dispositivo de demostracion (p. ej. DEMO-GESTANTE-02). Por omision, "
            "el de provision.json."
        ),
    )
    capturar.add_argument(
        "--lecturas",
        type=int,
        default=1,
        metavar="N",
        help="Lecturas conjuntas de la sesion de signos maternos (por omision 1).",
    )
    return parser


def capturar(
    settings: GestanteSettings,
    tipo: TipoSesion,
    ahora: datetime,
    dispositivo: str | None = None,
    lecturas: int = 1,
) -> int:
    try:
        ruta_provision = ruta_de_aprovisionamiento(settings, dispositivo)
    except ValueError as fallo:
        print(f"Error: {fallo}", file=sys.stderr)
        return CODIGO_DE_ERROR
    try:
        aprovisionamiento = provision.cargar(ruta_provision)
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
            lecturas=lecturas,
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
        return capturar(
            configuracion, TipoSesion(argumentos.tipo), reloj(), argumentos.dispositivo,
            argumentos.lecturas,
        )
    except ConfiguracionGestanteInvalida as error:
        print(f"Error: {error}", file=sys.stderr)
        return CODIGO_DE_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
