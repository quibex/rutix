"""/track — multi-step mood entry via inline buttons.

Each answered step is persisted to the DB immediately (write-through), so an
interrupted /track keeps everything answered so far. Re-running /track the same
day resumes from the first unanswered step; a new day starts from scratch.

A standalone bot message (med reminder, evening ping, …) cancels the pending
step — see `rutix.bot.notify.Notifier` — so a reply meant for that message
isn't swallowed by a stale /track prompt. Nothing is lost: the resume logic
rebuilds progress from the persisted values.
"""

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rutix.db.models import MedActive, MedicationLog, MoodEntry
from rutix.settings import Settings
from rutix.time_utils import is_saturday, parse_hours_text, subjective_today

logger = logging.getLogger(__name__)

router = Router(name="track")


class TrackStates(StatesGroup):
    mood = State()
    anxiety = State()
    irritability = State()
    energy = State()
    appetite = State()
    sleep = State()
    meds = State()
    vpn = State()
    english = State()
    weight = State()


def _kb_grid(values: list[tuple[str, str]], cols: int) -> InlineKeyboardMarkup:
    rows = [values[i : i + cols] for i in range(0, len(values), cols)]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=cb) for label, cb in row]
            for row in rows
        ]
    )


def _mood_keyboard() -> InlineKeyboardMarkup:
    return _kb_grid(
        [
            ("-3", "mood:-3"),
            ("-2", "mood:-2"),
            ("-1", "mood:-1"),
            ("0", "mood:0"),
            ("+1", "mood:1"),
            ("+2", "mood:2"),
            ("+3", "mood:3"),
        ],
        cols=4,
    )


def _0_to_3(prefix: str) -> InlineKeyboardMarkup:
    return _kb_grid([(str(i), f"{prefix}:{i}") for i in range(4)], cols=4)


def _energy_keyboard_generic(prefix: str = "energy") -> InlineKeyboardMarkup:
    return _kb_grid(
        [
            ("-2", f"{prefix}:-2"),
            ("-1", f"{prefix}:-1"),
            ("0", f"{prefix}:0"),
            ("+1", f"{prefix}:1"),
            ("+2", f"{prefix}:2"),
        ],
        cols=5,
    )


def _energy_keyboard() -> InlineKeyboardMarkup:
    return _energy_keyboard_generic("energy")


def _sleep_keyboard() -> InlineKeyboardMarkup:
    return _kb_grid(
        [(h, f"sleep:{h}") for h in ("6.5", "7", "7.5", "8", "8.5", "9")],
        cols=3,
    )


def _med_keyboard(key: str) -> InlineKeyboardMarkup:
    return _kb_grid([("✓ Да", f"med:{key}:1"), ("✗ Нет", f"med:{key}:0")], cols=2)


def _weight_skip_keyboard() -> InlineKeyboardMarkup:
    return _kb_grid([("Пропустить", "weight:skip")], cols=1)


