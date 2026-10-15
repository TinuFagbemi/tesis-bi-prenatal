"""Demo Control de FetalAlert: preparación y controles básicos de la sustentación.

Uso desde la raíz del repositorio, con el entorno virtual del backend::

    .\\backend\\.venv\\Scripts\\python.exe scripts\\demo_control.py

**Qué es.** Una consola interna para quien prepara y presenta la demostración
técnica (SCRUM-104). No es una interfaz para pacientes, médicos ni
administradores, ni un módulo de pruebas. **Orquesta lo que ya existe** y no
reimplementa nada:

1. Verificar estado -- solo lectura: PostgreSQL en transacciones READ ONLY,
   la cola SQLite de la cuenta con ``mode=ro``, Docker con ``ps``/``inspect``/
   ``config`` y HTTP con ``GET``. No corrige nada.
2. Restablecer baseline -- solo artefactos locales: reconstruye
   ``provision.json`` a partir de la base, leída en solo lectura, con escritura
   atómica y respaldo. **Ninguna escritura en PostgreSQL.** Una cola con
   capturas sin entregar no se toca: requiere preparación.
3. Simular desconexión -- ``docker compose stop api`` y comprobación de que
   la API del portal dejó de responder.
4. Restablecer conexión -- ``docker compose start api`` y ``GET /health``.
5. Previsualizar la próxima carga ETL -- ``etl_analitico.py previsualizar``.
6. Ejecutar ETL -- ``etl_analitico.py ejecutar``, la misma ejecución
   transaccional de siempre, tras validar el destino y pedir ``EJECUTAR``.

La observación del dato --operacional, analítica, publicación y auditoría--
sigue en Adminer (SCRUM-103, ``docs/demo_adminer.md``).

**Nunca imprime** una URL de conexión, una contraseña, un token, el ``.env``
ni el entorno de un contenedor, ni un valor biométrico o un dato personal.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import filecmp
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

RAIZ_DEL_REPOSITORIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_DEL_REPOSITORIO / "backend"))

from sqlalchemy import create_engine, text  # noqa: E402  -- tras ajustar sys.path
from sqlalchemy.engine import Connection, make_url  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.config import (  # noqa: E402
    VARIABLE_URL_ALEMBIC,
    VARIABLE_URL_API,
    VARIABLE_URL_ETL,
    UrlDeEntornoInvalida,
    _leer_del_entorno,
    exigir_url_de_entorno,
)
from app.edge.almacenamiento import (  # noqa: E402
    TABLA_CAPTURA,
    TABLA_INTENTO,
    TABLA_OUTBOX,
    VERSION_DE_ESQUEMA,
)
from app.etl import reglas  # noqa: E402
from app.etl.ejecucion import candado_ocupado, describir_error_de_base  # noqa: E402
from app.gestante import provision as provision_local  # noqa: E402
from app.gestante.config import (  # noqa: E402
    ConfiguracionGestanteInvalida,
    GestanteSettings,
    cargar_settings_gestante,
)
from app.gestante.movimientos import ruta_para_la_cuenta  # noqa: E402
from app.loader.postgres import preflight, sanear_mensaje  # noqa: E402
from app.services.ingesta import semana_gestacional  # noqa: E402


def _cargar_provisionar_demo():
    """``scripts/provisionar_demo.py`` como módulo, para reutilizar su lectura.

    Se carga por ruta y no por nombre: así funciona igual lanzado como script
    que importado desde las pruebas.
    """
    ruta = Path(__file__).resolve().parent / "provisionar_demo.py"
    especificacion = importlib.util.spec_from_file_location("provisionar_demo", ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    sys.modules.setdefault("provisionar_demo", modulo)
    especificacion.loader.exec_module(modulo)
    return modulo


provisionar_demo = _cargar_provisionar_demo()

CODIGO_DE_EXITO = 0
CODIGO_INTERRUMPIDO = 130

# Identidades esperadas de cada credencial. Son nombres de rol, no secretos.
ROL_API = "fetalalert_api"
ROL_ETL = "fetalalert_etl"

SERVICIO_DB = "db"
SERVICIO_API = "api"
SERVICIO_ADMINER = "adminer"
PUERTO_INTERNO_DB = 5432
PUERTO_INTERNO_API = 8000
PUERTO_POSTGRES_POR_OMISION = 5433  # el mismo valor por omisión del Compose
URL_ADMINER = "http://127.0.0.1:8090/"
JWT_MINUTOS_POR_OMISION = 30

HOSTS_LOCALES = frozenset({"127.0.0.1", "localhost", "::1"})

# Plazos, en segundos.
PLAZO_DOCKER = 60.0
PLAZO_ETL = 900.0
PLAZO_HTTP = 3.0
PLAZO_CONEXION = 5
PLAZO_SALUD_API = 60.0
INTERVALO_SALUD_API = 1.0

# Semanas antes del final del catálogo a partir de las cuales se advierte.
MARGEN_DE_SEMANAS = 2

CONFIRMACION_ETL = "EJECUTAR"

ZONA_CLINICA = reglas.ZONA_HORARIA_CLINICA

SEPARADOR = "=" * 58
LINEA = "-" * 58

# ---------------------------------------------------------------------------
# Dependencias, reemplazables en las pruebas
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Proceso:
    """Lo que devolvió un proceso hijo."""

    codigo: int
    salida: str
    error: str


def _ejecutar_proceso(argumentos: list[str], plazo: float, raiz: Path) -> Proceso:
    """Un proceso hijo con argumentos en lista: nunca pasa por una shell."""
    entorno = dict(os.environ)
    entorno["PYTHONIOENCODING"] = "utf-8"
    completado = subprocess.run(  # noqa: S603 -- argumentos fijos, sin shell
        argumentos,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=plazo,
        cwd=str(raiz),
        env=entorno,
        check=False,
    )
    return Proceso(completado.returncode, completado.stdout, completado.stderr)


def _http_get(url: str, plazo: float) -> tuple[int | None, str]:
    """Solo GET. ``(None, clase)`` si no hubo respuesta."""
    try:
        with urllib.request.urlopen(url, timeout=plazo) as respuesta:  # noqa: S310
            return respuesta.status, respuesta.read(512).decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, ""
    except (urllib.error.URLError, OSError, ValueError) as error:
        return None, type(error).__name__


@contextmanager
def _solo_lectura(url: str) -> Iterator[Connection]:
    """Una conexión PostgreSQL que no puede escribir, por dos vías.

    ``default_transaction_read_only=on`` en la sesión y una transacción
    REPEATABLE READ declarada READ ONLY: PostgreSQL rechaza cualquier INSERT,
    UPDATE, DELETE, DDL o ``nextval``. La transacción siempre se revierte.
    """
    motor = create_engine(
        url,
        poolclass=NullPool,
        connect_args={
            "options": "-c default_transaction_read_only=on",
            # Una base caida debe informarse en segundos, no en minutos.
            "connect_timeout": PLAZO_CONEXION,
        },
    )
    try:
        with motor.connect() as conexion:
            conexion.execution_options(
                isolation_level="REPEATABLE READ", postgresql_readonly=True
            )
            transaccion = conexion.begin()
            try:
                yield conexion
            finally:
                transaccion.rollback()
    finally:
        motor.dispose()


@dataclass
class Dependencias:
    """Todo lo que toca el mundo exterior. Las pruebas lo sustituyen."""

    entrada: Callable[[str], str] = input
    salida: Callable[[str], None] = print
    ejecutar: Callable[[list[str], float, Path], Proceso] = _ejecutar_proceso
    http_get: Callable[[str, float], tuple[int | None, str]] = _http_get
    conectar: Callable[[str], object] = _solo_lectura
    leer_variable: Callable[[str], str | None] = _leer_del_entorno
    exigir_url: Callable[[str], str] = exigir_url_de_entorno
    ajustes_gestante: Callable[[], GestanteSettings] = cargar_settings_gestante
    reloj: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    dormir: Callable[[float], None] = time.sleep
    raiz: Path = RAIZ_DEL_REPOSITORIO
    python: str = sys.executable


# ---------------------------------------------------------------------------
# Errores y salida segura
# ---------------------------------------------------------------------------


class EntornoNoValidado(RuntimeError):
    """El destino no se pudo identificar sin dudas. Se falla cerrado."""

    def __init__(self, motivos: list[str]) -> None:
        self.motivos = motivos
        super().__init__("; ".join(motivos))


class DockerNoPermitido(RuntimeError):
    """Un subcomando de Docker fuera de la lista blanca. Es un defecto."""


def _decir(dep: Dependencias, texto: str = "") -> None:
    """Toda la salida pasa por aquí: una URL con credenciales nunca sale."""
    dep.salida(sanear_mensaje(texto))


def _describir_excepcion(error: BaseException) -> str:
    if isinstance(error, SQLAlchemyError):
        return describir_error_de_base(error)
    detalle = getattr(error, "detalle", None)
    if isinstance(detalle, str):
        return sanear_mensaje(detalle)
    return type(error).__name__


# ---------------------------------------------------------------------------
# Docker: lista blanca de subcomandos
# ---------------------------------------------------------------------------

SUBCOMANDOS_DE_LECTURA = frozenset({"ps", "config"})
CONTROL_PERMITIDO = frozenset({("stop", SERVICIO_API), ("start", SERVICIO_API)})


def _compose(dep: Dependencias, *argumentos: str, control: bool = False) -> Proceso:
    """``docker compose`` fijado a este repositorio y limitado a la lista blanca.

    De lectura: ``ps`` y ``config``. De control, solo con ``control=True``:
    ``stop api`` y ``start api``. Cualquier otra cosa es un defecto y se
    rechaza antes de lanzar nada.
    """
    if control:
        if tuple(argumentos) not in CONTROL_PERMITIDO:
            raise DockerNoPermitido(f"compose {' '.join(argumentos)}")
    elif not argumentos or argumentos[0] not in SUBCOMANDOS_DE_LECTURA:
        raise DockerNoPermitido(f"compose {' '.join(argumentos)}")
    comando = [
        "docker",
        "compose",
        "--project-directory",
        str(dep.raiz),
        "--profile",
        "demo",
        *argumentos,
    ]
    return dep.ejecutar(comando, PLAZO_DOCKER, dep.raiz)


def _inspect(dep: Dependencias, identificadores: list[str]) -> Proceso:
    return dep.ejecutar(["docker", "inspect", *identificadores], PLAZO_DOCKER, dep.raiz)


@dataclass(frozen=True)
class Contenedor:
    """Un contenedor del proyecto. ``entorno`` nunca se imprime."""

    identificador: str
    servicio: str
    estado: str
    salud: str | None
    etiquetas: dict
    puertos: dict
    redes: dict
    entorno: tuple[str, ...] = field(repr=False, default=())

    @property
    def en_marcha(self) -> bool:
        return self.estado == "running"

    def puerto_publicado(self, interno: int) -> tuple[str, int] | None:
        enlaces = self.puertos.get(f"{interno}/tcp") or []
        if len(enlaces) != 1:
            return None
        try:
            return enlaces[0].get("HostIp") or "", int(enlaces[0].get("HostPort"))
        except (TypeError, ValueError):
            return None

    def variable(self, nombre: str) -> str | None:
        prefijo = f"{nombre}="
        for entrada in self.entorno:
            if entrada.startswith(prefijo):
                return entrada[len(prefijo):]
        return None


def _lineas_json(texto: str) -> list[dict]:
    """``docker compose ps --format json``: un arreglo o un objeto por línea."""
    texto = texto.strip()
    if not texto:
        return []
    if texto.startswith("["):
        return list(json.loads(texto))
    return [json.loads(linea) for linea in texto.splitlines() if linea.strip()]


def leer_contenedores(dep: Dependencias) -> dict[str, list[Contenedor]]:
    """Los contenedores del proyecto Compose de este repositorio, por servicio."""
    try:
        listado = _compose(dep, "ps", "-a", "--format", "json")
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise EntornoNoValidado(
            [f"Docker no está disponible ({type(error).__name__})."]
        ) from None
    if listado.codigo != 0:
        raise EntornoNoValidado(["'docker compose ps' falló; Docker no responde."])
    try:
        filas = _lineas_json(listado.salida)
    except json.JSONDecodeError:
        raise EntornoNoValidado(
            ["'docker compose ps' devolvió un formato inesperado."]
        ) from None
    identificadores = [str(fila.get("ID")) for fila in filas if fila.get("ID")]
    if not identificadores:
        return {}
    detalle = _inspect(dep, identificadores)
    if detalle.codigo != 0:
        raise EntornoNoValidado(["'docker inspect' falló."])
    try:
        inspeccion = json.loads(detalle.salida)
    except json.JSONDecodeError:
        raise EntornoNoValidado(["'docker inspect' devolvió un formato inesperado."]) from None

    por_servicio: dict[str, list[Contenedor]] = {}
    for datos in inspeccion:
        configuracion = datos.get("Config") or {}
        etiquetas = configuracion.get("Labels") or {}
        estado = datos.get("State") or {}
        redes = (datos.get("NetworkSettings") or {}).get("Networks") or {}
        contenedor = Contenedor(
            identificador=str(datos.get("Id", ""))[:12],
            servicio=etiquetas.get("com.docker.compose.service", ""),
            estado=estado.get("Status", "desconocido"),
            salud=(estado.get("Health") or {}).get("Status"),
            etiquetas=dict(etiquetas),
            puertos=(datos.get("HostConfig") or {}).get("PortBindings") or {},
            redes={nombre: (red or {}).get("IPAddress") for nombre, red in redes.items()},
            entorno=tuple(configuracion.get("Env") or ()),
        )
        por_servicio.setdefault(contenedor.servicio, []).append(contenedor)
    return por_servicio


def _misma_ruta(a: str, b: Path) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(
        os.path.normpath(str(b))
    )


def _del_proyecto(dep: Dependencias, contenedor: Contenedor) -> bool:
    """El contenedor lo creó Compose desde **este** directorio y este archivo."""
    etiquetas = contenedor.etiquetas
    directorio = etiquetas.get("com.docker.compose.project.working_dir", "")
    archivos = etiquetas.get("com.docker.compose.project.config_files", "")
    esperado = dep.raiz / "docker-compose.yml"
    return _misma_ruta(directorio, dep.raiz) and any(
        _misma_ruta(archivo, esperado) for archivo in archivos.split(",") if archivo
    )


def hashes_de_configuracion(dep: Dependencias) -> dict[str, str]:
    """El hash que Compose calcula hoy para cada servicio, con el ``.env`` actual.

    Compose guarda ese mismo hash en la etiqueta ``config-hash`` del contenedor
    al crearlo; si difieren, el contenedor se creó con otra configuración.
    """
    servicios = f"{SERVICIO_DB},{SERVICIO_API},{SERVICIO_ADMINER}"
    resultado = _compose(dep, "config", "--hash", servicios)
    if resultado.codigo != 0:
        raise EntornoNoValidado(["'docker compose config --hash' falló."])
    hashes = {}
    for linea in resultado.salida.splitlines():
        partes = linea.split()
        if len(partes) == 2:
            hashes[partes[0]] = partes[1]
    return hashes


def _uno_del_servicio(
    contenedores: dict[str, list[Contenedor]], servicio: str
) -> Contenedor | None:
    lista = contenedores.get(servicio) or []
    if len(lista) > 1:
        raise EntornoNoValidado(
            [f"Hay {len(lista)} contenedores del servicio '{servicio}'; se esperaba uno."]
        )
    return lista[0] if lista else None


# ---------------------------------------------------------------------------
# Validación del entorno de sustentación (falla cerrada)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntornoValidado:
    """El destino identificado sin dudas. Solo nombres y números, sin secretos."""

    base: str
    proyecto: str
    puerto_db: int
    ip_db: str
    rol_etl: str
    episodio: dict
    contenedores: dict = field(repr=False)


def _puerto_postgres(dep: Dependencias) -> int:
    crudo = dep.leer_variable("POSTGRES_PORT")
    try:
        return int(crudo) if crudo and crudo.strip() else PUERTO_POSTGRES_POR_OMISION
    except ValueError:
        raise EntornoNoValidado(["POSTGRES_PORT no es un número."]) from None


def validar_urls(dep: Dependencias) -> dict:
    """Las tres credenciales nombran la misma base, en el servidor esperado.

    Sin conectarse. Nunca se devuelve ni se imprime una URL.
    """
    motivos: list[str] = []
    try:
        etl = make_url(dep.exigir_url(VARIABLE_URL_ETL))
        alembic = make_url(dep.exigir_url(VARIABLE_URL_ALEMBIC))
    except UrlDeEntornoInvalida as error:
        raise EntornoNoValidado([sanear_mensaje(str(error))]) from None
    crudo_api = dep.leer_variable(VARIABLE_URL_API)
    if not crudo_api or not crudo_api.strip():
        raise EntornoNoValidado(["Falta DATABASE_URL, la credencial de la API."])
    try:
        api = make_url(crudo_api.strip())
    except Exception:  # noqa: BLE001 -- el mensaje del parser repetiría la URL
        raise EntornoNoValidado(["DATABASE_URL no es una URL válida."]) from None

    nombres = {api.database, etl.database, alembic.database}
    if len(nombres) != 1 or not etl.database:
        motivos.append(
            "DATABASE_URL, ETL_DATABASE_URL y ALEMBIC_DATABASE_URL no nombran la "
            "misma base."
        )
    puerto = _puerto_postgres(dep)
    for nombre, url in (("ETL_DATABASE_URL", etl), ("ALEMBIC_DATABASE_URL", alembic)):
        if url.host not in HOSTS_LOCALES:
            motivos.append(f"{nombre} no apunta a la interfaz local.")
        if url.port != puerto:
            motivos.append(f"{nombre} no usa el puerto POSTGRES_PORT={puerto}.")
    if api.host != SERVICIO_DB or (api.port or PUERTO_INTERNO_DB) != PUERTO_INTERNO_DB:
        motivos.append("DATABASE_URL no apunta al servicio db:5432 de Compose.")
    if api.username != ROL_API:
        motivos.append(f"DATABASE_URL no usa el rol {ROL_API}.")
    if etl.username != ROL_ETL:
        motivos.append(f"ETL_DATABASE_URL no usa el rol {ROL_ETL}.")
    if motivos:
        raise EntornoNoValidado(motivos)
    return {"base": etl.database, "puerto": puerto, "api": api}


@dataclass(frozen=True)
class Identidad:
    """Dónde aterrizó una conexión, visto desde el propio servidor."""

    ip: str | None
    puerto: int | None
    base: str
    rol: str
    superusuario: bool
    bypass: bool
    oid: int


def leer_identidad(conexion: Connection) -> Identidad:
    """La IP y el puerto **del servidor** que atiende esta conexión.

    Una conexión que entra por el puerto publicado de Docker llega al
    PostgreSQL del contenedor por su interfaz de la red del proyecto, así que
    ``inet_server_addr()`` es la IP del contenedor ``db``. Otro PostgreSQL que
    escuchara en el mismo puerto de la computadora respondería con otra.
    """
    fila = conexion.execute(
        text(
            "SELECT host(inet_server_addr()) AS ip, inet_server_port() AS puerto,"
            "       current_database() AS base, current_user AS rol,"
            "       r.rolsuper AS superusuario, r.rolbypassrls AS bypass,"
            "       (SELECT oid FROM pg_database"
            "         WHERE datname = current_database()) AS oid"
            "  FROM pg_roles r WHERE r.rolname = current_user"
        )
    ).one()
    return Identidad(
        ip=fila.ip,
        puerto=fila.puerto,
        base=fila.base,
        rol=fila.rol,
        superusuario=bool(fila.superusuario),
        bypass=bool(fila.bypass),
        oid=int(fila.oid),
    )


def validar_entorno(dep: Dependencias, *, exigir_api: bool = True) -> EntornoValidado:
    """Identifica el destino o se niega. Solo lee: Docker, URLs y PostgreSQL.

    1. Las tres URLs coinciden en la base y apuntan al servidor esperado.
    2. El ``db`` es el de este proyecto Compose, está sano y publica el puerto
       de las URLs solo en la interfaz local.
    3. Las conexiones del ETL y del migrador llegan **a ese contenedor**: el
       servidor responde desde la IP del ``db`` en la red del proyecto, no desde
       otro PostgreSQL que escuche en el mismo puerto.
    4. La credencial del ETL es ``fetalalert_etl``, sin superusuario ni
       BYPASSRLS, y la base está en el head de Alembic.
    5. Con ``exigir_api``: el contenedor ``api`` es de este proyecto y no está
       desfasado (su ``config-hash`` y su ``DATABASE_URL``, comparados solo en
       memoria, coinciden con la configuración actual).
    6. El episodio de demostración se identifica sin ambigüedad.
    """
    urls = validar_urls(dep)
    motivos: list[str] = []

    contenedores = leer_contenedores(dep)
    db = _uno_del_servicio(contenedores, SERVICIO_DB)
    if db is None:
        raise EntornoNoValidado(["No existe el contenedor del servicio db de este proyecto."])
    if not _del_proyecto(dep, db):
        motivos.append("El contenedor db no pertenece a este directorio de Compose.")
    if not db.en_marcha or db.salud != "healthy":
        motivos.append(f"db no está en marcha y sano ({db.estado}/{db.salud}).")
    if db.puerto_publicado(PUERTO_INTERNO_DB) != ("127.0.0.1", urls["puerto"]):
        motivos.append(
            f"db no publica 5432 en 127.0.0.1:{urls['puerto']} (POSTGRES_PORT)."
        )
    proyecto = db.etiquetas.get("com.docker.compose.project", "")
    red_del_proyecto = f"{proyecto}_default"
    ip_db = db.redes.get(red_del_proyecto)
    if not ip_db:
        motivos.append(f"db no tiene dirección en la red {red_del_proyecto}.")

    if exigir_api:
        api = _uno_del_servicio(contenedores, SERVICIO_API)
        if api is None:
            motivos.append("No existe el contenedor del servicio api de este proyecto.")
        else:
            if not _del_proyecto(dep, api):
                motivos.append("El contenedor api no pertenece a este directorio de Compose.")
            hashes = hashes_de_configuracion(dep)
            if api.etiquetas.get("com.docker.compose.config-hash") != hashes.get(SERVICIO_API):
                motivos.append(
                    "El contenedor api se creó con otra configuración (config-hash "
                    "distinto). Recréalo a mano con 'docker compose up -d api'."
                )
            # D4: el entorno del contenedor solo se compara en memoria; ninguno
            # de los dos valores se imprime ni se guarda.
            if api.variable(VARIABLE_URL_API) != (
                dep.leer_variable(VARIABLE_URL_API) or ""
            ).strip():
                motivos.append(
                    "La DATABASE_URL del contenedor api no coincide con la del .env."
                )
    if motivos:
        raise EntornoNoValidado(motivos)

    try:
        with dep.conectar(dep.exigir_url(VARIABLE_URL_ETL)) as conexion:
            fila = leer_identidad(conexion)
            preflight(conexion)
        with dep.conectar(dep.exigir_url(VARIABLE_URL_ALEMBIC)) as conexion:
            fila_migrador = leer_identidad(conexion)
            episodio = provisionar_demo.localizar_episodio_demo(conexion)
    except provisionar_demo.ErrorDeAprovisionamiento as error:
        raise EntornoNoValidado([error.detalle]) from None
    except (SQLAlchemyError, OSError) as error:
        raise EntornoNoValidado([_describir_excepcion(error)]) from None
    except Exception as error:  # noqa: BLE001 -- preflight y otros: sin secretos
        raise EntornoNoValidado([_describir_excepcion(error)]) from None

    if fila.ip != ip_db or fila.puerto != PUERTO_INTERNO_DB:
        motivos.append(
            "La conexión del ETL no llega al contenedor db de este proyecto "
            "(otro PostgreSQL podría estar atendiendo ese puerto)."
        )
    if fila_migrador.ip != ip_db or fila_migrador.oid != fila.oid:
        motivos.append("La credencial de mantenimiento no llega a la misma base.")
    if fila.base != urls["base"]:
        motivos.append("La base conectada no es la que nombran las URLs.")
    if fila.rol != ROL_ETL or fila.superusuario or fila.bypass:
        motivos.append(
            f"La credencial del ETL no es {ROL_ETL} sin superusuario ni BYPASSRLS."
        )
    if motivos:
        raise EntornoNoValidado(motivos)

    return EntornoValidado(
        base=fila.base,
        proyecto=proyecto,
        puerto_db=urls["puerto"],
        ip_db=ip_db,
        rol_etl=fila.rol,
        episodio=episodio,
        contenedores=contenedores,
    )


def _informar_entorno_no_validado(dep: Dependencias, error: EntornoNoValidado) -> None:
    _decir(dep, "[FAIL] No se pudo validar el destino. No se hizo nada.")
    for motivo in error.motivos:
        _decir(dep, f"       - {motivo}")


# ---------------------------------------------------------------------------
# Cola SQLite de la cuenta: inventario de solo lectura
# ---------------------------------------------------------------------------

ACOMPANANTES = ("-journal", "-wal", "-shm")


@dataclass(frozen=True)
class EstadoCola:
    """Lo que hay en la cola local de la cuenta, leído sin escribir."""

    existe: bool
    legible: bool = True
    detalle: str | None = None
    pendientes: int = 0
    enviados: int = 0
    fallidos_reintentables: int = 0
    fallidos_en_revision: int = 0
    intentos_abiertos: int = 0
    acompanantes: tuple[str, ...] = ()

    @property
    def con_residuos(self) -> bool:
        return bool(
            self.pendientes
            or self.fallidos_reintentables
            or self.fallidos_en_revision
            or self.intentos_abiertos
        )


def inspeccionar_cola(ruta: Path) -> EstadoCola:
    """Conteos por estado, sin crear el archivo ni escribir en él.

    Se abre con ``mode=ro``. Si hay un ``-journal`` o un ``-wal`` al lado, no se
    abre: puede ser una transacción interrumpida que solo el proceso dueño debe
    recuperar.
    """
    acompanantes = tuple(
        sufijo for sufijo in ACOMPANANTES if Path(f"{ruta}{sufijo}").exists()
    )
    if not ruta.exists():
        return EstadoCola(existe=False, acompanantes=acompanantes)
    if acompanantes:
        return EstadoCola(
            existe=True,
            legible=False,
            acompanantes=acompanantes,
            detalle=(
                "Hay archivos auxiliares de SQLite junto a la cola "
                f"({', '.join(acompanantes)}): puede quedar una transacción a medias."
            ),
        )
    try:
        with closing(
            sqlite3.connect(f"{ruta.resolve().as_uri()}?mode=ro", uri=True)
        ) as conexion:
            version = conexion.execute("PRAGMA user_version").fetchone()[0]
            tablas = {
                fila[0]
                for fila in conexion.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if version != VERSION_DE_ESQUEMA or not {
                TABLA_CAPTURA,
                TABLA_OUTBOX,
                TABLA_INTENTO,
            } <= tablas:
                return EstadoCola(
                    existe=True,
                    legible=False,
                    detalle="La cola no tiene el esquema que esta versión espera.",
                )
            if conexion.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                return EstadoCola(
                    existe=True, legible=False, detalle="La cola no pasa quick_check."
                )
            conteos = {"PENDIENTE": 0, "ENVIADO": 0}
            reintentables = en_revision = 0
            for estado, reintentable, cuantos in conexion.execute(
                f"SELECT estado, reintentable, count(*) FROM {TABLA_OUTBOX}"
                " GROUP BY estado, reintentable"
            ):
                if estado == "FALLIDO":
                    if reintentable == 1:
                        reintentables += cuantos
                    else:
                        en_revision += cuantos
                else:
                    conteos[estado] = conteos.get(estado, 0) + cuantos
            abiertos = conexion.execute(
                f"SELECT count(*) FROM {TABLA_INTENTO}"
                " WHERE finalizado_en IS NULL AND reconciliado_en IS NULL"
            ).fetchone()[0]
    except sqlite3.Error as error:
        return EstadoCola(
            existe=True,
            legible=False,
            detalle=f"La cola no se pudo leer ({type(error).__name__}).",
        )
    return EstadoCola(
        existe=True,
        pendientes=conteos.get("PENDIENTE", 0),
        enviados=conteos.get("ENVIADO", 0),
        fallidos_reintentables=reintentables,
        fallidos_en_revision=en_revision,
        intentos_abiertos=abiertos,
    )


PREPARACION_DE_LA_COLA = (
    "La cola no se retira ni se archiva desde Demo Control. Para dejarla lista: "
    "con la API en marcha (opción 4), inicia sesión como la gestante en el "
    "portal y espera a que el envío automático deje los eventos en ENVIADO; un "
    "intento abierto lo reconcilia el propio portal. Un FALLIDO en revisión "
    "necesita revisión manual (python scripts/edge_node.py --base <cola> traza "
    "<clave>; ese comando no es de solo lectura garantizada: pasa por la "
    "inicialización del almacenamiento). Después, vuelve a verificar con la "
    "opción 1, que sí lee la cola en solo lectura."
)


# ---------------------------------------------------------------------------
# Opción 1 -- Verificar estado (solo lectura)
# ---------------------------------------------------------------------------

OK, INFO, WARN, FAIL = "OK", "INFO", "WARN", "FAIL"


@dataclass(frozen=True)
class Linea:
    nivel: str
    etiqueta: str
    detalle: str = ""


@dataclass
class Informe:
    lineas: list[Linea] = field(default_factory=list)

    def agregar(self, nivel: str, etiqueta: str, detalle: str = "") -> None:
        # La validacion del destino y las comprobaciones de detalle pueden
        # tropezar con la misma causa: se informa una sola vez.
        if detalle and any(
            linea.nivel == nivel and linea.detalle == detalle for linea in self.lineas
        ):
            return
        self.lineas.append(Linea(nivel, etiqueta, detalle))

    @property
    def estado_global(self) -> str:
        niveles = {linea.nivel for linea in self.lineas}
        if FAIL in niveles:
            return "NO DISPONIBLE"
        if WARN in niveles:
            return "REQUIERE PREPARACIÓN"
        return "LISTO PARA DEMO"


def _hoy_clinico(dep: Dependencias) -> date:
    return dep.reloj().astimezone(ZONA_CLINICA).date()


def _estado_adminer(dep: Dependencias, contenedor: Contenedor | None) -> tuple[str, str]:
    """Tres estados que no se confunden: disponible, apagado, sin respuesta."""
    if contenedor is None or not contenedor.en_marcha:
        return "APAGADO", "Adminer está apagado (es opcional; Demo Control no lo arranca)."
    codigo, _ = dep.http_get(URL_ADMINER, PLAZO_HTTP)
    if codigo == 200:
        return "DISPONIBLE", "Adminer en marcha y respondiendo en 127.0.0.1:8090."
    return "SIN RESPUESTA", "Adminer está en marcha pero no responde en 127.0.0.1:8090."


def _url_del_portal(dep: Dependencias) -> tuple[str, str | None, int | None]:
    ajustes = dep.ajustes_gestante()
    partes = urlsplit(ajustes.api_base_url)
    return ajustes.api_base_url.rstrip("/"), partes.hostname, partes.port


def portal_usa_la_api_de_compose(
    dep: Dependencias, api: Contenedor | None
) -> tuple[bool, str]:
    """Si ``GESTANTE_API_BASE_URL`` es el puerto publicado del servicio ``api``.

    Devuelve tambien la URL base. Es la unica comprobacion de esta relacion: la
    usan la verificacion y las opciones 3 y 4. Si el portal habla con otra API
    --por ejemplo, un ``uvicorn`` en otro puerto--, detener o iniciar el
    contenedor no cambia nada para la gestante, y un ``/health`` correcto en esa
    URL no dice nada sobre el servicio de Compose.
    """
    base, host, puerto = _url_del_portal(dep)
    publicado = api.puerto_publicado(PUERTO_INTERNO_API) if api is not None else None
    coincide = host in HOSTS_LOCALES and publicado is not None and publicado[1] == puerto
    return coincide, base


def _salud_api(dep: Dependencias, base: str) -> bool:
    codigo, cuerpo = dep.http_get(f"{base}/health", PLAZO_HTTP)
    if codigo != 200:
        return False
    try:
        return json.loads(cuerpo).get("status") == "ok"
    except (json.JSONDecodeError, AttributeError):
        return False


def _verificar_destino(dep: Dependencias, informe: Informe) -> EntornoValidado | None:
    """La misma validacion que exigen las opciones 2 a 6, sin relajar nada.

    Es lo que impide que la verificacion diga «LISTO PARA DEMO» sobre un destino
    que despues bloquearia la ejecucion real: otra base, otro PostgreSQL en el
    mismo puerto, otro proyecto Compose o un contenedor ``api`` desfasado. Solo
    lee.
    """
    try:
        entorno = validar_entorno(dep, exigir_api=True)
    except EntornoNoValidado as error:
        for motivo in error.motivos:
            informe.agregar(FAIL, "Destino", motivo)
        return None
    informe.agregar(
        OK,
        "Destino",
        f"base '{entorno.base}', proyecto {entorno.proyecto}, db en "
        f"127.0.0.1:{entorno.puerto_db}, rol {entorno.rol_etl}. Es el que usarían "
        "las opciones 2 a 6.",
    )
    return entorno


def _verificar_docker(
    dep: Dependencias, informe: Informe, contenedores: dict | None = None
) -> dict:
    """El detalle de cada servicio. La configuracion la juzga ``_verificar_destino``."""
    if contenedores is None:
        try:
            contenedores = leer_contenedores(dep)
        except EntornoNoValidado as error:
            for motivo in error.motivos:
                informe.agregar(FAIL, "Docker", motivo)
            return {}
    for servicio in (SERVICIO_DB, SERVICIO_API, SERVICIO_ADMINER):
        if len(contenedores.get(servicio) or []) > 1:
            informe.agregar(
                FAIL, "Docker", f"Hay más de un contenedor del servicio {servicio}."
            )
    db = (contenedores.get(SERVICIO_DB) or [None])[0]
    if db is None:
        informe.agregar(FAIL, "PostgreSQL (db)", "No existe el contenedor db de este proyecto.")
    elif not _del_proyecto(dep, db):
        informe.agregar(FAIL, "PostgreSQL (db)", "El contenedor no es de este directorio.")
    elif db.en_marcha and db.salud == "healthy":
        informe.agregar(OK, "PostgreSQL (db)", "En marcha y sano.")
    else:
        informe.agregar(FAIL, "PostgreSQL (db)", f"Estado {db.estado}/{db.salud}.")

    if not contenedores.get(SERVICIO_API):
        informe.agregar(FAIL, "API central", "No existe el contenedor api de este proyecto.")
    adminer = (contenedores.get(SERVICIO_ADMINER) or [None])[0]
    estado, detalle = _estado_adminer(dep, adminer)
    informe.agregar(WARN if estado == "SIN RESPUESTA" else INFO, "Adminer", detalle)
    return contenedores


def _verificar_api(dep: Dependencias, informe: Informe, contenedores: dict) -> None:
    api = (contenedores.get(SERVICIO_API) or [None])[0]
    if api is None:
        return
    try:
        coincide, base = portal_usa_la_api_de_compose(dep, api)
    except ConfiguracionGestanteInvalida as error:
        informe.agregar(FAIL, "Portal → API", sanear_mensaje(str(error)))
        return
    if not coincide:
        # Bloquearia las opciones 3 y 4, asi que no es una advertencia. Y no se
        # consulta /health en esa URL: lo que respondiera no seria este servicio.
        informe.agregar(
            FAIL,
            "Portal → API",
            "GESTANTE_API_BASE_URL no apunta al puerto publicado del servicio api: "
            "detenerlo no cortaría la comunicación de la gestante, e iniciarlo no "
            "la restablecería.",
        )
        return
    informe.agregar(OK, "Portal → API", "El portal usa la API de Compose.")
    if not api.en_marcha:
        informe.agregar(WARN, "API central", "Detenida. La opción 4 la inicia.")
    elif _salud_api(dep, base):
        informe.agregar(OK, "API central", "En marcha; GET /health responde.")
    else:
        informe.agregar(WARN, "API central", "En marcha, pero GET /health no responde.")


def _verificar_base(
    dep: Dependencias, informe: Informe, ajustes: GestanteSettings
) -> dict | None:
    """Todo lo que se lee de PostgreSQL, en transacciones READ ONLY."""
    try:
        urls = validar_urls(dep)
        informe.agregar(OK, "Credenciales", f"Las tres URLs nombran la base '{urls['base']}'.")
    except EntornoNoValidado as error:
        for motivo in error.motivos:
            informe.agregar(FAIL, "Credenciales", motivo)
        return None

    hoy = _hoy_clinico(dep)
    ahora = dep.reloj()
    try:
        with dep.conectar(dep.exigir_url(VARIABLE_URL_ALEMBIC)) as conexion:
            revision = preflight(conexion)
            informe.agregar(OK, "Migraciones", f"Base en el head de Alembic ({revision}).")
            try:
                episodio = provisionar_demo.localizar_episodio_demo(conexion)
            except provisionar_demo.ErrorDeAprovisionamiento as error:
                informe.agregar(FAIL, "Episodio demo", error.detalle)
                return None
            catalogos = provisionar_demo.leer_catalogos(conexion)
            datos = _leer_contexto_demo(conexion, episodio)
        with dep.conectar(dep.exigir_url(VARIABLE_URL_ETL)) as conexion:
            ocupado = candado_ocupado(conexion)
    except Exception as error:  # noqa: BLE001 -- saneado, nunca la URL
        informe.agregar(FAIL, "PostgreSQL", _describir_excepcion(error))
        return None

    e = episodio["id_embarazo"]
    informe.agregar(
        OK,
        "Episodio demo",
        f"embarazo={e} cuenta={episodio['id_usuario']} "
        f"dispositivo={episodio['id_dispositivo']} clínica={episodio['id_clinica']}",
    )
    informe.agregar(
        OK if episodio["cuenta_activa"] else FAIL,
        "Cuenta PACIENTE",
        "Activa." if episodio["cuenta_activa"] else "La cuenta está desactivada.",
    )
    informe.agregar(
        OK if episodio["estado_embarazo"] == "ACTIVO" else FAIL,
        "Embarazo vigente",
        f"Estado {episodio['estado_embarazo']}.",
    )

    semanas = [fila["semana"] for fila in catalogos["tiempo_gestacional"]]
    ultima = max(semanas)
    semana = semana_gestacional(episodio["fecha_inicio"], ahora)
    fin = episodio["fecha_inicio"] + timedelta(days=ultima * 7 - 1)
    if not 1 <= semana <= ultima:
        informe.agregar(
            FAIL,
            "Semana gestacional",
            f"Hoy es la semana {semana}, fuera del catálogo (1-{ultima}): la API "
            "rechazaría la captura. Hace falta un episodio nuevo.",
        )
    elif semana < reglas.SEMANA_MINIMA_DE_MOVIMIENTO:
        informe.agregar(
            WARN,
            "Semana gestacional",
            f"Semana {semana}: antes de la {reglas.SEMANA_MINIMA_DE_MOVIMIENTO} no "
            "se admiten capturas de movimiento fetal.",
        )
    elif semana > ultima - MARGEN_DE_SEMANAS:
        informe.agregar(
            WARN,
            "Semana gestacional",
            f"Semana {semana}: el episodio sale del catálogo después del "
            f"{fin.isoformat()}.",
        )
    else:
        informe.agregar(OK, "Semana gestacional", f"Semana {semana}.")
    informe.agregar(
        INFO,
        "Vigencia del episodio",
        f"Las capturas se admiten hasta la semana {ultima}, es decir, hasta el "
        f"{fin.isoformat()}. Después hará falta un episodio nuevo.",
    )

    try:
        id_medico = reglas.medico_responsable(datos["seguimientos"], hoy, id_lectura=0)
        reglas.verificar_afiliacion(
            datos["afiliaciones"].get(id_medico, []),
            hoy,
            id_medico=id_medico,
            id_clinica_embarazo=episodio["id_clinica"],
            id_lectura=0,
        )
        informe.agregar(
            OK,
            "Médico responsable",
            f"médico={id_medico}, afiliado hoy a la clínica {episodio['id_clinica']}.",
        )
    except reglas.ErrorDeDatos as error:
        informe.agregar(
            FAIL,
            "Médico responsable",
            f"El ETL rechazaría una captura de hoy ({type(error).__name__}).",
        )

    asignacion_vigente = (
        episodio["asignacion_activa"]
        and episodio["asignacion_inicio"] <= hoy
        and (episodio["asignacion_fin"] is None or hoy <= episodio["asignacion_fin"])
    )
    informe.agregar(
        OK if episodio["estado_dispositivo"] == "ASIGNADO" and asignacion_vigente else FAIL,
        "Dispositivo",
        f"Estado {episodio['estado_dispositivo']}; asignación "
        f"{'vigente' if asignacion_vigente else 'no vigente'}.",
    )
    informe.agregar(
        OK if datos["seudonimo"] else FAIL,
        "Seudónimo del embarazo",
        "Existe."
        if datos["seudonimo"]
        else "No existe: ejecuta el ETL antes de preparar Adminer.",
    )
    if datos["sin_etl_total"]:
        informe.agregar(
            WARN,
            "ETL al día",
            f"{datos['sin_etl_total']} lectura(s) sin incorporar "
            f"({datos['sin_etl_demo']} del embarazo demo). Previsualiza (5) y ejecuta (6).",
        )
    else:
        informe.agregar(OK, "ETL al día", "Ninguna lectura pendiente de incorporar.")
    if datos["ultima_captura"] is not None and datos["ultima_captura"] >= ahora:
        informe.agregar(
            WARN, "Última captura", "Hay una captura del embarazo demo con fecha futura."
        )
    informe.agregar(
        INFO,
        "Base de demostración",
        f"Estado acumulado: {datos['sesiones_demo']} sesión(es) y "
        f"{datos['lecturas_demo']} lectura(s) del embarazo demo. No es el dataset "
        "canónico: sus conteos no son resultados de la tesis.",
    )
    informe.agregar(
        INFO, "Candado del ETL", "Ocupado por otra ejecución." if ocupado else "Libre."
    )

    _verificar_provision(informe, ajustes, episodio, catalogos)
    return episodio


def _leer_contexto_demo(conexion: Connection, episodio: dict) -> dict:
    e = episodio["id_embarazo"]
    seguimientos = [
        reglas.PeriodoDeSeguimiento(
            id_seguimiento=fila.id_seguimiento,
            id_medico=fila.id_medico,
            fecha_asignacion=fila.fecha_asignacion,
            fecha_fin=fila.fecha_fin,
        )
        for fila in conexion.execute(
            text(
                "SELECT id_seguimiento, id_medico, fecha_asignacion, fecha_fin"
                "  FROM operacional.seguimiento_clinico"
                " WHERE id_embarazo = :e AND rol_seguimiento = 'PRINCIPAL'"
            ),
            {"e": e},
        )
    ]
    afiliaciones: dict[int, list] = {}
    for fila in conexion.execute(
        text(
            "SELECT id_medico, id_clinica, fecha_inicio, fecha_final"
            "  FROM operacional.medico_clinica"
            " WHERE id_medico IN (SELECT id_medico FROM operacional.seguimiento_clinico"
            "                      WHERE id_embarazo = :e)"
        ),
        {"e": e},
    ):
        afiliaciones.setdefault(fila.id_medico, []).append(
            reglas.AfiliacionClinica(
                id_clinica=fila.id_clinica,
                fecha_inicio=fila.fecha_inicio,
                fecha_final=fila.fecha_final,
            )
        )
    resumen = conexion.execute(
        text(
            "SELECT"
            " (SELECT count(*) FROM operacional.lectura_biometrica l"
            "   WHERE NOT EXISTS (SELECT 1 FROM analitico.fact_lectura_biometrica f"
            "                      WHERE f.id_lectura = l.id_lectura)) AS sin_etl_total,"
            " (SELECT count(*) FROM operacional.lectura_biometrica l"
            "   JOIN operacional.sesion_monitoreo s USING (id_sesion)"
            "  WHERE s.id_embarazo = :e AND NOT EXISTS ("
            "        SELECT 1 FROM analitico.fact_lectura_biometrica f"
            "         WHERE f.id_lectura = l.id_lectura)) AS sin_etl_demo,"
            " (SELECT count(*) FROM operacional.sesion_monitoreo"
            "   WHERE id_embarazo = :e) AS sesiones_demo,"
            " (SELECT count(*) FROM operacional.lectura_biometrica l"
            "   JOIN operacional.sesion_monitoreo s USING (id_sesion)"
            "  WHERE s.id_embarazo = :e) AS lecturas_demo,"
            " (SELECT max(l.fecha_hora_captura) FROM operacional.lectura_biometrica l"
            "   JOIN operacional.sesion_monitoreo s USING (id_sesion)"
            "  WHERE s.id_embarazo = :e) AS ultima_captura,"
            " EXISTS (SELECT 1 FROM privado.seudonimo_embarazo"
            "          WHERE id_embarazo = :e) AS seudonimo"
        ),
        {"e": e},
    ).one()
    return {
        "seguimientos": seguimientos,
        "afiliaciones": afiliaciones,
        "sin_etl_total": resumen.sin_etl_total,
        "sin_etl_demo": resumen.sin_etl_demo,
        "sesiones_demo": resumen.sesiones_demo,
        "lecturas_demo": resumen.lecturas_demo,
        "ultima_captura": resumen.ultima_captura,
        "seudonimo": resumen.seudonimo,
    }


def comparar_provision(
    ruta: Path, episodio: dict, catalogos: dict
) -> tuple[str, list[str]]:
    """``("COHERENTE"|"AUSENTE"|"ILEGIBLE"|"DESACTUALIZADO", diferencias)``.

    Compara lo que el portal y el dispositivo usarían con lo que hay en la
    base. Las diferencias son identificadores, nunca datos personales.
    """
    if not ruta.exists():
        return "AUSENTE", []
    try:
        actual = provision_local.cargar(ruta)
    except provision_local.ProvisionInvalida:
        return "ILEGIBLE", []
    diferencias = []
    for nombre, local, base in (
        ("id_usuario", actual.id_usuario, episodio["id_usuario"]),
        ("id_embarazo", actual.id_embarazo, episodio["id_embarazo"]),
        ("fecha_inicio", actual.fecha_inicio_embarazo, episodio["fecha_inicio"]),
        ("id_dispositivo", actual.id_dispositivo, episodio["id_dispositivo"]),
    ):
        if local != base:
            diferencias.append(f"{nombre}: archivo={local} base={base}")
    if actual.semaforos != catalogos["semaforo"]:
        diferencias.append("catálogo de semáforo distinto")
    semanas = {
        fila["semana"]: (fila["id_tiempo_gest"], fila["trimestre"])
        for fila in catalogos["tiempo_gestacional"]
    }
    if {s: (v.id_tiempo_gest, v.trimestre) for s, v in actual.semanas.items()} != semanas:
        diferencias.append("catálogo gestacional distinto")
    return ("COHERENTE" if not diferencias else "DESACTUALIZADO"), diferencias


def _verificar_provision(
    informe: Informe, ajustes: GestanteSettings, episodio: dict, catalogos: dict
) -> None:
    estado, diferencias = comparar_provision(ajustes.provision_path, episodio, catalogos)
    if estado == "COHERENTE":
        informe.agregar(OK, "Aprovisionamiento local", "provision.json coincide con la base.")
    else:
        detalle = {
            "AUSENTE": "provision.json no existe.",
            "ILEGIBLE": "provision.json no se puede leer.",
            "DESACTUALIZADO": "provision.json no coincide con la base: "
            + "; ".join(diferencias),
        }[estado]
        informe.agregar(
            WARN, "Aprovisionamiento local", detalle + " La opción 2 lo reconstruye."
        )


def _verificar_cola(informe: Informe, ajustes: GestanteSettings, episodio: dict | None) -> None:
    if episodio is not None:
        id_usuario = episodio["id_usuario"]
    else:
        # Sin base no hay episodio confirmado: se revisa la cola de la cuenta
        # que nombra el aprovisionamiento local, y se dice de donde sale.
        try:
            id_usuario = provision_local.cargar(ajustes.provision_path).id_usuario
        except provision_local.ProvisionInvalida:
            informe.agregar(
                WARN,
                "Cola local",
                "Sin episodio identificado ni provision.json, no se sabe qué cola "
                "revisar.",
            )
            return
        informe.agregar(
            INFO,
            "Cola local",
            f"Cuenta {id_usuario} tomada de provision.json; la base no la confirmó.",
        )
    ruta = ruta_para_la_cuenta(ajustes, id_usuario)
    estado = inspeccionar_cola(ruta)
    if not estado.existe:
        informe.agregar(OK, "Cola local", "Vacía: se crea con la primera captura.")
    elif not estado.legible:
        informe.agregar(FAIL, "Cola local", f"{estado.detalle} {PREPARACION_DE_LA_COLA}")
    elif estado.con_residuos:
        informe.agregar(
            WARN,
            "Cola local",
            f"PENDIENTE={estado.pendientes} FALLIDO(reintentable)="
            f"{estado.fallidos_reintentables} FALLIDO(revisión)="
            f"{estado.fallidos_en_revision} intentos abiertos="
            f"{estado.intentos_abiertos}. Capturas sin entregar: aparecerían junto a "
            f"la nueva. {PREPARACION_DE_LA_COLA}",
        )
    else:
        informe.agregar(OK, "Cola local", f"Sin pendientes ({estado.enviados} ENVIADO).")


def opcion_verificar(dep: Dependencias) -> Informe:
    """Opción 1. Solo lectura: no crea, no corrige y no arranca nada."""
    _titulo(dep, "VERIFICACIÓN DE DEMOSTRACIÓN")
    informe = Informe()
    # Primero, el mismo destino que validarian las opciones con efectos: si
    # falla, hay al menos un FAIL y el estado no puede ser «LISTO PARA DEMO».
    entorno = _verificar_destino(dep, informe)
    contenedores = _verificar_docker(
        dep, informe, entorno.contenedores if entorno is not None else None
    )
    try:
        ajustes = dep.ajustes_gestante()
    except ConfiguracionGestanteInvalida as error:
        informe.agregar(FAIL, "Configuración del portal", sanear_mensaje(str(error)))
        _imprimir_informe(dep, informe)
        return informe
    episodio = _verificar_base(dep, informe, ajustes)
    _verificar_cola(informe, ajustes, episodio)
    _verificar_api(dep, informe, contenedores)
    codigo, _ = dep.http_get(f"http://127.0.0.1:{ajustes.port}/", PLAZO_HTTP)
    if codigo == 200:
        informe.agregar(
            OK, "Portal de la gestante", f"En marcha en 127.0.0.1:{ajustes.port}."
        )
    else:
        # El portal es parte del recorrido: sin el no hay inicio de sesion de
        # la gestante ni envio automatico de la cola.
        informe.agregar(
            WARN,
            "Portal de la gestante",
            f"No responde en 127.0.0.1:{ajustes.port}. Inícialo con "
            "'python scripts\\gestante_web.py'; Demo Control no lo arranca.",
        )
    minutos = dep.leer_variable("JWT_EXPIRATION_MINUTES") or str(JWT_MINUTOS_POR_OMISION)
    informe.agregar(
        INFO,
        "Token de la gestante",
        f"Caduca a los {minutos} min. Si caduca con la API detenida, la captura "
        "sale después de un nuevo inicio de sesión: conviene iniciarla poco antes "
        "de la opción 3.",
    )
    _imprimir_informe(dep, informe)
    _decir(dep, "Antes del ensayo, toma ahora S0/L0/Q0 y el seudónimo con la consulta")
    _decir(dep, "de preparación de Adminer (docs/demo_adminer.md §4.2). No reutilices")
    _decir(dep, "valores de un ensayo anterior.")
    return informe


def _imprimir_informe(dep: Dependencias, informe: Informe) -> None:
    for linea in informe.lineas:
        etiqueta = f"[{linea.nivel}]".ljust(7)
        _decir(dep, f"{etiqueta}{linea.etiqueta}: {linea.detalle}".rstrip(": "))
    _decir(dep, LINEA)
    _decir(dep, f"ESTADO GENERAL: {informe.estado_global}")
    _decir(dep, LINEA)


# ---------------------------------------------------------------------------
# Opción 2 -- Restablecer baseline (solo artefactos locales)
# ---------------------------------------------------------------------------


def _respaldar(ruta: Path, momento: datetime) -> Path:
    """Copia recuperable del archivo anterior, verificada byte a byte."""
    destino = ruta.with_name(f"{ruta.name}.{momento.strftime('%Y%m%dT%H%M%SZ')}.bak")
    if destino.exists():
        raise FileExistsError(destino.name)
    shutil.copy2(ruta, destino)
    if not filecmp.cmp(ruta, destino, shallow=False):
        raise OSError("el respaldo no coincide con el original")
    return destino


def opcion_restablecer(dep: Dependencias) -> None:
    """Opción 2. Reconstruye ``provision.json`` desde la base; nada más.

    Ninguna escritura en PostgreSQL: el episodio y los catálogos se leen en una
    transacción READ ONLY y la ruta que crea filas (``provisionar``) no se
    invoca. La cola de capturas no se toca nunca: si tiene algo sin entregar,
    requiere preparación manual.
    """
    _titulo(dep, "RESTABLECER BASELINE DE DEMOSTRACIÓN")
    for linea in (
        "Esta operación:",
        "- NO modifica PostgreSQL: la base conserva todas las capturas anteriores.",
        "- NO toca el dataset canónico, que es el origen de las cifras de la tesis.",
        "- NO crea episodios, dispositivos ni cuentas.",
        "- NO retira ni archiva capturas de la cola local.",
        "- SOLO reconstruye provision.json si no coincide con la base, con respaldo.",
    ):
        _decir(dep, linea)
    _decir(dep)

    try:
        ajustes = dep.ajustes_gestante()
        entorno = validar_entorno(dep, exigir_api=False)
        with dep.conectar(dep.exigir_url(VARIABLE_URL_ALEMBIC)) as conexion:
            episodio = provisionar_demo.localizar_episodio_demo(conexion)
            catalogos = provisionar_demo.leer_catalogos(conexion)
    except EntornoNoValidado as error:
        _informar_entorno_no_validado(dep, error)
        return
    except ConfiguracionGestanteInvalida as error:
        _decir(dep, f"[FAIL] {sanear_mensaje(str(error))} No se hizo nada.")
        return
    except provisionar_demo.ErrorDeAprovisionamiento as error:
        _decir(dep, f"[FAIL] {error.detalle} No se hizo nada.")
        return
    except Exception as error:  # noqa: BLE001
        _decir(dep, f"[FAIL] {_describir_excepcion(error)}. No se hizo nada.")
        return

    _decir(
        dep,
        f"Destino validado: base '{entorno.base}', embarazo demo "
        f"{episodio['id_embarazo']}.",
    )

    cola = inspeccionar_cola(ruta_para_la_cuenta(ajustes, episodio["id_usuario"]))
    if cola.existe and (not cola.legible or cola.con_residuos):
        _decir(dep, "[WARN] Cola local: REQUIERE PREPARACIÓN. No se modifica.")
        _decir(dep, f"       {PREPARACION_DE_LA_COLA}")
    else:
        _decir(dep, "[OK]   Cola local sin capturas pendientes: nada que hacer.")

    ruta = ajustes.provision_path
    estado, diferencias = comparar_provision(ruta, episodio, catalogos)
    if estado == "COHERENTE":
        _decir(dep, "[OK]   provision.json ya coincide con la base: sin cambios.")
    else:
        _decir(dep, f"provision.json: {estado}.")
        for diferencia in diferencias:
            _decir(dep, f"  - {diferencia}")
        respuesta = dep.entrada("¿Reconstruir provision.json desde la base? [S/N]: ")
        if respuesta.strip().upper() != "S":
            _decir(dep, "Cancelado. No se modificó ningún archivo.")
        else:
            _reconstruir_provision(dep, ruta, episodio, catalogos)
    _decir(dep)
    opcion_verificar(dep)


def _reconstruir_provision(
    dep: Dependencias, ruta: Path, episodio: dict, catalogos: dict
) -> None:
    respaldo = None
    try:
        if ruta.exists():
            respaldo = _respaldar(ruta, dep.reloj())
            _decir(dep, f"[OK]   Respaldo del archivo anterior: {respaldo.name}")
        provisionar_demo.escribir_provision(ruta, episodio, catalogos)
    except OSError as error:
        _decir(
            dep,
            f"[FAIL] OPERACIÓN INCOMPLETA: no se pudo escribir provision.json "
            f"({type(error).__name__}). "
            + (f"El anterior se conserva en {respaldo.name}." if respaldo else "")
            + " La escritura es atómica: el archivo activo es el anterior o ninguno.",
        )
        return
    estado, _ = comparar_provision(ruta, episodio, catalogos)
    if estado == "COHERENTE":
        _decir(dep, "[OK]   provision.json reconstruido y comprobado.")
    else:
        _decir(
            dep,
            "[FAIL] OPERACIÓN INCOMPLETA: el archivo escrito no coincide con la base."
            + (f" Respaldo: {respaldo.name}." if respaldo else ""),
        )


# ---------------------------------------------------------------------------
# Opciones 3 y 4 -- Desconexión y reconexión de la API central
# ---------------------------------------------------------------------------


def _servicio(contenedores: dict, nombre: str) -> Contenedor | None:
    return (contenedores.get(nombre) or [None])[0]


def _api_del_portal(
    dep: Dependencias, si_no_coincide: str
) -> tuple[EntornoValidado, Contenedor, str] | None:
    """Lo que las opciones 3 y 4 exigen antes de tocar Docker.

    El destino validado y, ademas, que el portal hable con el servicio ``api``
    de ese proyecto. Si algo no se cumple, informa y devuelve ``None``: quien
    llama no hace nada. Devuelve el entorno, el contenedor y la URL base.
    """
    try:
        entorno = validar_entorno(dep, exigir_api=True)
        api = _servicio(entorno.contenedores, SERVICIO_API)
        coincide, base = portal_usa_la_api_de_compose(dep, api)
    except EntornoNoValidado as error:
        _informar_entorno_no_validado(dep, error)
        return None
    except ConfiguracionGestanteInvalida as error:
        _decir(dep, f"[FAIL] {sanear_mensaje(str(error))} No se hizo nada.")
        return None
    if not coincide:
        _decir(
            dep,
            "[FAIL] GESTANTE_API_BASE_URL no apunta al servicio api de Compose. "
            f"{si_no_coincide} No se hizo nada.",
        )
        return None
    return entorno, api, base


def opcion_desconectar(dep: Dependencias) -> None:
    """Opción 3. Detiene **solo** el servicio ``api`` y comprueba el efecto."""
    _titulo(dep, "SIMULANDO DESCONEXIÓN")
    preparado = _api_del_portal(
        dep, "SIMULACIÓN NO EFECTIVA: detenerlo no cortaría al portal."
    )
    if preparado is None:
        return
    entorno, api, base = preparado
    if not api.en_marcha:
        _decir(dep, "[INFO] La API central ya está detenida. No se hizo nada.")
        return
    adminer_antes, _ = _estado_adminer(dep, _servicio(entorno.contenedores, SERVICIO_ADMINER))

    resultado = _compose(dep, "stop", SERVICIO_API, control=True)
    if resultado.codigo != 0:
        _decir(dep, "[FAIL] 'docker compose stop api' falló. Revisa Docker.")
        return

    try:
        despues = leer_contenedores(dep)
    except EntornoNoValidado as error:
        _informar_entorno_no_validado(dep, error)
        return
    db = _servicio(despues, SERVICIO_DB)
    api = _servicio(despues, SERVICIO_API)
    adminer_despues, _ = _estado_adminer(dep, _servicio(despues, SERVICIO_ADMINER))
    correcto = True
    if db is not None and db.en_marcha and db.salud == "healthy":
        _decir(dep, "[OK]   PostgreSQL continúa disponible.")
    else:
        correcto = False
        _decir(dep, "[FAIL] PostgreSQL no está en marcha y sano.")
    if adminer_despues == adminer_antes:
        _decir(dep, f"[OK]   Adminer sin cambios ({adminer_despues}).")
    else:
        correcto = False
        _decir(dep, f"[WARN] Adminer cambió: {adminer_antes} → {adminer_despues}.")
    if api is not None and not api.en_marcha:
        _decir(dep, "[OK]   Contenedor api detenido.")
    else:
        correcto = False
        _decir(dep, "[FAIL] El contenedor api sigue en marcha.")
    if _salud_api(dep, base):
        correcto = False
        _decir(
            dep,
            "[FAIL] SIMULACIÓN NO EFECTIVA: algo sigue respondiendo /health en la "
            "URL del portal (¿otra instancia de la API?).",
        )
    else:
        _decir(dep, "[OK]   La API del portal ya no responde.")
    _decir(dep, "[OK]   La cola local no se toca: las capturas quedarán PENDIENTE.")
    _decir(dep, LINEA)
    _decir(
        dep,
        "ESTADO: SERVIDOR CENTRAL NO DISPONIBLE"
        if correcto
        else "ESTADO: REVISAR — la desconexión no quedó comprobada",
    )
    _decir(dep, LINEA)


def opcion_reconectar(dep: Dependencias) -> None:
    """Opción 4. Inicia **solo** el servicio ``api`` y comprueba que volvió.

    «Restablecida» exige las tres cosas: que el portal use el servicio ``api``
    de este proyecto, que ese contenedor esté en marcha y que ``/health``
    responda. Una respuesta de ``/health`` no basta por sí sola: si el portal
    apuntara a otra API, o si otra instancia ocupara ese puerto, respondería
    aunque el servicio de Compose siguiera caído.
    """
    _titulo(dep, "RESTABLECIENDO CONEXIÓN")
    preparado = _api_del_portal(
        dep,
        "RECONEXIÓN NO COMPROBABLE: iniciarlo no restablecería la comunicación "
        "del portal.",
    )
    if preparado is None:
        return
    _, api, base = preparado
    if not api.en_marcha:
        resultado = _compose(dep, "start", SERVICIO_API, control=True)
        if resultado.codigo != 0:
            _decir(dep, "[FAIL] 'docker compose start api' falló. Revisa Docker.")
            return
        _decir(dep, "[OK]   Contenedor api iniciado.")
    else:
        _decir(dep, "[INFO] El contenedor api ya estaba en marcha.")

    intentos = int(PLAZO_SALUD_API / INTERVALO_SALUD_API)
    while not _salud_api(dep, base):
        intentos -= 1
        if intentos <= 0:
            _decir(
                dep,
                f"[FAIL] La API no respondió a GET /health en {int(PLAZO_SALUD_API)} s. "
                "Revisa 'docker compose logs api'.",
            )
            return
        dep.dormir(INTERVALO_SALUD_API)
    _decir(dep, "[OK]   GET /health responde.")

    # /health respondio; falta saber que quien responde es el contenedor.
    try:
        despues = leer_contenedores(dep)
        api = _uno_del_servicio(despues, SERVICIO_API)
    except EntornoNoValidado as error:
        _informar_entorno_no_validado(dep, error)
        return
    if api is None or not api.en_marcha or not _del_proyecto(dep, api):
        _decir(
            dep,
            "[FAIL] Algo responde /health en la URL del portal, pero el contenedor "
            "api de este proyecto no está en marcha (¿otra instancia de la API?). "
            "La reconexión no quedó comprobada.",
        )
        _decir(dep, LINEA)
        _decir(dep, "ESTADO: REVISAR — la reconexión no quedó comprobada")
        _decir(dep, LINEA)
        return
    _decir(dep, "[OK]   Contenedor api en marcha.")
    _decir(
        dep,
        "       /health confirma que la API responde; no es una comprobación "
        "integral de PostgreSQL.",
    )
    _decir(dep, LINEA)
    _decir(dep, "ESTADO: CONEXIÓN RESTABLECIDA")
    _decir(dep, LINEA)
    _decir(
        dep,
        "El portal envía en su próximo ciclo. Si el token de la gestante caducó, "
        "debe volver a iniciar sesión: no se amplía ni se omite la autenticación.",
    )


# ---------------------------------------------------------------------------
# Opciones 5 y 6 -- ETL
# ---------------------------------------------------------------------------

RUTA_ETL = Path("scripts") / "etl_analitico.py"

MENSAJES_DEL_ETL = {
    0: "ETL COMPLETADO: la transacción se confirmó entera.",
    1: "Error de configuración, precondición o base de datos. La transacción se "
    "revirtió: no se conservó ningún cambio.",
    2: "El origen contiene algo que las reglas SIM-1.0 no permiten cargar. No se "
    "conservó ningún cambio.",
    3: "La conciliación encontró diferencias, u otra inconsistencia del ETL. Se "
    "revirtió todo.",
    4: "Otra ejecución del ETL tiene el candado. No se leyó ni se escribió nada; "
    "vuelve a intentarlo cuando termine.",
}

MENSAJES_DEL_PREVIEW = {
    1: "Error de configuración, precondición o base de datos. No se escribió nada.",
    2: "La ejecución real RECHAZARÍA este origen. No se escribió nada.",
    3: "El ETL detectó una inconsistencia previa a la carga. No se escribió nada.",
}

CAMPOS_DE_CANDIDATA = (
    "id_lectura",
    "id_sesion",
    "id_embarazo",
    "id_clinica",
    "id_medico",
    "semana",
    "trimestre",
    "fecha_clinica",
    "estado_hr",
    "estado_spo2",
    "estado_mov",
    "semaforo_derivado",
    "semaforo_origen",
    "validacion",
    "en_dim_embarazo",
    "destino",
)


class FormatoInesperado(ValueError):
    """La salida de un comando no tiene la forma acordada."""


def _pares(linea: str) -> dict[str, str]:
    pares = {}
    for trozo in linea.split():
        clave, separador, valor = trozo.partition("=")
        if not separador:
            raise FormatoInesperado(clave)
        pares[clave] = valor
    return pares


def interpretar_previsualizacion(texto: str) -> dict:
    """Lee la salida de ``etl_analitico.py previsualizar`` o falla.

    Solo se aceptan las claves acordadas; si algo no encaja, no se muestra
    la salida cruda.
    """
    simples: dict[str, str] = {}
    candidatas: list[dict] = []
    revalidar: list[dict] = []
    for linea in texto.splitlines():
        if linea.startswith("candidata "):
            pares = _pares(linea[len("candidata "):])
            if tuple(pares) != CAMPOS_DE_CANDIDATA:
                raise FormatoInesperado("candidata")
            candidatas.append(pares)
        elif linea.startswith("revalidar "):
            pares = _pares(linea[len("revalidar "):])
            if set(pares) != {"tabla", "filas"}:
                raise FormatoInesperado("revalidar")
            revalidar.append(pares)
        elif linea.startswith("aviso="):
            simples["aviso"] = linea[len("aviso="):]
        elif "=" in linea and " " not in linea:
            clave, _, valor = linea.partition("=")
            simples[clave] = valor
    requeridas = {
        "modo", "resultado", "version_umbrales", "instantanea", "candado_etl",
        "hechos_existentes", "candidatas", "escrituras", "aviso",
    }
    if not requeridas <= set(simples):
        raise FormatoInesperado("faltan claves")
    if (
        simples["resultado"] != "PREVIEW"
        or simples["modo"] != "SOLO_LECTURA"
        or simples["escrituras"] != "0"
        or int(simples["candidatas"]) != len(candidatas)
    ):
        raise FormatoInesperado("valores")
    return {"resumen": simples, "candidatas": candidatas, "revalidar": revalidar}


def _linea_de_error(texto: str) -> str | None:
    for linea in texto.splitlines():
        if linea.startswith("Error: "):
            return sanear_mensaje(linea[len("Error: "):])
    return None


def _lanzar_etl(dep: Dependencias, orden: str) -> Proceso:
    return dep.ejecutar(
        [dep.python, str(dep.raiz / RUTA_ETL), orden], PLAZO_ETL, dep.raiz
    )


def opcion_previsualizar(dep: Dependencias) -> None:
    """Opción 5. La misma extracción y transformación del ETL, sin escribir."""
    _titulo(dep, "PREVISUALIZACIÓN DE PRÓXIMA CARGA ETL")
    try:
        entorno = validar_entorno(dep, exigir_api=True)
    except EntornoNoValidado as error:
        _informar_entorno_no_validado(dep, error)
        return
    e = str(entorno.episodio["id_embarazo"])
    proceso = _lanzar_etl(dep, "previsualizar")
    if proceso.codigo != 0:
        mensaje = MENSAJES_DEL_PREVIEW.get(
            proceso.codigo, f"Código de salida {proceso.codigo}."
        )
        _decir(dep, f"[FAIL] {mensaje}")
        detalle = _linea_de_error(proceso.error)
        if detalle:
            _decir(dep, f"       {detalle}")
        return
    try:
        datos = interpretar_previsualizacion(proceso.salida)
    except (FormatoInesperado, ValueError):
        _decir(
            dep,
            "[FAIL] La previsualización devolvió un formato inesperado. Por "
            "seguridad no se muestra su salida.",
        )
        return

    r = datos["resumen"]
    _decir(dep, "MODO: SOLO LECTURA — no se realizará ninguna modificación.")
    _decir(dep, f"Instantánea: {r['instantanea']}   Candado del ETL: {r['candado_etl']}")
    _decir(
        dep,
        f"Reglas: {r['version_umbrales']}   Hechos ya cargados: {r['hechos_existentes']}",
    )
    _decir(dep)
    _decir(dep, "EXTRACCIÓN (anti-join: lecturas que el hecho todavía no tiene)")
    _decir(dep, LINEA)
    candidatas = datos["candidatas"]
    _decir(dep, f"Candidatas a hecho nuevo:        {len(candidatas)}")
    ajenas = [c for c in candidatas if c["id_embarazo"] != e]
    if not candidatas:
        _decir(dep, "La próxima carga no crearía hechos; solo revalidaría las dimensiones.")
    if ajenas:
        _decir(
            dep,
            f"[ATENCIÓN] {len(ajenas)} candidata(s) NO pertenecen al embarazo demo "
            f"{e}. No se filtran: el ETL real también las cargaría.",
        )
    for c in candidatas:
        marca = "DEMO" if c["id_embarazo"] == e else "NO ESPERADA"
        _decir(dep)
        _decir(dep, f"Lectura #{c['id_lectura']} (sesión #{c['id_sesion']}) — {marca}")
        _decir(dep, "  CONTEXTO")
        _decir(dep, f"    Embarazo:                   #{c['id_embarazo']}")
        _decir(dep, f"    Fecha clínica (Panamá):     {c['fecha_clinica']}")
        _decir(dep, f"    Semana / trimestre:         {c['semana']} / {c['trimestre']}")
        _decir(dep, f"    Clínica:                    #{c['id_clinica']}")
        _decir(
            dep,
            f"    Médico PRINCIPAL:           #{c['id_medico']} (afiliación verificada)",
        )
        _decir(dep, "  TRANSFORMACIÓN (clasificación por métrica, sin valores)")
        for nombre, clave in (
            ("Frecuencia cardíaca", "estado_hr"),
            ("SpO₂", "estado_spo2"),
            ("Movimiento fetal", "estado_mov"),
        ):
            valor = "no aplica" if c[clave] == "NA" else c[clave]
            _decir(dep, f"    {nombre + ':':<28}{valor}")
        _decir(dep, f"    Semáforo derivado:          {c['semaforo_derivado']}")
        _decir(dep, f"    Semáforo en origen:         {c['semaforo_origen']}")
        _decir(dep, f"    Validación:                 {c['validacion']}")
        _decir(dep, "  DESTINO PREVISTO")
        _decir(dep, f"    Nueva fila candidata en:    {c['destino']}")
        en_dimension = "SÍ" if c["en_dim_embarazo"] == "SI" else "NO"
        _decir(dep, f"    Embarazo ya en Dim_Embarazo: {en_dimension}")
    _decir(dep)
    filas = sum(int(fila["filas"]) for fila in datos["revalidar"])
    _decir(dep, f"Dimensiones y bridge: {filas} filas se revalidarían (upsert tipo 1).")
    _decir(dep, LINEA)
    _decir(dep, f"LIMITACIÓN: {r['aviso']}")
    _decir(dep, "NO SE REALIZÓ NINGUNA ESCRITURA")
    _decir(dep, LINEA)


CLAVES_DEL_RESUMEN = (
    "resultado=",
    "hechos_nuevos=",
    "hechos_existentes=",
    "version_umbrales=",
    "duracion_s=",
)


def interpretar_resumen_etl(texto: str) -> dict:
    """Las claves del resumen del ETL que se muestran. Nada más."""
    claves = {}
    for linea in texto.splitlines():
        if linea.startswith(CLAVES_DEL_RESUMEN):
            clave, _, valor = linea.partition("=")
            claves[clave] = valor
        elif linea.startswith("conciliacion="):
            pares = _pares(linea)
            claves.update(pares)
    if claves.get("resultado") != "SUCCESS" or "hechos_nuevos" not in claves:
        raise FormatoInesperado("resumen")
    return claves


def opcion_ejecutar_etl(dep: Dependencias) -> None:
    """Opción 6. El ETL real, entero, tras validar el destino y confirmar."""
    _titulo(dep, "EJECUTAR ETL FETALALERT")
    try:
        entorno = validar_entorno(dep, exigir_api=True)
    except EntornoNoValidado as error:
        _informar_entorno_no_validado(dep, error)
        return
    _decir(dep, "Destino validado:")
    _decir(dep, f"  Base:        {entorno.base}")
    _decir(dep, f"  Proyecto:    {entorno.proyecto} (db en 127.0.0.1:{entorno.puerto_db})")
    _decir(dep, f"  Rol:         {entorno.rol_etl}")
    _decir(dep, "Es una operación CON ESCRITURAS en los esquemas analitico y privado.")
    _decir(dep, "Se ejecuta como una única transacción: extracción → transformación →")
    _decir(dep, "carga → conciliación → commit; ante cualquier fallo se revierte todo.")
    respuesta = dep.entrada(f"Escribe {CONFIRMACION_ETL} para continuar: ")
    if respuesta.strip() != CONFIRMACION_ETL:
        _decir(dep, "Cancelado. No se ejecutó el ETL.")
        return
    proceso = _lanzar_etl(dep, "ejecutar")
    mensaje = MENSAJES_DEL_ETL.get(
        proceso.codigo, f"Código de salida inesperado: {proceso.codigo}."
    )
    _decir(dep, LINEA)
    if proceso.codigo != 0:
        _decir(dep, f"RESULTADO: FALLO (código {proceso.codigo})")
        _decir(dep, mensaje)
        detalle = _linea_de_error(proceso.error)
        if detalle:
            _decir(dep, f"Detalle: {detalle}")
        _decir(dep, LINEA)
        return
    try:
        resumen = interpretar_resumen_etl(proceso.salida)
    except FormatoInesperado:
        _decir(dep, "RESULTADO: código 0, pero el resumen no tiene el formato esperado.")
        _decir(dep, "Comprueba con 'python scripts/etl_analitico.py conciliar'.")
        _decir(dep, LINEA)
        return
    _decir(dep, f"RESULTADO: {mensaje}")
    _decir(dep, LINEA)
    _decir(dep, f"Hechos nuevos:        {resumen['hechos_nuevos']}")
    _decir(dep, f"Hechos existentes:    {resumen.get('hechos_existentes', '?')}")
    _decir(dep, f"Conciliación:         {resumen.get('conciliacion', '?')} "
                f"({resumen.get('verificaciones', '?')} verificaciones, "
                f"{resumen.get('fallidas', '?')} fallidas)")
    _decir(dep, f"Reglas:               {resumen.get('version_umbrales', '?')}")


# ---------------------------------------------------------------------------
# Menú
# ---------------------------------------------------------------------------

OPCIONES = {
    "1": opcion_verificar,
    "2": opcion_restablecer,
    "3": opcion_desconectar,
    "4": opcion_reconectar,
    "5": opcion_previsualizar,
    "6": opcion_ejecutar_etl,
}


def _titulo(dep: Dependencias, texto: str) -> None:
    _decir(dep, SEPARADOR)
    _decir(dep, texto.center(len(SEPARADOR)).rstrip())
    _decir(dep, SEPARADOR)


def _menu(dep: Dependencias) -> None:
    _titulo(dep, "FETALALERT — DEMO CONTROL")
    _decir(dep, "PREPARACIÓN")
    _decir(dep, "1. Verificar estado")
    _decir(dep, "2. Restablecer baseline")
    _decir(dep)
    _decir(dep, "DEMOSTRACIÓN")
    _decir(dep, "3. Simular desconexión")
    _decir(dep, "4. Restablecer conexión")
    _decir(dep, "5. Previsualizar próxima carga ETL")
    _decir(dep, "6. Ejecutar ETL")
    _decir(dep)
    _decir(dep, "7. Salir")


def main(argv: list[str] | None = None, *, dependencias: Dependencias | None = None) -> int:
    """Bucle del menú. Ninguna opción se ejecuta al abrir."""
    dep = dependencias or Dependencias()
    en_curso: str | None = None
    try:
        while True:
            en_curso = None
            _menu(dep)
            eleccion = dep.entrada("Opción: ").strip()
            if eleccion == "7":
                _decir(dep, "Hasta luego.")
                return CODIGO_DE_EXITO
            accion = OPCIONES.get(eleccion)
            if accion is None:
                _decir(dep, "Opción no válida. Elige un número del 1 al 7.")
                continue
            en_curso = eleccion
            try:
                accion(dep)
            except (DockerNoPermitido, EOFError):
                # Una entrada que se cierra en mitad de una confirmacion es una
                # interrupcion de esa opcion, no un error que el menu absorba.
                raise
            except Exception as error:  # noqa: BLE001 -- nunca un traceback con secretos
                _decir(dep, f"[FAIL] Error inesperado: {_describir_excepcion(error)}.")
    except (KeyboardInterrupt, EOFError):
        _decir(dep)
        _informar_interrupcion(dep, en_curso)
        return CODIGO_INTERRUMPIDO


def _informar_interrupcion(dep: Dependencias, en_curso: str | None) -> None:
    """Qué se puede afirmar tras un Ctrl+C, y nada más.

    En el menú no había nada ejecutándose. Dentro de una opción no se sabe en
    qué punto cayó la interrupción: un ``docker compose stop`` o un ETL pueden
    haber terminado, no haber empezado o seguir su curso en otro proceso. No se
    promete un estado: se pide comprobarlo.
    """
    if en_curso is None:
        _decir(
            dep,
            "Demo Control interrumpido en el menú: no había ninguna opción en curso.",
        )
        return
    _decir(dep, f"Demo Control interrumpido durante la opción {en_curso}.")
    _decir(
        dep,
        "No se puede saber en qué punto quedó. Antes de repetirla, comprueba el "
        "estado de la API y de los datos: vuelve a abrir Demo Control y usa la "
        "opción 1; tras un ETL, 'python scripts\\etl_analitico.py conciliar'.",
    )


if __name__ == "__main__":
    raise SystemExit(main())
