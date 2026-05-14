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


def test_subjective_today_after_5am_returns_calendar_day():
    now = datetime(2026, 5, 14, 10, 0, tzinfo=MSK)
    assert subjective_today(now) == date(2026, 5, 14)


def test_subjective_today_before_5am_returns_yesterday():
    now = datetime(2026, 5, 14, 4, 30, tzinfo=MSK)
    assert subjective_today(now) == date(2026, 5, 13)


def test_subjective_today_at_exactly_5am_returns_today():
    now = datetime(2026, 5, 14, 5, 0, tzinfo=MSK)
    assert subjective_today(now) == date(2026, 5, 14)


def test_subjective_today_handles_utc_input():
    # 02:00 UTC == 05:00 MSK
    now = datetime(2026, 5, 14, 2, 0, tzinfo=ZoneInfo("UTC"))
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
