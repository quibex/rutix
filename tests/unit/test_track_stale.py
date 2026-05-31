"""/track stale-session guard — an abandoned session from a previous subjective
day must not eat the next number the user types (e.g. a med-reminder snooze)."""

import pytest

from rutix.bot.handlers import track as track_handler
from rutix.bot.handlers.track import TrackStates


class FakeState:
    """Minimal aiogram FSMContext stand-in backed by a dict."""

    def __init__(self, data=None, state=None):
        self._data = dict(data or {})
        self._state = state

    async def get_data(self):
        return dict(self._data)

    async def update_data(self, **kwargs):
        self._data.update(kwargs)
        return dict(self._data)

    async def set_state(self, state):
        self._state = state

    async def get_state(self):
        return self._state.state if hasattr(self._state, "state") else self._state

    async def clear(self):
        self._data = {}
        self._state = None


class FakeMessage:
    """Captures .answer() calls."""

    def __init__(self, text):
        self.text = text
        self.answers: list[str] = []

    async def answer(self, text, reply_markup=None):
        self.answers.append(text)


class FakeSettings:
    tz = "Europe/Moscow"


@pytest.mark.asyncio
async def test_stale_filter_matches_old_day():
    # Weight step left over from a long-past /track session.
    state = FakeState(data={"day": "2020-01-01"}, state=TrackStates.weight)
    matched = await track_handler._StaleTrackFilter()(FakeMessage("60"), state, FakeSettings())
    assert matched is True


@pytest.mark.asyncio
async def test_stale_filter_ignores_current_day():
    # A session for a future day is never stale.
    state = FakeState(data={"day": "2999-01-01"}, state=TrackStates.weight)
    matched = await track_handler._StaleTrackFilter()(FakeMessage("60"), state, FakeSettings())
    assert matched is False


@pytest.mark.asyncio
async def test_stale_filter_ignores_no_state():
    state = FakeState(data={}, state=None)
    matched = await track_handler._StaleTrackFilter()(FakeMessage("60"), state, FakeSettings())
    assert matched is False


@pytest.mark.asyncio
async def test_stale_handler_clears_and_notifies():
    state = FakeState(data={"day": "2020-01-01", "mood": 2}, state=TrackStates.weight)
    msg = FakeMessage("60")
    await track_handler.msg_track_stale(msg, state)
    assert await state.get_state() is None
    assert await state.get_data() == {}
    assert msg.answers
    assert "устарел" in msg.answers[-1]
