"""Configuracion del adaptador de la interfaz de la gestante (SCRUM-72).

Nueve ajustes, leidos con el mismo mecanismo que ya usan el backend y el nodo
edge --``pydantic-settings`` sobre el ``.env`` del repositorio-- pero bajo su
propio prefijo ``GESTANTE_`` y en una clase propia.

**Por que una clase separada y no ampliar ``app.config.Settings``.** Aquella
lleva ``database_url``, y este adaptador no debe abrir jamas una conexion a
PostgreSQL: todo lo central lo pide a la API por HTTP. Mantenerlas aparte hace
que esa regla la imponga lo que es alcanzable, no la disciplina. Es la misma
razon por la que ``app.edge.config`` tiene la suya.

**Aqui no hay ninguna credencial, y la ausencia es deliberada.** No existe campo
para una contrasena, para un secreto de firma ni para ``EDGE_API_TOKEN``. El
adaptador no firma nada: entrega las credenciales que escribe la paciente a
``POST /api/v1/autenticacion/token`` y guarda lo que reciba en memoria, nunca en
disco ni en configuracion. ``EDGE_API_TOKEN`` pertenece al nodo edge y este
proceso no lo lee, no lo reenvia y no lo necesita.

**La ventana de sesion local vive aqui y solo aqui.** :data:`VENTANA_POR_OMISION_HORAS`
es la unica declaracion del valor en todo el repositorio; la cookie deriva su
``Max-Age`` del mismo instante de expiracion que guarda SQLite, de modo que
cambiar la configuracion cambia las dos cosas a la vez y no pueden separarse.

**Nada se construye al importar.** Igual que en ``app.edge.config`` desde
SCRUM-65: una variable mal escrita tiene que fallar dentro del limite
controlado del arranque, con una frase y un codigo de salida, no con un
traceback en mitad de un import.

Todos los datos que maneja esta interfaz son ficticios y simulados.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = REPOSITORY_ROOT / ".env"

# Archivos estaticos de la interfaz. Viven fuera de ``backend/`` porque no son
# codigo de la aplicacion: son la interfaz que el navegador descarga.
DIRECTORIO_FRONTEND_POR_OMISION = REPOSITORY_ROOT / "frontend" / "gestante"

# Bajo ``data/``, junto al dataset generado y a la base del nodo edge, y
# ignorado por Git igual que ellos. Es estado de un dispositivo concreto.
RUTA_SQLITE_POR_OMISION = REPOSITORY_ROOT / "data" / "gestante" / "sesion_local.sqlite3"

# Carpeta donde vive, **una base SQLite por cuenta**, el registro local de
# sesiones de movimiento simuladas (SCRUM-72). Nunca un archivo compartido: dos
# pacientes de este mismo dispositivo no deben poder ver ni tocar la cola de
# capturas de la otra, y separar el archivo por cuenta lo garantiza sin tocar
# el esquema ni las reglas de ``app.edge``.
RUTA_MOVIMIENTOS_POR_OMISION = REPOSITORY_ROOT / "data" / "gestante" / "movimientos"

# API central del entorno de desarrollo. No es un destino de despliegue: este
# proyecto no tiene ninguno.
URL_API_POR_OMISION = "http://127.0.0.1:8000"

# Donde escucha el adaptador. Loopback a proposito: la interfaz se sirve al
# dispositivo que la usa, no a una red.
HOST_POR_OMISION = "127.0.0.1"
PUERTO_POR_OMISION = 8100

# Segundos. Suficiente para una peticion local, corto para que un equipo sin
# conectividad no parezca colgado.
TIMEOUT_HTTP_POR_OMISION = 10.0

# Milisegundos que SQLite espera un bloqueo antes de fallar. Mismo criterio que
# el nodo edge.
ESPERA_DE_BLOQUEO_POR_OMISION = 5000

# ---------------------------------------------------------------------------
# La ventana de la sesion local
# ---------------------------------------------------------------------------

# **La unica declaracion de este numero en el repositorio.** Tres dias es lo que
# se acordo para que una paciente en una zona con conectividad intermitente
# pueda abrir y cerrar la interfaz sin volver a escribir sus credenciales en
# cada apertura.
#
# La ventana autoriza el **uso local** de la interfaz. No prolonga la vida del
# token central ni la sustituye: son dos relojes distintos, y el del servidor lo
# gobierna SCRUM-70.
VENTANA_POR_OMISION_HORAS = 72.0

# Cotas de la ventana. El suelo impide una ventana nula o negativa --una sesion
# que nace expirada--; el techo evita que una variable mal puesta autorice un
# dispositivo durante meses.
VENTANA_MINIMA_HORAS = 0.25
VENTANA_MAXIMA_HORAS = 720.0

# ``Secure`` en la cookie. En el MVP local se sirve HTTP plano y el navegador
# descartaria una cookie ``Secure``, asi que por omision esta desactivada. Se
# expone como ajuste para que la decision no quede cableada en el codigo: este
# prototipo no implementa HTTPS/TLS, y eso es una limitacion del entorno, no una
# propiedad del diseno.
COOKIE_SECURE_POR_OMISION = False

# Nombre de la cookie de sesion local. Lo que viaja en ella es un identificador
# opaco y nada mas: ni el token central, ni el correo, ni el rol, ni dato alguno
# de la cuenta.
NOMBRE_DE_COOKIE = "fa_sesion"


class ConfiguracionGestanteInvalida(RuntimeError):
    """El adaptador no puede operar con la configuracion que recibio.

    Lleva solo el mensaje saneado que construye :func:`cargar_settings_gestante`.
    El valor rechazado nunca se adjunta, se formatea ni se registra.
    """


class GestanteSettings(BaseSettings):
    """Ajustes del adaptador, leidos de variables ``GESTANTE_*``."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="GESTANTE_",
    )

    sqlite_path: Path = RUTA_SQLITE_POR_OMISION
    movimientos_dir: Path = RUTA_MOVIMIENTOS_POR_OMISION
    frontend_dir: Path = DIRECTORIO_FRONTEND_POR_OMISION
    api_base_url: str = URL_API_POR_OMISION
    host: str = HOST_POR_OMISION
    port: int = Field(default=PUERTO_POR_OMISION, ge=1, le=65535)
    http_timeout: float = Field(default=TIMEOUT_HTTP_POR_OMISION, gt=0)
    busy_timeout_ms: int = Field(default=ESPERA_DE_BLOQUEO_POR_OMISION, ge=0)

    ventana_sesion_horas: float = Field(
        default=VENTANA_POR_OMISION_HORAS,
        ge=VENTANA_MINIMA_HORAS,
        le=VENTANA_MAXIMA_HORAS,
    )
    cookie_secure: bool = COOKIE_SECURE_POR_OMISION

    @property
    def ventana_en_segundos(self) -> int:
        """La ventana en segundos enteros.

        Una sola conversion, usada tanto para calcular ``expira_en`` como para
        el ``Max-Age`` de la cookie, de modo que los dos no puedan discrepar.
        """
        return int(self.ventana_sesion_horas * 3600)


