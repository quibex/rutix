from unittest.mock import AsyncMock, MagicMock

import pytest

from rutix.bot.handlers.note_done import (
    NoteDoneStates,
    cb_cancel,
    cmd_done,
    cmd_note,
    msg_await_text,
)
from rutix.integrations.github import FileContent

DAILY = """# x

## Привычки
- [ ]

## Питание

| Приём | Что | Ккал | Б | Ж | У |
|-------|-----|------|---|---|---|
|  |  |  |  |  |  |
| **Итого** |  |  |  |  |  |

---

## Что сделано

- existing done

## Заметки

- existing note
"""


@pytest.fixture
def fake_settings():
    s = MagicMock()
    s.tz = "Europe/Moscow"
    return s


@pytest.fixture
def fake_github():
    g = MagicMock()
    g.read = AsyncMock(return_value=FileContent(text=DAILY, sha="x"))
    g.write = AsyncMock(return_value="newsha")
    return g


@pytest.fixture
def fake_message():
    m = MagicMock()
    m.answer = AsyncMock()
    return m


@pytest.fixture
def fake_state():
    s = MagicMock()
    s.set_state = AsyncMock()
    s.update_data = AsyncMock()
    s.get_data = AsyncMock(return_value={})
    s.clear = AsyncMock()
    return s


async def test_cmd_note_appends_to_notes(fake_message, fake_state, fake_settings, fake_github):
    fake_message.text = "/note важная мысль"
    await cmd_note(fake_message, state=fake_state, settings=fake_settings, github=fake_github)

    written = fake_github.write.call_args.args[1]
    notes = written.split("## Заметки", 1)[1]
    assert "- existing note" in notes
    assert "- важная мысль" in notes
    fake_message.answer.assert_awaited()
    fake_state.set_state.assert_not_awaited()
    fake_state.clear.assert_awaited()


async def test_cmd_done_appends_to_done(fake_message, fake_state, fake_settings, fake_github):
    fake_message.text = "/done закрыл задачу"
    await cmd_done(fake_message, state=fake_state, settings=fake_settings, github=fake_github)

    written = fake_github.write.call_args.args[1]
    done = written.split("## Что сделано", 1)[1].split("## Заметки", 1)[0]
    assert "- existing done" in done
    assert "- закрыл задачу" in done
    fake_state.set_state.assert_not_awaited()


async def test_cmd_note_no_args_enters_state(fake_message, fake_state, fake_settings, fake_github):
    fake_message.text = "/note"
    await cmd_note(fake_message, state=fake_state, settings=fake_settings, github=fake_github)

    fake_github.write.assert_not_called()
    fake_state.set_state.assert_awaited_once_with(NoteDoneStates.await_text)
    fake_state.update_data.assert_awaited_once_with(cmd="note")
    answer_args = fake_message.answer.call_args
    assert "заметку" in answer_args.args[0].lower()
    assert answer_args.kwargs.get("reply_markup") is not None


async def test_cmd_done_no_args_enters_state(fake_message, fake_state, fake_settings, fake_github):
    fake_message.text = "/done"
    await cmd_done(fake_message, state=fake_state, settings=fake_settings, github=fake_github)

    fake_github.write.assert_not_called()
    fake_state.set_state.assert_awaited_once_with(NoteDoneStates.await_text)
    fake_state.update_data.assert_awaited_once_with(cmd="done")
    answer_args = fake_message.answer.call_args
    assert "сделали" in answer_args.args[0].lower()
    assert answer_args.kwargs.get("reply_markup") is not None


async def test_cmd_note_scaffolds_when_daily_missing(
    fake_message, fake_state, fake_settings, fake_github
):
    """Missing daily file is created from a template and the note is written."""
    fake_github.read = AsyncMock(return_value=None)
    fake_message.text = "/note hi"
    await cmd_note(fake_message, state=fake_state, settings=fake_settings, github=fake_github)
    fake_github.write.assert_awaited_once()
    # sha=None → create (not update) on the remote.
    assert fake_github.write.call_args.kwargs.get("sha") is None
    written = fake_github.write.call_args.args[1]
    notes = written.split("## Заметки", 1)[1]
    assert "- hi" in notes


async def test_msg_await_text_writes_done(fake_message, fake_state, fake_settings, fake_github):
    fake_state.get_data = AsyncMock(return_value={"cmd": "done"})
    fake_message.text = "урок акм 3ч"

    await msg_await_text(fake_message, state=fake_state, settings=fake_settings, github=fake_github)

    fake_state.clear.assert_awaited()
    written = fake_github.write.call_args.args[1]
    done = written.split("## Что сделано", 1)[1].split("## Заметки", 1)[0]
    assert "- урок акм 3ч" in done


async def test_msg_await_text_writes_note(fake_message, fake_state, fake_settings, fake_github):
    fake_state.get_data = AsyncMock(return_value={"cmd": "note"})
    fake_message.text = "интересная мысль"

    await msg_await_text(fake_message, state=fake_state, settings=fake_settings, github=fake_github)

    fake_state.clear.assert_awaited()
    written = fake_github.write.call_args.args[1]
    notes = written.split("## Заметки", 1)[1]
    assert "- интересная мысль" in notes


async def test_msg_await_text_empty_does_nothing(
    fake_message, fake_state, fake_settings, fake_github
):
    fake_state.get_data = AsyncMock(return_value={"cmd": "done"})
    fake_message.text = "   "

    await msg_await_text(fake_message, state=fake_state, settings=fake_settings, github=fake_github)

    fake_github.write.assert_not_called()
    fake_state.clear.assert_awaited()


async def test_cb_cancel_clears_state():
    state = MagicMock()
    state.clear = AsyncMock()
    cb = MagicMock()
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()

    await cb_cancel(cb, state=state)

    state.clear.assert_awaited()
    cb.message.edit_text.assert_awaited_with("Отменено.")
    cb.answer.assert_awaited()
