"""The schedule is data, not code: JOB_SPECS ships the defaults, `job_prefs`
rows override them, and an absent row must keep behaving exactly as before the
table existed."""

import pytest

from rutix.db.models import JobPref
from rutix.jobs.job_prefs import (
    JOB_SPEC_BY_ID,
    JobPrefsManager,
    UnknownJobError,
    format_time_label,
)


def _session_factory(session):
    def factory():
        class CM:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, *a):
                pass

        return CM()

    return factory


async def _manager(session) -> JobPrefsManager:
    m = JobPrefsManager(_session_factory(session))
    await m.load()
    return m


def test_time_label_covers_daily_hourly_and_per_minute():
    assert format_time_label("9", "0") == "09:00"
    assert format_time_label("*", "7") == "каждый час в :07"
    assert format_time_label("*", "*") == "каждую минуту"


async def test_defaults_when_no_rows_exist(session):
    m = await _manager(session)

    settings = m.all()
    assert [s.job_id for s in settings] == list(JOB_SPEC_BY_ID)
    assert all(s.enabled for s in settings)
    assert all(s.is_default_time for s in settings)
    assert m.get("daily_plan_ping").time_label == "09:00"


async def test_disable_survives_a_restart(session):
    m = await _manager(session)
    await m.set_enabled("daily_plan_ping", False)

    reloaded = await _manager(session)
    assert reloaded.get("daily_plan_ping").enabled is False
    # Everything else stays on.
    assert reloaded.get("daily_3am").enabled is True


async def test_toggle_flips_and_returns_the_new_setting(session):
    m = await _manager(session)

    off = await m.toggle("daily_plan_ping")
    assert off.enabled is False
    on = await m.toggle("daily_plan_ping")
    assert on.enabled is True


async def test_set_time_persists_and_reports_it(session):
    m = await _manager(session)

    setting = await m.set_time("daily_plan_ping", "10", "30")
    assert setting.time_label == "10:30"
    assert setting.is_default_time is False

    reloaded = await _manager(session)
    assert reloaded.get("daily_plan_ping").time_label == "10:30"


async def test_set_time_rejected_for_polling_jobs(session):
    m = await _manager(session)

    with pytest.raises(ValueError):
        await m.set_time("med_reminder_tick", "10", "0")
    with pytest.raises(ValueError):
        await m.set_time("tz_sync", "10", "0")


async def test_unknown_job_raises(session):
    m = await _manager(session)

    with pytest.raises(UnknownJobError):
        m.get("nope")
    with pytest.raises(UnknownJobError):
        await m.set_enabled("nope", False)


async def test_row_for_a_removed_job_is_ignored(session):
    """A rename/removal in a later version leaves a stale row behind — it must
    not blow up the startup path."""
    session.add(JobPref(job_id="retired_job", enabled=False, hour="1", minute="0"))
    await session.commit()

    m = await _manager(session)
    assert [s.job_id for s in m.all()] == list(JOB_SPEC_BY_ID)


async def test_hooks_fire_with_the_changed_job_id(session):
    m = await _manager(session)
    seen: list[str] = []

    async def hook(job_id: str) -> None:
        seen.append(job_id)

    m.subscribe(hook)
    await m.set_enabled("daily_3am", False)
    await m.set_time("daily_plan_ping", "11", "0")

    assert seen == ["daily_3am", "daily_plan_ping"]


async def test_a_failing_hook_does_not_lose_the_change(session):
    m = await _manager(session)

    async def boom(job_id: str) -> None:
        raise RuntimeError("scheduler exploded")

    m.subscribe(boom)
    await m.set_enabled("daily_3am", False)

    assert m.get("daily_3am").enabled is False
    reloaded = await _manager(session)
    assert reloaded.get("daily_3am").enabled is False
