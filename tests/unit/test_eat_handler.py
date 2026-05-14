from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from rutix.bot.handlers.eat import _slot_for_time, cmd_eat
from rutix.integrations.github import FileContent
from rutix.markdown.daily import MealItem


MSK = ZoneInfo("Europe/Moscow")
DAILY = """# x

## Привычки
- [ ]
---
## Питание

| Приём | Что | Ккал | Б | Ж | У |
|-------|-----|------|---|---|---|
|  |  |  |  |  |  |
| **Итого** |  |  |  |  |  |

---

## Что сделано
-
## Заметки
-
"""


def test_slot_for_time_breakfast():
    assert _slot_for_time(datetime(2026, 5, 14, 9, 0, tzinfo=MSK)) == "Завтрак"


def test_slot_for_time_lunch():
    assert _slot_for_time(datetime(2026, 5, 14, 13, 0, tzinfo=MSK)) == "Обед"


def test_slot_for_time_dinner():
    assert _slot_for_time(datetime(2026, 5, 14, 19, 0, tzinfo=MSK)) == "Ужин"


def test_slot_for_time_snack():
    assert _slot_for_time(datetime(2026, 5, 14, 23, 30, tzinfo=MSK)) == "Перекус"


@pytest.fixture
def fake_settings():
    s = MagicMock()
    s.tz = "Europe/Moscow"
    return s


@pytest.fixture
def fake_github():
    g = MagicMock()
    g.read = AsyncMock()
    g.write = AsyncMock(return_value="newsha")
    return g


@pytest.fixture
def fake_claude():
    c = MagicMock()
    c.parse_eat = AsyncMock()
    return c


@pytest.fixture
def fake_message():
    m = MagicMock()
    m.text = "/eat шаурма"
    m.reply = AsyncMock()
    m.answer = AsyncMock()
    return m


async def test_cmd_eat_writes_to_daily_and_replies(
    fake_message, fake_github, fake_claude, fake_settings, monkeypatch
):
    # Reference is fetched on first call
    fake_github.read.side_effect = [
        FileContent(text="ref content", sha="rsha"),  # nutrition/reference.md
        FileContent(text=DAILY, sha="dsha"),  # daily/<today>.md
    ]
    fake_claude.parse_eat.return_value = [MealItem("", "Шаурма", 450, 22.0, 18.0, 45.0)]

    fake_message.text = "/eat шаурма"

    await cmd_eat(fake_message, settings=fake_settings, github=fake_github, claude=fake_claude)

    # GitHub write called with updated Питание containing the row
    write_args = fake_github.write.call_args
    written_text = write_args.args[1]
    assert "Шаурма" in written_text
    assert "450" in written_text

    # Reply mentions added items + total
    fake_message.answer.assert_awaited()
    reply_text = fake_message.answer.call_args.args[0]
    assert "Шаурма" in reply_text
    assert "450" in reply_text


async def test_cmd_eat_replies_with_error_if_claude_fails(
    fake_message, fake_github, fake_claude, fake_settings
):
    fake_github.read.return_value = FileContent(text="ref", sha="x")
    fake_claude.parse_eat.side_effect = ValueError("bad json")

    fake_message.text = "/eat что-то непонятное"

    await cmd_eat(fake_message, settings=fake_settings, github=fake_github, claude=fake_claude)

    fake_github.write.assert_not_called()
    reply_text = fake_message.answer.call_args.args[0]
    assert "не получилось" in reply_text.lower() or "⚠️" in reply_text


async def test_cmd_eat_replies_with_help_when_no_args(
    fake_message, fake_github, fake_claude, fake_settings
):
    fake_message.text = "/eat"

    await cmd_eat(fake_message, settings=fake_settings, github=fake_github, claude=fake_claude)

    fake_github.write.assert_not_called()
    fake_claude.parse_eat.assert_not_called()
    reply_text = fake_message.answer.call_args.args[0]
    assert "/eat" in reply_text
