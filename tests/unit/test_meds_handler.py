from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from rutix.bot.handlers.meds import cb_med_taken, cmd_meds
from rutix.db.models import MedActive, MedicationLog
from rutix.jobs.med_reminder import ALL_DONE_TEXT, CB_PREFIX


@pytest.fixture
def fake_settings():
    s = MagicMock()
    s.tz = "Europe/Moscow"
    return s


@pytest.fixture
def fake_message():
    m = MagicMock()
    m.answer = AsyncMock()
    return m


def _session_factory_for(session):
    def factory():
        class CM:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, *a):
                pass

        return CM()

    return factory


def _fake_callback(data: str):
    cb = MagicMock()
    cb.data = data
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()
    return cb


async def test_cmd_meds_lists_active(fake_message, fake_settings, session):
    session.add(
        MedActive(
            key="seizar",
            name="Сейзар",
            column_label="Сейзар",
            current_dose="25",
            started_at=date(2026, 4, 26),
        )
    )
    await session.commit()

    def session_factory_call():
        class CM:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, *a):
                pass

        return CM()

    sf = MagicMock(side_effect=lambda: session_factory_call())

    await cmd_meds(fake_message, settings=fake_settings, session_factory=sf)

    text = fake_message.answer.call_args.args[0]
    assert "Сейзар" in text
    assert "25" in text
    # Buttons present
    kb = fake_message.answer.call_args.kwargs["reply_markup"]
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Добавить" in l for l in labels)


# cb_med_taken — pressing the "✓ принял" button on a reminder


async def _add_med(session, key, name="Сейзар", dose="25"):
    session.add(
        MedActive(
            key=key,
            name=name,
            column_label=name,
            current_dose=dose,
            started_at=date(2026, 5, 1),
        )
    )
    await session.commit()


async def test_cb_med_taken_inserts_log_when_no_row(session):
    await _add_med(session, "seizar")
    cb = _fake_callback(f"{CB_PREFIX}:2026-05-23:seizar")
    await cb_med_taken(cb, session_factory=_session_factory_for(session))

    log = await session.get(MedicationLog, (date(2026, 5, 23), "seizar"))
    assert log is not None
    assert log.taken is True
    cb.message.edit_text.assert_awaited_once()
    cb.message.edit_text.call_args.args[0] == ALL_DONE_TEXT


async def test_cb_med_taken_flips_existing_false_row_to_true(session):
    """User pressed ✗ Нет in /track, then taps reminder button — should flip to True."""
    await _add_med(session, "seizar")
    session.add(MedicationLog(day=date(2026, 5, 23), med_key="seizar", taken=False))
    await session.commit()

    cb = _fake_callback(f"{CB_PREFIX}:2026-05-23:seizar")
    await cb_med_taken(cb, session_factory=_session_factory_for(session))

    log = await session.get(MedicationLog, (date(2026, 5, 23), "seizar"))
    assert log.taken is True


async def test_cb_med_taken_last_med_shows_all_done(session):
    await _add_med(session, "seizar")
    cb = _fake_callback(f"{CB_PREFIX}:2026-05-23:seizar")
    await cb_med_taken(cb, session_factory=_session_factory_for(session))

    args, kwargs = cb.message.edit_text.call_args
    assert args[0] == ALL_DONE_TEXT
    assert "reply_markup" not in kwargs  # no buttons after all-done


async def test_cb_med_taken_remaining_meds_keep_their_buttons(session):
    await _add_med(session, "seizar", "Сейзар")
    await _add_med(session, "atarax", "Атаракс")

    cb = _fake_callback(f"{CB_PREFIX}:2026-05-23:seizar")
    await cb_med_taken(cb, session_factory=_session_factory_for(session))

    args, kwargs = cb.message.edit_text.call_args
    text = args[0]
    assert "Атаракс" in text
    assert "Сейзар" not in text  # taken one is gone
    kb = kwargs["reply_markup"]
    btn = kb.inline_keyboard[0][0]
    assert btn.callback_data == f"{CB_PREFIX}:2026-05-23:atarax"


async def test_cb_med_taken_uses_callback_day_not_today(session):
    """A late-night tap should credit the day encoded in the callback,
    not the wall-clock date when the button was tapped."""
    await _add_med(session, "seizar")
    cb = _fake_callback(f"{CB_PREFIX}:2026-05-20:seizar")
    await cb_med_taken(cb, session_factory=_session_factory_for(session))

    log = await session.get(MedicationLog, (date(2026, 5, 20), "seizar"))
    assert log is not None and log.taken is True


async def test_cb_med_taken_unknown_med_alerts_silently(session):
    """Med was archived between reminder send and button press — fail soft."""
    cb = _fake_callback(f"{CB_PREFIX}:2026-05-23:ghost")
    await cb_med_taken(cb, session_factory=_session_factory_for(session))

    cb.answer.assert_awaited_once()
    assert cb.answer.call_args.kwargs.get("show_alert") is True
    cb.message.edit_text.assert_not_called()


async def test_cb_med_taken_malformed_callback_alerts(session):
    """Defensive: garbage callback data shouldn't crash the bot."""
    cb = _fake_callback("med_taken:not-a-date:foo")
    await cb_med_taken(cb, session_factory=_session_factory_for(session))

    cb.answer.assert_awaited_once()
    assert cb.answer.call_args.kwargs.get("show_alert") is True
