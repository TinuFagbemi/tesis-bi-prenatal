from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPOSITORY_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    app_name: str = "FetalAlert API"
    app_env: str = "development"
    database_url: str = "postgresql+psycopg://fetalalert_dev:dev_only_change_me@localhost:5433/fetalalert_dev"


settings = Settings()
