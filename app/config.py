"""Settings loaded from the environment, once, at startup."""
from __future__ import annotations

from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    telegram_bot_token: str

    database_path: str = "weatherbot.db"
    log_level: str = "INFO"

    city_name: str = "Almaty"
    latitude: float = Field(43.2389, ge=-90, le=90)
    longitude: float = Field(76.8897, ge=-180, le=180)
    default_timezone: str = "Asia/Almaty"

    phrase_mode_enabled: bool = True

    @field_validator("default_timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"invalid IANA timezone: {value!r}") from exc
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
