"""Weekly flush — runs Monday 03:00 to wrap up the just-finished week.

Idempotent via FlushLog `week:<id>`. Delegates editorial work to Claude
(close_week): semantic habit matching, week score, what worked / failed,
focus for next week, and templates for the 7 daily files of the upcoming week.

Pure-code side: parses meals + notes from daily files (so Питание and Заметки
end up in nutrition/Wxx.md and thoughts/Wxx.md respectively), writes all four
target files, then deletes the closed week's daily files.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from rutix.db.models import FlushLog, MedicationLog, MoodEntry
from rutix.integrations.claude import ClaudeClient
from rutix.integrations.github import GitHubClient
from rutix.integrations.todoist import TodoistClient
from rutix.markdown.daily import parse_meals, parse_notes
from rutix.markdown.nutrition_weekly import NutritionDay, render_nutrition_weekly
from rutix.markdown.thoughts import ThoughtsDay, render_thoughts_weekly
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


@dataclass
class FlushWeekResult:
    sha: str
    user_message: str


async def flush_week(
    session: AsyncSession,
    github: GitHubClient,
    today: date,
    claude: ClaudeClient,
    todoist: TodoistClient,
) -> FlushWeekResult | None:
    if today.weekday() != 0:  # Monday
        return None

    sunday = yesterday_of(today)
    wid = week_id(sunday)
    period_id = f"week:{wid}"

    if await session.get(FlushLog, period_id):
        logger.info("flush_week skipped — %s already flushed", period_id)
        return None

    week_dates = days_of_week(sunday)
    next_week_dates = [d + timedelta(days=7) for d in week_dates]

    # --- Read all inputs ---
    daily_files: dict[date, object] = {}
    for d in week_dates:
        daily_files[d] = await github.read(f"daily/{d.isoformat()}.md")

    habits_file = await github.read("habits.md")
    habits_md = habits_file.text if habits_file else ""
    habits = _parse_habits_md(habits_md) if habits_md else HabitsConfig(daily=[], scheduled={})

    goals_file = await github.read("goals.md")
    goals_md = goals_file.text if goals_file else ""

    prev_week_id = week_id(week_dates[0] - timedelta(days=1))
    prev_weekly_file = await github.read(f"weekly/{prev_week_id}.md")
    prev_weekly_md = prev_weekly_file.text if prev_weekly_file else ""

    todoist_completions: dict[str, list[str]] = {}
    for d in week_dates:
        try:
            titles = await todoist.completed_titles_for_day(d)
        except Exception:
            logger.exception("todoist failed for %s — using empty list", d)
            titles = set()
        todoist_completions[d.isoformat()] = sorted(titles)

    # --- Claude does the editorial closure ---
    close = await claude.close_week(
        week_id=wid,
        dates=[d.isoformat() for d in week_dates],
        next_week_dates=[d.isoformat() for d in next_week_dates],
        habits_md=habits_md,
        goals_md=goals_md,
        prev_weekly_md=prev_weekly_md,
        daily_files={
            d.isoformat(): (daily_files[d].text if daily_files[d] else "") for d in week_dates
        },
        todoist_completions=todoist_completions,
    )

    # --- Build per-day artifacts for nutrition + thoughts (pure code) ---
    nutrition_days: list[NutritionDay] = []
    thoughts_days: list[ThoughtsDay] = []
    weekly_days: list[WeeklyDay] = []
    for d in week_dates:
        f = daily_files.get(d)
        if f is None:
            nutrition_days.append(NutritionDay(date=d, meals=[]))
            thoughts_days.append(ThoughtsDay(date=d, notes=[]))
            weekly_days.append(
                WeeklyDay(date=d, done_habits=set(), sleep_offh=None, sleep_onh=None, kcal=None)
            )
            continue
        meals = parse_meals(f.text)
        notes = parse_notes(f.text)
        nutrition_days.append(NutritionDay(date=d, meals=meals))
        thoughts_days.append(ThoughtsDay(date=d, notes=notes))
        weekly_days.append(
            WeeklyDay(
                date=d,
                done_habits=set(),  # legacy field — render_weekly uses habits_count instead
                sleep_offh=None,
                sleep_onh=None,
                kcal=sum(m.kcal for m in meals) if meals else None,
            )
        )

    weekly_md = render_weekly(
        year=sunday.isocalendar().year,
        week_num=sunday.isocalendar().week,
        days=weekly_days,
        habits=habits,
        habits_count=close.habits_aggregate,
        avg_kcal_override=close.avg_kcal,
        score=close.score,
        what_worked=close.what_worked,
        what_failed=close.what_failed,
        focus_next_week=close.focus_next_week,
        trend_kcal=close.trend_kcal,
    )
    nutrition_md = render_nutrition_weekly(
        year=sunday.isocalendar().year,
        week_num=sunday.isocalendar().week,
        days=nutrition_days,
    )
    thoughts_md = render_thoughts_weekly(
        year=sunday.isocalendar().year,
        week_num=sunday.isocalendar().week,
        days=thoughts_days,
    )

    # --- Write weekly + nutrition + thoughts ---
    weekly_path = f"weekly/{wid}.md"
    nutrition_path = f"nutrition/{wid}.md"
    thoughts_path = f"thoughts/{wid}.md"

    weekly_existing = await github.read(weekly_path)
    weekly_sha = await github.write(
        weekly_path,
        weekly_md,
        f"weekly({wid}): close + open {week_id(next_week_dates[0])}",
        sha=weekly_existing.sha if weekly_existing else None,
    )
    nutrition_existing = await github.read(nutrition_path)
    await github.write(
        nutrition_path,
        nutrition_md,
        f"nutrition({wid}): авто-запись из rutix-bot",
        sha=nutrition_existing.sha if nutrition_existing else None,
    )
    thoughts_existing = await github.read(thoughts_path)
    await github.write(
        thoughts_path,
        thoughts_md,
        f"thoughts({wid}): перенос заметок из daily",
        sha=thoughts_existing.sha if thoughts_existing else None,
    )

    # --- Create 7 daily files for next week ---
    next_wid = week_id(next_week_dates[0])
    for d in next_week_dates:
        iso = d.isoformat()
        template = close.next_week_daily.get(iso)
        if not template:
            logger.warning("close_week did not return template for %s — skipping", iso)
            continue
        path = f"daily/{iso}.md"
        existing = await github.read(path)
        await github.write(
            path,
            template,
            f"daily({iso}): шаблон на {next_wid}",
            sha=existing.sha if existing else None,
        )

    # --- Delete closed-week daily files ---
    for d in week_dates:
        f = daily_files.get(d)
        if f is None:
            continue
        await github.delete(
            f"daily/{d.isoformat()}.md",
            f"daily({d.isoformat()}): cleanup after weekly flush",
            sha=f.sha,
        )

    # --- Purge SQLite for the closed week ---
    week_set = set(week_dates)
    await session.execute(delete(MoodEntry).where(MoodEntry.day.in_(week_set)))
    await session.execute(delete(MedicationLog).where(MedicationLog.day.in_(week_set)))

    session.add(FlushLog(period_id=period_id, git_sha=weekly_sha))
    await session.commit()
    logger.info("flush_week committed %s as %s", wid, weekly_sha)
    return FlushWeekResult(sha=weekly_sha, user_message=close.user_message)
