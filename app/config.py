"""
app/config.py — Pydantic Settings from environment variables.

Pool v2: SQLite + KeyDB, no PostgreSQL.
"""
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    DATA_DIR: str = "/data"

    KEYDB_URL: str = "redis://127.0.0.1:6379/0"

    SECRET_KEY: str = secrets.token_urlsafe(64)
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    TELEGRAM_STATS_INTERVAL: int = 15

    CORS_ORIGINS: str = ""

    HOST: str = "0.0.0.0"
    PORT: int = 8421
    DEBUG: bool = False

    TRAINER_AUTH_TOKEN: str = ""
    EXPORT_TOKEN: str = ""

    MACHINE_ALIVE_TTL: int = 60
    ACTIVE_X_TTL: int = 300

    LEASE_TTL: int = 60
    REQUEUE_INTERVAL: int = 5
    REQUEUE_BATCH: int = 500

    @property
    def db_path(self) -> str:
        return str(Path(self.DATA_DIR) / "pool.db")


@lru_cache
def get_settings() -> Settings:
    return Settings()
