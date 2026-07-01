"""/state — subjective-state snapshot (mood, energy, appetite).

Unlike /report this is deliberately multi-run: it can be fired morning, noon and
evening. Each run appends one `StateEntry` row (no averaging, no resume) with the
local wall-clock time; flush_day renders them as timestamped lines in the daily
file's `## Самочувствие` section.
"""

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rutix.bot.handlers._ui import kb_grid, parse_score, send
from rutix.db.models import StateEntry
from rutix.settings import Settings
from rutix.time_utils import subjective_today

logger = logging.getLogger(__name__)

router = Router(name="state")


class StateStates(StatesGroup):
    mood = State()
    energy = State()
    appetite = State()


def _mood_keyboard() -> InlineKeyboardMarkup:
    return kb_grid([(f"{i:+d}" if i else "0", f"smood:{i}") for i in range(-3, 4)], cols=4)


def _five_point_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return kb_grid([(f"{i:+d}" if i else "0", f"{prefix}:{i}") for i in range(-2, 3)], cols=5)


@router.message(Command("state"))
async def cmd_state(
    message: Message,
    state: FSMContext,
    settings: Settings,
):
    now = datetime.now(ZoneInfo(settings.tz))
    day = subjective_today(now, settings.tz)
    await state.clear()
    await state.update_data(day=day.isoformat(), ts=now.replace(tzinfo=None).isoformat())
    await state.set_state(StateStates.mood)
    await message.answer(
        "🧭 Как сейчас?\n\nНастроение?",
        reply_markup=_mood_keyboard(),
    )


async def _go_energy(message: Message, state: FSMContext, mood: int, *, use_answer: bool = False):
    await state.update_data(mood=mood)
    await state.set_state(StateStates.energy)
    await send(
        message,
        f"Настроение: {mood:+d}.\n\nСколько сил/энергии?",
        _five_point_keyboard("senergy"),
        use_answer,
    )


async def _go_appetite(
    message: Message, state: FSMContext, energy: int, *, use_answer: bool = False
):
    await state.update_data(energy=energy)
    await state.set_state(StateStates.appetite)
    await send(
        message, f"Энергия: {energy:+d}.\n\nАппетит?", _five_point_keyboard("sappetite"), use_answer
    )


async def _save_and_finish(
    message: Message,
    state: FSMContext,
    appetite: int,
    session_factory,
    *,
    use_answer: bool = False,
):
    await state.update_data(appetite=appetite)
    data = await state.get_data()
    day = date.fromisoformat(data["day"])
    ts = datetime.fromisoformat(data["ts"])
    mood = data.get("mood")
    energy = data.get("energy")

    async with session_factory() as session:
        session.add(StateEntry(day=day, ts=ts, mood=mood, energy=energy, appetite=appetite))
        await session.commit()

    summary = (
        f"✅ {ts.strftime('%H:%M')} — настроение {mood:+d}, "
        f"энергия {energy:+d}, аппетит {appetite:+d}"
    )
    if use_answer:
        await message.answer(summary)
    else:
        await message.edit_text(summary)
    await state.clear()
    logger.info("state snapshot saved for %s at %s", day, ts.strftime("%H:%M"))


@router.callback_query(StateStates.mood, F.data.startswith("smood:"))
async def cb_mood(cb: CallbackQuery, state: FSMContext):
    await _go_energy(cb.message, state, int(cb.data.split(":", 1)[1]))
    await cb.answer()


@router.message(StateStates.mood, F.text)
async def msg_mood(message: Message, state: FSMContext):
    value = parse_score(message.text, -3, 3)
    if value is None:
        await message.answer("⚠️ Не понял. Напишите число от −3 до +3 (или нажмите кнопку).")
        return
    await _go_energy(message, state, value, use_answer=True)


@router.callback_query(StateStates.energy, F.data.startswith("senergy:"))
async def cb_energy(cb: CallbackQuery, state: FSMContext):
    await _go_appetite(cb.message, state, int(cb.data.split(":", 1)[1]))
    await cb.answer()


@router.message(StateStates.energy, F.text)
async def msg_energy(message: Message, state: FSMContext):
    value = parse_score(message.text, -2, 2)
    if value is None:
        await message.answer("⚠️ Не понял. Напишите число от −2 до +2 (или нажмите кнопку).")
        return
    await _go_appetite(message, state, value, use_answer=True)


@router.callback_query(StateStates.appetite, F.data.startswith("sappetite:"))
async def cb_appetite(
    cb: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    await _save_and_finish(cb.message, state, int(cb.data.split(":", 1)[1]), session_factory)
    await cb.answer()


@router.message(StateStates.appetite, F.text)
async def msg_appetite(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    value = parse_score(message.text, -2, 2)
    if value is None:
        await message.answer("⚠️ Не понял. Напишите число от −2 до +2 (или нажмите кнопку).")
        return
    await _save_and_finish(message, state, value, session_factory, use_answer=True)
