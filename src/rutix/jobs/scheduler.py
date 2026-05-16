"""APScheduler — recurring jobs:

- daily_3am (03:00): flush_day(yesterday) + update_habits(yesterday) + flush_week(today)
- evening_ping (21:00): nudge user to /track if they haven't yet
"""

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rutix.db.models import MoodEntry
from rutix.integrations.github import GitHubClient
from rutix.integrations.todoist import TodoistClient
from rutix.jobs.flush_day import flush_day
from rutix.jobs.flush_week import flush_week
from rutix.jobs.update_habits import UpdateHabitsResult, update_habits
from rutix.time_utils import subjective_today, week_id, yesterday_of

logger = logging.getLogger(__name__)

EVENING_PING_TEXT = "🌙 Напоминаю: вы ещё не делали /track за сегодня.\nЗаймёт минуту."

_MAX_MARKED_IN_MESSAGE = 15


def _fmt_sha(sha: str | None) -> str:
    return f" ({sha[:7]})" if sha else ""


def _fmt_flush_day_line(target_iso: str, result: "str | Exception | None") -> str:
    if isinstance(result, Exception):
        return f"⚠️ flush_day за {target_iso}: ошибка — {type(result).__name__}: {result}"
    if result is None:
        return f"⏭ flush_day за {target_iso}: пропущено (нет данных или уже записано)"
    return f"✅ flush_day за {target_iso}: записал{_fmt_sha(result)}"


def _habit_word(n: int) -> str:
    """Russian pluralization for 'привычка'."""
    if n % 10 == 1 and n % 100 != 11:
        return "привычку"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "привычки"
    return "привычек"


def _fmt_update_habits_lines(
    target_iso: str, result: "UpdateHabitsResult | Exception"
) -> list[str]:
    if isinstance(result, Exception):
        return [f"⚠️ update_habits за {target_iso}: ошибка — {type(result).__name__}: {result}"]
    if result.sha is None:
        return [f"⏭ update_habits за {target_iso}: нечего отмечать"]
    n = len(result.marked)
    head = f"✅ update_habits за {target_iso}: отметил {n} {_habit_word(n)}{_fmt_sha(result.sha)}"
    lines = [head]
    shown = result.marked[:_MAX_MARKED_IN_MESSAGE]
    for label in shown:
        lines.append(f"   • {label}")
    if len(result.marked) > _MAX_MARKED_IN_MESSAGE:
        lines.append(f"   … и ещё {len(result.marked) - _MAX_MARKED_IN_MESSAGE}")
    return lines


def _fmt_flush_week_line(today_is_monday: bool, wid: str, result: "str | Exception | None") -> str:
    if not today_is_monday:
        return "⏭ flush_week: не понедельник, пропущено"
    if isinstance(result, Exception):
        return f"⚠️ flush_week {wid}: ошибка — {type(result).__name__}: {result}"
    if result is None:
        return f"⏭ flush_week {wid}: уже записано"
    return f"✅ flush_week {wid}: weekly+nutrition записаны{_fmt_sha(result)}"


def build_3am_summary(
    today: date,
    target: date,
    flush_day_outcome: "str | Exception | None",
    update_habits_outcome: "UpdateHabitsResult | Exception",
    flush_week_outcome: "str | Exception | None",
) -> str:
    lines = [f"🌅 3am job: {today.isoformat()}"]
    lines.append(_fmt_flush_day_line(target.isoformat(), flush_day_outcome))
    lines.extend(_fmt_update_habits_lines(target.isoformat(), update_habits_outcome))
    is_monday = today.weekday() == 0
    wid = week_id(yesterday_of(today)) if is_monday else ""
    lines.append(_fmt_flush_week_line(is_monday, wid, flush_week_outcome))
    return "\n".join(lines)


async def send_evening_ping_if_needed(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
    telegram_user_id: int,
    tz: str,
) -> bool:
    """Send a /track reminder unless today's MoodEntry already has a mood value.

    Returns True if a message was sent, False if skipped.
    """
    today = subjective_today(datetime.now(ZoneInfo(tz)), tz)
    async with session_factory() as session:
        entry = await session.get(MoodEntry, today)
    if entry is not None and entry.mood is not None:
        logger.info("evening_ping skipped — mood already tracked for %s", today)
        return False

    await bot.send_message(chat_id=telegram_user_id, text=EVENING_PING_TEXT)
    logger.info("evening_ping sent for %s", today)
    return True


def make_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    github: GitHubClient,
    todoist: TodoistClient,
    bot: Bot,
    telegram_user_id: int,
    tz: str,
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(tz))

    async def daily_3am():
        today = subjective_today(datetime.now(ZoneInfo(tz)), tz)
        target = yesterday_of(today)
        logger.info("3am job running for target=%s today=%s", target, today)

        flush_day_outcome: str | Exception | None
        update_habits_outcome: UpdateHabitsResult | Exception
        flush_week_outcome: str | Exception | None

        async with session_factory() as session:
            try:
                flush_day_outcome = await flush_day(session, github, target)
                logger.info("flush_day result: %s", flush_day_outcome)
            except Exception as e:
                logger.exception("flush_day failed")
                flush_day_outcome = e

        try:
            update_habits_outcome = await update_habits(github, todoist, target)
            logger.info("update_habits result: %s", update_habits_outcome)
        except Exception as e:
            logger.exception("update_habits failed")
            update_habits_outcome = e

        async with session_factory() as session:
            try:
                flush_week_outcome = await flush_week(session, github, today)
                logger.info("flush_week result: %s", flush_week_outcome)
            except Exception as e:
                logger.exception("flush_week failed")
                flush_week_outcome = e

        summary = build_3am_summary(
            today=today,
            target=target,
            flush_day_outcome=flush_day_outcome,
            update_habits_outcome=update_habits_outcome,
            flush_week_outcome=flush_week_outcome,
        )
        try:
            await bot.send_message(chat_id=telegram_user_id, text=summary)
        except Exception:
            logger.exception("failed to send 3am summary")

    async def evening_ping():
        try:
            await send_evening_ping_if_needed(session_factory, bot, telegram_user_id, tz)
        except Exception:
            logger.exception("evening_ping failed")

    scheduler.add_job(
        daily_3am,
        trigger=CronTrigger(hour=3, minute=0, timezone=ZoneInfo(tz)),
        id="daily_3am",
        replace_existing=True,
    )
    scheduler.add_job(
        evening_ping,
        trigger=CronTrigger(hour=21, minute=0, timezone=ZoneInfo(tz)),
        id="evening_ping",
        replace_existing=True,
    )
    return scheduler
