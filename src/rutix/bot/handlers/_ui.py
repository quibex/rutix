"""Shared inline-keyboard / message helpers for the /state and /report flows."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message


def kb_grid(values: list[tuple[str, str]], cols: int) -> InlineKeyboardMarkup:
    rows = [values[i : i + cols] for i in range(0, len(values), cols)]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=cb) for label, cb in row]
            for row in rows
        ]
    )


async def send(message: Message, text: str, kb: InlineKeyboardMarkup, use_answer: bool) -> None:
    """Send the next prompt — edit the prior message (button flow) or post a
    fresh one (text-input flow, since you can't edit the user's own message)."""
    if use_answer:
        await message.answer(text, reply_markup=kb)
    else:
        await message.edit_text(text, reply_markup=kb)


def parse_score(text: str, lo: int, hi: int) -> int | None:
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
