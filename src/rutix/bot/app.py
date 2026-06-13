"""Build aiogram Bot + Dispatcher with all routers wired."""

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from rutix.bot.auth import WhitelistMiddleware
from rutix.bot.handlers import eat as eat_handler
from rutix.bot.handlers import meds as meds_handler
from rutix.bot.handlers import note_done as note_done_handler
from rutix.bot.handlers import start as start_handler
from rutix.bot.handlers import sync as sync_handler
from rutix.bot.handlers import today as today_handler
from rutix.bot.handlers import track as track_handler
from rutix.bot.handlers import week as week_handler
from rutix.bot.handlers import word as word_handler

BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="track", description="📊 Трек настроения / лекарств"),
    BotCommand(command="eat", description="🍽 Записать приём пищи"),
    BotCommand(command="note", description="📝 Заметка дня"),
    BotCommand(command="done", description="✅ Что сделано"),
    BotCommand(command="today", description="📆 Сводка за сегодня"),
    BotCommand(command="week", description="📅 Отчёт по неделе"),
    BotCommand(command="meds", description="💊 Лекарства"),
    BotCommand(command="sync", description="🔄 Форс flush в git"),
    BotCommand(command="start", description="ℹ️ О боте"),
]


def build_bot(token: str) -> Bot:
    return Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def build_dispatcher(allowed_user_id: int) -> Dispatcher:
    dp = Dispatcher()
    dp.update.middleware(WhitelistMiddleware(allowed_user_id))
    dp.include_router(start_handler.router)
    dp.include_router(track_handler.router)
    dp.include_router(sync_handler.router)
    dp.include_router(eat_handler.router)
    dp.include_router(note_done_handler.router)
    dp.include_router(today_handler.router)
    dp.include_router(week_handler.router)
    dp.include_router(meds_handler.router)
    # word must stay LAST: its StateFilter(None) text handler is a catch-all and
    # would otherwise shadow command/state handlers in the routers above.
    dp.include_router(word_handler.router)
    return dp