def _hours_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return _kb_grid(
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


def _parse_score(text: str, lo: int, hi: int) -> int | None:
    """Parse a typed integer score like "1", "+2", "-3", "0" within [lo, hi].

    Accepts leading +/-, unicode minus/dashes and a trailing ".0". Returns None
    for non-integers or out-of-range values.
    """
    if not text:
        return None
    s = text.strip().replace("−", "-").replace("–", "-").replace("—", "-").replace(",", ".")
    try:
        f = float(s)
    except ValueError:
        return None
    if f != int(f):
        return None
    v = int(f)
    if not (lo <= v <= hi):
        return None
    return v


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


async def _send(message: Message, text: str, kb: InlineKeyboardMarkup, use_answer: bool):
    """Send the next prompt — edit the prior message (button flow) or post a
    fresh one (text-input flow, since you can't edit the user's own message)."""
    if use_answer:
        await message.answer(text, reply_markup=kb)
    else:
        await message.edit_text(text, reply_markup=kb)


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


# --- Resume: rebuild progress from persisted values --------------------------


async def _compute_resume(session_factory, day: date) -> tuple[str | None, dict, MoodEntry | None]:
    """Inspect today's persisted state and return (step, seed_data, existing).

    `step` is the first unanswered step (or None if the day is fully tracked).
    `seed_data` pre-fills the FSM with everything already answered so the final
    summary is complete and the meds step skips meds already logged.
    `existing` is the MoodEntry row (None ⇒ a fresh day).
    """
    async with session_factory() as session:
        entry = await session.get(MoodEntry, day)
        meds = (
            await session.scalars(
                select(MedActive)
                .where(MedActive.archived_at.is_(None))
                .order_by(MedActive.started_at, MedActive.name)
            )
        ).all()
        log_rows = (
            await session.scalars(select(MedicationLog).where(MedicationLog.day == day))
        ).all()

    logged = {row.med_key: row.taken for row in log_rows}
    meds_pending = [m.key for m in meds if m.key not in logged]
    meds_taken = [{"key": m.key, "taken": logged[m.key]} for m in meds if m.key in logged]

    seed: dict = {"day": day.isoformat(), "meds_taken": meds_taken, "meds_pending": meds_pending}
    if entry is not None:
        for field in (
            "mood",
            "anxiety",
            "irritability",
            "energy",
            "appetite",
            "sleep_hours",
            "vpn_hours",
            "eng_hours",
            "weight",
        ):
            value = getattr(entry, field)
            if value is not None:
                seed[field] = value

    def missing(field: str) -> bool:
        return entry is None or getattr(entry, field) is None

    if missing("mood"):
        step: str | None = "mood"
    elif missing("anxiety"):
        step = "anxiety"
    elif missing("irritability"):
        step = "irritability"
    elif missing("energy"):
        step = "energy"
    elif missing("appetite"):
        step = "appetite"
    elif missing("sleep_hours"):
        step = "sleep"
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
    if step == "mood":
        await state.set_state(TrackStates.mood)
        await message.answer("Какое было настроение?", reply_markup=_mood_keyboard())
    elif step == "anxiety":
        await state.set_state(TrackStates.anxiety)
        await message.answer("Какая была тревога?", reply_markup=_0_to_3("anx"))
    elif step == "irritability":
        await state.set_state(TrackStates.irritability)
        await message.answer("Какая была раздражительность?", reply_markup=_0_to_3("irr"))
    elif step == "energy":
        await state.set_state(TrackStates.energy)
        await message.answer("Сколько было сил/энергии?", reply_markup=_energy_keyboard())
    elif step == "appetite":
        await state.set_state(TrackStates.appetite)
        await message.answer(
            "Какой был аппетит?", reply_markup=_energy_keyboard_generic("appetite")
        )
    elif step == "sleep":
        await state.set_state(TrackStates.sleep)
        await message.answer("Сколько часов спали?", reply_markup=_sleep_keyboard())
    elif step == "meds":
        await state.set_state(TrackStates.meds)
        await _ask_next_med(message, state, session_factory, use_answer=True)
    elif step == "vpn":
        await _ask_vpn(message, state, use_answer=True)
    elif step == "english":
        await _ask_english(message, state, use_answer=True)
    elif step == "weight":
        await state.set_state(TrackStates.weight)
        await message.answer(
            "Какой сегодня вес (кг)? Напишите числом или нажмите «Пропустить».",
            reply_markup=_weight_skip_keyboard(),
        )


@router.message(Command("track"))
async def cmd_track(
    message: Message,
    state: FSMContext,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
):
    today = subjective_today(datetime.now(ZoneInfo(settings.tz)), settings.tz)
    step, seed, existing = await _compute_resume(session_factory, today)

    await state.clear()
    await state.update_data(**seed)

    if existing is None:
        await state.set_state(TrackStates.mood)
        await message.answer(
            f"📊 Трек за {today.isoformat()}.\n\nКакое было настроение?",
            reply_markup=_mood_keyboard(),
        )
        return

    if step is None:
        await state.set_state(TrackStates.mood)
        await message.answer(
            f"✅ Трек за {today.isoformat()} уже заполнен. Пройдём заново.\n\n"
            "Какое было настроение?",
            reply_markup=_mood_keyboard(),
        )
        return

    await message.answer(f"📊 Продолжаем трек за {today.isoformat()}.")
    await _prompt_step(message, state, step, session_factory)


# --- Stale-session guard ---------------------------------------------------
#
# FSM state lives in memory and survives across days while the bot runs. An
# abandoned /track (e.g. the user never answered the Saturday weight step)
# would otherwise sit in a TrackStates state and silently eat the next number
# the user types for something else — like a med-reminder snooze read as weight.
# This guard catches input that lands in a /track step started on an earlier
# subjective day, drops the session, and asks the user to repeat.


class _StaleTrackFilter(BaseFilter):
    async def __call__(self, message: Message, state: FSMContext, settings: Settings) -> bool:
        current = await state.get_state()
        if current is None or not current.startswith(f"{TrackStates.__name__}:"):
            return False
        day_iso = (await state.get_data()).get("day")
        if not day_iso:
            return False
        today = subjective_today(datetime.now(ZoneInfo(settings.tz)), settings.tz)
        return date.fromisoformat(day_iso) < today


@router.message(_StaleTrackFilter())
async def msg_track_stale(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "⏳ Прошлая сессия /track устарела и отменена.\n"
        "Повторите ввод или начните заново через /track."
    )


# --- Step transitions (shared by button callbacks and text input) ----------


async def _go_anxiety(
    message: Message, state: FSMContext, mood: int, session_factory, *, use_answer=False
):
    await state.update_data(mood=mood)
    data = await state.get_data()
    await _persist_fields(session_factory, date.fromisoformat(data["day"]), mood=mood)
    await state.set_state(TrackStates.anxiety)
    await _send(
        message,
        f"Настроение: {mood:+d}.\n\nКакая была тревога?",
        _0_to_3("anx"),
        use_answer,
    )


async def _go_irritability(
    message: Message, state: FSMContext, anxiety: int, session_factory, *, use_answer=False
):
    await state.update_data(anxiety=anxiety)
    data = await state.get_data()
    await _persist_fields(session_factory, date.fromisoformat(data["day"]), anxiety=anxiety)
    await state.set_state(TrackStates.irritability)
    await _send(
        message,
        f"Тревога: {anxiety}.\n\nКакая была раздражительность?",
        _0_to_3("irr"),
        use_answer,
    )


async def _go_energy(
    message: Message, state: FSMContext, irritability: int, session_factory, *, use_answer=False
):
    await state.update_data(irritability=irritability)
    data = await state.get_data()
    await _persist_fields(
        session_factory, date.fromisoformat(data["day"]), irritability=irritability
    )
    await state.set_state(TrackStates.energy)
    await _send(
        message,
        f"Раздражительность: {irritability}.\n\nСколько было сил/энергии?",
        _energy_keyboard(),
        use_answer,
    )


async def _go_appetite(
    message: Message, state: FSMContext, energy: int, session_factory, *, use_answer=False
):
    await state.update_data(energy=energy)
    data = await state.get_data()
    await _persist_fields(session_factory, date.fromisoformat(data["day"]), energy=energy)
    await state.set_state(TrackStates.appetite)
    await _send(
        message,
        f"Энергия: {energy:+d}.\n\nКакой был аппетит?",
        _energy_keyboard_generic("appetite"),
        use_answer,
    )


async def _go_sleep(
    message: Message, state: FSMContext, appetite: int, session_factory, *, use_answer=False
):
    await state.update_data(appetite=appetite)
    data = await state.get_data()
    await _persist_fields(session_factory, date.fromisoformat(data["day"]), appetite=appetite)
    await state.set_state(TrackStates.sleep)
    await _send(
        message,
        f"Аппетит: {appetite:+d}.\n\nСколько часов спали?",
        _sleep_keyboard(),
        use_answer,
    )


async def _go_meds(
    message: Message,
    state: FSMContext,
    sleep_hours: float,
    session_factory,
    *,
    use_answer=False,
):
    await state.update_data(sleep_hours=sleep_hours)

    data = await state.get_data()
    day = date.fromisoformat(data["day"])
    await _persist_fields(session_factory, day, sleep_hours=sleep_hours)

    await state.set_state(TrackStates.meds)

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

    # A med with any log row for today (taken True *or* False) is already
    # answered — skip it so resume doesn't re-ask answered meds.
    meds_taken = [{"key": m.key, "taken": logged[m.key]} for m in meds if m.key in logged]
    meds_pending = [m.key for m in meds if m.key not in logged]
    await state.update_data(meds_pending=meds_pending, meds_taken=meds_taken)

    if meds_pending:
        await _ask_next_med(message, state, session_factory, use_answer=use_answer)
    else:
        await _ask_vpn(message, state, use_answer=use_answer)


@router.callback_query(TrackStates.mood, F.data.startswith("mood:"))
async def cb_mood(
    cb: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    value = int(cb.data.split(":", 1)[1])
    await _go_anxiety(cb.message, state, value, session_factory)
    await cb.answer()


@router.message(TrackStates.mood, F.text)
async def msg_mood_input(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    value = _parse_score(message.text, -3, 3)
    if value is None:
        await message.answer("⚠️ Не понял. Напишите число от −3 до +3 (или нажмите кнопку).")
        return
    await _go_anxiety(message, state, value, session_factory, use_answer=True)


@router.callback_query(TrackStates.anxiety, F.data.startswith("anx:"))
async def cb_anxiety(
    cb: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    value = int(cb.data.split(":", 1)[1])
    await _go_irritability(cb.message, state, value, session_factory)
    await cb.answer()


@router.message(TrackStates.anxiety, F.text)
async def msg_anxiety_input(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    value = _parse_score(message.text, 0, 3)
    if value is None:
        await message.answer("⚠️ Не понял. Напишите число от 0 до 3 (или нажмите кнопку).")
        return
    await _go_irritability(message, state, value, session_factory, use_answer=True)


@router.callback_query(TrackStates.irritability, F.data.startswith("irr:"))
async def cb_irritability(
    cb: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    value = int(cb.data.split(":", 1)[1])
    await _go_energy(cb.message, state, value, session_factory)
    await cb.answer()


@router.message(TrackStates.irritability, F.text)
async def msg_irritability_input(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    value = _parse_score(message.text, 0, 3)
    if value is None:
        await message.answer("⚠️ Не понял. Напишите число от 0 до 3 (или нажмите кнопку).")
        return
    await _go_energy(message, state, value, session_factory, use_answer=True)


@router.callback_query(TrackStates.energy, F.data.startswith("energy:"))
async def cb_energy(
    cb: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    value = int(cb.data.split(":", 1)[1])
    await _go_appetite(cb.message, state, value, session_factory)
    await cb.answer()


@router.message(TrackStates.energy, F.text)
async def msg_energy_input(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    value = _parse_score(message.text, -2, 2)
    if value is None:
        await message.answer("⚠️ Не понял. Напишите число от −2 до +2 (или нажмите кнопку).")
        return
    await _go_appetite(message, state, value, session_factory, use_answer=True)


@router.callback_query(TrackStates.appetite, F.data.startswith("appetite:"))
async def cb_appetite(
    cb: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    value = int(cb.data.split(":", 1)[1])
    await _go_sleep(cb.message, state, value, session_factory)
    await cb.answer()


@router.message(TrackStates.appetite, F.text)
async def msg_appetite_input(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    value = _parse_score(message.text, -2, 2)
    if value is None:
        await message.answer("⚠️ Не понял. Напишите число от −2 до +2 (или нажмите кнопку).")
        return
    await _go_sleep(message, state, value, session_factory, use_answer=True)


@router.callback_query(TrackStates.sleep, F.data.startswith("sleep:"))
async def cb_sleep(
    cb: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    value = float(cb.data.split(":", 1)[1])
    await _go_meds(cb.message, state, value, session_factory)
    await cb.answer()


@router.message(TrackStates.sleep, F.text)
async def msg_sleep_input(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    hours = parse_hours_text(message.text)
    if hours is None:
        await message.answer("⚠️ Не понял. Напишите число часов (7, 7.5, 8ч).")
        return
    await _go_meds(message, state, hours, session_factory, use_answer=True)


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
    await _send(
        message,
        f"Принимали {med.name} ({med.current_dose} мг)?",
        _med_keyboard(next_key),
        use_answer,
    )


@router.callback_query(TrackStates.meds, F.data.startswith("med:"))
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


@router.message(TrackStates.meds, F.text)
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
    await state.set_state(TrackStates.vpn)
    await _send(message, "VPN сегодня (ч)?", _hours_keyboard("vpn"), use_answer)


async def _ask_english(message: Message, state: FSMContext, *, use_answer: bool = False):
    await state.set_state(TrackStates.english)
    text = "English сегодня (ч)?"
    if use_answer:
        await message.answer(text, reply_markup=_hours_keyboard("eng"))
    else:
        await message.edit_text(text, reply_markup=_hours_keyboard("eng"))


@router.callback_query(TrackStates.vpn, F.data.startswith("vpn:"))
async def cb_vpn(
    cb: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    payload = cb.data.split(":", 1)[1]
    hours = float(payload)
    await state.update_data(vpn_hours=hours)
    data = await state.get_data()
    await _persist_fields(session_factory, date.fromisoformat(data["day"]), vpn_hours=hours)
    await _ask_english(cb.message, state)
    await cb.answer()


@router.message(TrackStates.vpn, F.text)
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


@router.callback_query(TrackStates.english, F.data.startswith("eng:"))
async def cb_english(
    cb: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    payload = cb.data.split(":", 1)[1]
    hours = float(payload)
    await state.update_data(eng_hours=hours)
    data = await state.get_data()
    await _persist_fields(session_factory, date.fromisoformat(data["day"]), eng_hours=hours)
    await _maybe_ask_weight_or_save(cb.message, state, session_factory)
    await cb.answer()


@router.message(TrackStates.english, F.text)
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
        await state.set_state(TrackStates.weight)
        prompt = "Какой сегодня вес (кг)? Напишите числом или нажмите «Пропустить»."
        if use_answer:
            await message.answer(prompt, reply_markup=_weight_skip_keyboard())
        else:
            await message.edit_text(prompt, reply_markup=_weight_skip_keyboard())
    else:
        await _save_and_finish(message, state, session_factory, use_answer=use_answer)


@router.message(TrackStates.weight, F.text)
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


@router.callback_query(TrackStates.weight, F.data == "weight:skip")
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
            existing.mood = data.get("mood")
            existing.anxiety = data.get("anxiety")
            existing.irritability = data.get("irritability")
            existing.energy = data.get("energy")
            existing.appetite = data.get("appetite")
            existing.sleep_hours = data.get("sleep_hours")
            existing.vpn_hours = data.get("vpn_hours")
            existing.eng_hours = data.get("eng_hours")
            if "weight" in data:
                existing.weight = data["weight"]
        else:
            session.add(
                MoodEntry(
                    day=day,
                    mood=data.get("mood"),
                    anxiety=data.get("anxiety"),
                    irritability=data.get("irritability"),
                    energy=data.get("energy"),
                    appetite=data.get("appetite"),
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
                session.add(
                    MedicationLog(
                        day=day,
                        med_key=entry["key"],
                        taken=entry["taken"],
                    )
                )
        await session.commit()

    summary = (
        f"✅ Записал за {day.isoformat()}:\n"
        f"настроение {data.get('mood', '?'):+d}, "
        f"тревога {data.get('anxiety', '?')}, "
        f"раздр. {data.get('irritability', '?')}, "
        f"энергия {data.get('energy', '?'):+d}, "
        f"аппетит {data.get('appetite', '?'):+d}, "
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
    logger.info("track saved for %s by handler", day)
