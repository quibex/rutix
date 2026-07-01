"""Tests for the /report steps: sleep, meds, VPN/English, weight (via buttons
and free-text input)."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

from rutix.bot.handlers.report import (
    ReportStates,
    cb_english,
    cb_sleep,
    cb_vpn,
    msg_english_input,
    msg_med_input,
    msg_sleep_input,
    msg_vpn_input,
)
from rutix.db.models import MedActive, MedicationLog, MoodEntry

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


def _make_cb(data: str):
    cb = MagicMock()
    cb.data = data
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.message.answer = AsyncMock()
    cb.answer = AsyncMock()
    return cb


def _med(key: str, name: str, started: date = date(2026, 5, 1)) -> MedActive:
    return MedActive(
        key=key,
        name=name,
        column_label=name,
        current_dose="50",
        started_at=started,
        reminder_time=None,  # no schedule → always due
    )


# --- sleep (free hours) ----------------------------------------------------


async def test_msg_sleep_typed_no_meds_goes_to_vpn(session):
    state, data, holder = _make_state()
    msg = _make_msg("6,5")

    await msg_sleep_input(
        msg, state=state, settings=_Settings(), session_factory=_session_factory(session)
    )

    assert data["sleep_hours"] == 6.5
    assert holder["value"] is ReportStates.vpn
    assert "VPN" in msg.answer.call_args.args[0]


async def test_msg_sleep_typed_with_meds_asks_first_med(session):
    session.add(_med("ssri", "Сертралин"))
    await session.commit()

    state, data, holder = _make_state()
    msg = _make_msg("7")

    await msg_sleep_input(
        msg, state=state, settings=_Settings(), session_factory=_session_factory(session)
    )

    assert data["sleep_hours"] == 7.0
    assert holder["value"] is ReportStates.meds
    assert data["meds_pending"] == ["ssri"]
    assert "Сертралин" in msg.answer.call_args.args[0]


async def test_msg_sleep_rejects_garbage(session):
    state, data, holder = _make_state()
    msg = _make_msg("норм")

    await msg_sleep_input(
        msg, state=state, settings=_Settings(), session_factory=_session_factory(session)
    )

    assert "sleep_hours" not in data
    assert holder["value"] is None
    assert "Не понял" in msg.answer.call_args.args[0]


# --- meds skip-already-taken (cb_sleep) ------------------------------------


async def test_report_skips_med_already_taken_via_reminder(session):
    session.add(_med("seizar", "Сейзар"))
    session.add(_med("atarax", "Атаракс", started=date(2026, 5, 2)))
    session.add(MedicationLog(day=date(2026, 5, 13), med_key="seizar", taken=True))
    await session.commit()

    state, data, holder = _make_state()
    cb = _make_cb("sleep:7")

    await cb_sleep(cb, state=state, settings=_Settings(), session_factory=_session_factory(session))

    assert "seizar" not in data["meds_pending"]
    assert "atarax" in data["meds_pending"]
    assert {"key": "seizar", "taken": True} in data["meds_taken"]


async def test_report_skips_med_answered_no(session):
    session.add(_med("seizar", "Сейзар"))
    session.add(MedicationLog(day=date(2026, 5, 13), med_key="seizar", taken=False))
    await session.commit()

    state, data, holder = _make_state()
    cb = _make_cb("sleep:7")

    await cb_sleep(cb, state=state, settings=_Settings(), session_factory=_session_factory(session))

    assert "seizar" not in data["meds_pending"]
    assert data["meds_taken"] == [{"key": "seizar", "taken": False}]


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
    assert "Препарат B" in msg.answer.call_args.args[0]


async def test_msg_med_no_on_last_goes_to_vpn(session):
    session.add(_med("a", "Препарат A"))
    await session.commit()

    state, data, holder = _make_state(meds_pending=["a"], meds_taken=[])
    msg = _make_msg("нет")

    await msg_med_input(msg, state=state, session_factory=_session_factory(session))

    assert data["meds_taken"] == [{"key": "a", "taken": False}]
    assert data["meds_pending"] == []
    assert holder["value"] is ReportStates.vpn


async def test_msg_med_garbage_reprompts(session):
    session.add(_med("a", "Препарат A"))
    await session.commit()

    state, data, holder = _make_state(meds_pending=["a"], meds_taken=[])
    msg = _make_msg("может быть")

    await msg_med_input(msg, state=state, session_factory=_session_factory(session))

    assert data["meds_taken"] == []
    assert data["meds_pending"] == ["a"]
    assert "Не понял" in msg.answer.call_args.args[0]


# --- VPN / English ---------------------------------------------------------


async def test_cb_vpn_numeric_advances_to_english(session):
    state, data, holder = _make_state(sleep_hours=7.0)
    cb = _make_cb("vpn:2")

    await cb_vpn(cb, state=state, session_factory=_session_factory(session))

    assert data["vpn_hours"] == 2.0
    assert holder["value"] is ReportStates.english
    assert "English" in cb.message.edit_text.call_args.args[0]


async def test_msg_vpn_input_parses_ru_word(session):
    state, data, holder = _make_state(sleep_hours=7.0)
    msg = _make_msg("полтора")

    await msg_vpn_input(msg, state=state, session_factory=_session_factory(session))

    assert data["vpn_hours"] == 1.5
    assert holder["value"] is ReportStates.english


async def test_cb_english_saves_and_finishes_on_weekday(session):
    state, data, holder = _make_state(sleep_hours=7.0, vpn_hours=1.0)
    cb = _make_cb("eng:1")

    await cb_english(cb, state=state, session_factory=_session_factory(session))

    assert data["eng_hours"] == 1.0
    saved = await session.get(MoodEntry, date(2026, 5, 13))
    assert saved is not None
    assert saved.sleep_hours == 7.0
    assert saved.vpn_hours == 1.0
    assert saved.eng_hours == 1.0


async def test_msg_english_rejects_misrouted_snooze(session):
    # "45" meant a 45-min med-reminder snooze typed while /report waited on the
    # English step. It must NOT be recorded as 45 hours of English.
    state, data, holder = _make_state(sleep_hours=7.0, vpn_hours=1.0)
    msg = _make_msg("45")

    await msg_english_input(msg, state=state, session_factory=_session_factory(session))

    assert "eng_hours" not in data
    assert "Не понял" in msg.answer.call_args.args[0]


async def test_cb_english_on_saturday_asks_weight(session):
    saturday = "2026-05-16"
    state, data, holder = _make_state(day=saturday, sleep_hours=7.0, vpn_hours=1.0)
    cb = _make_cb("eng:1")

    await cb_english(cb, state=state, session_factory=_session_factory(session))

    assert holder["value"] is ReportStates.weight
    assert "вес" in cb.message.edit_text.call_args.args[0].lower()
