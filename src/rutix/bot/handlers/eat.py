"""/eat — text or photo. Claude parses, user confirms, bot writes to daily Питание.

Flow:
1. User sends `/eat <text>` or just a photo (with optional caption).
2. Bot parses via Claude (adaptive thinking + reference cache).
3. Bot shows preview + ✅ Записать / ❌ Отменить buttons.
4. While in confirming state, plain text or photo messages are treated as
   corrections — passed to Claude as multi-turn history so the model
   adjusts (not adds to) the prior parse.
5. On ✅: writes to today's daily Питание section. If any items were
   estimates (not in reference), also appends them to nutrition/reference.md
   under «## Из бота (требует проверки)» so they're available next time.
"""

import base64
import io
import json
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
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
REFERENCE_BOT_SECTION = "## Из бота (требует проверки)"


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
        marker = " 🆕" if it.source == "estimate" else ""
        lines.append(f"• {it.name}{marker} — {_format_kbju(it.kcal, it.protein, it.fat, it.carbs)}")
    total_kcal = sum(it.kcal for it in items)
    total_p = sum(it.protein for it in items)
    total_f = sum(it.fat for it in items)
    total_c = sum(it.carbs for it in items)
    lines.append(f"\nИтого: {_format_kbju(total_kcal, total_p, total_f, total_c)}")
    if any(it.source == "estimate" for it in items):
        lines.append("\n🆕 — нет в справочнике, оценка модели. При записи добавлю в reference.")
    lines.append("\nЗаписать в файл дня?")
    return "\n".join(lines)


def _items_to_dump(items: list[MealItem]) -> list[dict]:
    return [
        {
            "name": it.name,
            "kcal": it.kcal,
            "protein": it.protein,
            "fat": it.fat,
            "carbs": it.carbs,
            "source": it.source,
        }
        for it in items
    ]


def _items_from_dump(items_dump: list[dict], slot: str) -> list[MealItem]:
    return [MealItem(slot=slot, **i) for i in items_dump]


async def _download_photo_b64(bot: Bot, file_id: str) -> str:
    file = await bot.get_file(file_id)
    buf = io.BytesIO()
    await bot.download_file(file.file_path, buf)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _build_user_content(text: str, image_b64: str | None) -> str | list[dict]:
    if image_b64 is None:
        return text
    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": image_b64,
            },
        },
        {"type": "text", "text": text or "Что на фото? Распарси КБЖУ."},
    ]


async def _start_eat_flow(
    message: Message,
    state: FSMContext,
    settings: Settings,
    github: GitHubClient,
    claude: ClaudeClient,
    food_text: str,
    image_b64: str | None = None,
):
    """Common entry path for both /eat (text) and photo messages."""
    now = datetime.now(ZoneInfo(settings.tz))
    day = subjective_today(now, settings.tz)
    slot = _slot_for_time(now)

    daily_path = f"daily/{day.isoformat()}.md"
    daily_file = await github.read(daily_path)
    if daily_file is None:
        await message.answer(
            f"⚠️ Файл {daily_path} не найден в репозитории.\nПроверьте что он создан в Obsidian."
        )
        return

    thinking_msg = await message.answer("🤔 Разбираю что вы съели…")

    reference = await github.read(REFERENCE_PATH)
    reference_text = reference.text if reference else ""

    user_content = _build_user_content(food_text, image_b64)
    initial_messages = [{"role": "user", "content": user_content}]

    try:
        items = await claude.parse_eat(initial_messages, reference_md=reference_text)
    except ValueError as e:
        logger.exception("Claude parse failed")
        await thinking_msg.edit_text(
            f"⚠️ Не получилось разобрать ответ модели: {e}\nПопробуйте переформулировать."
        )
        return

    if not items:
        await thinking_msg.edit_text("⚠️ Модель вернула пустой список. Уточните, что вы съели.")
        return

    items_dump = _items_to_dump(items)
    # Seed history with the original turn + Claude's parse so refinements have context.
    # For multimodal content, we serialize a text-only summary in history (Claude can
    # still use it as context, even without re-sending the image).
    seed_user_text = food_text if food_text else "[фото еды]"
    history = [
        {"role": "user", "content": seed_user_text},
        {
            "role": "assistant",
            "content": json.dumps({"items": items_dump}, ensure_ascii=False),
        },
    ]
    await state.update_data(
        items=items_dump,
        slot=slot,
        day=day.isoformat(),
        food_text=food_text or "[фото]",
        history=history,
    )
    await state.set_state(EatStates.confirming)
    await thinking_msg.edit_text(_format_preview(items, slot), reply_markup=_confirm_keyboard())


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
        await message.answer(
            "Пример использования:\n/eat шаурма + кола 0.5\n\nИли просто отправьте фото еды."
        )
        return
    await _start_eat_flow(message, state, settings, github, claude, raw[1].strip(), image_b64=None)


