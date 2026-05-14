from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from freezegun import freeze_time

from rutix.bot.handlers.meds import cmd_meds, MedsStates
from rutix.db.models import MedActive


@pytest.fixture
def fake_settings():
    s = MagicMock()
    s.tz = "Europe/Moscow"
    return s


@pytest.fixture
def fake_message():
    m = MagicMock()
    m.answer = AsyncMock()
    return m


async def test_cmd_meds_lists_active(fake_message, fake_settings, session):
    session.add(
        MedActive(
            key="seizar",
            name="Сейзар",
            column_label="Сейзар",
            current_dose="25",
            started_at=date(2026, 4, 26),
        )
    )
    await session.commit()

    def session_factory_call():
        class CM:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, *a):
                pass

        return CM()

    sf = MagicMock(side_effect=lambda: session_factory_call())

    await cmd_meds(fake_message, settings=fake_settings, session_factory=sf)

    text = fake_message.answer.call_args.args[0]
    assert "Сейзар" in text
    assert "25" in text
    # Buttons present
    kb = fake_message.answer.call_args.kwargs["reply_markup"]
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Добавить" in l for l in labels)
