"""Runtime schedule prefs — which cron jobs run, and when.

The bot's crons used to be hardcoded in `scheduler.py`. They still declare
their *defaults* there-ish (the `JOB_SPECS` registry below), but the live
schedule is whatever the `job_prefs` table says: the user turns a job off or
moves its time from `/schedule`, and the change applies immediately — no
restart, no redeploy.

Shape mirrors `tz_manager.py` on purpose:

- one owner for the value (`JobPrefsManager`),
- a `subscribe()` hook that `make_scheduler` uses to re-register the affected
  APScheduler job, since a `CronTrigger` is immutable once added,
- an absent DB row means "the default", so a newly added job needs no backfill
  and an untouched install behaves exactly as before.

`hour`/`minute` are strings all the way down because they are passed to
`CronTrigger`, which accepts wildcards ("*") as well as plain numbers — that's
how `tz_sync` (hourly at :07) and `med_reminder_tick` (every minute) fit the
same registry as the daily jobs.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rutix.db.models import JobPref

logger = logging.getLogger(__name__)

# (job_id) -> awaitable. Registered by make_scheduler to re-point one cron.
ChangeHook = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class JobSpec:
    """A schedulable job: its identity, its shipped default schedule, and how
    it's described to the user in `/schedule`."""

    job_id: str
    label: str
    description: str
    hour: str
    minute: str
    # False for the two polling jobs (every-minute tick, hourly tz check) —
    # they can be switched off, but "at what time" is meaningless for them.
    time_configurable: bool = True

    @property
    def default_time_label(self) -> str:
        return format_time_label(self.hour, self.minute)


def format_time_label(hour: str, minute: str) -> str:
    """Human-readable Russian label for a cron hour/minute pair."""
    if hour == "*" and minute == "*":
        return "каждую минуту"
    if hour == "*":
        return f"каждый час в :{int(minute):02d}"
    return f"{int(hour):02d}:{int(minute):02d}"


JOB_SPECS: list[JobSpec] = [
    JobSpec(
        job_id="daily_3am",
        label="🌅 Ночная сводка",
        description="закрывает вчерашний день: flush в Obsidian, привычки, перенос задач",
        hour="3",
        minute="0",
    ),
    JobSpec(
        job_id="update_habits_retry_06",
        label="🔁 Повтор привычек (утро)",
        description="вторая попытка, если ночью Todoist не ответил",
        hour="6",
        minute="0",
    ),
    JobSpec(
        job_id="update_habits_retry_08",
        label="🔁 Повтор привычек (финал)",
        description="последняя попытка; пишет, только если что-то изменилось или упало",
        hour="8",
        minute="0",
    ),
    JobSpec(
        job_id="daily_plan_ping",
        label="🗓 План на день",
        description="присылает «🗓 План на день» из daily-файла",
        hour="9",
        minute="0",
    ),
    JobSpec(
        job_id="tz_sync",
        label="🌍 Часовой пояс",
        description="сверяет часовой пояс с Todoist раз в час",
        hour="*",
        minute="7",
        time_configurable=False,
    ),
    JobSpec(
        job_id="med_reminder_tick",
        label="💊 Напоминания о лекарствах",
        description="проверка раз в минуту; время каждого препарата — в /meds",
        hour="*",
        minute="*",
        time_configurable=False,
    ),
]

JOB_SPEC_BY_ID: dict[str, JobSpec] = {spec.job_id: spec for spec in JOB_SPECS}


@dataclass(frozen=True)
class JobSetting:
    """A spec merged with whatever the user overrode."""

    spec: JobSpec
    enabled: bool
    hour: str
    minute: str

    @property
    def job_id(self) -> str:
        return self.spec.job_id

    @property
    def time_label(self) -> str:
        return format_time_label(self.hour, self.minute)

    @property
    def is_default_time(self) -> bool:
        return self.hour == self.spec.hour and self.minute == self.spec.minute


