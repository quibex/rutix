"""/eat <text> — Claude parses, bot writes to today's daily Питание."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from rutix.integrations.claude import ClaudeClient
from rutix.integrations.github import GitHubClient
from rutix.markdown.daily import append_meal
from rutix.settings import Settings
from rutix.time_utils import subjective_today

logger = logging.getLogger(__name__)

router = Router(name="eat")

REFERENCE_PATH = "nutrition/reference.md"


def _slot_for_time(now: datetime) -> str:
    h = now.hour
    if 8 <= h <= 11:
        return "Завтрак"
    if 12 <= h <= 16:
        return "Обед"
    if 17 <= h <= 21:
        return "Ужин"
    return "Перекус"


def _format_kbju(kcal: int, p: float, f: float, c: float) -> str:
    return f"{kcal} ккал · Б{p:g} Ж{f:g} У{c:g}"


@router.message(Command("eat"))
async def cmd_eat(
    message: Message,
    settings: Settings,
    github: GitHubClient,
    claude: ClaudeClient,
):
    raw = (message.text or "").split(maxsplit=1)
    if len(raw) < 2 or not raw[1].strip():
        await message.answer("Использование: /eat <что съел>\nПример: /eat шаурма + кола")
        return

    food_text = raw[1].strip()
    now = datetime.now(ZoneInfo(settings.tz))
    day = subjective_today(now, settings.tz)
    slot = _slot_for_time(now)

    # Fetch reference + daily file
    reference = await github.read(REFERENCE_PATH)
    reference_text = reference.text if reference else ""

    daily_path = f"daily/{day.isoformat()}.md"
    daily_file = await github.read(daily_path)
    if daily_file is None:
        await message.answer(f"❌ Нет файла {daily_path}. Создай его сначала в Obsidian.")
        return

    # Parse via Claude
    try:
        items = await claude.parse_eat(food_text, reference_md=reference_text)
    except ValueError as e:
        logger.exception("Claude parse failed")
        await message.answer(f"❌ Не смог распарсить: {e}. Попробуй переписать.")
        return

    if not items:
        await message.answer("⚠️ Claude вернул пустой список. Попробуй уточнить.")
        return

    # Apply slot to all items + append to daily
    new_text = daily_file.text
    for item in items:
        item.slot = slot
        new_text = append_meal(new_text, item)

    sha = await github.write(
        daily_path,
        new_text,
        f"eat({day.isoformat()}): {food_text[:60]}",
        sha=daily_file.sha,
    )

    # Build reply
    added_lines = [
        f"• {it.name} — {_format_kbju(it.kcal, it.protein, it.fat, it.carbs)}" for it in items
    ]
    total_kcal = sum(it.kcal for it in items)
    total_p = sum(it.protein for it in items)
    total_f = sum(it.fat for it in items)
    total_c = sum(it.carbs for it in items)
    reply = (
        f"✅ Добавил в {slot}:\n"
        + "\n".join(added_lines)
        + f"\n\nИтого добавлено: {_format_kbju(total_kcal, total_p, total_f, total_c)}\n"
        f"Файл: {sha[:7]}"
    )
    await message.answer(reply)
