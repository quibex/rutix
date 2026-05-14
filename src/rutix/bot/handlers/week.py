"""/week — 7 buttons (current ISO week) → day summary on tap."""
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rutix.db.models import MoodEntry
from rutix.integrations.github import GitHubClient
from rutix.markdown.daily import parse_meals
from rutix.settings import Settings
from rutix.time_utils import days_of_week, subjective_today

logger = logging.getLogger(__name__)

router = Router(name="week")

DAY_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def _build_keyboard(days: list[date]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text=f"{DAY_LABELS[d.weekday()]} {d.day}",
                callback_data=f"week_day:{d.isoformat()}",
            )
            for d in days
        ]]
    )


@router.message(Command("week"))
async def cmd_week(message: Message, settings: Settings):
    today = subjective_today(datetime.now(ZoneInfo(settings.tz)), settings.tz)
    week = days_of_week(today)
    await message.answer(
        f"📅 Неделя {week[0].isoformat()} — {week[-1].isoformat()}",
        reply_markup=_build_keyboard(week),
    )


@router.callback_query(F.data.startswith("week_day:"))
async def cb_week_day(
    cb: CallbackQuery,
    settings: Settings,
    github: GitHubClient,
    session_factory: async_sessionmaker[AsyncSession],
):
    day = date.fromisoformat(cb.data.split(":", 1)[1])

    async with session_factory() as session:
        mood = await session.get(MoodEntry, day)

    file = await github.read(f"daily/{day.isoformat()}.md")
    meals = parse_meals(file.text) if file else []

    lines = [f"📆 {day.isoformat()}\n"]

    if mood is None:
        lines.append("📊 Трек не сделан")
    else:
        mood_str = f"+{mood.mood}" if mood.mood and mood.mood > 0 else str(mood.mood) if mood.mood is not None else "—"
        lines.append(
            f"📊 {mood_str} · трев {mood.anxiety} · разд {mood.irritability} · сон {mood.sleep_hours}ч"
        )

    if meals:
        kcal = sum(m.kcal for m in meals)
        lines.append(f"\n🍽 {kcal} ккал — {len(meals)} приёмов")
    else:
        lines.append("\n🍽 Пусто")

    await cb.message.edit_text("\n".join(lines))
    await cb.answer()