def _default_setting(spec: JobSpec) -> JobSetting:
    return JobSetting(spec=spec, enabled=True, hour=spec.hour, minute=spec.minute)


class UnknownJobError(KeyError):
    """Raised for a job_id that isn't in the registry (stale callback data)."""


class JobPrefsManager:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._settings: dict[str, JobSetting] = {
            spec.job_id: _default_setting(spec) for spec in JOB_SPECS
        }
        self._hooks: list[ChangeHook] = []

    def subscribe(self, hook: ChangeHook) -> None:
        """Register a coroutine run after every applied change, with the job_id."""
        self._hooks.append(hook)

    async def load(self) -> None:
        """Read stored overrides into memory. Called once at startup, before the
        scheduler is built, so jobs register on the user's schedule rather than
        the shipped one."""
        async with self._session_factory() as session:
            rows = (await session.scalars(select(JobPref))).all()

        for row in rows:
            spec = JOB_SPEC_BY_ID.get(row.job_id)
            if spec is None:
                # A job that was renamed or removed in a later version. Harmless
                # leftover — the row just stops being read.
                logger.info("job_prefs: ignoring row for unknown job %s", row.job_id)
                continue
            self._settings[spec.job_id] = JobSetting(
                spec=spec,
                enabled=row.enabled,
                hour=row.hour if row.hour is not None else spec.hour,
                minute=row.minute if row.minute is not None else spec.minute,
            )

        off = [s.job_id for s in self._settings.values() if not s.enabled]
        logger.info("job_prefs loaded: %d override row(s), disabled=%s", len(rows), off or "none")

    def get(self, job_id: str) -> JobSetting:
        try:
            return self._settings[job_id]
        except KeyError as e:
            raise UnknownJobError(job_id) from e

    def all(self) -> list[JobSetting]:
        """Settings in registry order — the order `/schedule` renders them in."""
        return [self._settings[spec.job_id] for spec in JOB_SPECS]

    async def _persist(self, setting: JobSetting) -> None:
        async with self._session_factory() as session:
            row = await session.get(JobPref, setting.job_id)
            if row is None:
                row = JobPref(job_id=setting.job_id)
                session.add(row)
            row.enabled = setting.enabled
            # Store the resolved values rather than NULL-for-default: a later
            # change to a shipped default must not silently move a job the user
            # already reviewed on this screen.
            row.hour = setting.hour
            row.minute = setting.minute
            await session.commit()

    async def _apply(self, setting: JobSetting) -> JobSetting:
        self._settings[setting.job_id] = setting
        await self._persist(setting)
        for hook in self._hooks:
            try:
                await hook(setting.job_id)
            except Exception:
                logger.exception("job_prefs: change hook failed for %s", setting.job_id)
        return setting

    async def set_enabled(self, job_id: str, enabled: bool) -> JobSetting:
        current = self.get(job_id)
        logger.info("job_prefs: %s enabled %s -> %s", job_id, current.enabled, enabled)
        return await self._apply(
            JobSetting(spec=current.spec, enabled=enabled, hour=current.hour, minute=current.minute)
        )

    async def toggle(self, job_id: str) -> JobSetting:
        return await self.set_enabled(job_id, not self.get(job_id).enabled)

    async def set_time(self, job_id: str, hour: str, minute: str) -> JobSetting:
        """Move a job to a new wall-clock time. Raises ValueError for the
        polling jobs, whose schedule isn't a time of day."""
        current = self.get(job_id)
        if not current.spec.time_configurable:
            raise ValueError(f"job {job_id} has no configurable time")
        logger.info(
            "job_prefs: %s time %s:%s -> %s:%s",
            job_id,
            current.hour,
            current.minute,
            hour,
            minute,
        )
        return await self._apply(
            JobSetting(spec=current.spec, enabled=current.enabled, hour=hour, minute=minute)
        )
