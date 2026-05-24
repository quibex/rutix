"""3am-cron step that keeps Todoist's task list in sync with the user's
subjective day.

Two distinct corrections:

1) **Pull-back** for the post-midnight recurrence bug. Todoist treats midnight
   as the day boundary, so a recurring habit ticked at 02:00 fires its next
   instance from "today" (calendar) — i.e. tomorrow's calendar day. But the
   user did it during their subjective *yesterday*, so the next instance should
   land on subjective *today*, not tomorrow. We detect this (task completed in
   [today 00:00, today 03:00) local + due.date == today+1) and pull the
   due_date back by one day.

2) **Push-forward** for anything still overdue at 03:00. The user wants their
   schedule to always look fresh on entry: anything left from yesterday and
   anything left from today should appear "tomorrow" on the right cadence.

   Per-task target date:
   - Recurring "every N days" (N ≥ 2): today + N.
   - Daily / non-recurring / unparseable recurrence: today + 1.

Weekly/monthly recurrences (e.g. "every Monday", "every 1st of month") are NOT
moved in v1 — leaving them alone is safer than guessing the next valid date.

The job is idempotent: updates are only sent when the computed target differs
from the task's current due.date.
"""

import logging
import re
from datetime import date, datetime, time, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

from rutix.integrations.todoist import TodoistClient
from rutix.time_utils import EARLY_MORNING_BOUNDARY

logger = logging.getLogger(__name__)


class RescheduleResult(NamedTuple):
    pulled_back: list[str]  # task content strings, for the summary
    pushed_forward: list[str]
    skipped: list[str]  # tasks we deliberately didn't touch (e.g. weekly recurrence)
    errors: list[str]  # task content + error message


# --- Recurrence parsing -----------------------------------------------------

# Recognise English + Russian "every N days" cadences. Anything else is left to
# the "tomorrow" fallback so we don't accidentally move a "every Monday" task to
# an arbitrary weekday.
_EVERY_DAY_RE = re.compile(r"^\s*(?:every\s+day|ежедневно|каждый\s+день)\b", re.IGNORECASE)
_EVERY_N_DAYS_RE = re.compile(
    r"^\s*(?:every\s+(\d+)\s+days?|кажд(?:ые|ый|ая|ого)\s+(\d+)\s+(?:дня|дней|день))\b",
    re.IGNORECASE,
)


def parse_recurrence_days(due_string: str | None) -> int | None:
    """Recurrence period in days for a Todoist `due.string`. None if the cadence
    isn't a fixed-day interval we understand (callers should fall back to a
    safe default)."""
    if not due_string:
        return None
    if _EVERY_DAY_RE.match(due_string):
        return 1
    m = _EVERY_N_DAYS_RE.match(due_string)
    if m:
        return int(m.group(1) or m.group(2))
    return None


def _parse_due_date(due: dict | None) -> date | None:
    """Extract YYYY-MM-DD from a Todoist task's due object. Returns None if the
    task has no due date or the date string is malformed."""
    if not due:
        return None
    s = due.get("date")
    if not s:
        return None
    # `due.date` is either YYYY-MM-DD (full-day) or YYYY-MM-DDTHH:MM:SS (timed).
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


# --- Per-task decisions -----------------------------------------------------


def compute_push_forward_target(task: dict, today: date) -> date | None:
    """Date this overdue task should move to. None = leave alone (e.g. weekly
    recurrence we can't safely rebase, or the task isn't actually overdue)."""
    current = _parse_due_date(task.get("due"))
    if current is None or current >= today:
        return None  # not overdue
    due = task.get("due") or {}
    if due.get("is_recurring"):
        days = parse_recurrence_days(due.get("string"))
        if days is None:
            # Recurring with a cadence we don't understand (weekly, monthly,
            # weekday-specific). Skip rather than guess.
            return None
        # `days == 1` (daily) → tomorrow. `days >= 2` → today + days.
        return today + timedelta(days=max(days, 1))
    # Non-recurring overdue → tomorrow.
    return today + timedelta(days=1)


