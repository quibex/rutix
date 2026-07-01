from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from freezegun import freeze_time

from rutix.db.models import MoodEntry
from rutix.jobs.scheduler import send_evening_ping_if_needed


@pytest.fixture
def fake_bot():
    b = MagicMock()
    b.send_message = AsyncMock()
    return b


def _session_factory(session):
    def factory():
        class CM:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, *a):
                pass

        return CM()

    return factory


@freeze_time("2026-05-14 18:00:00")  # 21:00 MSK — comfortably past 03:00 boundary
async def test_skips_when_report_done(fake_bot, session):
    session.add(MoodEntry(day=date(2026, 5, 14), sleep_hours=7.5))
    await session.commit()

    sent = await send_evening_ping_if_needed(
        _session_factory(session), fake_bot, telegram_user_id=42, tz="Europe/Moscow"
    )
    assert sent is False
    fake_bot.send_message.assert_not_called()


@freeze_time("2026-05-14 18:00:00")  # 21:00 MSK — comfortably past 03:00 boundary
async def test_sends_when_no_report(fake_bot, session):
    sent = await send_evening_ping_if_needed(
        _session_factory(session), fake_bot, telegram_user_id=42, tz="Europe/Moscow"
    )
    assert sent is True
    fake_bot.send_message.assert_awaited_once()
    kwargs = fake_bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == 42
    assert "/report" in kwargs["text"]


@freeze_time("2026-05-14 18:00:00")  # 21:00 MSK — comfortably past 03:00 boundary
async def test_sends_when_report_row_has_null_sleep(fake_bot, session):
    """Defensive: row exists but sleep_hours is null (report not started)."""
    session.add(MoodEntry(day=date(2026, 5, 14), sleep_hours=None))
    await session.commit()

    sent = await send_evening_ping_if_needed(
        _session_factory(session), fake_bot, telegram_user_id=42, tz="Europe/Moscow"
    )
    assert sent is True
    fake_bot.send_message.assert_awaited_once()
