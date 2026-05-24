"""09:00 cron — read today's `## 🗓 План на день` from daily/<today>.md and post it.

Sends "Плана на сегодня нет" when the section is missing, the file is missing,
or the section only has placeholder dashes.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot

from rutix.integrations.github import GitHubClient
from rutix.markdown.daily import parse_day_plan
from rutix.time_utils import subjective_today

logger = logging.getLogger(__name__)

EMPTY_PLAN_TEXT = "🗓 Плана на сегодня нет."


def format_plan_message(bullets: list[str], day_iso: str) -> str:
    if not bullets:
        return EMPTY_PLAN_TEXT
    lines = [f"🗓 План на {day_iso}:"]
    for b in bullets:
        lines.append(f"• {b}")
    return "\n".join(lines)


async def daily_plan_ping(
    github: GitHubClient,
    bot: Bot,
    telegram_user_id: int,
    tz: str,
) -> bool:
    """Send today's day-plan to the user. Returns True if a message was sent."""
    today = subjective_today(datetime.now(ZoneInfo(tz)), tz)
    path = f"daily/{today.isoformat()}.md"
    file = await github.read(path)

    if file is None:
        logger.info("daily_plan_ping: %s not found — sending empty-plan message", path)
        bullets: list[str] = []
    else:
        bullets = parse_day_plan(file.text)

    text = format_plan_message(bullets, today.isoformat())
    await bot.send_message(chat_id=telegram_user_id, text=text)
    logger.info("daily_plan_ping sent for %s (%d items)", today, len(bullets))
    return True
