"""/report — once-a-day summary: sleep, meds, VPN/English hours, weight (Sat).

Each answered step is persisted to the DB immediately (write-through), so an
interrupted /report keeps everything answered so far. Re-running /report the same
day resumes from the first unanswered step; a new day starts from scratch.

Meds are only asked once their scheduled time has arrived: a med with a
`reminder_time` later than "now" is skipped so running /report in the morning
doesn't ask about an 11:00 pill. Re-running later re-evaluates due meds.

A standalone bot message (med reminder, evening ping, …) cancels the pending
step — see `rutix.bot.notify.Notifier` — so a reply meant for that message isn't
swallowed by a stale /report prompt. Nothing is lost: the resume logic rebuilds
progress from the persisted values.
"""

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rutix.bot.handlers._ui import kb_grid, send
from rutix.db.models import MedActive, MedicationLog, MoodEntry
from rutix.settings import Settings
from rutix.time_utils import is_saturday, parse_hours_text, subjective_today

logger = logging.getLogger(__name__)

router = Router(name="report")


class ReportStates(StatesGroup):
    sleep = State()
    meds = State()
    vpn = State()
    english = State()
    weight = State()


def _sleep_keyboard() -> InlineKeyboardMarkup:
    return kb_grid(
        [(h, f"sleep:{h}") for h in ("6.5", "7", "7.5", "8", "8.5", "9")],
        cols=3,
    )


def _med_keyboard(key: str) -> InlineKeyboardMarkup:
    return kb_grid([("✓ Да", f"med:{key}:1"), ("✗ Нет", f"med:{key}:0")], cols=2)


def _weight_skip_keyboard() -> InlineKeyboardMarkup:
    return kb_grid([("Пропустить", "weight:skip")], cols=1)


def _hours_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return kb_grid(
        [
            ("0", f"{prefix}:0"),
            ("0.5", f"{prefix}:0.5"),
            ("1", f"{prefix}:1"),
            ("2", f"{prefix}:2"),
        ],
        cols=4,
    )


def _fmt_hours(v: float | None) -> str:
    if v is None:
        return "—"
    return str(int(v)) if v == int(v) else f"{v:g}"


def _now_hhmm(settings: Settings) -> str:
    return datetime.now(ZoneInfo(settings.tz)).strftime("%H:%M")


def _med_due(reminder_time: str | None, now_hhmm: str) -> bool:
    """A med is askable if it has no scheduled time or its time has arrived.

    `reminder_time` is a zero-padded "HH:MM" string, so lexicographic comparison
    matches chronological order.
    """
    return reminder_time is None or reminder_time <= now_hhmm


_YES_WORDS = {"да", "ага", "угу", "+", "1", "yes", "y", "принял", "принимал", "выпил", "пил"}
_NO_WORDS = {"нет", "не", "-", "0", "no", "n", "пропустил", "забыл", "не пил"}


def _parse_yesno(text: str) -> bool | None:
    """Parse a typed yes/no answer for the medication question."""
    s = (text or "").strip().lower()
    if s in _YES_WORDS:
        return True
    if s in _NO_WORDS:
        return False
    return None


# --- Write-through persistence (one field / one med per answered step) -------


async def _persist_fields(session_factory, day: date, **fields) -> None:
    """Upsert today's MoodEntry, setting just the answered field(s). A value of
    0 / 0.0 is a real answer — only `None` means "not yet answered", which is
    what the resume logic keys off."""
    async with session_factory() as session:
        entry = await session.get(MoodEntry, day)
        if entry is None:
            entry = MoodEntry(day=day)
            session.add(entry)
        for key, value in fields.items():
            setattr(entry, key, value)
        await session.commit()


async def _persist_med(session_factory, day: date, key: str, taken: bool) -> None:
    """Upsert a per-med MedicationLog row the moment the user answers it."""
    async with session_factory() as session:
        log = await session.get(MedicationLog, (day, key))
        if log is None:
            session.add(MedicationLog(day=day, med_key=key, taken=taken))
        else:
            log.taken = taken
        await session.commit()


