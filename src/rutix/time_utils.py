"""Time helpers — subjective day, week ids, weekday checks."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

EARLY_MORNING_BOUNDARY = time(5, 0)


def subjective_today(now: datetime, tz: str = "Europe/Moscow") -> date:
    """User's perceived 'today'.

    If local time is before 05:00, returns yesterday — the user hasn't slept yet.
    """
    local = now.astimezone(ZoneInfo(tz))
    if local.time() < EARLY_MORNING_BOUNDARY:
        return (local - timedelta(days=1)).date()
    return local.date()


def yesterday_of(d: date) -> date:
    return d - timedelta(days=1)


def is_saturday(d: date) -> bool:
    return d.weekday() == 5


def is_sunday(d: date) -> bool:
    return d.weekday() == 6


def week_id(d: date) -> str:
    """ISO week id like '2026-W19' (zero-padded week number)."""
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def days_of_week(any_day_of_week: date) -> list[date]:
    """Mon..Sun for the ISO week containing the given date."""
    monday = any_day_of_week - timedelta(days=any_day_of_week.weekday())
    return [monday + timedelta(days=i) for i in range(7)]
