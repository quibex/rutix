"""APScheduler — recurring jobs:

- daily_3am (03:00): flush_day(yesterday) + update_habits(yesterday) + reschedule_overdue(today)
- update_habits_retry (06:00, 08:00): re-run update_habits(yesterday) if 03:00 lost
  it to a transient Todoist outage. Idempotent — silent unless it actually changes
  something or the 08:00 final attempt still errors.
- daily_plan_ping (09:00): post today's `## 🗓 План на день` to the user.
- med_reminder_tick (every minute): per-pill reminder — fires for meds whose
  `reminder_time` matches the current minute. Silent unless something is due.
- tz_sync (hourly at :07): pulls the timezone from the Todoist profile so the
  whole schedule follows the user when they travel. Silent unless it moves.

Every cron below is registered in the *current* timezone rather than a fixed
one. A `CronTrigger`'s timezone is frozen when the job is added, so a timezone
change has to re-register them — the `on_tz_change` hook does that from the
same `_CronJob` list used to add them, so the two can't drift apart.
"""

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rutix.integrations.claude import ClaudeClient
from rutix.integrations.github import GitHubClient
from rutix.integrations.todoist import TodoistClient
from rutix.jobs.daily_plan import daily_plan_ping
from rutix.jobs.flush_day import flush_day
from rutix.jobs.med_reminder import catch_up_after_tz_change, med_reminder_tick
from rutix.jobs.reschedule_overdue import RescheduleResult, reschedule_overdue
from rutix.jobs.update_habits import UpdateHabitsResult, update_habits
from rutix.time_utils import subjective_today, yesterday_of
from rutix.tz_manager import TimezoneManager

logger = logging.getLogger(__name__)

_MAX_MARKED_IN_MESSAGE = 15


@dataclass(frozen=True)
class _CronJob:
    """A cron job's schedule, kept separate from the timezone it runs in so the
    same spec can re-register the job when the timezone changes."""

    job_id: str
    func: Callable[..., Coroutine[Any, Any, None]]
    hour: str | int
    minute: str | int
    kwargs: dict[str, Any] = field(default_factory=dict)

    def trigger(self, tz: str) -> CronTrigger:
        return CronTrigger(hour=self.hour, minute=self.minute, timezone=ZoneInfo(tz))


def build_tz_change_message(old_tz: str, new_tz: str, now: datetime) -> str:
    return (
        f"🌍 Часовой пояс: {old_tz} → {new_tz}\n"
        f"Определил по Todoist. Локальное время сейчас {now.strftime('%H:%M')}.\n"
        "Напоминания и ночные джобы пересчитаны — время в них осталось прежним "
        "(08:00 значит 8 утра там, где ты)."
    )


def _fmt_sha(sha: str | None) -> str:
    return f" ({sha[:7]})" if sha else ""


def _fmt_flush_day_line(target_iso: str, result: "str | Exception | None") -> str:
    if isinstance(result, Exception):
        return f"⚠️ flush_day за {target_iso}: ошибка — {type(result).__name__}: {result}"
    if result is None:
        return f"⏭ flush_day за {target_iso}: пропущено (нет данных или уже записано)"
    return f"✅ flush_day за {target_iso}: записал{_fmt_sha(result)}"


def _habit_word(n: int) -> str:
    """Russian pluralization for 'привычка'."""
    if n % 10 == 1 and n % 100 != 11:
        return "привычку"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "привычки"
    return "привычек"


_SKIP_REASON_TEXT = {
    "no_completions": "Todoist не вернул завершённых задач",
    "no_daily_file": "нет daily-файла в репо",
    "no_op": "нечего менять (всё уже отмечено)",
}


def _fmt_update_habits_lines(
    target_iso: str, result: "UpdateHabitsResult | Exception"
) -> list[str]:
    if isinstance(result, Exception):
        return [f"⚠️ update_habits за {target_iso}: ошибка — {type(result).__name__}: {result}"]
    if result.sha is None:
        reason = _SKIP_REASON_TEXT.get(result.skip_reason or "", "нечего отмечать")
        return [f"⏭ update_habits за {target_iso}: {reason}"]
    n = len(result.marked)
    head = f"✅ update_habits за {target_iso}: отметил {n} {_habit_word(n)}"
    m = len(result.appended_done)
    if m:
        head += f" + {m} в Что сделано"
    head += _fmt_sha(result.sha)
    lines = [head]
    shown = result.marked[:_MAX_MARKED_IN_MESSAGE]
    for label in shown:
        lines.append(f"   • {label}")
    if len(result.marked) > _MAX_MARKED_IN_MESSAGE:
        lines.append(f"   … и ещё {len(result.marked) - _MAX_MARKED_IN_MESSAGE}")
    shown_done = result.appended_done[:_MAX_MARKED_IN_MESSAGE]
    for title in shown_done:
        lines.append(f"   ↳ {title}")
    if len(result.appended_done) > _MAX_MARKED_IN_MESSAGE:
        lines.append(f"   ↳ … и ещё {len(result.appended_done) - _MAX_MARKED_IN_MESSAGE}")
    return lines


