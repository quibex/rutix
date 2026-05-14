from datetime import date, datetime
from zoneinfo import ZoneInfo

from rutix.time_utils import (
    days_of_week,
    is_saturday,
    is_sunday,
    subjective_today,
    week_id,
    yesterday_of,
)

MSK = ZoneInfo("Europe/Moscow")


def test_subjective_today_morning_returns_calendar_day():
    """After 03:00 — the new day has started."""
    now = datetime(2026, 5, 14, 10, 0, tzinfo=MSK)
    assert subjective_today(now) == date(2026, 5, 14)


def test_subjective_today_late_night_returns_yesterday():
    """01:00 → still yesterday (user hasn't slept yet)."""
    now = datetime(2026, 5, 14, 1, 0, tzinfo=MSK)
    assert subjective_today(now) == date(2026, 5, 13)


def test_subjective_today_just_before_3am_returns_yesterday():
    """02:59 — still yesterday."""
    now = datetime(2026, 5, 14, 2, 59, tzinfo=MSK)
    assert subjective_today(now) == date(2026, 5, 13)


def test_subjective_today_at_exactly_3am_returns_today():
    """03:00 sharp — new day has begun."""
    now = datetime(2026, 5, 14, 3, 0, tzinfo=MSK)
    assert subjective_today(now) == date(2026, 5, 14)


def test_subjective_today_handles_utc_input():
    # 00:00 UTC == 03:00 MSK — the boundary
    now = datetime(2026, 5, 14, 0, 0, tzinfo=ZoneInfo("UTC"))
    assert subjective_today(now) == date(2026, 5, 14)


def test_yesterday_of():
    assert yesterday_of(date(2026, 5, 14)) == date(2026, 5, 13)
    # Cross-month
    assert yesterday_of(date(2026, 6, 1)) == date(2026, 5, 31)


def test_is_saturday():
    assert is_saturday(date(2026, 5, 16))
    assert not is_saturday(date(2026, 5, 14))


def test_is_sunday():
    assert is_sunday(date(2026, 5, 17))
    assert not is_sunday(date(2026, 5, 14))


def test_week_id_iso_format():
    assert week_id(date(2026, 5, 14)) == "2026-W20"
    # First week of January edge case
    assert week_id(date(2026, 1, 5)) == "2026-W02"


def test_days_of_week_returns_mon_to_sun():
    days = days_of_week(date(2026, 5, 14))  # Thursday
    assert days[0] == date(2026, 5, 11)
    assert days[6] == date(2026, 5, 17)
    assert len(days) == 7


def test_days_of_week_when_sunday_input():
    days = days_of_week(date(2026, 5, 17))  # Sunday
    assert days[0] == date(2026, 5, 11)
    assert days[6] == date(2026, 5, 17)
