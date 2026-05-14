"""Build aiogram Bot + Dispatcher with all routers wired."""

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from rutix.bot.auth import WhitelistMiddleware
from rutix.bot.handlers import sync as sync_handler
from rutix.bot.handlers import track as track_handler


def build_bot(token: str) -> Bot:
    return Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def build_dispatcher(allowed_user_id: int) -> Dispatcher:
    dp = Dispatcher()
    dp.update.middleware(WhitelistMiddleware(allowed_user_id))
    dp.include_router(track_handler.router)
    dp.include_router(sync_handler.router)
    return dp