def _detalle_de_validacion(error: ValidationError) -> str:
    """Que variables ``GESTANTE_*`` estan mal y por que, sin repetir un valor.

    La representacion propia de un ``ValidationError`` incluye ``input_value``,
    y una variable de entorno puede llevar cualquier cosa --una ruta con el
    nombre de alguien, una credencial pegada por error--. Asi que el mensaje se
    reconstruye con las dos partes de cada error que son esquema y no dato: el
    campo y el tipo de error. Misma regla que sigue ``app.edge.config``.
    """
    partes = []
    for detalle in error.errors():
        campo = ".".join(str(tramo) for tramo in detalle.get("loc", ()))
        variable = f"GESTANTE_{campo.upper()}" if campo else "GESTANTE_*"
        partes.append(f"{variable}: {detalle.get('type', 'invalido')}")
    return "; ".join(partes)


def cargar_settings_gestante() -> GestanteSettings:
    """Lee los ajustes ``GESTANTE_*``, convirtiendo un entorno malo en una frase.

    El unico sitio donde este paquete construye un :class:`GestanteSettings`. Se
    llama desde dentro del ``try`` del arranque, de modo que una variable mal
    formada sea un codigo de salida y una linea en stderr, no un traceback.

    Solo se captura :class:`pydantic.ValidationError`, no ``Exception``: un
    archivo ausente o un error de permisos no es un problema de configuracion y
    no debe disfrazarse de uno.
    """
    try:
        return GestanteSettings()
    except ValidationError as error:
        raise ConfiguracionGestanteInvalida(
            "La configuracion de la interfaz de la gestante no es valida "
            f"({_detalle_de_validacion(error)}). Revisa las variables GESTANTE_* "
            "del entorno o del archivo .env."
        ) from error
