"""Build aiogram Bot + Dispatcher with all routers wired."""

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from rutix.bot.auth import WhitelistMiddleware
from rutix.bot.handlers import eat as eat_handler
from rutix.bot.handlers import meds as meds_handler
from rutix.bot.handlers import note_done as note_done_handler
from rutix.bot.handlers import sync as sync_handler
from rutix.bot.handlers import today as today_handler
from rutix.bot.handlers import track as track_handler
from rutix.bot.handlers import week as week_handler


def build_bot(token: str) -> Bot:
    return Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def build_dispatcher(allowed_user_id: int) -> Dispatcher:
    dp = Dispatcher()
    dp.update.middleware(WhitelistMiddleware(allowed_user_id))
    dp.include_router(track_handler.router)
    dp.include_router(sync_handler.router)
    dp.include_router(eat_handler.router)
    dp.include_router(note_done_handler.router)
    dp.include_router(today_handler.router)
    dp.include_router(week_handler.router)
    dp.include_router(meds_handler.router)
    return dp
