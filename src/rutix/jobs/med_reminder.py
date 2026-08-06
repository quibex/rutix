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

from rutix.db.models import MedActive, MedicationLog, MedSnooze
from rutix.time_utils import subjective_today

logger = logging.getLogger(__name__)

REMINDER_HEADER = "💊 Не забудь принять:"
CATCH_UP_HEADER = "💊 Пропущено из-за смены часового пояса:"
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


def build_reminder_text(meds: list[MedActive], header: str = REMINDER_HEADER) -> str:
    """Bullet-list of meds with current dose. Caller guarantees non-empty."""
    lines = [header]
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


async def pending_reminder_meds(session: AsyncSession, day: date) -> list[MedActive]:
    """Active meds with a reminder_time set that aren't yet taken today.

    Used by the snooze handler to decide whether an incoming number is a snooze
    request and which meds to defer.
    """
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
    all_active = (
        await session.scalars(
            select(MedActive)
            .where(MedActive.archived_at.is_(None), MedActive.reminder_time.isnot(None))
            .order_by(MedActive.started_at, MedActive.name)
        )
    ).all()
    return [m for m in all_active if m.key not in taken_keys]


async def schedule_snooze(session: AsyncSession, meds: list[MedActive], fire_at: datetime) -> None:
    med_keys = ",".join(m.key for m in meds)
    session.add(MedSnooze(fire_at=fire_at, med_keys=med_keys))
    await session.commit()


async def _fire_due_snoozes(
    session: AsyncSession,
    bot: Bot,
    telegram_user_id: int,
    now: datetime,
    day: date,
    tz: str,
) -> bool:
    """Send any snooze reminders whose fire_at <= now and delete them. Returns
    True if at least one was sent."""
    due = (
        await session.scalars(
            select(MedSnooze).where(MedSnooze.fire_at <= now.replace(tzinfo=None))
        )
    ).all()
    if not due:
        return False

    # Collect all unique med keys from due snoozes, deduplicate while preserving order.
    seen: set[str] = set()
    keys: list[str] = []
    for snooze in due:
        for k in snooze.med_keys.split(","):
            k = k.strip()
            if k and k not in seen:
                seen.add(k)
                keys.append(k)

    # Filter to only those still active and untaken.
    untaken = await untaken_active_meds(session, day)
    untaken_keys = {m.key for m in untaken}
    meds_to_send = [m for m in untaken if m.key in keys and m.key in untaken_keys]

    # Delete the fired rows regardless of whether meds remain untaken.
    for snooze in due:
        await session.delete(snooze)
    await session.commit()

    if not meds_to_send:
        return False

    await bot.send_message(
        chat_id=telegram_user_id,
        text=build_reminder_text(meds_to_send),
        reply_markup=build_reminder_keyboard(day, meds_to_send),
    )
    logger.info(
        "snooze reminder sent at %s — %d meds (%s)",
        now.strftime("%H:%M"),
        len(meds_to_send),
        ", ".join(m.key for m in meds_to_send),
    )
    return True


async def catch_up_after_tz_change(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
    telegram_user_id: int,
    old_tz: str,
    new_tz: str,
) -> list[MedActive]:
    """Re-send reminders the timezone shift jumped over. Returns what was sent.

    `med_reminder_tick` only fires on an exact HH:MM match, so moving east
    (local clock jumps forward) skips every reminder in the interval it passed
    through — move at 05:40 MSK to 08:40 Bishkek and an 08:00 pill is simply
    never announced that day.

    Only meds in the half-open window `(old local time, new local time]` are
    replayed: those are exactly the ones neither timezone announced. Meds whose
    time had already passed in the *old* zone were announced then and are not
    nudged again. Moving west skips nothing, so nothing is sent.
    """
    now = datetime.now(ZoneInfo(new_tz))
    old_hh_mm = datetime.now(ZoneInfo(old_tz)).strftime("%H:%M")
    new_hh_mm = now.strftime("%H:%M")
    # Moved west (or across midnight, where a wall-clock comparison is
    # meaningless) — nothing was skipped. Under-firing beats double-nudging.
    if new_hh_mm <= old_hh_mm:
        return []

    day = subjective_today(now, new_tz)
    async with session_factory() as session:
        meds = [
            m
            for m in await pending_reminder_meds(session, day)
            if m.reminder_time and old_hh_mm < m.reminder_time <= new_hh_mm
        ]
        if not meds:
            return []
        await bot.send_message(
            chat_id=telegram_user_id,
            text=build_reminder_text(meds, header=CATCH_UP_HEADER),
            reply_markup=build_reminder_keyboard(day, meds),
        )
    logger.info(
        "tz change %s -> %s: replayed %d skipped reminder(s) in (%s, %s]",
        old_tz,
        new_tz,
        len(meds),
        old_hh_mm,
        new_hh_mm,
    )
    return meds


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
    sent = False
    async with session_factory() as session:
        meds = await due_active_meds(session, day, hh_mm)
        if meds:
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
            sent = True
        snooze_sent = await _fire_due_snoozes(session, bot, telegram_user_id, now, day, tz)
    return sent or snooze_sent
