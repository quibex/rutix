from datetime import date, datetime
from zoneinfo import ZoneInfo

from rutix.time_utils import (
    days_of_week,
    extract_day_hint,
    is_saturday,
    is_sunday,
    parse_hours_text,
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


TODAY = date(2026, 5, 16)


def test_extract_day_hint_yesterday():
    day, rest = extract_day_hint("вчера онигири, паста", TODAY)
    assert day == date(2026, 5, 15)
    assert rest == "онигири, паста"


def test_extract_day_hint_day_before_yesterday():
    day, rest = extract_day_hint("позавчера шаурма", TODAY)
    assert day == date(2026, 5, 14)
    assert rest == "шаурма"


def test_extract_day_hint_today_is_noop_for_day_but_strips_word():
    day, rest = extract_day_hint("сегодня борщ", TODAY)
    assert day == TODAY
    assert rest == "борщ"


def test_extract_day_hint_case_insensitive():
    day, rest = extract_day_hint("Вчера паста", TODAY)
    assert day == date(2026, 5, 15)
    assert rest == "паста"


def test_extract_day_hint_with_comma_after():
    day, rest = extract_day_hint("вчера, паста", TODAY)
    assert day == date(2026, 5, 15)
    assert rest == "паста"


def test_extract_day_hint_only_hint():
    day, rest = extract_day_hint("вчера", TODAY)
    assert day == date(2026, 5, 15)
    assert rest == ""


def test_extract_day_hint_no_hint_leaves_text_intact():
    day, rest = extract_day_hint("онигири с тунцом", TODAY)
    assert day == TODAY
    assert rest == "онигири с тунцом"


def test_extract_day_hint_only_matches_at_start():
    # "вчера" mid-sentence is part of the food description, not a date hint.
    day, rest = extract_day_hint("ел вчера шаурму", TODAY)
    assert day == TODAY
    assert rest == "ел вчера шаурму"


def test_extract_day_hint_does_not_match_prefix_of_other_word():
    # "вчерашний" should not be treated as "вчера".
    day, rest = extract_day_hint("вчерашний хлеб", TODAY)
    assert day == TODAY
    assert rest == "вчерашний хлеб"


# --- parse_hours_text ---


def test_parse_hours_text_plain_number():
    assert parse_hours_text("1.5") == 1.5
    assert parse_hours_text("0") == 0.0
    assert parse_hours_text("2") == 2.0


def test_parse_hours_text_comma_decimal():
    assert parse_hours_text("1,5") == 1.5


def test_parse_hours_text_with_ch_suffix():
    assert parse_hours_text("2ч") == 2.0
    assert parse_hours_text("2 ч") == 2.0
    assert parse_hours_text("1.5ч") == 1.5
    assert parse_hours_text("3 часа") == 3.0
    assert parse_hours_text("5 часов") == 5.0


def test_parse_hours_text_english_suffix():
    assert parse_hours_text("2h") == 2.0
    assert parse_hours_text("1.5 hrs") == 1.5


def test_parse_hours_text_minutes_to_hours():
    assert parse_hours_text("30мин") == 0.5
    assert parse_hours_text("90 минут") == 1.5


def test_parse_hours_text_words():
    assert parse_hours_text("полтора") == 1.5
    assert parse_hours_text("полчаса") == 0.5
    assert parse_hours_text("Полтора") == 1.5


def test_parse_hours_text_returns_none_on_garbage():
    assert parse_hours_text("abc") is None
    assert parse_hours_text("") is None
    assert parse_hours_text("две банки") is None


def test_parse_hours_text_rejects_negative():
    assert parse_hours_text("-1") is None
