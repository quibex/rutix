"""Time helpers — subjective day, week ids, weekday checks."""

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

EARLY_MORNING_BOUNDARY = time(3, 0)

_DAY_HINT_OFFSETS = {
    "сегодня": 0,
    "вчера": -1,
    "позавчера": -2,
}
_DAY_HINT_RE = re.compile(
    r"^\s*(сегодня|вчера|позавчера)\b[\s,:.\-—]*",
    re.IGNORECASE,
)


def extract_day_hint(text: str, today: date) -> tuple[date, str]:
    """Detect a leading Russian day word ("вчера", "позавчера", "сегодня")
    and return (resolved_day, text_without_hint).

    Returns `(today, text)` unchanged if no hint is found.
    """
    m = _DAY_HINT_RE.match(text)
    if not m:
        return today, text
    offset = _DAY_HINT_OFFSETS[m.group(1).lower()]
    return today + timedelta(days=offset), text[m.end() :]


def subjective_today(now: datetime, tz: str = "Europe/Moscow") -> date:
    """User's perceived 'today'.

    If local time is before 03:00, returns yesterday — the user hasn't slept yet
    (the day "extends" past midnight by 3 hours).
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


# --- Free-text hour parser (for /track VPN/English) -------------------------

_WORD_HOURS = {
    "полтора": 1.5,
    "полчаса": 0.5,
    "полчасика": 0.5,
    "час": 1.0,
    "часик": 1.0,
    "два часа": 2.0,
    "три часа": 3.0,
    "ноль": 0.0,
}

_HOURS_RE = re.compile(
    r"^\s*(\d+(?:[.,]\d+)?)\s*(?:ч|h|hrs?|hours?|часа?|часов)?\.?\s*$",
    re.IGNORECASE,
)
_MINUTES_RE = re.compile(
    r"^\s*(\d+)\s*(?:мин|min|минут|минута|минуты)\.?\s*$",
    re.IGNORECASE,
)


# A day holds 24 hours — anything above this is not a duration-in-a-day but a
# misrouted reply (e.g. a "45"-minute med snooze typed while /track was still
# waiting on the English step). Reject it so it can't be recorded as 45ч.
MAX_HOURS_IN_DAY = 24.0


def parse_hours_text(text: str, max_hours: float = MAX_HOURS_IN_DAY) -> float | None:
    """Parse user-typed durations into a float of hours.

    Examples: "1.5" / "1,5" / "2ч" / "2 ч" / "30мин" / "полтора" / "полчаса"
    Returns None for unparseable, negative, or out-of-range input (more hours
    than fit in a day — see `MAX_HOURS_IN_DAY`).
    """
    if not text:
        return None
    s = text.strip().lower()
    if s in _WORD_HOURS:
        return _WORD_HOURS[s]
    m = _HOURS_RE.match(s)
    if m:
        v = float(m.group(1).replace(",", "."))
        if v < 0 or v > max_hours:
            return None
        return v
    m = _MINUTES_RE.match(s)
    if m:
        v = int(m.group(1)) / 60.0
        if v < 0 or v > max_hours:
            return None
        return v
    return None
