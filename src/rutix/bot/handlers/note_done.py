"""/note and /done — append a bullet to today's daily Заметки / Что сделано."""

import logging
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from rutix.integrations.github import GitHubClient
from rutix.markdown.daily import append_done, append_note
from rutix.settings import Settings
from rutix.time_utils import subjective_today

logger = logging.getLogger(__name__)

router = Router(name="note_done")


_USAGE_EXAMPLES = {
    "note": "/note важная мысль или наблюдение",
    "done": "/done закрыл задачу X",
}


async def _append_to_daily(
    message: Message,
    settings: Settings,
    github: GitHubClient,
    cmd_name: str,
    section_label: str,
    appender: Callable[[str, str], str],
):
    raw = (message.text or "").split(maxsplit=1)
    if len(raw) < 2 or not raw[1].strip():
        await message.answer(f"Пример использования:\n{_USAGE_EXAMPLES[cmd_name]}")
        return

    text = raw[1].strip()
    day = subjective_today(datetime.now(ZoneInfo(settings.tz)), settings.tz)
    path = f"daily/{day.isoformat()}.md"

    file = await github.read(path)
    if file is None:
        await message.answer(f"⚠️ Файл {path} не найден.\nПроверьте что он создан в Obsidian.")
        return

    new_text = appender(file.text, text)
    if new_text == file.text:
        await message.answer("⏭ Без изменений (видимо, такая запись уже есть).")
        return

    sha = await github.write(
        path,
        new_text,
        f"{cmd_name}({day.isoformat()}): {text[:60]}",
        sha=file.sha,
    )
    await message.answer(f"✅ Добавил в «{section_label}». Коммит: {sha[:7]}")


@router.message(Command("note"))
async def cmd_note(message: Message, settings: Settings, github: GitHubClient):
    await _append_to_daily(message, settings, github, "note", "Заметки", append_note)


@router.message(Command("done"))
async def cmd_done(message: Message, settings: Settings, github: GitHubClient):
    await _append_to_daily(message, settings, github, "done", "Что сделано", append_done)
