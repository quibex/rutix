"""Tests for /report not asking about meds whose scheduled time hasn't arrived.

Running /report at 09:00 must not ask about an 11:00 pill; re-running at 12:00
must then include it. This is driven by `_load_meds` / `_compute_resume`, which
take an explicit "now" (HH:MM) so the behaviour is testable without freezing the
clock.
"""

from datetime import date

from rutix.bot.handlers.report import _compute_resume, _load_meds, _med_due
from rutix.db.models import MedActive, MedicationLog, MoodEntry

_DAY = date(2026, 5, 13)


def _session_factory(session):
    def factory():
        class CM:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, *a):
                pass

        return CM()

    return factory


def _med(key: str, name: str, reminder_time, started=date(2026, 5, 1)) -> MedActive:
    return MedActive(
        key=key,
        name=name,
        column_label=name,
        current_dose="25",
        started_at=started,
        reminder_time=reminder_time,
    )


def test_med_due_helper():
    assert _med_due(None, "09:00") is True  # no schedule → always askable
    assert _med_due("08:00", "09:00") is True  # already passed
    assert _med_due("09:00", "09:00") is True  # exactly now
    assert _med_due("11:00", "09:00") is False  # still in the future


async def test_load_meds_excludes_not_yet_due(session):
    session.add(_med("morning", "Утренняя", "08:00", started=date(2026, 5, 1)))
    session.add(_med("late", "Поздняя", "11:00", started=date(2026, 5, 2)))
    await session.commit()

    pending, taken = await _load_meds(_session_factory(session), _DAY, "09:00")

    assert pending == ["morning"]  # 11:00 pill is hidden at 09:00
    assert taken == []


async def test_load_meds_includes_due_after_time_arrives(session):
    session.add(_med("morning", "Утренняя", "08:00", started=date(2026, 5, 1)))
    session.add(_med("late", "Поздняя", "11:00", started=date(2026, 5, 2)))
    await session.commit()

    pending, _ = await _load_meds(_session_factory(session), _DAY, "12:00")

    assert pending == ["morning", "late"]


async def test_resume_skips_meds_step_when_only_future_meds(session):
    # sleep done; the only active med is due later than now → skip straight to vpn.
    session.add(MoodEntry(day=_DAY, sleep_hours=7.0))
    session.add(_med("late", "Поздняя", "11:00"))
    await session.commit()

    step, seed, _ = await _compute_resume(_session_factory(session), _DAY, "09:00")

    assert step == "vpn"
    assert seed["meds_pending"] == []


async def test_resume_hits_meds_step_once_due(session):
    session.add(MoodEntry(day=_DAY, sleep_hours=7.0))
    session.add(_med("late", "Поздняя", "11:00"))
    await session.commit()

    step, seed, _ = await _compute_resume(_session_factory(session), _DAY, "12:00")

    assert step == "meds"
    assert seed["meds_pending"] == ["late"]


async def test_already_logged_future_med_not_reasked(session):
    # Even before its time, a med already logged counts as answered, never pending.
    session.add(_med("late", "Поздняя", "11:00"))
    session.add(MedicationLog(day=_DAY, med_key="late", taken=True))
    await session.commit()

    pending, taken = await _load_meds(_session_factory(session), _DAY, "09:00")

    assert pending == []
    assert taken == [{"key": "late", "taken": True}]
