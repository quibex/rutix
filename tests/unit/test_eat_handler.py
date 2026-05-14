from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from rutix.bot.handlers.eat import _slot_for_time, cb_ok, cmd_eat
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
def fake_state():
    """Mock FSMContext that records updates and clears."""
    s = MagicMock()
    s.update_data = AsyncMock()
    s.set_state = AsyncMock()
    s.get_data = AsyncMock(return_value={})
    s.clear = AsyncMock()
    return s


@pytest.fixture
def fake_message():
    m = MagicMock()
    m.text = "/eat шаурма"
    m.reply = AsyncMock()

    # answer() returns a "thinking" message that we then edit
    thinking_msg = MagicMock()
    thinking_msg.edit_text = AsyncMock()
    m.answer = AsyncMock(return_value=thinking_msg)
    m._thinking_msg = thinking_msg  # so tests can assert on the edit
    return m


async def test_cmd_eat_shows_preview_with_buttons_does_not_write(
    fake_message, fake_state, fake_github, fake_claude, fake_settings
):
    # 1st github.read = daily file existence check; 2nd = reference fetch
    fake_github.read.side_effect = [
        FileContent(text=DAILY, sha="dsha"),
        FileContent(text="ref content", sha="rsha"),
    ]
    fake_claude.parse_eat.return_value = [MealItem("", "Шаурма", 450, 22.0, 18.0, 45.0)]
    fake_message.text = "/eat шаурма"

    await cmd_eat(
        fake_message,
        state=fake_state,
        settings=fake_settings,
        github=fake_github,
        claude=fake_claude,
    )

    # No write yet — preview shown, awaiting confirmation
    fake_github.write.assert_not_called()

    # Preview was shown via edit_text with confirm keyboard
    fake_message._thinking_msg.edit_text.assert_awaited()
    edit_kwargs = fake_message._thinking_msg.edit_text.call_args.kwargs
    edit_args = fake_message._thinking_msg.edit_text.call_args.args
    preview_text = edit_args[0]
    assert "Шаурма" in preview_text
    assert "450" in preview_text
    assert "reply_markup" in edit_kwargs
    # State has the items saved (current_items model — no growing history)
    fake_state.update_data.assert_awaited()
    saved = fake_state.update_data.call_args.kwargs
    assert any(it["name"] == "Шаурма" for it in saved["items"])


async def test_cb_ok_writes_to_daily(fake_state, fake_github, fake_settings):
    fake_state.get_data = AsyncMock(
        return_value={
            "items": [{"name": "Шаурма", "kcal": 450, "protein": 22.0, "fat": 18.0, "carbs": 45.0}],
            "slot": "Обед",
            "day": "2026-05-15",
            "food_text": "шаурма",
        }
    )
    fake_github.read.return_value = FileContent(text=DAILY, sha="dsha")

    cb = MagicMock()
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()

    await cb_ok(cb, state=fake_state, settings=fake_settings, github=fake_github)

    # Wrote to daily file
    fake_github.write.assert_awaited_once()
    written_text = fake_github.write.call_args.args[1]
    assert "Шаурма" in written_text
    assert "450" in written_text

    # Confirmation message
    reply = cb.message.edit_text.call_args.args[0]
    assert "✅" in reply or "Записал" in reply


async def test_cmd_eat_replies_with_error_if_claude_fails(
    fake_message, fake_state, fake_github, fake_claude, fake_settings
):
    fake_github.read.side_effect = [
        FileContent(text=DAILY, sha="dsha"),
        FileContent(text="ref", sha="x"),
    ]
    fake_claude.parse_eat.side_effect = ValueError("bad json")

    fake_message.text = "/eat что-то непонятное"

    await cmd_eat(
        fake_message,
        state=fake_state,
        settings=fake_settings,
        github=fake_github,
        claude=fake_claude,
    )

    fake_github.write.assert_not_called()
    reply_text = fake_message._thinking_msg.edit_text.call_args.args[0]
    assert "не получилось" in reply_text.lower() or "⚠️" in reply_text


async def test_cmd_eat_no_args_opens_empty_session(
    fake_message, fake_state, fake_github, fake_claude, fake_settings
):
    """`/eat` alone opens a session and waits for input — no Claude call yet."""
    fake_message.text = "/eat"

    await cmd_eat(
        fake_message,
        state=fake_state,
        settings=fake_settings,
        github=fake_github,
        claude=fake_claude,
    )

    fake_github.write.assert_not_called()
    fake_claude.parse_eat.assert_not_called()
    # State was set to session (set_state called at least once)
    fake_state.set_state.assert_awaited()
    # Preview-empty message was sent
    reply_text = fake_message.answer.call_args.args[0]
    assert "Сессия" in reply_text or "сессия" in reply_text.lower()
