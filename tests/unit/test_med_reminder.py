from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from freezegun import freeze_time

from rutix.db.models import MedActive, MedicationLog
from rutix.jobs.med_reminder import (
    ALL_DONE_TEXT,
    CB_PREFIX,
    REMINDER_HEADER,
    build_reminder_keyboard,
    build_reminder_text,
    due_active_meds,
    med_reminder_tick,
    parse_reminder_time,
    untaken_active_meds,
)


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


async def _add_med(
    session,
    key,
    name="Test",
    dose="25",
    started=date(2026, 1, 1),
    archived=None,
    reminder_time=None,
):
    session.add(
        MedActive(
            key=key,
            name=name,
            column_label=name,
            current_dose=dose,
            started_at=started,
            archived_at=archived,
            reminder_time=reminder_time,
        )
    )
    await session.commit()


# parse_reminder_time


def test_parse_canonical():
    assert parse_reminder_time("09:30") == "09:30"


def test_parse_pads_single_digits():
    assert parse_reminder_time("9:5") == "09:05"


def test_parse_strips_whitespace():
    assert parse_reminder_time("  09:30  ") == "09:30"


@pytest.mark.parametrize("bad", ["", "9", "9:", ":30", "25:00", "12:60", "abc", "9-30", "12::00"])
def test_parse_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_reminder_time(bad)


# due_active_meds


async def test_due_returns_only_meds_with_matching_time(session):
    await _add_med(session, "morning", reminder_time="09:00")
    await _add_med(session, "evening", reminder_time="21:00")
    due = await due_active_meds(session, date(2026, 5, 23), "09:00")
    assert [m.key for m in due] == ["morning"]


async def test_due_excludes_meds_without_reminder_time(session):
    await _add_med(session, "morning", reminder_time="09:00")
    await _add_med(session, "ad_hoc", reminder_time=None)
    due = await due_active_meds(session, date(2026, 5, 23), "09:00")
    assert [m.key for m in due] == ["morning"]


async def test_due_excludes_archived(session):
    await _add_med(
        session, "old", reminder_time="09:00", archived=date(2026, 4, 1)
    )
    due = await due_active_meds(session, date(2026, 5, 23), "09:00")
    assert due == []


async def test_due_excludes_meds_already_taken(session):
    await _add_med(session, "morning", reminder_time="09:00")
    session.add(MedicationLog(day=date(2026, 5, 23), med_key="morning", taken=True))
    await session.commit()
    due = await due_active_meds(session, date(2026, 5, 23), "09:00")
    assert due == []


async def test_due_includes_meds_with_log_taken_false(session):
    """User said 'no' in /track but the reminder time hasn't passed yet — still nag."""
    await _add_med(session, "morning", reminder_time="09:00")
    session.add(MedicationLog(day=date(2026, 5, 23), med_key="morning", taken=False))
    await session.commit()
    due = await due_active_meds(session, date(2026, 5, 23), "09:00")
    assert [m.key for m in due] == ["morning"]


async def test_due_batches_when_two_meds_share_time(session):
    await _add_med(
        session, "first", "Первый", reminder_time="09:00", started=date(2026, 1, 1)
    )
    await _add_med(
        session, "second", "Второй", reminder_time="09:00", started=date(2026, 2, 1)
    )
    due = await due_active_meds(session, date(2026, 5, 23), "09:00")
    assert [m.key for m in due] == ["first", "second"]


# untaken_active_meds — used by the callback handler to refresh the keyboard


async def test_untaken_returns_all_active_regardless_of_reminder_time(session):
    """The keyboard-refresh helper should list every active untaken med,
    not only the ones whose reminder_time matches now."""
    await _add_med(session, "scheduled", reminder_time="09:00")
    await _add_med(session, "ad_hoc", reminder_time=None)
    untaken = await untaken_active_meds(session, date(2026, 5, 23))
    assert sorted(m.key for m in untaken) == ["ad_hoc", "scheduled"]


# build_reminder_text / keyboard