def compute_pull_back_target(task: dict, today: date) -> date | None:
    """If this recurring task's due.date is exactly today+1 — the symptom of a
    post-midnight completion — return today. Else None (don't touch)."""
    due = task.get("due") or {}
    if not due.get("is_recurring"):
        return None
    current = _parse_due_date(due)
    if current is None:
        return None
    if current != today + timedelta(days=1):
        return None
    return today


# --- Job entrypoint ---------------------------------------------------------


async def reschedule_overdue(todoist: TodoistClient, today: date) -> RescheduleResult:
    """Run both corrections against Todoist. `today` is the subjective day at
    the time of the 3am run."""
    target_tz = ZoneInfo(todoist.tz)

    # Pull-back candidates: recurring tasks completed between today 00:00 and
    # today 03:00 local. Anything completed at/after 03:00 is the new subjective
    # day's work and doesn't need rebasing.
    post_midnight_start = datetime.combine(today, time(0, 0), tzinfo=target_tz)
    post_midnight_end = datetime.combine(today, EARLY_MORNING_BOUNDARY, tzinfo=target_tz)
    pulled_back: list[str] = []
    pushed_forward: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    pull_back_ids: set[str] = set()
    try:
        pull_back_ids = await todoist.completed_task_ids_between(
            post_midnight_start, post_midnight_end
        )
    except Exception as e:
        # Activity-log failure doesn't block push-forward — log and proceed.
        logger.exception("reschedule_overdue: pull-back candidate fetch failed")
        errors.append(f"activity-log: {type(e).__name__}: {e}")

    for task_id in pull_back_ids:
        try:
            task = await todoist.fetch_task(task_id)
        except Exception as e:
            logger.exception("reschedule_overdue: fetch_task(%s) failed", task_id)
            errors.append(f"fetch {task_id}: {type(e).__name__}: {e}")
            continue
        if task is None:
            continue
        target = compute_pull_back_target(task, today)
        if target is None:
            continue
        try:
            await todoist.update_task_due_date(task_id, target)
        except Exception as e:
            logger.exception("reschedule_overdue: pull-back update for %s failed", task_id)
            errors.append(f"pull-back {task.get('content', task_id)}: {type(e).__name__}: {e}")
            continue
        pulled_back.append(task.get("content") or task_id)
        logger.info(
            "reschedule_overdue: pulled back %r from %s to %s",
            task.get("content"),
            _parse_due_date(task.get("due")),
            target,
        )

    # Push-forward: scan all active tasks, update anything with due.date < today.
    try:
        active = await todoist.fetch_active_tasks()
    except Exception as e:
        logger.exception("reschedule_overdue: fetch_active_tasks failed")
        errors.append(f"fetch active: {type(e).__name__}: {e}")
        active = []

    for task in active:
        task_id = task.get("id")
        if not task_id:
            continue
        target = compute_push_forward_target(task, today)
        current = _parse_due_date(task.get("due"))
        if current is not None and current < today and target is None:
            # Overdue but we couldn't compute a target — recurrence we don't
            # parse. Surface in the summary so the user can fix it manually.
            skipped.append(task.get("content") or task_id)
            continue
        if target is None or current == target:
            continue
        try:
            await todoist.update_task_due_date(task_id, target)
        except Exception as e:
            logger.exception("reschedule_overdue: push-forward update for %s failed", task_id)
            errors.append(f"push {task.get('content', task_id)}: {type(e).__name__}: {e}")
            continue
        pushed_forward.append(task.get("content") or task_id)
        logger.info(
            "reschedule_overdue: pushed %r from %s to %s",
            task.get("content"),
            current,
            target,
        )

    return RescheduleResult(
        pulled_back=sorted(pulled_back),
        pushed_forward=sorted(pushed_forward),
        skipped=sorted(skipped),
        errors=errors,
    )
