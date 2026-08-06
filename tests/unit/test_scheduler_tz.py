"""A timezone change must re-point the already-registered cron triggers.

A `CronTrigger`'s timezone is frozen when the job is added, so without the
change hook the bot would persist the new zone, tell the user about it, and go
right on firing the 03:00 flush and every med tick on the old clock.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from rutix.jobs.scheduler import build_tz_change_message, make_scheduler
from rutix.settings import Settings
from rutix.tz_manager import TimezoneManager

_CRON_JOB_IDS = {
    "daily_3am",
    "update_habits_retry_06",
    "update_habits_retry_08",
    "daily_plan_ping",
    "tz_sync",
    "med_reminder_tick",
}


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


async def _build(session, fake_bot, remote_tz=None):
    todoist = MagicMock()
    todoist.get_user_timezone = AsyncMock(return_value=remote_tz)
    todoist.tz = "Europe/Moscow"

    factory = _session_factory(session)
    manager = TimezoneManager(factory, Settings.model_construct(tz="Europe/Moscow"), todoist)
    await manager.load()

    scheduler = make_scheduler(
        factory,
        MagicMock(),  # github
        todoist,
        MagicMock(),  # claude
        fake_bot,
        telegram_user_id=1,
        tz_manager=manager,
    )
    return scheduler, manager


def _trigger_zones(scheduler) -> dict[str, str]:
    # Jobs sit in _pending_jobs until the scheduler starts; this test never
    # starts it, so nothing fires while we inspect the triggers.
    return {job.id: str(job.trigger.timezone) for job, _, _ in scheduler._pending_jobs}


async def test_all_cron_jobs_registered_in_the_current_zone(session, fake_bot):
    scheduler, _ = await _build(session, fake_bot)
    zones = _trigger_zones(scheduler)

    assert set(zones) == _CRON_JOB_IDS
    assert set(zones.values()) == {"Europe/Moscow"}


async def test_tz_change_reschedules_every_cron_job(session, fake_bot):
    scheduler, manager = await _build(session, fake_bot)

    await manager.set_tz("Asia/Bishkek")

    zones = _trigger_zones(scheduler)
    assert set(zones) == _CRON_JOB_IDS
    assert set(zones.values()) == {"Asia/Bishkek"}


async def test_tz_change_tells_the_user(session, fake_bot):
    _, manager = await _build(session, fake_bot)

    await manager.set_tz("Asia/Bishkek")

    text = fake_bot.send_message.call_args_list[0].kwargs["text"]
    assert "Europe/Moscow" in text and "Asia/Bishkek" in text


async def test_hourly_sync_job_applies_a_detected_move(session, fake_bot):
    """End to end: the cron calls Todoist, the zone lands everywhere."""
    scheduler, manager = await _build(session, fake_bot, remote_tz="Asia/Bishkek")

    tz_sync = next(job for job, _, _ in scheduler._pending_jobs if job.id == "tz_sync")
    await tz_sync.func()

    assert manager.tz == "Asia/Bishkek"
    assert set(_trigger_zones(scheduler).values()) == {"Asia/Bishkek"}


def test_change_message_names_both_zones_and_local_time():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    msg = build_tz_change_message(
        "Europe/Moscow",
        "Asia/Bishkek",
        datetime(2026, 8, 6, 8, 40, tzinfo=ZoneInfo("Asia/Bishkek")),
    )
    assert "Europe/Moscow → Asia/Bishkek" in msg
    assert "08:40" in msg
