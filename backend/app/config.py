"""Application settings, shared by everything that reads the repository's ``.env``.

**Why the JWT secret is optional here (SCRUM-70).** ``settings`` is built at
import time, and ``app.config`` is imported by things that have nothing to do
with authentication: ``alembic/env.py``, the ETL entry point and the dataset
loader all pull ``database_url`` from here and never issue or verify a token.
Declaring ``jwt_secret_key`` as a required field would make a missing variable
break ``alembic upgrade head``, the loader and the ETL, none of which need it.

So the field is optional in the shared settings, and the requirement is enforced
**where it matters**: :func:`exigir_configuracion_jwt` is called from
``app.main`` while the FastAPI application is being built, so the API fails
closed and refuses to start without a usable secret, while every non-API tool
keeps working. There is no usable default anywhere -- ``None`` signs nothing.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPOSITORY_ROOT / ".env"

# Minimum size of the signing material, in **bytes of UTF-8**, not characters.
# HS256 is HMAC-SHA256, whose block size is 64 bytes and whose output is 32; a
# key shorter than the digest it produces adds nothing. Counting bytes rather
# than characters is what keeps a short string of wide code points from passing
# a check it should not.
LONGITUD_MINIMA_DEL_SECRETO = 32

# Bounds of the token lifetime, in minutes. The floor keeps a zero or negative
# expiry -- a token born expired -- out of the configuration; the ceiling is one
# day, because this MVP implements no revocation and a credential that cannot be
# revoked should not outlive a working session by much.
EXPIRACION_MINIMA_MINUTOS = 1
EXPIRACION_MAXIMA_MINUTOS = 1440
EXPIRACION_POR_OMISION_MINUTOS = 30

# Ambientes que operan sobre el dataset simulado, y por tanto los únicos donde
# este proyecto puede comportarse de forma permisiva. Vive aquí, junto a
# ``app_env``, porque es el valor con el que se compara; ``app.loader`` y
# ``app.db.privilegios`` lo importan de este módulo en lugar de declararlo cada
# uno por su cuenta.
AMBIENTES_PERMITIDOS = frozenset({"development", "test", "ci"})

MENSAJE_SECRETO_AUSENTE = (
    "Falta la variable de entorno JWT_SECRET_KEY. La API no arranca sin "
    "material de firma: no existe un valor por omision. Genera uno con "
    "'python -c \"import secrets; print(secrets.token_urlsafe(32))\"' y "
    "definelo en el entorno o en el archivo .env, que no se versiona."
)

# Deliberately generic, and it covers two different failures: a secret that is
# too short and a secret that repeats the PostgreSQL password. Naming which one
# happened would put a fact about the secret into a log -- "your signing key is
# your database password" is exactly the sentence an attacker reading a log
# would like to find. The absence of the variable is reported precisely instead,
# because "it is not set" reveals nothing at all.
MENSAJE_SECRETO_INVALIDO = (
    "La variable JWT_SECRET_KEY no cumple los requisitos de la configuracion "
    f"de autenticacion: debe aportar al menos {LONGITUD_MINIMA_DEL_SECRETO} "
    "bytes de material aleatorio y no puede reutilizar otra credencial del "
    "proyecto. Genera uno nuevo con 'python -c \"import secrets; "
    "print(secrets.token_urlsafe(32))\"'. Su valor no se registra."
)


class ConfiguracionJWTInvalida(RuntimeError):
    """The API cannot issue or accept tokens with the configuration it was given.

    Carries only the sanitised message above. The offending value is never
    attached, formatted or logged.
    """


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    app_name: str = "FetalAlert API"
    app_env: str = "development"
    # **El valor por omisión nombra al rol restringido, no al propietario.**
    # Antes apuntaba a ``fetalalert_dev``, que es el migrador y dueño de las
    # tablas: un despliegue que olvidara definir la variable habría arrancado
    # con la credencial que omite toda política RLS, y el olvido no se habría
    # notado. Ahora el defecto nombra a ``fetalalert_api`` y lleva una
    # contraseña que no abre nada, así que un entorno sin configurar falla al
    # conectar en vez de funcionar con demasiados privilegios.
    database_url: str = (
        "postgresql+psycopg://fetalalert_api:sin_configurar@localhost:5432/fetalalert"
    )

    # ``SecretStr`` and not ``str``: its ``repr`` is ``SecretStr('**********')``,
    # so the value cannot reach a log, a traceback or a settings dump by being
    # printed. Reading it requires asking for it by name.
    jwt_secret_key: SecretStr | None = None
    jwt_expiration_minutes: int = Field(
        default=EXPIRACION_POR_OMISION_MINUTOS,
        ge=EXPIRACION_MINIMA_MINUTOS,
        le=EXPIRACION_MAXIMA_MINUTOS,
    )

    @field_validator("jwt_secret_key", mode="before")
    @classmethod
    def _en_blanco_es_ausente(cls, valor: Any) -> Any:
        """``JWT_SECRET_KEY=`` or a value of only whitespace means *not configured*.

        ``.env.example`` ships the variable empty on purpose, so copying it as is
        yields an empty string, not ``None``. Treating that as absent is what
        makes the refusal say what actually happened -- the secret is missing --
        instead of reporting a short secret nobody set.

        A non-blank value is returned **untouched**. Stripping it would sign with
        a key different from the one configured, silently.
        """
        texto = valor.get_secret_value() if isinstance(valor, SecretStr) else valor
        if isinstance(texto, str) and not texto.strip():
            return None
        return valor


settings = Settings()


@dataclass(frozen=True)
class ConfiguracionJWT:
    """Validated signing material and lifetime, built once and passed explicitly.

    A frozen dataclass rather than the ``Settings`` object so the functions that
    issue and validate tokens take exactly what they need and nothing else, and
    so a test can build one without touching the environment.

    ``secreto`` is excluded from the generated ``repr``. Without that, printing
    this object -- a debugger, a traceback rendered with its locals, a stray log
    line -- would show the signing key in clear, which is exactly what wrapping
    it in ``SecretStr`` inside ``Settings`` exists to prevent.
    """

    secreto: str = field(repr=False)
    expiracion: timedelta


def _password_de_postgresql(url_de_base: str) -> str | None:
    """The password embedded in the database URL, or ``None`` if unreadable.

    Parsed with SQLAlchemy's own parser instead of a regular expression: a
    password may legitimately contain ``@`` and ``/``, and a hand-written split
    gets that wrong. An unparseable URL is not an error here -- this function
    only exists to refuse a reused password, and an URL nobody can read carries
    no password to compare against.
    """
    try:
        return make_url(url_de_base).password
    except Exception:  # noqa: BLE001 -- an unreadable URL is not a JWT problem
        return None


def exigir_configuracion_jwt(configuracion: Settings | None = None) -> ConfiguracionJWT:
    """The validated JWT configuration, or a refusal to operate.

    Called from ``app.main`` when the application is built, which is what makes
    the API fail closed: uvicorn imports that module, the check runs, and a
    missing or unusable secret stops the process with a sentence instead of
    letting it serve requests it cannot authenticate.

    Three rules, and each one closes a specific hole:

    * **present** -- there is no default, so ``None`` means the deployment never
      configured one and must not be allowed to sign anything;
    * **long enough** -- measured in bytes of UTF-8, see
      :data:`LONGITUD_MINIMA_DEL_SECRETO`;
    * **not a reused credential** -- a signing key equal to the PostgreSQL
      password turns one leak into two.

    The exception carries a sanitised message and never the value.
    """
    configuracion = configuracion if configuracion is not None else settings

    if configuracion.jwt_secret_key is None:
        raise ConfiguracionJWTInvalida(MENSAJE_SECRETO_AUSENTE)

    secreto = configuracion.jwt_secret_key.get_secret_value()

    if len(secreto.encode("utf-8")) < LONGITUD_MINIMA_DEL_SECRETO:
        raise ConfiguracionJWTInvalida(MENSAJE_SECRETO_INVALIDO)

    password = _password_de_postgresql(configuracion.database_url)
    if password is not None and secreto == password:
        raise ConfiguracionJWTInvalida(MENSAJE_SECRETO_INVALIDO)

    return ConfiguracionJWT(
        secreto=secreto,
        expiracion=timedelta(minutes=configuracion.jwt_expiration_minutes),
    )


# ---------------------------------------------------------------------------
# URLs de los procesos que no son la API (SCRUM-98)
# ---------------------------------------------------------------------------

# Tres procesos, tres credenciales, tres variables. La separación es el punto:
# la API corre con un rol restringido, las migraciones con el rol que posee los
# objetos y el ETL con un rol técnico propio. Una sola URL para los tres
# obligaría a darle a la API los privilegios del migrador.
VARIABLE_URL_API = "DATABASE_URL"
VARIABLE_URL_ALEMBIC = "ALEMBIC_DATABASE_URL"
VARIABLE_URL_ETL = "ETL_DATABASE_URL"

# Motor exigido. Se comprueba sobre la URL, sin conectarse, para que un destino
# equivocado se rechace antes de que exista un engine que pueda crear algo.
MOTOR_REQUERIDO = "postgresql"


class UrlDeEntornoInvalida(RuntimeError):
    """El proceso no recibió una URL utilizable, y no va a inventarse una.

    Lleva solo el mensaje saneado: el valor rechazado nunca se adjunta, porque
    una URL de conexión contiene credenciales.
    """


def _leer_del_entorno(variable: str) -> str | None:
    """El valor de **una** variable, de ``os.environ`` o del ``.env`` del repo.

    Se construye un modelo con un único campo, en la llamada, y ese detalle es
    la corrección: una clase con los dos campos declarados leería ambas
    variables cada vez, de modo que pedir la del ETL cargaría también la del
    migrador en memoria del mismo proceso. Un proceso solo debe tener a la vista
    la credencial que le corresponde.

    Nada de esto vive en ``Settings``. ``app.config`` lo importa la API, y un
    campo allí bastaría para que el proceso web cargase la credencial del
    migrador solo por existir en el entorno.

    Leer el ``.env`` -- y no solo ``os.environ`` -- es lo que mantiene el flujo
    de trabajo local del repositorio: cada desarrolladora define sus
    credenciales en su propio ``.env``, que no se versiona.
    """
    modelo = type(
        "_UrlDeUnProceso",
        (BaseSettings,),
        {
            "model_config": SettingsConfigDict(
                env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore"
            ),
            "__annotations__": {"valor": "str | None"},
            "valor": Field(default=None, validation_alias=variable),
        },
    )
    return modelo().valor


# Mensaje de una variable ausente. Dice qué falta y quién la usa, y no propone
# ningún valor: no existe un valor por omisión, a propósito.
_FORMATO_AUSENTE = (
    "Falta la variable de entorno {variable}. {proceso} no arranca sin ella y "
    "**no** reutiliza {alternativa}: esa credencial tiene más privilegios de los "
    "que este proceso debe tener, y heredarla en silencio anularía la separación "
    "de roles. Defínela en el entorno o en el archivo .env, que no se versiona."
)

_DESCRIPCION = {
    VARIABLE_URL_ALEMBIC: (
        "El proceso de migraciones",
        "DATABASE_URL",
    ),
    VARIABLE_URL_ETL: (
        "El ETL analítico",
        "DATABASE_URL",
    ),
}


def exigir_url_de_entorno(variable: str) -> str:
    """La URL PostgreSQL de un proceso, o una negativa a operar.

    Sin valor por omisión y **sin respaldo**: si la variable falta, esta
    función levanta en lugar de caer sobre ``DATABASE_URL``. Un respaldo
    silencioso es exactamente el fallo que la separación de credenciales existe
    para evitar -- las migraciones correrían con el rol restringido de la API,
    o peor, la API acabaría corriendo con el rol que posee las tablas.

    Se valida solo la forma, sin abrir conexión: presente, no vacía, y
    PostgreSQL. Si el rol al otro lado tiene los privilegios correctos es otra
    pregunta, y la responde ``app.db.privilegios`` en el arranque.
    """
    if variable not in _DESCRIPCION:
        raise UrlDeEntornoInvalida(
            f"'{variable}' no es una variable de conexión reconocida por este proyecto."
        )

    crudo = _leer_del_entorno(variable)

    if crudo is None or not crudo.strip():
        proceso, alternativa = _DESCRIPCION[variable]
        raise UrlDeEntornoInvalida(
            _FORMATO_AUSENTE.format(
                variable=variable, proceso=proceso, alternativa=alternativa
            )
        )

    valor = crudo.strip()

    try:
        analizada = make_url(valor)
    except Exception:  # noqa: BLE001 -- el mensaje del parser puede repetir la URL
        # ``from None`` y no ``from error``: el texto del parser suele incluir el
        # valor que no pudo leer, y ese valor es una credencial.
        raise UrlDeEntornoInvalida(
            f"El valor de {variable} no es una URL de conexión válida. Su "
            "contenido no se muestra porque puede contener credenciales."
        ) from None

    if analizada.get_backend_name() != MOTOR_REQUERIDO:
        raise UrlDeEntornoInvalida(
            f"{variable} no apunta a una base PostgreSQL. Este proyecto solo "
            "opera contra PostgreSQL; el valor configurado no se muestra porque "
            "puede contener credenciales."
        )

    return valor
