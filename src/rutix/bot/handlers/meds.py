"""/meds — list/add/archive/change-dose for active medication protocol."""
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

from rutix.db.models import MedActive
from rutix.settings import Settings

logger = logging.getLogger(__name__)

router = Router(name="meds")


class MedsStates(StatesGroup):
    add_key = State()
    add_name = State()
    add_label = State()
    add_dose = State()
    edit_dose_value = State()


def _menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="➕ Добавить", callback_data="meds:add"),
        InlineKeyboardButton(text="📦 Архив", callback_data="meds:archive_pick"),
        InlineKeyboardButton(text="✏️ Доза", callback_data="meds:dose_pick"),
    ]])


def _picklist_kb(meds: list[MedActive], action: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{m.name} ({m.current_dose})", callback_data=f"meds:{action}:{m.key}")]
        for m in meds
    ]
    rows.append([InlineKeyboardButton(text="← Отмена", callback_data="meds:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_list(meds: list[MedActive]) -> str:
    if not meds:
        return "🩺 Активных препаратов нет"
    rows = [f"• {m.name} — {m.current_dose} мг (с {m.started_at.isoformat()})" for m in meds]
    return "🩺 Активные:\n" + "\n".join(rows)


@router.message(Command("meds"))
async def cmd_meds(
    message: Message,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
):
    async with session_factory() as session:
        meds = (await session.scalars(
            select(MedActive).where(MedActive.archived_at.is_(None)).order_by(MedActive.started_at)
        )).all()
    await message.answer(_format_list(meds), reply_markup=_menu_kb())


@router.callback_query(F.data == "meds:cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Отменено")
    await cb.answer()


# --- Add flow ---

@router.callback_query(F.data == "meds:add")
async def cb_add(cb: CallbackQuery, state: FSMContext):
    await state.set_state(MedsStates.add_key)
    await cb.message.edit_text("Введи короткий ключ (slug, например `seizar`):")
    await cb.answer()


@router.message(MedsStates.add_key, F.text)
async def msg_add_key(message: Message, state: FSMContext):
    await state.update_data(key=message.text.strip())
    await state.set_state(MedsStates.add_name)
    await message.answer("Полное название (например `Сейзар`):")


@router.message(MedsStates.add_name, F.text)
async def msg_add_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(MedsStates.add_label)
    await message.answer("Заголовок колонки в mood_tracker (например `Сейзар` или `Гидр.К`):")


@router.message(MedsStates.add_label, F.text)
async def msg_add_label(message: Message, state: FSMContext):
    await state.update_data(label=message.text.strip())
    await state.set_state(MedsStates.add_dose)
    await message.answer("Текущая доза (например `25` или `12.5`):")


@router.message(MedsStates.add_dose, F.text)
async def msg_add_dose(
    message: Message, state: FSMContext, settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
):
    data = await state.get_data()
    today = datetime.now(ZoneInfo(settings.tz)).date()
    async with session_factory() as session:
        session.add(MedActive(
            key=data["key"], name=data["name"], column_label=data["label"],
            current_dose=message.text.strip(), started_at=today,
        ))
        await session.commit()
    await state.clear()
    await message.answer(f"✅ Добавил {data['name']} ({message.text.strip()})")


# --- Archive flow ---

@router.callback_query(F.data == "meds:archive_pick")
async def cb_archive_pick(
    cb: CallbackQuery, session_factory: async_sessionmaker[AsyncSession],
):
    async with session_factory() as session:
        meds = (await session.scalars(
            select(MedActive).where(MedActive.archived_at.is_(None))
        )).all()
    if not meds:
        await cb.message.edit_text("Нет активных")
        await cb.answer()
        return
    await cb.message.edit_text("Какой архивировать?", reply_markup=_picklist_kb(meds, "archive"))
    await cb.answer()


@router.callback_query(F.data.startswith("meds:archive:"))
async def cb_archive_apply(
    cb: CallbackQuery, settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
):
    key = cb.data.split(":", 2)[2]
    today = datetime.now(ZoneInfo(settings.tz)).date()
    async with session_factory() as session:
        med = await session.get(MedActive, key)
        if med:
            med.archived_at = today
            await session.commit()
            await cb.message.edit_text(f"📦 Архивировал {med.name}")
        else:
            await cb.message.edit_text("Не нашёл")
    await cb.answer()


# --- Dose flow ---

@router.callback_query(F.data == "meds:dose_pick")
async def cb_dose_pick(
    cb: CallbackQuery, session_factory: async_sessionmaker[AsyncSession],
):
    async with session_factory() as session:
        meds = (await session.scalars(
            select(MedActive).where(MedActive.archived_at.is_(None))
        )).all()
    if not meds:
        await cb.message.edit_text("Нет активных")
        await cb.answer()
        return
    await cb.message.edit_text("Кому менять дозу?", reply_markup=_picklist_kb(meds, "dose"))
    await cb.answer()


@router.callback_query(F.data.startswith("meds:dose:"))
async def cb_dose_pick_med(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split(":", 2)[2]
    await state.update_data(dose_key=key)
    await state.set_state(MedsStates.edit_dose_value)
    await cb.message.edit_text("Новая доза:")
    await cb.answer()


@router.message(MedsStates.edit_dose_value, F.text)
async def msg_dose_value(
    message: Message, state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
):
    data = await state.get_data()
    async with session_factory() as session:
        med = await session.get(MedActive, data["dose_key"])
        if med:
            med.current_dose = message.text.strip()
            await session.commit()
            await message.answer(f"✅ {med.name}: {med.current_dose}")
        else:
            await message.answer("Не нашёл")
    await state.clear()
