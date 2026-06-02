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
   schedule to always look fresh on entry: anything left over from yesterday or
   earlier rolls to **today**, so the missed item resurfaces in today's plan.

   Recurrence is preserved by the write path (`TodoistClient.update_task_due_date`
   re-anchors recurring tasks via the Sync API instead of flattening them into
   one-shots), so we can move recurring tasks here too — only the anchor date
   moves, the cadence stays.

The job is idempotent: updates are only sent when the computed target differs
from the task's current due.date.
"""

import logging
from datetime import date, datetime, time, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

from rutix.integrations.todoist import TodoistClient
from rutix.time_utils import EARLY_MORNING_BOUNDARY

logger = logging.getLogger(__name__)


class RescheduleResult(NamedTuple):
    pulled_back: list[str]  # task content strings, for the summary
    pushed_forward: list[str]
    errors: list[str]  # task content + error message


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
    """Date this overdue task should move to, or None to leave it alone.

    Anything due *before* today (left over from yesterday or earlier) rolls to
    **today** so it resurfaces in today's plan. Tasks due today or later — and
    tasks with no due date — are untouched.

    Recurring tasks are moved too: the write path re-anchors them via the Sync
    API and keeps the cadence, so moving an overdue habit to today no longer
    flattens it into a one-shot.
    """
    current = _parse_due_date(task.get("due"))
    if current is None or current >= today:
        return None
    return today


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
            await todoist.update_task_due_date(task_id, target, due=task.get("due"))
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
        if target is None:
            continue
        current = _parse_due_date(task.get("due"))
        if current == target:
            continue
        try:
            await todoist.update_task_due_date(task_id, target, due=task.get("due"))
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
        errors=errors,
    )