async def _load_meds(session_factory, day: date, now_hhmm: str) -> tuple[list[str], list[dict]]:
    """Return (meds_pending, meds_taken) for today.

    `meds_pending` holds only *due* active meds not yet logged (a med scheduled
    later than now is excluded). `meds_taken` holds every med already logged.
    """
    async with session_factory() as session:
        meds = (
            await session.scalars(
                select(MedActive)
                .where(MedActive.archived_at.is_(None))
                .order_by(MedActive.started_at)
            )
        ).all()
        logged = {
            row.med_key: row.taken
            for row in (
                await session.scalars(select(MedicationLog).where(MedicationLog.day == day))
            ).all()
        }
    meds_taken = [{"key": m.key, "taken": logged[m.key]} for m in meds if m.key in logged]
    meds_pending = [
        m.key for m in meds if m.key not in logged and _med_due(m.reminder_time, now_hhmm)
    ]
    return meds_pending, meds_taken


# --- Resume: rebuild progress from persisted values --------------------------


async def _compute_resume(
    session_factory, day: date, now_hhmm: str
) -> tuple[str | None, dict, MoodEntry | None]:
    """Inspect today's persisted state and return (step, seed_data, existing).

    `step` is the first unanswered step (or None if the day is fully tracked).
    Only meds whose scheduled time has arrived count toward the meds step.
    """
    async with session_factory() as session:
        entry = await session.get(MoodEntry, day)

    meds_pending, meds_taken = await _load_meds(session_factory, day, now_hhmm)

    seed: dict = {"day": day.isoformat(), "meds_taken": meds_taken, "meds_pending": meds_pending}
    if entry is not None:
        for f in ("sleep_hours", "vpn_hours", "eng_hours", "weight"):
            value = getattr(entry, f)
            if value is not None:
                seed[f] = value

    def missing(field: str) -> bool:
        return entry is None or getattr(entry, field) is None

    if missing("sleep_hours"):
        step: str | None = "sleep"
    elif meds_pending:
        step = "meds"
    elif missing("vpn_hours"):
        step = "vpn"
    elif missing("eng_hours"):
        step = "english"
    elif is_saturday(day) and missing("weight"):
        step = "weight"
    else:
        step = None
    return step, seed, entry


async def _prompt_step(message: Message, state: FSMContext, step: str, session_factory) -> None:
    """Post the prompt for a resume step (plain question, no "previous: X" prefix)."""
    if step == "sleep":
        await state.set_state(ReportStates.sleep)
        await message.answer("Сколько часов спали?", reply_markup=_sleep_keyboard())
    elif step == "meds":
        await state.set_state(ReportStates.meds)
        await _ask_next_med(message, state, session_factory, use_answer=True)
    elif step == "vpn":
        await _ask_vpn(message, state, use_answer=True)
    elif step == "english":
        await _ask_english(message, state, use_answer=True)
    elif step == "weight":
        await state.set_state(ReportStates.weight)
        await message.answer(
            "Какой сегодня вес (кг)? Напишите числом или нажмите «Пропустить».",
            reply_markup=_weight_skip_keyboard(),
        )


@router.message(Command("report"))
async def cmd_report(
    message: Message,
    state: FSMContext,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
):
    today = subjective_today(datetime.now(ZoneInfo(settings.tz)), settings.tz)
    step, seed, existing = await _compute_resume(session_factory, today, _now_hhmm(settings))

    await state.clear()
    await state.update_data(**seed)

    if existing is None:
        await state.set_state(ReportStates.sleep)
        await message.answer(
            f"📋 Отчёт за {today.isoformat()}.\n\nСколько часов спали?",
            reply_markup=_sleep_keyboard(),
        )
        return

    if step is None:
        await state.set_state(ReportStates.sleep)
        await message.answer(
            f"✅ Отчёт за {today.isoformat()} уже заполнен. Пройдём заново.\n\n"
            "Сколько часов спали?",
            reply_markup=_sleep_keyboard(),
        )
        return

    await message.answer(f"📋 Продолжаем отчёт за {today.isoformat()}.")
    await _prompt_step(message, state, step, session_factory)


# --- Sleep → meds ------------------------------------------------------------


async def _go_meds(
    message: Message,
    state: FSMContext,
    sleep_hours: float,
    session_factory,
    now_hhmm: str,
    *,
    use_answer=False,
):
    await state.update_data(sleep_hours=sleep_hours)
    data = await state.get_data()
    day = date.fromisoformat(data["day"])
    await _persist_fields(session_factory, day, sleep_hours=sleep_hours)

    await state.set_state(ReportStates.meds)
    meds_pending, meds_taken = await _load_meds(session_factory, day, now_hhmm)
    await state.update_data(meds_pending=meds_pending, meds_taken=meds_taken)

    if meds_pending:
        await _ask_next_med(message, state, session_factory, use_answer=use_answer)
    else:
        await _ask_vpn(message, state, use_answer=use_answer)


