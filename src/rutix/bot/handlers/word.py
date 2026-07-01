"""Запись забытых слов с оценкой сложности (1 легко … 3 сложно).

Личный словарик «слов, которые трудно вспомнить». Триггер — обычный текст,
без команды:

- `паллиатив` — бот предложит кнопки 1 / 2 / 3 (или можно ввести цифру).
- `паллиатив 3` — запишет сразу, последняя цифра 1–3 = сложность.

Слово уходит буллетом в секцию `## Слова` сегодняшнего daily-файла. Этот
роутер подключается ПОСЛЕДНИМ и ловит текст только когда нет активного
FSM-состояния (StateFilter(None)) — чтобы не перехватывать шаги /state, /report и /eat.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from rutix.daily_io import daily_path, read_or_init_daily
from rutix.integrations.github import GitHubClient
from rutix.markdown.daily import append_word
from rutix.settings import Settings
from rutix.time_utils import subjective_today

logger = logging.getLogger(__name__)

router = Router(name="word")

_DIFFICULTIES = {"1", "2", "3"}


class WordStates(StatesGroup):
    await_difficulty = State()


def _difficulty_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1 · легко", callback_data="word:1"),
                InlineKeyboardButton(text="2 · средне", callback_data="word:2"),
                InlineKeyboardButton(text="3 · сложно", callback_data="word:3"),
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="word:cancel")],
        ]
    )


def _split_word_difficulty(text: str) -> tuple[str, int | None]:
    """Split "слово 3" → ("слово", 3); "слово" → ("слово", None).

    Only a trailing 1/2/3 token counts as difficulty, so "слово 5" or "слово"
    keep the whole text as the word and leave difficulty unset.
    """
    parts = text.split()
    if len(parts) >= 2 and parts[-1] in _DIFFICULTIES:
        return " ".join(parts[:-1]).strip(), int(parts[-1])
    return text.strip(), None


async def _write_word(
    message: Message,
    settings: Settings,
    github: GitHubClient,
    word: str,
    difficulty: int,
):
    day = subjective_today(datetime.now(ZoneInfo(settings.tz)), settings.tz)
    path = daily_path(day)

    file = await read_or_init_daily(github, day)

    new_text = append_word(file.text, word, difficulty)
    sha = await github.write(
        path,
        new_text,
        f"word({day.isoformat()}): {word} ({difficulty})",
        sha=file.sha,
    )
    await message.answer(f"✅ Записал «{word}» (сложность {difficulty}). Коммит: {sha[:7]}")


@router.message(StateFilter(None), F.text)
async def msg_word(
    message: Message,
    state: FSMContext,
    settings: Settings,
    github: GitHubClient,
):
    text = (message.text or "").strip()
    # Commands and empty text aren't words — let other routers / nothing handle them.
    if not text or text.startswith("/"):
        return

    word, difficulty = _split_word_difficulty(text)
    if not word:
        return

    if difficulty is not None:
        await _write_word(message, settings, github, word, difficulty)
        return

    await state.set_state(WordStates.await_difficulty)
    await state.update_data(word=word)
    await message.answer(
        f"Насколько сложно было вспомнить «{word}»?\n1 — легко, 3 — еле вспомнил.",
        reply_markup=_difficulty_kb(),
    )


@router.callback_query(WordStates.await_difficulty, F.data.startswith("word:"))
async def cb_difficulty(
    cb: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    github: GitHubClient,
):
    payload = cb.data.split(":", 1)[1]
    if payload == "cancel":
        await state.clear()
        await cb.message.edit_text("Отменено.")
        await cb.answer()
        return

    data = await state.get_data()
    word = data.get("word")
    await state.clear()
    await cb.message.edit_reply_markup(reply_markup=None)
    if word:
        await _write_word(cb.message, settings, github, word, int(payload))
    await cb.answer()


@router.message(WordStates.await_difficulty, F.text)
async def msg_difficulty(
    message: Message,
    state: FSMContext,
    settings: Settings,
    github: GitHubClient,
):
    text = (message.text or "").strip()
    if text not in _DIFFICULTIES:
        await message.answer("⚠️ Напишите 1, 2 или 3 (или нажмите кнопку).")
        return

    data = await state.get_data()
    word = data.get("word")
    await state.clear()
    if not word:
        return
    await _write_word(message, settings, github, word, int(text))
