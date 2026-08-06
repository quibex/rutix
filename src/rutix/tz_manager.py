"""Runtime timezone — one owner for "what time is it for the user right now".

The user travels. Everything time-shaped in this bot (the 03:00 flush, the
09:00 plan ping, and every med `reminder_time`, which is a bare "HH:MM" string
compared against the current minute) is evaluated in a single timezone, so a
move silently shifts the whole schedule by the offset difference — reminders
keep firing, just at the wrong local hour.

`TZ` in the environment is only the **seed**. The live value lives in the
`user_prefs` row and is kept current automatically, with no command to run: the
Todoist mobile app rewrites the account timezone when the phone changes zones,
and `sync_from_todoist` reads it back hourly (see `scheduler.py`).

Reminder times are wall-clock, not instants: a med set to "08:00" means 8 in
the morning wherever the user is, so a timezone change re-points it at the new
local 08:00 rather than shifting the string. That's the intent, and it's why
nothing about `meds_active` needs migrating on a move.

Changing the timezone must reach three places that cached it, which is exactly
what this class exists to keep in sync:

1. `Settings.tz` — every handler reads `settings.tz` for `subjective_today()`,
   so mutating the shared instance updates all of them at once (a deliberate
   choice over threading a provider object through ~17 call sites).
2. `TodoistClient.tz` — its subjective-day window for the Activity Log.
3. The APScheduler cron triggers — re-registered by a subscriber hook, since a
   `CronTrigger`'s timezone is fixed when the job is added.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rutix.db.models import UserPref
from rutix.integrations.todoist import TodoistClient
from rutix.settings import Settings

logger = logging.getLogger(__name__)

_PREF_ID = 1

# (old_tz, new_tz) -> awaitable. Registered by make_scheduler to re-point cron
# triggers and replay reminders the shift skipped past.
ChangeHook = Callable[[str, str], Awaitable[None]]


def is_valid_tz(name: str) -> bool:
    """True if `name` is a zone the stdlib can resolve (e.g. 'Asia/Bishkek')."""
    if not name or not name.strip():
        return False
    try:
        ZoneInfo(name.strip())
    except Exception:
        return False
    return True


class TimezoneManager:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        todoist: TodoistClient,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._todoist = todoist
        self._tz = settings.tz
        self._hooks: list[ChangeHook] = []

    @property
    def tz(self) -> str:
        return self._tz

    def now(self) -> datetime:
        return datetime.now(ZoneInfo(self._tz))

    def subscribe(self, hook: ChangeHook) -> None:
        """Register a coroutine to run after every applied timezone change."""
        self._hooks.append(hook)

    async def _row(self, session: AsyncSession) -> UserPref | None:
        return (
            await session.scalars(select(UserPref).where(UserPref.id == _PREF_ID))
        ).one_or_none()

    async def load(self) -> str:
        """Read the stored timezone, seeding the row from `TZ` on first run.

        Called once at startup, before the scheduler is built, so the cron
        triggers are registered in the persisted zone rather than the env one.
        """
        async with self._session_factory() as session:
            row = await self._row(session)
            if row is None:
                row = UserPref(id=_PREF_ID, tz=self._settings.tz)
                session.add(row)
                await session.commit()
                logger.info("tz: seeded user_prefs from TZ env (%s)", self._settings.tz)
            self._tz = row.tz
        self._apply_in_process(self._tz)
        return self._tz

    def _apply_in_process(self, tz: str) -> None:
        self._tz = tz
        self._settings.tz = tz
        self._todoist.tz = tz

    async def set_tz(self, new_tz: str) -> bool:
        """Persist and apply a timezone. Returns True if the zone actually moved.

        Raises ValueError on a non-IANA name. `get_user_timezone` already
        validates what it returns; this is the backstop for any other caller.
        """
        name = new_tz.strip()
        if not is_valid_tz(name):
            raise ValueError(f"unknown timezone: {new_tz!r}")

        old = self._tz
        if name == old:
            return False

        async with self._session_factory() as session:
            row = await self._row(session)
            if row is None:
                row = UserPref(id=_PREF_ID, tz=name)
                session.add(row)
            row.tz = name
            await session.commit()

        self._apply_in_process(name)
        logger.info("tz: %s -> %s", old, name)
        for hook in self._hooks:
            try:
                await hook(old, name)
            except Exception:
                logger.exception("tz: change hook failed")
        return True

    async def sync_from_todoist(self) -> str | None:
        """Pull the timezone from the Todoist profile and apply it if it moved.

        Returns the new zone when it changed, else None. Never raises — a
        Todoist outage leaves the current zone in place (see
        `get_user_timezone`, which returns None rather than propagating).
        """
        remote = await self._todoist.get_user_timezone()
        if remote is None or remote == self._tz:
            return None
        await self.set_tz(remote)
        return remote
