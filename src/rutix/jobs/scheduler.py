"""APScheduler — daily 03:00 jobs:
- flush_day(yesterday)        — Phase 1
- update_habits(yesterday)    — Phase 2
- flush_week(today)           — Phase 2 (Monday-only check inside)
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rutix.integrations.github import GitHubClient
from rutix.integrations.todoist import TodoistClient
from rutix.jobs.flush_day import flush_day
from rutix.jobs.flush_week import flush_week
from rutix.jobs.update_habits import update_habits
from rutix.time_utils import subjective_today, yesterday_of

logger = logging.getLogger(__name__)


def make_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    github: GitHubClient,
    todoist: TodoistClient,
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

    scheduler.add_job(
        daily_3am,
        trigger=CronTrigger(hour=3, minute=0, timezone=ZoneInfo(tz)),
        id="daily_3am",
        replace_existing=True,
    )
    return scheduler
