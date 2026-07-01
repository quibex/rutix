"""Tests for /state — multi-run subjective-state snapshots (mood/energy/appetite).

Each run appends one StateEntry row with a wall-clock timestamp; no resume, no
averaging. Running /state twice a day yields two rows.
"""

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select

from rutix.bot.handlers.state import (
    StateStates,
    cmd_state,
    msg_appetite,
    msg_energy,
    msg_mood,
)
from rutix.db.models import StateEntry

_TZ = "Europe/Moscow"


class _Settings:
    tz = _TZ


def _session_factory(session):
    def factory():
        class CM:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, *a):
                pass

        return CM()

    return factory


def _make_state(**overrides):
    state = MagicMock()
    _data = dict(overrides)
    _state_holder = {"value": None}

    async def get_data():
        return dict(_data)

    async def update_data(**kwargs):
        _data.update(kwargs)

    async def set_state(s):
        _state_holder["value"] = s

    async def clear():
        _data.clear()
        _state_holder["value"] = None

    state.get_data = get_data
    state.update_data = update_data
    state.set_state = set_state
    state.clear = clear
    return state, _data, _state_holder


def _make_msg(text: str = ""):
    msg = MagicMock()
    msg.text = text
    msg.answer = AsyncMock()
    msg.edit_text = AsyncMock()
    return msg


async def test_cmd_state_seeds_day_and_ts_and_asks_mood(session):
    state, data, holder = _make_state()
    msg = _make_msg()

    await cmd_state(msg, state=state, settings=_Settings())

    assert holder["value"] is StateStates.mood
    assert "day" in data and "ts" in data
    # day/ts must parse back
    date.fromisoformat(data["day"])
    datetime.fromisoformat(data["ts"])
    assert "настроение" in msg.answer.call_args.args[0].lower()


async def test_full_state_flow_writes_one_row(session):
    state, data, holder = _make_state(day="2026-05-13", ts="2026-05-13T09:15:00")

    await msg_mood(_make_msg("2"), state=state)
    assert data["mood"] == 2
    assert holder["value"] is StateStates.energy

    await msg_energy(_make_msg("1"), state=state)
    assert data["energy"] == 1
    assert holder["value"] is StateStates.appetite

    await msg_appetite(_make_msg("-1"), state=state, session_factory=_session_factory(session))

    rows = (await session.scalars(select(StateEntry))).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.day == date(2026, 5, 13)
    assert (row.mood, row.energy, row.appetite) == (2, 1, -1)
    assert row.ts.strftime("%H:%M") == "09:15"


async def test_two_runs_same_day_make_two_rows(session):
    for ts, values in (("2026-05-13T09:00:00", (1, 0, 1)), ("2026-05-13T18:00:00", (2, 1, 0))):
        state, data, holder = _make_state(day="2026-05-13", ts=ts, mood=values[0], energy=values[1])
        await msg_appetite(
            _make_msg(str(values[2])), state=state, session_factory=_session_factory(session)
        )

    rows = (await session.scalars(select(StateEntry).order_by(StateEntry.ts))).all()
    assert len(rows) == 2
    assert [r.ts.strftime("%H:%M") for r in rows] == ["09:00", "18:00"]


async def test_mood_rejects_out_of_range(session):
    state, data, holder = _make_state(day="2026-05-13", ts="2026-05-13T09:00:00")
    msg = _make_msg("5")

    await msg_mood(msg, state=state)

    assert "mood" not in data
    assert holder["value"] is None
    assert "Не понял" in msg.answer.call_args.args[0]
