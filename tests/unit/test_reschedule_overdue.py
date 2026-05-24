from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from rutix.jobs.reschedule_overdue import (
    compute_pull_back_target,
    compute_push_forward_target,
    parse_recurrence_days,
    reschedule_overdue,
)


# parse_recurrence_days


@pytest.mark.parametrize(
    "due_string,expected",
    [
        ("every day", 1),
        ("Every Day", 1),
        ("ежедневно", 1),
        ("каждый день", 1),
        ("every 2 days", 2),
        ("every 7 days", 7),
        ("каждые 3 дня", 3),
        ("каждые 5 дней", 5),
        # Unsupported cadences — explicit None so caller knows to skip
        ("every Monday", None),
        ("every other week", None),
        ("monthly on the 1st", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_recurrence_days(due_string, expected):
    assert parse_recurrence_days(due_string) == expected


# compute_push_forward_target

TODAY = date(2026, 5, 24)


def _task(due_date=None, due_string=None, is_recurring=False, content="t"):
    due = None
    if due_date or due_string:
        due = {
            "date": due_date,
            "string": due_string,
            "is_recurring": is_recurring,
        }
    return {"id": "1", "content": content, "due": due}


def test_push_forward_non_recurring_overdue_moves_to_tomorrow():
    t = _task(due_date="2026-05-22", is_recurring=False)
    assert compute_push_forward_target(t, TODAY) == date(2026, 5, 25)


def test_push_forward_non_overdue_returns_none():
    """Tasks due today or later are not touched — the user might still do them today."""
    assert compute_push_forward_target(_task(due_date="2026-05-24"), TODAY) is None
    assert compute_push_forward_target(_task(due_date="2026-06-01"), TODAY) is None


def test_push_forward_daily_recurring_moves_to_tomorrow():
    t = _task(due_date="2026-05-22", due_string="every day", is_recurring=True)
    assert compute_push_forward_target(t, TODAY) == date(2026, 5, 25)


def test_push_forward_every_2_days_moves_to_today_plus_2():
    """User's exact example: 'every 2 days' overdue today should move to +2, not +1."""
    t = _task(due_date="2026-05-22", due_string="every 2 days", is_recurring=True)
    assert compute_push_forward_target(t, TODAY) == date(2026, 5, 26)


def test_push_forward_every_7_days_moves_to_today_plus_7():
    t = _task(due_date="2026-05-15", due_string="every 7 days", is_recurring=True)
    assert compute_push_forward_target(t, TODAY) == date(2026, 5, 31)


def test_push_forward_unparseable_recurrence_returns_none():
    """Weekly / monthly — don't guess. Surface to user as 'skipped' instead."""
    t = _task(due_date="2026-05-20", due_string="every Monday", is_recurring=True)
    assert compute_push_forward_target(t, TODAY) is None


def test_push_forward_task_with_no_due_returns_none():
    assert compute_push_forward_target({"id": "1", "content": "no due"}, TODAY) is None


def test_push_forward_handles_timed_due_date():
    """due.date can be 'YYYY-MM-DDTHH:MM:SS' for timed tasks — only the date part matters."""
    t = _task(due_date="2026-05-22T09:00:00", is_recurring=False)
    assert compute_push_forward_target(t, TODAY) == date(2026, 5, 25)


# compute_pull_back_target


def test_pull_back_recurring_due_tomorrow_returns_today():
    """Symptom of post-midnight recurrence: due was rolled to today+1."""
    t = _task(due_date="2026-05-25", due_string="every day", is_recurring=True)
    assert compute_pull_back_target(t, TODAY) == date(2026, 5, 24)


def test_pull_back_non_recurring_returns_none():
    """Non-recurring tasks don't have the post-midnight recurrence bug."""
    t = _task(due_date="2026-05-25", is_recurring=False)
    assert compute_pull_back_target(t, TODAY) is None


def test_pull_back_recurring_due_today_returns_none():
    t = _task(due_date="2026-05-24", due_string="every day", is_recurring=True)
    assert compute_pull_back_target(t, TODAY) is None


def test_pull_back_recurring_due_far_future_returns_none():
    """Not the +1 day pattern — must be a different cause (user manually moved, etc.)."""
    t = _task(due_date="2026-06-01", due_string="every day", is_recurring=True)
    assert compute_pull_back_target(t, TODAY) is None


def test_pull_back_no_due_returns_none():
    assert compute_pull_back_target({"id": "1"}, TODAY) is None


# reschedule_overdue (job-level) — uses a fake TodoistClient


class FakeTodoist:
    """In-memory fake for the methods reschedule_overdue uses. Records writes."""

    def __init__(self, tz="Europe/Moscow"):
        self.tz = tz
        self.completed_ids: set[str] = set()  # post-midnight completions to return
        self.tasks: dict[str, dict] = {}  # task_id → task dict
        self.updates: list[tuple[str, date]] = []  # (task_id, new_date) writes

    async def completed_task_ids_between(self, start, end):
        return set(self.completed_ids)

    async def fetch_task(self, task_id):
        return self.tasks.get(task_id)

    async def fetch_active_tasks(self):
        return list(self.tasks.values())

    async def update_task_due_date(self, task_id, new_date):
        self.updates.append((task_id, new_date))
        # Reflect the write so subsequent reads see the new state.
        task = self.tasks.get(task_id)
        if task and task.get("due"):
            task["due"]["date"] = new_date.isoformat()


async def test_reschedule_pull_back_for_post_midnight_recurring():
    todoist = FakeTodoist()
    todoist.tasks["1"] = _task(
        due_date="2026-05-25", due_string="every day", is_recurring=True, content="Anki"
    )
    todoist.tasks["1"]["id"] = "1"
    todoist.completed_ids = {"1"}

    result = await reschedule_overdue(todoist, TODAY)

    assert ("1", date(2026, 5, 24)) in todoist.updates
    assert result.pulled_back == ["Anki"]
    assert result.errors == []


async def test_reschedule_push_forward_for_overdue_non_recurring():
    todoist = FakeTodoist()
    todoist.tasks["t1"] = {
        "id": "t1",
        "content": "Купить хлеб",
        "due": {"date": "2026-05-22", "string": None, "is_recurring": False},
    }

    result = await reschedule_overdue(todoist, TODAY)

    assert ("t1", date(2026, 5, 25)) in todoist.updates
    assert result.pushed_forward == ["Купить хлеб"]


async def test_reschedule_push_forward_every_2_days():
    todoist = FakeTodoist()
    todoist.tasks["t1"] = {
        "id": "t1",
        "content": "Английский",
        "due": {"date": "2026-05-22", "string": "every 2 days", "is_recurring": True},
    }

    result = await reschedule_overdue(todoist, TODAY)

    assert ("t1", date(2026, 5, 26)) in todoist.updates
    assert result.pushed_forward == ["Английский"]


async def test_reschedule_skips_unparseable_recurrence():
    todoist = FakeTodoist()
    todoist.tasks["t1"] = {
        "id": "t1",
        "content": "Стрижка",
        "due": {"date": "2026-05-20", "string": "every 2 weeks", "is_recurring": True},
    }

    result = await reschedule_overdue(todoist, TODAY)

    assert todoist.updates == []
    assert "Стрижка" in result.skipped


async def test_reschedule_idempotent_no_writes_when_dates_correct():
    """Re-running on already-correct state must not POST anything to Todoist."""
    todoist = FakeTodoist()
    todoist.tasks["t1"] = {
        "id": "t1",
        "content": "Future task",
        "due": {"date": "2026-05-30", "string": None, "is_recurring": False},
    }

    result = await reschedule_overdue(todoist, TODAY)

    assert todoist.updates == []
    assert result.pulled_back == []
    assert result.pushed_forward == []


async def test_reschedule_fetch_active_failure_is_isolated():
    """Push-forward fetch failure must not lose pull-back work and vice versa."""
    todoist = FakeTodoist()
    todoist.fetch_active_tasks = AsyncMock(side_effect=RuntimeError("boom"))
    todoist.tasks["1"] = _task(
        due_date="2026-05-25", due_string="every day", is_recurring=True, content="Anki"
    )
    todoist.tasks["1"]["id"] = "1"
    todoist.completed_ids = {"1"}

    result = await reschedule_overdue(todoist, TODAY)

    assert result.pulled_back == ["Anki"]  # pull-back still happened
    assert any("fetch active" in e for e in result.errors)


async def test_reschedule_pull_back_uses_fresh_due_date_from_fetch():
    """The completion event has no due-date info; we MUST refetch the task to
    decide. This catches a regression where the rescheduler reads stale data."""
    todoist = FakeTodoist()
    real_fetch = MagicMock()
    real_fetch.id = "1"

    todoist.tasks["1"] = {
        "id": "1",
        "content": "Anki",
        "due": {"date": "2026-05-25", "string": "every day", "is_recurring": True},
    }
    todoist.completed_ids = {"1"}

    await reschedule_overdue(todoist, TODAY)
    # The task was fetched, then updated.
    assert todoist.updates == [("1", date(2026, 5, 24))]
