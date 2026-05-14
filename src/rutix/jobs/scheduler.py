"""APScheduler — daily 03:00 cron that calls flush_day(yesterday)."""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rutix.integrations.github import GitHubClient
from rutix.jobs.flush_day import flush_day
from rutix.time_utils import subjective_today, yesterday_of

logger = logging.getLogger(__name__)


def make_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    github: GitHubClient,
    tz: str,
) -> AsyncIOScheduler:
    """Build (but don't start) the scheduler with the daily flush job."""
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(tz))

    async def daily_flush():
        today = subjective_today(datetime.now(ZoneInfo(tz)), tz)
        target = yesterday_of(today)
        logger.info("scheduled flush running for %s", target)
        async with session_factory() as session:
            try:
                sha = await flush_day(session, github, target)
                if sha:
                    logger.info("scheduled flush committed: %s", sha)
                else:
                    logger.info("scheduled flush — nothing to do")
            except Exception:
                logger.exception("scheduled flush failed")

    scheduler.add_job(
        daily_flush,
        trigger=CronTrigger(hour=3, minute=0, timezone=ZoneInfo(tz)),
        id="daily_flush",
        replace_existing=True,
    )
    return scheduler
