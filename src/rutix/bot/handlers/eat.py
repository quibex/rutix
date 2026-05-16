"""/eat — open a session, add foods (text or photos), confirm to write.

Flow:
1. `/eat` (alone) opens an empty session, bot says "пишите".
2. `/eat <text>` or photo (with no active state) opens a session AND parses
   the first input immediately.
3. Inside the session, every text/photo is fed to Claude WITH the current
   parsed list (explicit state) — model is told to update that list with
   the new turn (add / replace / remove / adjust).
4. Telegram albums (multi-photo messages) are buffered by media_group_id
   and processed as one input.
5. After each turn, bot edits the same preview message with the updated
   parse and ✅ Записать / ❌ Отменить buttons.
6. ✅: writes to today's daily Питание section. Estimates are also appended
   to nutrition/reference.md under «## Из бота (требует проверки)».
7. ❌: clears state.
"""

import asyncio
import base64
import io
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
from rutix.time_utils import extract_day_hint, subjective_today

logger = logging.getLogger(__name__)

router = Router(name="eat")

REFERENCE_PATH = "nutrition/reference.md"
REFERENCE_BOT_SECTION = "## Из бота (требует проверки)"
ALBUM_DEBOUNCE_SECONDS = 1.5

# Buffer photos by media_group_id so albums (multi-photo messages) collapse
# into a single Claude call. Module-level dict — single bot process.
_album_buffers: dict[str, list[Message]] = {}


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


def _format_preview(items: list[MealItem], slot: str, day_label: str = "") -> str:
    suffix = f" за {day_label}" if day_label else ""
    if not items:
        return (
            f"📝 Сессия записи открыта (слот «{slot}»{suffix}).\n\n"
            "Пишите блюда текстом или кидайте фото. "
            "Можно несколько сообщений подряд — я буду накапливать.\n\n"
            "Когда всё перечислили — нажмите ✅."
        )
    lines = [f"📋 Текущий список «{slot}»{suffix}:\n"]
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


def _build_user_input(text: str, image_b64s: list[str]) -> str | list[dict]:
    """Build user_input for ClaudeClient.parse_eat:

    - text-only: returns str
    - has images: returns list of content blocks (images + final text block)
    """
    if not image_b64s:
        return text
    blocks = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
        }
        for b64 in image_b64s
    ]
    blocks.append({"type": "text", "text": text or "Что на фото? Распарси КБЖУ."})
    return blocks


# --- Session lifecycle ---


async def _open_session(message: Message, state: FSMContext, settings: Settings) -> dict:
    """Initialize a fresh session in state. Returns the seed data dict."""
    now = datetime.now(ZoneInfo(settings.tz))
    day = subjective_today(now, settings.tz)
    slot = _slot_for_time(now)
    data = {
        "items": [],
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
    image_b64s: list[str],
):
    """Append the new turn to current state, re-parse with explicit current_items.

    Edits the preview message in place if one exists.
    """
    data = await state.get_data()
    if not data:
        data = await _open_session(message, state, settings)

    current_items = list(data.get("items", []))
    slot = data.get("slot", "Перекус")
    day_iso = data.get("day")
    preview_chat_id = data.get("preview_chat_id")
    preview_message_id = data.get("preview_message_id")

    # First-turn day hint: "/eat вчера ..." retargets the day. Don't re-parse
    # the hint on later turns — that would let mid-session text accidentally
    # bump the date around.
    day_changed = False
    if not current_items and text:
        today = subjective_today(datetime.now(ZoneInfo(settings.tz)), settings.tz)
        resolved_day, text = extract_day_hint(text, today)
        text = text.strip()
        if resolved_day != today:
            day_iso = resolved_day.isoformat()
            # Past-day entries have no meaningful time-of-day slot — default to «Перекус».
            slot = "Перекус"
            day_changed = True

    day_label = (
        day_iso
        if day_iso != subjective_today(datetime.now(ZoneInfo(settings.tz)), settings.tz).isoformat()
        else ""
    )

    # If only a day hint was supplied (e.g. "/eat вчера"), nothing to parse —
    # save the new day/slot and show the empty-session preview.
    if not text and not image_b64s:
        await state.update_data(items=[], slot=slot, day=day_iso)
        if day_changed and preview_chat_id and preview_message_id:
            try:
                await message.bot.edit_message_text(
                    _format_preview([], slot, day_label),
                    chat_id=preview_chat_id,
                    message_id=preview_message_id,
                    reply_markup=_confirm_keyboard(),
                )
                return
            except Exception:
                pass
        msg = await message.answer(
            _format_preview([], slot, day_label),
            reply_markup=_confirm_keyboard(),
        )
        await state.update_data(preview_chat_id=msg.chat.id, preview_message_id=msg.message_id)
        return

    # Sanity check before burning Claude tokens
    daily_path = f"daily/{day_iso}.md"
    daily_file = await github.read(daily_path)
    if daily_file is None:
        await message.answer(
            f"⚠️ Файл {daily_path} не найден в репозитории.\n"
            "Создайте его в Obsidian и попробуйте снова."
        )
        await state.clear()
        return

    user_input = _build_user_input(text, image_b64s)

    thinking_msg = await message.answer("🤔 Разбираю…")

    reference = await github.read(REFERENCE_PATH)
    reference_text = reference.text if reference else ""

    try:
        items = await claude.parse_eat(
            user_input,
            reference_md=reference_text,
            current_items=current_items if current_items else None,
        )
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
    preview_text = _format_preview(items, slot, day_label)

    # Try to edit the existing preview message; fall back to using the thinking msg.
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
            await thinking_msg.edit_text(preview_text, reply_markup=_confirm_keyboard())
            preview_chat_id = thinking_msg.chat.id
            preview_message_id = thinking_msg.message_id
    else:
        await thinking_msg.edit_text(preview_text, reply_markup=_confirm_keyboard())
        preview_chat_id = thinking_msg.chat.id
        preview_message_id = thinking_msg.message_id

    await state.update_data(
        items=items_dump,
        slot=slot,
        day=day_iso,
        preview_chat_id=preview_chat_id,
        preview_message_id=preview_message_id,
    )


