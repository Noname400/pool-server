"""
app/config.py — Pydantic Settings from environment variables.

Pool v3: SQLite + KeyDB, lease-based distribution for ~2000 GPU cards.
"""
import logging
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_logger = logging.getLogger("pool_server.config")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    DATA_DIR: str = "/data"

    KEYDB_URL: str = "redis://127.0.0.1:6379/0"

    SECRET_KEY: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    TELEGRAM_STATS_INTERVAL: int = 15

    CORS_ORIGINS: str = ""

    HOST: str = "0.0.0.0"
    PORT: int = 8421
    DEBUG: bool = False

    TRAINER_AUTH_TOKEN: str = ""
    EXPORT_TOKEN: str = ""

    MACHINE_ALIVE_TTL: int = 180

    LEASE_TTL: int = 60
    REQUEUE_INTERVAL: int = 5
    REQUEUE_BATCH: int = 500

    READY_LOW_WATERMARK: int = 10_000
    READY_TARGET: int = 50_000
    REFILL_INTERVAL: float = 1.0
    REFILL_BATCH: int = 5_000

    @property
    def db_path(self) -> str:
        return str(Path(self.DATA_DIR) / "pool.db")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if not s.SECRET_KEY:
        s.SECRET_KEY = secrets.token_urlsafe(64)
        _logger.warning(
            "SECRET_KEY not set — generated ephemeral key. "
            "JWT sessions will NOT survive restarts or work across multiple workers. "
            "Set SECRET_KEY in .env for production."
        )
    return s
