"""Interfaz web de la gestante de FetalAlert: servidor local del dispositivo.

Uso desde la raiz del repositorio::

    python scripts/gestante_web.py                  # sirve en http://127.0.0.1:8100
    python scripts/gestante_web.py --puerto 9000    # otro puerto
    python scripts/gestante_web.py --comprobar      # revisa la configuracion y sale

Este proceso corre **en el dispositivo de la paciente**. Sirve los tres archivos
de la interfaz y actua de intermediario con lo que ya existe: la API central,
por HTTP; el almacenamiento del nodo edge compartido, en solo lectura; y su
propio archivo SQLite por cuenta para las sesiones de movimiento simuladas,
que captura y sincroniza reutilizando las funciones de ``app.edge`` sin
duplicar ninguna de sus reglas.

**Por que el navegador no llama directamente a la API central.** Porque asi el
token de la sesion nunca entra en JavaScript --vive en la memoria de este
proceso--, porque la interfaz tiene que seguir abriendose cuando el servidor
central no responde, y porque servir la pagina desde el mismo origen que las
rutas evita tener que anadir CORS a la aplicacion central, que este ticket no
toca.

La ruta del SQLite de sesiones, la carpeta de las sesiones de movimiento
simuladas, la URL de la API, el puerto, los tiempos de espera y la duracion de
la ventana de sesion salen de la configuracion del entorno
(``GESTANTE_SQLITE_PATH``, ``GESTANTE_MOVIMIENTOS_DIR``,
``GESTANTE_FRONTEND_DIR``, ``GESTANTE_API_BASE_URL``, ``GESTANTE_HOST``,
``GESTANTE_PORT``, ``GESTANTE_HTTP_TIMEOUT``, ``GESTANTE_BUSY_TIMEOUT_MS``,
``GESTANTE_VENTANA_SESION_HORAS``, ``GESTANTE_COOKIE_SECURE``). ``--puerto`` y
``--host`` permiten cambiar el destino de una demostracion sin tocar el entorno.

**Este comando no recibe ninguna credencial.** No hay opcion para pasar una
contrasena, un token ni una URL de base de datos, y no la habra: la paciente
escribe sus credenciales en la interfaz, viajan una vez hacia la API central y
no se guardan. ``EDGE_API_TOKEN`` es del nodo edge y este proceso no lo lee.

**Tampoco inicializa el almacenamiento del nodo edge.** Si no existe, la
interfaz lo dice y explica que se crea con ``python scripts/edge_node.py init``.
Crear la base del nodo es una decision de quien lo opera.

Este proceso no abre ninguna conexion a PostgreSQL y no escribe ninguna fila
clinica. Tampoco imprime nunca una credencial, una cookie ni el contenido de un
paquete.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ_DEL_REPOSITORIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_DEL_REPOSITORIO / "backend"))

import uvicorn  # noqa: E402  -- tras ajustar sys.path

from app.gestante.aplicacion import crear_aplicacion  # noqa: E402
from app.gestante.config import (  # noqa: E402
    ConfiguracionGestanteInvalida,
    GestanteSettings,
    cargar_settings_gestante,
)

CODIGO_DE_EXITO = 0
CODIGO_DE_ERROR = 1

# Lo que uvicorn registra en consola. ``warning`` y no ``info`` a proposito: el
# registro de acceso de ``info`` imprime cada URL solicitada, y aunque hoy
# ninguna ruta de este adaptador lleva datos en la URL, un registro que crece
# solo es una cosa menos que vigilar en el dispositivo de una paciente.
NIVEL_DE_REGISTRO = "warning"


def construir_parser(settings: GestanteSettings) -> argparse.ArgumentParser:
    """Argumentos del comando, con los valores por omision de la configuracion.

    Recibe la configuracion en lugar de leer un global: los valores que publica
    ``--help`` salen del entorno, asi que construir el parser puede fallar si el
    entorno esta mal, y eso tiene que ocurrir dentro del limite controlado de
    ``main()``.

    No existe una opcion para pasar credenciales ni una URL de base de datos: la
    interfaz no las necesita y no debe poder recibirlas.
    """
    parser = argparse.ArgumentParser(
        prog="gestante_web.py",
        description=(
            "Servidor local de la interfaz de la gestante de FetalAlert. Sirve "
            "la interfaz, autentica contra la API central y muestra el estado "
            "local del nodo edge. Datos simulados."
        ),
    )
    parser.add_argument(
        "--host",
        default=settings.host,
        help=(
            "Direccion en la que escuchar. Por omision, la de GESTANTE_HOST "
            f"({settings.host}). Loopback a proposito: la interfaz se sirve al "
            "dispositivo que la usa, no a una red."
        ),
    )
    parser.add_argument(
        "--puerto",
        type=int,
        default=settings.port,
        help=f"Puerto en el que escuchar (por omision {settings.port}).",
    )
    parser.add_argument(
        "--comprobar",
        action="store_true",
        help=(
            "Revisa la configuracion y los archivos de la interfaz, informa, y "
            "sale sin levantar el servidor."
        ),
    )
    return parser


def _archivos_de_la_interfaz(settings: GestanteSettings) -> list[Path]:
    """Los tres archivos que el adaptador sirve."""
    return [
        settings.frontend_dir / "index.html",
        settings.frontend_dir / "styles.css",
        settings.frontend_dir / "app.js",
    ]


def comprobar(settings: GestanteSettings) -> int:
    """Informa de la configuracion efectiva, sin levantar nada.

    No imprime ninguna credencial porque no hay ninguna que imprimir: esta
    configuracion no tiene campos para secretos.
    """
    print("Interfaz de la gestante — configuracion efectiva:")
    print(f"  interfaz            : {settings.frontend_dir}")
    print(f"  sesiones (SQLite)   : {settings.sqlite_path}")
    print(f"  movimientos (SQLite): {settings.movimientos_dir} (un archivo por cuenta)")
    print(f"  API central         : {settings.api_base_url}")
    print(f"  escucha en          : http://{settings.host}:{settings.port}")
    print(f"  ventana de sesion   : {settings.ventana_sesion_horas} h")
    print(f"  cookie Secure       : {settings.cookie_secure}")

    faltantes = [ruta for ruta in _archivos_de_la_interfaz(settings) if not ruta.is_file()]
    if faltantes:
        print("", file=sys.stderr)
        print("Faltan archivos de la interfaz:", file=sys.stderr)
        for ruta in faltantes:
            print(f"  - {ruta}", file=sys.stderr)
        return CODIGO_DE_ERROR

    print("  archivos            : los tres estan presentes.")
    return CODIGO_DE_EXITO


def servir(settings: GestanteSettings, host: str, puerto: int) -> int:
    """Levanta el adaptador. Bloquea hasta que se interrumpe."""
    faltantes = [ruta for ruta in _archivos_de_la_interfaz(settings) if not ruta.is_file()]
    if faltantes:
        print(
            "Error: faltan archivos de la interfaz. Ejecuta con --comprobar "
            "para ver cuales.",
            file=sys.stderr,
        )
        return CODIGO_DE_ERROR

    aplicacion = crear_aplicacion(settings=settings)

    print(f"FetalAlert — interfaz de la gestante en http://{host}:{puerto}")
    print("Datos simulados. Prototipo academico. Ctrl+C para detener.")

    uvicorn.run(aplicacion, host=host, port=puerto, log_level=NIVEL_DE_REGISTRO)
    return CODIGO_DE_EXITO


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada.

    La configuracion se carga **dentro** del ``try``, igual que en
    ``scripts/edge_node.py`` desde SCRUM-65: una variable ``GESTANTE_*`` mal
    escrita se convierte en una frase y un codigo de salida, no en un traceback
    lanzado mientras se construia el parser.
    """
    try:
        settings = cargar_settings_gestante()
        argumentos = construir_parser(settings).parse_args(argv)

        if argumentos.comprobar:
            return comprobar(settings)

        return servir(settings, argumentos.host, argumentos.puerto)
    except ConfiguracionGestanteInvalida as error:
        print(f"Error: {error}", file=sys.stderr)
        return CODIGO_DE_ERROR
    except KeyboardInterrupt:
        print("")
        print("Interfaz detenida.")
        return CODIGO_DE_EXITO


if __name__ == "__main__":
    raise SystemExit(main())
