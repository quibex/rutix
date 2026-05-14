import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from rutix.integrations.claude import ClaudeClient
from rutix.markdown.daily import MealItem


@pytest.fixture
def fake_anthropic():
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock()
    return client


@pytest.fixture
def claude(tmp_path, fake_anthropic, monkeypatch):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "eat.md").write_text("EAT_SYSTEM\n", encoding="utf-8")
    return ClaudeClient(
        api_key="sk-ant-test",
        prompts_dir=prompts_dir,
        sdk_client=fake_anthropic,
    )


async def test_parse_eat_returns_meal_items(claude, fake_anthropic):
    payload = {
        "items": [
            {
                "name": "Шаурма",
                "kcal": 450,
                "protein": 22.0,
                "fat": 18.0,
                "carbs": 45.0,
                "source": "estimate",
            },
            {
                "name": "Кола 0.4л",
                "kcal": 170,
                "protein": 0,
                "fat": 0,
                "carbs": 42,
                "source": "reference",
            },
        ]
    }
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps(payload))]
    fake_anthropic.messages.create.return_value = msg

    items = await claude.parse_eat("шаурма + кола", reference_md="## ВкусВилл\n...")

    assert items == [
        MealItem("", "Шаурма", 450, 22.0, 18.0, 45.0),
        MealItem("", "Кола 0.4л", 170, 0.0, 0.0, 42.0),
    ]
    fake_anthropic.messages.create.assert_awaited_once()
    call_kwargs = fake_anthropic.messages.create.call_args.kwargs
    assert "EAT_SYSTEM" in call_kwargs["system"]
    assert "## ВкусВилл" in call_kwargs["system"]
    assert call_kwargs["messages"][0]["content"] == "шаурма + кола"


async def test_parse_eat_raises_on_malformed_json(claude, fake_anthropic):
    msg = MagicMock()
    msg.content = [MagicMock(text="not a json")]
    fake_anthropic.messages.create.return_value = msg

    with pytest.raises(ValueError, match="invalid JSON"):
        await claude.parse_eat("eggs", reference_md="")


async def test_parse_eat_raises_on_missing_items_key(claude, fake_anthropic):
    msg = MagicMock()
    msg.content = [MagicMock(text='{"foo": "bar"}')]
    fake_anthropic.messages.create.return_value = msg

    with pytest.raises(ValueError, match="missing 'items'"):
        await claude.parse_eat("eggs", reference_md="")
