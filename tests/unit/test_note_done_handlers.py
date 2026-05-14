from unittest.mock import AsyncMock, MagicMock

import pytest

from rutix.bot.handlers.note_done import cmd_done, cmd_note
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


async def test_cmd_note_appends_to_notes(fake_message, fake_settings, fake_github):
    fake_message.text = "/note важная мысль"
    await cmd_note(fake_message, settings=fake_settings, github=fake_github)

    written = fake_github.write.call_args.args[1]
    notes = written.split("## Заметки", 1)[1]
    assert "- existing note" in notes
    assert "- важная мысль" in notes
    fake_message.answer.assert_awaited()


async def test_cmd_done_appends_to_done(fake_message, fake_settings, fake_github):
    fake_message.text = "/done закрыл задачу"
    await cmd_done(fake_message, settings=fake_settings, github=fake_github)

    written = fake_github.write.call_args.args[1]
    done = written.split("## Что сделано", 1)[1].split("## Заметки", 1)[0]
    assert "- existing done" in done
    assert "- закрыл задачу" in done


async def test_cmd_note_no_args_shows_usage(fake_message, fake_settings, fake_github):
    fake_message.text = "/note"
    await cmd_note(fake_message, settings=fake_settings, github=fake_github)
    fake_github.write.assert_not_called()
    assert "/note" in fake_message.answer.call_args.args[0]


async def test_cmd_done_no_args_shows_usage(fake_message, fake_settings, fake_github):
    fake_message.text = "/done"
    await cmd_done(fake_message, settings=fake_settings, github=fake_github)
    fake_github.write.assert_not_called()


async def test_cmd_note_when_daily_missing(fake_message, fake_settings, fake_github):
    fake_github.read = AsyncMock(return_value=None)
    fake_message.text = "/note hi"
    await cmd_note(fake_message, settings=fake_settings, github=fake_github)
    fake_github.write.assert_not_called()
    assert "не найден" in fake_message.answer.call_args.args[0].lower()