_MAX_LISTED_RESCHEDULES = 10


def _fmt_reschedule_lines(result: "RescheduleResult | Exception") -> list[str]:
    if isinstance(result, Exception):
        return [f"⚠️ reschedule: ошибка — {type(result).__name__}: {result}"]
    lines: list[str] = []
    if result.pulled_back:
        lines.append(f"⏪ pull-back: {len(result.pulled_back)} задач(и)")
        for c in result.pulled_back[:_MAX_LISTED_RESCHEDULES]:
            lines.append(f"   • {c}")
        if len(result.pulled_back) > _MAX_LISTED_RESCHEDULES:
            lines.append(f"   … и ещё {len(result.pulled_back) - _MAX_LISTED_RESCHEDULES}")
    if result.pushed_forward:
        lines.append(f"⏩ push-forward: {len(result.pushed_forward)} задач(и)")
        for c in result.pushed_forward[:_MAX_LISTED_RESCHEDULES]:
            lines.append(f"   • {c}")
        if len(result.pushed_forward) > _MAX_LISTED_RESCHEDULES:
            lines.append(f"   … и ещё {len(result.pushed_forward) - _MAX_LISTED_RESCHEDULES}")
    if result.errors:
        lines.append(f"⚠️ reschedule: {len(result.errors)} ошибок")
        for msg in result.errors[:_MAX_LISTED_RESCHEDULES]:
            lines.append(f"   • {msg}")
    return lines


def build_retry_summary(
    target: date,
    result: "UpdateHabitsResult | Exception",
    is_final_attempt: bool,
) -> str | None:
    """Build a Telegram message for an update_habits catch-up run.

    Returns None when the retry should stay silent (no_op, no_completions,
    or non-final exception). Returns a formatted message otherwise.
    """
    target_iso = target.isoformat()
    if isinstance(result, Exception):
        if not is_final_attempt:
            return None
        return (
            f"⚠️ update_habits за {target_iso}: финальная попытка тоже упала — "
            f"{type(result).__name__}: {result}"
        )
    if result.sha is None:
        # skip_reason in {no_completions, no_daily_file, no_op} — nothing to report;
        # the 03:00 summary already informed the user about the original outcome.
        return None
    n = len(result.marked)
    m = len(result.appended_done)
    head = f"🔁 update_habits за {target_iso} (catch-up): отметил {n} {_habit_word(n)}"
    if m:
        head += f" + {m} в Что сделано"
    head += _fmt_sha(result.sha)
    lines = [head]
    for label in result.marked[:_MAX_MARKED_IN_MESSAGE]:
        lines.append(f"   • {label}")
    if len(result.marked) > _MAX_MARKED_IN_MESSAGE:
        lines.append(f"   … и ещё {len(result.marked) - _MAX_MARKED_IN_MESSAGE}")
    for title in result.appended_done[:_MAX_MARKED_IN_MESSAGE]:
        lines.append(f"   ↳ {title}")
    if len(result.appended_done) > _MAX_MARKED_IN_MESSAGE:
        lines.append(f"   ↳ … и ещё {len(result.appended_done) - _MAX_MARKED_IN_MESSAGE}")
    return "\n".join(lines)


def build_3am_summary(
    today: date,
    target: date,
    flush_day_outcome: "str | Exception | None",
    update_habits_outcome: "UpdateHabitsResult | Exception",
    reschedule_outcome: "RescheduleResult | Exception | None" = None,
) -> str:
    lines = [f"🌅 3am job: {today.isoformat()}"]
    lines.append(_fmt_flush_day_line(target.isoformat(), flush_day_outcome))
    lines.extend(_fmt_update_habits_lines(target.isoformat(), update_habits_outcome))
    if reschedule_outcome is not None:
        lines.extend(_fmt_reschedule_lines(reschedule_outcome))
    return "\n".join(lines)


