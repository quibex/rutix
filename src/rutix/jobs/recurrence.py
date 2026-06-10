"""Parse a Todoist natural-language recurrence string and compute the next
occurrence on or after a given day, *keeping the original cadence phase*.

Why this exists: the 03:00 push-forward used to drag every overdue task —
recurring ones included — to **today**. For a recurring habit that re-anchors
the whole series to today (a "yoga, every 3 days" task you skip on Monday
suddenly recurs Thu/Sun/Wed… instead of staying on its Mon/Thu/Sun phase). The
user wants an overdue habit to advance to its *next scheduled* occurrence, not
to today. This module computes that next occurrence from the recurrence string
plus the current (overdue) anchor date.

We only need the cadences the user actually keeps; anything we can't parse
returns ``None`` so the caller can fall back to the old "move to today"
behaviour rather than guessing wrong.

`every!` (recur-after-completion) is deliberately treated as "today": those
tasks recur N days after you *finish* them, so when one is overdue the right
next date is now — do it today, the next instance counts from that.
"""

import math
import re
from datetime import date, timedelta

# Aliases that don't start with "every" → canonical "every <unit>" form.
_ALIASES = {
    "daily": "every day",
    "weekly": "every week",
    "biweekly": "every 2 weeks",
    "fortnightly": "every 2 weeks",
    "monthly": "every month",
    "yearly": "every year",
    "annually": "every year",
}

_WEEKDAYS = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

# Daily synonyms Todoist accepts ("every morning", "every night", …).
_DAILY_WORDS = {"day", "days", "morning", "afternoon", "evening", "night"}


def _add_months(d: date, n: int) -> date:
    """Add ``n`` months to ``d``, clamping the day to the target month's length
    (e.g. Jan 31 + 1 month → Feb 28/29)."""
    month_index = d.month - 1 + n
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    # Last day of the target month.
    if month == 12:
        last = 31
    else:
        last = (date(year, month + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(d.day, last))


def _advance_by_days(anchor: date, today: date, step: int) -> date:
    """Smallest ``anchor + k*step`` (k ≥ 1) that is ≥ ``today``. Assumes
    ``anchor < today`` (the overdue case)."""
    delta = (today - anchor).days
    k = math.ceil(delta / step)
    return anchor + timedelta(days=k * step)


def _advance_by_months(anchor: date, today: date, step: int) -> date:
    """First ``anchor + k*step`` months (k ≥ 0) that is ≥ ``today``."""
    k = 0
    nxt = anchor
    while nxt < today:
        k += 1
        nxt = _add_months(anchor, step * k)
    return nxt


def _advance_by_years(anchor: date, today: date, step: int) -> date:
    return _advance_by_months(anchor, today, 12 * step)


def _next_weekday(today: date, weekdays: set[int]) -> date:
    """First day ≥ ``today`` whose weekday is in ``weekdays``."""
    for i in range(7):
        d = today + timedelta(days=i)
        if d.weekday() in weekdays:
            return d
    return today  # unreachable — weekdays is non-empty


def _parse_count(body: str) -> tuple[int, str]:
    """Strip a leading count ("other", "2", "2nd"…) off the recurrence body.
    Returns (count, remaining-body)."""
    body = body.strip()
    if body.startswith("other "):
        return 2, body[len("other ") :].strip()
    m = re.match(r"^(\d+)(?:st|nd|rd|th)?\s+(.*)$", body)
    if m:
        return int(m.group(1)), m.group(2).strip()
    return 1, body


def next_occurrence(string: str | None, anchor: date, today: date) -> date | None:
    """Next occurrence of a recurring task on or after ``today``, preserving the
    cadence anchored at ``anchor``. ``string`` is Todoist's ``due.string``.

    Returns ``None`` when the recurrence can't be parsed, so the caller can fall
    back to its default behaviour.
    """
    if not string:
        return None
    raw = string.strip().lower()
    raw = _ALIASES.get(raw, raw)

    # Recur-after-completion: do it today, the next instance counts from now.
    if raw.startswith("every!"):
        return today
    if not raw.startswith("every"):
        return None
    body = raw[len("every") :].strip()
    # Drop a trailing time-of-day clause ("… at 9am") — it doesn't affect dates.
    body = body.split(" at ")[0].strip()

    # Explicit weekday names (e.g. "mon, wed, fri"). Only treat as a weekday-set
    # recurrence when *every* token is a weekday name — "3rd thursday" and the
    # like carry an ordinal we don't model, so bail to None (fall back to today).
    tokens = [t for t in re.split(r"[,\s]+", body) if t and t != "and"]
    named = {_WEEKDAYS[t] for t in tokens if t in _WEEKDAYS}
    if named:
        if all(t in _WEEKDAYS for t in tokens):
            return _next_weekday(today, named)
        return None

    if "workday" in body or "weekday" in body:
        return _next_weekday(today, {0, 1, 2, 3, 4})
    if "weekend" in body:
        return _next_weekday(today, {5, 6})

    count, rest = _parse_count(body)
    head = rest.split()[0] if rest.split() else ""

    if head in _DAILY_WORDS:
        return _advance_by_days(anchor, today, count)
    if head in ("week", "weeks"):
        return _advance_by_days(anchor, today, 7 * count)
    if head in ("month", "months"):
        return _advance_by_months(anchor, today, count)
    if head in ("year", "years"):
        return _advance_by_years(anchor, today, count)

    return None
