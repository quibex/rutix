from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from rutix.db.models import FlushLog, MedActive, MedicationLog, MoodEntry
from rutix.integrations.github import FileContent
from rutix.jobs.flush_day import MOOD_TRACKER_PATH, flush_day


SAMPLE_TRACKER = """# Таблица настроения

## Май 2026

| День | Настр. | Сон (ч) | Вес | Тревога | Раздр. | Сейзар | Гидр.К | Алк/Нарк | Заметки |
|------|--------|---------|-----|---------|--------|--------|--------|----------|---------|
| 13   |        |         |     |         |        |        |        |          |         |
"""


@pytest.fixture
def fake_github():
    g = MagicMock()
    g.read = AsyncMock(return_value=FileContent(text=SAMPLE_TRACKER, sha="oldsha"))
    g.write = AsyncMock(return_value="newsha")
    return g


async def test_flush_day_writes_row_and_marks_log(session, fake_github):
    session.add(
        MedActive(
            key="seizar",
            name="Сейзар",
            column_label="Сейзар",
            current_dose="25",
            started_at=date(2026, 5, 1),
        )
    )
    session.add(
        MedActive(
            key="gidr_kanon",
            name="Гидр.Канон",
            column_label="Гидр.К",
            current_dose="12.5",
            started_at=date(2026, 5, 1),
        )
    )
    session.add(
        MoodEntry(
            day=date(2026, 5, 13),
            mood=1,
            anxiety=0,
            irritability=0,
            sleep_hours=7.5,
        )
    )
    session.add(MedicationLog(day=date(2026, 5, 13), med_key="seizar", taken=True))
    session.add(MedicationLog(day=date(2026, 5, 13), med_key="gidr_kanon", taken=True))
    await session.commit()

    sha = await flush_day(session, fake_github, date(2026, 5, 13))
    assert sha == "newsha"

    fake_github.read.assert_awaited_once_with(MOOD_TRACKER_PATH)
    fake_github.write.assert_awaited_once()
    call_kwargs = fake_github.write.call_args.kwargs
    written_text = (
        fake_github.write.call_args.args[1]
        if len(fake_github.write.call_args.args) > 1
        else call_kwargs["text"]
    )
    assert "| 13 | +1 |" in written_text
    assert "✓ 25" in written_text
    assert "✓ 12.5" in written_text

    log = await session.get(FlushLog, "day:2026-05-13")
    assert log is not None
    assert log.git_sha == "newsha"


async def test_flush_day_skips_if_already_flushed(session, fake_github):
    session.add(FlushLog(period_id="day:2026-05-13", git_sha="oldsha"))
    session.add(MoodEntry(day=date(2026, 5, 13), mood=1))
    await session.commit()

    sha = await flush_day(session, fake_github, date(2026, 5, 13))
    assert sha is None
    fake_github.write.assert_not_called()


async def test_flush_day_skips_if_no_mood_entry(session, fake_github):
    sha = await flush_day(session, fake_github, date(2026, 5, 13))
    assert sha is None
    fake_github.write.assert_not_called()


async def test_flush_day_no_op_if_content_unchanged(session, fake_github):
    """If the rendered row equals what's already in the file, skip the PUT."""
    # Pre-populate the markdown with the exact same row we're about to render
    # (mood=1, sleep=7.0, anx=0, irr=0, seizar taken 25mg, gidr_kanon taken 12.5mg, notes=quiet)
    rendered = "| 13 | +1 | 7 |  | 0 | 0 | ✓ 25 | ✓ 12.5 |  | quiet |"
    pre_filled = SAMPLE_TRACKER.replace(
        "| 13   |        |         |     |         |        |        |        |          |         |",
        rendered,
    )
    fake_github.read = AsyncMock(return_value=FileContent(text=pre_filled, sha="x"))

    session.add(
        MedActive(
            key="seizar",
            name="Сейзар",
            column_label="Сейзар",
            current_dose="25",
            started_at=date(2026, 5, 1),
        )
    )
    session.add(
        MedActive(
            key="gidr_kanon",
            name="Гидр.Канон",
            column_label="Гидр.К",
            current_dose="12.5",
            started_at=date(2026, 5, 1),
        )
    )
    session.add(
        MoodEntry(
            day=date(2026, 5, 13),
            mood=1,
            anxiety=0,
            irritability=0,
            sleep_hours=7.0,
            notes="quiet",
        )
    )
    session.add(MedicationLog(day=date(2026, 5, 13), med_key="seizar", taken=True))
    session.add(MedicationLog(day=date(2026, 5, 13), med_key="gidr_kanon", taken=True))
    await session.commit()

    sha = await flush_day(session, fake_github, date(2026, 5, 13))
    assert sha is None
    fake_github.write.assert_not_called()
