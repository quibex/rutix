"""Per-pill med-reminder job.

Each `MedActive` carries its own `reminder_time` (HH:MM local) or NULL for
"no reminder". A cron triggered every minute (`med_reminder_tick`) picks up
all meds whose `reminder_time` matches the current minute and pings the user
with one inline "✓ принял" button per due med. The button callback
(`med_taken:{day}:{key}` in [src/rutix/bot/handlers/meds.py]) writes
`MedicationLog(taken=True)` and edits the message in place.

The tick is silent if no meds are due — INFO-level logging only fires on
actual sends so per-minute polling doesn't flood the logs.

HH:MM is validated by `parse_reminder_time` at the input boundary (the /meds
add and "set time" handlers) so the cron can compare strings directly.
"""

import logging
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rutix.db.models import MedActive, MedicationLog
from rutix.time_utils import subjective_today

logger = logging.getLogger(__name__)

REMINDER_HEADER = "💊 Не забудь принять:"
ALL_DONE_TEXT = "✅ Все препараты приняты."

CB_PREFIX = "med_taken"

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_reminder_time(raw: str) -> str:
    """Normalize HH:MM input to a canonical "HH:MM" string. Accepts "9:5"
    → "09:05". Raises ValueError on malformed input."""
    s = raw.strip()
    if ":" not in s:
        raise ValueError(f"need HH:MM, got {raw!r}")
    h_str, m_str = s.split(":", 1)
    try:
        h, m = int(h_str), int(m_str)
    except ValueError as e:
        raise ValueError(f"need HH:MM, got {raw!r}") from e
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"out-of-range time: {raw!r}")
    normalized = f"{h:02d}:{m:02d}"
    # Defensive: assert the canonical form matches the validating regex.
    assert _HHMM_RE.match(normalized), normalized
    return normalized


async def due_active_meds(session: AsyncSession, day: date, hh_mm: str) -> list[MedActive]:
    """Active meds with `reminder_time == hh_mm` that aren't yet logged
    `taken=True` for the given day. Ordered by started_at for stable output."""
    taken_keys = set(
        (
            await session.scalars(
                select(MedicationLog.med_key).where(
                    MedicationLog.day == day,
                    MedicationLog.taken.is_(True),
                )
            )
        ).all()
    )
    candidates = (
        await session.scalars(
            select(MedActive)
            .where(
                MedActive.archived_at.is_(None),
                MedActive.reminder_time == hh_mm,
            )
            .order_by(MedActive.started_at, MedActive.name)
        )
    ).all()
    return [m for m in candidates if m.key not in taken_keys]


async def untaken_active_meds(session: AsyncSession, day: date) -> list[MedActive]:
    """All active meds not yet logged `taken=True` for the day (regardless of
    their reminder_time). Used by the callback handler when refreshing the
    keyboard so a multi-med reminder loses one button at a time."""
    taken_keys = set(
        (
            await session.scalars(
                select(MedicationLog.med_key).where(
                    MedicationLog.day == day,
                    MedicationLog.taken.is_(True),
                )
            )
        ).all()
    )
    active = (
        await session.scalars(
            select(MedActive)
            .where(MedActive.archived_at.is_(None))
            .order_by(MedActive.started_at, MedActive.name)
        )
    ).all()
    return [m for m in active if m.key not in taken_keys]


def build_reminder_text(meds: list[MedActive]) -> str:
    """Bullet-list of meds with current dose. Caller guarantees non-empty."""
    lines = [REMINDER_HEADER]
    for m in meds:
        lines.append(f"• {m.name} — {m.current_dose} мг")
    return "\n".join(lines)


def build_reminder_keyboard(day: date, meds: list[MedActive]) -> InlineKeyboardMarkup:
    """One button per med. The subjective day is encoded in callback_data so a
    late tap (after midnight) still credits the correct day."""
    day_iso = day.isoformat()
    rows = [
        [
            InlineKeyboardButton(
                text=f"💊 Выпил — {m.name}",
                callback_data=f"{CB_PREFIX}:{day_iso}:{m.key}",
            )
        ]
        for m in meds
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def med_reminder_tick(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
    telegram_user_id: int,
    tz: str,
) -> bool:
    """Per-minute cron entrypoint. Returns True if a reminder was sent."""
    now = datetime.now(ZoneInfo(tz))
    hh_mm = now.strftime("%H:%M")
    day = subjective_today(now, tz)
    async with session_factory() as session:
        meds = await due_active_meds(session, day, hh_mm)
    if not meds:
        return False
    await bot.send_message(
        chat_id=telegram_user_id,
        text=build_reminder_text(meds),
        reply_markup=build_reminder_keyboard(day, meds),
    )
    logger.info(
        "med_reminder_tick sent for %s at %s — %d due (%s)",
        day,
        hh_mm,
        len(meds),
        ", ".join(m.key for m in meds),
    )
    return True
