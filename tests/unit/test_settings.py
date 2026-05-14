import pytest
from pydantic import ValidationError

from rutix.settings import Settings


REQUIRED_VARS = [
    "BOT_TOKEN",
    "TELEGRAM_USER_ID",
    "GITHUB_API_TOKEN",
    "ANTHROPIC_API_KEY",
    "TODOIST_TOKEN",
]


def _set_all(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "test-bot")
    monkeypatch.setenv("TELEGRAM_USER_ID", "12345")
    monkeypatch.setenv("GITHUB_API_TOKEN", "ghp_test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("TODOIST_TOKEN", "tod_test")


def test_settings_loads_required_fields_from_env(monkeypatch):
    _set_all(monkeypatch)

    s = Settings(_env_file=None)

    assert s.bot_token == "test-bot"
    assert s.telegram_user_id == 12345
    assert s.github_api_token == "ghp_test"
    assert s.anthropic_api_key == "sk-ant-test"
    assert s.todoist_token == "tod_test"


def test_settings_defaults(monkeypatch):
    for var in ["LIFE_REPO", "TZ", "DATABASE_URL"]:
        monkeypatch.delenv(var, raising=False)
    _set_all(monkeypatch)

    s = Settings(_env_file=None)

    assert s.life_repo == "quibex/life"
    assert s.tz == "Europe/Moscow"
    assert s.database_url == "sqlite+aiosqlite:///data/bot.db"


@pytest.mark.parametrize("missing", REQUIRED_VARS)
def test_settings_missing_required_raises(monkeypatch, missing):
    _set_all(monkeypatch)
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
