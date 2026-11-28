"""Application settings, loaded from environment variables and the .env file."""

from functools import lru_cache
from typing import Annotated

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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
    # NoDecode: don't try to read this value as JSON; our validator below splits the commas.
    default_quotes: Annotated[list[str], NoDecode] = ["USD", "GBP", "NGN", "JPY", "CHF", "CAD"]

    # --- Week 3: the "Ask your data" agent ---
    # SecretStr: printing or logging the settings shows "**********", never the key.
    anthropic_api_key: SecretStr | None = None
    agent_model: str = "claude-sonnet-5-5"
    agent_dev_model: str = "claude-haiku-5-5"  # cheaper, for manual tries (--dev)
    agent_max_steps: int = 8
    agent_api_base_url: str = "http://localhost:8000"  # the agent calls OUR API here

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