def make_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    github: GitHubClient,
    todoist: TodoistClient,
    claude: ClaudeClient,
    bot: Bot,
    telegram_user_id: int,
    tz_manager: TimezoneManager,
) -> AsyncIOScheduler:
    # Read through the manager on every run — `tz` is a snapshot that goes stale
    # the moment the user travels, so jobs must never close over it.
    def tz_now() -> str:
        return tz_manager.tz

    scheduler = AsyncIOScheduler(timezone=ZoneInfo(tz_now()))

    async def daily_3am():
        tz = tz_now()
        today = subjective_today(datetime.now(ZoneInfo(tz)), tz)
        target = yesterday_of(today)
        logger.info("3am job running for target=%s today=%s", target, today)

        flush_day_outcome: str | Exception | None
        update_habits_outcome: UpdateHabitsResult | Exception
        reschedule_outcome: RescheduleResult | Exception

        async with session_factory() as session:
            try:
                flush_day_outcome = await flush_day(session, github, target)
                logger.info("flush_day result: %s", flush_day_outcome)
            except Exception as e:
                logger.exception("flush_day failed")
                flush_day_outcome = e

        try:
            update_habits_outcome = await update_habits(github, todoist, claude, target)
            logger.info("update_habits result: %s", update_habits_outcome)
        except Exception as e:
            logger.exception("update_habits failed")
            update_habits_outcome = e

        try:
            reschedule_outcome = await reschedule_overdue(todoist, today)
            logger.info("reschedule_overdue result: %s", reschedule_outcome)
        except Exception as e:
            logger.exception("reschedule_overdue failed")
            reschedule_outcome = e

        summary = build_3am_summary(
            today=today,
            target=target,
            flush_day_outcome=flush_day_outcome,
            update_habits_outcome=update_habits_outcome,
            reschedule_outcome=reschedule_outcome,
        )
        try:
            await bot.send_message(chat_id=telegram_user_id, text=summary)
        except Exception:
            logger.exception("failed to send 3am summary")

    async def update_habits_retry(is_final_attempt: bool):
        tz = tz_now()
        today = subjective_today(datetime.now(ZoneInfo(tz)), tz)
        target = yesterday_of(today)
        logger.info(
            "update_habits_retry running for target=%s (final=%s)", target, is_final_attempt
        )
        result: UpdateHabitsResult | Exception
        try:
            result = await update_habits(github, todoist, claude, target)
            logger.info("update_habits_retry result: %s", result)
        except Exception as e:
            logger.exception("update_habits_retry failed")
            result = e

        msg = build_retry_summary(target=target, result=result, is_final_attempt=is_final_attempt)
        if msg is None:
            return
        try:
            await bot.send_message(chat_id=telegram_user_id, text=msg)
        except Exception:
            logger.exception("failed to send update_habits_retry message")

    async def daily_plan():
        try:
            await daily_plan_ping(github, bot, telegram_user_id, tz_now())
        except Exception:
            logger.exception("daily_plan_ping failed")

    async def med_reminder():
        try:
            await med_reminder_tick(session_factory, bot, telegram_user_id, tz_now())
        except Exception:
            logger.exception("med_reminder_tick failed")

    async def tz_sync():
        """Hourly travel check. Silent unless the zone actually moved — the
        change hook below does the rescheduling and the user-facing message."""
        try:
            await tz_manager.sync_from_todoist()
        except Exception:
            logger.exception("tz_sync failed")

    cron_jobs = [
        _CronJob("daily_3am", daily_3am, 3, 0),
        _CronJob("update_habits_retry_06", update_habits_retry, 6, 0, {"is_final_attempt": False}),
        _CronJob("update_habits_retry_08", update_habits_retry, 8, 0, {"is_final_attempt": True}),
        _CronJob("daily_plan_ping", daily_plan, 9, 0),
        # :07 rather than :00 so the travel check doesn't land on the same
        # minute as the hour-aligned jobs above.
        _CronJob("tz_sync", tz_sync, "*", 7),
        _CronJob("med_reminder_tick", med_reminder, "*", "*"),
    ]

    for job in cron_jobs:
        scheduler.add_job(
            job.func,
            trigger=job.trigger(tz_now()),
            kwargs=job.kwargs,
            id=job.job_id,
            replace_existing=True,
        )

    async def on_tz_change(old_tz: str, new_tz: str) -> None:
        # The scheduler-level timezone is only a default for triggers that omit
        # one, and every trigger below sets it explicitly — so it needs no
        # update here (and `scheduler.configure()` would raise anyway: it only
        # accepts a stopped scheduler).
        for spec in cron_jobs:
            scheduler.reschedule_job(spec.job_id, trigger=spec.trigger(new_tz))
        logger.info("tz: rescheduled %d cron job(s) into %s", len(cron_jobs), new_tz)

        try:
            await bot.send_message(
                chat_id=telegram_user_id,
                text=build_tz_change_message(old_tz, new_tz, datetime.now(ZoneInfo(new_tz))),
            )
        except Exception:
            logger.exception("failed to send tz change message")

        # Do this after the message so the catch-up reads as a consequence of
        # the move rather than an unexplained reminder.
        try:
            await catch_up_after_tz_change(session_factory, bot, telegram_user_id, old_tz, new_tz)
        except Exception:
            logger.exception("med catch-up after tz change failed")

    tz_manager.subscribe(on_tz_change)
    return scheduler
