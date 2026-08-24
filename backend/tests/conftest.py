"""Shared Alembic wiring for the migration tests.

Both migration modules need an Alembic ``Config`` that resolves ``alembic/``
from the repository layout instead of the current working directory, so pytest
can be launched from anywhere. It lives here rather than being duplicated.
"""

from pathlib import Path

from alembic.config import Config

DIRECTORIO_BACKEND = Path(__file__).resolve().parents[1]
DIRECTORIO_ALEMBIC = DIRECTORIO_BACKEND / "alembic"


def construir_config_alembic() -> Config:
    """Alembic ``Config`` that does not depend on the working directory.

    ``sqlalchemy.url`` is left unset on purpose: the offline tests never open a
    connection, and ``alembic/env.py`` always overwrites it with
    ``settings.database_url`` anyway.
    """
    config = Config()
    config.set_main_option("script_location", str(DIRECTORIO_ALEMBIC))
    return config
