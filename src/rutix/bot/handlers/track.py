"""/track — multi-step mood entry via inline buttons."""

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rutix.db.models import MedActive, MedicationLog, MoodEntry
from rutix.settings import Settings
from rutix.time_utils import is_saturday, subjective_today

logger = logging.getLogger(__name__)

router = Router(name="track")


class TrackStates(StatesGroup):
    mood = State()
    anxiety = State()
    irritability = State()
    energy = State()
    sleep = State()
    meds = State()
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


def _energy_keyboard() -> InlineKeyboardMarkup:
    return _kb_grid(
        [
            ("-2", "energy:-2"),
            ("-1", "energy:-1"),
            ("0", "energy:0"),
            ("+1", "energy:1"),
            ("+2", "energy:2"),
        ],
        cols=5,
    )


def _sleep_keyboard() -> InlineKeyboardMarkup:
    return _kb_grid(
        [(h, f"sleep:{h}") for h in ("6.5", "7", "7.5", "8", "8.5", "9")],
        cols=3,
    )


def _med_keyboard(key: str) -> InlineKeyboardMarkup:
    return _kb_grid([("✓ Да", f"med:{key}:1"), ("✗ Нет", f"med:{key}:0")], cols=2)


def _weight_skip_keyboard() -> InlineKeyboardMarkup:
    return _kb_grid([("Пропустить", "weight:skip")], cols=1)


@router.message(Command("track"))
async def cmd_track(message: Message, state: FSMContext, settings: Settings):
    today = subjective_today(datetime.now(ZoneInfo(settings.tz)), settings.tz)
    await state.update_data(day=today.isoformat(), meds_taken=[], meds_pending=[])
    await state.set_state(TrackStates.mood)
    await message.answer(
        f"📊 Трек за {today.isoformat()}.\n\nКакое было настроение?",
        reply_markup=_mood_keyboard(),
    )


@router.callback_query(TrackStates.mood, F.data.startswith("mood:"))
async def cb_mood(cb: CallbackQuery, state: FSMContext):
    value = int(cb.data.split(":", 1)[1])
    await state.update_data(mood=value)
    await state.set_state(TrackStates.anxiety)
    await cb.message.edit_text(
        f"Настроение: {value:+d}.\n\nКакая была тревога?",
        reply_markup=_0_to_3("anx"),
    )
    await cb.answer()


@router.callback_query(TrackStates.anxiety, F.data.startswith("anx:"))
async def cb_anxiety(cb: CallbackQuery, state: FSMContext):
    value = int(cb.data.split(":", 1)[1])
    await state.update_data(anxiety=value)
    await state.set_state(TrackStates.irritability)
    await cb.message.edit_text(
        f"Тревога: {value}.\n\nКакая была раздражительность?",
        reply_markup=_0_to_3("irr"),
    )
    await cb.answer()


@router.callback_query(TrackStates.irritability, F.data.startswith("irr:"))
async def cb_irritability(cb: CallbackQuery, state: FSMContext):
    value = int(cb.data.split(":", 1)[1])
    await state.update_data(irritability=value)
    await state.set_state(TrackStates.energy)
    await cb.message.edit_text(
        f"Раздражительность: {value}.\n\nСколько было сил/энергии?",
        reply_markup=_energy_keyboard(),
    )
    await cb.answer()


@router.callback_query(TrackStates.energy, F.data.startswith("energy:"))
async def cb_energy(cb: CallbackQuery, state: FSMContext):
    value = int(cb.data.split(":", 1)[1])
    await state.update_data(energy=value)
    await state.set_state(TrackStates.sleep)
    await cb.message.edit_text(
        f"Энергия: {value:+d}.\n\nСколько часов спали?",
        reply_markup=_sleep_keyboard(),
    )
    await cb.answer()


@router.callback_query(TrackStates.sleep, F.data.startswith("sleep:"))
async def cb_sleep(
    cb: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    value = float(cb.data.split(":", 1)[1])
    await state.update_data(sleep_hours=value)
    await state.set_state(TrackStates.meds)

    async with session_factory() as session:
        meds = (
            await session.scalars(
                select(MedActive)
                .where(MedActive.archived_at.is_(None))
                .order_by(MedActive.started_at)
            )
        ).all()
    await state.update_data(meds_pending=[m.key for m in meds], meds_taken=[])

    if meds:
        await _ask_next_med(cb.message, state, session_factory)
    else:
        await _maybe_ask_weight_or_save(cb.message, state, session_factory)
    await cb.answer()


async def _ask_next_med(message: Message, state: FSMContext, session_factory):
    data = await state.get_data()
    pending = list(data.get("meds_pending", []))
    if not pending:
        return await _maybe_ask_weight_or_save(message, state, session_factory)
    next_key = pending[0]
    async with session_factory() as session:
        med = await session.get(MedActive, next_key)
    if med is None:
        await state.update_data(meds_pending=pending[1:])
        return await _ask_next_med(message, state, session_factory)
    await message.edit_text(
        f"Принимали {med.name} ({med.current_dose} мг)?",
        reply_markup=_med_keyboard(next_key),
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
    taken_list = list(data.get("meds_taken", []))
    taken_list.append({"key": key, "taken": taken})
    pending = [k for k in data.get("meds_pending", []) if k != key]
    await state.update_data(meds_taken=taken_list, meds_pending=pending)

    if pending:
        await _ask_next_med(cb.message, state, session_factory)
    else:
        await _maybe_ask_weight_or_save(cb.message, state, session_factory)
    await cb.answer()


async def _maybe_ask_weight_or_save(message: Message, state: FSMContext, session_factory):
    data = await state.get_data()
    day = date.fromisoformat(data["day"])
    if is_saturday(day):
        await state.set_state(TrackStates.weight)
        await message.edit_text(
            "Какой сегодня вес (кг)? Напишите числом или нажмите «Пропустить».",
            reply_markup=_weight_skip_keyboard(),
        )
    else:
        await _save_and_finish(message, state, session_factory)


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
    await _save_and_finish(message, state, session_factory)


@router.callback_query(TrackStates.weight, F.data == "weight:skip")
async def cb_weight_skip(
    cb: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    await _save_and_finish(cb.message, state, session_factory)
    await cb.answer()


async def _save_and_finish(message: Message, state: FSMContext, session_factory):
    data = await state.get_data()
    day = date.fromisoformat(data["day"])

    async with session_factory() as session:
        existing = await session.get(MoodEntry, day)
        if existing:
            existing.mood = data.get("mood")
            existing.anxiety = data.get("anxiety")
            existing.irritability = data.get("irritability")
            existing.energy = data.get("energy")
            existing.sleep_hours = data.get("sleep_hours")
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
                    sleep_hours=data.get("sleep_hours"),
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
        f"сон {data.get('sleep_hours', '?')}ч"
    )
    if "weight" in data:
        summary += f", вес {data['weight']}кг"
    await message.edit_text(summary)
    await state.clear()
    logger.info("track saved for %s by handler", day)
