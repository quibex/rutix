import pytest
from pydantic import ValidationError

from rutix.settings import Settings


def test_settings_loads_required_fields_from_env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_USER_ID", "12345")
    monkeypatch.setenv("GITHUB_API_TOKEN", "ghp_test")

    s = Settings(_env_file=None)

    assert s.bot_token == "test-token"
    assert s.telegram_user_id == 12345
    assert s.github_api_token == "ghp_test"


def test_settings_defaults(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_USER_ID", "1")
    monkeypatch.setenv("GITHUB_API_TOKEN", "x")

    s = Settings(_env_file=None)

    assert s.life_repo == "quibex/life"
    assert s.tz == "Europe/Moscow"
    assert s.database_url == "sqlite+aiosqlite:///data/bot.db"


def test_settings_missing_required_raises(monkeypatch):
    for var in ["BOT_TOKEN", "TELEGRAM_USER_ID", "GITHUB_API_TOKEN"]:
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
