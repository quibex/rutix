"""Daily flush: SQLite mood/meds for a given day → `## Самочувствие` + `## Время (ч)`
sections in daily/<date>.md."""

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rutix.db.models import FlushLog, MedActive, MedicationLog, MoodEntry
from rutix.integrations.github import GitHubClient
from rutix.markdown.daily import (
    WellbeingData,
    WellbeingMed,
    render_wellbeing_section,
    update_time_section,
    upsert_section,
)
from rutix.time_utils import is_saturday

logger = logging.getLogger(__name__)

WELLBEING_TITLE = "Самочувствие"


def _daily_path(day: date) -> str:
    return f"daily/{day.isoformat()}.md"


async def flush_day(
    session: AsyncSession,
    github: GitHubClient,
    day: date,
) -> str | None:
    """Write today's wellbeing + time data into daily/<day>.md.

    Returns the new commit SHA on success, or None when:
    - already flushed (idempotent re-run),
    - no MoodEntry for that day (nothing to write),
    - daily file doesn't exist in the repo,
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
            select(MedActive).where(MedActive.archived_at.is_(None)).order_by(MedActive.started_at)
        )
    ).all()
    log_rows = (await session.scalars(select(MedicationLog).where(MedicationLog.day == day))).all()
    taken_by_key = {r.med_key: r.taken for r in log_rows}

    well = WellbeingData(
        mood=mood.mood,
        anxiety=mood.anxiety,
        irritability=mood.irritability,
        appetite=mood.appetite,
        sleep_hours=mood.sleep_hours,
        weight=mood.weight,
        include_weight=is_saturday(day),
        meds=[
            WellbeingMed(
                column_label=m.column_label,
                taken=taken_by_key.get(m.key, False),
                dose=m.current_dose,
            )
            for m in meds_active
        ],
    )

    path = _daily_path(day)
    file = await github.read(path)
    if file is None:
        logger.warning("flush_day skipped — no daily file at %s", path)
        return None

    new_text = upsert_section(file.text, WELLBEING_TITLE, render_wellbeing_section(well))

    # Optional: also fill in `## Время (ч)` if the user tracked it via /track.
    vpn_hours = getattr(mood, "vpn_hours", None)
    eng_hours = getattr(mood, "eng_hours", None)
    if vpn_hours is not None or eng_hours is not None:
        try:
            new_text = update_time_section(new_text, vpn_hours=vpn_hours, eng_hours=eng_hours)
        except ValueError:
            logger.warning(
                "flush_day: no '## Время (ч)' section in %s — skipping time update",
                path,
            )

    if new_text == file.text:
        logger.info("flush_day no-op — content unchanged for %s", day)
        return None

    sha = await github.write(
        path,
        new_text,
        f"daily({day.isoformat()}): авто-запись из rutix-bot",
        sha=file.sha,
    )

    session.add(FlushLog(period_id=period_id, git_sha=sha))
    await session.commit()
    logger.info("flush_day committed %s as %s", day, sha)
    return sha
