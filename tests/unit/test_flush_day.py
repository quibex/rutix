from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from rutix.db.models import FlushLog, MedActive, MedicationLog, MoodEntry, StateEntry
from rutix.integrations.github import FileContent
from rutix.jobs.flush_day import flush_day


SAMPLE_DAILY = """# Среда, 13 мая

[[2026-W20|← Неделя 20]]

## 🗓 План на день

- one

---

## Сон

- Отбой: 23:30
- Подъём: 07:00

## Время (ч)

- VPN:
- Английский:

## Самочувствие

-

## Отчёт

-

## Привычки

- [ ] 📚 Anki

## Питание

| Приём | Что | Ккал | Б | Ж | У |
|-------|-----|------|---|---|---|
|  |  |  |  |  |  |
| **Итого** |  |  |  |  |  |

## Что сделано

-

## Заметки

-
"""


@pytest.fixture
def fake_github():
    g = MagicMock()
    g.read = AsyncMock(return_value=FileContent(text=SAMPLE_DAILY, sha="oldsha"))
    g.write = AsyncMock(return_value="newsha")
    return g


async def test_flush_day_writes_report_section(session, fake_github):
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
    session.add(MoodEntry(day=date(2026, 5, 13), sleep_hours=8.0, vpn_hours=2.0, eng_hours=1.0))
    session.add(MedicationLog(day=date(2026, 5, 13), med_key="seizar", taken=True))
    session.add(MedicationLog(day=date(2026, 5, 13), med_key="gidr_kanon", taken=True))
    await session.commit()

    sha = await flush_day(session, fake_github, date(2026, 5, 13))
    assert sha == "newsha"

    fake_github.read.assert_awaited_once_with("daily/2026-05-13.md")
    fake_github.write.assert_awaited_once()
    written_text = fake_github.write.call_args.args[1]
    assert "## Отчёт" in written_text
    assert "- Сон (ч): 8" in written_text
    assert "- Сейзар: ✓ 25" in written_text
    assert "- Гидр.К: ✓ 12.5" in written_text
    # VPN/English filled in-place in ## Время (ч)
    assert "- VPN: 2" in written_text
    assert "- Английский: 1" in written_text
    # Wednesday → no weight line
    assert "Вес:" not in written_text
    # Other sections preserved
    assert "## 🗓 План на день" in written_text
    assert "## Привычки" in written_text

    log = await session.get(FlushLog, "day:2026-05-13")
    assert log is not None
    assert log.git_sha == "newsha"


async def test_flush_day_writes_state_log(session, fake_github):
    session.add(
        StateEntry(
            day=date(2026, 5, 13),
            ts=datetime(2026, 5, 13, 9, 15),
            mood=1,
            energy=0,
            appetite=-1,
        )
    )
    session.add(
        StateEntry(
            day=date(2026, 5, 13),
            ts=datetime(2026, 5, 13, 18, 30),
            mood=2,
            energy=1,
            appetite=0,
        )
    )
    await session.commit()

    await flush_day(session, fake_github, date(2026, 5, 13))
    written_text = fake_github.write.call_args.args[1]
    assert "## Самочувствие" in written_text
    assert "- 09:15 — настроение: +1, энергия: 0, аппетит: -1" in written_text
    assert "- 18:30 — настроение: +2, энергия: +1, аппетит: 0" in written_text


async def test_flush_day_state_only_no_report(session, fake_github):
    """State snapshots but no /report → Самочувствие written, Отчёт left as-is."""
    session.add(
        StateEntry(
            day=date(2026, 5, 13), ts=datetime(2026, 5, 13, 8, 0), mood=0, energy=0, appetite=0
        )
    )
    await session.commit()

    sha = await flush_day(session, fake_github, date(2026, 5, 13))
    assert sha == "newsha"
    written_text = fake_github.write.call_args.args[1]
    assert "- 08:00 — настроение: 0" in written_text
    # No report data → the Отчёт placeholder is untouched (no sleep line added)
    assert "- Сон (ч):" not in written_text


async def test_flush_day_saturday_includes_weight(session, fake_github):
    saturday = date(2026, 5, 16)
    fake_github.read = AsyncMock(
        return_value=FileContent(text=SAMPLE_DAILY.replace("13 мая", "16 мая"), sha="s")
    )
    session.add(MoodEntry(day=saturday, sleep_hours=7.0, weight=57.5))
    await session.commit()

    await flush_day(session, fake_github, saturday)
    written_text = fake_github.write.call_args.args[1]
    assert "- Вес: 57.5" in written_text