@router.callback_query(ReportStates.sleep, F.data.startswith("sleep:"))
async def cb_sleep(
    cb: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
):
    value = float(cb.data.split(":", 1)[1])
    await _go_meds(cb.message, state, value, session_factory, _now_hhmm(settings))
    await cb.answer()


@router.message(ReportStates.sleep, F.text)
async def msg_sleep_input(
    message: Message,
    state: FSMContext,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
):
    hours = parse_hours_text(message.text)
    if hours is None:
        await message.answer("⚠️ Не понял. Напишите число часов (7, 7.5, 8ч).")
        return
    await _go_meds(message, state, hours, session_factory, _now_hhmm(settings), use_answer=True)


# --- Meds --------------------------------------------------------------------


async def _ask_next_med(message: Message, state: FSMContext, session_factory, *, use_answer=False):
    data = await state.get_data()
    pending = list(data.get("meds_pending", []))
    if not pending:
        return await _ask_vpn(message, state, use_answer=use_answer)
    next_key = pending[0]
    async with session_factory() as session:
        med = await session.get(MedActive, next_key)
    if med is None:
        await state.update_data(meds_pending=pending[1:])
        return await _ask_next_med(message, state, session_factory, use_answer=use_answer)
    await send(
        message,
        f"Принимали {med.name} ({med.current_dose} мг)?",
        _med_keyboard(next_key),
        use_answer,
    )


