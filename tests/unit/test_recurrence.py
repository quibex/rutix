from datetime import date

import pytest

from rutix.jobs.recurrence import next_occurrence

# Reference "today" for the suite — Sunday 2026-05-24.
TODAY = date(2026, 5, 24)


@pytest.mark.parametrize(
    "string,anchor,expected",
    [
        # Daily: next occurrence on/after today is today itself.
        ("every day", date(2026, 5, 20), TODAY),
        ("daily", date(2026, 5, 20), TODAY),
        ("every morning", date(2026, 5, 20), TODAY),
        # Interval in days — phase preserved from the anchor.
        ("every 3 days", date(2026, 5, 19), date(2026, 5, 25)),  # 19,22,25
        ("every 3 days", date(2026, 5, 18), date(2026, 5, 24)),  # 18,21,24
        ("every other day", date(2026, 5, 21), date(2026, 5, 25)),  # 21,23,25
        ("every 2 days", date(2026, 5, 20), date(2026, 5, 24)),  # 20,22,24
        # Weeks — interval of 7*n days from the anchor.
        ("every week", date(2026, 5, 17), date(2026, 5, 24)),  # 17,24
        ("every 2 weeks", date(2026, 5, 20), date(2026, 6, 3)),  # 20, +14
        ("weekly", date(2026, 5, 10), date(2026, 5, 24)),  # 10,17,24
        # Named weekdays — absolute, anchor only proves recurrence.
        ("every monday", date(2026, 5, 18), date(2026, 5, 25)),
        ("every mon, wed, fri", date(2026, 5, 22), date(2026, 5, 25)),  # Sun→Mon
        ("every sunday", date(2026, 5, 17), TODAY),  # today is Sunday
        ("every weekday", date(2026, 5, 22), date(2026, 5, 25)),  # Sun→Mon
        ("every weekend", date(2026, 5, 23), TODAY),  # today is Sat/Sun set
        # Months — clamps day-of-month and keeps phase.
        ("every month", date(2026, 4, 15), date(2026, 6, 15)),  # Apr15→May15(<today)→Jun15
        ("every month", date(2026, 5, 28), date(2026, 5, 28)),  # already ≥ today
        ("monthly", date(2026, 3, 24), TODAY),  # Mar24→Apr24→May24
        # Years.
        ("every year", date(2025, 8, 1), date(2026, 8, 1)),
        # Recur-after-completion → do it today.
        ("every! 3 days", date(2026, 5, 18), TODAY),
        ("every! week", date(2026, 5, 1), TODAY),
    ],
)
def test_next_occurrence(string, anchor, expected):
    assert next_occurrence(string, anchor, TODAY) == expected


@pytest.mark.parametrize(
    "string",
    [
        None,
        "",
        "tomorrow",  # one-shot natural language, not a recurrence
        "every 3rd thursday",  # ordinal-weekday — unsupported
        "every last day",  # last-day-of-month — unsupported
        "every 15th",  # day-of-month ordinal — unsupported
    ],
)
def test_next_occurrence_unparseable_returns_none(string):
    assert next_occurrence(string, date(2026, 5, 20), TODAY) is None
