"""/eat — open a session, add foods (text or photo), confirm to write.

Flow:
1. `/eat` (alone) opens an empty session, bot says "пишите".
2. `/eat <text>` or photo (with no active state) opens a session AND parses
   the first input immediately.
3. Inside the session, every text/photo is fed to Claude as a new turn —
   model is told to return the **full current list** (add / replace / remove /
   adjust portion based on context).
4. After each turn, bot edits the same preview message with the updated parse
   and ✅ Записать / ❌ Отменить buttons.
5. ✅: writes to today's daily Питание section. Estimates (items not in
   reference.md) are appended to nutrition/reference.md under
   «## Из бота (требует проверки)».
6. ❌: clears state.
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
    session = State()


# --- Slot / formatting helpers ---


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
    if not items:
        return (
            f"📝 Сессия записи открыта (слот «{slot}»).\n\n"
            "Пишите блюда текстом или кидайте фото. "
            "Можно несколько сообщений подряд — я буду накапливать.\n\n"
            "Когда всё перечислили — нажмите ✅."
        )
    lines = [f"📋 Текущий список «{slot}»:\n"]
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
    lines.append("\nДополните или нажмите ✅ чтобы записать.")
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


# --- Photo / multimodal helpers ---


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


# --- Session lifecycle ---


async def _open_session(message: Message, state: FSMContext, settings: Settings) -> dict:
    """Initialize a fresh session in state. Returns the seed data dict."""
    now = datetime.now(ZoneInfo(settings.tz))
    day = subjective_today(now, settings.tz)
    slot = _slot_for_time(now)
    data = {
        "items": [],
        "history": [],
        "slot": slot,
        "day": day.isoformat(),
        "preview_chat_id": None,
        "preview_message_id": None,
    }
    await state.update_data(**data)
    await state.set_state(EatStates.session)
    return data


async def _process_input(
    message: Message,
    state: FSMContext,
    settings: Settings,
    github: GitHubClient,
    claude: ClaudeClient,
    text: str,
    image_b64: str | None,
):
    """Append the new turn to history, re-parse, edit the preview message.

    Works for both first turn (history empty) and subsequent refinements.
    """
    data = await state.get_data()
    if not data:
        # Defensive — shouldn't happen because we set state before this is called
        data = await _open_session(message, state, settings)

    history = list(data.get("history", []))
    slot = data.get("slot", "Перекус")
    day_iso = data.get("day")
    preview_chat_id = data.get("preview_chat_id")
    preview_message_id = data.get("preview_message_id")

    # Sanity check: daily file must exist before we burn Claude tokens
    daily_path = f"daily/{day_iso}.md"
    daily_file = await github.read(daily_path)
    if daily_file is None:
        await message.answer(
            f"⚠️ Файл {daily_path} не найден в репозитории.\n"
            "Создайте его в Obsidian и попробуйте снова."
        )
        await state.clear()
        return

    # Append the new user turn
    user_content = _build_user_content(text, image_b64)
    history.append({"role": "user", "content": user_content})

    thinking_msg = await message.answer("🤔 Разбираю…")

    reference = await github.read(REFERENCE_PATH)
    reference_text = reference.text if reference else ""

    try:
        items = await claude.parse_eat(history, reference_md=reference_text)
    except ValueError as e:
        logger.exception("Claude parse failed")
        await thinking_msg.edit_text(
            f"⚠️ Не получилось разобрать ответ модели: {e}\n"
            "Попробуйте переформулировать или нажмите ❌."
        )
        return

    if not items:
        await thinking_msg.edit_text("⚠️ Модель вернула пустой список. Уточните или нажмите ❌.")
        return

    items_dump = _items_to_dump(items)
    history.append(
        {"role": "assistant", "content": json.dumps({"items": items_dump}, ensure_ascii=False)}
    )

    # Update or create the preview message. We try to edit the previous preview;
    # the "thinking" message is then deleted to avoid clutter.
    preview_text = _format_preview(items, slot)
    if preview_chat_id and preview_message_id:
        try:
            await message.bot.edit_message_text(
                preview_text,
                chat_id=preview_chat_id,
                message_id=preview_message_id,
                reply_markup=_confirm_keyboard(),
            )
            await thinking_msg.delete()
        except Exception:
            # If editing failed (message too old, deleted, etc.), fall back to
            # the thinking message
            await thinking_msg.edit_text(preview_text, reply_markup=_confirm_keyboard())
            preview_chat_id = thinking_msg.chat.id
            preview_message_id = thinking_msg.message_id
    else:
        await thinking_msg.edit_text(preview_text, reply_markup=_confirm_keyboard())
        preview_chat_id = thinking_msg.chat.id
        preview_message_id = thinking_msg.message_id

    await state.update_data(
        items=items_dump,
        history=history,
        slot=slot,
        day=day_iso,
        preview_chat_id=preview_chat_id,
        preview_message_id=preview_message_id,
    )


# --- /eat command ---


@router.message(Command("eat"))
async def cmd_eat(
    message: Message,
    state: FSMContext,
    settings: Settings,
    github: GitHubClient,
    claude: ClaudeClient,
):
    raw = (message.text or "").split(maxsplit=1)
    food_text = raw[1].strip() if len(raw) > 1 else ""

    # Reset any prior session and open a fresh one
    await state.clear()
    data = await _open_session(message, state, settings)

    if not food_text:
        # Empty session — just send the prompt-for-input message and store its id
        msg = await message.answer(
            _format_preview([], data["slot"]),
            reply_markup=_confirm_keyboard(),
        )
        await state.update_data(preview_chat_id=msg.chat.id, preview_message_id=msg.message_id)
        return

    # Has text — parse immediately
    await _process_input(message, state, settings, github, claude, text=food_text, image_b64=None)


# --- Photo as session opener (no state) ---


@router.message(StateFilter(None), F.photo)
async def msg_eat_photo(
    message: Message,
    state: FSMContext,
    settings: Settings,
    github: GitHubClient,
    claude: ClaudeClient,
    bot: Bot,
):
    """Photo (with optional caption) outside any session opens a fresh /eat session."""
    photo = message.photo[-1]
    image_b64 = await _download_photo_b64(bot, photo.file_id)
    caption = (message.caption or "").strip()
    if caption.lower().startswith("/eat"):
        caption = caption[4:].strip()

    await state.clear()
    await _open_session(message, state, settings)
    await _process_input(
        message, state, settings, github, claude, text=caption, image_b64=image_b64
    )


# --- In-session input (text + photo) ---


@router.message(EatStates.session, F.photo)
async def msg_session_photo(
    message: Message,
    state: FSMContext,
    settings: Settings,
    github: GitHubClient,
    claude: ClaudeClient,
    bot: Bot,
):
    photo = message.photo[-1]
    image_b64 = await _download_photo_b64(bot, photo.file_id)
    caption = (message.caption or "").strip()
    await _process_input(
        message, state, settings, github, claude, text=caption, image_b64=image_b64
    )


@router.message(EatStates.session, F.text & ~F.text.startswith("/"))
async def msg_session_text(
    message: Message,
    state: FSMContext,
    settings: Settings,
    github: GitHubClient,
    claude: ClaudeClient,
):
    text = message.text.strip()
    if not text:
        return
    await _process_input(message, state, settings, github, claude, text=text, image_b64=None)


# --- Confirm / cancel ---


@router.callback_query(EatStates.session, F.data == "eat:cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("❌ Сессия отменена. Ничего не записал.")
    await cb.answer()


def _append_to_reference(reference_md: str, estimates: list[MealItem], day_iso: str) -> str:
    """Append new estimate rows to «## Из бота» section. Creates the section
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


@router.callback_query(EatStates.session, F.data == "eat:ok")
async def cb_ok(
    cb: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    github: GitHubClient,
):
    data = await state.get_data()
    await state.clear()

    items_dump = data.get("items", [])
    if not items_dump:
        await cb.message.edit_text("⏭ Сессия пустая. Ничего не записал.")
        await cb.answer()
        return

    slot = data.get("slot", "Перекус")
    day = date.fromisoformat(data["day"])
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
        f"eat({day.isoformat()}): {len(items)} позиций",
        sha=daily_file.sha,
    )

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
