"""/start — intro message with command list."""

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

router = Router(name="start")


HELP_TEXT = (
    "👋 Привет! Я <b>Rutix</b> — ваш личный трекер психики и питания.\n\n"
    "📊 /track — настроение, тревога, сон, лекарства\n"
    "🍽 /eat — записать приём пищи (я разберу через Claude)\n"
    "📝 /note — заметка дня\n"
    "✅ /done — что сделано за день\n"
    "📆 /today — сводка за сегодня\n"
    "📅 /week — отчёт по дням недели\n"
    "💊 /meds — управление лекарствами\n"
    "🔄 /sync — записать вчера в Obsidian вручную\n\n"
    "🧠 <b>Слова:</b> просто пришлите слово — запишу в «Слова» с оценкой "
    "сложности (1 легко … 3 сложно). Можно сразу с цифрой: «паллиатив 3».\n\n"
    "<b>Расписание:</b>\n"
    "• 21:00 — напомню про /track, если ещё не делали\n"
    "• 03:00 — закрою вчерашний день в Obsidian\n"
    "• Понедельник 03:00 — закрою прошлую неделю + наведу порядок"
)


@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(HELP_TEXT)
