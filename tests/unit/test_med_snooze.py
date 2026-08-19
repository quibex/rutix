"""Snooze: typing a number of minutes defers the reminders waiting on an answer.

The bug this guards: typing "180" at 11:47 after the 08:00 pill also re-pointed
the untouched 23:30 pill to 14:47, so the evening med was announced nine hours
early (and its own 23:30 tick then found it "already reminded").
"""

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from freezegun import freeze_time
from sqlalchemy import select

from rutix.bot.handlers.meds import msg_snooze_minutes
from rutix.db.models import MedActive, MedicationLog, MedSnooze
from rutix.jobs.med_reminder import fired_reminder_meds, med_reminder_tick


@pytest.fixture
def fake_settings():
    s = MagicMock()
    s.tz = "Europe/Moscow"
    return s


@pytest.fixture
def fake_bot():
    b = MagicMock()
    b.send_message = AsyncMock()
    return b


def _session_factory(session):
    def factory():
        class CM:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, *a):
                pass

        return CM()

    return factory


def _fake_message(text: str):
    m = MagicMock()
    m.text = text
    m.answer = AsyncMock()
    return m


def _fake_state(current=None):
    st = MagicMock()
    st.get_state = AsyncMock(return_value=current)
    return st


async def _add_med(session, key, name, reminder_time, started=date(2026, 1, 1), dose="100"):
    session.add(
        MedActive(
            key=key,
            name=name,
            column_label=name,
            current_dose=dose,
            started_at=started,
            reminder_time=reminder_time,
        )
    )
    await session.commit()


async def _seizar_and_melatonin(session):
    await _add_med(session, "seizar", "Сейзар", "08:00", started=date(2026, 1, 1))
    await _add_med(session, "melatonin", "Мелатонин", "23:30", started=date(2026, 2, 1), dose="3")


# fired_reminder_meds


async def test_fired_excludes_meds_whose_time_is_still_ahead(session):
    await _seizar_and_melatonin(session)
    meds = await fired_reminder_meds(session, date(2026, 8, 19), "11:47")
    assert [m.key for m in meds] == ["seizar"]


async def test_fired_includes_a_med_due_this_very_minute(session):
    await _seizar_and_melatonin(session)
    meds = await fired_reminder_meds(session, date(2026, 8, 19), "08:00")
    assert [m.key for m in meds] == ["seizar"]


async def test_fired_counts_last_nights_evening_med_after_midnight(session):
    """01:30 is still the same subjective day, so 23:30 has already fired —
    a plain string compare would call it "ahead"."""
    await _seizar_and_melatonin(session)
    meds = await fired_reminder_meds(session, date(2026, 8, 19), "01:30")
    assert [m.key for m in meds] == ["seizar", "melatonin"]


async def test_fired_excludes_meds_already_taken(session):
    await _seizar_and_melatonin(session)
    session.add(MedicationLog(day=date(2026, 8, 19), med_key="seizar", taken=True))
    await session.commit()
    meds = await fired_reminder_meds(session, date(2026, 8, 19), "11:47")
    assert meds == []


# msg_snooze_minutes


@freeze_time("2026-08-19 08:47:30")  # 11:47 MSK
async def test_snooze_defers_only_the_med_already_reminded(session, fake_settings):
    await _seizar_and_melatonin(session)
    msg = _fake_message("180")

    await msg_snooze_minutes(
        msg,
        state=_fake_state(),
        session_factory=_session_factory(session),
        settings=fake_settings,
    )

    rows = (await session.scalars(select(MedSnooze))).all()
    assert len(rows) == 1
    assert rows[0].med_keys == "seizar"
    text = msg.answer.call_args.args[0]
    assert "Сейзар" in text
    assert "Мелатонин" not in text
    assert "3 ч" in text and "14:47" in text


@freeze_time("2026-08-19 08:47:30")  # 11:47 MSK
async def test_snooze_fires_on_the_minute_it_promised(session, fake_settings, fake_bot):
    """fire_at is floored to the minute — the tick only matches whole minutes,
    so an unfloored 14:47:30 would go out at 14:48."""
    await _seizar_and_melatonin(session)
    await msg_snooze_minutes(
        _fake_message("180"),
        state=_fake_state(),
        session_factory=_session_factory(session),
        settings=fake_settings,
    )
    row = (await session.scalars(select(MedSnooze))).one()
    assert row.fire_at == datetime(2026, 8, 19, 14, 47)

    with freeze_time("2026-08-19 11:47:05"):  # 14:47 MSK
        sent = await med_reminder_tick(
            _session_factory(session), fake_bot, telegram_user_id=42, tz="Europe/Moscow"
        )
    assert sent is True
    text = fake_bot.send_message.call_args.kwargs["text"]
    assert "Сейзар" in text
    assert "Мелатонин" not in text


@freeze_time("2026-08-19 08:47:00")  # 11:47 MSK
async def test_snooze_ignored_when_nothing_has_been_reminded_yet(session, fake_settings):
    await _add_med(session, "melatonin", "Мелатонин", "23:30", dose="3")
    msg = _fake_message("180")

    await msg_snooze_minutes(
        msg,
        state=_fake_state(),
        session_factory=_session_factory(session),
        settings=fake_settings,
    )

    assert (await session.scalars(select(MedSnooze))).all() == []
    msg.answer.assert_not_called()


@freeze_time("2026-08-19 08:47:00")  # 11:47 MSK
async def test_snooze_does_not_steal_input_from_an_active_flow(session, fake_settings):
    await _seizar_and_melatonin(session)
    msg = _fake_message("180")

    await msg_snooze_minutes(
        msg,
        state=_fake_state(current="ReportStates:sleep"),
        session_factory=_session_factory(session),
        settings=fake_settings,
    )

    assert (await session.scalars(select(MedSnooze))).all() == []
    msg.answer.assert_not_called()