@router.callback_query(ReportStates.meds, F.data.startswith("med:"))
async def cb_med(
    cb: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    _, key, taken_str = cb.data.split(":", 2)
    taken = bool(int(taken_str))

    data = await state.get_data()
    await _persist_med(session_factory, date.fromisoformat(data["day"]), key, taken)

    taken_list = list(data.get("meds_taken", []))
    taken_list.append({"key": key, "taken": taken})
    pending = [k for k in data.get("meds_pending", []) if k != key]
    await state.update_data(meds_taken=taken_list, meds_pending=pending)

    if pending:
        await _ask_next_med(cb.message, state, session_factory)
    else:
        await _ask_vpn(cb.message, state)
    await cb.answer()


@router.message(ReportStates.meds, F.text)
async def msg_med_input(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    data = await state.get_data()
    pending = list(data.get("meds_pending", []))
    if not pending:
        await _ask_vpn(message, state, use_answer=True)
        return
    taken = _parse_yesno(message.text)
    if taken is None:
        await message.answer("⚠️ Не понял. Ответьте «да» или «нет» (или нажмите кнопку).")
        return
    key = pending[0]
    await _persist_med(session_factory, date.fromisoformat(data["day"]), key, taken)

    taken_list = list(data.get("meds_taken", []))
    taken_list.append({"key": key, "taken": taken})
    pending = pending[1:]
    await state.update_data(meds_taken=taken_list, meds_pending=pending)

    if pending:
        await _ask_next_med(message, state, session_factory, use_answer=True)
    else:
        await _ask_vpn(message, state, use_answer=True)


# --- VPN / English (free hours via inline buttons + text fallback) ---------


async def _ask_vpn(message: Message, state: FSMContext, *, use_answer: bool = False):
    await state.set_state(ReportStates.vpn)
    await send(message, "VPN сегодня (ч)?", _hours_keyboard("vpn"), use_answer)


async def _ask_english(message: Message, state: FSMContext, *, use_answer: bool = False):
    await state.set_state(ReportStates.english)
    await send(message, "English сегодня (ч)?", _hours_keyboard("eng"), use_answer)


@router.callback_query(ReportStates.vpn, F.data.startswith("vpn:"))
async def cb_vpn(
    cb: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    hours = float(cb.data.split(":", 1)[1])
    await state.update_data(vpn_hours=hours)
    data = await state.get_data()
    await _persist_fields(session_factory, date.fromisoformat(data["day"]), vpn_hours=hours)
    await _ask_english(cb.message, state)
    await cb.answer()


@router.message(ReportStates.vpn, F.text)
async def msg_vpn_input(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    hours = parse_hours_text(message.text)
    if hours is None:
        await message.answer("⚠️ Не понял. Введите число часов за день (0–24): 1.5, 2ч, полтора.")
        return
    await state.update_data(vpn_hours=hours)
    data = await state.get_data()
    await _persist_fields(session_factory, date.fromisoformat(data["day"]), vpn_hours=hours)
    await _ask_english(message, state, use_answer=True)


@router.callback_query(ReportStates.english, F.data.startswith("eng:"))
async def cb_english(
    cb: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    hours = float(cb.data.split(":", 1)[1])
    await state.update_data(eng_hours=hours)
    data = await state.get_data()
    await _persist_fields(session_factory, date.fromisoformat(data["day"]), eng_hours=hours)
    await _maybe_ask_weight_or_save(cb.message, state, session_factory)
    await cb.answer()


@router.message(ReportStates.english, F.text)
async def msg_english_input(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    hours = parse_hours_text(message.text)
    if hours is None:
        await message.answer("⚠️ Не понял. Введите число часов за день (0–24): 1.5, 2ч, полтора.")
        return
    await state.update_data(eng_hours=hours)
    data = await state.get_data()
    await _persist_fields(session_factory, date.fromisoformat(data["day"]), eng_hours=hours)
    await _maybe_ask_weight_or_save(message, state, session_factory, use_answer=True)


# --- Weight (Saturday only) → finish ----------------------------------------


async def _maybe_ask_weight_or_save(
    message: Message,
    state: FSMContext,
    session_factory,
    *,
    use_answer: bool = False,
):
    data = await state.get_data()
    day = date.fromisoformat(data["day"])
    if is_saturday(day):
        await state.set_state(ReportStates.weight)
        prompt = "Какой сегодня вес (кг)? Напишите числом или нажмите «Пропустить»."
        if use_answer:
            await message.answer(prompt, reply_markup=_weight_skip_keyboard())
        else:
            await message.edit_text(prompt, reply_markup=_weight_skip_keyboard())
    else:
        await _save_and_finish(message, state, session_factory, use_answer=use_answer)


@router.message(ReportStates.weight, F.text)
async def msg_weight(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    try:
        weight = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("⚠️ Это не число. Попробуйте ещё раз или нажмите «Пропустить».")
        return
    await state.update_data(weight=weight)
    data = await state.get_data()
    await _persist_fields(session_factory, date.fromisoformat(data["day"]), weight=weight)
    await _save_and_finish(message, state, session_factory, use_answer=True)


@router.callback_query(ReportStates.weight, F.data == "weight:skip")
async def cb_weight_skip(
    cb: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    await _save_and_finish(cb.message, state, session_factory)
    await cb.answer()


async def _save_and_finish(
    message: Message,
    state: FSMContext,
    session_factory,
    *,
    use_answer: bool = False,
):
    data = await state.get_data()
    day = date.fromisoformat(data["day"])

    # Steps were written through as they were answered; this final upsert is an
    # idempotent safety net that also covers a skipped (button) weight step.
    async with session_factory() as session:
        existing = await session.get(MoodEntry, day)
        if existing:
            existing.sleep_hours = data.get("sleep_hours")
            existing.vpn_hours = data.get("vpn_hours")
            existing.eng_hours = data.get("eng_hours")
            if "weight" in data:
                existing.weight = data["weight"]
        else:
            session.add(
                MoodEntry(
                    day=day,
                    sleep_hours=data.get("sleep_hours"),
                    vpn_hours=data.get("vpn_hours"),
                    eng_hours=data.get("eng_hours"),
                    weight=data.get("weight"),
                )
            )
        for entry in data.get("meds_taken", []):
            log = await session.get(MedicationLog, (day, entry["key"]))
            if log:
                log.taken = entry["taken"]
            else:
                session.add(MedicationLog(day=day, med_key=entry["key"], taken=entry["taken"]))
        await session.commit()

    summary = (
        f"✅ Отчёт за {day.isoformat()}:\n"
        f"сон {data.get('sleep_hours', '?')}ч, "
        f"VPN {_fmt_hours(data.get('vpn_hours'))}ч, "
        f"English {_fmt_hours(data.get('eng_hours'))}ч"
    )
    if "weight" in data:
        summary += f", вес {data['weight']}кг"
    if use_answer:
        await message.answer(summary)
    else:
        await message.edit_text(summary)
    await state.clear()
    logger.info("report saved for %s by handler", day)
