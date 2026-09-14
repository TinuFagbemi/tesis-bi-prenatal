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

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from pydantic import Field, SecretStr
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
    database_url: str = "postgresql+psycopg://fetalalert_dev:dev_only_change_me@localhost:5433/fetalalert_dev"

    # ``SecretStr`` and not ``str``: its ``repr`` is ``SecretStr('**********')``,
    # so the value cannot reach a log, a traceback or a settings dump by being
    # printed. Reading it requires asking for it by name.
    jwt_secret_key: SecretStr | None = None
    jwt_expiration_minutes: int = Field(
        default=EXPIRACION_POR_OMISION_MINUTOS,
        ge=EXPIRACION_MINIMA_MINUTOS,
        le=EXPIRACION_MAXIMA_MINUTOS,
    )


settings = Settings()


@dataclass(frozen=True)
class ConfiguracionJWT:
    """Validated signing material and lifetime, built once and passed explicitly.

    A frozen dataclass rather than the ``Settings`` object so the functions that
    issue and validate tokens take exactly what they need and nothing else, and
    so a test can build one without touching the environment.
    """

    secreto: str
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
