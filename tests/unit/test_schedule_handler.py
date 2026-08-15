"""/schedule — the chat-side view of the cron schedule."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from rutix.bot.handlers.schedule import (
    cb_time_pick,
    cb_toggle,
    cmd_schedule,
    msg_time_value,
)
from rutix.jobs.job_prefs import JobPrefsManager


def _session_factory(session):
    def factory():
        class CM:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, *a):
                pass

        return CM()

    return factory


@pytest.fixture
async def job_prefs(session):
    m = JobPrefsManager(_session_factory(session))
    await m.load()
    return m


@pytest.fixture
def fake_message():
    m = MagicMock()
    m.answer = AsyncMock()
    return m


@pytest.fixture
def fake_state():
    s = MagicMock()
    s.set_state = AsyncMock()
    s.update_data = AsyncMock()
    s.get_data = AsyncMock(return_value={})
    s.clear = AsyncMock()
    return s


def _fake_callback(data: str):
    cb = MagicMock()
    cb.data = data
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()
    return cb


def _buttons(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


async def test_cmd_schedule_lists_every_job_with_its_time(fake_message, job_prefs):
    await cmd_schedule(fake_message, job_prefs=job_prefs)

    text = fake_message.answer.call_args.args[0]
    assert "План на день" in text
    assert "09:00" in text
    assert "Ночная сводка" in text
    assert "03:00" in text
    # The polling jobs are listed too, described by cadence rather than a time.
    assert "каждую минуту" in text
    assert "каждый час в :07" in text

    labels = _buttons(fake_message.answer.call_args.kwargs["reply_markup"])
    assert any("🕒 09:00" in label for label in labels)
    assert all("✅" in label or "🕒" in label for label in labels)


async def test_toggle_turns_a_job_off_and_redraws(job_prefs):
    cb = _fake_callback("sched:toggle:daily_plan_ping")

    await cb_toggle(cb, job_prefs=job_prefs)

    assert job_prefs.get("daily_plan_ping").enabled is False
    text = cb.message.edit_text.call_args.args[0]
    assert "🚫" in text
    assert "выключено" in text
    labels = _buttons(cb.message.edit_text.call_args.kwargs["reply_markup"])
    assert any(label.startswith("🚫") for label in labels)


async def test_toggle_of_an_unknown_job_is_reported_not_raised(job_prefs):
    cb = _fake_callback("sched:toggle:ghost_job")

    await cb_toggle(cb, job_prefs=job_prefs)

    assert cb.answer.call_args.kwargs["show_alert"] is True
    cb.message.edit_text.assert_not_called()


async def test_time_button_asks_for_hh_mm(fake_state, job_prefs):
    cb = _fake_callback("sched:time:daily_plan_ping")

    await cb_time_pick(cb, state=fake_state, job_prefs=job_prefs)

    fake_state.update_data.assert_awaited_with(job_id="daily_plan_ping")
    prompt = cb.message.edit_text.call_args.args[0]
    assert "HH:MM" in prompt
    assert "09:00" in prompt


async def test_time_button_refuses_the_polling_jobs(fake_state, job_prefs):
    cb = _fake_callback("sched:time:med_reminder_tick")

    await cb_time_pick(cb, state=fake_state, job_prefs=job_prefs)

    assert cb.answer.call_args.kwargs["show_alert"] is True
    fake_state.set_state.assert_not_called()


async def test_typing_a_new_time_moves_the_job(fake_message, fake_state, job_prefs):
    fake_state.get_data = AsyncMock(return_value={"job_id": "daily_plan_ping"})
    fake_message.text = "10:30"

    await msg_time_value(fake_message, state=fake_state, job_prefs=job_prefs)

    setting = job_prefs.get("daily_plan_ping")
    assert (setting.hour, setting.minute) == ("10", "30")
    assert "10:30" in fake_message.answer.call_args.args[0]
    fake_state.clear.assert_awaited()


async def test_a_bad_time_keeps_the_flow_open(fake_message, fake_state, job_prefs):
    fake_state.get_data = AsyncMock(return_value={"job_id": "daily_plan_ping"})
    fake_message.text = "завтра утром"

    await msg_time_value(fake_message, state=fake_state, job_prefs=job_prefs)

    assert "Не понял время" in fake_message.answer.call_args.args[0]
    fake_state.clear.assert_not_called()
    assert job_prefs.get("daily_plan_ping").time_label == "09:00"


async def test_setting_a_time_on_a_disabled_job_says_it_is_still_off(
    fake_message, fake_state, job_prefs
):
    await job_prefs.set_enabled("daily_plan_ping", False)
    fake_state.get_data = AsyncMock(return_value={"job_id": "daily_plan_ping"})
    fake_message.text = "10:30"

    await msg_time_value(fake_message, state=fake_state, job_prefs=job_prefs)

    reply = fake_message.answer.call_args.args[0]
    assert "10:30" in reply
    assert "выключена" in reply
