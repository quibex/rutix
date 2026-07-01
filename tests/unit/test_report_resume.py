"""Tests for /report write-through persistence + same-day resume.

Each answered step is committed immediately, so an interrupted /report keeps its
progress; re-running /report the same day continues from the first unanswered
step (a new day starts fresh).
"""

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from rutix.bot.handlers.report import (
    ReportStates,
    cmd_report,
    msg_sleep_input,
)
from rutix.db.models import MedActive, MedicationLog, MoodEntry
from rutix.time_utils import subjective_today

_TZ = "Europe/Moscow"


def _today() -> date:
    """The day cmd_report will compute — seed fixtures for this date."""
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


async def test_sleep_step_persists_immediately(session):
    state, data, holder = _make_state()

    await msg_sleep_input(
        _make_msg("7"),
        state=state,
        settings=_Settings(),
        session_factory=_session_factory(session),
    )
    entry = await session.get(MoodEntry, date(2026, 5, 13))
    assert entry is not None
    assert entry.sleep_hours == 7.0
    assert entry.vpn_hours is None  # not answered yet


# --- resume ----------------------------------------------------------------


async def test_fresh_day_starts_at_sleep(session):
    state, data, holder = _make_state()
    msg = _make_msg()

    await cmd_report(
        msg, state=state, settings=_Settings(), session_factory=_session_factory(session)
    )

    assert holder["value"] is ReportStates.sleep
    text = msg.answer.call_args.args[0]
    assert "час" in text.lower()
    assert "Продолжаем" not in text


async def test_resume_continues_from_first_unanswered_step(session):
    # sleep already answered, no meds → resume at vpn.
    session.add(MoodEntry(day=_today(), sleep_hours=7.0))
    await session.commit()

    state, data, holder = _make_state()
    msg = _make_msg()

    await cmd_report(
        msg, state=state, settings=_Settings(), session_factory=_session_factory(session)
    )

    assert holder["value"] is ReportStates.vpn
    assert "Продолжаем" in msg.answer.call_args_list[0].args[0]
    assert "VPN" in msg.answer.call_args_list[-1].args[0]
    assert data["sleep_hours"] == 7.0


async def test_resume_into_meds_when_meds_pending(session):
    session.add(MoodEntry(day=_today(), sleep_hours=7.0))
    session.add(
        MedActive(
            key="atarax",
            name="Атаракс",
            column_label="Атаракс",
            current_dose="25",
            started_at=date(2026, 5, 1),
            reminder_time="00:00",  # always due
        )
    )
    await session.commit()

    state, data, holder = _make_state()
    msg = _make_msg()

    await cmd_report(
        msg, state=state, settings=_Settings(), session_factory=_session_factory(session)
    )

    assert holder["value"] is ReportStates.meds
    assert data["meds_pending"] == ["atarax"]
    assert "Атаракс" in msg.answer.call_args_list[-1].args[0]


async def test_resume_skips_already_logged_meds(session):
    session.add(MoodEntry(day=_today(), sleep_hours=7.0, vpn_hours=1.0))
    session.add(
        MedActive(
            key="atarax",
            name="Атаракс",
            column_label="Атаракс",
            current_dose="25",
            started_at=date(2026, 5, 1),
        )
    )
    session.add(MedicationLog(day=_today(), med_key="atarax", taken=True))
    await session.commit()

    state, data, holder = _make_state()
    msg = _make_msg()

    await cmd_report(
        msg, state=state, settings=_Settings(), session_factory=_session_factory(session)
    )

    # meds done (logged) + vpn done → resume at english
    assert holder["value"] is ReportStates.english
    assert data["meds_pending"] == []
    assert {"key": "atarax", "taken": True} in data["meds_taken"]


async def test_fully_tracked_day_offers_redo(session):
    # weight set too so the day counts as complete even on a Saturday.
    session.add(MoodEntry(day=_today(), sleep_hours=7.0, vpn_hours=1.0, eng_hours=1.0, weight=70.0))
    await session.commit()

    state, data, holder = _make_state()
    msg = _make_msg()

    await cmd_report(
        msg, state=state, settings=_Settings(), session_factory=_session_factory(session)
    )

    assert holder["value"] is ReportStates.sleep
    assert "уже заполнен" in msg.answer.call_args.args[0]
