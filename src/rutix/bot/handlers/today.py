"""/today — show today's mood (SQLite) + meals (GitHub) summary."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rutix.db.models import MoodEntry
from rutix.integrations.github import GitHubClient
from rutix.markdown.daily import parse_meals
from rutix.settings import Settings
from rutix.time_utils import subjective_today

logger = logging.getLogger(__name__)

router = Router(name="today")


@router.message(Command("today"))
async def cmd_today(
    message: Message,
    settings: Settings,
    github: GitHubClient,
    session_factory: async_sessionmaker[AsyncSession],
):
    day = subjective_today(datetime.now(ZoneInfo(settings.tz)), settings.tz)

    async with session_factory() as session:
        mood = await session.get(MoodEntry, day)

    file = await github.read(f"daily/{day.isoformat()}.md")
    meals = parse_meals(file.text) if file else []

    lines = [f"📆 {day.isoformat()}\n"]

    if mood is None:
        lines.append("📊 Трек ещё не делал — /track")
    else:
        if mood.mood is None:
            mood_str = "—"
        elif mood.mood > 0:
            mood_str = f"+{mood.mood}"
        else:
            mood_str = str(mood.mood)
        lines.append(
            f"📊 Настр. {mood_str} · тревога {mood.anxiety} · "
            f"раздр. {mood.irritability} · сон {mood.sleep_hours}ч"
        )

    if meals:
        kcal = sum(m.kcal for m in meals)
        p = sum(m.protein for m in meals)
        f = sum(m.fat for m in meals)
        c = sum(m.carbs for m in meals)
        lines.append(
            f"\n🍽 Итого за день: {kcal} ккал · Б{p:g} Ж{f:g} У{c:g}\n"
            + "\n".join(f"• {m.name} — {m.kcal}" for m in meals)
        )
    else:
        lines.append("\n🍽 Ничего не ел сегодня — /eat <что>")

    await message.answer("\n".join(lines))
