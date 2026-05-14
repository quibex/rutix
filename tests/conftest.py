"""Shared pytest fixtures."""
import pytest_asyncio

from rutix.db.engine import make_engine, make_session_factory
from rutix.db.models import Base


@pytest_asyncio.fixture
async def session():
    """In-memory SQLite session with all tables created."""
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = make_session_factory(engine)
    async with Session() as s:
        yield s
    await engine.dispose()