async def test_flush_day_weekday_omits_weight_line(session, fake_github):
    wednesday = date(2026, 5, 13)
    session.add(MoodEntry(day=wednesday, sleep_hours=7.0, weight=57.5))
    await session.commit()
    await flush_day(session, fake_github, wednesday)
    written_text = fake_github.write.call_args.args[1]
    assert "Вес:" not in written_text


async def test_flush_day_med_not_taken_shows_cross(session, fake_github):
    day = date(2026, 5, 13)
    session.add(
        MedActive(
            key="seizar",
            name="Сейзар",
            column_label="Сейзар",
            current_dose="25",
            started_at=date(2026, 5, 1),
        )
    )
    session.add(MoodEntry(day=day, sleep_hours=7.0))
    session.add(MedicationLog(day=day, med_key="seizar", taken=False))
    await session.commit()

    await flush_day(session, fake_github, day)
    written_text = fake_github.write.call_args.args[1]
    assert "- Сейзар: ✗" in written_text


async def test_flush_day_appends_sections_when_missing(session, fake_github):
    # A daily file that predates the Самочувствие/Отчёт sections.
    stripped = SAMPLE_DAILY.replace("## Самочувствие\n\n-\n\n", "").replace("## Отчёт\n\n-\n\n", "")
    fake_github.read = AsyncMock(return_value=FileContent(text=stripped, sha="s"))
    session.add(MoodEntry(day=date(2026, 5, 13), sleep_hours=7.0))
    session.add(
        StateEntry(
            day=date(2026, 5, 13), ts=datetime(2026, 5, 13, 9, 0), mood=1, energy=0, appetite=0
        )
    )
    await session.commit()
    await flush_day(session, fake_github, date(2026, 5, 13))
    written_text = fake_github.write.call_args.args[1]
    assert "## Самочувствие" in written_text
    assert "## Отчёт" in written_text


async def test_flush_day_skips_if_already_flushed(session, fake_github):
    session.add(FlushLog(period_id="day:2026-05-13", git_sha="oldsha"))
    session.add(MoodEntry(day=date(2026, 5, 13), sleep_hours=7.0))
    await session.commit()

    sha = await flush_day(session, fake_github, date(2026, 5, 13))
    assert sha is None
    fake_github.write.assert_not_called()


async def test_flush_day_skips_if_no_data(session, fake_github):
    sha = await flush_day(session, fake_github, date(2026, 5, 13))
    assert sha is None
    fake_github.write.assert_not_called()


async def test_flush_day_scaffolds_if_no_daily_file(session, fake_github):
    """Missing daily file is created from a template and the data is written."""
    fake_github.read = AsyncMock(return_value=None)
    session.add(MoodEntry(day=date(2026, 5, 13), sleep_hours=7.0))
    session.add(
        StateEntry(
            day=date(2026, 5, 13), ts=datetime(2026, 5, 13, 9, 0), mood=1, energy=0, appetite=0
        )
    )
    await session.commit()
    sha = await flush_day(session, fake_github, date(2026, 5, 13))
    assert sha == "newsha"
    fake_github.write.assert_awaited_once()
    # sha=None → create (not update) on the remote.
    assert fake_github.write.call_args.kwargs.get("sha") is None
    written_text = fake_github.write.call_args.args[1]
    assert "## Самочувствие" in written_text
    assert "- 09:00 — настроение: +1" in written_text
    assert "## Отчёт" in written_text
    assert "- Сон (ч): 7" in written_text
    # Scaffolded sections are present for later /eat, /done, etc.
    assert "## Питание" in written_text
    assert "## Что сделано" in written_text

    log = await session.get(FlushLog, "day:2026-05-13")
    assert log is not None


async def test_flush_day_no_op_if_content_unchanged(session, fake_github):
    """Second flush_day for the same data is a no-op (no PUT)."""
    session.add(MoodEntry(day=date(2026, 5, 13), sleep_hours=8.0))
    await session.commit()

    # First flush writes the section
    await flush_day(session, fake_github, date(2026, 5, 13))
    written_text = fake_github.write.call_args.args[1]

    # Clear flush_log so flush_day won't short-circuit
    await session.delete(await session.get(FlushLog, "day:2026-05-13"))
    await session.commit()

    # Second read returns the just-written text → no-op
    fake_github.read = AsyncMock(return_value=FileContent(text=written_text, sha="x"))
    fake_github.write.reset_mock()

    sha = await flush_day(session, fake_github, date(2026, 5, 13))
    assert sha is None
    fake_github.write.assert_not_called()
