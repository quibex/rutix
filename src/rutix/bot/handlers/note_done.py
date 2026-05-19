"""/note and /done — append a bullet to today's daily Заметки / Что сделано.

Two ways to record:
- `/done <text>` (text on the same message) — writes immediately.
- `/done` alone — bot asks «Напишите…»; the next text message is recorded.
  Tap «Отмена» to abort without writing.
"""

import logging
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from rutix.integrations.github import GitHubClient
from rutix.markdown.daily import append_done, append_note
from rutix.settings import Settings
from rutix.time_utils import subjective_today

logger = logging.getLogger(__name__)

router = Router(name="note_done")


class NoteDoneStates(StatesGroup):
    await_text = State()


_APPENDERS: dict[str, tuple[Callable[[str, str], str], str, str]] = {
    "note": (append_note, "Заметки", "📝 Напишите заметку следующим сообщением."),
    "done": (append_done, "Что сделано", "✅ Напишите, что вы сделали, следующим сообщением."),
}


def _cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="note_done:cancel")]]
    )


async def _write_entry(
    message: Message,
    settings: Settings,
    github: GitHubClient,
    cmd_name: str,
    text: str,
):
    appender, section_label, _ = _APPENDERS[cmd_name]
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


async def _handle_command(
    message: Message,
    state: FSMContext,
    settings: Settings,
    github: GitHubClient,
    cmd_name: str,
):
    raw = (message.text or "").split(maxsplit=1)
    if len(raw) >= 2 and raw[1].strip():
        await state.clear()
        await _write_entry(message, settings, github, cmd_name, raw[1].strip())
        return

    _, _, prompt = _APPENDERS[cmd_name]
    await state.set_state(NoteDoneStates.await_text)
    await state.update_data(cmd=cmd_name)
    await message.answer(prompt, reply_markup=_cancel_kb())


@router.message(Command("note"))
async def cmd_note(
    message: Message,
    state: FSMContext,
    settings: Settings,
    github: GitHubClient,
):
    await _handle_command(message, state, settings, github, "note")


@router.message(Command("done"))
async def cmd_done(
    message: Message,
    state: FSMContext,
    settings: Settings,
    github: GitHubClient,
):
    await _handle_command(message, state, settings, github, "done")


@router.callback_query(F.data == "note_done:cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Отменено.")
    await cb.answer()


@router.message(NoteDoneStates.await_text, F.text)
async def msg_await_text(
    message: Message,
    state: FSMContext,
    settings: Settings,
    github: GitHubClient,
):
    data = await state.get_data()
    cmd_name = data.get("cmd")
    await state.clear()
    if cmd_name not in _APPENDERS:
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пустой текст. Отменено.")
        return
    await _write_entry(message, settings, github, cmd_name, text)
