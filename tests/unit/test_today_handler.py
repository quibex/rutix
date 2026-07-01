from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from freezegun import freeze_time

from datetime import datetime

from rutix.bot.handlers.today import cmd_today
from rutix.db.models import MoodEntry, StateEntry
from rutix.integrations.github import FileContent


@pytest.fixture
def fake_settings():
    s = MagicMock()
    s.tz = "Europe/Moscow"
    return s


@pytest.fixture
def fake_github():
    g = MagicMock()
    g.read = AsyncMock()
    return g


@pytest.fixture
def fake_message():
    m = MagicMock()
    m.answer = AsyncMock()
    return m


@freeze_time("2026-05-14 12:00:00", tz_offset=3)
async def test_today_shows_state_and_report_and_meals(
    fake_message, fake_settings, fake_github, session
):
    session.add(MoodEntry(day=date(2026, 5, 14), sleep_hours=7.5))
    session.add(
        StateEntry(
            day=date(2026, 5, 14),
            ts=datetime(2026, 5, 14, 9, 15),
            mood=1,
            energy=0,
            appetite=1,
        )
    )
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
    assert "09:15" in reply  # state snapshot time
    assert "+1" in reply  # mood
    assert "7.5" in reply  # report sleep
    assert "200" in reply  # kcal


@freeze_time("2026-05-14 12:00:00", tz_offset=3)
async def test_today_when_no_entries(fake_message, fake_settings, fake_github, session):
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
    assert "/state" in reply.lower()
    assert "/report" in reply.lower()
