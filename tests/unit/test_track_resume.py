"""Tests for /track write-through persistence + same-day resume.

Each answered step is committed immediately, so an interrupted /track keeps its
progress; re-running /track the same day continues from the first unanswered
step (a new day starts fresh).
"""

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from rutix.bot.handlers.track import (
    TrackStates,
    cmd_track,
    msg_anxiety_input,
    msg_mood_input,
)
from rutix.db.models import MedActive, MedicationLog, MoodEntry
from rutix.time_utils import subjective_today

_TZ = "Europe/Moscow"


def _today() -> date:
    """The day cmd_track will compute — seed fixtures for this date."""
    return subjective_today(datetime.now(ZoneInfo(_TZ)), _TZ)


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
        _data.clear()

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


class _Settings:
    tz = _TZ


# --- write-through persistence --------------------------------------------


async def test_each_step_persists_immediately(session):
    """Answering mood then anxiety writes both to the DB before the flow ends."""
    state, data, holder = _make_state()

    await msg_mood_input(_make_msg("2"), state=state, session_factory=_session_factory(session))
    entry = await session.get(MoodEntry, date(2026, 5, 13))
    assert entry is not None
    assert entry.mood == 2
    assert entry.anxiety is None  # not answered yet

    await msg_anxiety_input(_make_msg("1"), state=state, session_factory=_session_factory(session))
    await session.refresh(entry)
    assert entry.anxiety == 1


async def test_zero_is_a_real_answer_not_unanswered(session):
    """mood=0 must persist as 0 (None is the only "unanswered" marker)."""
    state, data, holder = _make_state()
    await msg_mood_input(_make_msg("0"), state=state, session_factory=_session_factory(session))
    entry = await session.get(MoodEntry, date(2026, 5, 13))
    assert entry.mood == 0


# --- resume ----------------------------------------------------------------


async def test_fresh_day_starts_at_mood(session):
    state, data, holder = _make_state()
    msg = _make_msg()

    await cmd_track(msg, state=state, settings=_Settings(), session_factory=_session_factory(session))

    assert holder["value"] is TrackStates.mood
    text = msg.answer.call_args.args[0]
    assert "настроение" in text.lower()
    assert "Продолжаем" not in text


async def test_resume_continues_from_first_unanswered_step(session):
    # mood/anxiety/irritability already answered → resume at energy.
    session.add(MoodEntry(day=_today(), mood=1, anxiety=0, irritability=2))
    await session.commit()

    state, data, holder = _make_state()
    msg = _make_msg()

    await cmd_track(msg, state=state, settings=_Settings(), session_factory=_session_factory(session))

    assert holder["value"] is TrackStates.energy
    # greeting + the energy prompt
    assert "Продолжаем" in msg.answer.call_args_list[0].args[0]
    assert "энерги" in msg.answer.call_args_list[-1].args[0].lower()
    # answered values are seeded back into the FSM for the final summary
    assert data["mood"] == 1
    assert data["anxiety"] == 0
    assert data["irritability"] == 2


async def test_resume_into_meds_when_meds_pending(session):
    session.add(MoodEntry(day=_today(), mood=0, anxiety=0, irritability=0, energy=0,
                          appetite=0, sleep_hours=7.0))
    session.add(MedActive(key="atarax", name="Атаракс", column_label="Атаракс",
                          current_dose="25", started_at=date(2026, 5, 1), reminder_time="23:00"))
    await session.commit()

    state, data, holder = _make_state()
    msg = _make_msg()

    await cmd_track(msg, state=state, settings=_Settings(), session_factory=_session_factory(session))

    assert holder["value"] is TrackStates.meds
    assert data["meds_pending"] == ["atarax"]
    assert "Атаракс" in msg.answer.call_args_list[-1].args[0]


async def test_resume_skips_already_logged_meds(session):
    session.add(MoodEntry(day=_today(), mood=0, anxiety=0, irritability=0, energy=0,
                          appetite=0, sleep_hours=7.0, vpn_hours=1.0))
    session.add(MedActive(key="atarax", name="Атаракс", column_label="Атаракс",
                          current_dose="25", started_at=date(2026, 5, 1)))
    session.add(MedicationLog(day=_today(), med_key="atarax", taken=True))
    await session.commit()

    state, data, holder = _make_state()
    msg = _make_msg()

    await cmd_track(msg, state=state, settings=_Settings(), session_factory=_session_factory(session))

    # meds done (logged) + vpn done → resume at english
    assert holder["value"] is TrackStates.english
    assert data["meds_pending"] == []
    assert {"key": "atarax", "taken": True} in data["meds_taken"]


async def test_fully_tracked_day_offers_redo(session):
    # weight set too so the day counts as complete even on a Saturday.
    session.add(MoodEntry(day=_today(), mood=0, anxiety=0, irritability=0, energy=0,
                          appetite=0, sleep_hours=7.0, vpn_hours=1.0, eng_hours=1.0, weight=70.0))
    await session.commit()

    state, data, holder = _make_state()
    msg = _make_msg()

    await cmd_track(msg, state=state, settings=_Settings(), session_factory=_session_factory(session))

    assert holder["value"] is TrackStates.mood
    assert "уже заполнен" in msg.answer.call_args.args[0]
