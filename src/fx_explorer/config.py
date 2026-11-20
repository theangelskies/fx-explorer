"""Application settings, loaded from environment variables and the .env file."""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every field here can be set in .env (names are case-insensitive)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env also holds POSTGRES_* values for Docker; we don't need them here
    )

    database_url: str
    test_database_url: str
    frankfurter_base_url: str = "https://api.frankfurter.dev/v2"
    http_timeout_seconds: float = 10
    max_retries: int = 3
    request_delay_seconds: float = 1
    default_base: str = "EUR"
    default_quotes: list[str] = ["USD", "GBP", "NGN", "JPY", "CHF", "CAD"]

    @field_validator("default_quotes", mode="before")
    @classmethod
    def _split_quotes(cls, value: object) -> object:
        """Allow DEFAULT_QUOTES=USD,GBP,NGN in .env (a plain comma-separated string)."""
        if isinstance(value, str):
            return [code.strip().upper() for code in value.split(",") if code.strip()]
        return value

    @field_validator("default_base")
    @classmethod
    def _upper_base(cls, value: str) -> str:
        return value.strip().upper()


@lru_cache
def get_settings() -> Settings:
    """Create the settings once and reuse them."""
    return Settings()
