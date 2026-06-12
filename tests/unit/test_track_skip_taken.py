"""Tests for /track skipping meds already marked as taken (e.g. via reminder button)."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

from rutix.bot.handlers.track import cb_sleep
from rutix.db.models import MedActive, MedicationLog


def _session_factory(session):
    def factory():
        class CM:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, *a):
                pass

        return CM()

    return factory


async def _add_med(session, key, name="Test", dose="25"):
    session.add(
        MedActive(
            key=key,
            name=name,
            column_label=name,
            current_dose=dose,
            started_at=date(2026, 1, 1),
        )
    )
    await session.commit()


def _make_state(day: str = "2026-05-23"):
    state = MagicMock()
    _data = {
        "day": day,
        "mood": 0,
        "anxiety": 0,
        "irritability": 0,
        "energy": 0,
        "appetite": 2,
        "sleep_hours": 7.0,
    }

    async def get_data():
        return dict(_data)

    async def update_data(**kwargs):
        _data.update(kwargs)

    async def set_state(s):
        pass

    state.get_data = get_data
    state.update_data = update_data
    state.set_state = set_state
    return state, _data


def _make_cb(data: str = "sleep:7"):
    cb = MagicMock()
    cb.data = data
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()
    return cb


async def test_track_skips_med_already_taken_via_reminder(session):
    await _add_med(session, "seizar", "Сейзар")
    await _add_med(session, "atarax", "Атаракс")
    session.add(MedicationLog(day=date(2026, 5, 23), med_key="seizar", taken=True))
    await session.commit()

    state, data = _make_state()
    cb = _make_cb()

    await cb_sleep(cb, state=state, session_factory=_session_factory(session))

    assert "seizar" not in data["meds_pending"]
    assert "atarax" in data["meds_pending"]
    taken_keys = {e["key"] for e in data["meds_taken"]}
    assert "seizar" in taken_keys


async def test_track_skips_all_meds_when_all_taken(session):
    await _add_med(session, "seizar", "Сейзар")
    session.add(MedicationLog(day=date(2026, 5, 23), med_key="seizar", taken=True))
    await session.commit()

    state, data = _make_state()
    cb = _make_cb()

    await cb_sleep(cb, state=state, session_factory=_session_factory(session))

    assert data["meds_pending"] == []
    assert len(data["meds_taken"]) == 1
    assert data["meds_taken"][0]["taken"] is True


async def test_track_asks_all_meds_when_none_taken(session):
    await _add_med(session, "seizar", "Сейзар")
    await _add_med(session, "atarax", "Атаракс")

    state, data = _make_state()
    cb = _make_cb()

    await cb_sleep(cb, state=state, session_factory=_session_factory(session))

    assert sorted(data["meds_pending"]) == ["atarax", "seizar"]
    assert data["meds_taken"] == []


async def test_track_skips_med_answered_no(session):
    """A log with taken=False is an *answered* step (the user said "no") — with
    write-through persistence + resume, it's skipped, not re-asked."""
    await _add_med(session, "seizar", "Сейзар")
    session.add(MedicationLog(day=date(2026, 5, 23), med_key="seizar", taken=False))
    await session.commit()

    state, data = _make_state()
    cb = _make_cb()

    await cb_sleep(cb, state=state, session_factory=_session_factory(session))

    assert "seizar" not in data["meds_pending"]
    assert data["meds_taken"] == [{"key": "seizar", "taken": False}]
