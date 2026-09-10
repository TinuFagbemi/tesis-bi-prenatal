"""Configuration of the simulated edge node (SCRUM-64).

Four settings and nothing else: where the local SQLite file lives, which API
the sender talks to, how long it waits for an answer, and how long SQLite waits
for a lock before failing fast. They are read with the same mechanism the backend already uses -- ``pydantic-settings`` over the
repository's ``.env`` -- but under their own ``EDGE_`` prefix and in a class of
their own, so the edge cannot accidentally inherit, override or leak the
backend's ``database_url``.

**No credentials, ever.** The edge talks HTTP to a public endpoint and writes to
a local file; neither needs a secret. There is deliberately no field for one, so
a password cannot be introduced by configuration alone. Authentication is
SCRUM-65 and beyond, and adding a token here before there is an endpoint to send
it to would be speculation.

**Why a separate class instead of extending ``app.config.Settings``.** The
backend's settings carry ``database_url``, and the edge must never open a
PostgreSQL connection -- everything it sends goes through the API. Keeping the
two apart means that rule is enforced by what is reachable, not by discipline.

``settings_edge`` is built at import time, like the backend's, for the CLI to
use. The library functions in this package never read it: they take their
arguments explicitly, so a test can point them at a temporary file without
touching the environment.

All data handled by this node is fictitious and simulated.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

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
    """Settings of the edge node, read from ``EDGE_*`` variables."""

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


settings_edge = EdgeSettings()
