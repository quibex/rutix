"""/schedule — turn each cron job on/off and move its time, from the chat.

Reads and writes through `JobPrefsManager`, which persists the change and
re-registers the APScheduler job on the spot: no restart, no redeploy. The
message is a live view — every toggle re-renders it in place, so the list
always shows what the scheduler is actually doing right now.
"""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from rutix.jobs.job_prefs import JobPrefsManager, JobSetting, UnknownJobError
from rutix.jobs.med_reminder import parse_reminder_time

logger = logging.getLogger(__name__)

router = Router(name="schedule")

CB_PREFIX = "sched"

HEADER = "⏰ <b>Расписание</b>"
FOOTER = "Нажми на джобу, чтобы включить или выключить. 🕒 — изменить время."


class ScheduleStates(StatesGroup):
    edit_time = State()


def _status_icon(setting: JobSetting) -> str:
    return "✅" if setting.enabled else "🚫"


def _job_line(setting: JobSetting) -> str:
    when = setting.time_label if setting.enabled else f"выключено, было {setting.time_label}"
    head = f"{_status_icon(setting)} <b>{setting.spec.label}</b> — {when}"
    return f"{head}\n   ↳ {setting.spec.description}"


def format_schedule(settings: list[JobSetting]) -> str:
    lines = [HEADER, ""]
    lines.extend(_job_line(s) for s in settings)
    lines.append("")
    lines.append(FOOTER)
    return "\n".join(lines)


def build_keyboard(settings: list[JobSetting]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for s in settings:
        row = [
            InlineKeyboardButton(
                text=f"{_status_icon(s)} {s.spec.label}",
                callback_data=f"{CB_PREFIX}:toggle:{s.job_id}",
            )
        ]
        if s.spec.time_configurable:
            row.append(
                InlineKeyboardButton(
                    text=f"🕒 {s.time_label}",
                    callback_data=f"{CB_PREFIX}:time:{s.job_id}",
                )
            )
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render(cb: CallbackQuery, job_prefs: JobPrefsManager) -> None:
    settings = job_prefs.all()
    await cb.message.edit_text(format_schedule(settings), reply_markup=build_keyboard(settings))


@router.message(Command("schedule"))
async def cmd_schedule(message: Message, job_prefs: JobPrefsManager):
    settings = job_prefs.all()
    await message.answer(format_schedule(settings), reply_markup=build_keyboard(settings))


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:toggle:"))
async def cb_toggle(cb: CallbackQuery, job_prefs: JobPrefsManager):
    job_id = cb.data.split(":", 2)[2]
    try:
        setting = await job_prefs.toggle(job_id)
    except UnknownJobError:
        await cb.answer("⚠️ Такой джобы больше нет.", show_alert=True)
        return
    await _render(cb, job_prefs)
    await cb.answer(f"{setting.spec.label}: {'включено' if setting.enabled else 'выключено'}")


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:time:"))
async def cb_time_pick(cb: CallbackQuery, state: FSMContext, job_prefs: JobPrefsManager):
    job_id = cb.data.split(":", 2)[2]
    try:
        setting = job_prefs.get(job_id)
    except UnknownJobError:
        await cb.answer("⚠️ Такой джобы больше нет.", show_alert=True)
        return
    if not setting.spec.time_configurable:
        await cb.answer("У этой джобы нет времени — она работает по опросу.", show_alert=True)
        return

    await state.set_state(ScheduleStates.edit_time)
    await state.update_data(job_id=job_id)
    await cb.message.edit_text(
        f"🕒 <b>{setting.spec.label}</b> — сейчас {setting.time_label}.\n"
        f"Новое время HH:MM (например 09:30). По умолчанию: {setting.spec.default_time_label}."
    )
    await cb.answer()


@router.message(ScheduleStates.edit_time, F.text)
async def msg_time_value(message: Message, state: FSMContext, job_prefs: JobPrefsManager):
    try:
        hh_mm = parse_reminder_time(message.text.strip())
    except ValueError:
        await message.answer("⚠️ Не понял время. Введи HH:MM, например 09:30.")
        return  # stay in state so the next message retries

    data = await state.get_data()
    job_id = data.get("job_id", "")
    hour, minute = hh_mm.split(":")
    try:
        setting = await job_prefs.set_time(job_id, hour, minute)
    except (UnknownJobError, ValueError):
        await state.clear()
        await message.answer("⚠️ Не нашёл эту джобу.")
        return
    await state.clear()

    tail = "" if setting.enabled else "\nДжоба сейчас выключена — включи её в /schedule."
    await message.answer(f"✅ <b>{setting.spec.label}</b> — теперь в {setting.time_label}.{tail}")