def test_reminder_text_lists_meds_with_doses():
    meds = [
        MedActive(
            key="seizar",
            name="Сейзар",
            column_label="Сейзар",
            current_dose="25",
            started_at=date(2026, 1, 1),
        ),
    ]
    text = build_reminder_text(meds)
    assert REMINDER_HEADER in text
    assert "Сейзар — 25 мг" in text


def test_reminder_keyboard_encodes_day_and_key():
    meds = [
        MedActive(
            key="seizar",
            name="Сейзар",
            column_label="Сейзар",
            current_dose="25",
            started_at=date(2026, 1, 1),
        )
    ]
    kb = build_reminder_keyboard(date(2026, 5, 23), meds)
    btn = kb.inline_keyboard[0][0]
    assert btn.callback_data == f"{CB_PREFIX}:2026-05-23:seizar"


# med_reminder_tick (per-minute cron entrypoint)


@freeze_time("2026-05-23 06:00:00")  # 09:00 MSK
async def test_tick_fires_when_med_due_now(fake_bot, session):
    await _add_med(session, "morning", "Утренний", dose="25", reminder_time="09:00")
    sent = await med_reminder_tick(
        _session_factory(session), fake_bot, telegram_user_id=42, tz="Europe/Moscow"
    )
    assert sent is True
    fake_bot.send_message.assert_awaited_once()
    btn = fake_bot.send_message.call_args.kwargs["reply_markup"].inline_keyboard[0][0]
    assert btn.callback_data == f"{CB_PREFIX}:2026-05-23:morning"


@freeze_time("2026-05-23 06:00:00")  # 09:00 MSK
async def test_tick_silent_when_no_med_matches_minute(fake_bot, session):
    await _add_med(session, "evening", reminder_time="21:00")
    sent = await med_reminder_tick(
        _session_factory(session), fake_bot, telegram_user_id=42, tz="Europe/Moscow"
    )
    assert sent is False
    fake_bot.send_message.assert_not_called()


@freeze_time("2026-05-23 06:00:00")  # 09:00 MSK
async def test_tick_silent_when_med_already_taken_today(fake_bot, session):
    await _add_med(session, "morning", reminder_time="09:00")
    session.add(MedicationLog(day=date(2026, 5, 23), med_key="morning", taken=True))
    await session.commit()
    sent = await med_reminder_tick(
        _session_factory(session), fake_bot, telegram_user_id=42, tz="Europe/Moscow"
    )
    assert sent is False


@freeze_time("2026-05-23 06:00:00")  # 09:00 MSK
async def test_tick_batches_meds_sharing_a_minute(fake_bot, session):
    await _add_med(
        session, "first", "Первый", reminder_time="09:00", started=date(2026, 1, 1)
    )
    await _add_med(
        session, "second", "Второй", reminder_time="09:00", started=date(2026, 2, 1)
    )
    sent = await med_reminder_tick(
        _session_factory(session), fake_bot, telegram_user_id=42, tz="Europe/Moscow"
    )
    assert sent is True
    kb = fake_bot.send_message.call_args.kwargs["reply_markup"].inline_keyboard
    assert len(kb) == 2


@freeze_time("2026-05-23 00:00:00")  # 03:00 MSK exactly — boundary
async def test_tick_uses_subjective_today(fake_bot, session):
    """A 03:00 reminder on day D should credit day D (subjective_today returns
    D at exactly 03:00 — pre-3AM rolls back to D-1)."""
    await _add_med(session, "morning", reminder_time="03:00")
    sent = await med_reminder_tick(
        _session_factory(session), fake_bot, telegram_user_id=42, tz="Europe/Moscow"
    )
    assert sent is True
    btn = fake_bot.send_message.call_args.kwargs["reply_markup"].inline_keyboard[0][0]
    assert btn.callback_data == f"{CB_PREFIX}:2026-05-23:morning"


def test_all_done_text_constant():
    assert "✅" in ALL_DONE_TEXT
