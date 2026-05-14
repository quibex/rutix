from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from freezegun import freeze_time

from rutix.bot.handlers.week import cb_week_day, cmd_week
from rutix.db.models import MoodEntry
from rutix.integrations.github import FileContent


@pytest.fixture
def fake_settings():
    s = MagicMock(); s.tz = "Europe/Moscow"; return s


@pytest.fixture
def fake_github():
    g = MagicMock(); g.read = AsyncMock(); return g


@pytest.fixture
def fake_message():
    m = MagicMock(); m.answer = AsyncMock(); return m


@freeze_time("2026-05-14 12:00:00", tz_offset=3)
async def test_cmd_week_shows_7_buttons(fake_message, fake_settings):
    await cmd_week(fake_message, settings=fake_settings)

    fake_message.answer.assert_awaited()
    kw = fake_message.answer.call_args.kwargs
    kb = kw["reply_markup"]
    flat = [b for row in kb.inline_keyboard for b in row]
    assert len(flat) == 7
    callback_dates = [b.callback_data.split(":")[1] for b in flat]
    # Week 20 of 2026: Mon May 11 .. Sun May 17
    assert callback_dates == [
        "2026-05-11", "2026-05-12", "2026-05-13", "2026-05-14",
        "2026-05-15", "2026-05-16", "2026-05-17",
    ]


@freeze_time("2026-05-14 12:00:00", tz_offset=3)
async def test_cb_week_day_replies_with_day_summary(
    fake_settings, fake_github, session,
):
    session.add(MoodEntry(day=date(2026, 5, 13), mood=2, anxiety=0, irritability=1, sleep_hours=8))
    await session.commit()

    daily = "## Питание\n\n| Приём | Что | Ккал | Б | Ж | У |\n|---|---|---|---|---|---|\n| Обед | Плов | 400 | 17 | 12 | 56 |\n| **Итого** |  | **400** | **17** | **12** | **56** |\n"
    fake_github.read.return_value = FileContent(text=daily, sha="x")

    cb = MagicMock()
    cb.data = "week_day:2026-05-13"
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()

    def session_factory_call():
        class CM:
            async def __aenter__(self_inner): return session
            async def __aexit__(self_inner, *a): pass
        return CM()
    sf = MagicMock(side_effect=lambda: session_factory_call())

    await cb_week_day(cb, settings=fake_settings, github=fake_github, session_factory=sf)

    cb.message.edit_text.assert_awaited()
    text = cb.message.edit_text.call_args.args[0]
    assert "2026-05-13" in text
    assert "+2" in text or "2" in text
    assert "400" in text
