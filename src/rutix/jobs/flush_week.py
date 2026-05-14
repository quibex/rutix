"""Weekly flush — runs Monday 03:00 to wrap up the just-finished week.

Idempotent via FlushLog `week:<id>`.
"""
import logging
import re
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from rutix.db.models import FlushLog, MedicationLog, MoodEntry
from rutix.integrations.github import GitHubClient
from rutix.markdown.daily import parse_meals
from rutix.markdown.nutrition_weekly import NutritionDay, render_nutrition_weekly
from rutix.markdown.weekly import HabitsConfig, WeeklyDay, render_weekly
from rutix.time_utils import days_of_week, week_id, yesterday_of

logger = logging.getLogger(__name__)


_HABITS_DAILY_TABLE_RE = re.compile(
    r"## Ежедневные\s*\n\s*\n\| Привычка \|.*?\n\|[\s\-:|]+\|\n((?:\|.*\n)+)",
    re.DOTALL,
)
_HABITS_SCHED_TABLE_RE = re.compile(
    r"## По расписанию\s*\n\s*\n\| Привычка \|.*?\n\|[\s\-:|]+\|\n((?:\|.*\n)+)",
    re.DOTALL,
)


def _parse_habits_md(habits_md: str) -> HabitsConfig:
    daily_match = _HABITS_DAILY_TABLE_RE.search(habits_md)
    daily = []
    if daily_match:
        for row in daily_match.group(1).splitlines():
            cells = [c.strip() for c in row.split("|")]
            if len(cells) > 2 and cells[1]:
                daily.append(cells[1])

    scheduled = {}
    sched_match = _HABITS_SCHED_TABLE_RE.search(habits_md)
    if sched_match:
        for row in sched_match.group(1).splitlines():
            cells = [c.strip() for c in row.split("|")]
            if len(cells) > 4 and cells[1]:
                scheduled[cells[1]] = [d.strip() for d in cells[3].split("/")]

    return HabitsConfig(daily=daily, scheduled=scheduled)


_HABIT_LINE_RE = re.compile(r"^\s*-\s*\[([ x])\]\s*(.+?)\s*$")


def _parse_done_habits(daily_md: str) -> set[str]:
    done = set()
    in_habits = False
    for line in daily_md.splitlines():
        if line.startswith("## Привычки"):
            in_habits = True
            continue
        if in_habits and line.startswith("## "):
            break
        if in_habits:
            m = _HABIT_LINE_RE.match(line)
            if m and m.group(1) == "x":
                done.add(m.group(2))
    return done


async def flush_week(
    session: AsyncSession,
    github: GitHubClient,
    today: date,
) -> str | None:
    if today.weekday() != 0:  # Monday
        return None

    sunday = yesterday_of(today)
    wid = week_id(sunday)
    period_id = f"week:{wid}"

    if await session.get(FlushLog, period_id):
        logger.info("flush_week skipped — %s already flushed", period_id)
        return None

    week_dates = days_of_week(sunday)

    # Read daily files (some may be missing if user didn't have them)
    daily_contents: dict[date, str | None] = {}
    for d in week_dates:
        file = await github.read(f"daily/{d.isoformat()}.md")
        daily_contents[d] = file.text if file else None
        # We need SHA for delete later — re-fetch later or stash now:
    # Re-read with SHAs (one fetch each, simple)
    daily_files = {}
    for d in week_dates:
        f = await github.read(f"daily/{d.isoformat()}.md")
        daily_files[d] = f

    # Parse habits.md for the config
    habits_file = await github.read("habits.md")
    habits = _parse_habits_md(habits_file.text) if habits_file else HabitsConfig(daily=[], scheduled={})

    # Build WeeklyDay + NutritionDay arrays
    weekly_days: list[WeeklyDay] = []
    nutrition_days: list[NutritionDay] = []
    for d in week_dates:
        f = daily_files.get(d)
        if f is None:
            weekly_days.append(WeeklyDay(date=d, done_habits=set(), sleep_offh=None, sleep_onh=None, kcal=None))
            nutrition_days.append(NutritionDay(date=d, meals=[]))
            continue
        meals = parse_meals(f.text)
        kcal_total = sum(m.kcal for m in meals) if meals else None
        weekly_days.append(WeeklyDay(
            date=d, done_habits=_parse_done_habits(f.text),
            sleep_offh=None, sleep_onh=None, kcal=kcal_total,
        ))
        nutrition_days.append(NutritionDay(date=d, meals=meals))

    weekly_md = render_weekly(
        year=sunday.isocalendar().year, week_num=sunday.isocalendar().week,
        days=weekly_days, habits=habits,
    )
    nutrition_md = render_nutrition_weekly(
        year=sunday.isocalendar().year, week_num=sunday.isocalendar().week,
        days=nutrition_days,
    )

    weekly_path = f"weekly/{wid}.md"
    nutrition_path = f"nutrition/{wid}.md"

    weekly_existing = await github.read(weekly_path)
    weekly_sha = await github.write(
        weekly_path, weekly_md, f"weekly({wid}): авто-запись из rutix-bot",
        sha=weekly_existing.sha if weekly_existing else None,
    )
    nutrition_existing = await github.read(nutrition_path)
    await github.write(
        nutrition_path, nutrition_md, f"nutrition({wid}): авто-запись из rutix-bot",
        sha=nutrition_existing.sha if nutrition_existing else None,
    )

    # Delete daily files
    for d in week_dates:
        f = daily_files.get(d)
        if f is None:
            continue
        await github.delete(
            f"daily/{d.isoformat()}.md",
            f"daily({d.isoformat()}): cleanup after weekly flush",
            sha=f.sha,
        )

    # Purge SQLite for the week
    week_set = set(week_dates)
    await session.execute(delete(MoodEntry).where(MoodEntry.day.in_(week_set)))
    await session.execute(delete(MedicationLog).where(MedicationLog.day.in_(week_set)))

    session.add(FlushLog(period_id=period_id, git_sha=weekly_sha))
    await session.commit()
    logger.info("flush_week committed %s as %s", wid, weekly_sha)
    return weekly_sha
