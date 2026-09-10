"""Configuration of the simulated edge node (SCRUM-64, extended in SCRUM-65).

Eight settings and nothing else. Four describe the node itself: where the local
SQLite file lives, which API the sender talks to, how long it waits for an
answer, and how long SQLite waits for a lock before failing fast. Four more
describe the retry policy: how many attempts an event gets in total, the base and
the ceiling of the incremental wait, and how many events one round takes at a
time. They are read with the same mechanism the backend already uses --
``pydantic-settings`` over the repository's ``.env`` -- but under their own
``EDGE_`` prefix and in a class of their own, so the edge cannot accidentally
inherit, override or leak the backend's ``database_url``.

**No credentials, ever.** The edge talks HTTP to a public endpoint and writes to
a local file; neither needs a secret. There is deliberately no field for one, so
a password cannot be introduced by configuration alone. Authentication, JWT and
RBAC are **not** part of SCRUM-65 either -- that ticket is deferred
synchronisation, retries and traceability -- and adding a token here before there
is an endpoint to send it to would be speculation.

**Why a separate class instead of extending ``app.config.Settings``.** The
backend's settings carry ``database_url``, and the edge must never open a
PostgreSQL connection -- everything it sends goes through the API. Keeping the
two apart means that rule is enforced by what is reachable, not by discipline.

**Nothing is built at import time (SCRUM-65).** There used to be a
``settings_edge = EdgeSettings()`` at module level, and it made a whole class of
misconfiguration unreportable: ``EDGE_MAX_ATTEMPTS=abc`` raised Pydantic's
``ValidationError`` while the module was still being imported, so the failure
happened *before* ``main()`` existed, before its ``try``, and before anything
could turn it into a sentence and an exit code. What the user got was a
traceback.

So the settings are loaded by :func:`cargar_settings_edge`, explicitly, from
inside the CLI's controlled boundary. The library functions in this package never
read a global: they take their arguments explicitly, so a test can point them at a
temporary file without touching the environment, and importing this module --
or ``app.edge``, or the CLI script -- reads nothing and can fail at nothing.

All data handled by this node is fictitious and simulated.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.edge.politica import (
    BASE_DELAY_POR_OMISION,
    BATCH_LIMIT_POR_OMISION,
    MAX_ATTEMPTS_POR_OMISION,
    MAX_DELAY_POR_OMISION,
    ConfiguracionInvalida,
    PoliticaDeReintentos,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = REPOSITORY_ROOT / ".env"

# Under ``data/`` next to the generated dataset, and ignored by Git the same
# way. A demonstration database is a local artefact, never a versioned file.
RUTA_SQLITE_POR_OMISION = REPOSITORY_ROOT / "data" / "edge" / "nodo_edge.sqlite3"

# Local API of the development environment. Not a deployment target: this
# project has none.
URL_API_POR_OMISION = "http://127.0.0.1:8000"

# Seconds. Long enough for a local request, short enough that a node with no
# connectivity does not appear to hang. A timeout is not data loss: the event
# stays in the outbox with its key, and another pass may retry it.
TIMEOUT_HTTP_POR_OMISION = 10.0

# Milliseconds SQLite waits for a lock before giving up. It exists so that a
# second sender invoked by mistake fails with a readable error instead of
# blocking forever. It does **not** protect the ENVIADO state -- that is the job
# of the conditional UPDATE in ``app.edge.outbox``.
ESPERA_DE_BLOQUEO_POR_OMISION = 5000


class EdgeSettings(BaseSettings):
    """Settings of the edge node, read from ``EDGE_*`` variables.

    The four policy fields are only defaults: the value that governs a given
    event is the one it **adopted** when it claimed its first attempt, stored in
    ``outbox.max_intentos_aplicado``. Changing the environment therefore reaches
    events that have not started synchronising yet, and cannot rewrite the
    contract an event is already living under.
    """

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="EDGE_",
    )

    sqlite_path: Path = RUTA_SQLITE_POR_OMISION
    api_base_url: str = URL_API_POR_OMISION
    http_timeout: float = TIMEOUT_HTTP_POR_OMISION
    busy_timeout_ms: int = ESPERA_DE_BLOQUEO_POR_OMISION

    # Total attempts per event, **the first one included**.
    max_attempts: int = MAX_ATTEMPTS_POR_OMISION
    base_delay_seconds: float = BASE_DELAY_POR_OMISION
    max_delay_seconds: float = MAX_DELAY_POR_OMISION
    # Events one **round** takes. Not the maximum number of attempts, and not
    # the total a run processes -- that is bounded by the watermark.
    batch_limit: int = BATCH_LIMIT_POR_OMISION

    def politica(self) -> PoliticaDeReintentos:
        """The configured policy, validated.

        Raises :class:`app.edge.politica.ConfiguracionInvalida` for anything the
        policy refuses -- a non-finite wait, a ceiling below the base, a limit
        below one. Built here rather than at import time so a bad ``.env`` fails
        with a sentence when a command runs, instead of breaking every import.
        """
        return PoliticaDeReintentos(
            max_attempts=self.max_attempts,
            base_delay_seconds=self.base_delay_seconds,
            max_delay_seconds=self.max_delay_seconds,
            batch_limit=self.batch_limit,
            http_timeout=self.http_timeout,
        )


def _detalle_de_validacion(error: ValidationError) -> str:
    """Which ``EDGE_*`` variables are wrong and why, without echoing a value.

    Pydantic's own rendering of a ``ValidationError`` embeds ``input_value``,
    and an environment variable can carry anything -- a path with somebody's
    name, a token pasted by mistake. So the message is rebuilt from the two
    parts of each error that are schema and not data: the field and the
    machine-readable error type. Same rule ``app.edge.captura`` follows for a
    rejected package: pick the safe fields one by one, never format the
    exception.
    """
    partes = []
    for detalle in error.errors():
        campo = ".".join(str(tramo) for tramo in detalle.get("loc", ()))
        variable = f"EDGE_{campo.upper()}" if campo else "EDGE_*"
        partes.append(f"{variable}: {detalle.get('type', 'invalido')}")
    return "; ".join(partes)


def cargar_settings_edge() -> EdgeSettings:
    """Read the ``EDGE_*`` settings, turning a bad environment into a sentence.

    The only place this package builds an :class:`EdgeSettings`. It is called
    from inside the CLI's ``try``, so a malformed variable becomes exit code 1
    and one line on stderr instead of a traceback.

    Only :class:`pydantic.ValidationError` is caught -- not ``Exception``: a
    missing file or a permission error is not a configuration problem and must
    not be disguised as one.
    """
    try:
        return EdgeSettings()
    except ValidationError as error:
        raise ConfiguracionInvalida(
            "La configuracion del nodo edge no es valida "
            f"({_detalle_de_validacion(error)}). Revisa las variables EDGE_* "
            "del entorno o del archivo .env."
        ) from error
