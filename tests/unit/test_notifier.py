"""Tests for Notifier — standalone bot messages cancel an in-progress flow."""

from unittest.mock import AsyncMock, MagicMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from rutix.bot.notify import Notifier

_USER_ID = 42
_BOT_ID = 123


def _ctx(storage: MemoryStorage) -> FSMContext:
    key = StorageKey(bot_id=_BOT_ID, chat_id=_USER_ID, user_id=_USER_ID)
    return FSMContext(storage=storage, key=key)


def _fake_bot() -> MagicMock:
    bot = MagicMock()
    bot.id = _BOT_ID
    bot.send_message = AsyncMock(return_value="sent")
    return bot


async def test_send_message_clears_pending_state_then_sends():
    storage = MemoryStorage()
    ctx = _ctx(storage)
    await ctx.set_state("TrackStates:english")  # /track waiting on a step

    bot = _fake_bot()
    notifier = Notifier(bot, storage, _USER_ID)

    result = await notifier.send_message(chat_id=_USER_ID, text="💊 Не забудь принять")

    assert await ctx.get_state() is None  # pending step cancelled
    bot.send_message.assert_awaited_once_with(chat_id=_USER_ID, text="💊 Не забудь принять")
    assert result == "sent"


async def test_send_message_keeps_persisted_fsm_data():
    # Cancelling clears the *state* but the data dict is harmless to keep —
    # /track rebuilds progress from the DB, not the FSM.
    storage = MemoryStorage()
    ctx = _ctx(storage)
    await ctx.set_state("TrackStates:english")
    await ctx.update_data(mood=2)

    notifier = Notifier(_fake_bot(), storage, _USER_ID)
    await notifier.send_message(chat_id=_USER_ID, text="hi")

    assert await ctx.get_state() is None


async def test_proxies_other_bot_attributes():
    bot = _fake_bot()
    bot.some_method = MagicMock(return_value="proxied")
    notifier = Notifier(bot, MemoryStorage(), _USER_ID)
    assert notifier.id == _BOT_ID
    assert notifier.some_method() == "proxied"
