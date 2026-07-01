"""/today — show today's state snapshots + report + meals (GitHub) summary."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rutix.db.models import MoodEntry, StateEntry
from rutix.integrations.github import GitHubClient
from rutix.markdown.daily import parse_meals
from rutix.settings import Settings
from rutix.time_utils import subjective_today

logger = logging.getLogger(__name__)

router = Router(name="today")


def _signed(v: int | None) -> str:
    if v is None:
        return "—"
    return f"+{v}" if v > 0 else str(v)


@router.message(Command("today"))
async def cmd_today(
    message: Message,
    settings: Settings,
    github: GitHubClient,
    session_factory: async_sessionmaker[AsyncSession],
):
    day = subjective_today(datetime.now(ZoneInfo(settings.tz)), settings.tz)

    async with session_factory() as session:
        report = await session.get(MoodEntry, day)
        states = (
            await session.scalars(
                select(StateEntry).where(StateEntry.day == day).order_by(StateEntry.ts)
            )
        ).all()

    file = await github.read(f"daily/{day.isoformat()}.md")
    meals = parse_meals(file.text) if file else []

    lines = [f"📆 {day.isoformat()}\n"]

    if not states:
        lines.append("🧭 Состояние сегодня не отмечено — нажмите /state")
    else:
        for s in states:
            lines.append(
                f"🧭 {s.ts.strftime('%H:%M')} — настроение {_signed(s.mood)} · "
                f"энергия {_signed(s.energy)} · аппетит {_signed(s.appetite)}"
            )

    if report is None or report.sleep_hours is None:
        lines.append("📋 Отчёт за сегодня ещё не сделан — нажмите /report")
    else:
        lines.append(f"📋 Сон {report.sleep_hours}ч")

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
        lines.append("\n🍽 Сегодня ничего не записано — попробуйте /eat")

    await message.answer("\n".join(lines))
