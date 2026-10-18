"""Application configuration. Values come from environment variables or a local .env file."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_prefix="INVEST_",
        extra="ignore",
    )

    app_name: str = "Invest App"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = False
    host: str = "127.0.0.1"
    port: int = 8100
    log_level: str = "INFO"
    log_dir: Path = BASE_DIR / "logs"
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'invest.db'}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
