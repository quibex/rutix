"""APScheduler — recurring jobs:

- daily_3am (03:00): flush_day(yesterday) + update_habits(yesterday) + flush_week(today)
- evening_ping (21:00): nudge user to /track if they haven't yet
"""

import logging
from datetime import datetime
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
from rutix.jobs.update_habits import update_habits
from rutix.time_utils import subjective_today, yesterday_of

logger = logging.getLogger(__name__)

EVENING_PING_TEXT = "🌙 Не забыл /track за сегодня?"


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

        async with session_factory() as session:
            try:
                sha = await flush_day(session, github, target)
                logger.info("flush_day result: %s", sha)
            except Exception:
                logger.exception("flush_day failed")

        try:
            sha = await update_habits(github, todoist, target)
            logger.info("update_habits result: %s", sha)
        except Exception:
            logger.exception("update_habits failed")

        async with session_factory() as session:
            try:
                sha = await flush_week(session, github, today)
                logger.info("flush_week result: %s", sha)
            except Exception:
                logger.exception("flush_week failed")

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
