"""A `/schedule` change must reach APScheduler immediately.

A `CronTrigger` is frozen once the job is added, so turning a job off has to
remove it and moving its time has to re-add it. The scheduler is started
*paused* here: jobs land in the real jobstore (which is what `apply_job` talks
to in production) but nothing ever fires during the test.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from rutix.jobs.job_prefs import JobPrefsManager
from rutix.jobs.scheduler import make_scheduler
from rutix.settings import Settings
from rutix.tz_manager import TimezoneManager


def _session_factory(session):
    def factory():
        class CM:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, *a):
                pass

        return CM()

    return factory


@pytest.fixture
def fake_bot():
    b = MagicMock()
    b.send_message = AsyncMock()
    return b


async def _build(session, fake_bot):
    todoist = MagicMock()
    todoist.get_user_timezone = AsyncMock(return_value=None)
    todoist.tz = "Europe/Moscow"

    factory = _session_factory(session)
    tz_manager = TimezoneManager(factory, Settings.model_construct(tz="Europe/Moscow"), todoist)
    await tz_manager.load()
    job_prefs = JobPrefsManager(factory)
    await job_prefs.load()

    scheduler = make_scheduler(
        factory,
        MagicMock(),  # github
        todoist,
        MagicMock(),  # claude
        fake_bot,
        telegram_user_id=1,
        tz_manager=tz_manager,
        job_prefs=job_prefs,
    )
    scheduler.start(paused=True)
    return scheduler, job_prefs, tz_manager


@pytest.fixture
async def built(session, fake_bot):
    scheduler, job_prefs, tz_manager = await _build(session, fake_bot)
    yield scheduler, job_prefs, tz_manager
    scheduler.shutdown(wait=False)


def _job_ids(scheduler) -> set[str]:
    return {job.id for job in scheduler.get_jobs()}


def _cron_fields(scheduler, job_id: str) -> dict[str, str]:
    return {f.name: str(f) for f in scheduler.get_job(job_id).trigger.fields}


async def test_disabling_a_job_unregisters_it(built):
    scheduler, job_prefs, _ = built
    assert "daily_plan_ping" in _job_ids(scheduler)

    await job_prefs.set_enabled("daily_plan_ping", False)

    assert "daily_plan_ping" not in _job_ids(scheduler)
    # Its neighbours are untouched.
    assert "daily_3am" in _job_ids(scheduler)


async def test_re_enabling_registers_it_again(built):
    scheduler, job_prefs, _ = built

    await job_prefs.set_enabled("daily_plan_ping", False)
    await job_prefs.set_enabled("daily_plan_ping", True)

    assert "daily_plan_ping" in _job_ids(scheduler)
    assert _cron_fields(scheduler, "daily_plan_ping")["hour"] == "9"


async def test_a_job_disabled_before_startup_is_never_registered(session, fake_bot):
    """The bot restarts with the user's schedule, not the shipped one."""
    pre = JobPrefsManager(_session_factory(session))
    await pre.load()
    await pre.set_enabled("daily_plan_ping", False)

    scheduler, _, _ = await _build(session, fake_bot)
    try:
        assert "daily_plan_ping" not in _job_ids(scheduler)
    finally:
        scheduler.shutdown(wait=False)


async def test_changing_the_time_repoints_the_trigger(built):
    scheduler, job_prefs, _ = built

    await job_prefs.set_time("daily_plan_ping", "10", "30")

    fields = _cron_fields(scheduler, "daily_plan_ping")
    assert fields["hour"] == "10"
    assert fields["minute"] == "30"


async def test_a_timezone_move_keeps_the_users_own_schedule(built):
    """Travel re-registers every job — it must not resurrect a disabled one or
    snap a moved job back to its default hour."""
    scheduler, job_prefs, tz_manager = built
    await job_prefs.set_enabled("update_habits_retry_06", False)
    await job_prefs.set_time("daily_plan_ping", "10", "30")

    await tz_manager.set_tz("Asia/Bishkek")

    assert "update_habits_retry_06" not in _job_ids(scheduler)
    fields = _cron_fields(scheduler, "daily_plan_ping")
    assert (fields["hour"], fields["minute"]) == ("10", "30")
    assert {str(job.trigger.timezone) for job in scheduler.get_jobs()} == {"Asia/Bishkek"}
