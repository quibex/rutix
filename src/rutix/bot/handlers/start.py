"""/start — intro message with command list."""

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

router = Router(name="start")


HELP_TEXT = (
    "👋 <b>Rutix</b> — личный трекер психики и питания.\n\n"
    "📊 /track — настроение, тревога, сон, лекарства\n"
    "🍽 /eat &lt;что&gt; — приём пищи (Claude парсит → daily файл)\n"
    "📝 /note &lt;текст&gt; — заметка дня\n"
    "✅ /done &lt;текст&gt; — что сделано\n"
    "📆 /today — сводка за сегодня\n"
    "📅 /week — отчёт по дням недели\n"
    "💊 /meds — лекарства (добавить / архив / доза)\n"
    "🔄 /sync — форс flush в git\n\n"
    "<b>Авто:</b>\n"
    "• 21:00 — пинг если не было /track\n"
    "• 03:00 — закрытие вчерашнего дня в Obsidian\n"
    "• Пн 03:00 — закрытие недели + чистка daily/"
)


@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(HELP_TEXT)
