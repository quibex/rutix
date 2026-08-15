"""/start — intro message with command list."""

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

router = Router(name="start")


HELP_TEXT = (
    "👋 Привет! Я <b>Rutix</b> — ваш личный трекер психики и питания.\n\n"
    "🧭 /state — состояние: настроение, энергия, аппетит (можно несколько раз в день)\n"
    "📋 /report — дневной отчёт: сон, лекарства, VPN, English\n"
    "🍽 /eat — записать приём пищи (я разберу через Claude)\n"
    "📝 /note — заметка дня\n"
    "✅ /done — что сделано за день\n"
    "📆 /today — сводка за сегодня\n"
    "📅 /week — отчёт по дням недели\n"
    "💊 /meds — управление лекарствами\n"
    "⏰ /schedule — расписание: включить/выключить джобу или сдвинуть её время\n"
    "🔄 /sync — записать вчера в Obsidian вручную\n\n"
    "📝 <b>Заметки:</b> просто пришлите любой текст — добавлю в «Заметки» дня.\n\n"
    "<b>Расписание по умолчанию</b> (меняется в /schedule):\n"
    "• 03:00 — закрою вчерашний день в Obsidian\n"
    "• 06:00 и 08:00 — повторю привычки, если ночью Todoist молчал\n"
    "• 09:00 — пришлю план на день"
)


@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(HELP_TEXT)
