"""/eat <text> — Claude parses, user confirms, bot writes to today's daily Питание."""

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

from rutix.integrations.claude import ClaudeClient
from rutix.integrations.github import GitHubClient
from rutix.markdown.daily import MealItem, append_meal
from rutix.settings import Settings
from rutix.time_utils import subjective_today

logger = logging.getLogger(__name__)

router = Router(name="eat")

REFERENCE_PATH = "nutrition/reference.md"


class EatStates(StatesGroup):
    confirming = State()


def _slot_for_time(now: datetime) -> str:
    h = now.hour
    if 8 <= h <= 11:
        return "Завтрак"
    if 12 <= h <= 16:
        return "Обед"
    if 17 <= h <= 21:
        return "Ужин"
    return "Перекус"


def _format_kbju(kcal: int, p: float, f: float, c: float) -> str:
    return f"{kcal} ккал · Б{p:g} Ж{f:g} У{c:g}"


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Записать", callback_data="eat:ok"),
                InlineKeyboardButton(text="❌ Отменить", callback_data="eat:cancel"),
            ]
        ]
    )


def _format_preview(items: list[MealItem], slot: str) -> str:
    lines = [f"📋 Распарсил для «{slot}»:\n"]
    for it in items:
        lines.append(f"• {it.name} — {_format_kbju(it.kcal, it.protein, it.fat, it.carbs)}")
    total_kcal = sum(it.kcal for it in items)
    total_p = sum(it.protein for it in items)
    total_f = sum(it.fat for it in items)
    total_c = sum(it.carbs for it in items)
    lines.append(f"\nИтого: {_format_kbju(total_kcal, total_p, total_f, total_c)}")
    lines.append("\nЗаписать в файл дня?")
    return "\n".join(lines)


@router.message(Command("eat"))
async def cmd_eat(
    message: Message,
    state: FSMContext,
    settings: Settings,
    github: GitHubClient,
    claude: ClaudeClient,
):
    raw = (message.text or "").split(maxsplit=1)
    if len(raw) < 2 or not raw[1].strip():
        await message.answer("Пример использования:\n/eat шаурма + кола 0.5")
        return

    food_text = raw[1].strip()
    now = datetime.now(ZoneInfo(settings.tz))
    day = subjective_today(now, settings.tz)
    slot = _slot_for_time(now)

    # Quick existence check before spending Claude tokens
    daily_path = f"daily/{day.isoformat()}.md"
    daily_file = await github.read(daily_path)
    if daily_file is None:
        await message.answer(
            f"⚠️ Файл {daily_path} не найден в репозитории.\nПроверьте что он создан в Obsidian."
        )
        return

    # Show "thinking..." while Claude works (adaptive thinking can take a few seconds)
    thinking_msg = await message.answer("🤔 Разбираю что вы съели…")

    reference = await github.read(REFERENCE_PATH)
    reference_text = reference.text if reference else ""

    try:
        items = await claude.parse_eat(food_text, reference_md=reference_text)
    except ValueError as e:
        logger.exception("Claude parse failed")
        await thinking_msg.edit_text(
            f"⚠️ Не получилось разобрать ответ модели: {e}\nПопробуйте переформулировать."
        )
        return

    if not items:
        await thinking_msg.edit_text("⚠️ Модель вернула пустой список. Уточните, что вы съели.")
        return

    # Stash everything we need to apply the write on confirm.
    # Re-fetching the daily file on confirm handles the case where the user
    # took a long time to confirm and the file changed.
    import json as _json

    items_dump = [
        {
            "name": it.name,
            "kcal": it.kcal,
            "protein": it.protein,
            "fat": it.fat,
            "carbs": it.carbs,
        }
        for it in items
    ]
    # Seed conversation history with the original turn + Claude's parse, so any
    # follow-up correction sees the full context, not just the new edit text.
    history = [
        {"role": "user", "content": food_text},
        {
            "role": "assistant",
            "content": _json.dumps({"items": items_dump}, ensure_ascii=False),
        },
    ]
    await state.update_data(
        items=items_dump,
        slot=slot,
        day=day.isoformat(),
        food_text=food_text,
        history=history,
    )
    await state.set_state(EatStates.confirming)

    await thinking_msg.edit_text(_format_preview(items, slot), reply_markup=_confirm_keyboard())