@router.message(StateFilter(None), F.photo)
async def msg_eat_photo(
    message: Message,
    state: FSMContext,
    settings: Settings,
    github: GitHubClient,
    claude: ClaudeClient,
    bot: Bot,
):
    """Photo (alone or with caption) starts an /eat flow with vision."""
    photo = message.photo[-1]  # highest-res variant
    image_b64 = await _download_photo_b64(bot, photo.file_id)
    caption = (message.caption or "").strip()
    # If caption starts with /eat, strip the command prefix
    if caption.lower().startswith("/eat"):
        caption = caption[4:].strip()
    await _start_eat_flow(message, state, settings, github, claude, caption, image_b64=image_b64)


@router.message(EatStates.confirming, F.photo)
async def msg_refine_photo(
    message: Message,
    state: FSMContext,
    settings: Settings,
    github: GitHubClient,
    claude: ClaudeClient,
    bot: Bot,
):
    """Photo while in confirming state — treat as a correction with vision context."""
    photo = message.photo[-1]
    image_b64 = await _download_photo_b64(bot, photo.file_id)
    caption = (message.caption or "Скорректируй по этому фото.").strip()
    await _refine_eat(message, state, github, claude, edit_text=caption, image_b64=image_b64)


@router.message(EatStates.confirming, F.text & ~F.text.startswith("/"))
async def msg_refine_text(
    message: Message,
    state: FSMContext,
    settings: Settings,
    github: GitHubClient,
    claude: ClaudeClient,
):
    """Plain text while in confirming state — treat as a correction."""
    edit_text = message.text.strip()
    if not edit_text:
        return
    await _refine_eat(message, state, github, claude, edit_text=edit_text, image_b64=None)


async def _refine_eat(
    message: Message,
    state: FSMContext,
    github: GitHubClient,
    claude: ClaudeClient,
    edit_text: str,
    image_b64: str | None,
):
    data = await state.get_data()
    history = list(data.get("history", []))
    slot = data.get("slot", "Перекус")

    user_content = _build_user_content(edit_text, image_b64)
    history.append({"role": "user", "content": user_content})

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

    items_dump = _items_to_dump(items)
    history.append(
        {"role": "assistant", "content": json.dumps({"items": items_dump}, ensure_ascii=False)}
    )
    await state.update_data(items=items_dump, history=history, slot=slot)
    await thinking_msg.edit_text(_format_preview(items, slot), reply_markup=_confirm_keyboard())


@router.callback_query(EatStates.confirming, F.data == "eat:cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("❌ Отменено. Ничего не записал.")
    await cb.answer()


def _append_to_reference(reference_md: str, estimates: list[MealItem], day_iso: str) -> str:
    """Append new estimate rows to the «## Из бота» section. Creates the section
    at the end of the file if missing.
    """
    text = reference_md.rstrip()
    if REFERENCE_BOT_SECTION not in text:
        text += (
            f"\n\n{REFERENCE_BOT_SECTION}\n\n"
            "| Продукт | Вес | Ккал | Б | Ж | У | Примечание |\n"
            "|---------|-----|------|---|---|---|------------|"
        )
    new_rows = [
        f"| {it.name} | — | {it.kcal} | {it.protein:g} | {it.fat:g} | {it.carbs:g} | "
        f"оценка от {day_iso} |"
        for it in estimates
    ]
    return text + "\n" + "\n".join(new_rows) + "\n"


async def _persist_estimates_to_reference(
    github: GitHubClient, items: list[MealItem], day_iso: str
) -> str | None:
    """Append estimate-source items to nutrition/reference.md. Returns commit SHA or None."""
    estimates = [it for it in items if it.source == "estimate"]
    if not estimates:
        return None
    file = await github.read(REFERENCE_PATH)
    if file is None:
        logger.warning("reference.md not found — skipping estimate append")
        return None
    new_text = _append_to_reference(file.text, estimates, day_iso)
    if new_text == file.text:
        return None
    return await github.write(
        REFERENCE_PATH,
        new_text,
        f"reference: добавил {len(estimates)} оценок от /eat ({day_iso})",
        sha=file.sha,
    )


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

    items = _items_from_dump(items_dump, slot)
    daily_path = f"daily/{day.isoformat()}.md"

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

    daily_sha = await github.write(
        daily_path,
        new_text,
        f"eat({day.isoformat()}): {food_text[:60]}",
        sha=daily_file.sha,
    )

    # Auto-append estimates to reference.md
    ref_sha = None
    try:
        ref_sha = await _persist_estimates_to_reference(github, items, day.isoformat())
    except Exception:
        logger.exception("Failed to append estimates to reference.md")

    total_kcal = sum(it.kcal for it in items)
    total_p = sum(it.protein for it in items)
    total_f = sum(it.fat for it in items)
    total_c = sum(it.carbs for it in items)

    word = "позицию" if len(items) == 1 else "позиций"
    summary = (
        f"✅ Записал в «{slot}» ({len(items)} {word}):\n"
        + "\n".join(
            f"• {it.name} — {_format_kbju(it.kcal, it.protein, it.fat, it.carbs)}" for it in items
        )
        + f"\n\nИтого: {_format_kbju(total_kcal, total_p, total_f, total_c)}\n"
        f"Daily: {daily_sha[:7]}"
    )
    if ref_sha:
        n_est = sum(1 for it in items if it.source == "estimate")
        summary += f"\n📚 Reference: +{n_est} → {ref_sha[:7]} (требует проверки)"
    await cb.message.edit_text(summary)
    await cb.answer()
