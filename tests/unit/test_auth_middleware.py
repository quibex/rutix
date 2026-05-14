from unittest.mock import AsyncMock, MagicMock

from aiogram.types import User

from rutix.bot.auth import WhitelistMiddleware


async def test_middleware_allows_whitelisted_user():
    mw = WhitelistMiddleware(allowed_user_id=42)
    handler = AsyncMock(return_value="ok")
    user = User(id=42, is_bot=False, first_name="Test")
    data = {"event_from_user": user}

    result = await mw(handler, MagicMock(), data)

    assert result == "ok"
    handler.assert_awaited_once()


async def test_middleware_blocks_other_user():
    mw = WhitelistMiddleware(allowed_user_id=42)
    handler = AsyncMock(return_value="ok")
    user = User(id=999, is_bot=False, first_name="Stranger")
    data = {"event_from_user": user}

    result = await mw(handler, MagicMock(), data)

    assert result is None
    handler.assert_not_awaited()


async def test_middleware_blocks_when_no_user_in_data():
    """Defensive: if event_from_user is missing entirely, block (zero trust)."""
    mw = WhitelistMiddleware(allowed_user_id=42)
    handler = AsyncMock(return_value="ok")

    result = await mw(handler, MagicMock(), {})

    assert result is None
    handler.assert_not_awaited()
