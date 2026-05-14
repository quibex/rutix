"""Daily flush: SQLite mood/meds for a given day → row in mood_tracker.md."""
import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rutix.db.models import FlushLog, MedActive, MedicationLog, MoodEntry
from rutix.integrations.github import GitHubClient
from rutix.markdown.mood_tracker import DayRow, MedColumn, render_row, update_day_row

logger = logging.getLogger(__name__)

MOOD_TRACKER_PATH = "health/mood_tracker.md"


async def flush_day(
    session: AsyncSession,
    github: GitHubClient,
    day: date,
) -> str | None:
    """Flush a single day's data to mood_tracker.md.

    Returns the new commit SHA on success, or None when:
    - already flushed (idempotent re-run),
    - no MoodEntry for that day (nothing to write),
    - content already matches (no-op).
    """
    period_id = f"day:{day.isoformat()}"

    if await session.get(FlushLog, period_id):
        logger.info("flush_day skipped — %s already flushed", period_id)
        return None

    mood = await session.get(MoodEntry, day)
    if mood is None:
        logger.info("flush_day skipped — no MoodEntry for %s", day)
        return None

    meds_active = (
        await session.scalars(
            select(MedActive)
            .where(MedActive.archived_at.is_(None))
            .order_by(MedActive.started_at)
        )
    ).all()
    log_rows = (
        await session.scalars(
            select(MedicationLog).where(MedicationLog.day == day)
        )
    ).all()
    taken_by_key = {r.med_key: r.taken for r in log_rows}

    row = DayRow(
        day=day.day,
        mood=mood.mood,
        sleep_hours=mood.sleep_hours,
        weight=mood.weight,
        anxiety=mood.anxiety,
        irritability=mood.irritability,
        notes=mood.notes or "",
        meds=[
            MedColumn(
                column_label=m.column_label,
                taken=taken_by_key.get(m.key, False),
                dose=m.current_dose,
            )
            for m in meds_active
        ],
    )

    rendered = render_row(row)

    file = await github.read(MOOD_TRACKER_PATH)
    if file is None:
        raise RuntimeError(f"{MOOD_TRACKER_PATH} not found in repo")

    new_text = update_day_row(file.text, day.year, day.month, day.day, rendered)
    if new_text == file.text:
        logger.info("flush_day no-op — content unchanged for %s", day)
        return None

    sha = await github.write(
        MOOD_TRACKER_PATH,
        new_text,
        f"mood({day.isoformat()}): авто-запись из rutix-bot",
        sha=file.sha,
    )

    session.add(FlushLog(period_id=period_id, git_sha=sha))
    await session.commit()
    logger.info("flush_day committed %s as %s", day, sha)
    return sha