@router.message(EatStates.confirming, F.text & ~F.text.startswith("/"))
async def msg_refine(
    message: Message,
    state: FSMContext,
    settings: Settings,
    github: GitHubClient,
    claude: ClaudeClient,
):
    """While waiting for ✅/❌, treat plain text as a correction — re-parse with conversation history.

    Pass the full back-and-forth to Claude (original food text + previous parses + new edit)
    so the model treats new turns as adjustments to the prior result, not as additions.
    """
    import json as _json

    data = await state.get_data()
    history = data.get("history", [])
    edit_text = message.text.strip()
    if not edit_text:
        return

    # Append the new user turn to the conversation
    history = list(history) + [{"role": "user", "content": edit_text}]

    slot = data.get("slot", "Перекус")
    day = date.fromisoformat(data["day"])

    thinking_msg = await message.answer("🤔 Учту и перепарсю…")

    reference = await github.read(REFERENCE_PATH)
    reference_text = reference.text if reference else ""

    try:
        items = await claude.parse_eat(history, reference_md=reference_text)
    except ValueError as e:
        logger.exception("Claude reparse failed")
        await thinking_msg.edit_text(
            f"⚠️ Не получилось разобрать: {e}\nПопробуйте переформулировать или нажмите ❌."
        )
        return

    if not items:
        await thinking_msg.edit_text("⚠️ Модель вернула пустой список. Уточните или нажмите ❌.")
        return

    items_dump = [
        {
            "name": it.name,
            "kcal": it.kcal,
            "protein": it.protein,
            "fat": it.fat,
            "carbs": it.carbs,
        }
        for it in items
    ]
    # Append assistant turn (the JSON we just parsed) so the next correction has full context
    history = history + [
        {"role": "assistant", "content": _json.dumps({"items": items_dump}, ensure_ascii=False)}
    ]
    await state.update_data(items=items_dump, history=history, day=day.isoformat(), slot=slot)
    await thinking_msg.edit_text(_format_preview(items, slot), reply_markup=_confirm_keyboard())


@router.callback_query(EatStates.confirming, F.data == "eat:cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("❌ Отменено. Ничего не записал.")
    await cb.answer()


@router.callback_query(EatStates.confirming, F.data == "eat:ok")
async def cb_ok(
    cb: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    github: GitHubClient,
):
    data = await state.get_data()
    await state.clear()

    items_dump = data.get("items", [])
    slot = data.get("slot", "Перекус")
    day = date.fromisoformat(data["day"])
    food_text = data.get("food_text", "")

    items = [MealItem(slot=slot, **i) for i in items_dump]
    daily_path = f"daily/{day.isoformat()}.md"

    # Re-fetch daily file (may have changed since the parse)
    daily_file = await github.read(daily_path)
    if daily_file is None:
        await cb.message.edit_text(
            f"⚠️ Файл {daily_path} пропал между разбором и записью. Попробуйте ещё раз."
        )
        await cb.answer()
        return

    new_text = daily_file.text
    for item in items:
        new_text = append_meal(new_text, item)

    sha = await github.write(
        daily_path,
        new_text,
        f"eat({day.isoformat()}): {food_text[:60]}",
        sha=daily_file.sha,
    )

    total_kcal = sum(it.kcal for it in items)
    total_p = sum(it.protein for it in items)
    total_f = sum(it.fat for it in items)
    total_c = sum(it.carbs for it in items)
    summary = (
        f"✅ Записал в «{slot}» ({len(items)} {('позицию' if len(items) == 1 else 'позиций')}):\n"
        + "\n".join(
            f"• {it.name} — {_format_kbju(it.kcal, it.protein, it.fat, it.carbs)}" for it in items
        )
        + f"\n\nИтого: {_format_kbju(total_kcal, total_p, total_f, total_c)}\n"
        f"Коммит: {sha[:7]}"
    )
    await cb.message.edit_text(summary)
    await cb.answer()
