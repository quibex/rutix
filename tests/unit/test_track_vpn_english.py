"""Tests for the VPN/English steps added to /track."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

from rutix.bot.handlers.track import (
    TrackStates,
    cb_english,
    cb_vpn,
    msg_english_input,
    msg_vpn_input,
)

from rutix.db.models import MoodEntry


def _session_factory(session):
    def factory():
        class CM:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, *a):
                pass

        return CM()

    return factory


def _make_state(day: str = "2026-05-13", **overrides):
    state = MagicMock()
    _data = {
        "day": day,
        "mood": 0,
        "anxiety": 0,
        "irritability": 0,
        "energy": 0,
        "appetite": 2,
        "sleep_hours": 7.0,
        "meds_taken": [],
        "meds_pending": [],
        **overrides,
    }
    _state_holder = {"value": None}

    async def get_data():
        return dict(_data)

    async def update_data(**kwargs):
        _data.update(kwargs)

    async def set_state(s):
        _state_holder["value"] = s

    async def clear():
        pass

    state.get_data = get_data
    state.update_data = update_data
    state.set_state = set_state
    state.clear = clear
    return state, _data, _state_holder


def _make_cb(data: str):
    cb = MagicMock()
    cb.data = data
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.message.answer = AsyncMock()
    cb.answer = AsyncMock()
    return cb


def _make_msg(text: str):
    msg = MagicMock()
    msg.text = text
    msg.answer = AsyncMock()
    msg.edit_text = AsyncMock()
    return msg


# --- VPN button selections ---


async def test_cb_vpn_numeric_advances_to_english(session):
    state, data, state_holder = _make_state()
    cb = _make_cb("vpn:2")

    await cb_vpn(cb, state=state, session_factory=_session_factory(session))

    assert data["vpn_hours"] == 2.0
    assert state_holder["value"] is TrackStates.english
    cb.message.edit_text.assert_awaited()
    args, _ = cb.message.edit_text.call_args
    assert "English" in args[0]


async def test_cb_vpn_half_hour(session):
    state, data, state_holder = _make_state()
    cb = _make_cb("vpn:0.5")
    await cb_vpn(cb, state=state, session_factory=_session_factory(session))
    assert data["vpn_hours"] == 0.5


# --- VPN text input ---


async def test_msg_vpn_input_parses_number_and_advances(session):
    state, data, state_holder = _make_state()
    msg = _make_msg("1.5")

    await msg_vpn_input(msg, state=state, session_factory=_session_factory(session))

    assert data["vpn_hours"] == 1.5
    assert state_holder["value"] is TrackStates.english
    msg.answer.assert_awaited()
    text_arg = msg.answer.call_args.args[0]
    assert "English" in text_arg


async def test_msg_vpn_input_parses_ru_word(session):
    state, data, state_holder = _make_state()
    msg = _make_msg("полтора")
    await msg_vpn_input(msg, state=state, session_factory=_session_factory(session))
    assert data["vpn_hours"] == 1.5
    assert state_holder["value"] is TrackStates.english


async def test_msg_vpn_input_invalid_reprompts_and_keeps_state(session):
    state, data, state_holder = _make_state()
    msg = _make_msg("много")

    await msg_vpn_input(msg, state=state, session_factory=_session_factory(session))

    assert "vpn_hours" not in data
    # State did not transition
    assert state_holder["value"] is None
    msg.answer.assert_awaited()
    assert "Не понял" in msg.answer.call_args.args[0]


# --- English button selections ---


async def test_cb_english_numeric_saves_and_finishes_on_weekday(session):
    state, data, state_holder = _make_state(vpn_hours=1.0)
    cb = _make_cb("eng:1")

    await cb_english(cb, state=state, session_factory=_session_factory(session))

    assert data["eng_hours"] == 1.0
    saved = await session.get(MoodEntry, date(2026, 5, 13))
    assert saved is not None
    assert saved.vpn_hours == 1.0
    assert saved.eng_hours == 1.0


# --- English text input ---


async def test_msg_english_input_parses_and_saves(session):
    state, data, state_holder = _make_state(vpn_hours=0.5)
    msg = _make_msg("2ч")

    await msg_english_input(msg, state=state, session_factory=_session_factory(session))

    assert data["eng_hours"] == 2.0
    saved = await session.get(MoodEntry, date(2026, 5, 13))
    assert saved is not None
    assert saved.vpn_hours == 0.5
    assert saved.eng_hours == 2.0


async def test_msg_english_input_invalid_reprompts(session):
    state, data, state_holder = _make_state(vpn_hours=1.0)
    msg = _make_msg("чёт непонятное")

    await msg_english_input(msg, state=state, session_factory=_session_factory(session))

    assert "eng_hours" not in data
    msg.answer.assert_awaited()
    assert "Не понял" in msg.answer.call_args.args[0]


# --- Saturday flow continues to weight ---


async def test_cb_english_on_saturday_asks_weight(session):
    saturday = "2026-05-16"
    state, data, state_holder = _make_state(day=saturday, vpn_hours=1.0)
    cb = _make_cb("eng:1")

    await cb_english(cb, state=state, session_factory=_session_factory(session))

    assert state_holder["value"] is TrackStates.weight
    cb.message.edit_text.assert_awaited()
    assert "вес" in cb.message.edit_text.call_args.args[0].lower()
