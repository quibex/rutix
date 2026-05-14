from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from freezegun import freeze_time

from rutix.bot.handlers.today import cmd_today
from rutix.db.models import MoodEntry
from rutix.integrations.github import FileContent


@pytest.fixture
def fake_settings():
    s = MagicMock(); s.tz = "Europe/Moscow"; return s


@pytest.fixture
def fake_github():
    g = MagicMock()
    g.read = AsyncMock()
    return g


@pytest.fixture
def fake_message():
    m = MagicMock(); m.answer = AsyncMock(); return m


@freeze_time("2026-05-14 12:00:00", tz_offset=3)
async def test_today_shows_mood_and_meals(fake_message, fake_settings, fake_github, session):
    session.add(MoodEntry(
        day=date(2026, 5, 14), mood=1, anxiety=0, irritability=0, sleep_hours=7.5,
    ))
    await session.commit()

    daily = """# x

## Питание

| Приём | Что | Ккал | Б | Ж | У |
|-------|-----|------|---|---|---|
| Завтрак | Яйца | 200 | 14 | 14 | 2 |
| **Итого** |  | **200** | **14** | **14** | **2** |

## Что сделано
-
## Заметки
-
"""
    fake_github.read.return_value = FileContent(text=daily, sha="x")

    def session_factory_call():
        class CM:
            async def __aenter__(self_inner):
                return session
            async def __aexit__(self_inner, *a):
                pass
        return CM()
    sf = MagicMock(side_effect=lambda: session_factory_call())

    await cmd_today(
        fake_message,
        settings=fake_settings,
        github=fake_github,
        session_factory=sf,
    )

    reply = fake_message.answer.call_args.args[0]
    assert "+1" in reply or "1" in reply  # mood
    assert "7.5" in reply
    assert "200" in reply  # kcal


@freeze_time("2026-05-14 12:00:00", tz_offset=3)
async def test_today_when_no_mood_entry(fake_message, fake_settings, fake_github, session):
    daily = "## Питание\n\n| Приём | Что | Ккал | Б | Ж | У |\n|---|---|---|---|---|---|\n|  |  |  |  |  |  |\n| **Итого** |  |  |  |  |  |\n\n## Что сделано\n-\n## Заметки\n-\n"
    fake_github.read.return_value = FileContent(text=daily, sha="x")

    def session_factory_call():
        class CM:
            async def __aenter__(self_inner):
                return session
            async def __aexit__(self_inner, *a):
                pass
        return CM()
    sf = MagicMock(side_effect=lambda: session_factory_call())

    await cmd_today(
        fake_message,
        settings=fake_settings,
        github=fake_github,
        session_factory=sf,
    )

    reply = fake_message.answer.call_args.args[0]
    assert "не делал" in reply.lower() or "/track" in reply.lower()
