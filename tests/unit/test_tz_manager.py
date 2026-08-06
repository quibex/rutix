"""Runtime timezone: DB seeding/persistence, Todoist auto-detection, change hooks."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from rutix.db.models import UserPref
from rutix.settings import Settings
from rutix.tz_manager import TimezoneManager, is_valid_tz


def _session_factory(session):
    def factory():
        class CM:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, *a):
                pass

        return CM()

    return factory


def _settings(tz="Europe/Moscow"):
    return Settings.model_construct(tz=tz)


def _todoist(remote_tz=None):
    t = AsyncMock()
    t.get_user_timezone = AsyncMock(return_value=remote_tz)
    t.tz = "Europe/Moscow"
    return t


def _manager(session, *, settings=None, todoist=None):
    return TimezoneManager(
        _session_factory(session),
        settings or _settings(),
        todoist or _todoist(),
    )


# --- is_valid_tz -----------------------------------------------------------


@pytest.mark.parametrize("name", ["Europe/Moscow", "Asia/Bishkek", "UTC"])
def test_is_valid_tz_accepts_iana(name):
    assert is_valid_tz(name)


@pytest.mark.parametrize("name", ["", "   ", "+06:00", "MSK+3", "Mars/Olympus"])
def test_is_valid_tz_rejects_junk(name):
    """`tz_info` also carries a `gmt_string` like '+06:00' — it must never be
    mistaken for a zone name and reach a ZoneInfo() at cron time."""
    assert not is_valid_tz(name)


# --- load() ----------------------------------------------------------------


async def test_load_seeds_row_from_env_on_first_run(session):
    m = _manager(session, settings=_settings("Europe/Moscow"))
    assert await m.load() == "Europe/Moscow"

    row = (await session.scalars(select(UserPref))).one()
    assert row.tz == "Europe/Moscow"


async def test_load_prefers_stored_tz_over_env(session):
    """A restart must not snap the schedule back to the deploy-time TZ — the
    stored zone is the last known truth (and the fallback if Todoist is down)."""
    session.add(UserPref(id=1, tz="Asia/Bishkek"))
    await session.commit()

    settings = _settings("Europe/Moscow")
    todoist = _todoist()
    m = _manager(session, settings=settings, todoist=todoist)

    assert await m.load() == "Asia/Bishkek"
    assert settings.tz == "Asia/Bishkek"
    assert todoist.tz == "Asia/Bishkek"


# --- set_tz ----------------------------------------------------------------


async def test_set_tz_updates_settings_todoist_and_db(session):
    """The three places that cache the zone must move together — handlers read
    settings.tz, the Activity Log window reads todoist.tz, and the DB survives
    a restart."""
    settings = _settings("Europe/Moscow")
    todoist = _todoist()
    m = _manager(session, settings=settings, todoist=todoist)
    await m.load()

    assert await m.set_tz("Asia/Bishkek") is True

    assert m.tz == "Asia/Bishkek"
    assert settings.tz == "Asia/Bishkek"
    assert todoist.tz == "Asia/Bishkek"
    assert (await session.scalars(select(UserPref))).one().tz == "Asia/Bishkek"


async def test_set_tz_to_same_zone_is_a_noop(session):
    m = _manager(session)
    await m.load()
    hook = AsyncMock()
    m.subscribe(hook)

    assert await m.set_tz("Europe/Moscow") is False
    hook.assert_not_awaited()


async def test_set_tz_rejects_unknown_zone(session):
    m = _manager(session)
    await m.load()
    with pytest.raises(ValueError):
        await m.set_tz("Mars/Olympus")
    assert m.tz == "Europe/Moscow"


async def test_change_hooks_receive_old_and_new(session):
    m = _manager(session)
    await m.load()
    hook = AsyncMock()
    m.subscribe(hook)

    await m.set_tz("Asia/Bishkek")
    hook.assert_awaited_once_with("Europe/Moscow", "Asia/Bishkek")


async def test_failing_hook_does_not_undo_the_change(session):
    """A broken reschedule must not leave the manager and the DB disagreeing."""
    m = _manager(session)
    await m.load()
    m.subscribe(AsyncMock(side_effect=RuntimeError("boom")))

    assert await m.set_tz("Asia/Bishkek") is True
    assert m.tz == "Asia/Bishkek"


# --- sync_from_todoist -----------------------------------------------------


async def test_sync_applies_remote_timezone(session):
    todoist = _todoist("Asia/Bishkek")
    m = _manager(session, todoist=todoist)
    await m.load()

    assert await m.sync_from_todoist() == "Asia/Bishkek"
    assert m.tz == "Asia/Bishkek"


async def test_sync_is_a_noop_when_remote_matches(session):
    m = _manager(session, todoist=_todoist("Europe/Moscow"))
    await m.load()
    hook = AsyncMock()
    m.subscribe(hook)

    assert await m.sync_from_todoist() is None
    hook.assert_not_awaited()


async def test_sync_keeps_current_tz_when_todoist_gives_nothing(session):
    """Todoist down / payload unusable → keep running on the known-good zone
    rather than falling back to the env default."""
    session.add(UserPref(id=1, tz="Asia/Bishkek"))
    await session.commit()
    m = _manager(session, todoist=_todoist(None))
    await m.load()

    assert await m.sync_from_todoist() is None
    assert m.tz == "Asia/Bishkek"
