"""Tests for free-text input on every /track step (type the number/answer
instead of tapping a button)."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from rutix.bot.handlers.track import (
    TrackStates,
    msg_anxiety_input,
    msg_appetite_input,
    msg_energy_input,
    msg_irritability_input,
    msg_med_input,
    msg_mood_input,
    msg_sleep_input,
)
from rutix.db.models import MedActive


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
    _data = {"day": day, "meds_taken": [], "meds_pending": [], **overrides}
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


def _make_msg(text: str):
    msg = MagicMock()
    msg.text = text
    msg.answer = AsyncMock()
    msg.edit_text = AsyncMock()
    return msg


def _med(key: str, name: str, dose: str = "50", started: date = date(2026, 5, 1)) -> MedActive:
    return MedActive(
        key=key,
        name=name,
        column_label=name,
        current_dose=dose,
        started_at=started,
        reminder_time=None,
    )


# --- mood (-3..+3) ---------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [("3", 3), ("+2", 2), ("-3", -3), ("0", 0), ("−1", -1), ("1.0", 1)],
)
async def test_msg_mood_accepts_typed_score(session, text, expected):
    state, data, holder = _make_state()
    msg = _make_msg(text)

    await msg_mood_input(msg, state=state, session_factory=_session_factory(session))

    assert data["mood"] == expected
    assert holder["value"] is TrackStates.anxiety
    msg.answer.assert_awaited()
    assert "тревога" in msg.answer.call_args.args[0].lower()


@pytest.mark.parametrize("text", ["4", "-4", "семь", "много", "1.5"])
async def test_msg_mood_rejects_out_of_range_or_garbage(session, text):
    state, data, holder = _make_state()
    msg = _make_msg(text)

    await msg_mood_input(msg, state=state, session_factory=_session_factory(session))

    assert "mood" not in data
    assert holder["value"] is None
    assert "Не понял" in msg.answer.call_args.args[0]


# --- anxiety / irritability (0..3) -----------------------------------------


async def test_msg_anxiety_typed_advances(session):
    state, data, holder = _make_state(mood=0)
    msg = _make_msg("2")

    await msg_anxiety_input(msg, state=state, session_factory=_session_factory(session))

    assert data["anxiety"] == 2
    assert holder["value"] is TrackStates.irritability


async def test_msg_anxiety_rejects_negative(session):
    state, data, holder = _make_state(mood=0)
    msg = _make_msg("-1")

    await msg_anxiety_input(msg, state=state, session_factory=_session_factory(session))

    assert "anxiety" not in data
    assert holder["value"] is None


async def test_msg_irritability_typed_advances(session):
    state, data, holder = _make_state(mood=0, anxiety=0)
    msg = _make_msg("3")

    await msg_irritability_input(msg, state=state, session_factory=_session_factory(session))

    assert data["irritability"] == 3
    assert holder["value"] is TrackStates.energy


# --- energy / appetite (-2..+2) --------------------------------------------


async def test_msg_energy_typed_advances(session):
    state, data, holder = _make_state(mood=0, anxiety=0, irritability=0)
    msg = _make_msg("+1")

    await msg_energy_input(msg, state=state, session_factory=_session_factory(session))

    assert data["energy"] == 1
    assert holder["value"] is TrackStates.appetite


async def test_msg_appetite_typed_advances_to_sleep(session):
    state, data, holder = _make_state(mood=0, anxiety=0, irritability=0, energy=0)
    msg = _make_msg("-2")

    await msg_appetite_input(msg, state=state, session_factory=_session_factory(session))

    assert data["appetite"] == -2
    assert holder["value"] is TrackStates.sleep
    assert "час" in msg.answer.call_args.args[0].lower()


async def test_msg_energy_rejects_out_of_range(session):
    state, data, holder = _make_state(mood=0, anxiety=0, irritability=0)
    msg = _make_msg("3")

    await msg_energy_input(msg, state=state, session_factory=_session_factory(session))

    assert "energy" not in data
    assert holder["value"] is None


# --- sleep (free hours) ----------------------------------------------------


async def test_msg_sleep_typed_no_meds_goes_to_vpn(session):
    state, data, holder = _make_state(mood=0, anxiety=0, irritability=0, energy=0, appetite=0)
    msg = _make_msg("6,5")

    await msg_sleep_input(msg, state=state, session_factory=_session_factory(session))

    assert data["sleep_hours"] == 6.5
    assert holder["value"] is TrackStates.vpn
    assert "VPN" in msg.answer.call_args.args[0]


async def test_msg_sleep_typed_with_meds_asks_first_med(session):
    session.add(_med("ssri", "Сертралин"))
    await session.commit()

    state, data, holder = _make_state(mood=0, anxiety=0, irritability=0, energy=0, appetite=0)
    msg = _make_msg("7")

    await msg_sleep_input(msg, state=state, session_factory=_session_factory(session))

    assert data["sleep_hours"] == 7.0
    assert holder["value"] is TrackStates.meds
    assert data["meds_pending"] == ["ssri"]
    assert "Сертралин" in msg.answer.call_args.args[0]


async def test_msg_sleep_rejects_garbage(session):
    state, data, holder = _make_state(mood=0, anxiety=0, irritability=0, energy=0, appetite=0)
    msg = _make_msg("норм")

    await msg_sleep_input(msg, state=state, session_factory=_session_factory(session))

    assert "sleep_hours" not in data
    assert holder["value"] is None
    assert "Не понял" in msg.answer.call_args.args[0]


# --- meds (да/нет text) ----------------------------------------------------


async def test_msg_med_yes_advances_to_next_med(session):
    session.add(_med("a", "Препарат A", started=date(2026, 5, 1)))
    session.add(_med("b", "Препарат B", started=date(2026, 5, 2)))
    await session.commit()

    state, data, holder = _make_state(meds_pending=["a", "b"], meds_taken=[])
    msg = _make_msg("да")

    await msg_med_input(msg, state=state, session_factory=_session_factory(session))

    assert data["meds_taken"] == [{"key": "a", "taken": True}]
    assert data["meds_pending"] == ["b"]
    assert holder["value"] is None  # still in meds, asking next
    assert "Препарат B" in msg.answer.call_args.args[0]


async def test_msg_med_no_on_last_goes_to_vpn(session):
    session.add(_med("a", "Препарат A"))
    await session.commit()

    state, data, holder = _make_state(meds_pending=["a"], meds_taken=[])
    msg = _make_msg("нет")

    await msg_med_input(msg, state=state, session_factory=_session_factory(session))

    assert data["meds_taken"] == [{"key": "a", "taken": False}]
    assert data["meds_pending"] == []
    assert holder["value"] is TrackStates.vpn


async def test_msg_med_garbage_reprompts(session):
    session.add(_med("a", "Препарат A"))
    await session.commit()

    state, data, holder = _make_state(meds_pending=["a"], meds_taken=[])
    msg = _make_msg("может быть")

    await msg_med_input(msg, state=state, session_factory=_session_factory(session))

    assert data["meds_taken"] == []
    assert data["meds_pending"] == ["a"]
    assert "Не понял" in msg.answer.call_args.args[0]
