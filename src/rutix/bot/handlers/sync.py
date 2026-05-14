"""/sync — force flush of yesterday into mood_tracker.md."""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rutix.integrations.github import GitHubClient
from rutix.jobs.flush_day import flush_day
from rutix.settings import Settings
from rutix.time_utils import subjective_today, yesterday_of

logger = logging.getLogger(__name__)

router = Router(name="sync")


@router.message(Command("sync"))
async def cmd_sync(
    message: Message,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    github: GitHubClient,
):
    today = subjective_today(datetime.now(ZoneInfo(settings.tz)), settings.tz)
    target = yesterday_of(today)
    async with session_factory() as session:
        try:
            sha = await flush_day(session, github, target)
        except Exception as e:
            logger.exception("sync failed")
            await message.answer(f"❌ /sync упал: {type(e).__name__}: {e}")
            return
    if sha:
        await message.answer(f"✅ Закоммитил {target.isoformat()} → {sha[:7]}")
    else:
        await message.answer(
            f"⏭ Нечего коммитить за {target.isoformat()} (уже сделано или нет данных)"
        )
