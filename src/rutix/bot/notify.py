"""Standalone bot notifications that cancel any in-progress single-user flow.

A cron-sent message (med reminder, snooze re-send, evening ping, daily plan …)
arriving while `/report` is waiting on a step must invalidate that pending step —
otherwise the user's next reply (e.g. "45" meaning "snooze Atarax 45 min") gets
swallowed by the stale prompt. Persisted step values survive (the /report resume
logic rebuilds progress from the DB), so cancelling loses nothing.

`Notifier` is a transparent stand-in for `Bot`: it proxies every attribute to
the wrapped bot and only augments `send_message`, clearing the user's FSM state
before delegating. The jobs already type their dependency as `bot` and call
`bot.send_message(...)`, so wiring a `Notifier` in `__main__` needs no changes
to job signatures or tests.
"""

from typing import Any

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import BaseStorage, StorageKey


class Notifier:
    def __init__(self, bot: Bot, storage: BaseStorage, user_id: int) -> None:
        self._bot = bot
        self._storage = storage
        self._user_id = user_id

    def __getattr__(self, name: str) -> Any:
        # Only called for attributes not set on the instance — proxy them to the
        # real bot. The leading-underscore guard avoids recursion before
        # __init__ has populated self._bot.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._bot, name)

    async def _cancel_pending_flow(self) -> None:
        key = StorageKey(bot_id=self._bot.id, chat_id=self._user_id, user_id=self._user_id)
        ctx = FSMContext(storage=self._storage, key=key)
        await ctx.set_state(None)

    async def send_message(self, *args: Any, **kwargs: Any):
        await self._cancel_pending_flow()
        return await self._bot.send_message(*args, **kwargs)
