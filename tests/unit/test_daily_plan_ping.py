"""Tests for the 09:00 daily-plan ping."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from rutix.integrations.github import FileContent
from rutix.jobs.daily_plan import (
    EMPTY_PLAN_TEXT,
    daily_plan_ping,
    format_plan_message,
)


def test_format_plan_message_with_bullets():
    msg = format_plan_message(["встреча 14:00", "купить хлеб"], "2026-05-24")
    assert "2026-05-24" in msg
    assert "• встреча 14:00" in msg
    assert "• купить хлеб" in msg


def test_format_plan_message_empty_returns_default():
    assert format_plan_message([], "2026-05-24") == EMPTY_PLAN_TEXT


SAMPLE_DAILY = """# Воскресенье

## 🗓 План на день

- утром бегать
- встреча 14:00

---

## Сон
"""

EMPTY_PLAN_DAILY = """# Воскресенье

## 🗓 План на день

-

## Сон
"""


@pytest.fixture
def fake_github():
    g = MagicMock()
    g.read = AsyncMock(return_value=FileContent(text=SAMPLE_DAILY, sha="x"))
    return g


@pytest.fixture
def fake_bot():
    b = MagicMock()
    b.send_message = AsyncMock()
    return b


async def test_daily_plan_sends_bullets_when_present(fake_github, fake_bot):
    sent = await daily_plan_ping(fake_github, fake_bot, telegram_user_id=1, tz="Europe/Moscow")
    assert sent is True
    fake_bot.send_message.assert_awaited_once()
    text = fake_bot.send_message.call_args.kwargs["text"]
    assert "• утром бегать" in text
    assert "• встреча 14:00" in text


async def test_daily_plan_sends_empty_message_when_placeholder_dash(fake_bot):
    g = MagicMock()
    g.read = AsyncMock(return_value=FileContent(text=EMPTY_PLAN_DAILY, sha="x"))
    await daily_plan_ping(g, fake_bot, telegram_user_id=1, tz="Europe/Moscow")
    text = fake_bot.send_message.call_args.kwargs["text"]
    assert text == EMPTY_PLAN_TEXT


async def test_daily_plan_sends_empty_message_when_section_missing(fake_bot):
    g = MagicMock()
    g.read = AsyncMock(return_value=FileContent(text="## Сон\n- a\n", sha="x"))
    await daily_plan_ping(g, fake_bot, telegram_user_id=1, tz="Europe/Moscow")
    text = fake_bot.send_message.call_args.kwargs["text"]
    assert text == EMPTY_PLAN_TEXT


async def test_daily_plan_sends_empty_message_when_file_missing(fake_bot):
    g = MagicMock()
    g.read = AsyncMock(return_value=None)
    await daily_plan_ping(g, fake_bot, telegram_user_id=1, tz="Europe/Moscow")
    text = fake_bot.send_message.call_args.kwargs["text"]
    assert text == EMPTY_PLAN_TEXT
