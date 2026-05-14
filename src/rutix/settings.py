"""Configuration via env vars (validated by pydantic-settings)."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    bot_token: str = Field(...)
    telegram_user_id: int = Field(...)
    github_api_token: str = Field(...)
    anthropic_api_key: str = Field(...)
    todoist_token: str = Field(...)

    life_repo: str = Field(default="quibex/life")
    database_url: str = Field(default="sqlite+aiosqlite:///data/bot.db")
    tz: str = Field(default="Europe/Moscow")


def load_settings() -> Settings:
    return Settings()
