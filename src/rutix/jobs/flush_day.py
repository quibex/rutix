"""Daily flush: SQLite state/report/meds for a given day → `## Самочувствие`
(state log) + `## Отчёт` (report) + `## Время (ч)` sections in daily/<date>.md."""

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rutix.daily_io import daily_path, read_or_init_daily
from rutix.db.models import FlushLog, MedActive, MedicationLog, MoodEntry, StateEntry
from rutix.integrations.github import GitHubClient
from rutix.markdown.daily import (
    ReportData,
    ReportMed,
    StateSnapshot,
    render_report_section,
    render_state_section,
    update_time_section,
    upsert_section,
)
from rutix.time_utils import is_saturday

logger = logging.getLogger(__name__)

STATE_TITLE = "Самочувствие"
REPORT_TITLE = "Отчёт"


async def flush_day(
    session: AsyncSession,
    github: GitHubClient,
    day: date,
) -> str | None:
    """Write today's state log + report + time data into daily/<day>.md.

    The daily file is scaffolded automatically if the user hasn't created it in
    Obsidian yet, so the day's data is never dropped.

    Returns the new commit SHA on success, or None when:
    - already flushed (idempotent re-run),
    - no StateEntry and no MoodEntry for that day (nothing to write),
    - content already matches (no-op).
    """
    period_id = f"day:{day.isoformat()}"

    if await session.get(FlushLog, period_id):
        logger.info("flush_day skipped — %s already flushed", period_id)
        return None

    states = (
        await session.scalars(
            select(StateEntry).where(StateEntry.day == day).order_by(StateEntry.ts)
        )
    ).all()
    mood = await session.get(MoodEntry, day)

    if not states and mood is None:
        logger.info("flush_day skipped — no StateEntry/MoodEntry for %s", day)
        return None

    path = daily_path(day)
    file = await read_or_init_daily(github, day)
    if file.sha is None:
        logger.info("flush_day scaffolding missing daily file at %s", path)

    new_text = file.text

    # `## Самочувствие` — one timestamped line per /state snapshot.
    if states:
        snapshots = [
            StateSnapshot(
                time_label=s.ts.strftime("%H:%M"),
                mood=s.mood,
                energy=s.energy,
                appetite=s.appetite,
            )
            for s in states
        ]
        new_text = upsert_section(new_text, STATE_TITLE, render_state_section(snapshots))

    # `## Отчёт` — sleep / weight / meds from the once-a-day /report.
    if mood is not None:
        meds_active = (
            await session.scalars(
                select(MedActive)
                .where(MedActive.archived_at.is_(None))
                .order_by(MedActive.started_at)
            )
        ).all()
        log_rows = (
            await session.scalars(select(MedicationLog).where(MedicationLog.day == day))
        ).all()
        taken_by_key = {r.med_key: r.taken for r in log_rows}

        report = ReportData(
            sleep_hours=mood.sleep_hours,
            weight=mood.weight,
            include_weight=is_saturday(day),
            meds=[
                ReportMed(
                    column_label=m.column_label,
                    taken=taken_by_key.get(m.key, False),
                    dose=m.current_dose,
                )
                for m in meds_active
            ],
        )
        new_text = upsert_section(new_text, REPORT_TITLE, render_report_section(report))

        # Also fill in `## Время (ч)` if the user tracked it via /report.
        vpn_hours = mood.vpn_hours
        eng_hours = mood.eng_hours
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