# --- Album buffering (Telegram sends album photos as separate updates) ---


async def _collect_album_photos(message: Message, bot: Bot) -> tuple[list[str], str]:
    """If `message` is part of an album, wait briefly for siblings and download all.
    Otherwise return just this photo. Returns (image_b64s, caption).

    Caller invokes this from the *first* photo handler call for a given album;
    later sibling calls early-return via the buffer check before reaching here.
    """
    gid = message.media_group_id
    if not gid:
        b64 = await _download_photo_b64(bot, message.photo[-1].file_id)
        return [b64], (message.caption or "").strip()

    # Mark first arrival so siblings know to buffer-and-bail
    _album_buffers[gid] = [message]
    await asyncio.sleep(ALBUM_DEBOUNCE_SECONDS)
    siblings = _album_buffers.pop(gid, [message])

    image_b64s = []
    for m in siblings:
        b64 = await _download_photo_b64(bot, m.photo[-1].file_id)
        image_b64s.append(b64)
    caption = next((m.caption.strip() for m in siblings if m.caption), "")
    return image_b64s, caption


def _is_album_sibling(message: Message) -> bool:
    """True if this is a follow-up photo of an already-buffered album.

    The first photo of the album gets here when the buffer is still empty
    (we set it inside _collect_album_photos). Subsequent photos see a non-empty
    buffer — append to it and bail; the first call's debounced sleep will
    pick them up.
    """
    gid = message.media_group_id
    if not gid:
        return False
    if gid in _album_buffers:
        _album_buffers[gid].append(message)
        return True
    return False


# --- /eat command ---


@router.message(Command("eat"), ~F.photo)
async def cmd_eat(
    message: Message,
    state: FSMContext,
    settings: Settings,
    github: GitHubClient,
    claude: ClaudeClient,
):
    raw = (message.text or "").split(maxsplit=1)
    food_text = raw[1].strip() if len(raw) > 1 else ""

    await state.clear()
    data = await _open_session(message, state, settings)

    if not food_text:
        # Empty session — wait for input
        msg = await message.answer(
            _format_preview([], data["slot"]),
            reply_markup=_confirm_keyboard(),
        )
        await state.update_data(preview_chat_id=msg.chat.id, preview_message_id=msg.message_id)
        return

    await _process_input(message, state, settings, github, claude, text=food_text, image_b64s=[])


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
    if _is_album_sibling(message):
        return  # buffered for the first-arrival handler to pick up

    image_b64s, caption = await _collect_album_photos(message, bot)
    if caption.lower().startswith("/eat"):
        caption = caption[4:].strip()

    await state.clear()
    await _open_session(message, state, settings)
    await _process_input(
        message, state, settings, github, claude, text=caption, image_b64s=image_b64s
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
    if _is_album_sibling(message):
        return

    image_b64s, caption = await _collect_album_photos(message, bot)
    await _process_input(
        message, state, settings, github, claude, text=caption, image_b64s=image_b64s
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
    await _process_input(message, state, settings, github, claude, text=text, image_b64s=[])


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
    today = subjective_today(datetime.now(ZoneInfo(settings.tz)), settings.tz)
    day_suffix = f" за {day.isoformat()}" if day != today else ""
    summary = (
        f"✅ Записал в «{slot}»{day_suffix} ({len(items)} {word}):\n"
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
